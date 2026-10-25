"""Claude Code headless 运行器。

以 `claude -p --output-format stream-json --verbose` 方式调用 CLI，
完整采集 stream-json 事件作为 trace，从 result 事件提取 session 记录
（session_id / 轮次 / 费用 / 时长 / token 用量）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path


def resolve_claude_command(binary: str = "claude") -> list[str]:
    """解析 claude CLI 启动命令。

    关键点：npm 的 Windows shim（claude.ps1 / claude.cmd）转发参数时会破坏含空格的中文
    prompt（引号被剥离 / 参数被重新分词），导致 --output-format stream-json 等 flag 被
    吞进 prompt、运行退化为纯文本模式。因此必须优先定位其背后的原生可执行文件：
      <npm目录>/node_modules/@anthropic-ai/claude-code/bin/claude.exe
    只有找不到原生 exe 时才退回 shell shim 方式。
    """
    found = shutil.which(binary) or shutil.which(binary + ".exe")
    if not found:
        raise FileNotFoundError(
            "未找到 claude CLI。请先安装：npm install -g @anthropic-ai/claude-code"
        )
    lower = found.lower()
    if lower.endswith((".exe", ".com")):
        return [found]

    base_dir = Path(found).resolve().parent
    native_candidates = [
        base_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / "claude.exe",
        base_dir / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js",
    ]
    for cand in native_candidates:
        if cand.exists():
            if cand.suffix.lower() == ".exe":
                return [str(cand)]
            node = shutil.which("node")
            if node:
                return [node, str(cand)]

    if lower.endswith((".cmd", ".bat")):
        return ["cmd", "/c", found]
    if lower.endswith(".ps1"):
        return ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", found]
    return [found]


@dataclass
class ClaudeRunResult:
    ok: bool = False
    timed_out: bool = False
    returncode: int | None = None
    events: list[dict] = field(default_factory=list)
    result_event: dict = field(default_factory=dict)
    session: dict = field(default_factory=dict)
    stderr_tail: str = ""
    duration_wall_ms: int = 0


def run_headless(
    prompt: str,
    cwd: str | Path,
    *,
    model: str = "",
    max_turns: int = 40,
    timeout_seconds: int = 900,
    allowed_tools: list[str] | None = None,
    permission_mode: str = "default",
    api_key: str = "",
    auth_token: str = "",
    base_url: str = "",
    trace_path: str | Path | None = None,
    stderr_log_path: str | Path | None = None,
    on_event=None,
) -> ClaudeRunResult:
    """在 cwd 中以 headless 模式运行 claude，返回采集到的 trace 与 session 信息。

    api_key / auth_token / base_url 非空时注入对应环境变量
    （ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN / ANTHROPIC_BASE_URL），
    用于按阶段覆盖模型渠道；留空则沿用本机已有环境。
    """
    cmd = resolve_claude_command()
    cmd += [
        "-p", prompt,
        "--output-format", "stream-json",
        "--verbose",
        "--max-turns", str(max_turns),
    ]
    if model:
        cmd += ["--model", model]
    if allowed_tools:
        cmd += ["--allowedTools", ",".join(allowed_tools)]
    if permission_mode and permission_mode != "default":
        cmd += ["--permission-mode", permission_mode]

    trace_fh = None
    if trace_path is not None:
        Path(trace_path).parent.mkdir(parents=True, exist_ok=True)
        trace_fh = open(trace_path, "w", encoding="utf-8")

    started = time.monotonic()
    out = ClaudeRunResult()
    stderr_lines: list[str] = []

    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    if api_key:
        env["ANTHROPIC_API_KEY"] = api_key
    if auth_token:
        env["ANTHROPIC_AUTH_TOKEN"] = auth_token
    if base_url:
        env["ANTHROPIC_BASE_URL"] = base_url.rstrip("/")

    try:
        proc = subprocess.Popen(
            cmd,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
        )
    except OSError as exc:
        if trace_fh:
            trace_fh.close()
        out.stderr_tail = f"启动失败: {exc}"
        out.duration_wall_ms = int((time.monotonic() - started) * 1000)
        return out

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            stderr_lines.append(line)
            if stderr_log_path is not None:
                try:
                    with open(stderr_log_path, "a", encoding="utf-8") as f:
                        f.write(line)
                except OSError:
                    pass

    def _read_stdout() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            if trace_fh is not None:
                try:
                    trace_fh.write(line + "\n")
                    trace_fh.flush()
                except OSError:
                    pass
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            out.events.append(ev)
            if on_event is not None:
                try:
                    on_event(ev)
                except Exception:
                    pass

    stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
    stdout_thread = threading.Thread(target=_read_stdout, daemon=True)
    stderr_thread.start()
    stdout_thread.start()

    try:
        proc.wait(timeout=timeout_seconds)
    except subprocess.TimeoutExpired:
        out.timed_out = True
        proc.kill()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pass

    stdout_thread.join(timeout=15)
    stderr_thread.join(timeout=5)
    if trace_fh is not None:
        trace_fh.close()

    out.returncode = proc.returncode
    out.ok = (not out.timed_out) and proc.returncode == 0
    out.duration_wall_ms = int((time.monotonic() - started) * 1000)
    out.stderr_tail = "".join(stderr_lines)[-4000:]

    if stderr_log_path is not None and out.stderr_tail:
        try:
            Path(stderr_log_path).parent.mkdir(parents=True, exist_ok=True)
            Path(stderr_log_path).write_text(out.stderr_tail, encoding="utf-8")
        except OSError:
            pass

    # 提取 result 事件 → session 记录
    for ev in out.events:
        if ev.get("type") == "result":
            out.result_event = ev
    r = out.result_event
    init_ev = next((e for e in out.events if e.get("type") == "system"), {})
    out.session = {
        "session_id": r.get("session_id"),
        "model": r.get("model") or init_ev.get("model") or model or None,
        "num_turns": r.get("num_turns"),
        "duration_ms": r.get("duration_ms"),
        "duration_wall_ms": out.duration_wall_ms,
        "cost_usd": r.get("total_cost_usd") if r.get("total_cost_usd") is not None else r.get("cost_usd"),
        "usage": r.get("usage"),
        "is_error": bool(r.get("is_error")),
        "subtype": r.get("subtype"),
        "timed_out": out.timed_out,
        "returncode": out.returncode,
    }
    return out
