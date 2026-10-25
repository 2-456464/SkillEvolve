# -*- coding: utf-8 -*-
"""
评测集生成脚本：SkillEvolve-CR

每条案例包含：
  - id / language / file_path / change_summary
  - pre  : 变更前源码
  - post : 变更后源码（检视目标）
  - diff : 由 difflib 统一生成，保证与源码一致
  - ground_truth : 标准答案（检视意见），severity ∈ critical/high/medium/low
                    category ∈ security/correctness/robustness/performance/maintainability

case-009 / case-010 为干净案例（GT=0），用于度量精确率（FP）。

用法：python dataset/build_eval_set.py
"""
from __future__ import annotations

import difflib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dataset" / "eval_set.jsonl"

CASES: list[dict] = [
    # ------------------------------------------------------------------ 001
    {
        "id": "case-001",
        "language": "python",
        "file_path": "services/payment/refund_service.py",
        "change_summary": "为退款查询接口增加按客户邮箱与最低金额过滤的能力",
        "pre": '''"""退款查询服务。"""
import sqlite3

DB_PATH = "billing.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def list_refunds(order_id: str, limit: int = 50):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, order_id, amount, status, created_at "
            "FROM refunds WHERE order_id = ? ORDER BY created_at DESC LIMIT ?",
            (order_id, limit),
        )
        return cursor.fetchall()
    finally:
        conn.close()
''',
        "post": '''"""退款查询服务。"""
import sqlite3

DB_PATH = "billing.db"


def get_connection():
    return sqlite3.connect(DB_PATH)


def list_refunds(order_id: str, limit: int = 50):
    conn = get_connection()
    try:
        cursor = conn.execute(
            "SELECT id, order_id, amount, status, created_at "
            "FROM refunds WHERE order_id = ? ORDER BY created_at DESC LIMIT ?",
            (order_id, limit),
        )
        return cursor.fetchall()
    finally:
        conn.close()


def list_refunds_by_email(email: str, amount_min: float = 0.0, limit: int = 50):
    conn = get_connection()
    try:
        query = (
            "SELECT id, order_id, amount, status, created_at "
            "FROM refunds WHERE customer_email = '" + email + "'"
            " AND amount >= " + str(amount_min) +
            " ORDER BY created_at DESC LIMIT " + str(limit)
        )
        cursor = conn.execute(query)
        return cursor.fetchall()
    finally:
        conn.close()
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "security",
                "severity": "high",
                "title": "SQL 注入：外部输入直接拼接进 SQL 语句",
                "detail": "list_refunds_by_email 将 customer_email、amount_min、limit 用字符串拼接进 SQL，攻击者可通过 email 字段注入任意 SQL（如 ' OR '1'='1）。必须改回参数化查询（占位符 + 参数元组）。",
                "line_hint": "customer_email",
            },
            {
                "id": "GT-2",
                "category": "robustness",
                "severity": "medium",
                "title": "输入参数缺少校验：email 可为空、limit 无上限",
                "detail": "email 未做空值/格式校验，limit 未限制上限，调用方可传入超大 limit 拖垮数据库。建议对参数做基本校验并限制 limit 最大值。",
                "line_hint": "limit: int = 50",
            },
        ],
    },
    # ------------------------------------------------------------------ 002
    {
        "id": "case-002",
        "language": "java",
        "file_path": "src/main/java/com/bank/auth/PasswordResetUtil.java",
        "change_summary": "新增忘记密码流程：管理员重置为默认密码，并升级用户密码存储",
        "pre": '''package com.bank.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HexFormat;

public final class PasswordResetUtil {

    private PasswordResetUtil() {
    }

    public static String hashPassword(String rawPassword, byte[] salt) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        digest.update(salt);
        return HexFormat.of().formatHex(digest.digest(rawPassword.getBytes(StandardCharsets.UTF_8)));
    }

    public static byte[] newSalt() {
        byte[] salt = new byte[16];
        new SecureRandom().nextBytes(salt);
        return salt;
    }
}
''',
        "post": '''package com.bank.auth;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.SecureRandom;
import java.util.HexFormat;

public final class PasswordResetUtil {

    private static final String DEFAULT_RESET_PASSWORD = "Reset@123456";

    private PasswordResetUtil() {
    }

    public static String hashPassword(String rawPassword, byte[] salt) throws Exception {
        MessageDigest digest = MessageDigest.getInstance("SHA-256");
        digest.update(salt);
        return HexFormat.of().formatHex(digest.digest(rawPassword.getBytes(StandardCharsets.UTF_8)));
    }

    public static byte[] newSalt() {
        byte[] salt = new byte[16];
        new SecureRandom().nextBytes(salt);
        return salt;
    }

    public static String resetToDefault(long userId) throws Exception {
        byte[] salt = newSalt();
        String stored = hashPassword(DEFAULT_RESET_PASSWORD, salt);
        UserDao.updateCredential(userId, stored, salt);
        return DEFAULT_RESET_PASSWORD;
    }

    public static String hashLegacy(String rawPassword) throws Exception {
        MessageDigest md5 = MessageDigest.getInstance("MD5");
        return HexFormat.of().formatHex(md5.digest(rawPassword.getBytes(StandardCharsets.UTF_8)));
    }
}
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "security",
                "severity": "high",
                "title": "硬编码默认密码，且重置后返回明文",
                "detail": "DEFAULT_RESET_PASSWORD 以常量硬编码在源码中并随包发布，任何能读到代码/反编译产物的人都可登录被重置的账户；resetToDefault 还把明文默认密码返回给调用方（可能进入日志或响应）。应改为生成一次性随机密码/重置链接，通过安全渠道下发给用户。",
                "line_hint": "DEFAULT_RESET_PASSWORD",
            },
            {
                "id": "GT-2",
                "category": "security",
                "severity": "high",
                "title": "使用 MD5 存储口令（弱哈希）",
                "detail": "hashLegacy 使用 MD5 做口令摘要。MD5 已被破解且无盐，不适合口令存储；如需兼容旧数据应标记 @Deprecated 并逐步迁移到 bcrypt/argon2/PBKDF2。",
                "line_hint": "MD5",
            },
        ],
    },
    # ------------------------------------------------------------------ 003
    {
        "id": "case-003",
        "language": "python",
        "file_path": "inventory/report_export.py",
        "change_summary": "报表导出新增分页函数与客单价统计",
        "pre": '''"""库存报表导出。"""
import csv
from pathlib import Path


def export_rows(rows: list[dict], target: Path) -> int:
    with target.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)
''',
        "post": '''"""库存报表导出。"""
import csv
from pathlib import Path


def export_rows(rows: list[dict], target: Path) -> int:
    with target.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
        writer.writeheader()
        writer.writerows(rows)
    return len(rows)


def paginate(rows: list[dict], page: int, page_size: int) -> list[dict]:
    """返回第 page 页数据，page 从 1 开始。"""
    start = (page - 1) * page_size
    end = page * page_size + 1
    return rows[start:end]


def average_order_value(orders: list[dict]) -> float:
    paid = [o for o in orders if o["status"] == "PAID"]
    total = sum(o["amount"] for o in paid)
    return round(total / len(paid), 2)
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "correctness",
                "severity": "medium",
                "title": "average_order_value 无已支付订单时除零",
                "detail": "当 orders 中不存在 status == 'PAID' 的记录时 len(paid) == 0，total / len(paid) 抛 ZeroDivisionError。应先判空返回 0.0 或抛出带业务语义的异常。",
                "line_hint": "total / len(paid)",
            },
            {
                "id": "GT-2",
                "category": "correctness",
                "severity": "medium",
                "title": "paginate 切片右端点 +1 导致相邻页重复一条记录",
                "detail": "end = page * page_size + 1 多取了一行：第 1 页返回 rows[0:page_size+1]，第 2 页从 rows[page_size] 开始，边界行会在两页中重复出现，导出数据重复。应为 end = page * page_size。",
                "line_hint": "page * page_size + 1",
            },
        ],
    },
    # ------------------------------------------------------------------ 004
    {
        "id": "case-004",
        "language": "javascript",
        "file_path": "web/src/components/CommentList.jsx",
        "change_summary": "评论区支持 Markdown 富文本渲染，并新增本地会话保持",
        "pre": '''import React from "react";
import { fetchComments } from "../api/comments";

export function saveSession(token) {
  sessionStorage.setItem("jwt", token);
}

export default function CommentList({ videoId }) {
  const [comments, setComments] = React.useState([]);

  React.useEffect(() => {
    fetchComments(videoId).then(setComments).catch(() => setComments([]));
  }, [videoId]);

  return (
    <ul className="comment-list">
      {comments.map((c) => (
        <li key={c.id}>
          <span className="author">{c.author}</span>
          <p className="body">{c.body}</p>
        </li>
      ))}
    </ul>
  );
}
''',
        "post": '''import React from "react";
import { fetchComments } from "../api/comments";
import { renderMarkdown } from "../utils/markdown";

export function saveSession(token) {
  localStorage.setItem("jwt", token);
}

export default function CommentList({ videoId }) {
  const [comments, setComments] = React.useState([]);

  React.useEffect(() => {
    fetchComments(videoId).then(setComments).catch(() => setComments([]));
  }, [videoId]);

  return (
    <ul className="comment-list">
      {comments.map((c) => (
        <li key={c.id}>
          <span className="author">{c.author}</span>
          <div
            className="body"
            dangerouslySetInnerHTML={{ __html: renderMarkdown(c.body) }}
          />
        </li>
      ))}
    </ul>
  );
}
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "security",
                "severity": "high",
                "title": "XSS：用户内容渲染进 dangerouslySetInnerHTML 且未消毒",
                "detail": "c.body 是用户提交的内容，renderMarkdown 的输出未经 sanitize（如 DOMPurify）直接注入 dangerouslySetInnerHTML，攻击者可注入 <img onerror> / <script> 窃取其他用户会话。必须在注入前对 HTML 消毒。",
                "line_hint": "dangerouslySetInnerHTML",
            },
            {
                "id": "GT-2",
                "category": "security",
                "severity": "medium",
                "title": "JWT 迁移到 localStorage 更易被 XSS 窃取",
                "detail": "saveSession 由 sessionStorage 改为 localStorage：localStorage 持久化且同源脚本可直接读取，一旦出现 XSS（结合 GT-1）token 可被长期窃取。建议保持在 sessionStorage 或改用 HttpOnly Cookie。",
                "line_hint": "localStorage.setItem",
            },
        ],
    },
    # ------------------------------------------------------------------ 005
    {
        "id": "case-005",
        "language": "python",
        "file_path": "etl/load_orders.py",
        "change_summary": "ETL 新增从文本文件批量导入订单的入口",
        "pre": '''"""订单 ETL 工具。"""
from datetime import datetime


def parse_order(line: str) -> dict:
    parts = line.strip().split("\\t")
    return {
        "order_id": parts[0],
        "amount": float(parts[1]),
        "created_at": datetime.fromisoformat(parts[2]),
    }


def save_order(record: dict) -> None:
    """写入数据库（此处为桩实现）。"""
    print("saved", record["order_id"])
''',
        "post": '''"""订单 ETL 工具。"""
from datetime import datetime


def parse_order(line: str) -> dict:
    parts = line.strip().split("\\t")
    return {
        "order_id": parts[0],
        "amount": float(parts[1]),
        "created_at": datetime.fromisoformat(parts[2]),
    }


def save_order(record: dict) -> None:
    """写入数据库（此处为桩实现）。"""
    print("saved", record["order_id"])


def import_orders(path: str) -> int:
    f = open(path, "r", encoding="utf-8")
    count = 0
    try:
        for line in f:
            record = parse_order(line)
            try:
                save_order(record)
            except Exception:
                pass
            count += 1
    except Exception:
        return -1
    return count
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "robustness",
                "severity": "medium",
                "title": "文件句柄未关闭：open 后缺少 close/上下文管理器",
                "detail": "import_orders 中 open(path) 的文件对象从未关闭（仅 try，无 finally/with），函数提前 return -1 或调用方持有引用期间都会造成资源泄漏。应使用 with open(...) as f。",
                "line_hint": "open(path",
            },
            {
                "id": "GT-2",
                "category": "correctness",
                "severity": "medium",
                "title": "save_order 异常被静默吞掉，且失败记录仍被计数",
                "detail": "内层 except Exception: pass 吞掉单条保存错误，count += 1 在 except 之外仍执行，导致返回值把失败记录也算作成功；外层 except 返回 -1 也丢失了错误信息。至少应记录失败明细/使用日志，并让计数反映真实成功数。",
                "line_hint": "except Exception:\\n                pass",
            },
        ],
    },
    # ------------------------------------------------------------------ 006
    {
        "id": "case-006",
        "language": "go",
        "file_path": "worker/aggregator.go",
        "change_summary": "聚合器新增并发事件计数与多文件读取",
        "pre": '''package worker

import (
	"fmt"
	"io"
)

type Event struct {
	Key   string
	Value int
}

type Aggregator struct{}

func (a *Aggregator) Collect(events []Event, out io.Writer) error {
	counts := make(map[string]int)
	for _, e := range events {
		counts[e.Key] += e.Value
	}
	for k, v := range counts {
		if _, err := fmt.Fprintf(out, "%s=%d\\n", k, v); err != nil {
			return err
		}
	}
	return nil
}
''',
        "post": '''package worker

import (
	"fmt"
	"io"
	"os"
	"sync"
)

type Event struct {
	Key   string
	Value int
}

type Aggregator struct{}

func (a *Aggregator) Collect(events []Event, out io.Writer) error {
	counts := make(map[string]int)
	var wg sync.WaitGroup
	for _, e := range events {
		wg.Add(1)
		go func(e Event) {
			defer wg.Done()
			counts[e.Key] += e.Value
		}(e)
	}
	wg.Wait()
	for k, v := range counts {
		if _, err := fmt.Fprintf(out, "%s=%d\\n", k, v); err != nil {
			return err
		}
	}
	return nil
}

func (a *Aggregator) MergeFiles(paths []string, out io.Writer) error {
	for _, p := range paths {
		f, err := os.Open(p)
		if err != nil {
			return err
		}
		defer f.Close()
		if _, err := io.Copy(out, f); err != nil {
			return err
		}
	}
	return nil
}
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "correctness",
                "severity": "high",
                "title": "多 goroutine 并发写 map，存在数据竞争",
                "detail": "counts 是普通 map，多个 goroutine 同时执行 counts[e.Key] += e.Value 未加任何锁。Go 的 map 并发写会触发 fatal error: concurrent map writes 直接崩溃进程。应加 sync.Mutex/RWMutex，或改用分片聚合、sync.Map。",
                "line_hint": "counts[e.Key] += e.Value",
            },
            {
                "id": "GT-2",
                "category": "robustness",
                "severity": "medium",
                "title": "循环内 defer f.Close() 导致句柄延迟到函数结束才释放",
                "detail": "MergeFiles 在 for 循环内 defer，所有打开的文件要等函数返回才关闭；路径很多时会同时持有大量句柄。应把循环体提取成函数，或在每次迭代内显式关闭（注意检查 Close 错误）。",
                "line_hint": "defer f.Close()",
            },
        ],
    },
    # ------------------------------------------------------------------ 007
    {
        "id": "case-007",
        "language": "python",
        "file_path": "api/download.py",
        "change_summary": "新增静态资料文件下载接口",
        "pre": '''"""资料下载 API。"""
from flask import Blueprint, send_file

bp = Blueprint("download", __name__)

UPLOAD_DIR = "/srv/app/uploads"


@bp.route("/healthz")
def healthz():
    return {"ok": True}
''',
        "post": '''"""资料下载 API。"""
import os

from flask import Blueprint, abort, request, send_file

bp = Blueprint("download", __name__)

UPLOAD_DIR = "/srv/app/uploads"


@bp.route("/healthz")
def healthz():
    return {"ok": True}


@bp.route("/files/<name>")
def download(name: str):
    file_path = os.path.join(UPLOAD_DIR, name)
    return send_file(file_path, as_attachment=True)
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "security",
                "severity": "high",
                "title": "路径遍历：URL 中的 name 可跳出 UPLOAD_DIR",
                "detail": "name 来自 URL 且未过滤，传入 ..%2f..%2fetc%2fpasswd 时 os.path.join(UPLOAD_DIR, name) 会直接指向 UPLOAD_DIR 之外，任意文件可被下载。应使用 werkzeug.utils.secure_filename 或校验 realpath 必须位于 UPLOAD_DIR 内。",
                "line_hint": "os.path.join(UPLOAD_DIR, name)",
            },
            {
                "id": "GT-2",
                "category": "security",
                "severity": "low",
                "title": "缺少文件类型白名单",
                "detail": "接口允许下载目录内任意类型文件（包括上传的 .html/.exe 等），建议按业务限定扩展名白名单，降低被滥用与存储型风险。",
                "line_hint": "send_file(file_path",
            },
        ],
    },
    # ------------------------------------------------------------------ 008
    {
        "id": "case-008",
        "language": "java",
        "file_path": "src/main/java/com/shop/order/OrderQueryService.java",
        "change_summary": "订单查询服务支持按日期展示与前端自定义分页大小",
        "pre": '''package com.shop.order;

import java.util.List;

public class OrderQueryService {

    private final OrderDao orderDao;

    public OrderQueryService(OrderDao orderDao) {
        this.orderDao = orderDao;
    }

    public List<Order> listOrders(int page) {
        return orderDao.find(page, 20);
    }
}
''',
        "post": '''package com.shop.order;

import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;

public class OrderQueryService {

    private static final SimpleDateFormat DATE_FMT = new SimpleDateFormat("yyyy-MM-dd HH:mm:ss");

    private final OrderDao orderDao;

    public OrderQueryService(OrderDao orderDao) {
        this.orderDao = orderDao;
    }

    public List<Order> listOrders(int page, int pageSize) {
        return orderDao.find(page, pageSize);
    }

    public String formatExportTitle(Date start, Date end) {
        return "Orders " + DATE_FMT.format(start) + " ~ " + DATE_FMT.format(end);
    }
}
''',
        "ground_truth": [
            {
                "id": "GT-1",
                "category": "correctness",
                "severity": "medium",
                "title": "静态共享 SimpleDateFormat 线程不安全",
                "detail": "SimpleDateFormat 非线程安全，这里作为 static final 被所有请求线程共享（formatExportTitle 会被并发调用），并发下会产生错乱日期甚至 ArrayIndexOutOfBoundsException。建议改为 ThreadLocal、方法内局部变量或 java.time.format.DateTimeFormatter（线程安全）。",
                "line_hint": "static final SimpleDateFormat",
            },
            {
                "id": "GT-2",
                "category": "robustness",
                "severity": "medium",
                "title": "pageSize 未做上限与下限校验",
                "detail": "pageSize 直接透传到 DAO 的 SQL LIMIT：调用方可传 1000000 造成深分页/大结果集拖垮数据库，也可传 0 或负数引发异常。应限制 pageSize ∈ [1, 100] 之类的安全范围。",
                "line_hint": "listOrders(int page, int pageSize)",
            },
        ],
    },
    # ------------------------------------------------------------------ 009（干净案例，GT=0）
    {
        "id": "case-009",
        "language": "python",
        "file_path": "common/text/casing.py",
        "change_summary": "新增 snake_case 与 lowerCamelCase 互转工具",
        "pre": '''"""文本命名风格工具。"""
''',
        "post": '''"""文本命名风格工具。"""
import re

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def snake_to_camel(name: str) -> str:
    """snake_case -> lowerCamelCase。连续下划线按空字段跳过。"""
    if not name:
        return name
    head, *rest = name.split("_")
    return head + "".join(part.title() for part in rest if part)


def camel_to_snake(name: str) -> str:
    """lowerCamelCase / UpperCamelCase -> snake_case。"""
    if not name:
        return name
    return _CAMEL_BOUNDARY.sub("_", name).lower()
''',
        "ground_truth": [],
    },
    # ------------------------------------------------------------------ 010（干净案例，GT=0）
    {
        "id": "case-010",
        "language": "python",
        "file_path": "common/config/settings.py",
        "change_summary": "新增带缓存的配置读取模块",
        "pre": '''"""应用配置。"""
''',
        "post": '''"""应用配置。"""
import json
import os
from functools import lru_cache

DEFAULT_PATH = os.path.join(os.path.dirname(__file__), "defaults.json")


@lru_cache(maxsize=8)
def load_settings(path: str = DEFAULT_PATH) -> dict:
    """加载并缓存 JSON 配置文件。"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_setting(key: str, default=None):
    return load_settings().get(key, default)


def clear_settings_cache() -> None:
    """测试或热更新后手动清除缓存。"""
    load_settings.cache_clear()
''',
        "ground_truth": [],
    },
]


def build_diff(pre: str, post: str, file_path: str) -> str:
    diff = list(
        difflib.unified_diff(
            pre.splitlines(keepends=True),
            post.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
        )
    )
    return "".join(diff) if diff else ""


def build_case_record(case: dict) -> dict:
    return {
        "id": case["id"],
        "language": case["language"],
        "file_path": case["file_path"],
        "change_summary": case["change_summary"],
        "diff": build_diff(case["pre"], case["post"], case["file_path"]),
        "source_code": case["post"],
        "ground_truth": case["ground_truth"],
        "gt_count": len(case["ground_truth"]),
        "is_clean_case": len(case["ground_truth"]) == 0,
    }


def main() -> None:
    records = [build_case_record(c) for c in CASES]
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8", newline="\n") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    total_gt = sum(r["gt_count"] for r in records)
    print(f"OK 已生成 {OUT}（{len(records)} 条案例，标准答案共 {total_gt} 条）")


if __name__ == "__main__":
    main()
