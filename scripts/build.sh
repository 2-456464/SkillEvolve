#!/usr/bin/env bash
# ============================================================
# SkillEvolve-CR 前后端统一构建 / 预检脚本
# 适用：Linux 服务器 / macOS / Windows（Git Bash 或 WSL）
#
# 用法：
#   bash scripts/build.sh                 # 默认：创建并使用 .venv
#   bash scripts/build.sh --system        # 跳过 venv，直接用系统 Python
#   PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/build.sh
#
# 做什么：
#   1) Python >= 3.11 检查（tomllib 为内置依赖）
#   2) 虚拟环境与依赖安装（后端）
#   3) 后端全模块语法编译
#   4) 前端静态资产完整性校验（web/ 为免构建静态站，由内置服务直接托管）
#   5) config.toml / config.yaml 存在性检查
#   6) javac / claude CLI 可用性检查（缺失降级为警告）
#   7) 评测集 + 技能文件真实校验（按 config.yaml dataset.path 读取）
# ============================================================
set -euo pipefail
cd "$(dirname "$0")/.."

GREEN='\033[92m'; RED='\033[91m'; YEL='\033[93m'; GRAY='\033[90m'; BOLD='\033[1m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[ OK ]${NC} $1"; }
warn() { echo -e "${YEL}[WARN]${NC} $1"; }
fail() { echo -e "${RED}[FAIL]${NC} $1"; }
info() { echo -e "${GRAY}       $1${NC}"; }
die()  { fail "$1"; echo -e "${RED}构建中止${NC}"; exit 1; }

echo -e "${BOLD}== SkillEvolve-CR 统一构建（后端 Python + 前端静态资产）==${NC}"
echo

# ---- 1) Python >= 3.11 ----
PY=python3
command -v "$PY" >/dev/null 2>&1 || PY=python
command -v "$PY" >/dev/null 2>&1 || die "未找到 python3，请先安装 Python 3.11+（Ubuntu: sudo apt install -y python3 python3-venv python3-pip）"
PY_VER=$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')
"$PY" -c 'import sys; assert sys.version_info >= (3, 11)' 2>/dev/null \
  || die "Python $PY_VER 过低：本项目需要 3.11+（config.toml 解析依赖内置 tomllib）"
ok "Python $PY_VER"

# ---- 2) 虚拟环境 ----
USE_SYSTEM=0
for a in "$@"; do [ "$a" = "--system" ] && USE_SYSTEM=1; done
if [ "$USE_SYSTEM" = "1" ]; then
  ok "--system：跳过 venv，使用系统 Python"
else
  VENV_PY=""
  # Linux/macOS 布局 bin/，Windows 布局 Scripts/（Git Bash 下同样适用）
  [ -x .venv/bin/python ] && VENV_PY=".venv/bin/python"
  if [ -z "$VENV_PY" ] && [ -f .venv/Scripts/python.exe ]; then VENV_PY=".venv/Scripts/python.exe"; fi
  if [ -z "$VENV_PY" ]; then
    info "创建虚拟环境 .venv ..."
    "$PY" -m venv .venv || die "venv 创建失败（Debian/Ubuntu 需先安装 python3-venv）"
    [ -x .venv/bin/python ] && VENV_PY=".venv/bin/python"
    [ -z "$VENV_PY" ] && VENV_PY=".venv/Scripts/python.exe"
  fi
  PY="$VENV_PY"
  ok "虚拟环境 .venv（python: $PY）"
fi

# ---- 3) 依赖安装 ----
info "安装依赖 requirements.txt ..."
# shellcheck disable=SC2086
"$PY" -m pip install -q -r requirements.txt ${PIP_INDEX_URL:+-i "$PIP_INDEX_URL"} \
  || die "依赖安装失败；可换源重试: PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple bash scripts/build.sh"
ok "Python 依赖（pyyaml $("$PY" -c 'import yaml; print(yaml.__version__)')）"

# ---- 4) 后端编译检查 ----
"$PY" -m compileall -q skillevolve || die "Python 模块编译失败"
ok "后端模块编译（skillevolve/*.py）"

# ---- 5) 前端静态资产（免构建，校验完整性）----
for f in web/index.html web/app.js web/style.css; do
  [ -f "$f" ] || die "缺少前端资产: $f"
done
ls web/vendor/*.js >/dev/null 2>&1 || die "缺少前端 vendor 依赖库（web/vendor/）"
ok "前端静态资产完整（web/ 为免构建静态站，由内置服务直接托管）"

# ---- 6) 配置文件 ----
[ -f config.toml ] || die "缺少 config.toml（流程控制：loop/paths/report）"
[ -f config.yaml ] || die "缺少 config.yaml（三阶段提示词 / CLI 能力 / 评测集路径）"
ok "配置文件 config.toml + config.yaml"

# ---- 7) javac（评测案例编译门槛，可缺）----
if command -v javac >/dev/null 2>&1; then
  ok "JDK javac：$(javac -version 2>&1)"
else
  warn "未找到 javac：评测前编译验证将跳过（建议安装 JDK：sudo apt install -y default-jdk）"
fi

# ---- 8) claude CLI（运行必需，可后置安装）----
if command -v claude >/dev/null 2>&1; then
  ok "claude CLI：$(claude --version 2>&1 | head -n 1)"
else
  warn "未找到 claude CLI（评测运行必需）"
  info "  安装:  npm install -g @anthropic-ai/claude-code   （需 Node.js 18+）"
  info "  服务器免交互认证: 在 config.yaml 配置 claude.api_key / base_url，见 docs/DEPLOY.md"
fi

# ---- 9) 评测集 + 技能真实校验 ----
"$PY" -m skillevolve validate || die "评测集/技能校验未通过"

echo
echo -e "${BOLD}== 构建完成 ==${NC}"
info "下一步:"
info "  python -m skillevolve run --no-optimize          # 单轮冒烟（不优化、费用最小）"
info "  python -m skillevolve serve --open               # 本机 Dashboard"
info "  python -m skillevolve serve --host 0.0.0.0       # 服务器对外（注意鉴权，见 docs/DEPLOY.md）"
info "  服务器部署: docs/DEPLOY.md · 操作手册: docs/OPERATIONS.md"
