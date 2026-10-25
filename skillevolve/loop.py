"""闭环编排：评估 → 评分 → 指标 → 优化，轮次可配置，每轮完全隔离。"""
from __future__ import annotations

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

from . import console, evaluate, metrics, optimizer, scoring
from .config import STAGES, resolve_path, resolve_stage_cfg, sanitize_cfg
from .report import build_report
from .trace_diagnostics import build_trace_report


def load_dataset(dataset_path: Path) -> list[dict]:
    cases = []
    with dataset_path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    return cases


def select_cases(cases: list[dict], wanted: list[str]) -> list[dict]:
    if not wanted:
        return cases
    id_set = set(wanted)
    missing = id_set - {c["id"] for c in cases}
    if missing:
        raise ValueError(f"评测集中不存在这些案例: {sorted(missing)}")
    return [c for c in cases if c["id"] in id_set]


def resolve_start_skill(skills_dir: Path, start_skill: str) -> tuple[Path, str]:
    """解析起始 SKILL：main=当前主文件；baseline=初始版本基线；weak=弱化演示版本。缺失时回退主文件。"""
    code_dir = skills_dir / "code-review"
    if start_skill == "baseline":
        p = code_dir / "SKILL.baseline.md"
        if p.exists():
            return p, "初始版本 baseline"
    if start_skill == "weak":
        p = code_dir / "SKILL.weak.md"
        if p.exists():
            return p, "弱化版本 weak（演示起点）"
    return code_dir / "SKILL.md", "当前主 SKILL"


def _stage_cost(infos: list[dict]) -> float | None:
    """汇总一组 case 记录中 session 的费用（无费用数据时返回 None）。"""
    total, seen = 0.0, False
    for info in infos:
        c = (info.get("session") or {}).get("cost_usd")
        if c is not None:
            total += float(c)
            seen = True
    return total if seen else None


def _cost_line(cost: float | None) -> str:
    return f" · 费用 {console.fmt_cost(cost)}" if cost else ""


def _model_label(cfg: dict) -> str:
    """展示各阶段有效模型：三阶段一致时显示单个，否则逐阶段列出。"""
    models = [resolve_stage_cfg(cfg, s)["model"] or "(默认)" for s in STAGES]
    if len(set(models)) == 1:
        return models[0]
    return " ".join(f"{s}={m}" for s, m in zip(("检视", "评分", "优化"), models))


