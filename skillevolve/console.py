"""终端输出助手：零依赖彩色输出（Windows 10+ / Linux / macOS 兼容）。

非 TTY（如重定向到文件）时自动降级为纯文本，保证日志文件干净可读。
"""
from __future__ import annotations

import os
import sys

if sys.platform == "win32":
    os.system("")  # 激活 ANSI 转义序列支持（Windows 10+）

USE_COLOR = bool(sys.stdout.isatty()) and not os.environ.get("NO_COLOR")


def _c(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if USE_COLOR else str(text)


def bold(t: str) -> str:
    return _c("1", t)


def dim(t: str) -> str:
    return _c("2", t)


def red(t: str) -> str:
    return _c("91", t)


def green(t: str) -> str:
    return _c("92", t)


def yellow(t: str) -> str:
    return _c("93", t)


def blue(t: str) -> str:
    return _c("94", t)


def magenta(t: str) -> str:
    return _c("95", t)


def cyan(t: str) -> str:
    return _c("96", t)


def gray(t: str) -> str:
    return _c("90", t)


def fmt_dur(sec: float | None) -> str:
    """把秒数格式化为人类可读时长：45s / 3m12s / 1h02m。"""
    if sec is None:
        return "-"
    sec = int(sec)
    if sec < 60:
        return f"{sec}s"
    m, s = divmod(sec, 60)
    if m < 60:
        return f"{m}m{s:02d}s"
    h, m = divmod(m, 60)
    return f"{h}h{m:02d}m"


def fmt_cost(v: float | None) -> str:
    """把费用格式化为 $0.0835 形式；None 显示 -。"""
    if v is None:
        return "-"
    return f"${float(v):.4f}"
