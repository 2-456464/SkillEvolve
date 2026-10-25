---
name: score-review
description: 将代码检视产出与标准答案（ground truth）逐条比对，判定 TP/FP 并找出未命中的 GT（FN），输出结构化评分 JSON。用于 SkillEvolve-CR 评分阶段。
---

# 检视意见评分技能

## 任务

对某案例的检视意见（被评对象）与该案例的标准答案（ground truth，简称 GT）做语义匹配，
逐条给出判定，并输出结构化评分结果。**本技能只做判定，不做指标计算**（TP/FP/FN 的数量由流水线汇总）。

## 输入

- 案例文件（JSON）：`ground_truth` 字段为标准答案数组；`diff`/`source_code` 提供代码上下文。
- 检视结果文件（JSON）：`findings` 字段为被评的检视意见数组。

## 匹配判定规则

对每条检视意见（finding），按以下顺序判定：

1. **TP**：存在至少一条 GT 与之语义匹配。匹配标准：
   - 同一根因（如"SQL 拼接"与"f-string 构造 SQL 导致注入"是同一条）；
   - 指向同一处代码或同一逻辑位置（行号允许偏差，以代码位置语义为准）；
   - `severity` 允许一档偏差（如 GT=high，finding=medium 仍算 TP，但要在 reason 中注明 `severity_deviation`）；
   - 措辞、术语、语言不同不影响匹配。
2. **FP**：没有任何 GT 与之匹配。包括：
   - 报告的问题在代码中并不成立（误判代码行为）；
   - 报告的问题在 diff/上下文中无法定位到具体代码；
   - 与另一条已判 TP 的 finding 重复指向同一条 GT（`duplicate_of` 填那条 finding 的 id）；
   - 纯风格/偏好类建议而 GT 中没有对应项。
3. **无法匹配的 GT**：没有任何 finding 命中的 GT，全部列入 `unmatched_gt`（这些构成 FN）。

注意：一条 GT 只能被一条 finding 命中（取最早/最准确的那条），重复命中的其余 findings 判 FP。
干净案例（`ground_truth` 为空数组）：该案例所有 findings 一律判 FP，reason 注明"干净案例"。

## 输出要求

将结果以 UTF-8 JSON（无 BOM、无多余文本）写入调用方指定的输出路径：

```json
{
  "case_id": "案例 id",
  "results": [
    {
      "finding_id": "F-1",
      "verdict": "TP",
      "matched_gt": "GT-1",
      "reason": "同一根因：均指出 SQL 注入，位置一致",
      "severity_deviation": ""
    },
    {
      "finding_id": "F-2",
      "verdict": "FP",
      "matched_gt": null,
      "duplicate_of": null,
      "reason": "代码上下文中不成立：该函数已做空值保护"
    }
  ],
  "unmatched_gt": ["GT-2"],
  "notes": "整体备注（可为空字符串）"
}
```

字段约束：

- `verdict` 只能是 `"TP"` 或 `"FP"`；TP 时 `matched_gt` 必填 GT id，FP 时为 `null`。
- FP 若因重复产生，`duplicate_of` 填被重复的那条 finding id，否则为 `null`。
- `unmatched_gt` 列出所有未被任何 TP 命中的 GT id；GT 为空时为空数组。
- 必须覆盖 findings 中的每一条意见，不得遗漏。
