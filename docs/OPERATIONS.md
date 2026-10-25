# SkillEvolve-CR 操作要点手册

> 面向日常使用的完整操作参考：配置怎么改、评测集怎么换、闭环怎么跑、结果怎么看、
> 出了问题怎么查。部署相关见 [docs/DEPLOY.md](DEPLOY.md)。

---

## 0. 速查卡

| 我要… | 命令 / 操作 |
|---|---|
| 校验评测集与技能 | `python -m skillevolve validate` |
| 单轮冒烟（不优化，费用最小） | `python -m skillevolve run --no-optimize` |
| 完整 N 轮自优化闭环 | `python -m skillevolve run --rounds 3` |
| 只跑部分案例 | `python -m skillevolve run --cases case-001,case-009` |
| 临时换评测集（免改配置） | `python -m skillevolve run --dataset E:/data/my.jsonl` |
| 临时换模型 | `python -m skillevolve run --model <模型名>` |
| 换起始技能 | `--start-skill main`（默认）/ `baseline` / `weak` |
| 打开 Dashboard | `python main.py`（自动开浏览器） |
| 服务器对外提供 Dashboard | `python main.py --host 0.0.0.0 --port 8766 --no-open` |
| 一键构建/环境预检 | `bash scripts/build.sh` |
| 停止后台运行 | Dashboard 运行监控页「停止」按钮，或 `POST /api/stop` |
| 查看某次运行 | Dashboard 顶部批次选择器；或直接读 `runs/<run_id>/report.json` |

---

## 1. 两种使用姿势

### 1.1 Dashboard（推荐）

```bash
python main.py                          # http://127.0.0.1:8765（自动开浏览器）
```

页面自上而下按执行顺序组织：**评测集 → 运行监控 → 结果分析 · SKILL 演进**。

- **评测集**：全量案例表格（勾选参与案例）、快捷选择、单轮验证 / 多轮优化触发按钮；
- **运行监控**：阶段步骤条 + 实时进度 + 运行日志 + 停止按钮；
- **结果分析 · SKILL 演进**：首轮 vs 末轮指标对比卡、轮次趋势图、逐轮明细表、
  SKILL 版本行级 diff 对照（红删绿增）、链路诊断与 session 执行轨迹时间线。

同一时间只允许一个后台运行（互斥保护）；服务重启后会从本地运行记录推断状态，不会误显空闲。

### 1.2 命令行

```bash
python -m skillevolve run [options]     # 见速查卡；跑完可加 --serve 自动开 Dashboard
```

服务端 API（自建脚本对接）：`POST /api/run`、`POST /api/stop`、`GET /api/runs`、
`GET /api/run-status`、`GET /api/run-log`、`GET /api/dataset`、`GET /api/session-trace` 等。

---

## 2. 配置体系

**分工**：`config.toml` = 流程控制（轮次/并发/路径）；`config.yaml` = 三阶段提示词、
CLI 能力（模型/密钥/端点/预算）、评测集路径。优先级：内置默认 < config.toml < config.yaml。

### 2.1 config.yaml 全字段

```yaml
claude:                  # 全局默认（所有阶段的兜底）
  model: ""              # 留空 = claude CLI 当前默认模型
  api_key: ""            # → ANTHROPIC_API_KEY；留空用本机登录态
  auth_token: ""         # → ANTHROPIC_AUTH_TOKEN（部分网关用）
  base_url: ""           # → ANTHROPIC_BASE_URL（网关端点）
  max_turns: 40          # 单 headless 会话最大轮次
  timeout_seconds: 900   # 单会话超时
  retries: 1             # 输出无效重试次数
  permission_mode: default
  allowed_tools: [Read, Grep, Glob, Write, Skill, SlashCommand, TodoWrite]

stages:                  # 阶段级配置：留空/省略的字段自动继承 claude 全局值
  review:   { prompt: …, model/api_key/base_url/… 可覆盖 }   # ① 检视生成
  score:    { prompt: …, … }                                  # ② 评分
  optimize: { prompt: …, … }                                  # ③ SKILL 优化

dataset:
  path: "dataset/eval_set.jsonl"   # 评测集 JSONL，支持绝对路径
  presets: {}                      # 注册多套评测集后，path 可直接填预设名整体切换
```

占位符：`review/score` 的 prompt 用 `{case_id}`；`optimize` 用 `{prev_line}`。
替换按名称进行，prompt 中的其他花括号内容（如 JSON 示例）原样保留。

### 2.2 config.toml 全字段

