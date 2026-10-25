# SkillEvolve-CR · 代码检视 SKILL 自优化闭环平台

> 为**代码检视 SKILL** 构建的「生成 → 评分 → 优化 → 比对」可验收进化流水线。
> 全程使用 **Claude Code headless** 模式，三阶段独立配置模型/密钥/提示词，评测集可整体替换。

**文档导航**：服务器部署细节 → [docs/DEPLOY.md](docs/DEPLOY.md) · 日常操作与配置全集 → [docs/OPERATIONS.md](docs/OPERATIONS.md) · 三轮执行链路图 → [docs/execution-flow.html](docs/execution-flow.html)（浏览器直接打开）

---

## 1. 先搞清楚三件事

**① 前端无需任何构建。**
`web/` 是原生 HTML/JS/CSS，Vue3 与 Element Plus 以 vendor 文件内置，没有 npm/webpack/打包器。
Dashboard 由 Python 服务（`skillevolve/server.py`）直接托管 `web/` 目录——启动一个 Python 命令，
前后端就都在了。改前端 = 改文件 + 浏览器刷新（服务端已带 no-store 缓存头，即改即生效）。

**② 页面上的所有操作都对应 python 命令。**
Dashboard 只是一层薄壳：触发类操作在后台执行 `python -m skillevolve run`，展示类操作只读取
`runs/<run_id>/` 产物文件，不掺入任何独立逻辑。对照关系：

| 页面操作 | 等价命令 |
|---|---|
| 评测集页勾选案例 → 点「单轮验证」 | `python -m skillevolve run --no-optimize --cases case-121,case-122` |
| 勾选案例 → 点「多轮优化」（选轮次/起始 SKILL） | `python -m skillevolve run --rounds 2 --start-skill main` |
| 临时换评测集（页面不可配时） | `python -m skillevolve run --dataset <路径>` |
| 运行监控页 | 运行进程实时日志的网页化展示 |
| 停止按钮 | 终止运行进程（等价 Ctrl+C） |
| 结果分析 · 链路诊断 | 只读 `runs/<run_id>/report.json` 与 trace 产物，无对应命令 |

**③ 一次运行 = 一份完整产物。**
每次运行在 `runs/<run_id>/` 下留全套证据（评测集/配置快照、每轮 SKILL 版本、trace、session 费用、
报告），页面只是这份产物的可视化阅读器。删除 runs/ 不影响系统，只丢历史。

## 2. 一图看懂

```
                    ┌──────────────────────────────────────────────────┐
                    │                评测集 eval_set.jsonl (24 条)      │
                    │   diff + 变更后源码 + 语言 + 标准答案(GT findings) │
                    └──────────────────────────────────────────────────┘
                                       │ 每轮全量、独立进程、隔离子工作区
        ┌──────────────────────────────▼───────────────────────────────┐
        │  Round N                                                     │
        │  ① 检视生成  code-review SKILL (headless) → findings/*.json   │
        │  ② 评分      score-review SKILL (headless) → TP/FP/FN + P/R/F1│
        │  ③ SKILL优化 optimize-review SKILL (headless) → 新版 SKILL.md │
        └──────────────────────────────┬───────────────────────────────┘
                                       │ 新版 SKILL 进入 Round N+1
        ┌──────────────────────────────▼───────────────────────────────┐
        │  report.json + Web Dashboard：轮次趋势 · 逐轮明细 · SKILL diff │
        └──────────────────────────────────────────────────────────────┘
```

## 3. 启动方式 A · 本机直接跑（Windows / Linux 同一套命令）

前置：**Python 3.11+**、**claude CLI**（`npm install -g @anthropic-ai/claude-code`）、
评测含 Java 案例需 JDK（javac 在 PATH）。

```bash
pip install pyyaml                        # 唯一第三方依赖
python -m skillevolve validate            # 校验评测集与技能文件
python main.py                            # 启动 Dashboard → http://127.0.0.1:8765（自动开浏览器）
```

然后浏览器打开页面，在「评测集」页勾选案例、点「单轮验证」或「多轮优化」即可触发。
常用命令参数：

