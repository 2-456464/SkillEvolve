"""SkillEvolve-CR CLI 入口。

  python -m skillevolve validate          校验评测集与技能文件
  python -m skillevolve run [options]     执行自优化闭环
  python -m skillevolve serve [options]   启动 Dashboard
"""
from __future__ import annotations

import argparse
import sys

from . import console
from .config import load_config, resolve_path
from .dataset_check import validate_all
from .loop import run_loop

EPILOG = """\
示例:
  python -m skillevolve validate                                      校验评测集与技能文件
  python -m skillevolve run                                           默认 3 轮 × 全量 10 案例
  python -m skillevolve run --rounds 2 --serve                        跑完自动打开 Dashboard
  python -m skillevolve run --cases case-001,case-009 --run-id smoke  子集冒烟运行
  python -m skillevolve run --no-optimize                             只评测不优化（自动 1 轮）
  python -m skillevolve serve --port 9000                             指定端口查看报告

更多说明见 web 界面「使用指南」标签页或 README.md
"""


def _cmd_validate(args) -> int:
    cfg = load_config(args.config)
    ok, messages = validate_all(
        dataset_path=resolve_path(cfg, "dataset"),
        skills_dir=resolve_path(cfg, "skills_dir"),
    )
    for m in messages:
        print(m)
    if ok:
        print(console.green("✓ 全部通过"))
        return 0
    print(console.red("✗ 存在问题，请修复后重试"))
    return 1


def _cmd_run(args) -> int:
    cfg = load_config(args.config)
    overrides = {
        "loop": {"rounds": args.rounds, "cases": args.cases_list, "concurrency": args.concurrency,
                 "start_skill": args.start_skill},
        "claude": {"model": args.model},
        "paths": {"dataset": args.dataset},
    }
    has_override = any(v is not None for vals in overrides.values() for v in vals.values())
    try:
        run_loop(
            cfg,
            run_id=args.run_id,
            overrides=overrides if has_override else None,
            log=lambda m: print(m, flush=True),
        )
    except FileExistsError as exc:
        print(console.red(f"错误: {exc}"))
        print(console.gray("提示: 用 --run-id 指定新名称，例如  python -m skillevolve run --run-id run-2"))
        return 1
    except FileNotFoundError as exc:
        print(console.red(f"错误: {exc}"))
        print(console.gray("提示: 检查评测集/技能文件路径；并运行 claude --version 确认 Claude Code CLI 可用"))
        return 1
    except ValueError as exc:
        print(console.red(f"错误: {exc}"))
        return 1
    except KeyboardInterrupt:
        print(console.yellow("\n已中断。已完成的轮次数据仍保留在 runs/<run_id>/ 下，可用 serve 查看"))
        return 130
    if args.serve:
        from .server import serve
        return serve(port=args.port, open_browser=True)
    print(console.gray("查看结果: python -m skillevolve serve --open"))
    return 0


def _cmd_serve(args) -> int:
    from .server import serve
    return serve(port=args.port, open_browser=not args.no_open, config_path=args.config, host=args.host)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="skillevolve",
        description="代码检视 SKILL 自优化闭环平台（评测 → 评分 → 优化 → 比对）",
        epilog=EPILOG,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--config", default=None,
                        help="配置文件路径（config.toml；三阶段提示词与模型配置在同级 config.yaml）")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("validate", help="校验评测集与技能文件")

    p_run = sub.add_parser("run", help="执行自优化闭环")
    p_run.add_argument("--rounds", type=int, default=None, help="优化迭代轮次（覆盖 config）")
    p_run.add_argument("--cases", default=None, help="逗号分隔的案例子集，如 case-001,case-009")
    p_run.add_argument("--dataset", default=None, help="评测集 JSONL 路径（覆盖 config.yaml 的 dataset.path，冒烟个人评测集免改配置）")
    p_run.add_argument("--concurrency", type=int, default=None, help="每阶段并行数")
    p_run.add_argument("--model", default=None, help="覆盖全局默认模型（config.yaml 阶段级 model 配置优先）")
    p_run.add_argument("--start-skill", choices=["main", "baseline", "weak"], default=None,
                       help="起始技能：main=当前主 SKILL.md（默认）；baseline=初始版本 SKILL.baseline.md；weak=弱化演示版本 SKILL.weak.md")
    p_run.add_argument("--run-id", default=None, help="指定 run 目录名")
    p_run.add_argument("--no-optimize", action="store_true", help="只评测不优化（自动只跑 1 轮）")
    p_run.add_argument("--serve", action="store_true", help="跑完后自动启动 Dashboard")
    p_run.add_argument("--port", type=int, default=8765, help="Dashboard 端口")

    p_serve = sub.add_parser("serve", help="启动 Dashboard 查看/比对运行结果")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--host", default="127.0.0.1",
                         help="监听地址（服务器部署用 0.0.0.0；Dashboard 无鉴权，公网暴露需自行加访问控制）")
    p_serve.add_argument("--no-open", action="store_true", help="不自动打开浏览器")

    args = parser.parse_args(argv)
    if args.command == "validate":
        return _cmd_validate(args)
    if args.command == "run":
        if args.no_optimize:
            args.rounds = 1
        args.cases_list = [c.strip() for c in args.cases.split(",") if c.strip()] if args.cases else None
        return _cmd_run(args)
    if args.command == "serve":
        return _cmd_serve(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
