"""轮次比对报告：指标趋势、逐案例变化、SKILL 版本 diff、优化点高亮。"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

from . import __version__


def build_report(*, run_dir: Path, cfg: dict, rounds_data: list[dict], run_id: str,
                 elapsed_sec: float, final: bool, cases: list[dict]) -> dict:
    dataset_meta = [
        {"id": c["id"], "language": c["language"], "file_path": c["file_path"],
         "change_summary": c["change_summary"], "gt_count": c["gt_count"],
         "is_clean_case": c["is_clean_case"], "diff": c["diff"],
         "ground_truth": [
             {k: g[k] for k in ("id", "category", "severity", "title", "detail", "line_hint")}
             for g in c.get("ground_truth", [])
         ]}
        for c in sorted(cases, key=lambda x: x["id"])
    ]

    skills_dir = run_dir / "skills"
    skill_versions = {}
    for p in sorted(skills_dir.glob("skill_v*.md")):
        skill_versions[p.stem.replace("skill_", "")] = p.read_text(encoding="utf-8")

    comparison = build_comparison(rounds_data, skill_versions)

    return {
        "schema_version": 1,
        "generator": f"SkillEvolve-CR {__version__}",
        "run_id": run_id,
        "created_at": _now(),
        "final": final,
        "elapsed_sec": round(elapsed_sec, 1),
        "config": {
            "rounds": cfg["loop"]["rounds"],
            "cases": cfg["loop"]["cases"],
            "model": cfg["claude"]["model"],
            "max_turns": cfg["claude"]["max_turns"],
            "concurrency": cfg["loop"]["concurrency"],
            "optimize_last_round": cfg["loop"]["optimize_last_round"],
            "start_skill": cfg["loop"].get("start_skill") or "main",
        },
        "dataset": dataset_meta,
        "skill_versions": skill_versions,
        "rounds": rounds_data,
        "comparison": comparison,
    }


def build_comparison(rounds_data: list[dict], skill_versions: dict[str, str]) -> dict:
    trend = [
        {
            "round": r["round"],
            "skill_version": r["skill_version"],
            **r["metrics"],
        }
        for r in rounds_data
    ]

    deltas = []
    for i in range(1, len(rounds_data)):
        prev, cur = rounds_data[i - 1], rounds_data[i]
        pm, cm = prev["metrics"], cur["metrics"]
        per_case_delta = _per_case_delta(prev["per_case"], cur["per_case"])
        deltas.append({
            "from_round": prev["round"],
            "to_round": cur["round"],
            "from_version": prev["skill_version"],
            "to_version": cur["skill_version"],
            "metric_delta": {
                k: round(cm[k] - pm[k], 4)
                for k in ("precision", "recall", "f1", "tp", "fp", "fn", "clean_case_fp")
            },
            **per_case_delta,
        })

    highlights = _highlights(rounds_data, deltas)

    skill_diffs = []
    versions = list(skill_versions.keys())
    for a, b in zip(versions, versions[1:]):
        diff = "\n".join(difflib.unified_diff(
            skill_versions[a].splitlines(), skill_versions[b].splitlines(),
            fromfile=f"skill_{a}", tofile=f"skill_{b}", lineterm="",
        ))
        changelog = _find_changelog(rounds_data, a)
        skill_diffs.append({"from": a, "to": b, "unified_diff": diff, "changelog": changelog})

    return {"metric_trend": trend, "deltas": deltas, "highlights": highlights, "skill_diffs": skill_diffs}


def _per_case_delta(prev_case: list[dict], cur_case: list[dict]) -> dict:
    prev_by = {m["case_id"]: m for m in prev_case}
    cur_by = {m["case_id"]: m for m in cur_case}
    fixed, regressed, changed = [], [], []
    for cid in sorted(set(prev_by) | set(cur_by)):
        p, c = prev_by.get(cid, {}), cur_by.get(cid, {})
        pf, cf = (p.get("fp", 0), p.get("fn", 0)), (c.get("fp", 0), c.get("fn", 0))
        if cf == pf:
            continue
        item = {
            "case_id": cid,
            "fp": [pf[0], cf[0]],
            "fn": [pf[1], cf[1]],
            "note": _note(pf, cf),
        }
        changed.append(item)
        if cf[1] < pf[1] or cf[0] < pf[0]:
            fixed.append(item)
        else:
            regressed.append(item)
    return {"cases_changed": changed, "cases_fixed": fixed, "cases_regressed": regressed}


def _note(pf: tuple[int, int], cf: tuple[int, int]) -> str:
    parts = []
    if cf[1] < pf[1]:
        parts.append(f"漏报 {pf[1]}→{cf[1]}")
    elif cf[1] > pf[1]:
        parts.append(f"漏报 {pf[1]}→{cf[1]}（恶化）")
    if cf[0] < pf[0]:
        parts.append(f"误报 {pf[0]}→{cf[0]}")
    elif cf[0] > pf[0]:
        parts.append(f"误报 {pf[0]}→{cf[0]}（恶化）")
    return "，".join(parts) if parts else "无变化"


def _highlights(rounds_data: list[dict], deltas: list[dict]) -> list[str]:
    out: list[str] = []
    if not rounds_data:
        return out
    first = rounds_data[0]["metrics"]
    last = rounds_data[-1]["metrics"]
    out.append(
        f"全流程 F1 {first['f1']:.2f} → {last['f1']:.2f}"
        f"（P {first['precision']:.2f}→{last['precision']:.2f}，"
        f"R {first['recall']:.2f}→{last['recall']:.2f}）"
    )
    for d in deltas:
        md = d["metric_delta"]
        out.append(
            f"R{d['from_round']}→R{d['to_round']}: F1 {md['f1']:+.2f}（{d['from_version']}→{d['to_version']}），"
            f"FP {md['fp']:+d}，FN {md['fn']:+d}"
        )
        if d["cases_fixed"]:
            out.append("  修复: " + "；".join(f"{c['case_id']}（{c['note']}）" for c in d["cases_fixed"]))
        if d["cases_regressed"]:
            out.append("  回退: " + "；".join(f"{c['case_id']}（{c['note']}）" for c in d["cases_regressed"]))
    # 汇总修复的漏报/误报
    all_fixed_fn = [c for d in deltas for c in d["cases_fixed"] if c["fn"][1] < c["fn"][0]]
    all_fixed_fp = [c for d in deltas for c in d["cases_fixed"] if c["fp"][1] < c["fp"][0]]
    if all_fixed_fn:
        out.append(f"累计修复漏报 {len(all_fixed_fn)} 个案例: {', '.join(c['case_id'] for c in all_fixed_fn)}")
    if all_fixed_fp:
        out.append(f"累计修复误报 {len(all_fixed_fp)} 个案例: {', '.join(c['case_id'] for c in all_fixed_fp)}")
    # 优化器有效性 / 护栏事件
    for r in rounds_data:
        opt = r.get("optimizer") or {}
        if r.get("reverted_to"):
            out.append(f"R{r['round']} 指标较历史最佳回退，优化产物已丢弃并回退到 {r['reverted_to']}")
        elif opt.get("ran") is False and opt.get("reason"):
            out.append(f"R{r['round']} 跳过 SKILL 优化（{opt.get('reason')}），沿用 {r['skill_version']}")
        elif opt and opt.get("ran") and not opt.get("valid"):
            out.append(f"R{r['round']} 优化产物未通过校验（{opt.get('reason','')}），沿用旧版技能")
    return [h for h in out if h]


def _find_changelog(rounds_data: list[dict], from_version: str) -> dict | None:
    for r in rounds_data:
        opt = r.get("optimizer") or {}
        cl = opt.get("changelog") or {}
        if cl.get("from_version") == from_version:
            return cl
    return None


def _now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")
