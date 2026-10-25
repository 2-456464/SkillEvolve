"""评测集与技能文件校验。"""
from __future__ import annotations

import json
from pathlib import Path

REQUIRED_CASE_FIELDS = ("id", "language", "file_path", "diff", "source_code", "ground_truth")
REQUIRED_GT_FIELDS = ("id", "category", "severity", "title", "detail")
VALID_SEVERITY = {"critical", "high", "medium", "low"}
VALID_CATEGORY = {"security", "correctness", "robustness", "performance", "maintainability"}
REQUIRED_SKILLS = ("code-review", "score-review", "optimize-review")


def _check_skill(skills_dir: Path, name: str, messages: list[str]) -> bool:
    path = skills_dir / name / "SKILL.md"
    if not path.exists():
        messages.append(f"[FAIL] 缺少技能文件: {path}")
        return False
    text = path.read_text(encoding="utf-8")
    if not text.lstrip().startswith("---"):
        messages.append(f"[FAIL] {path}: 缺少 YAML frontmatter")
        return False
    head = "\n".join(text.lstrip().splitlines()[:10])
    if f"name: {name}" not in head:
        messages.append(f"[FAIL] {path}: frontmatter 中应包含 name: {name}")
        return False
    if "description:" not in head:
        messages.append(f"[FAIL] {path}: frontmatter 中缺少 description")
        return False
    messages.append(f"[ OK ] 技能 {name}（{len(text)} 字符）")
    return True


def validate_all(dataset_path: Path, skills_dir: Path) -> tuple[bool, list[str]]:
    ok = True
    messages: list[str] = []

    if not dataset_path.exists():
        return False, [f"[FAIL] 评测集不存在: {dataset_path}（检查 config.yaml 的 dataset.path，"
                       f"或先运行 python dataset/build_eval_set.py 生成默认集）"]

    seen_ids = set()
    gt_total = 0
    n_cases = 0
    with dataset_path.open("r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            n_cases += 1
            try:
                case = json.loads(line)
            except json.JSONDecodeError as exc:
                ok = False
                messages.append(f"[FAIL] 评测集第 {lineno} 行不是合法 JSON: {exc}")
                continue
            missing = [k for k in REQUIRED_CASE_FIELDS if k not in case]
            if missing:
                ok = False
                messages.append(f"[FAIL] 第 {lineno} 行缺少字段: {missing}")
                continue
            if case["id"] in seen_ids:
                ok = False
                messages.append(f"[FAIL] 案例 id 重复: {case['id']}")
            seen_ids.add(case["id"])
            for g in case["ground_truth"]:
                gt_total += 1
                gm = [k for k in REQUIRED_GT_FIELDS if k not in g]
                if gm:
                    ok = False
                    messages.append(f"[FAIL] {case['id']} 标准答案缺少字段: {gm}")
                if g.get("severity") not in VALID_SEVERITY:
                    ok = False
                    messages.append(f"[FAIL] {case['id']} {g.get('id')}: severity 非法: {g.get('severity')}")
                if g.get("category") not in VALID_CATEGORY:
                    ok = False
                    messages.append(f"[FAIL] {case['id']} {g.get('id')}: category 非法: {g.get('category')}")
            if not case.get("diff"):
                ok = False
                messages.append(f"[FAIL] {case['id']}: diff 为空")
            if not case.get("source_code"):
                ok = False
                messages.append(f"[FAIL] {case['id']}: source_code 为空")

    messages.append(f"[ OK ] 评测集: {n_cases} 条案例，标准答案共 {gt_total} 条")
    if n_cases < 3:
        ok = False
        messages.append("[FAIL] 案例数过少（<3）")

    for name in REQUIRED_SKILLS:
        if not _check_skill(skills_dir, name, messages):
            ok = False

    return ok, messages
