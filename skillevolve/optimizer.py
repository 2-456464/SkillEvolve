"""阶段③：SKILL 优化 —— optimize-review SKILL (headless) 依据错例与链路诊断改进检视技能。"""
from __future__ import annotations

import difflib
import json
import shutil
from pathlib import Path

from . import console
from .claude_runner import run_headless
from .config import render_prompt, resolve_stage_cfg
from .evaluate import parse_json_lenient
from .trace_diagnostics import build_trace_report


def prepare_workspace(
    ws: Path,
    skill_text: str,
    *,
    errors_pack: dict,
    trace_report: dict,
    round_metrics: dict,
    prev_changelog: dict | None,
) -> None:
    (ws / ".claude" / "skills" / "optimize-review").mkdir(parents=True, exist_ok=True)
    skill_src = Path(__file__).resolve().parent.parent / "skills" / "optimize-review" / "SKILL.md"
    shutil.copyfile(skill_src, ws / ".claude" / "skills" / "optimize-review" / "SKILL.md")

    (ws / "skill_current").mkdir(parents=True, exist_ok=True)
    (ws / "skill_current" / "SKILL.md").write_text(skill_text, encoding="utf-8", newline="\n")

    (ws / "analysis").mkdir(parents=True, exist_ok=True)
    (ws / "analysis" / "errors.json").write_text(
        json.dumps(errors_pack, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    (ws / "analysis" / "trace_report.json").write_text(
        json.dumps(trace_report, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    (ws / "analysis" / "metrics.json").write_text(
        json.dumps(round_metrics, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )
    if prev_changelog is not None:
        (ws / "analysis" / "prev_changelog.json").write_text(
            json.dumps(prev_changelog, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
    (ws / "optimized").mkdir(parents=True, exist_ok=True)
    (ws / "traces").mkdir(parents=True, exist_ok=True)
    (ws / "sessions").mkdir(parents=True, exist_ok=True)
    (ws / "logs").mkdir(parents=True, exist_ok=True)


def build_errors_pack(
    round_no: int,
    cases: list[dict],
    eval_infos: list[dict],
    score_infos: list[dict],
    round_metrics: dict,
) -> dict:
    """构造错例包：FP（误报）/ FN（漏报）逐条附 diff 与原因；不含标准答案全文清单。"""
    case_by_id = {c["id"]: c for c in cases}
    eval_by_id = {i["case_id"]: i for i in eval_infos}
    score_by_id = {i["case_id"]: i for i in score_infos}

    hit_gt: dict[str, set[str]] = {}
    false_positives: list[dict] = []
    for sid in score_by_id:
        cid = sid
        gt_set = {g["id"] for g in case_by_id[cid].get("ground_truth", [])}
        hits = set()
        for r in score_by_id[cid].get("results", []):
            if r.get("verdict") == "TP" and r.get("matched_gt") in gt_set:
                hits.add(r["matched_gt"])
        hit_gt[cid] = hits

        for r in score_by_id[cid].get("results", []):
            if r.get("verdict") != "FP":
                continue
            finding = _find_finding(eval_by_id[cid], r.get("finding_id"))
            false_positives.append({
                "case_id": cid,
                "finding": finding,
                "scorer_reason": r.get("reason", ""),
                "duplicate_of": r.get("duplicate_of"),
                "diff": case_by_id[cid]["diff"],
            })

    false_negatives: list[dict] = []
    for cid, gt_ids in hit_gt.items():
        for g in case_by_id[cid].get("ground_truth", []):
            if g["id"] not in gt_ids:
                false_negatives.append({
                    "case_id": cid,
                    "missed_gt": {k: g[k] for k in ("id", "category", "severity", "title", "detail", "line_hint")},
                    "diff": case_by_id[cid]["diff"],
                })

    return {
        "round": round_no,
        "metrics": round_metrics,
        "false_positive_count": len(false_positives),
        "false_negative_count": len(false_negatives),
        "false_positives": false_positives,
        "false_negatives": false_negatives,
    }


def _find_finding(eval_info: dict, finding_id: str | None) -> dict:
    for f in eval_info.get("findings", []):
        if f.get("id") == finding_id:
            return f
    return {"id": finding_id, "note": "原始意见未找到"}


def validate_new_skill(new_text: str, old_text: str) -> tuple[bool, str]:
    if not new_text or len(new_text.strip()) < 200:
        return False, "新技能内容过短或为空"
    if not new_text.lstrip().startswith("---"):
        return False, "缺少 YAML frontmatter"
    head = "\n".join(new_text.lstrip().splitlines()[:10])
    if "name:" not in head or "code-review" not in head:
        return False, "frontmatter 中 name 必须保持为 code-review"
    if new_text.strip() == old_text.strip():
        return False, "新技能与当前版本完全相同（无有效修改）"
    for field in ("case_id", "findings", "summary"):
        if field not in new_text:
            return False, f"输出 JSON 顶层字段 {field} 缺失（评分流水线依赖该结构）"
    return True, ""


def optimize_skill(ws: Path, cfg: dict, round_no: int, prev_changelog: dict | None, log=print) -> dict:
    stage = resolve_stage_cfg(cfg, "optimize")
    prev_line = (
        f"- 上一轮优化说明（不要回退已有改动）：analysis/prev_changelog.json\n" if prev_changelog else ""
    )
    result = run_headless(
        render_prompt(stage["prompt"], prev_line=prev_line),
        cwd=ws,
        model=stage["model"],
        max_turns=max(stage["max_turns"], 40),
        timeout_seconds=stage["timeout_seconds"],
        allowed_tools=stage["allowed_tools"],
        permission_mode=stage["permission_mode"],
        api_key=stage["api_key"],
        auth_token=stage["auth_token"],
        base_url=stage["base_url"],
        trace_path=ws / "traces" / "optimizer.stream.jsonl",
        stderr_log_path=ws / "logs" / "optimizer.stderr.log",
    )
    (ws / "sessions" / "optimizer.session.json").write_text(
        json.dumps(result.session, ensure_ascii=False, indent=2),
        encoding="utf-8", newline="\n",
    )

    new_path = ws / "optimized" / "SKILL.md"
    changelog_path = ws / "optimized" / "changelog.json"
    new_text = new_path.read_text(encoding="utf-8-sig") if new_path.exists() else ""
    old_text = (ws / "skill_current" / "SKILL.md").read_text(encoding="utf-8")
    valid, reason = validate_new_skill(new_text, old_text)

    changelog, ch_err = None, ""
    if changelog_path.exists():
        try:
            changelog = parse_json_lenient(changelog_path.read_text(encoding="utf-8-sig"))
        except (ValueError, OSError) as exc:
            ch_err = f"changelog 解析失败: {exc}"
    else:
        ch_err = "changelog.json 未生成"

    unified_diff = "\n".join(
        difflib.unified_diff(
            old_text.splitlines(), new_text.splitlines(),
            fromfile=f"skill_v{round_no}", tofile=f"skill_v{round_no + 1}", lineterm="",
        )
    ) if valid else ""

    info = {
        "ran": True,
        "valid": valid,
        "reason": reason,
        "changelog": changelog,
        "changelog_error": ch_err,
        "new_skill_text": new_text if valid else "",
        "skill_diff": unified_diff,
        "timed_out": result.timed_out,
        "session": result.session,
    }
    state = console.green("OK") if valid else console.red(f"INVALID（{reason}）")
    dur = (result.session or {}).get("duration_ms")
    log(f"    [optim ] optimize-review: {state}" + (console.gray(" " + console.fmt_dur(dur / 1000)) if dur else ""))
    return info
