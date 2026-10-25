"""零依赖 Dashboard 服务：静态页面 + runs 数据 + 运行触发/监控 API。

API：
  GET  /api/runs                 列出全部运行批次
  GET  /api/dataset              全量评测集（含 diff / 源码 / 标准答案）
  GET  /api/run-status           当前（或最近一次）触发运行的实时状态
  GET  /api/run-log?run_id=      指定运行的完整控制台日志（尾部 500 行）
  POST /api/run                  触发运行 {mode:"single"|"multi", rounds?, cases?[], start_skill?:"main"|"baseline"|"weak"}
  POST /api/stop                 终止当前运行
  GET  /api/skill-info           主 SKILL 与初始版本基线（baseline）状态
  POST /api/skill-restore        将主 SKILL 回退为初始版本基线内容
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
import webbrowser
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import console
from .config import load_config, resolve_path
from .trace_view import parse_stream


def make_handler(web_dir: Path, runs_dir: Path):
    class Handler(SimpleHTTPRequestHandler):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, directory=str(web_dir), **kwargs)

        def translate_path(self, path: str) -> str:
            if path.startswith("/runs/"):
                rel = path[len("/runs/"):].split("?", 1)[0].split("#", 1)[0]
                return str((runs_dir / rel).resolve())
            return super().translate_path(path)

        def list_directory(self, path):
            return None  # 禁止目录浏览

        def end_headers(self):
            # 前端文件频繁迭代：禁用缓存，避免浏览器拿到旧版 HTML/JS/CSS
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            super().end_headers()

        def log_message(self, fmt, *args):  # 静默访问日志
            pass

    return Handler


# ---------------------------------------------------------------- 运行管理

class RunManager:
    """同一时间只允许一个后台运行（子进程执行 python -m skillevolve run）。"""

    def __init__(self, runs_dir: Path):
        self.runs_dir = runs_dir
        self.lock = threading.Lock()
        self.proc: subprocess.Popen | None = None
        self.run_id: str | None = None
        self.started_at: float = 0.0

    # ---- 触发
    def launch(self, payload: dict) -> tuple[int, dict]:
        with self.lock:
            if self.proc and self.proc.poll() is None:
                return 409, {"error": f"已有运行在进行中: {self.run_id}（可先停止或等待完成）"}
            mode = payload.get("mode", "multi")
            if mode not in ("single", "multi"):
                return 400, {"error": "mode 必须是 single 或 multi"}
            rounds = payload.get("rounds") or 3
            try:
                rounds = max(1, min(int(rounds), 10))
            except (TypeError, ValueError):
                return 400, {"error": "rounds 必须是数字"}
            cases = payload.get("cases") or []
            if not isinstance(cases, list):
                return 400, {"error": "cases 必须是数组"}
            start_skill = str(payload.get("start_skill") or "main").lower()
            if start_skill not in ("main", "baseline", "weak"):
                return 400, {"error": "start_skill 必须是 main、baseline 或 weak"}
            run_id = payload.get("run_id") or f"web-{datetime.now().strftime('%Y%m%d-%H%M%S')}"
            run_id = re.sub(r"[^\w\-]", "", str(run_id))[:60]
            if not run_id:
                return 400, {"error": "run_id 非法"}

            cmd = [sys.executable, "-m", "skillevolve", "run", "--run-id", run_id]
            if mode == "single":
                cmd.append("--no-optimize")
            else:
                cmd += ["--rounds", str(rounds)]
            if cases:
                cmd += ["--cases", ",".join(str(c) for c in cases)]
            if start_skill in ("baseline", "weak"):
                cmd += ["--start-skill", start_skill]

            log_path = self.runs_dir / f"{run_id}.launch.log"
            env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
            flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
            try:
                log_f = open(log_path, "w", encoding="utf-8")
                self.proc = subprocess.Popen(
                    cmd, cwd=str(Path(__file__).resolve().parent.parent),
                    stdout=log_f, stderr=subprocess.STDOUT,
                    env=env, creationflags=flags,
                )
            except OSError as exc:
                return 500, {"error": f"启动失败: {exc}"}
            self.run_id = run_id
            self.started_at = time.time()
            return 200, {"run_id": run_id, "mode": mode, "rounds": 1 if mode == "single" else rounds,
                         "cases": len(cases) or "全部", "start_skill": start_skill}

    # ---- 停止
    def stop(self) -> tuple[int, dict]:
        with self.lock:
            if not (self.proc and self.proc.poll() is None):
                return 400, {"error": "当前没有正在进行的运行"}
            proc, run_id = self.proc, self.run_id
        try:
            if sys.platform == "win32":
                subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)],
                               capture_output=True, timeout=10)
            else:
                proc.terminate()
        except OSError as exc:
            return 500, {"error": f"停止失败: {exc}"}
        return 200, {"stopped": run_id}

    # ---- 状态
    def status(self) -> dict:
        with self.lock:
            proc, run_id = self.proc, self.run_id
        if not run_id:
            # 服务重启会丢失子进程句柄：从最近一次运行报告推断完成态
            return self._infer_last_run() or {"state": "idle"}
        running = proc is not None and proc.poll() is None
        return {
            "run_id": run_id,
            "state": "running" if running else
                     ("error" if (proc is not None and proc.returncode not in (0,)) else "finished"),
            "exit_code": None if running else (proc.returncode if proc else None),
            "started_at": self.started_at or None,
            **_progress_info(self.runs_dir, run_id),
        }

    def _infer_last_run(self) -> dict | None:
        """扫描 runs_dir 下最新的 launch.log；若对应报告已 final 则返回 finished 状态。"""
        try:
            logs = sorted(self.runs_dir.glob("*.launch.log"),
                          key=lambda p: p.stat().st_mtime, reverse=True)
        except OSError:
            return None
        for log in logs[:3]:
            run_id = log.name[:-len(".launch.log")]
            info = _progress_info(self.runs_dir, run_id)
            if info["final"]:
                return {"run_id": run_id, "state": "finished", "exit_code": 0,
                        "started_at": None, **info}
        return None


def _case_progress(lines: list[str]) -> tuple[dict, list, str | None]:
    """解析当前轮的逐案例进度：{case_id: {review: ok|fail, score: ok|fail, findings: n}}。

    遇到新的 Round 标记即重置（每轮起点相同、环境隔离）。
    """
    cases: dict = {}
    order: list = []
    current_case = None
    for line in lines:
        if "Round " in line and "使用技能" in line:
            cases, order, current_case = {}, [], None
            continue
        m = re.search(r"\[compile\] (case-[\w\-]+): (OK|FAIL)", line)
        if m:
            cid = m.group(1)
            if cid not in cases:
                cases[cid] = {}
                order.append(cid)
            cases[cid]["compile"] = "ok" if m.group(2) == "OK" else "fail"
            continue
        m = re.search(r"\[review\] \[\d+/\d+\] (case-[\w\-]+): (OK|FAIL)", line)
        if m:
            cid = m.group(1)
            if cid not in cases:
                cases[cid] = {}
                order.append(cid)
            cases[cid]["review"] = "ok" if m.group(2) == "OK" else "fail"
            fm = re.search(r"findings=(\d+)", line)
            if fm:
                cases[cid]["findings"] = int(fm.group(1))
            current_case = cid
            continue
        m = re.search(r"\[score ?\] (case-[\w\-]+): (OK|FAIL)", line)
        if m:
            cid = m.group(1)
            if cid not in cases:
                cases[cid] = {}
                order.append(cid)
            cases[cid]["score"] = "ok" if m.group(2) == "OK" else "fail"
            current_case = cid
    return cases, order, current_case


def _progress_info(runs_dir: Path, run_id: str) -> dict:
    """从 progressive report + 日志尾部解析当前进度。"""
    info: dict = {"rounds_done": 0, "rounds_total": None, "stage": None, "final": False,
                  "last_metrics": None, "log_tail": [],
                  "cases": {}, "case_order": [], "current_case": None}
    report_path = runs_dir / run_id / "report.json"
    if report_path.exists():
        try:
            rep = json.loads(report_path.read_text(encoding="utf-8"))
            info["rounds_done"] = len(rep.get("rounds", []))
            info["rounds_total"] = (rep.get("config") or {}).get("rounds")
            info["final"] = bool(rep.get("final"))
            if rep.get("rounds"):
                last = rep["rounds"][-1]
                info["last_metrics"] = {"round": last["round"], "skill_version": last["skill_version"],
                                        **{k: last["metrics"][k] for k in ("precision", "recall", "f1", "tp", "fp", "fn")}}
        except (json.JSONDecodeError, KeyError, OSError):
            pass
    log_path = runs_dir / f"{run_id}.launch.log"
    if log_path.exists():
        try:
            lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            lines = []
        info["log_tail"] = lines[-60:]
        cases, case_order, current_case = _case_progress(lines)
        info["cases"], info["case_order"], info["current_case"] = cases, case_order, current_case
        round_no = rounds_total = None
        for line in reversed(lines):
            if info["stage"] is None and "跳过" not in line:
                if "① 检视生成" in line:
                    info["stage"] = "review"
                elif "② 评分" in line:
                    info["stage"] = "scoring"
                elif "③ SKILL 优化" in line:
                    info["stage"] = "optimize"
            m = re.search(r"Round (\d+)/(\d+)", line)
            if m:
                round_no, rounds_total = int(m.group(1)), int(m.group(2))
                break
        if round_no:
            info["round_current"] = round_no
            if rounds_total:
                info["rounds_total"] = rounds_total
    return info


def _tail_lines(path: Path, n: int) -> list[str]:
    if not path.exists():
        return []
    try:
        return path.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
    except OSError:
        return []


def _skill_info(skills_dir: Path) -> dict:
    """主 SKILL 与初始版本基线的状态对比。"""
    d = skills_dir / "code-review"
    out = {"main_exists": False, "baseline_exists": False, "weak_exists": False, "identical": False,
           "main_lines": 0, "baseline_lines": 0, "weak_lines": 0}
    main_t = base_t = None
    try:
        main_t = (d / "SKILL.md").read_text(encoding="utf-8")
        out["main_exists"], out["main_lines"] = True, len(main_t.splitlines())
    except OSError:
        pass
    try:
        base_t = (d / "SKILL.baseline.md").read_text(encoding="utf-8")
        out["baseline_exists"], out["baseline_lines"] = True, len(base_t.splitlines())
    except OSError:
        pass
    try:
        out["weak_exists"], out["weak_lines"] = True, len((d / "SKILL.weak.md").read_text(encoding="utf-8").splitlines())
    except OSError:
        pass
    if main_t is not None and base_t is not None:
        out["identical"] = main_t == base_t
    return out


def _skill_restore(skills_dir: Path) -> tuple[int, dict]:
    """用初始版本基线覆盖主 SKILL 文件。"""
    src = skills_dir / "code-review" / "SKILL.baseline.md"
    dst = skills_dir / "code-review" / "SKILL.md"
    if not src.exists():
        return 400, {"error": f"基线文件不存在: {src}"}
    try:
        text = src.read_text(encoding="utf-8")
        dst.write_text(text, encoding="utf-8", newline="\n")
    except OSError as exc:
        return 500, {"error": f"回退失败: {exc}"}
    return 200, {"restored": True, "lines": len(text.splitlines())}


def list_runs(runs_dir: Path) -> list[dict]:
    runs = []
    if runs_dir.exists():
        for report in sorted(runs_dir.glob("*/report.json"), reverse=True):
            try:
                data = json.loads(report.read_text(encoding="utf-8"))
                last = data["rounds"][-1]["metrics"] if data.get("rounds") else {}
                runs.append({
                    "run_id": data.get("run_id", report.parent.name),
                    "created_at": data.get("created_at", ""),
                    "final": data.get("final", False),
                    "rounds": len(data.get("rounds", [])),
                    "elapsed_sec": data.get("elapsed_sec"),
                    "last_metrics": {k: last.get(k) for k in ("precision", "recall", "f1", "fp", "fn")},
                })
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
    return runs


def load_dataset(dataset_path: Path) -> list[dict]:
    cases = []
    if dataset_path.exists():
        with dataset_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    cases.append(json.loads(line))
    return cases


# ---------------------------------------------------------------- 服务入口

def serve(port: int = 8765, open_browser: bool = False, config_path: str | Path | None = None,
          host: str = "127.0.0.1") -> int:
    cfg = load_config(config_path)
    web_dir = resolve_path(cfg, "web_dir")
    if not web_dir.exists():
        web_dir = resolve_path(cfg, "runs_dir").parent / "web"
    runs_dir = resolve_path(cfg, "runs_dir")
    skills_dir = resolve_path(cfg, "skills_dir")
    dataset_path = resolve_path(cfg, "dataset")
    manager = RunManager(runs_dir)

    class ApiHandler(make_handler(web_dir, runs_dir)):
        def _json(self, obj: dict, code: int = 200):
            body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?")[0]
            qs = dict(p.split("=", 1) for p in self.path.split("?", 1)[1].split("&") if "=" in p) if "?" in self.path else {}
            if path == "/api/runs":
                self._json({"runs": list_runs(runs_dir)})
            elif path == "/api/dataset":
                self._json({"cases": load_dataset(dataset_path)})
            elif path == "/api/run-status":
                self._json(manager.status())
            elif path == "/api/run-log":
                rid = qs.get("run_id", "")
                rid = re.sub(r"[^\w\-]", "", rid)
                self._json({"run_id": rid, "lines": _tail_lines(runs_dir / f"{rid}.launch.log", 500)})
            elif path == "/api/optimize-skill":
                # 优化器技能全文（"优化 SKILL 的 SKILL"）
                p = Path(__file__).resolve().parent.parent / "skills" / "optimize-review" / "SKILL.md"
                try:
                    self._json({"content": p.read_text(encoding="utf-8")})
                except OSError as exc:
                    self._json({"error": f"读取失败: {exc}"}, 404)
            elif path == "/api/skill-info":
                self._json(_skill_info(skills_dir))
            elif path == "/api/session-trace":
                rid = re.sub(r"[^\w\-]", "", qs.get("run_id", ""))
                stage = qs.get("stage", "")
                if stage not in ("review", "score", "optimize"):
                    self._json({"error": "stage 必须是 review / score / optimize"}, 400)
                    return
                try:
                    rnd = max(1, min(int(qs.get("round", "1")), 99))
                except ValueError:
                    self._json({"error": "round 必须是数字"}, 400)
                    return
                cid = re.sub(r"[^\w\-]", "", qs.get("case_id", ""))
                if stage == "optimize":
                    p = runs_dir / rid / f"round_{rnd:02d}" / "optimize_ws" / "traces" / "optimizer.stream.jsonl"
                else:
                    if not cid:
                        self._json({"error": "缺少 case_id"}, 400)
                        return
                    p = runs_dir / rid / f"round_{rnd:02d}" / f"{stage}_ws" / "traces" / f"{cid}.stream.jsonl"
                if not p.exists():
                    self._json({"error": f"轨迹文件不存在: {p.name}"}, 404)
                    return
                self._json(parse_stream(p))
            else:
                super().do_GET()

        def do_POST(self):
            path = self.path.split("?")[0]
            try:
                length = int(self.headers.get("Content-Length") or 0)
                payload = json.loads(self.rfile.read(length) or b"{}")
            except (ValueError, json.JSONDecodeError):
                self._json({"error": "请求体必须是 JSON"}, 400)
                return
            if path == "/api/run":
                code, resp = manager.launch(payload)
                self._json(resp, code)
            elif path == "/api/skill-restore":
                code, resp = _skill_restore(skills_dir)
                self._json(resp, code)
            elif path == "/api/stop":
                code, resp = manager.stop()
                self._json(resp, code)
            else:
                self._json({"error": "未知接口"}, 404)

    try:
        server = ThreadingHTTPServer((host, port), ApiHandler)
    except OSError as exc:
        print(console.red(f"Dashboard 启动失败: {exc}"))
        print(console.gray(f"提示: 端口 {port} 可能被占用，换个端口试试:  python -m skillevolve serve --port {port + 1}"))
        return 1

    url_host = "127.0.0.1" if host in ("0.0.0.0", "::") else host
    url = f"http://{url_host}:{port}/"
    print(console.bold("Dashboard 已启动"))
    print(f"   地址: {url}  (Ctrl+C 停止)")
    if host in ("0.0.0.0", "::"):
        print(console.yellow(
            "   ⚠ 已绑定全部网卡（0.0.0.0）：Dashboard 无内置鉴权，可访问该端口的人都能触发消耗额度的运行；"
            "建议仅限内网，并配合 nginx 反代 + 访问控制或 SSH 隧道（见 docs/DEPLOY.md）"
        ))
    runs = list_runs(runs_dir)
    if runs:
        print(console.gray(f"   共 {len(runs)} 个运行批次，最新: {runs[0]['run_id']}（结果分析页可切换）"))
    else:
        print(console.gray("   尚无运行数据 —— 打开页面「评测集」标签即可一键触发"))
    if open_browser:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0