```bash
python -m skillevolve run --no-optimize               # 只评测不优化（单轮）
python -m skillevolve run --rounds 3                  # 三轮完整闭环
python -m skillevolve run --cases case-121,case-109   # 只跑部分案例（冒烟）
python -m skillevolve run --dataset E:\data\my.jsonl  # 临时换评测集
python main.py --port 8766                # 换端口（等价旧写法：python -m skillevolve serve）
```

平台差异只有一点：Linux/macOS 可选 `bash scripts/build.sh`（自动建 venv + 全量预检），
Windows 直接按上面命令跑即可。CLI 认证两种方式（本机已登录 或 config.yaml 填密钥）见 §5。

## 4. 启动方式 B · 打包部署到服务器（用服务器 IP 访问）

无需 git、无需在服务器拉代码。本地一条命令打出**可部署完整包**（后端源码 + 前端静态 +
配置 + 三个 SKILL + 评测集 + 启动脚本），传到服务器三步跑起来：

```bash
# ---- 本地（Windows / Linux 通用，只需 Python）----
python scripts/package.py
# → dist/SkillEvolve-CR-<日期>.tar.gz（约 0.6 MB，runs 历史不打进去，密钥字段自动清空）

# ---- 传到服务器 ----
scp dist/SkillEvolve-CR-*.tar.gz user@SERVER_IP:~/
ssh user@SERVER_IP
mkdir -p skillevolve-cr && tar -xzf SkillEvolve-CR-*.tar.gz -C skillevolve-cr && cd skillevolve-cr

# ---- 服务器上（前置：Python 3.11+；Java 案例需 JDK；claude CLI 由 config.yaml 密钥渠道驱动）----
vi config.yaml            # 填 claude.api_key / base_url（包内密钥已清空，需重填）
bash start_server.sh      # 自动建 venv → 装依赖 → serve --host 0.0.0.0 --port 8765
```

然后本机浏览器访问 **http://SERVER_IP:8765** 即可。注意三点：

- **网络放行**：云服务器需在安全组/防火墙放行 8765 端口（`ufw allow 8765` 或 firewalld/安全组控制台）。
- **安全警告**：Dashboard 无内置鉴权，能访问端口的人都能触发消耗额度的运行。公网/办公网暴露时
  务必改用 SSH 隧道或 nginx + Basic Auth（现成配置见 [docs/DEPLOY.md §6](docs/DEPLOY.md)）。
- **常驻运行**：`start_server.sh` 是前台进程，退出 SSH 即停。生产环境用 systemd 常驻
  （单元文件与 PATH 陷阱见 [docs/DEPLOY.md §5](docs/DEPLOY.md)）。

升级/更新：本地改完后重新 `python scripts/package.py` → 上传覆盖解压即可；
`runs/` 不在包内，覆盖解压不会动服务器上的历史运行数据。

## 5. 配置速查

流程控制在 `config.toml`，三阶段提示词与 CLI 能力在 `config.yaml`（阶段级留空继承全局 `claude` 默认）。
完整字段说明与典型场景见 [docs/OPERATIONS.md §2](docs/OPERATIONS.md)。

| 配置 | 说明 |
|---|---|
| `[loop].rounds / concurrency / cases` | 轮次 / 每阶段并行数 / 案例子集 |
| `dataset.path / dataset.presets` | 评测集 JSONL 路径；presets 登记多套后按预设名整体切换 |
| `claude.model` | 全局默认模型，留空用 claude CLI 当前默认 |
| `claude.api_key / auth_token / base_url` | 留空沿用本机登录凭据；填写则注入对应 ANTHROPIC_* 环境变量 |
| `claude.max_turns / timeout_seconds / retries` | headless 会话预算 |
| `claude.allowed_tools / permission_mode` | 工具白名单与权限模式 |
| `stages.review / stages.score / stages.optimize` | 三阶段独立覆盖：`prompt` 提示词 + 上述全部 CLI 能力 |

CLI 覆盖参数：`--rounds --cases --concurrency --dataset --model --start-skill --run-id --no-optimize`；
Dashboard 服务：`--port --host`。

## 6. 目录结构

