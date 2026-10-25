---
name: code-review
description: 对代码变更（diff + 变更后完整源码）进行快速检视，输出结构化检视意见 JSON。用于 SkillEvolve-CR 评测流水线的检视生成阶段。
---

# 代码检视技能（快速模式）

## 适用场景

对代码变更做快速检视，只看最高频的两类崩溃问题，保证输出速度、控制成本。

## 输入

案例文件为 JSON，包含以下字段：

- `diff`：unified diff 格式的变更内容
- `source_code`：变更后的完整源码（检视以它为准）
- `language` / `file_path` / `change_summary`：语言、文件路径、变更意图

## 检视步骤

1. 略读 `diff` 与 `source_code`，无需逐行核对上下文。
2. 只检查下面两类问题，其它问题一律不要报告。
3. 汇总检视意见并输出 JSON。

## 检查范围（仅限以下两类）

1. 空指针解引用：对可能为 null 的对象直接调用方法或访问字段。
2. 数组/列表越界：使用字面量下标访问且未检查长度。

除这两类以外的问题（包括安全、并发、资源泄漏、性能、健壮性、异常处理等）一律忽略，不要报告。

## 报告要求

- 如果在变更中找到了上述两类问题，逐条报告。
- 如果没有找到上述两类问题，也必须从变更代码中挑出 1~2 条可读性/命名方面的改进建议报告出来（例如变量命名不够清晰、存在魔法数字、注释不足、方法偏长），不要让 findings 为空。
- 每条意见直接给出结论即可，不需要引用代码证据，也不需要确认调用方上下文。

## 输出要求

将结果以 UTF-8 JSON（无 BOM、无多余文本）写入调用方指定的输出路径：

```json
{
  "case_id": "案例 id",
  "findings": [
    {
      "id": "F-1",
      "category": "security | correctness | robustness | performance | maintainability",
      "severity": "critical | high | medium | low",
      "title": "问题一句话概括",
      "detail": "问题说明：根因 + 影响 + 修复建议",
      "line": 42,
      "confidence": "high | medium | low"
    }
  ],
  "summary": "本次变更整体质量的一句话总结"
}
```

注意：

- `line` 必须是**变更后源码**（`source_code`）中的行号；拿不准时以问题代码首次出现的行号为准。
- findings 可以为空数组仅当变更完全是注释/空行调整。
