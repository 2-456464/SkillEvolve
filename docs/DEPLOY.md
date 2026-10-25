# SkillEvolve-CR 服务器部署指南（Linux）

> 结论：**可以直接部署到 Linux 服务器**。后端为纯 Python（3.11+，仅依赖 pyyaml），
> 前端为免构建静态页（由内置服务直接托管），对平台敏感的只有两样外部工具：
> **Claude Code CLI（需要 Node.js）** 与 **JDK javac（编译验证环节，可选但建议）**。
> Windows 专属的 npm shim 问题在 Linux 上不存在。

---

## 1. 环境要求

| 项 | 要求 | 说明 |
|---|---|---|
| OS | Ubuntu 22.04+ / Debian 12+ / RHEL 系 / 其他主流发行版 | root 或 sudo 权限 |
| Python | **3.11+**（必须） | `config.toml` 解析依赖内置 `tomllib` |
| Node.js | **18+**（必须） | Claude Code CLI 的运行时 |
| Claude Code CLI | 最新版（必须） | `npm install -g @anthropic-ai/claude-code` |
| JDK | 17+（建议） | 每轮评测前对案例做 `javac` 编译验证；缺失时该环节自动跳过 |
| 磁盘 | ≥ 2 GB | `runs/` 每个批次含 stream-json 全量 trace（单批次约几十 MB） |
| 网络 | 可访问模型 API 端点 | 直连 Anthropic 或经网关（见 §3.3）；如需代理提前配好 `HTTPS_PROXY` |

---

## 2. 一键构建（前后端统一入口）

上传代码后执行唯一构建脚本，它会完成：venv 创建、依赖安装、后端编译检查、
前端资产完整性校验、javac/claude CLI 探测、评测集与技能真实校验。

```bash
cd /opt/skillevolve-cr
bash scripts/build.sh                # 默认创建 .venv 并使用
# 或
bash scripts/build.sh --system       # 使用系统 Python（已自备环境时）
```

任何一步失败脚本会立即中止并给出修复提示，全部通过末尾会打印下一步命令。
国内服务器可换 pip 源：`PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/build.sh`。

### 2.1 上传代码

```bash
# 方式 A：git（推荐）
git clone <你的仓库地址> /opt/skillevolve-cr

# 方式 B：从 Windows 开发机 rsync / scp（排除本地运行产物）
rsync -avz --exclude runs/ --exclude .venv/ --exclude __pycache__/ \
      /e/IdeaProjects/SkillEvolve-CR/ user@server:/opt/skillevolve-cr/
```

---

## 3. Claude CLI 安装与认证（服务器关键步骤）

### 3.1 安装

```bash
# Ubuntu/Debian（如无 Node 18+ 先装 Node）
curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
sudo apt install -y nodejs
sudo npm install -g @anthropic-ai/claude-code
claude --version    # 能打印版本即安装成功
```

### 3.2 认证方式 A：config.yaml 注入密钥（服务器推荐，免交互登录）

编辑 `config.yaml`，填写全局或按阶段覆盖：

```yaml
claude:
  model: ""                                  # 留空用 CLI 默认；可填模型名
  api_key: "sk-xxxx"                         # 注入 ANTHROPIC_API_KEY
  base_url: "https://api.deepseek.com/anthropic"   # 经网关时填写
```

- 三阶段（`stages.review/score/optimize`）还可各自覆盖 `api_key/model/base_url`，
  实现"检视用 A 模型、评分用 B 模型"的混部；
- `api_key / auth_token` 已在 run 产物 `config_snapshot.json` 中自动掩码为 `***`；
- **填写真实密钥后勿将 config.yaml 提交公共仓库。**

### 3.3 认证方式 B：交互登录（有桌面/终端交互条件时）

```bash
claude    # 按提示完成 OAuth 登录，凭据落在 ~/.claude/
```

适合能临时 SSH 进去完成一次性登录的场景；后续 headless 运行复用该凭据。

---

## 4. 首次验证

```bash
source .venv/bin/activate
python -m skillevolve validate                        # 评测集 + 技能校验
python -m skillevolve run --no-optimize               # 单轮冒烟（费用最小）
python main.py --no-open                            # 前台启动 Dashboard 验证页面
```

`--no-open` 在无头服务器上不弹浏览器。确认能访问后 Ctrl+C 停止，转正式服务。

---

## 5. 常驻服务（systemd）

### 5.1 服务单元