```toml
[loop]
rounds = 3                    # 优化迭代轮次
concurrency = 3               # 每阶段并行案例数
optimize_last_round = false   # 最后一轮是否仍执行优化（默认 false：无下一轮验证）
cases = []                    # 参与案例子集，留空 = 全部
start_skill = "main"          # 也可用 CLI --start-skill：main / baseline / weak

[paths]
skills_dir = "skills"
runs_dir = "runs"
web_dir = "web"
# dataset 路径在 config.yaml 配置

[report]
progressive = true            # 每轮结束刷新中间报告（崩溃可恢复）
```

### 2.3 典型配置场景

```yaml
# 场景 A：整体换到某个网关模型
claude:
  model: "deepseek-chat"
  api_key: "sk-xxx"
  base_url: "https://api.deepseek.com/anthropic"

# 场景 B：检视用便宜模型，评分/优化用强模型
stages:
  review:  { model: "cheap-model", api_key: "sk-A", base_url: "https://…" }
  score:   { model: "strong-model", api_key: "sk-B" }
  optimize: { model: "strong-model", api_key: "sk-B" }

# 场景 C：优化阶段允许更多轮次与更长超时
stages:
  optimize: { max_turns: 80, timeout_seconds: 1800 }
```

> CLI `--model` 只覆盖全局默认；阶段级显式配置的 model 优先于它。

---

## 3. 评测集管理

### 3.1 格式规范（JSONL，每行一条案例）

| 字段 | 必填 | 说明 |
|---|---|---|
| `id` | ✓ | 案例唯一标识（不可重复） |
| `language` | ✓ | 语言标识（当前流程为 Java） |
| `file_path` | ✓ | 变更文件路径（溯源字段） |
| `diff` | ✓ | unified diff（与 source_code 一致，评分展示用） |
| `source_code` | ✓ | 变更后完整源码（检视输入 + javac 编译验证） |
| `ground_truth` | ✓ | 标准答案数组；每条含 `id/category/severity/title/detail`（`line_hint` 建议） |

`category` ∈ security / correctness / robustness / performance / maintainability；
`severity` ∈ critical / high / medium / low。案例数 ≥ 3。

### 3.2 替换为个人评测集

```bash
# 方式一（长期）：config.yaml
dataset:
  path: "E:/data/my_cases.jsonl"      # 或服务器绝对路径
  presets:
    my-set: "E:/data/my_cases.jsonl"
    regression: "dataset/eval_set.jsonl"
# 之后 path: "my-set" 即可整体切换

# 方式二（临时）：命令行覆盖
python -m skillevolve run --dataset E:/data/my_cases.jsonl --no-optimize
```

替换后先跑 `python -m skillevolve validate` 校验格式；validate / run / Dashboard
评测集页全部跟随该配置。

### 3.3 构造评测集

编辑 `dataset/build_eval_set.py`：按 `pre`/`post` 源码对 + `ground_truth` 追加案例，
执行后自动用 difflib 生成统一 diff（保证与源码一致）。另有 `build_from_gerrit.py`
支持从 Gerrit 检视数据构建。

---

## 4. 运行生命周期与护栏

每轮完全隔离：**① 检视生成 → ② 评分 → ③ 指标汇总 → ④（可选）SKILL 优化 → 下一轮**。
每案例每阶段都是独立 `claude -p` headless 进程，SKILL 以快照装入隔离子工作区。

**起始 SKILL 三选项**（`--start-skill` / Dashboard 下拉）：

| 值 | 含义 | 适用 |
|---|---|---|
| `main` | skills/code-review/SKILL.md 当前主文件 | 日常 |
| `baseline` | SKILL.baseline.md 初始版本基线 | 演示"从零优化"（主文件不被改动） |
| `weak` | SKILL.weak.md 弱化演示版 | 演示多轮优化（保证优化器每轮真实运行） |

**内置护栏**（防止越改越差）：

1. **编译门槛**：javac 编译失败的案例不进入评测，也不计入 FP/FN；
2. **满分跳过**：本轮 FP=FN=0 且链路无告警 → 跳过优化，防止"为改而改"引入回归；
3. **劣化回退**：优化后版本进入下一轮前，若上轮指标低于历史最佳 → 丢弃优化产物回退最佳版本；
4. **结构校验**：新 SKILL 必须 frontmatter 完整、name 保持 code-review、非空且与旧版不同，
   且保留输出 JSON 契约字段（case_id/findings/summary），否则判 INVALID 沿用旧版；
5. **版本号纪律**：技能版本号只在真实采纳新技能时递增。

**每轮产物**（`runs/<run_id>/round_XX/`）：