def run_loop(cfg: dict, *, run_id: str | None = None, overrides: dict | None = None, log=print) -> Path:
    if overrides:
        cfg = _apply_overrides(cfg, overrides)
    rounds = int(cfg["loop"]["rounds"])
    if rounds < 1:
        raise ValueError("rounds 必须 >= 1")

    dataset_path = resolve_path(cfg, "dataset")
    skills_dir = resolve_path(cfg, "skills_dir")
    runs_dir = resolve_path(cfg, "runs_dir")

    run_id = run_id or f"run-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
    run_dir = runs_dir / run_id
    if run_dir.exists():
        raise FileExistsError(f"run 目录已存在: {run_dir}（换个 --run-id 或删除它）")
    (run_dir / "skills").mkdir(parents=True)
    (run_dir / "changelogs").mkdir(parents=True)

    all_cases = load_dataset(dataset_path)
    cases = select_cases(all_cases, list(cfg["loop"]["cases"]))
    shutil.copyfile(dataset_path, run_dir / "dataset_snapshot.jsonl")
    (run_dir / "config_snapshot.json").write_text(
        json.dumps(sanitize_cfg(cfg), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )

    review_skill_src, start_label = resolve_start_skill(
        skills_dir, str(cfg["loop"].get("start_skill") or "main")
    )
    skill_text = review_skill_src.read_text(encoding="utf-8")
    (run_dir / "skills" / "skill_v1.md").write_text(skill_text, encoding="utf-8", newline="\n")

    log(console.bold("== SkillEvolve-CR 自优化闭环 =="))
    log(console.gray(
        f"   run_id={run_id} · 轮次 {rounds} · 案例 {len(cases)} · 并发 {cfg['loop']['concurrency']}"
        f" · 模型 {_model_label(cfg)} · 起始SKILL {start_label}"
    ))

    # ⓪ 编译验证（案例有效性门槛：编译不通过的不进入评测，也不计 FP/FN）
    log(console.cyan("  ⓪ 编译验证") + console.gray(" · javac"))
    cases, compile_failures = evaluate.verify_compiles(cases, log)
    if compile_failures:
        log(console.yellow(f"    ⚠ {len(compile_failures)} 条案例编译未通过，已从本轮评测剔除"))
    if not cases:
        raise ValueError("所有案例编译失败，无可评测案例")

    rounds_data: list[dict] = []
    prev_changelog: dict | None = None
    started = time.monotonic()
    total_cost = 0.0
    failed_outputs = 0
    skill_version = "v1"
    best: dict = {"f1": -1.0, "version": skill_version, "skill_text": skill_text}

    for round_no in range(1, rounds + 1):
        round_dir = run_dir / f"round_{round_no:02d}"
        used_version = skill_version
        log("")
        log(console.bold(f"━━ Round {round_no}/{rounds}") + console.gray(f" · 使用技能 {used_version} ━━"))
        round_cost = 0.0

        # ① 检视生成
        log(console.cyan("  ① 检视生成") + console.gray(" · code-review (headless)"))
        t0 = time.monotonic()
        review_ws = round_dir / "review_ws"
        evaluate.prepare_workspace(review_ws, skill_text, cases)
        eval_infos = evaluate.evaluate_round(review_ws, cases, cfg, log)
        c = _stage_cost(eval_infos)
        round_cost += c or 0.0
        failed_outputs += sum(1 for i in eval_infos if not i["output_valid"])
        log(console.gray(f"    ↳ 阶段用时 {console.fmt_dur(time.monotonic() - t0)}{_cost_line(c)}"))

        # ② 评分
        log(console.cyan("  ② 评分") + console.gray(" · score-review (headless)"))
        t0 = time.monotonic()
        score_ws = round_dir / "score_ws"
        scoring.prepare_workspace(score_ws, review_ws, cases)
        score_infos = scoring.score_round(score_ws, cases, cfg, log)
        c = _stage_cost(score_infos)
        round_cost += c or 0.0
        failed_outputs += sum(1 for i in score_infos if not i["output_valid"])
        log(console.gray(f"    ↳ 阶段用时 {console.fmt_dur(time.monotonic() - t0)}{_cost_line(c)}"))

        # ③ 指标汇总（Python 确定性计算）
        case_metrics = [
            metrics.compute_case_metrics(cse, e, s)
            for cse, e, s in zip(sorted(cases, key=lambda x: x["id"]), eval_infos, score_infos)
        ]
        round_metrics = metrics.compute_round_metrics(case_metrics)
        log(
            "  " + console.bold("指标")
            + f": P={round_metrics['precision']:.3f} R={round_metrics['recall']:.3f} "
              f"F1={round_metrics['f1']:.3f} TP={round_metrics['tp']} FP={round_metrics['fp']} FN={round_metrics['fn']}"
        )
        (round_dir / "metrics.json").write_text(
            json.dumps({"round": round_no, "skill_version": used_version,
                        "metrics": round_metrics, "per_case": case_metrics},
                       ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
        # 历史最佳追踪：当前轮指标不低于最佳时，本轮技能成为新基准（平分时取新不取旧）
        if round_metrics["f1"] >= best["f1"]:
            best = {"f1": round_metrics["f1"], "version": used_version, "skill_text": skill_text}

        # ④ SKILL 优化（除最后一轮，或显式配置）
        trace_max_turns = max(
            resolve_stage_cfg(cfg, "review")["max_turns"],
            resolve_stage_cfg(cfg, "score")["max_turns"],
        )
        trace_report = build_trace_report(eval_infos, score_infos, max_turns=trace_max_turns)
        optimizer_info = None
        reverted_to: str | None = None
        do_optimize = cfg["loop"]["optimize_last_round"] or round_no < rounds
        perfect = round_metrics["fp"] == 0 and round_metrics["fn"] == 0
        if do_optimize and perfect and not trace_report["issues"]:
            reason = "本轮 FP=FN=0 且链路无告警，无改进空间（为改而改只会引入回归风险）"
            log("  " + console.gray(f"③ SKILL 优化: 跳过 · {reason}，下一轮沿用 {used_version}"))
            optimizer_info = {"ran": False, "valid": False, "reason": reason,
                              "changelog": None, "changelog_error": "", "skill_diff": "", "session": {}}
        elif do_optimize:
            log(console.cyan("  ③ SKILL 优化") + console.gray(" · optimize-review (headless)"))
            t0 = time.monotonic()
            errors_pack = optimizer.build_errors_pack(round_no, cases, eval_infos, score_infos, round_metrics)
            opt_ws = round_dir / "optimize_ws"
            optimizer.prepare_workspace(
                opt_ws, skill_text,
                errors_pack=errors_pack, trace_report=trace_report,
                round_metrics={"round": round_no, **round_metrics, "per_case": case_metrics},
                prev_changelog=prev_changelog,
            )
            optimizer_info = optimizer.optimize_skill(opt_ws, cfg, round_no, prev_changelog, log)
            c = _stage_cost([optimizer_info])
            round_cost += c or 0.0
            log(console.gray(f"    ↳ 阶段用时 {console.fmt_dur(time.monotonic() - t0)}{_cost_line(c)}"))
            if optimizer_info["valid"]:
                if round_metrics["f1"] < best["f1"]:
                    # 回退护栏：本轮指标低于历史最佳，丢弃优化产物，下一轮回到最佳版本
                    reverted_to = best["version"]
                    skill_text, skill_version = best["skill_text"], best["version"]
                    optimizer_info["valid"] = False
                    optimizer_info["new_skill_text"] = ""
                    optimizer_info["skill_diff"] = ""
                    optimizer_info["reason"] = (
                        f"指标回退护栏：本轮 F1 {round_metrics['f1']:.3f} 低于历史最佳 "
                        f"{best['f1']:.3f}，优化结果已丢弃并回退到 {best['version']}"
                    )
                    log("    " + console.yellow("! " + optimizer_info["reason"]))
                else:
                    next_version = f"v{int(skill_version[1:]) + 1}"
                    skill_text = optimizer_info["new_skill_text"]
                    (run_dir / "skills" / f"skill_{next_version}.md").write_text(
                        skill_text, encoding="utf-8", newline="\n"
                    )
                    cl = optimizer_info.get("changelog") or {"changes": [], "note": "changelog 缺失"}
                    cl["round"] = round_no
                    cl["from_version"] = used_version
                    cl["to_version"] = next_version
                    (run_dir / "changelogs" / f"changelog_{used_version}_to_{next_version}.json").write_text(
                        json.dumps(cl, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
                    )
                    prev_changelog = cl
                    skill_version = next_version
            else:
                log("    " + console.yellow(f"! 优化结果校验失败，下一轮沿用 {used_version}: {optimizer_info['reason']}"))
        else:
            log(console.gray("  ③ SKILL 优化: 跳过（最后一轮且 optimize_last_round=false）"))

        total_cost += round_cost
        round_data = {
            "round": round_no,
            "skill_version": used_version,
            "metrics": round_metrics,
            "per_case": case_metrics,
            "case_details": _case_details(cases, eval_infos, score_infos),
            "trace_report": trace_report,
            "optimizer": _optimizer_public(optimizer_info),
            "compile_failures": compile_failures,
            "cost_usd": round(round_cost, 4),
        }
        if reverted_to:
            round_data["reverted_to"] = reverted_to
        rounds_data.append(round_data)

        if cfg["report"]["progressive"]:
            _write_report(run_dir, cfg, rounds_data, run_id, started, final=False, cases=cases)

    report = _write_report(run_dir, cfg, rounds_data, run_id, started, final=True, cases=cases)
    final_m = rounds_data[-1]["metrics"]
    elapsed = time.monotonic() - started

    # 运行汇总
    log("")
    log(console.bold("━━━━ 运行汇总 " + "─" * 32))
    for rd in rounds_data:
        m = rd["metrics"]
        log(
            f"   R{rd['round']} {rd['skill_version']:<4}"
            f" P={m['precision'] * 100:6.1f}%  R={m['recall'] * 100:6.1f}%  F1={m['f1']:5.2f}"
            f"   TP {m['tp']:>2} · FP {m['fp']:>2} · FN {m['fn']:>2}"
            + (console.gray(f"   {console.fmt_cost(rd.get('cost_usd'))}") if rd.get("cost_usd") else "")
        )
    cost_txt = f" · 总费用 {console.fmt_cost(total_cost)}" if total_cost else ""
    log(
        f"   最终: P={final_m['precision']:.3f} R={final_m['recall']:.3f} F1={final_m['f1']:.3f}"
        + console.gray(f" · 用时 {console.fmt_dur(elapsed)}{cost_txt}")
    )
    if failed_outputs:
        log(console.yellow(f"   ⚠ {failed_outputs} 次阶段输出异常（漏报已计入 FN），详情见 Dashboard 链路诊断"))
    log(f"   报告: {report}")
    log(f"   下一步: " + console.cyan("python -m skillevolve serve --open")
        + console.gray("   # 打开 Dashboard 查看趋势、案例下钻与 SKILL 版本比对"))
    return run_dir


def _apply_overrides(cfg: dict, overrides: dict) -> dict:
    out = json.loads(json.dumps(cfg))  # deep copy
    for section, vals in overrides.items():
        for k, v in vals.items():
            if v is not None:
                out.setdefault(section, {})[k] = v
    return out


def _case_details(cases: list[dict], eval_infos: list[dict], score_infos: list[dict]) -> dict:
    eval_by = {i["case_id"]: i for i in eval_infos}
    score_by = {i["case_id"]: i for i in score_infos}
    details = {}
    for c in sorted(cases, key=lambda x: x["id"]):
        cid = c["id"]
        details[cid] = {
            "gt": [
                {k: g[k] for k in ("id", "category", "severity", "title", "detail", "line_hint")}
                for g in c.get("ground_truth", [])
            ],
            "generated_findings": eval_by[cid].get("findings", []),
            "review_summary": eval_by[cid].get("summary", ""),
            "verdicts": score_by[cid].get("results", []),
            "unmatched_gt": score_by[cid].get("unmatched_gt", []),
            "scorer_notes": score_by[cid].get("notes", ""),
            "review_session": eval_by[cid].get("session", {}),
            "score_session": score_by[cid].get("session", {}),
            "review_trace": eval_by[cid].get("trace", {}),
            "score_trace": score_by[cid].get("trace", {}),
            "review_output_valid": eval_by[cid].get("output_valid", False),
        }
    return details


def _optimizer_public(info: dict | None) -> dict | None:
    if info is None:
        return None
    return {
        "ran": info["ran"],
        "valid": info["valid"],
        "reason": info["reason"],
        "changelog": info.get("changelog"),
        "changelog_error": info.get("changelog_error", ""),
        "skill_diff": info.get("skill_diff", ""),
        "session": info.get("session", {}),
    }


def _write_report(run_dir: Path, cfg: dict, rounds_data, run_id, started, *, final: bool, cases) -> Path:
    report = build_report(
        run_dir=run_dir, cfg=cfg, rounds_data=rounds_data,
        run_id=run_id, elapsed_sec=time.monotonic() - started, final=final, cases=cases,
    )
    path = run_dir / "report.json"
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    return path
