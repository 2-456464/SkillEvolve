"""阶段①：检视生成 —— code-review SKILL (headless) 为每个案例产出检视意见。"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from . import console
from .claude_runner import run_headless
from .config import render_prompt, resolve_stage_cfg
from .trace_diagnostics import analyze_trace


def compile_case(case: dict) -> tuple[bool, str]:
    """javac 编译案例的变更后源码，返回 (ok, 错误输出尾部)。"""
    m = re.search(r"public\s+(?:abstract\s+|final\s+)?class\s+(\w+)", case["source_code"])
    cls = m.group(1) if m else "Case"
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / f"{cls}.java"
        src.write_text(case["source_code"], encoding="utf-8")
        try:
            proc = subprocess.run(
                ["javac", "-encoding", "utf-8", "-d", td, str(src)],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60,
            )
        except FileNotFoundError:
            return True, "javac 不可用（未安装 JDK），跳过编译验证"
        except subprocess.TimeoutExpired:
            return False, "javac 编译超时（60s）"
        if proc.returncode != 0:
            return False, ((proc.stderr or proc.stdout) or "javac 无输出")[-800:]
        return True, ""


def verify_compiles(cases: list[dict], log=print) -> tuple[list[dict], list[dict]]:
    """评测前置门槛：逐条编译验证案例源码，返回 (可评测案例, 编译失败记录)。

    编译失败 = 案例本身无效（无法作为合法检视对象），不进入检视/评分，
    也不计入 FP/FN。
    """
    failures: list[dict] = []
    ok_cases: list[dict] = []
    for c in cases:
        ok, err = compile_case(c)
        state = console.green("OK") if ok else console.red("FAIL")
        err_tail = err.splitlines()[-1][:120] if err else ""
        log(f"    [compile] {c['id']}: {state}" + ("" if ok else console.gray(f" · {err_tail}")))
        if ok:
            ok_cases.append(c)
        else:
            failures.append({"case_id": c["id"], "error": err})
    return ok_cases, failures


def prepare_workspace(ws: Path, skill_text: str, cases: list[dict]) -> None:
    (ws / ".claude" / "skills" / "code-review").mkdir(parents=True, exist_ok=True)
    (ws / ".claude" / "skills" / "code-review" / "SKILL.md").write_text(
        skill_text, encoding="utf-8", newline="\n"
    )
    (ws / "cases").mkdir(parents=True, exist_ok=True)
    (ws / "findings").mkdir(parents=True, exist_ok=True)
    (ws / "traces").mkdir(parents=True, exist_ok=True)
    (ws / "sessions").mkdir(parents=True, exist_ok=True)
    (ws / "logs").mkdir(parents=True, exist_ok=True)
    for case in cases:
        (ws / "cases" / f"{case['id']}.json").write_text(
            json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
        )


def parse_json_lenient(text: str):
    """容错解析 JSON：容忍 BOM 与 markdown 代码围栏。抛 ValueError 表示不可解析。"""
    t = text.strip()
    if t.startswith("﻿"):
        t = t.lstrip("﻿")
    if t.startswith("```"):
        first_nl = t.find("\n")
        if first_nl != -1:
            t = t[first_nl + 1:]
        if t.rstrip().endswith("```"):
            t = t.rstrip()[:-3]
    return json.loads(t)


def _load_findings(ws: Path, case_id: str):
    path = ws / "findings" / f"{case_id}.json"
    if not path.exists():
        return None, "findings 文件未生成"
    try:
        data = parse_json_lenient(path.read_text(encoding="utf-8-sig"))
    except (ValueError, OSError) as exc:
        return None, f"findings JSON 解析失败: {exc}"
    if not isinstance(data, dict) or not isinstance(data.get("findings"), list):
        return None, "findings 结构不合法（缺少 findings 数组）"
    return data, ""


def _run_case(case: dict, ws: Path, cfg: dict) -> dict:
    case_id = case["id"]
    stage = resolve_stage_cfg(cfg, "review")
    attempts = 0
    findings, err = None, "未执行"
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
        findings, err = _load_findings(ws, case_id)
        if findings is not None:
            break
    info = {
        "case_id": case_id,
        "ok": result.ok if result else False,
        "attempts": attempts,
        "findings": (findings or {}).get("findings", []),
        "summary": (findings or {}).get("summary", ""),
        "output_valid": findings is not None,
        "output_error": err,
        "timed_out": result.timed_out if result else False,
        "session": result.session if result else {},
        "trace": {},
    }
    if result:
        info["trace"] = analyze_trace(
            result.events,
            stage="review",
            case_id=case_id,
            skill_name="code-review",
            output_marker="findings",
        )
    return info


def evaluate_round(ws: Path, cases: list[dict], cfg: dict, log=print) -> list[dict]:
    """对一个轮次执行检视生成，返回逐案例记录。"""
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
                f"    [review] {console.gray(f'[{len(infos)}/{total}]')} {info['case_id']}: {state}"
                f" findings={len(info['findings'])} turns={info['trace'].get('num_turns') or '-'} {dur_txt}{retry}"
            )
    infos.sort(key=lambda i: i["case_id"])
    log(console.gray(f"    ↳ 检视阶段完成 · 用时 {console.fmt_dur(time.monotonic() - t0)}"))
    return infos
