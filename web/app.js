/* SkillEvolve-CR Dashboard —— Vue3 + Element Plus 版
   顺序链路：① 评测集（触发入口） → ② 运行监控（逐案例进度/日志） → ③ 结果分析 · Skill 演进（R1/R2 对比+优化点+版本演进） */
"use strict";

const { createApp } = Vue;
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const fmtPct = (v) => (typeof v === "number" ? (v * 100).toFixed(1) + "%" : "—");
const fmt2 = (v) => (typeof v === "number" ? v.toFixed(2) : "—");
const fmtCost = (v) => (typeof v === "number" ? "$" + v.toFixed(4) : "—");
const fmtTok = (n) => (typeof n === "number" && n > 0
  ? (n >= 1e6 ? (n / 1e6).toFixed(2) + "M" : n >= 1000 ? (n / 1000).toFixed(1) + "K" : String(n))
  : "—");
const fmtDurS = (sec) => {
  if (typeof sec !== "number" || !isFinite(sec)) return "—";
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + "s";
  const m = Math.floor(sec / 60), s = sec % 60;
  if (m < 60) return `${m}m${String(s).padStart(2, "0")}s`;
  return `${Math.floor(m / 60)}h${String(m % 60).padStart(2, "0")}m`;
};
const App = {
  data() {
    return {
      tab: "evaluate",
      theme: localStorage.getItem("sev-theme") || "light",
      // 评测集
      dataset: [], selected: [], rounds: 3, launching: null,
      startSkill: "main", skillInfo: null,
      // 运行
      status: null, lastState: null, pollStarted: 0,
      // 运行列表与报告
      runs: [], selectedRun: "", report: null,
      // 结果分析
    roundNo: null,
    // session 执行轨迹抽屉
    traceDrawer: false, sessionTrace: null, sessionTraceLoading: false, sessionTraceKey: "",
      // Skill 演进
      skillDiffIdx: 0,
      // 历史日志
      histLogVisible: false, histLogText: "", histLogRid: "",
      // 图标
      Refresh: ElementPlusIconsVue.Refresh,
      Download: ElementPlusIconsVue.Download,
      Moon: ElementPlusIconsVue.Moon,
      Sunny: ElementPlusIconsVue.Sunny,
      VideoPlay: ElementPlusIconsVue.VideoPlay,
      RefreshRight: ElementPlusIconsVue.RefreshRight,
      SwitchButton: ElementPlusIconsVue.SwitchButton,
    };
  },

  computed: {
    running() { return !!(this.status && this.status.state === "running"); },
    stageText() {
      return { review: "① 检视生成", scoring: "② 评分", optimize: "③ SKILL 优化" }[this.status?.stage] || "…";
    },
    stageActive() {
      if (!this.status || this.status.state === "idle") return 0;
      if (this.status.state === "finished") return 3;
      if (this.status.state === "error") return { review: 1, scoring: 2, optimize: 3 }[this.status.stage] || 1;
      return { review: 1, scoring: 2, optimize: 3 }[this.status.stage] || 0;
    },
    progressPct() {
      const st = this.status;
      if (!st || st.state === "idle") return 0;
      const frac = { review: 0.2, scoring: 0.6, optimize: 0.9 }[st.stage] ?? 0.1;
      if (st.state !== "running") return st.final ? 100 : (st.rounds_total ? Math.round(100 * st.rounds_done / st.rounds_total) : 0);
      if (!st.rounds_total) return 5;
      return Math.min(99, Math.round(100 * (st.rounds_done + frac) / st.rounds_total));
    },
    runningElapsed() {
      if (this.status?.state === "running" && this.status.started_at) {
        return fmtDurS(Date.now() / 1000 - this.status.started_at);
      }
      return "—";
    },
    gtTotal() { return this.dataset.reduce((a, c) => a + (c.gt_count || 0), 0); },
    estSingle() { return this.selected.length ? this.selected.length * 2 : 0; },
    estMulti() {
      const n = this.selected.length;
      return n ? n * 2 * this.rounds + Math.max(0, this.rounds - 1) : 0;
    },
    reportGtTotal() { return (this.report?.dataset || []).reduce((a, c) => a + (c.gt_count || 0), 0); },
    totalCost() {
      if (!this.report?.rounds) return 0;
      return this.report.rounds.reduce((a, r) => a + (this.costOf(r) || 0), 0);
    },
    summaryMetrics() {
      const rounds = this.report?.rounds || [];
      if (!rounds.length) return [];
      const last = rounds[rounds.length - 1], base = rounds[0].metrics, m = last.metrics;
      const mk = (label, val, key, tip) => {
        const d = m[key] - base[key];
        const flat = Math.abs(d) < 1e-9;
        return {
          label, value: val, tip,
          delta: flat ? "与 R1 持平" : `较 R1 ${d >= 0 ? "+" : ""}${fmt2(d)}`,
          deltaColor: flat ? "var(--el-text-color-secondary)" : d > 0 ? "var(--el-color-success)" : "var(--el-color-danger)",
        };
      };
      return [
        mk("F1 · 综合分", fmt2(m.f1), "f1", "F1 = 2·P·R/(P+R)：准确率与召回率的调和平均，两者都高 F1 才高，是衡量整体检视质量的首要指标，1.00 为满分。"),
        mk("Precision · 准确率", fmtPct(m.precision), "precision", "Precision = TP/(TP+FP)：报出的每条检视意见里有多少与标准答案对得上。偏低说明误报（FP）多，SKILL 优化主要提升这项。"),
        mk("Recall · 召回率", fmtPct(m.recall), "recall", "Recall = TP/(TP+FN)：标准答案中的真实缺陷被检出了多少。偏低说明漏报（FN）多，优化后需警惕它回退。"),
      ];
    },
    roundData() {
      const r = (this.report?.rounds || []).find((x) => x.round === this.roundNo);
      return r || { metrics: {}, per_case: [], skill_version: "", case_details: {}, trace_report: { issues: [], stage_summary: {} } };
    },
    cmp() {
      const rounds = this.report?.rounds || [];
      if (rounds.length < 2) return null;
      const first = rounds[0], last = rounds[rounds.length - 1];
      const base = first.metrics, m = last.metrics;
      const issueCases = (first.per_case || []).filter((c) => c.fp + c.fn > 0).map((c) => {
        const det = first.case_details?.[c.case_id] || {};
        const fps = (det.verdicts || []).filter((v) => v.verdict === "FP").map((v) => {
          const f = (det.generated_findings || []).find((x) => x.id === v.finding_id) || {};
          return { title: f.title || "(意见)", reason: v.reason || "" };
        });
        const lastPc = (last.per_case || []).find((x) => x.case_id === c.case_id) || {};
        const det2 = last.case_details?.[c.case_id] || {};
        return { case_id: c.case_id, fp: c.fp, fn: c.fn, fps, fns: det.unmatched_gt || [],
          findings_r1: (det.generated_findings || []).length, findings_r2: (det2.generated_findings || []).length,
          fp_r2: lastPc.fp ?? 0, fn_r2: lastPc.fn ?? 0, ok: (lastPc.fp || 0) + (lastPc.fn || 0) === 0 };
      });
      const cl = (this.report?.comparison?.skill_diffs || []).slice(-1)[0]?.changelog || null;
      return { multi: true, optimized: last.skill_version !== first.skill_version, first, last, base, m, issueCases, changelog: cl, rounds };
    },
    cmpRows() {
      if (!this.cmp) return [];
      const { base, m } = this.cmp;
      const flat = (d) => Math.abs(d) < 1e-9;
      const ppDelta = (k) => {
        const d = (m[k] - base[k]) * 100;
        if (flat(d)) return { delta: "持平", deltaColor: "var(--el-text-color-secondary)" };
        return { delta: `${d > 0 ? "+" : ""}${d.toFixed(1)}pp`, deltaColor: d > 0 ? "var(--el-color-success)" : "var(--el-color-danger)" };
      };
      const numDelta = (k) => {
        const d = m[k] - base[k];
        if (d === 0) return { delta: "持平", deltaColor: "var(--el-text-color-secondary)" };
        return { delta: `${d > 0 ? "+" : ""}${d}`, deltaColor: d < 0 ? "var(--el-color-success)" : "var(--el-color-danger)" };
      };
      const f1d = m.f1 - base.f1;
      const f1 = flat(f1d) ? { delta: "持平", deltaColor: "var(--el-text-color-secondary)" }
        : { delta: `${f1d > 0 ? "+" : ""}${fmt2(f1d)}`, deltaColor: f1d > 0 ? "var(--el-color-success)" : "var(--el-color-danger)" };
      return [
        { label: "Precision 准确率", tip: "Precision = TP / (TP+FP)：模型报出的每条检视意见中，与标准答案对得上的比例。P=75% 意味着每 4 条意见就有 1 条是误报。它直接反映误报（FP）的多少，是 SKILL 优化重点提升的指标。", v1: fmtPct(base.precision), v2: fmtPct(m.precision), cls2: m.precision >= base.precision ? "good" : "bad", ...ppDelta("precision") },
        { label: "Recall 召回率", tip: "Recall = TP / (TP+FN)：标准答案中标注的真实缺陷被检视检出的比例。R=100% 表示没有任何漏报（FN）。召回主要取决于模型能力，优化 SKILL 时应保持它不回退。", v1: fmtPct(base.recall), v2: fmtPct(m.recall), cls2: m.recall >= base.recall ? "good" : "bad", ...ppDelta("recall") },
        { label: "F1 综合分", tip: "F1 = 2·P·R/(P+R)：准确率与召回率的调和平均，只有两者都高 F1 才高。它是衡量整体检视质量与优化效果的首要指标，1.00 为满分。", v1: fmt2(base.f1), v2: fmt2(m.f1), cls2: m.f1 >= base.f1 ? "good" : "bad", ...f1 },
        { label: "TP 命中", tip: "True Positive：检视报出、且标准答案中确实存在的缺陷数量。TP 越多说明真实检出越多；TP 为 0 通常意味着检视完全没起作用。", v1: base.tp, v2: m.tp, cls2: "", ...numDelta("tp") },
        { label: "FP 误报", tip: "False Positive：检视报出、但标准答案不支持的条目——包括推测性风险、无证据的性能/并发担忧、风格化建议等。FP 是优化器要消除的主要对象，清零即 Precision=100%。", v1: base.fp, v2: m.fp, bad1: base.fp > 0, cls2: m.fp < base.fp ? "good" : (m.fp > base.fp ? "bad" : ""), ...numDelta("fp") },
        { label: "FN 漏报", tip: "False Negative：标准答案中存在、但检视未报出的真实缺陷。FN 增多说明优化牺牲了召回（矫枉过正），此时护栏会把指标拉回历史最佳版本。", v1: base.fn, v2: m.fn, bad1: base.fn > 0, cls2: m.fn < base.fn ? "good" : (m.fn > base.fn ? "bad" : ""), ...numDelta("fn") },
      ];
    },
    traceIssues() { return this.roundTrace?.issues || []; },
    issueCount() { return this.traceIssues.length; },
    roundTrace() {
      const r = (this.report?.rounds || []).find((x) => x.round === this.roundNo);
      return r?.trace_report || { issues: [], stage_summary: {} };
    },
    sessionRows() {
      const r = (this.report?.rounds || []).find((x) => x.round === this.roundNo);
      const rows = [];
      Object.entries(r?.case_details || {}).forEach(([cid, det]) => {
        const push = (stage, stage_key, s, t) => {
          const u = s?.usage || {};
          rows.push({
            cid, stage, stage_key, sid: s?.session_id || "-", turns: s?.num_turns ?? "-",
            dur: s?.duration_ms ? Math.round(s.duration_ms / 1000) + "s" : "-",
            durMs: s?.duration_ms || 0,
            cost: s?.cost_usd != null ? "$" + Number(s.cost_usd).toFixed(4) : "-",
            costNum: s?.cost_usd ?? 0,
            inTok: (u.input_tokens || 0) + (u.cache_read_input_tokens || 0) + (u.cache_creation_input_tokens || 0),
            outTok: u.output_tokens || 0,
            cacheRead: u.cache_read_input_tokens || 0,
            loaded: !!t?.skill_loaded, calls: t?.num_tool_calls ?? "-",
          });
        };
        push("①检视", "review", det.review_session, det.review_trace);
        push("②评分", "score", det.score_session, det.score_trace);
      });
      const os = r?.optimizer?.session;
      if (os?.session_id) {
        const ou = os.usage || {};
        rows.push({
          cid: "—", stage: "③优化", stage_key: "optimize", sid: os.session_id,
          turns: os.num_turns ?? "-", dur: os.duration_ms ? Math.round(os.duration_ms / 1000) + "s" : "-",
          durMs: os.duration_ms || 0,
          cost: os.cost_usd != null ? "$" + Number(os.cost_usd).toFixed(4) : "-", costNum: os.cost_usd ?? 0,
          inTok: (ou.input_tokens || 0) + (ou.cache_read_input_tokens || 0) + (ou.cache_creation_input_tokens || 0),
          outTok: ou.output_tokens || 0, cacheRead: ou.cache_read_input_tokens || 0,
          loaded: true, calls: "-",
        });
      }
      return rows;
    },
    resStats() {
      const rows = this.sessionRows;
      const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
      const inTok = sum("inTok"), cacheRead = sum("cacheRead");
      return {
        dur: sum("durMs"), cost: sum("costNum"), inTok, outTok: sum("outTok"),
        hit: inTok ? Math.round((100 * cacheRead) / inTok) : null,
        calls: rows.reduce((a, r) => a + (typeof r.calls === "number" ? r.calls : 0), 0),
        sessions: rows.length,
      };
    },
    stageMatrix() {
      const mk = (key, label) => {
        const rows = this.sessionRows.filter((r) => r.stage_key === key);
        if (!rows.length) return null;
        const sum = (k) => rows.reduce((a, r) => a + (r[k] || 0), 0);
        const inTok = sum("inTok"), cacheRead = sum("cacheRead");
        return {
          label, n: rows.length, dur: sum("durMs"), cost: sum("costNum"),
          inTok, outTok: sum("outTok"),
          hit: inTok ? Math.round((100 * cacheRead) / inTok) : null,
          calls: rows.reduce((a, r) => a + (typeof r.calls === "number" ? r.calls : 0), 0),
        };
      };
      return [mk("review", "① 检视"), mk("score", "② 评分"), mk("optimize", "③ 优化")].filter(Boolean);
    },
    roundRows() {
      const rounds = this.report?.rounds || [];
      return rounds.map((r, idx) => {
        const m = r.metrics || {}, o = r.optimizer || {};
        let action = "—", atype = "info";
        if (o.ran && o.valid) {
          const next = rounds[idx + 1];
          action = next && next.skill_version !== r.skill_version ? `SKILL 优化 → ${next.skill_version}` : "SKILL 优化执行";
          atype = "success";
        } else if (o.ran) {
          action = "优化产物未过校验，已回退"; atype = "danger";
        } else if (o.reason) {
          action = "跳过优化 · 满分无告警"; atype = "info";
        } else if (idx === rounds.length - 1) {
          action = "末轮（无优化阶段）"; atype = "info";
        }
        return { round: r.round, skill: r.skill_version, p: fmtPct(m.precision), r: fmtPct(m.recall), f1: fmt2(m.f1),
                 tfn: `${m.tp ?? "-"} / ${m.fp ?? "-"} / ${m.fn ?? "-"}`, fp: m.fp || 0, fn: m.fn || 0, action, atype };
      });
    },
    /* SKILL 行级 diff：LCS 对齐，左右两栏按行配对，删行红底 / 增行绿底 */
    skRows() {
      const oldText = this.versionPair.oldText, newText = this.versionPair.newText;
      if (!oldText || !newText || this.versionPair.same) return null;
      const a = oldText.split("\n"), b = newText.split("\n");
      const m = a.length, n = b.length;
      const dp = Array.from({ length: m + 1 }, () => new Uint16Array(n + 1));
      for (let i = m - 1; i >= 0; i--) for (let j = n - 1; j >= 0; j--)
        dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1]);
      const ops = []; let i = 0, j = 0;
      while (i < m && j < n) {
        if (a[i] === b[j]) { ops.push(["=", a[i], i + 1, j + 1]); i++; j++; }
        else if (dp[i + 1][j] >= dp[i][j + 1]) { ops.push(["-", a[i], i + 1, null]); i++; }
        else { ops.push(["+", b[j], null, j + 1]); j++; }
      }
      while (i < m) { ops.push(["-", a[i], i + 1, null]); i++; }
      while (j < n) { ops.push(["+", b[j], null, j + 1]); j++; }
      const rows = []; let k = 0, add = 0, del = 0;
      while (k < ops.length) {
        if (ops[k][0] === "=") { rows.push({ lt: "eq", lno: ops[k][2], l: ops[k][1], rt: "eq", rno: ops[k][3], r: ops[k][1] }); k++; continue; }
        const dels = [], adds = [];
        while (k < ops.length && ops[k][0] === "-") { dels.push(ops[k]); k++; }
        while (k < ops.length && ops[k][0] === "+") { adds.push(ops[k]); k++; }
        del += dels.length; add += adds.length;
        const len = Math.max(dels.length, adds.length);
        for (let t = 0; t < len; t++) {
          const d = dels[t], ad = adds[t];
          rows.push({ lt: d ? "del" : "gap", lno: d ? d[2] : "", l: d ? d[1] : "",
                      rt: ad ? "add" : "gap", rno: ad ? ad[3] : "", r: ad ? ad[1] : "" });
        }
      }
      return { rows, add, del };
    },
    versionPair() {
      const sv = this.report?.skill_versions || {};
      const from = this.currentDiff.from || "v1";
      const to = this.currentDiff.to || "v1";
      return { from, to, oldText: sv[from] || "", newText: sv[to] || "", same: from === to };
    },
    diffs() { return this.report?.comparison?.skill_diffs || []; },
    currentDiff() { return this.diffs[this.skillDiffIdx] || {}; },
    changelogHtml() {
      const cl = this.currentDiff.changelog;
      if (!cl) return `<div class="muted">无 changelog（${esc(this.currentDiff.from)}→${esc(this.currentDiff.to)}）</div>`;
      const tag = (t) => t === "added" ? "success" : t === "removed" ? "danger" : "info";
      return `<div class="cl-summary">${esc(cl.summary || "")}</div>` +
        (cl.changes || []).map((c) => `<div class="cl-item">
          <span class="ttag ${tag(c.type)}">${esc(c.type)}</span>
          <div class="cl-main">
            <div class="cl-title">${esc(c.section || "")}<span class="cl-sep">·</span>${esc(c.description)}</div>
            ${c.rationale ? `<div class="cl-line"><span class="cl-key">为什么</span><span class="cl-val">${esc(c.rationale)}</span></div>` : ""}
            ${c.addresses?.length ? `<div class="cl-line"><span class="cl-key">针对</span><span class="cl-val mono">${esc(c.addresses.join(", "))}</span></div>` : ""}
          </div>
        </div>`).join("");
    },
    trendHtml() { return this.buildTrend(); },
    highlightsHtml() {
      const hs = this.report?.comparison?.highlights || [];
      if (!hs.length) return `<li class="muted">仅一轮数据，暂无比对结论</li>`;
      return hs.map((h) => {
        const color = h.includes("回退") ? "var(--el-color-danger)" : h.includes("修复") ? "var(--el-color-success)" : h.includes("未通过校验") ? "var(--el-color-warning)" : "var(--el-color-primary)";
        return `<li style="border-left-color:${color}">${esc(h)}</li>`;
      }).join("");
    },
  },

  methods: {
    /* ---------- 工具 ---------- */
    fmt2, fmtPct, fmtCost,
    fmtDur(sec) { return fmtDurS(sec); },
    runLabel(r) {
      const m = r.last_metrics || {};
      return `${r.run_id} · ${r.rounds}轮 · F1=${fmt2(m.f1)} · ${r.final ? "完成" : "进行中"}`;
    },
    async getJSON(url) { try { return await (await fetch(url)).json(); } catch (e) { return null; } },
    async postJSON(url, payload) {
      try {
        const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload || {}) });
        let data = {};
        try { data = await res.json(); } catch (e) { /* ignore */ }
        return { ok: res.ok, data };
      } catch (e) { return { ok: false, data: { error: "网络错误：" + e.message } }; }
    },

    /* ---------- 数据加载 ---------- */
    async loadAll() {
      await Promise.all([this.loadDataset(), this.loadRuns(), this.loadSkillInfo()]);
      this.poll();
    },
    async loadDataset() {
      const data = await this.getJSON("/api/dataset");
      this.dataset = data?.cases || [];
      await this.$nextTick();
      this.applyDefaultSelection();
    },
    _toggleRows(ids) {
      const table = this.$refs.datasetTable;
      if (!table) return;
      table.clearSelection();
      this.$nextTick(() => {
        this.dataset.filter((c) => ids.includes(c.id)).forEach((row) => table.toggleRowSelection(row, true));
      });
    },
    applyDefaultSelection() {
      if (!this.$refs.datasetTable) return;
      let ids = this.smokeIds();
      if (!ids.length && this.dataset.length) ids = [this.dataset[0].id];
      this._toggleRows(ids);
    },
    smokeIds() {
      const def = this.dataset.find((c) => !c.is_clean_case);
      const clean = this.dataset.find((c) => c.is_clean_case);
      return [def?.id, clean?.id].filter(Boolean);
    },
    onRowClick(row, column, event) {
      if (event?.target?.closest?.(".el-checkbox")) return;
      this.$refs.datasetTable.toggleRowSelection(row);
    },
    onSelectionChange(rows) { this.selected = rows.map((r) => r.id); },
    selectQuick(kind) {
      if (!this.$refs.datasetTable) return;
      let ids = [];
      if (kind === "all") ids = this.dataset.map((c) => c.id);
      else if (kind === "clean") ids = this.dataset.filter((c) => c.is_clean_case).map((c) => c.id);
      else ids = this.smokeIds();
      this._toggleRows(ids);
    },
    async loadSkillInfo() {
      this.skillInfo = await this.getJSON("/api/skill-info");
    },
    async restoreSkill() {
      if (this.running) { ElementPlus.ElMessage.warning("有运行进行中，请稍后再回退"); return; }
      try {
        await ElementPlus.ElMessageBox.confirm(
          "将把主 SKILL（skills/code-review/SKILL.md）覆盖为初始版本基线内容。当前内容仍可从历史批次快照找回。确认回退？",
          "回退主 SKILL", { confirmButtonText: "回退", cancelButtonText: "取消", type: "warning" });
      } catch (e) { return; }
      const r = await this.postJSON("/api/skill-restore", {});
      if (r.ok) {
        ElementPlus.ElMessage.success(`主 SKILL 已回退到初始版本（${r.data.lines} 行）`);
        await this.loadSkillInfo();
      } else {
        ElementPlus.ElMessage.error(r.data.error || "回退失败");
      }
    },
    async loadRuns() {
      const data = await this.getJSON("/api/runs");
      this.runs = data?.runs || [];
      if (!this.runs.length) { this.report = null; this.selectedRun = ""; return; }
      const prev = this.selectedRun;
      this.selectedRun = prev && this.runs.some((r) => r.run_id === prev) ? prev : this.runs[0].run_id;
      await this.loadReport(this.selectedRun);
    },
    async loadReport(runId) {
      if (!runId) { this.report = null; return; }
      const rep = await this.getJSON(`/runs/${encodeURIComponent(runId)}/report.json`);
      this.report = rep;
      if (rep?.rounds?.length) {
        const keep = this.report?.rounds?.some((r) => r.round === this.roundNo);
        this.roundNo = keep && rep.run_id === (this._roundOwner || "") ? this.roundNo : rep.rounds[0].round;
        this._roundOwner = rep.run_id;
        this.skillDiffIdx = Math.max(0, (rep.comparison?.skill_diffs || []).length - 1);
      }
    },
    async onRunChange(runId) { await this.loadReport(runId); },
    async viewRun(runId) {
      this.selectedRun = runId;
      await this.loadReport(runId);
      this.tab = "results";
    },
    async showRunLog(runId) {
      if (this.histLogVisible && this.histLogRid === runId) { this.histLogVisible = false; return; }
      this.histLogRid = runId; this.histLogText = "加载中…"; this.histLogVisible = true;
      const data = await this.getJSON(`/api/run-log?run_id=${encodeURIComponent(runId)}`);
      this.histLogText = (data?.lines || []).join("\n") || "（无日志）";
    },

    /* ---------- 触发运行 ---------- */
    async launch(mode) {
      if (!this.selected.length) { ElementPlus.ElMessage.warning("请先勾选要评测的案例"); return; }
      this.launching = mode;
      const payload = { mode, cases: this.selected, start_skill: this.startSkill };
      if (mode === "multi") payload.rounds = this.rounds;
      const r = await this.postJSON("/api/run", payload);
      this.launching = null;
      if (r.ok) {
        const d = r.data;
        ElementPlus.ElMessage.success(`已触发 ${d.run_id}（${mode === "single" ? "单轮验证" : d.rounds + " 轮优化"} · ${this.selected.length} 条案例）`);
        this.tab = "monitor";
        this.poll();
      } else {
        ElementPlus.ElMessage.error(r.data.error || "触发失败");
      }
    },
    async stopRun() {
      const r = await this.postJSON("/api/stop");
      if (r.ok) { ElementPlus.ElMessage.info("停止指令已发送，进程即将终止"); setTimeout(() => this.poll(), 600); }
      else ElementPlus.ElMessage.error(r.data.error || "停止失败");
    },
    gotoResults() {
      const rid = this.status?.run_id;
      if (rid && this.selectedRun !== rid && this.runs.some((r) => r.run_id === rid)) {
        this.selectedRun = rid;
        this.loadReport(rid);
      }
      this.tab = "results";
    },
    toggleTheme() {
      this.theme = this.theme === "dark" ? "light" : "dark";
      localStorage.setItem("sev-theme", this.theme);
      document.documentElement.dataset.theme = this.theme;
      document.documentElement.classList.toggle("dark", this.theme === "dark");
    },

    /* ---------- 轮询 ---------- */
    async poll() {
      const st = await this.getJSON("/api/run-status");
      if (!st) return;
      this.status = st;
      this.$nextTick(() => {
        const el = this.$refs.logPre;
        if (el) el.scrollTop = el.scrollHeight;
      });
      if (this.lastState === "running" && st.state !== "running" && st.state !== "idle") {
        await this.loadRuns();
        if (st.state === "finished") ElementPlus.ElMessage.success(`运行 ${st.run_id} 已完成，可查看结果分析`);
        else ElementPlus.ElMessage.error(`运行 ${st.run_id} 异常退出（exit ${st.exit_code ?? "?"}），可在监控页查看日志`);
      }
      this.lastState = st.state;
    },

    /* ---------- 监控辅助 ---------- */
    caseTagType(cid) {
      const c = this.status?.cases?.[cid] || {};
      const fail = c.compile === "fail" || c.review === "fail" || c.score === "fail";
      if (fail) return "danger";
      if (c.review === "ok" && c.score === "ok") return "success";
      if (cid === this.status?.current_case && this.running) return "warning";
      return "info";
    },
    caseStateText(cid) {
      const c = this.status?.cases?.[cid] || {};
      const live = this.running;
      const parts = [];
      if (c.compile === "fail") parts.push("编译✗");
      if (c.review === "ok") parts.push("检视✓");
      else if (c.review === "fail") parts.push("检视✗");
      else if (c.compile === "ok" && cid === this.status?.current_case && live && this.status?.stage === "review") parts.push("检视中…");
      if (c.score === "ok") parts.push("评分✓");
      else if (c.score === "fail") parts.push("评分✗");
      else if (c.review === "ok" && live && this.status?.stage === "scoring") parts.push("评分中…");
      if (c.findings != null) parts.push(`${c.findings} 条意见`);
      return parts.length ? " · " + parts.join(" · ") : "";
    },

    /* ---------- 渲染 ---------- */
    costOf(r) {
      if (typeof r.cost_usd === "number") return r.cost_usd;
      let sum = 0, seen = false;
      for (const det of Object.values(r.case_details || {})) {
        const a = det.review_session?.cost_usd, b = det.score_session?.cost_usd;
        if (a != null) { sum += a; seen = true; }
        if (b != null) { sum += b; seen = true; }
      }
      const oc = r.optimizer?.session?.cost_usd;
      if (oc != null) { sum += oc; seen = true; }
      return seen ? sum : null;
    },
    loadPct(summary, stage) {
      const v = summary?.[stage]?.skill_loaded_ratio;
      return Math.round((v ?? 0) * 100);
    },
    avgTurns(summary, stage) { return summary?.[stage]?.avg_turns ?? "-"; },
    /* ---------- session 执行轨迹（Langfuse 风格） ---------- */
    kindLabel(s) {
      return { init: "会话", thinking: "思考", tool: "工具", text: "输出", truncated: "…" }[s.kind] || s.kind;
    },
    fmtMs(ms) {
      if (typeof ms !== "number" || !isFinite(ms) || ms < 0) return "";
      return ms < 1000 ? Math.round(ms) + "ms" : (ms / 1000).toFixed(1) + "s";
    },
    fmtTs(ts) {
      if (!ts) return "";
      const d = new Date(ts);
      return isNaN(d) ? "" : d.toLocaleTimeString("zh-CN", { hour12: false });
    },
    async openSessionTrace(row) {
      this.traceDrawer = true;
      this.sessionTraceKey = `${row.cid === "—" ? "优化器" : row.cid} · ${row.stage}`;
      this.sessionTrace = null;
      this.sessionTraceLoading = true;
      const q = new URLSearchParams({ run_id: this.selectedRun, round: String(this.roundNo), stage: row.stage_key });
      if (row.stage_key !== "optimize") q.set("case_id", row.cid);
      const d = await this.getJSON("/api/session-trace?" + q.toString());
      this.sessionTraceLoading = false;
      this.sessionTrace = (!d || d.error) ? { error: (d && d.error) || "加载失败", steps: [] } : d;
    },
    diffHtml(diffText) {
      if (!diffText) return `<div class="muted">（无 diff）</div>`;
      return diffText.split("\n").map((ln) => {
        if (ln.startsWith("+++") || ln.startsWith("---") || ln.startsWith("index ")) return `<span class="meta">${esc(ln)}</span>`;
        if (ln.startsWith("@@")) return `<span class="hunk">${esc(ln)}</span>`;
        if (ln.startsWith("+")) return `<span class="add">${esc(ln)}</span>`;
        if (ln.startsWith("-")) return `<span class="del">${esc(ln)}</span>`;
        return esc(ln);
      }).join("\n");
    },
    buildTrend() {
      const trend = this.report?.comparison?.metric_trend || [];
      if (!trend.length) return `<div class="center-empty muted">暂无数据</div>`;
      const dark = this.theme === "dark";
      const W = 980, H = 340;
      const C = { precision: "#6366f1", recall: "#0ea5e9", f1: "#10b981", tp: "#10b981", fp: "#ef4444", fn: "#f59e0b" };
      const grid = dark ? "#25314d" : "#e9edf5";
      const muted = dark ? "#a4aec3" : "#6b7690";
      const halo = `paint-order="stroke" stroke="${dark ? "#141d2f" : "#ffffff"}" stroke-width="5" stroke-linejoin="round"`;
      /* 折线区 y: 58..222 · 轮次标签 y=248 · 柱状区 y: 272..328 · 左留白 76 防坐标遮挡 */
      const padL = 76, padR = 26, padT = 58, lineH = 164;
      const barTop = 272, barH = 56, barBase = barTop + barH;
      const iw = W - padL - padR, ih = lineH;
      const n = trend.length;
      const x = (i) => padL + (n === 1 ? iw / 2 : (i * iw) / (n - 1));
      const y = (v) => padT + ih - v * ih;
      let s = "";
      s += `<defs><linearGradient id="gradF1" x1="0" y1="0" x2="0" y2="1">
        <stop offset="0%" stop-color="${C.f1}" stop-opacity="0.9"/>
        <stop offset="100%" stop-color="${C.f1}" stop-opacity="0"/>
      </linearGradient></defs>`;
      /* 图例 */
      const legend = [
        { c: C.precision, t: "Precision 准确率" },
        { c: C.recall, t: "Recall 召回率" },
        { c: C.f1, t: "F1 综合分" },
        { c: C.tp, t: "TP 命中" },
        { c: C.fp, t: "FP 误报" },
        { c: C.fn, t: "FN 漏报" },
      ];
      legend.forEach((l, i) => {
        const lx = padL + i * 152;
        s += `<rect x="${lx}" y="12" width="12" height="10" rx="3" fill="${l.c}"></rect>
          <text x="${lx + 17}" y="21" font-size="12" fill="${muted}">${l.t}</text>`;
      });
      /* 网格与 y 轴刻度 */
      for (let g = 0; g <= 4; g++) {
        const v = g / 4;
        s += `<line x1="${padL}" y1="${y(v)}" x2="${W - padR}" y2="${y(v)}" stroke="${grid}" stroke-width="1"></line>
          <text x="${padL - 10}" y="${y(v) + 4}" text-anchor="end" font-size="11.5" fill="${muted}">${v.toFixed(2)}</text>`;
      }
      /* F1 面积填充 */
      if (n > 1) {
        const pts = trend.map((t, i) => `${x(i)},${y(t.f1)}`).join(" ");
        s += `<polygon points="${padL},${y(0)} ${pts} ${x(n - 1)},${y(0)}" fill="${C.f1}" opacity="0.08"></polygon>`;
      }
      /* 三条折线 */
      for (const key of ["precision", "recall", "f1"]) {
        const pts = trend.map((t, i) => `${x(i)},${y(t[key])}`).join(" ");
        const isF1 = key === "f1";
        if (isF1) {
          const base = padT + ih;
          s += `<polygon points="${pts} ${x(n - 1)},${base} ${x(0)},${base}" fill="url(#gradF1)" opacity="0.14"></polygon>`;
        }
        s += `<polyline points="${pts}" fill="none" stroke="${C[key]}" stroke-width="${isF1 ? 3.4 : 2.2}" stroke-linejoin="round" stroke-linecap="round"></polyline>`;
        trend.forEach((t, i) => {
          s += `<circle cx="${x(i)}" cy="${y(t[key])}" r="${isF1 ? 5 : 4}" fill="${C[key]}" stroke="${dark ? "#141d2f" : "#fff"}" stroke-width="1.6"></circle>`;
        });
      }
      /* F1 数值与轮间变化量 */
      trend.forEach((t, i) => {
        const anchor = i === 0 ? "start" : i === n - 1 ? "end" : "middle";
        const lx = i === 0 ? x(i) + 10 : i === n - 1 ? x(i) - 10 : x(i);
        s += `<text x="${lx}" y="${y(t.f1) - 13}" text-anchor="${anchor}" font-size="12.5" font-weight="800" fill="${C.f1}" ${halo}>${fmt2(t.f1)}</text>`;
      });
      /* 轮间 Δ 已在对比表中展示，图内不再标注，避免与图例/折线重叠 */
      /* 轮次标签（折线区下方，两区共用 x 轴） */
      trend.forEach((t, i) => {
        s += `<text x="${x(i)}" y="248" text-anchor="middle" font-size="12.5" font-weight="800" fill="${muted}" ${halo}>R${t.round} <tspan font-weight="500" font-size="11.5">· ${esc(t.skill_version)}</tspan></text>`;
      });
      /* 下区：每轮 TP/FP/FN 构成柱（同一把尺：按多轮最大判定总数等比） */
      const maxTotal = Math.max(...trend.map((t) => t.tp + t.fp + t.fn), 1);
      const bw = Math.min(46, n === 1 ? 46 : (iw / n) * 0.5);
      s += `<line x1="${padL}" y1="${barBase}" x2="${W - padR}" y2="${barBase}" stroke="${grid}"></line>
        <text x="${padL - 30}" y="${barTop + 4}" text-anchor="end" font-size="11.5" fill="${muted}">${maxTotal}</text>
        <text x="${padL - 30}" y="${barBase + 4}" text-anchor="end" font-size="11.5" fill="${muted}">0</text>`;
      trend.forEach((t, i) => {
        const bx = x(i) - bw / 2;
        const hTP = (t.tp / maxTotal) * barH, hFP = (t.fp / maxTotal) * barH, hFN = (t.fn / maxTotal) * barH;
        const seg = (h, yy, color, val) => {
          let r = `<rect x="${bx}" y="${yy - h}" width="${bw}" height="${Math.max(h, 0)}" rx="3" fill="${color}" opacity="0.9"></rect>`;
          if (h >= 12 && val > 0) r += `<text x="${bx + bw / 2}" y="${yy - h / 2 + 3.5}" text-anchor="middle" font-size="10.5" font-weight="700" fill="#fff">${val}</text>`;
          return r;
        };
        s += seg(hTP, barBase, C.tp, t.tp) + seg(hFP, barBase - hTP, C.fp, t.fp) + seg(hFN, barBase - hTP - hFP, C.fn, t.fn);
      });
      /* 逐轮悬浮提示（整列热区） */
      const colW = n === 1 ? iw : iw / n;
      trend.forEach((t, i) => {
        const tip = `Round ${t.round} · 技能 ${t.skill_version}\nPrecision=${fmtPct(t.precision)}  Recall=${fmtPct(t.recall)}  F1=${fmt2(t.f1)}\nTP ${t.tp} · FP ${t.fp} · FN ${t.fn}`;
        s += `<rect x="${x(i) - colW / 2}" y="${padT - 6}" width="${colW}" height="${H - padT + 6}" fill="transparent" style="pointer-events:all"><title>${tip}</title></rect>`;
      });
      return `<svg viewBox="0 0 ${W} ${H}" style="width:100%;display:block" font-family="inherit">${s}</svg>`;
    },
  },

  async mounted() {
    await this.loadAll();
    setInterval(this.poll, 2000);
  },
};

const app = createApp(App);
// 模板内可用的格式化函数必须挂在 globalProperties（模块顶层 const 在模板作用域不可见）
app.config.globalProperties.fmtPct = fmtPct;
app.config.globalProperties.fmt2 = fmt2;
app.config.globalProperties.fmtCost = fmtCost;
app.config.globalProperties.fmtDurS = fmtDurS;
app.config.globalProperties.fmtTok = fmtTok;
app.use(ElementPlus, { locale: ElementPlusLocaleZhCn });
for (const [name, comp] of Object.entries(ElementPlusIconsVue)) app.component(name, comp);
app.mount("#app");
