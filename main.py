#!/usr/bin/env python3
"""SkillEvolve-CR 后端服务入口。

启动 Dashboard（前后端一体：Python 服务同时托管 web/ 静态资产与 API）。

  python main.py                                    # http://127.0.0.1:8765，自动开浏览器
  python main.py --no-open                          # 不开浏览器
  python main.py --host 0.0.0.0 --port 8765         # 服务器部署（无鉴权，暴露前加访问控制）

评测/校验等完整 CLI 仍走 python -m skillevolve（见 README.md）。
"""
from __future__ import annotations

import argparse


def main() -> int:
    parser = argparse.ArgumentParser(
        prog="main.py",
        description="SkillEvolve-CR 后端服务入口（Dashboard = 静态前端 + API，由本进程一体托管）",
    )
    parser.add_argument("--host", default="127.0.0.1",
                        help="监听地址，默认 127.0.0.1；服务器部署用 0.0.0.0")
    parser.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    parser.add_argument("--no-open", action="store_true", help="不自动打开浏览器")
    args = parser.parse_args()

    from skillevolve.server import serve
    return serve(port=args.port, host=args.host, open_browser=not args.no_open)


if __name__ == "__main__":
    raise SystemExit(main())
