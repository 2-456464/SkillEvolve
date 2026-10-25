"""配置加载：config.toml（流程控制）+ config.yaml（三阶段提示词与 CLI 能力）。

优先级：内置 DEFAULTS < config.toml < config.yaml
阶段级配置留空/省略的字段自动继承 claude 全局默认。
"""
from __future__ import annotations

import copy
import json
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

try:
    import yaml as _yaml
except ImportError:  # 仅在 config.yaml 存在且需要解析时报错
    _yaml = None

STAGES = ("review", "score", "optimize")

# 阶段级可覆盖的 CLI 能力字段（prompt 单独处理）
STAGE_KEYS = (
    "model", "api_key", "auth_token", "base_url",
    "max_turns", "timeout_seconds", "retries",
    "permission_mode", "allowed_tools",
)

# 三阶段提示词的内置默认值（config.yaml 缺失对应配置时回退）
DEFAULT_STAGE_PROMPTS = {
    "review": (
        "请使用 code-review 技能完成一次代码检视任务。\n"
        "- 案例文件：cases/{case_id}.json（包含 diff、变更后完整源码 source_code、语言、变更说明）\n"
        "- 请严格按照 code-review 技能中定义的检视流程、检查单与输出要求执行。\n"
        "- 完成后，将检视结果 JSON 写入文件：findings/{case_id}.json（UTF-8，无 BOM）。"
    ),
    "score": (
        "请使用 score-review 技能对代码检视结果进行评分。\n"
        "- 案例文件（含标准答案 ground_truth、diff、源码）：cases/{case_id}.json\n"
        "- 待评检视结果：findings/{case_id}.json\n"
        "- 请严格按照 score-review 技能中的匹配判定规则逐条判定 TP/FP，并找出未命中的 GT。\n"
        "- 完成后，将评分结果 JSON 写入文件：scores/{case_id}.json（UTF-8，无 BOM）。"
    ),
    "optimize": (
        "请使用 optimize-review 技能改进 code-review 代码检视技能。\n"
        "- 当前待优化技能：skill_current/SKILL.md\n"
        "- 错例分析包（FP/FN，含 diff 与原因）：analysis/errors.json\n"
        "- 链路诊断（技能加载/输出格式/工具报错等）：analysis/trace_report.json\n"
        "- 本轮指标与每案例明细：analysis/metrics.json\n"
        "{prev_line}"
        "- 请先通读当前技能与错例，再按 optimize-review 技能中的优化方法执行。\n"
        "- 将完整的新版 SKILL.md 写入：optimized/SKILL.md\n"
        "- 将变更说明 JSON 写入：optimized/changelog.json"
    ),
}

DEFAULTS = {
    "claude": {
        "model": "",
        "api_key": "",
        "auth_token": "",
        "base_url": "",
        "max_turns": 40,
        "timeout_seconds": 900,
        "retries": 1,
        "permission_mode": "default",
        "allowed_tools": ["Read", "Grep", "Glob", "Write", "Skill", "SlashCommand", "TodoWrite"],
    },
    "stages": {s: {"prompt": DEFAULT_STAGE_PROMPTS[s]} for s in STAGES},
    # path 默认留空：空值时不覆盖 toml [paths].dataset，最终回退到 paths 内置默认
    "dataset": {"path": "", "presets": {}},
    "loop": {
        "rounds": 3,
        "concurrency": 3,
        "optimize_last_round": False,
        "cases": [],
    },
    "paths": {
        "dataset": "dataset/eval_set.jsonl",
        "skills_dir": "skills",
        "runs_dir": "runs",
        "web_dir": "web",
    },
    "report": {"progressive": True},
}

_SECRET_KEYS = ("api_key", "auth_token")


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in (override or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _load_yaml(path: Path) -> dict:
    if _yaml is None:
        raise RuntimeError("解析 config.yaml 需要 PyYAML：pip install pyyaml")
    with path.open("r", encoding="utf-8") as f:
        return _yaml.safe_load(f) or {}


def load_config(config_path: str | Path | None = None) -> dict:
    """加载配置。toml 负责流程控制（loop/paths/report），yaml 负责三阶段提示词、CLI 能力与评测集路径。"""
    toml_path = Path(config_path) if config_path else PROJECT_ROOT / "config.toml"
    data: dict = {}
    if toml_path.exists():
        with toml_path.open("rb") as f:
            data = tomllib.load(f)

    yaml_path = toml_path.with_suffix(".yaml")
    if not yaml_path.exists():
        yaml_path = PROJECT_ROOT / "config.yaml"
    if yaml_path.exists():
        data = _deep_merge(data, _load_yaml(yaml_path))

    cfg = _deep_merge(DEFAULTS, data)

    # 评测集路径：config.yaml dataset.path 优先于 config.toml [paths].dataset；
    # path 可直接引用 dataset.presets 中登记的预设名，便于整体切换定制评测集
    ds = cfg.get("dataset") or {}
    p = str(ds.get("path") or "").strip()
    if p:
        presets = ds.get("presets") or {}
        cfg["paths"]["dataset"] = str(presets.get(p) or p)
    return cfg


def resolve_stage_cfg(cfg: dict, stage: str) -> dict:
    """解析某阶段的有效配置：claude 全局默认 + 阶段级覆盖（留空/空列表视为继承）。"""
    if stage not in STAGES:
        raise ValueError(f"未知阶段: {stage}（可选 {STAGES}）")
    eff = dict(cfg["claude"])
    over = (cfg.get("stages") or {}).get(stage) or {}
    for k in STAGE_KEYS:
        v = over.get(k)
        if v is None or v == "" or (isinstance(v, list) and not v):
            continue
        eff[k] = v
    prompt = (over.get("prompt") or "").strip()
    eff["prompt"] = prompt or DEFAULT_STAGE_PROMPTS[stage]
    return eff


def render_prompt(template: str, **vars: str) -> str:
    """按名称替换 {name} 占位符；模板中其他花括号内容（如 JSON 示例）原样保留。"""
    out = template
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def sanitize_cfg(cfg: dict) -> dict:
    """深拷贝并掩码敏感字段（api_key/auth_token），用于写入 run 快照与报告。"""
    out = json.loads(json.dumps(cfg, ensure_ascii=False))

    def walk(d: dict) -> None:
        for k, v in d.items():
            if isinstance(v, dict):
                walk(v)
            elif k in _SECRET_KEYS and isinstance(v, str) and v:
                d[k] = "***"

    walk(out)
    return out


def resolve_path(cfg: dict, key: str) -> Path:
    p = Path(cfg["paths"][key])
    return p if p.is_absolute() else PROJECT_ROOT / p
