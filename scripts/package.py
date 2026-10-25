#!/usr/bin/env python3
"""SkillEvolve-CR 一键打包脚本（Windows / Linux 通用，仅需 Python 3.8+）。

把可部署到服务器的最小完整包打进 dist/SkillEvolve-CR-<日期>.tar.gz：
  - skillevolve/   后端源码（编排引擎 + Dashboard 服务）
  - web/           前端静态资产（免构建，Python 服务直接托管）
  - skills/        三个 SKILL（检视/评分/优化）
  - dataset/       评测集与构建脚本
  - config.toml / config.yaml（api_key/auth_token 自动清空，服务器上重填）
  - requirements.txt / README.md / main.py / docs/
  - start_server.sh 服务器一键启动脚本（venv + 依赖 + serve --host 0.0.0.0，内置可执行位）

排除：runs/ 运行历史、.venv、__pycache__、.idea、.git、dist/ 等本地产物。

用法：
  python scripts/package.py
"""
import datetime
import io
import re
import sys
import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist"
SKIP_DIRS = {"__pycache__", ".venv", "venv", ".idea", ".git", "runs", "dist",
             "node_modules", ".tmp_ds", ".claude"}
COPY_TREES = ["skillevolve", "web", "skills", "dataset", "docs"]
COPY_FILES = ["main.py", "config.toml", "config.yaml", "requirements.txt", "README.md"]

SECRET_RE = re.compile(r"^(\s*(?:api_key|auth_token)\s*:\s*).*$", re.M)

START_SERVER_SH = """#!/usr/bin/env bash
# SkillEvolve-CR 服务器一键启动：venv + 依赖 + Dashboard（0.0.0.0:8765）
# 前置：Python 3.11+；评测含 Java 案例需 JDK(javac)；模型渠道见 config.yaml
set -e
cd "$(dirname "$0")"
PY=python3
command -v python3 >/dev/null 2>&1 || PY=python
$PY --version
[ -d .venv ] || $PY -m venv .venv
source .venv/bin/activate
pip install -q -r requirements.txt
echo ""
echo "== 启动后访问: http://<服务器IP>:8765  （放行端口/安全组后浏览器打开）=="
exec $PY main.py --host 0.0.0.0 --port 8765 --no-open
"""


def collect_files():
    """返回 [(相对路径, 来源绝对路径或 None(由内容生成))]。"""
    out = []
    for tree in COPY_TREES:
        base = ROOT / tree
        if not base.exists():
            print(f"[WARN] 缺少目录，跳过：{tree}")
            continue
        for p in sorted(base.rglob("*")):
            if p.is_dir():
                continue
            if any(part in SKIP_DIRS for part in p.relative_to(ROOT).parts):
                continue
            out.append((p.relative_to(ROOT).as_posix(), p))
    for f in COPY_FILES:
        p = ROOT / f
        if p.exists():
            out.append((f, p))
        else:
            print(f"[WARN] 缺少文件，跳过：{f}")
    out.append(("start_server.sh", None))
    return out


def main():
    stamp = datetime.date.today().strftime("%Y%m%d")
    tgz_path = DIST / f"SkillEvolve-CR-{stamp}.tar.gz"
    DIST.mkdir(exist_ok=True)
    if tgz_path.exists():
        tgz_path.unlink()

    n_files, n_secret = 0, 0
    with tarfile.open(tgz_path, "w:gz") as tar:
        for arcname, src in collect_files():
            if src is None:  # 生成式文件：start_server.sh（内容写死，给 0755 可执行位）
                data, mode = START_SERVER_SH.encode("utf-8"), 0o755
            elif arcname == "config.yaml":
                text = src.read_text(encoding="utf-8")
                text, k = SECRET_RE.subn(r'\1""', text)
                n_secret += k
                data, mode = text.encode("utf-8"), 0o600
            else:
                data, mode = src.read_bytes(), 0o644
            info = tarfile.TarInfo(name=arcname)
            info.size = len(data)
            info.mode = mode
            info.mtime = int(src.stat().st_mtime) if src is not None else int(time.time())
            tar.addfile(info, io.BytesIO(data))
            n_files += 1

    size_mb = tgz_path.stat().st_size / 1024 / 1024
    print(f"打包完成: {tgz_path}")
    print(f"  文件数 {n_files} · 大小 {size_mb:.1f} MB · config.yaml 已清空 {n_secret} 处密钥字段")
    print("")
    print("部署三步（详细见 docs/DEPLOY.md）：")
    print(f"  1) 上传      scp {tgz_path.name} user@SERVER_IP:~/")
    print( "  2) 解压      mkdir -p skillevolve-cr && tar -xzf SkillEvolve-CR-*.tar.gz -C skillevolve-cr && cd skillevolve-cr")
    print( "  3) 启动      vi config.yaml   # 填 claude.api_key / base_url")
    print( "              bash start_server.sh")
    print("  然后浏览器访问 http://SERVER_IP:8765（放行端口/安全组；Dashboard 无鉴权，")
    print("  对外服务建议配 SSH 隧道或 nginx + Basic Auth，见 docs/DEPLOY.md §6）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