```
SkillEvolve-CR/
├── config.toml                  # 流程控制：轮次/并发/路径/报告
├── config.yaml                  # 三阶段提示词 + CLI 能力（模型/密钥/端点/预算）+ 评测集路径
├── requirements.txt             # Python 依赖（pyyaml）
├── main.py                       # ★ 后端服务入口（python main.py 一键启动 Dashboard）
├── main.py                       # ★ 后端服务入口（python main.py 一键启动 Dashboard）
├── scripts/
│   ├── build.sh                 #   Linux/macOS 构建预检脚本（venv + 依赖 + 校验）
│   └── package.py               # ★ 一键打包部署包（Windows/Linux 通用）
├── dist/                        # 打包产物（SkillEvolve-CR-<日期>.tar.gz）
├── docs/
│   ├── DEPLOY.md                # 服务器部署指南（systemd/nginx/迁移/排查）
│   └── OPERATIONS.md            # 操作要点手册（配置/评测集/运行/排查/FAQ）
├── dataset/                     # 评测集 JSONL 与构建脚本
├── skills/                      # 检视（被优化对象）/ 评分 / 优化 三个 SKILL
├── skillevolve/                 # Python 编排引擎（含 server.py：API + 静态托管）
├── web/                         # Dashboard 前端（原生 HTML/JS/CSS，vendor 内置，免构建）
└── runs/<run_id>/               # 每次运行的完整产物
```

## 7. 运行产物（隔离性）

```
runs/<run_id>/
├── dataset_snapshot.jsonl        # 数据集快照：后续改动评测集不影响本 run 复盘
├── config_snapshot.json          # 本 run 生效配置（api_key 已掩码 ***）
├── skills/skill_v1..vN.md        # 各轮使用的检视 SKILL 版本
├── round_01/
│   ├── review_ws/                # ① 检视子工作区（findings + stream trace + session 费用）
│   ├── score_ws/                 # ② 评分子工作区（逐条 TP/FP 判定 + unmatched_gt）
│   ├── optimize_ws/              # ③ 优化子工作区（错例包/链路诊断/新版技能/changelog）
│   └── metrics.json              # 本轮 P/R/F1/TP/FP/FN + per_case
└── report.json                   # 全轮次比对报告（Dashboard 数据源）
```

隔离保证：每案例每阶段独立 `claude -p` 进程；每轮每阶段独立子工作区；评测集与配置 run 开始时快照；
优化产物**从不自动回写** `skills/code-review/SKILL.md`，采纳由人工完成。

## 8. 指标口径

| 指标 | 口径 |
|---|---|
| TP | 检视意见与某条标准答案语义匹配（同一根因/同一位置，措辞和严重级允许一档偏差） |
| FP | 没有任何标准答案能匹配的检视意见；干净案例（GT=0）上的任何意见均为 FP |
| FN | 未被任何检视意见命中的标准答案 |
| Precision / Recall / F1 | TP/(TP+FP) · TP/(TP+FN) · 2PR/(P+R) |

评分判定由 score-review SKILL（headless）完成，Python 只做确定性汇总，不掺入模型判断。

## 9. 评测集扩展

**替换为个人定制评测集**：在 `config.yaml` 的 `dataset.path` 填入你的 JSONL 路径（支持绝对路径），
或在 `dataset.presets` 登记多个评测集后把 `path` 写成预设名整体切换；临时冒烟也可用
`python -m skillevolve run --dataset <路径>` 免改配置。validate/run/Dashboard 均从该路径读取。

**构造评测集**：编辑 `dataset/build_eval_set.py`，按 `pre`/`post` 源码对 + `ground_truth` 追加案例，
执行 `python dataset/build_eval_set.py` 重新生成 `eval_set.jsonl`。字段规范见 [docs/OPERATIONS.md §3](docs/OPERATIONS.md)。

## 10. 已知边界

- LLM 判分与生成均含随机性，指标波动 ±0.1 属正常；同一 run 不保证逐字可复现。
- 优化 SKILL 产出先做结构校验（frontmatter、name 不变、非空、与上一版不同、保留 JSON 契约字段），
  校验失败保留旧版并在报告中标记 `optimizer.valid=false`。
- 优化上下文只含错例与链路诊断，不含标准答案全文清单，避免"对着答案改"。
- 编译验证环节当前面向 Java（javac）；其他语言案例会跳过编译验证或被剔除。
- Dashboard 无内置鉴权：对外暴露务必配合 SSH 隧道或 nginx + 访问控制（docs/DEPLOY.md §6）。