```bash
sudo tee /etc/systemd/system/skillevolve.service > /dev/null <<'EOF'
[Unit]
Description=SkillEvolve-CR Dashboard
After=network.target

[Service]
Type=simple
User=deploy
WorkingDirectory=/opt/skillevolve-cr
# 关键：systemd 默认 PATH 不含 npm 全局目录，claude 可能找不到
Environment="PATH=/usr/local/bin:/usr/bin:/bin:/home/deploy/.npm-global/bin"
Environment="PYTHONIOENCODING=utf-8"
Environment="LANG=C.UTF-8"
ExecStart=/opt/skillevolve-cr/.venv/bin/python main.py --port 8766
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now skillevolve
sudo journalctl -u skillevolve -f          # 查看日志
```

> **PATH 陷阱（最常见故障）**：systemd 环境下 `shutil.which("claude")` 依赖单元里的 PATH。
> 先在登录 shell 执行 `which claude`，把结果目录加进上面的 `Environment="PATH=..."`，
> 或直接 `sudo ln -s $(which claude) /usr/local/bin/claude`。

### 5.2 绑定地址

- 默认 `127.0.0.1`：仅本机可访问，配合 §6 的隧道/反代使用（**推荐**）；
- 需要局域网直接访问时改 `ExecStart` 为 `--host 0.0.0.0`，服务启动时会打印无鉴权警告。

### 5.3 备选：tmux / nohup（无 sudo 权限时）

```bash
tmux new -s skdev 'source .venv/bin/activate && python main.py --port 8766'
# Ctrl+B D 脱离；tmux attach -t skdev 回到
```

---

## 6. 对外访问与安全（必读）

**Dashboard 无内置鉴权**：任何能访问该端口的人都可以触发消耗 API 额度的评测运行、
回写 SKILL 主文件。因此：

| 方案 | 做法 | 适用 |
|---|---|---|
| **SSH 隧道（推荐）** | `ssh -L 8766:127.0.0.1:8766 user@server`，本地开 `http://127.0.0.1:8766` | 个人/小团队，零额外配置 |
| **nginx 反代 + Basic Auth** | 见下方配置 | 需多人访问的内网 |
| 防火墙直放 8766 | 不推荐 | 仅在完全隔离的内网且明确接受风险时 |

nginx 反代参考（含 Basic Auth）：

```nginx
server {
    listen 8767;
    server_name _;
    auth_basic "SkillEvolve-CR";
    auth_basic_user_file /etc/nginx/.htpasswd;   # htpasswd -c /etc/nginx/.htpasswd deploy

    location / {
        proxy_pass http://127.0.0.1:8766;
        proxy_read_timeout 3600s;   # 评测运行触发的长连接/轮询
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

```bash
sudo apt install -y nginx apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd deploy
sudo nginx -t && sudo systemctl reload nginx
```

---

## 7. 服务器侧配置要点（与开发机差异）

| 项 | 建议 |
|---|---|
| `claude.api_key / base_url` | 服务器推荐显式配置（免交互登录），不要依赖登录态 |
| `dataset.path` | 评测集放服务器本地路径（支持绝对路径）；`presets` 登记多套 |
| `[loop].concurrency` | 按 CPU/API 限流调整（3~5 常见）；过大会触发网关限流 |
| `claude.timeout_seconds` | 服务器网络抖动可适当调大（默认 900s） |
| 代理出口 | 需要代理访问 API 时，在 systemd 单元加 `Environment="HTTPS_PROXY=..."` |

---

## 8. 升级与数据迁移

- **`runs/` 与代码完全解耦**：升级只需替换代码目录，历史运行记录/报告原样保留；
- 代码更新后重跑 `bash scripts/build.sh`（依赖变更时自动补装）；
- `git pull` 流程：`systemctl stop skillevolve → git pull → bash scripts/build.sh → systemctl start skillevolve`；
- 跨机迁移历史数据：整个 `runs/` 目录拷贝即可，Dashboard 自动识别全部批次。

---

## 9. 故障排查

| 症状 | 原因 | 处置 |
|---|---|---|
| `systemd` 下报"未找到 claude CLI" | 单元 PATH 不含 npm 全局目录 | 见 §5.1 的 PATH 陷阱 |
| 端口占用 `Address already in use` | 端口被占 | `ss -ltnp \| grep 8766`；换 `--port` |
| 评测全部失败、stderr 有 401/403 | 密钥无效或无额度 | 核对 config.yaml `api_key`，网关核对 `base_url` |
| javac 不可用警告 | 未装 JDK | `sudo apt install -y default-jdk`（不装则编译验证自动跳过） |
| 中文乱码 | locale 非 UTF-8 | `sudo apt install -y locales && sudo locale-gen zh_CN.UTF-8`；unit 已设 `LANG=C.UTF-8` |
| 页面打不开（云服务器） | 安全组未放行 | 走 SSH 隧道（§6），或在云控制台安全组放行 nginx 端口 |
