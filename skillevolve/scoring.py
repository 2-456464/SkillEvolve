"""阶段②：评分 —— score-review SKILL (headless) 将检视意见与标准答案逐条比对。"""
from __future__ import annotations

import json
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import console
from .claude_runner import run_headless
from .config import render_prompt, resolve_stage_cfg
from .evaluate import parse_json_lenient
from .trace_diagnostics import analyze_trace


def prepare_workspace(ws: Path, review_ws: Path, cases: list[dict]) -> None:
    (ws / ".claude" / "skills" / "score-review").mkdir(parents=True, exist_ok=True)
    skill_src = Path(__file__).resolve().parent.parent / "skills" / "score-review" / "SKILL.md"
    shutil.copyfile(skill_src, ws / ".claude" / "skills" / "score-review" / "SKILL.md")
    (ws / "cases").mkdir(parents=True, exist_ok=True)
    (ws / "findings").mkdir(parents=True, exist_ok=True)
    (ws / "scores").mkdir(parents=True, exist_ok=True)
    (ws / "traces").mkdir(parents=True, exist_ok=True)
    (ws / "sessions").mkdir(parents=True, exist_ok=True)
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    for case in cases:
        shutil.copyfile(
            review_ws / "cases" / f"{case['id']}.json",
            ws / "cases" / f"{case['id']}.json",
        )
        src = review_ws / "findings" / f"{case['id']}.json"
        if src.exists():
            shutil.copyfile(src, ws / "findings" / f"{case['id']}.json")
        else:
            # 检视阶段输出缺失：以空结果进入评分（该案例全部 GT 记为 FN）
            (ws / "findings" / f"{case['id']}.json").write_text(
                json.dumps({"case_id": case["id"], "findings": [], "summary": ""}),
                encoding="utf-8", newline="\n",
            )


def _load_verdicts(ws: Path, case_id: str):
    path = ws / "scores" / f"{case_id}.json"
    if not path.exists():
        return None, "scores 文件未生成"
    try:
        data = parse_json_lenient(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        return None, f"scores JSON 解析失败: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("results"), list):
        return None, "scores 结构不合法（缺少 results 数组）"
    return data, ""


def _run_case(case: dict, ws: Path, cfg: dict) -> dict:
    case_id = case["id"]
    stage = resolve_stage_cfg(cfg, "score")
    attempts = 0
    verdicts, err = None, "未执行"
    result = None
    while attempts <= stage["retries"]:
        attempts += 1
        suffix = "" if attempts == 1 else f"（第 {attempts} 次尝试，上次输出无效，请确保写出合法 JSON 文件）"
        result = run_headless(
            render_prompt(stage["prompt"], case_id=case_id) + suffix,
            cwd=ws,
            model=stage["model"],
            max_turns=stage["max_turns"],
            timeout_seconds=stage["timeout_seconds"],
            allowed_tools=stage["allowed_tools"],
            permission_mode=stage["permission_mode"],
            api_key=stage["api_key"],
            auth_token=stage["auth_token"],
            base_url=stage["base_url"],
            trace_path=ws / "traces" / f"{case_id}.stream.jsonl",
            stderr_log_path=ws / "logs" / f"{case_id}.stderr.log",
        )
        (ws / "sessions" / f"{case_id}.session.json").write_text(
            json.dumps(result.session, ensure_ascii=False, indent=2),
            encoding="utf-8", newline="\n",
        )
        verdicts, err = _load_verdicts(ws, case_id)
        if verdicts is not None:
            break
    info = {
        "case_id": case_id,
        "ok": result.ok if result else False,
        "attempts": attempts,
        "results": (verdicts or {}).get("results", []),
        "unmatched_gt": (verdicts or {}).get("unmatched_gt", []),
        "notes": (verdicts or {}).get("notes", ""),
        "output_valid": verdicts is not None,
        "output_error": err,
        "timed_out": result.timed_out if result else False,
        "session": result.session if result else {},
        "trace": {},
    }
    if result:
        info["trace"] = analyze_trace(
            result.events,
            stage="scoring",
            case_id=case_id,
            skill_name="score-review",
            output_marker="scores",
        )
    return info


def score_round(ws: Path, cases: list[dict], cfg: dict, log=print) -> list[dict]:
    infos: list[dict] = []
    total = len(cases)
    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=cfg["loop"]["concurrency"]) as pool:
        futures = {pool.submit(_run_case, c, ws, cfg): c["id"] for c in cases}
        for fut in as_completed(futures):
            info = fut.result()
            infos.append(info)
            dur = (info.get("session") or {}).get("duration_ms")
            dur_txt = console.fmt_dur(dur / 1000) if dur else "-"
            state = console.green("OK") if info["output_valid"] else console.red("FAIL")
            retry = console.yellow(f" ·重试{info['attempts'] - 1}") if info["attempts"] > 1 else ""
            log(
                f"    [score ] {console.gray(f'[{len(infos)}/{total}]')} {info['case_id']}: {state}"
                f" results={len(info['results'])} {dur_txt}{retry}"
            )
    infos.sort(key=lambda i: i["case_id"])
    log(console.gray(f"    ↳ 评分阶段完成 · 用时 {console.fmt_dur(time.monotonic() - t0)}"))
    return infos
