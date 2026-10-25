---
name: code-review
description: 对代码变更（diff + 变更后完整源码）进行代码检视，输出结构化检视意见 JSON。用于 SkillEvolve-CR 评测流水线的检视生成阶段。
---

# 代码检视技能

## 适用场景

当需要对一段代码变更（unified diff 及其变更后完整源码）进行检视，
并产出结构化检视意见（JSON）时，使用本技能。

## 输入

案例文件为 JSON，包含以下字段：

- `diff`：unified diff 格式的变更内容
- `source_code`：变更后的完整源码（检视以它为准）
- `language` / `file_path` / `change_summary`：语言、文件路径、变更意图

## 检视步骤

1. 阅读 `change_summary` 理解变更意图。
2. 通读 `diff`，定位所有新增和修改的代码。
3. 结合 `source_code` 的完整上下文理解被改动的函数/类。
4. 按下面的检查单逐类检查。
5. 汇总检视意见并输出。

## 检查单

- 安全：SQL/命令注入、XSS、路径遍历、硬编码凭证、弱加密算法、权限校验缺失
- 正确性：边界条件、空值/空集合、类型错误、并发与数据竞争、资源泄漏
- 健壮性：异常处理、输入校验、超时与重试
- 性能：N+1 查询、循环内重复计算、不必要的全量加载
- 可维护性：命名、重复代码、魔法数字

## 严重级别定义

| 级别 | 含义 |
|---|---|
| critical | 可被直接利用，或必然导致线上事故 |
| high | 大概率引发缺陷或安全事故 |
| medium | 特定条件下出错，或造成明确维护负担 |
| low | 建议级改进 |

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

- `line` 必须是**变更后源码**（`source_code`）中的行号；拿不准时以问题代码首次出现的行号为 准。
- 只报告真实、能落到具体代码位置的问题；不确定的问题降低 `confidence`，不要夸大严重级别。
- 不要为了凑数报告纯风格意见。
