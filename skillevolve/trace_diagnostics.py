"""trace/session 链路诊断：从 stream-json 事件中提取执行链路健康度。

诊断维度：
  - 技能是否被加载（Skill 工具调用，或 Read 了 SKILL.md）
  - 是否读取了案例输入、是否写出目标输出文件
  - 工具调用错误次数与样例
  - 轮次是否逼近上限、是否超时
  - 调用次数 / 文本量等基础统计
这些结果按案例落盘，并在每轮末尾汇总成 trace_report 喂给优化技能。
"""
from __future__ import annotations

import json
from pathlib import Path


def _blocks(event: dict, kind: str) -> list[dict]:
    msg = event.get("message") or {}
    content = msg.get("content") or []
    if isinstance(content, str):
        return []
    return [b for b in content if isinstance(b, dict) and b.get("type") == kind]


def analyze_trace(
    events: list[dict],
    *,
    stage: str,
    case_id: str,
    skill_name: str,
    cases_marker: str = "cases",
    output_marker: str | None = None,
) -> dict:
    tool_uses: list[dict] = []
    tool_errors: list[str] = []
    text_chars = 0
    skill_loaded = False
    read_case = False
    wrote_output = False

    for ev in events:
        etype = ev.get("type")
        if etype == "assistant":
            for b in _blocks(ev, "tool_use"):
                name = b.get("name") or ""
                inp = b.get("input") or {}
                brief = _brief_input(name, inp)
                tool_uses.append({"tool": name, "brief": brief})
                blob = json.dumps(inp, ensure_ascii=False)
                blob_norm = blob.replace("\\\\", "\\").replace("\\", "/")
                if name in ("Skill", "SlashCommand") and skill_name in blob:
                    skill_loaded = True
                if name == "Read" and f"{skill_name}/SKILL.md" in blob_norm:
                    skill_loaded = True
                if name == "Read" and cases_marker in blob_norm:
                    read_case = True
                if output_marker and name in ("Write", "Edit") and output_marker in blob_norm:
                    wrote_output = True
            for b in _blocks(ev, "text"):
                text_chars += len(b.get("text") or "")
        elif etype == "user":
            for b in _blocks(ev, "tool_result"):
                if b.get("is_error"):
                    content = b.get("content")
                    if isinstance(content, list):
                        content = " ".join(
                            c.get("text", "") for c in content if isinstance(c, dict)
                        )
                    tool_errors.append(str(content)[:300])

    result_ev = next((e for e in events if e.get("type") == "result"), {})
    return {
        "stage": stage,
        "case_id": case_id,
        "skill_name": skill_name,
        "skill_loaded": skill_loaded,
        "read_case": read_case,
        "wrote_output": wrote_output,
        "num_tool_calls": len(tool_uses),
        "tool_call_summary": _summarize_tools(tool_uses),
        "tool_error_count": len(tool_errors),
        "tool_error_samples": tool_errors[:3],
        "text_chars": text_chars,
        "num_turns": result_ev.get("num_turns"),
        "session_id": result_ev.get("session_id"),
        "cost_usd": result_ev.get("total_cost_usd") if result_ev.get("total_cost_usd") is not None else result_ev.get("cost_usd"),
        "duration_ms": result_ev.get("duration_ms"),
    }


def _brief_input(name: str, inp: dict) -> str:
    if name == "Read":
        return str(inp.get("file_path", ""))[:120]
    if name in ("Write", "Edit"):
        return str(inp.get("file_path", ""))[:120]
    if name in ("Skill", "SlashCommand"):
        return str(inp.get("skill") or inp.get("command") or "")[:120]
    if name in ("Grep", "Glob"):
        return str(inp.get("pattern", ""))[:120]
    return json.dumps(inp, ensure_ascii=False)[:120]


def _summarize_tools(tool_uses: list[dict]) -> dict:
    counts: dict[str, int] = {}
    for t in tool_uses:
        counts[t["tool"]] = counts.get(t["tool"], 0) + 1
    return counts


# ---------------------------------------------------------------- 汇总报告

def build_trace_report(
    eval_infos: list[dict],
    score_infos: list[dict],
    *,
    max_turns: int,
) -> dict:
    """把两个阶段的逐案例诊断汇总成一份链路问题清单（喂给优化技能）。"""
    issues: list[dict] = []

    def _check(info: dict, stage: str, skill_name: str, need_output: bool, output_label: str) -> None:
        cid = info.get("case_id", "?")
        tr = info.get("trace") or {}
        if not tr.get("skill_loaded", False):
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": f"{skill_name} 技能未被加载（既没有 Skill 调用，也没有读取 SKILL.md）",
                "severity": "high",
            })
        if stage == "review" and not tr.get("read_case", False):
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": "未读取案例输入文件，检视可能基于空上下文",
                "severity": "high",
            })
        if need_output and not info.get("output_valid", False):
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": f"{output_label}缺失或 JSON 解析失败",
                "detail": info.get("output_error", ""),
                "severity": "high",
            })
        if info.get("timed_out"):
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": "headless 会话超时被终止",
                "severity": "high",
            })
        turns = info.get("trace", {}).get("num_turns") or 0
        if turns and max_turns and turns >= max_turns * 0.8:
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": f"轮次接近上限（{turns}/{max_turns}），可能被截断",
                "severity": "medium",
            })
        err_n = info.get("trace", {}).get("tool_error_count") or 0
        if err_n:
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": f"工具调用出错 {err_n} 次",
                "detail": "；".join(info.get("trace", {}).get("tool_error_samples", [])[:2]),
                "severity": "medium",
            })
        if info.get("attempts", 1) > 1:
            issues.append({
                "stage": stage, "case_id": cid,
                "issue": f"输出重试了 {info['attempts']} 次才成功/仍失败",
                "severity": "medium",
            })

    for info in eval_infos:
        _check(info, "review", "code-review", need_output=True, output_label="检视意见 findings JSON")
    for info in score_infos:
        _check(info, "scoring", "score-review", need_output=True, output_label="评分 verdict JSON")

    def _avg(infos: list[dict], key: str) -> float | None:
        vals = [i.get("trace", {}).get(key) for i in infos]
        vals = [v for v in vals if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 2) if vals else None

    summary = {
        "review": {
            "cases": len(eval_infos),
            "skill_loaded_ratio": _ratio(eval_infos, "skill_loaded"),
            "avg_turns": _avg(eval_infos, "num_turns"),
            "avg_cost_usd": _avg(eval_infos, "cost_usd"),
            "avg_duration_ms": _avg(eval_infos, "duration_ms"),
            "total_tool_errors": sum(i.get("trace", {}).get("tool_error_count") or 0 for i in eval_infos),
        },
        "scoring": {
            "cases": len(score_infos),
            "skill_loaded_ratio": _ratio(score_infos, "skill_loaded"),
            "avg_turns": _avg(score_infos, "num_turns"),
            "avg_cost_usd": _avg(score_infos, "cost_usd"),
            "avg_duration_ms": _avg(score_infos, "duration_ms"),
            "total_tool_errors": sum(i.get("trace", {}).get("tool_error_count") or 0 for i in score_infos),
        },
    }
    return {"issues": issues, "issue_count": len(issues), "stage_summary": summary}


def _ratio(infos: list[dict], key: str) -> float:
    if not infos:
        return 0.0
    return round(sum(1 for i in infos if (i.get("trace") or {}).get(key)) / len(infos), 2)
