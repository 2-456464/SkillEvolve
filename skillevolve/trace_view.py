"""stream-jsonl → Langfuse 风格执行轨迹（供 Dashboard /api/session-trace 使用）。

从 claude CLI 的 stream-json 事件流中提取有效执行步骤：
  system/init            → 会话初始化（模型、工作区、工具集）
  assistant/thinking     → 思考块（含思考耗时）
  assistant/tool_use     → 工具调用（名称 + 输入摘要）
  user/tool_result       → 工具结果（与 tool_use 配对，计算耗时）
  assistant/text         → 最终文本输出
  result                 → 会话汇总（总时长 / 轮次 / 费用）
其余事件（thinking_tokens 心跳、hook_* 噪声，单 session 可达 1 万+ 条）全部丢弃。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

TEXT_LIMIT = 600    # 思考/文本块截断长度
IO_LIMIT = 280      # 工具输入/结果截断长度
MAX_STEPS = 400     # 时间线最大步数


def _trim(s: object, limit: int) -> str:
    s = ("" if s is None else str(s)).strip()
    if len(s) <= limit:
        return s
    return s[:limit] + f" …（截断，共 {len(s)} 字符）"


def _ts(v) -> float | None:
    if not v:
        return None
    try:
        return datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def _result_text(content) -> str:
    """tool_result.content 可能是 str / 块数组 / dict。"""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for b in content:
            if isinstance(b, dict):
                if b.get("type") == "text":
                    parts.append(b.get("text") or "")
                else:
                    parts.append(json.dumps(b, ensure_ascii=False))
        return "\n".join(p for p in parts if p)
    if isinstance(content, dict):
        return json.dumps(content, ensure_ascii=False)
    return "" if content is None else str(content)


def parse_stream(path: Path) -> dict:
    out: dict = {"session_id": None, "model": None, "cwd": None,
                 "steps": [], "result": None, "total_events": 0}
    if not path.exists():
        return out

    pending: dict = {}  # tool_use_id -> step（等待配对 tool_result）
    prev_ts: float | None = None
    with path.open("r", encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                continue
            out["total_events"] += 1
            t = e.get("type")
            ts = _ts(e.get("timestamp"))
            gap_ms = int((ts - prev_ts) * 1000) if (ts is not None and prev_ts is not None) else None
            if ts is not None:
                prev_ts = ts

            if t == "system":
                if e.get("subtype") == "init":
                    out["session_id"] = e.get("session_id")
                    out["model"] = e.get("model")
                    out["cwd"] = e.get("cwd")
                    out["steps"].append({
                        "kind": "init", "ts": e.get("timestamp"),
                        "detail": f"模型 {e.get('model')} · 工作区 {Path(e.get('cwd') or '.').name}"
                                  f" · 可用工具 {len(e.get('tools') or [])} 个",
                    })
                continue  # thinking_tokens / hook_* 噪声

            if t == "assistant":
                for b in (e.get("message") or {}).get("content") or []:
                    bt = b.get("type")
                    if bt == "thinking":
                        out["steps"].append({
                            "kind": "thinking", "ts": e.get("timestamp"), "gap_ms": gap_ms,
                            "dur_ms": e.get("thinking_duration_ms"),
                            "text": _trim(b.get("thinking"), TEXT_LIMIT),
                        })
                    elif bt == "text":
                        txt = b.get("text") or ""
                        if txt.strip():
                            out["steps"].append({
                                "kind": "text", "ts": e.get("timestamp"), "gap_ms": gap_ms,
                                "text": _trim(txt, TEXT_LIMIT),
                            })
                    elif bt == "tool_use":
                        step = {
                            "kind": "tool", "ts": e.get("timestamp"), "gap_ms": gap_ms,
                            "name": b.get("name"),
                            "input": _trim(json.dumps(b.get("input"), ensure_ascii=False), IO_LIMIT),
                        }
                        out["steps"].append(step)
                        if b.get("id"):
                            pending[b["id"]] = step

            elif t == "user":
                content = (e.get("message") or {}).get("content")
                if isinstance(content, list):
                    for b in content:
                        if isinstance(b, dict) and b.get("type") == "tool_result":
                            step = pending.pop(b.get("tool_use_id"), None)
                            if step:
                                start = _ts(step.get("ts"))
                                if ts is not None and start is not None:
                                    step["dur_ms"] = int((ts - start) * 1000)
                                step["result"] = _trim(_result_text(b.get("content")), IO_LIMIT)
                                step["ok"] = not b.get("is_error")

            elif t == "result":
                out["result"] = {
                    "duration_ms": e.get("duration_ms"),
                    "num_turns": e.get("num_turns"),
                    "cost_usd": e.get("total_cost_usd"),
                    "stop_reason": e.get("stop_reason"),
                    "is_error": e.get("is_error"),
                }

    if len(out["steps"]) > MAX_STEPS:
        dropped = len(out["steps"]) - MAX_STEPS
        out["steps"] = out["steps"][:MAX_STEPS]
        out["steps"].append({"kind": "truncated", "detail": f"…另有 {dropped} 步未展示（超出时间线上限）"})
    return out