```
review_ws/    cases/ findings/ traces/*.stream.jsonl sessions/*.session.json logs/
score_ws/     scores/*.json（逐条 TP/FP 判定 + unmatched_gt）
optimize_ws/  analysis/{errors,trace_report,metrics}.json  optimized/{SKILL.md,changelog.json}
metrics.json  本轮 P/R/F1/TP/FP/FN + per_case
```

run 级：`dataset_snapshot.jsonl`（评测集快照）、`config_snapshot.json`（生效配置，
密钥已掩码）、`skills/skill_v1..vN.md`、`changelogs/`、`report.json`（Dashboard 数据源）。

---

## 5. 结果解读要点

- **指标口径**：TP=意见与某条 GT 语义匹配（同根因同位置）；FP=无 GT 能匹配的意见
  （干净案例上任何意见都是 FP）；FN=未被命中的 GT。评分由 score-review SKILL 判定，
  Python 只做确定性汇总。
- **看优化是否真实生效**：结果分析页对比卡（首轮 vs 末轮，FP/FN 变化标红/绿）、
  逐轮明细表（含每轮优化动作说明）、SKILL 对照 diff（红删绿增行级高亮）。
- **链路诊断**：技能未加载、输出格式错误、工具报错、轮次逼近上限等问题会列入
  问题清单并喂给优化器；session 明细可逐步查看 headless 执行时间线。
- **LLM 随机性**：同配置两次运行指标波动 ±0.1 属正常；单案例结论不外推，
  看整轮聚合指标。

---

## 6. SKILL 版本管理

- 优化产物只存在于 `runs/<run_id>/skills/`，**从不自动回写主文件**；
- 想采纳某次运行的新版本：把对应 `skill_vN.md` 内容复制到
  `skills/code-review/SKILL.md`（Dashboard 结果分析页有版本全文与 diff 辅助比对）；
- 主文件改坏了想回退：Dashboard 运行控制台「主 SKILL 回退初始版本」按钮
  （`POST /api/skill-restore`，用 SKILL.baseline.md 覆盖，主=baseline 时按钮隐藏）。

---

## 7. 故障排查

| 症状 | 原因 | 处置 |
|---|---|---|
| `输出无效 · 已重试` 频繁出现 | 模型没按契约写 findings/scores JSON | 看 logs/*.stderr.log 与链路诊断；必要时加大 retries 或换模型 |
| 每轮指标不动 | 触发满分跳过护栏，属正常 | 想演示优化：换 `--start-skill baseline/weak` 或加难案例 |
| 优化后反而变差 | 已被劣化回退护栏自动处理 | 结果分析页该轮显示"回退到 vN"，无需干预 |
| trace 大量告警 | 链路问题真实存在 | 链路诊断页看问题清单；确认 SKILL 装载与输出路径 |
| 费用超预期 | 案例×轮次×三阶段全在跑 | 先 `--no-optimize` 单轮冒烟；降 concurrency 无助省钱只影响节奏 |
| Windows 下中文 prompt 报错 | npm shim 破坏参数 | 已内置原生 exe 解析；若仍出现检查 claude 安装方式 |
| 页面改版不生效 | 浏览器缓存 | 服务端已发 no-store 头；强制刷新 Ctrl+F5 |
| 报告里 config_snapshot 出现 `***` | 密钥掩码，特性非 bug | 真实密钥只存在于 config.yaml |

---

## 8. FAQ

**Q: 每次运行大约什么成本量级？**
A: 成本 ≈ 案例数 × 轮次 × (检视 + 评分) + 优化次数。单案例单阶段为一次独立 headless 会话；
改动 config.yaml 的模型即可显著改变单价。先用 `--no-optimize --cases <两三条>` 冒烟估量。

**Q: 能不能并发跑多个 run？**
A: Dashboard 内互斥，同一时间一个；多个终端可各自 `python -m skillevolve run`
（注意 API 限流与额度），不同 `--run-id` 隔离。

**Q: 换模型/换网关要改代码吗？**
A: 不用，config.yaml 的 `claude.*` / `stages.*` 全覆盖（model/api_key/auth_token/base_url）。

**Q: 评测集能是别的语言吗？**
A: 流程通用，但编译验证环节当前是 javac（Java）。其他语言案例会走"javac 不可用→跳过"或
编译失败被剔除；需要按语言扩展编译器适配时改 `evaluate.compile_case`。

**Q: 为什么我的 FP/FN 和手工判断不一致？**
A: 判分由 score-review SKILL 按语义匹配规则执行（同根因同位置，措辞/严重级允许一档偏差）；
边界案例可把意见补充进 GT 或复核评分 trace（链路诊断页逐步查看）。
