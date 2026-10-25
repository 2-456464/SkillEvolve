---
name: optimize-review
description: 基于错例分析（FP/FN）、链路诊断（trace/session 问题）与历史优化记录，改进代码检视技能（code-review SKILL.md），输出新版技能与结构化变更说明。用于 SkillEvolve-CR 优化阶段。
---

# 代码检视技能优化器

## 任务

你将改进 `code-review` 技能（一个代码检视技能），使其在评测中的表现提升：
减少漏报（FN）与误报（FP），并修复执行链路中暴露的问题。

你只能修改 `code-review` 技能这一个文件（SKILL.md），不能修改评分技能、评测集或任何代码。

## 输入

| 文件 | 内容 |
|---|---|
| `skill_current/SKILL.md` | 当前版本的检视技能（待优化对象） |
| `analysis/errors.json` | 错例包：FP（不该报的报了）与 FN（该报的漏了），每条附代码 diff 与原因 |
| `analysis/trace_report.json` | 链路诊断：技能是否被加载、输出是否格式错误、工具报错、轮次/耗时异常等 |
| `analysis/metrics.json` | 本轮 Precision / Recall / F1 / TP / FP / FN 及每案例明细 |
| `analysis/prev_changelog.json` | 上一轮优化说明（首轮时不存在） |

## 优化方法（按顺序执行）

1. **归因 FN**：对每个漏报，回答"为什么当前技能检不出来？"
   - 检查单缺了这一类问题？→ 补充检查项，写出**可操作的判定方法**（什么代码模式要报），不要只写抽象名词。
   - 报了但被判 FP/级别不符？→ 修正严重级别定义或补充边界说明。
2. **归因 FP**：对每个误报，回答"为什么当前技能会引导出这个误报？"
   - 缺"不要报"的排除规则？→ 增加排除条款（什么样的现象不算问题）。
   - 严重级别定义过松导致小事高报？→ 收紧级别定义。
3. **归因链路问题**：trace_report 中的每一条（技能未加载、输出 JSON 非法、行号错误等），
   在技能中加入针对性约束（如输出前自检 JSON 合法性、行号必须取自 source_code 等）。
4. **对照历史**：prev_changelog 中已做过改动的地方不要回退；连续两轮未解决问题的条目要换思路而不是原样重复。
5. **保持克制**：只针对证据（错例+链路问题）做最小必要修改。每一处修改都要能说清"针对哪条错例/问题"。
   不要为了全面而堆砌无法执行的空话，规则数量增长要有节制。

## 硬性约束

- 保留 YAML frontmatter，且 `name` 必须仍为 `code-review`；`description` 可微调。
- **不得改变输出 JSON 的顶层结构**：`case_id` / `findings` / `summary` 与 findings 内
  `id`/`category`/`severity`/`title`/`detail`/`line`/`confidence` 字段必须保留
  （评分流水线依赖该结构）；可以给字段附加更严格的填写规则。
- `severity` 取值范围保持 `critical/high/medium/low`，`category` 取值范围保持
  `security/correctness/robustness/performance/maintainability`。
- 新技能必须是自洽完整的 SKILL.md（不是补丁、不是片段）。

## 输出要求

1. 将**完整的新版 SKILL.md** 写入 `optimized/SKILL.md`（UTF-8）。
2. 将变更说明写入 `optimized/changelog.json`（UTF-8）：

```json
{
  "round": 1,
  "summary": "本轮优化的一句话总结",
  "changes": [
    {
      "type": "added | modified | removed",
      "section": "改动的技能内小节，如 检查单/严重级别/输出要求",
      "description": "具体改了什么",
      "rationale": "为什么改：针对哪条错例或链路问题",
      "addresses": ["FN:case-003", "FP:case-009", "TRACE:case-001"]
    }
  ],
  "invariants_kept": ["frontmatter 与 name 保留", "输出 JSON 顶层结构不变"]
}
```

`addresses` 取值格式：`FN:<案例id>`（修漏报）、`FP:<案例id>`（修误报）、
`TRACE:<案例id|general>`（修链路问题）、`GENERAL:<说明>`（通用改进）。
