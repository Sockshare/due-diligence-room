/* Due-Diligence Room Frontend Application (Vanilla JS, Zero Build Step) */
(function () {
  "use strict";

  const state = {
    config: null,
    currentView: "memo",
    runId: null,
    run: null,
    events: [],
    eventSource: null,
    charts: {},
    history: [],
    activeRuns: [],
  };

  const DEFAULT_AGENTS = [
    { name: "Analyst", role: "Business overview, 5-year financial trends, valuation snapshot, peers", color: "#4f9cf9" },
    { name: "Forensic Accountant", role: "Beneish, Sloan, Altman, working-capital days, cash conversion, dilution, SBC, auditor events", color: "#f5a524" },
    { name: "Legal", role: "Risk factors & YoY diff, legal proceedings, 8-K events, insider activity, executive pay", color: "#a78bfa" },
    { name: "Skeptic", role: "Bear case, counters to every bullish claim, data-quality flags, unresolved disagreements", color: "#f26d6d" },
    { name: "Editor", role: "Confidence scoring, source enforcement, executive summary, memo assembly", color: "#34d399" },
  ];

  // ------------------------------------------------------------------ formatting helpers
  function esc(s) {
    if (s === null || s === undefined) return "";
    return String(s)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function fmtMoney(v, cur = "USD", digits = 1) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "n/a";
    const n = Number(v);
    const neg = n < 0 ? "−" : "";
    const a = Math.abs(n);
    const sym = { USD: "$", EUR: "€", GBP: "£", JPY: "¥", CAD: "C$" }[cur] || cur + " ";
    if (a >= 1e12) return `${neg}${sym}${(a / 1e12).toFixed(digits)}T`;
    if (a >= 1e9) return `${neg}${sym}${(a / 1e9).toFixed(digits)}B`;
    if (a >= 1e6) return `${neg}${sym}${(a / 1e6).toFixed(digits)}M`;
    if (a >= 1e3) return `${neg}${sym}${(a / 1e3).toFixed(digits)}K`;
    return `${neg}${sym}${a.toFixed(digits)}`;
  }

  function fmtPct(v, digits = 1, signed = false) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "n/a";
    const n = Number(v) * 100;
    const sign = n < 0 ? "−" : signed && n > 0 ? "+" : "";
    return `${sign}${Math.abs(n).toFixed(digits)}%`;
  }

  function fmtNum(v, digits = 2) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "n/a";
    const n = Number(v);
    return (n < 0 ? "−" : "") + Math.abs(n).toLocaleString("en-US", { minimumFractionDigits: digits, maximumFractionDigits: digits });
  }

  function fmtRatio(v, digits = 1, suffix = "x") {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "n/a";
    return `${fmtNum(v, digits)}${suffix}`;
  }

  function fmtCount(v) {
    if (v === null || v === undefined || Number.isNaN(Number(v))) return "n/a";
    const n = Number(v);
    const a = Math.abs(n);
    if (a >= 1e9) return `${(n / 1e9).toFixed(2)}B`;
    if (a >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
    if (a >= 1e3) return `${(n / 1e3).toFixed(1)}K`;
    return Math.round(n).toLocaleString("en-US");
  }

  function fmtTime(epoch) {
    if (!epoch) return "—";
    try {
      return new Date(epoch * 1000).toISOString().replace("T", " ").slice(0, 19) + " UTC";
    } catch (_) {
      return "—";
    }
  }

  function showAlert(msg) {
    const el = document.getElementById("global-alert");
    if (!el) return;
    if (!msg) {
      el.style.display = "none";
      el.textContent = "";
      return;
    }
    el.innerHTML = `<span>${esc(msg)}</span><button type="button" class="btn btn-secondary btn-sm" id="dismiss-alert-btn">Dismiss</button>`;
    el.style.display = "flex";
    const btn = document.getElementById("dismiss-alert-btn");
    if (btn) btn.onclick = () => showAlert("");
  }

  // ------------------------------------------------------------------ navigation
  function switchView(view) {
    state.currentView = view;
    document.querySelectorAll(".nav-btn").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === view);
    });
    document.querySelectorAll(".view-section").forEach((sec) => {
      sec.classList.toggle("active", sec.id === `view-${view}`);
    });
    const mainArea = document.querySelector(".main-area");
    if (mainArea) mainArea.scrollTop = 0;
    if (view === "financials" && state.run) {
      renderFinancialsCharts();
    } else if (view === "forensics" && state.run) {
      renderForensicsChart();
    } else if (view === "legal" && state.run) {
      renderLegalChart();
    } else if (view === "history") {
      loadHistory();
    }
  }

  // ------------------------------------------------------------------ API calls
  async function apiGet(path) {
    const r = await fetch(path);
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const j = await r.json();
        if (j.detail) detail = j.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return r.json();
  }

  async function apiPost(path, body) {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body || {}),
    });
    if (!r.ok) {
      let detail = `HTTP ${r.status}`;
      try {
        const j = await r.json();
        if (j.detail) detail = j.detail;
      } catch (_) {}
      throw new Error(detail);
    }
    return r.json();
  }

  async function updateBudgetEstimate() {
    const depth = document.getElementById("depth-select").value;
    const provider = document.getElementById("provider-select").value;
    const pill = document.getElementById("budget-pill");
    try {
      const est = await apiGet(`/api/estimate?depth=${encodeURIComponent(depth)}&provider=${encodeURIComponent(provider)}`);
      if (provider === "offline" || est.calls === 0) {
        pill.textContent = "0 tokens · deterministic";
        pill.title = est.note || "Offline mode uses deterministic templates and 0 LLM tokens.";
      } else {
        pill.textContent = `~${est.calls} LLM calls · ~${(est.tokens_in + est.tokens_out).toLocaleString()} tok`;
        pill.title = est.note || "";
      }
    } catch (_) {
      pill.textContent = provider === "offline" ? "0 tokens · deterministic" : "estimated budget";
    }
  }

  // ------------------------------------------------------------------ Run execution & SSE
  async function startRun() {
    showAlert("");
    const ticker = document.getElementById("ticker-input").value.trim().toUpperCase();
    if (!ticker) {
      showAlert("Please enter a ticker symbol or CIK.");
      return;
    }
    const depth = document.getElementById("depth-select").value;
    const provider = document.getElementById("provider-select").value;
    const gateChecked = document.getElementById("gate-checkbox").checked;
    const runBtn = document.getElementById("run-btn");
    runBtn.disabled = true;

    try {
      const summary = await apiPost("/api/runs", {
        ticker,
        depth,
        provider,
        auto_approve: !gateChecked,
      });
      state.runId = summary.id;
      state.events = [];
      switchView("live");
      await fetchRunDetails(summary.id);
      connectSSE(summary.id);
    } catch (err) {
      showAlert(`Could not start run: ${err.message}`);
    } finally {
      runBtn.disabled = false;
    }
  }

  function closeSSE() {
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
    const ind = document.getElementById("sse-indicator");
    if (ind) {
      ind.className = "badge badge-grey";
      ind.textContent = "SSE Closed";
    }
  }

  function connectSSE(runId) {
    closeSSE();
    const ind = document.getElementById("sse-indicator");
    if (ind) {
      ind.className = "badge badge-amber";
      ind.textContent = "SSE Streaming";
    }

    const es = new EventSource(`/api/runs/${encodeURIComponent(runId)}/events`);
    state.eventSource = es;

    const handleEvent = (e) => {
      try {
        const ev = JSON.parse(e.data);
        if (ev && ev.seq) {
          if (!state.events.some((x) => x.seq === ev.seq)) {
            state.events.push(ev);
          }
          onStreamEvent(ev);
        }
      } catch (_) {}
    };

    ["run.status", "agent.status", "agent.step", "blackboard", "approval", "approval.decision", "error", "memo"].forEach((t) => {
      es.addEventListener(t, handleEvent);
    });

    es.addEventListener("run.closed", async () => {
      closeSSE();
      await fetchRunDetails(runId);
      await loadHistory();
    });

    es.onerror = async () => {
      closeSSE();
      await fetchRunDetails(runId);
    };
  }

  async function onStreamEvent(ev) {
    renderAgentCards();
    if (ev.type === "approval") {
      await fetchRunDetails(state.runId);
    } else if (ev.type === "blackboard" || ev.type === "agent.status" || ev.type === "run.status" || ev.type === "memo") {
      await fetchRunDetails(state.runId);
    }
  }

  async function fetchRunDetails(runId) {
    if (!runId) return;
    try {
      const data = await apiGet(`/api/runs/${encodeURIComponent(runId)}`);
      state.runId = data.id;
      state.run = data;
      updateTopStatus(data);
      updateApprovalBanner(data);
      renderLiveRoom();
      renderFinancials();
      renderForensics();
      renderLegal();
      renderSources();
      if (data.status === "done") {
        await loadMemoHtml(data.id);
      } else {
        renderPartialMemo(data);
      }
      updateNavBadges(data);
    } catch (err) {
      showAlert(`Error loading run ${runId}: ${err.message}`);
    }
  }

  async function loadRunFull(runId, goToView = null) {
    closeSSE();
    state.runId = runId;
    state.events = [];
    try {
      const evResp = await apiGet(`/api/runs/${encodeURIComponent(runId)}/events.json`);
      state.events = evResp.events || [];
    } catch (_) {
      state.events = [];
    }
    await fetchRunDetails(runId);
    if (state.run && (state.run.status === "running" || state.run.status === "awaiting_approval" || state.run.status === "queued")) {
      connectSSE(runId);
    }
    if (goToView) {
      switchView(goToView);
    }
  }

  // ------------------------------------------------------------------ Top bar & Approval Gate
  function updateTopStatus(run) {
    const dot = document.getElementById("run-status-dot");
    const txt = document.getElementById("run-status-text");
    if (!run) {
      dot.className = "status-dot";
      txt.textContent = "Ready";
      return;
    }
    dot.className = `status-dot ${run.status || "idle"}`;
    const labelMap = {
      queued: "Queued",
      running: "Running",
      awaiting_approval: "Awaiting Approval",
      done: "Done",
      failed: "Failed",
      rejected: "Rejected",
    };
    txt.textContent = `${run.ticker} · ${labelMap[run.status] || run.status} (${run.depth})`;
  }

  function updateApprovalBanner(run) {
    const banner = document.getElementById("approval-banner");
    const details = document.getElementById("approval-details");
    if (!run || (run.status !== "awaiting_approval" && !run.approval_pending)) {
      banner.style.display = "none";
      return;
    }
    const bb = run.blackboard || {};
    const secsDone = Object.values(bb.sections || {}).filter((s) => s.status === "done").length;
    const claims = (bb.claims || []).length;
    const counters = (bb.counters || []).length;
    const gaps = (bb.gaps || []).length;
    details.textContent = `${run.name} (${run.ticker}): ${secsDone} sections ready, ${claims} claims, ${counters} Skeptic counters, ${gaps} logged gaps. Approve to let the Editor score confidence and assemble the final memo.`;
    banner.style.display = "flex";
  }

  async function decideApproval(approved) {
    if (!state.runId) return;
    const note = document.getElementById("approval-note").value.trim();
    try {
      await apiPost(`/api/runs/${encodeURIComponent(state.runId)}/approve`, { approved, note });
      document.getElementById("approval-banner").style.display = "none";
      await fetchRunDetails(state.runId);
    } catch (err) {
      showAlert(`Approval failed: ${err.message}`);
    }
  }

  function updateNavBadges(run) {
    if (!run || !run.blackboard) return;
    const bb = run.blackboard;
    const stats = run.stats || (bb.meta && bb.meta.stats) || {};
    document.getElementById("badge-memo").textContent = stats.citations ? `${stats.citations} cites` : run.ticker;
    const secsDone = Object.values(bb.sections || {}).filter((s) => s.status === "done").length;
    document.getElementById("badge-live").textContent = `${secsDone}/15`;
    const finSec = (bb.sections || {}).financials;
    const yrs = finSec && finSec.data && finSec.data.years ? finSec.data.years.length : 0;
    document.getElementById("badge-financials").textContent = yrs ? `${yrs}y` : "—";
    const fzSec = (bb.sections || {}).forensics;
    const meds = fzSec && fzSec.data && fzSec.data.metrics ? fzSec.data.metrics : [];
    const reds = meds.filter((m) => m.light === "red").length;
    document.getElementById("badge-forensics").textContent = reds > 0 ? `${reds} red` : `${meds.length} ok`;
    const legClaims = (bb.counters || []).length;
    document.getElementById("badge-legal").textContent = `${legClaims} ctrs`;
    const srcCount = Object.keys(bb.sources || {}).length;
    document.getElementById("badge-sources").textContent = String(srcCount);
  }

  // ------------------------------------------------------------------ 1. MEMO VIEW
  async function loadMemoHtml(runId) {
    const container = document.getElementById("memo-container");
    try {
      const r = await fetch(`/api/runs/${encodeURIComponent(runId)}/memo.html`);
      if (!r.ok) return;
      const html = await r.text();
      container.innerHTML = html;
      wireCitationLinks(container);

      const run = state.run;
      const stats = (run && run.stats) || (run && run.blackboard && run.blackboard.meta && run.blackboard.meta.stats) || {};
      const unsCount = run && typeof run.unsourced === "number"
        ? run.unsourced
        : (run && run.blackboard && run.blackboard.meta && (run.blackboard.meta.unsourced || []).length) || 0;

      document.getElementById("memo-header-title").textContent = `${run.name} (${run.ticker}) — Investment Memo`;
      document.getElementById("memo-header-sub").textContent =
        `CIK ${run.cik} · depth: ${run.depth} · mode: ${run.provider} · ${stats.words || 0} words · ${stats.citations || 0} linked citations · ~${stats.pages_est || 0} pages`;

      const auditBadge = document.getElementById("audit-badge");
      if (unsCount === 0) {
        auditBadge.className = "badge badge-green";
        auditBadge.textContent = `✓ 0 unsourced figures (${stats.citations || 0} cited)`;
      } else {
        auditBadge.className = "badge badge-amber";
        auditBadge.textContent = `${unsCount} unsourced figure(s)`;
      }

      document.getElementById("dl-md-btn").href = `/api/runs/${encodeURIComponent(runId)}/memo.md`;
      document.getElementById("dl-pdf-btn").href = `/api/runs/${encodeURIComponent(runId)}/memo.pdf`;
      document.getElementById("open-standalone-btn").href = `/api/runs/${encodeURIComponent(runId)}/memo.html?standalone=1`;

      renderMemoToc(container, run.blackboard);
    } catch (_) {}
  }

  function renderPartialMemo(run) {
    const container = document.getElementById("memo-container");
    if (!run || !run.blackboard) return;
    const secs = Object.values(run.blackboard.sections || {}).filter((s) => s.narrative);
    if (!secs.length) {
      container.innerHTML = `<div class="empty-state">Run <strong>${esc(run.ticker)}</strong> is ${esc(run.status)}… Switch to <strong>Live Room</strong> to watch the 5 agents populate the blackboard.</div>`;
      return;
    }
    container.innerHTML = `<div class="banner banner-warn" style="margin-bottom:16px;">Run is currently <strong>${esc(run.status)}</strong>. Showing ${secs.length} completed section(s) so far.</div>` +
      secs.map((s) => `<section style="margin-bottom:24px;"><h2>${esc(s.title)} <small style="color:var(--muted);font-size:12px;">(${esc(s.owner)})</small></h2><div style="white-space:pre-wrap;font-size:13px;">${esc(s.narrative)}</div></section>`).join("");
  }

  function renderMemoToc(container, bb) {
    const tocList = document.getElementById("memo-toc-list");
    if (!tocList || !bb) return;
    const headings = Array.from(container.querySelectorAll("h2"));
    const secList = Object.values(bb.sections || {});
    tocList.innerHTML = headings
      .map((h, idx) => {
        if (!h.id) h.id = `memo-sec-${idx}`;
        const text = h.textContent.replace(/^\d+\.\s*/, "");
        const matchSec = secList.find((s) => text.toLowerCase().includes(s.title.toLowerCase()));
        let badge = "";
        if (matchSec && matchSec.confidence !== null && matchSec.confidence !== undefined) {
          const pct = Math.round(matchSec.confidence * 100);
          const cls = pct >= 75 ? "badge-green" : pct >= 45 ? "badge-amber" : "badge-red";
          badge = `<span class="badge ${cls}">${pct}%</span>`;
        }
        return `<a href="#${esc(h.id)}" class="memo-toc-link" data-target="${esc(h.id)}">
          <span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(h.textContent)}</span>
          ${badge}
        </a>`;
      })
      .join("");

    tocList.querySelectorAll(".memo-toc-link").forEach((a) => {
      a.onclick = (e) => {
        e.preventDefault();
        const target = document.getElementById(a.dataset.target);
        if (target) target.scrollIntoView({ behavior: "smooth", block: "start" });
      };
    });
  }

  function wireCitationLinks(container) {
    const inspector = document.getElementById("cite-inspector");
    const sources = (state.run && state.run.blackboard && state.run.blackboard.sources) || {};
    const byUrlTitle = {};
    Object.values(sources).forEach((s) => {
      byUrlTitle[`${s.url}||${s.label}`] = s;
      byUrlTitle[s.label] = s;
    });

    container.querySelectorAll("a.cite").forEach((a) => {
      a.addEventListener("mouseenter", () => {
        const title = a.getAttribute("title") || "";
        const href = a.getAttribute("href") || "";
        const src = byUrlTitle[`${href}||${title}`] || byUrlTitle[title];
        if (!inspector) return;
        if (src) {
          inspector.innerHTML = `
            <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
              <strong>[${esc(src.id)}] ${esc(src.kind.toUpperCase())}</strong>
              <span style="color:var(--muted);font-family:var(--font-mono);">${esc(src.accn || src.period || "")}</span>
            </div>
            <div style="color:#e2e8f0;margin-bottom:4px;">${esc(src.label)}</div>
            ${src.concept ? `<div style="font-family:var(--font-mono);font-size:11px;color:#38bdf8;">Concept: ${esc(src.concept)}</div>` : ""}
            ${src.inputs && src.inputs.length ? `<div style="font-size:11px;color:var(--muted);">Inputs: ${esc(src.inputs.join(", "))}</div>` : ""}
            <div style="font-size:11px;color:var(--muted);margin-top:3px;">Click figure to open ${href.startsWith("/methodology") ? "methodology formula" : "SEC EDGAR filing"} ↗</div>
          `;
        } else {
          inspector.innerHTML = `<div><strong>Citation:</strong> ${esc(title || href)}</div>`;
        }
        inspector.classList.add("visible");
      });
      a.addEventListener("mouseleave", () => {
        if (inspector) inspector.classList.remove("visible");
      });
    });
  }

  // ------------------------------------------------------------------ 2. LIVE ROOM VIEW
  function renderLiveRoom() {
    renderAgentCards();
    const run = state.run;
    if (!run || !run.blackboard) return;
    const bb = run.blackboard;
    const secs = Object.values(bb.sections || {});
    const doneCount = secs.filter((s) => s.status === "done").length;
    const unsCount = typeof run.unsourced === "number" ? run.unsourced : ((bb.meta && bb.meta.unsourced) || []).length;

    document.getElementById("live-kpi-ticker").textContent = run.ticker || "—";
    document.getElementById("live-kpi-name").textContent = `${run.name || ""} (CIK ${run.cik || ""})`;
    document.getElementById("live-kpi-sections").textContent = `${doneCount} / ${secs.length || 15}`;
    document.getElementById("live-kpi-depth").textContent = `${run.depth} depth · ${run.provider} mode`;
    document.getElementById("live-kpi-sources").textContent = String(Object.keys(bb.sources || {}).length);
    document.getElementById("live-kpi-claims").textContent = `${(bb.claims || []).length} / ${(bb.counters || []).length}`;
    const unres = (bb.disagreements || []).filter((d) => !d.resolved).length;
    document.getElementById("live-kpi-gaps").textContent = `${unres} / ${(bb.gaps || []).length}`;
    document.getElementById("live-kpi-audit").textContent = unsCount === 0 ? "100% Cited" : `${unsCount} Uncited`;
    document.getElementById("live-kpi-audit-sub").textContent = `${(run.stats && run.stats.citations) || 0} inline source links`;

    const grid = document.getElementById("blackboard-grid");
    grid.innerHTML = secs
      .map((s) => {
        const conf = s.confidence !== null && s.confidence !== undefined ? Math.round(s.confidence * 100) : null;
        const comp = s.completeness !== null && s.completeness !== undefined ? Math.round(s.completeness * 100) : 0;
        const barPct = conf !== null ? conf : comp;
        const barColor = conf === null ? "#4f9cf9" : conf >= 75 ? "#22c55e" : conf >= 45 ? "#f59e0b" : "#ef4444";
        return `<div class="bb-cell" data-sec-key="${esc(s.key)}">
          <div class="bb-cell-top">
            <span class="bb-cell-title">${esc(s.title)}</span>
            <span class="badge badge-${esc(s.status)}">${esc(s.status)}</span>
          </div>
          <div class="bb-cell-owner">${esc(s.owner)} · ${conf !== null ? `confidence ${conf}%` : `completeness ${comp}%`}</div>
          <div class="conf-meter"><div class="conf-meter-fill" style="width:${barPct}%;background:${barColor};"></div></div>
          ${s.confidence_notes && s.confidence_notes.length ? `<div style="font-size:11px;color:var(--muted);">${esc(s.confidence_notes.join("; "))}</div>` : ""}
        </div>`;
      })
      .join("");

    grid.querySelectorAll(".bb-cell").forEach((cell) => {
      cell.onclick = () => {
        switchView("memo");
      };
    });
  }

  function renderAgentCards() {
    const catalog = (state.config && state.config.agents) || DEFAULT_AGENTS;
    const grid = document.getElementById("agents-grid");
    if (!grid) return;

    const runAgents = (state.run && state.run.agents) || {};
    const stepsByAgent = {};
    const statusFromEvents = {};
    catalog.forEach((a) => {
      stepsByAgent[a.name] = [];
    });

    (state.events || []).forEach((ev) => {
      if (ev.agent && stepsByAgent[ev.agent]) {
        if (ev.type === "agent.step") {
          stepsByAgent[ev.agent].push(ev);
        } else if (ev.type === "agent.status" && ev.data && ev.data.status) {
          statusFromEvents[ev.agent] = ev.data.status;
        }
      }
    });

    grid.innerHTML = catalog
      .map((a) => {
        let st = runAgents[a.name] || statusFromEvents[a.name] || (state.run && state.run.status === "done" ? "done" : "idle");
        const steps = stepsByAgent[a.name] || [];
        const linesHtml = steps.length
          ? steps
              .map((ev) => {
                const stCls = (ev.data && ev.data.status) || "info";
                return `<div class="step-line ${esc(stCls)}">${esc(ev.message)}</div>`;
              })
              .join("")
          : `<div style="color:var(--subtle);padding:8px 4px;">${state.run && state.run.status === "done" ? "✓ Completed all assigned blackboard sections." : "Waiting for turn…"}</div>`;

        return `<div class="agent-card ${esc(st)}" style="--agent-color:${esc(a.color)};">
          <div class="agent-head">
            <div class="agent-head-top">
              <span class="agent-name">${esc(a.name)}</span>
              <span class="badge badge-${esc(st)}">${esc(st)}</span>
            </div>
            <div class="agent-role">${esc(a.role)}</div>
          </div>
          <div class="agent-log">${linesHtml}</div>
        </div>`;
      })
      .join("");

    grid.querySelectorAll(".agent-log").forEach((el) => {
      el.scrollTop = el.scrollHeight;
    });
  }

  // ------------------------------------------------------------------ 3. FINANCIALS VIEW
  function renderFinancials() {
    const run = state.run;
    if (!run || !run.blackboard) return;
    const bb = run.blackboard;
    const fin = ((bb.sections || {}).financials || {}).data || {};
    const val = ((bb.sections || {}).valuation || {}).data || {};
    const peersData = ((bb.sections || {}).peers || {}).data || {};
    const cur = fin.currency || "USD";
    const summ = fin.summary || {};

    // Valuation KPIs
    const kpiWrap = document.getElementById("valuation-kpis");
    const kpis = [
      { label: "Market Cap", value: fmtMoney(val.market_cap, cur), sub: val.price ? `Share price ${fmtNum(val.price, 2)}` : "No price feed" },
      { label: "Enterprise Value", value: fmtMoney(val.enterprise_value, cur), sub: `Net debt ${fmtMoney(val.net_debt, cur)}` },
      { label: "Trailing P/E", value: fmtRatio(val.pe, 1), sub: `P/S ${fmtRatio(val.ps, 2)}` },
      { label: "EV / EBITDA", value: val.ev_ebitda !== null && val.ev_ebitda !== undefined ? fmtRatio(val.ev_ebitda, 1) : fmtRatio(val.ev_ebit, 1) + " (EBIT)", sub: `EBITDA ${fmtMoney(val.ebitda, cur)}` },
      { label: "FCF Yield", value: fmtPct(val.fcf_yield, 1), sub: `Revenue CAGR ${fmtPct(summ.revenue_cagr, 1)}` },
      { label: "Operating Margin Chg", value: fmtPct(summ.operating_margin_change, 1, true), sub: `Over ${summ.years || 0} fiscal years` },
    ];
    kpiWrap.innerHTML = kpis
      .map(
        (k) => `<div class="kpi-card">
          <div class="kpi-label">${esc(k.label)}</div>
          <div class="kpi-value">${esc(k.value)}</div>
          <div class="kpi-sub">${esc(k.sub)}</div>
        </div>`
      )
      .join("");

    // Multi-year Financial Table
    const rows = fin.rows || [];
    const years = fin.years || [];
    const finWrap = document.getElementById("fin-table-wrap");
    if (!rows.length) {
      finWrap.innerHTML = `<div class="empty-state">No annual XBRL financial data available for this filer.</div>`;
    } else {
      const metrics = [
        { label: "Revenue", key: "revenue", fn: (v) => fmtMoney(v, cur) },
        { label: "Gross Profit", key: "gross_profit", fn: (v) => fmtMoney(v, cur) },
        { label: "Gross Margin", key: "gross_margin", fn: (v) => fmtPct(v, 1) },
        { label: "Operating Income", key: "operating_income", fn: (v) => fmtMoney(v, cur) },
        { label: "Operating Margin", key: "operating_margin", fn: (v) => fmtPct(v, 1) },
        { label: "Net Income", key: "net_income", fn: (v) => fmtMoney(v, cur) },
        { label: "Net Margin", key: "net_margin", fn: (v) => fmtPct(v, 1) },
        { label: "Cash From Operations (CFO)", key: "cfo", fn: (v) => fmtMoney(v, cur) },
        { label: "Capital Expenditure", key: "capex", fn: (v) => fmtMoney(v, cur) },
        { label: "Free Cash Flow (FCF)", key: "fcf", fn: (v) => fmtMoney(v, cur) },
        { label: "FCF Margin", key: "fcf_margin", fn: (v) => fmtPct(v, 1) },
        { label: "ROIC (approx.)", key: "roic", fn: (v) => fmtPct(v, 1) },
        { label: "Total Assets", key: "total_assets", fn: (v) => fmtMoney(v, cur) },
        { label: "Total Debt", key: "total_debt", fn: (v) => fmtMoney(v, cur) },
        { label: "Cash & Short-Term Inv.", key: "cash_and_investments", fn: (v) => fmtMoney(v, cur) },
        { label: "Net Debt (Cash < 0)", key: "net_debt", fn: (v) => fmtMoney(v, cur) },
        { label: "Shareholders' Equity", key: "equity", fn: (v) => fmtMoney(v, cur) },
        { label: "Diluted Shares (Wtd)", key: "shares_diluted", fn: (v) => fmtCount(v) },
        { label: "Diluted EPS", key: "eps_diluted", fn: (v) => fmtNum(v, 2) },
        { label: "Stock-Based Comp", key: "sbc", fn: (v) => fmtMoney(v, cur) },
        { label: "Share Repurchases", key: "buybacks", fn: (v) => fmtMoney(v, cur) },
        { label: "Dividends Paid", key: "dividends", fn: (v) => fmtMoney(v, cur) },
      ];
      const byYear = {};
      rows.forEach((r) => {
        byYear[r.year] = r;
      });
      finWrap.innerHTML = `<table class="data-table">
        <thead>
          <tr><th>Line Item (${esc(cur)})</th>${years.map((y) => `<th class="num-col">FY${esc(y)}</th>`).join("")}</tr>
        </thead>
        <tbody>
          ${metrics
            .map(
              (m) => `<tr>
                <td><strong>${esc(m.label)}</strong></td>
                ${years.map((y) => `<td class="num-col">${esc(m.fn(byYear[y] ? byYear[y][m.key] : null))}</td>`).join("")}
              </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    }

    // Peers Table
    const peersWrap = document.getElementById("peers-table-wrap");
    const peers = peersData.peers || [];
    const cy = peersData.cy || "";
    document.getElementById("peers-table-sub").textContent = peersData.sic
      ? `SIC ${peersData.sic} peers in XBRL frame CY${cy}`
      : "Same-SIC peer comparison";
    if (!peers.length) {
      peersWrap.innerHTML = `<div class="empty-state">${peersData.skipped ? "Peer comparison skipped at quick depth." : "No same-SIC peers with XBRL frame data found."}</div>`;
    } else {
      peersWrap.innerHTML = `<table class="data-table">
        <thead>
          <tr>
            <th>Company</th>
            <th>Ticker</th>
            <th>CIK</th>
            <th class="num-col">Revenue (CY${esc(cy)})</th>
            <th class="num-col">Net Income (CY${esc(cy)})</th>
            <th class="num-col">Net Margin</th>
          </tr>
        </thead>
        <tbody>
          ${peers
            .map(
              (p) => `<tr>
                <td><strong>${esc(p.name)}</strong></td>
                <td style="font-family:var(--font-mono);color:var(--accent);">${esc(p.ticker || "—")}</td>
                <td style="font-family:var(--font-mono);color:var(--muted);">${esc(p.cik)}</td>
                <td class="num-col">${esc(p.revenue ? fmtMoney(p.revenue.val, "USD") : "n/a")}</td>
                <td class="num-col">${esc(p.net_income ? fmtMoney(p.net_income.val, "USD") : "n/a")}</td>
                <td class="num-col">${esc(p.net_margin !== null && p.net_margin !== undefined ? fmtPct(p.net_margin, 1) : "n/a")}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    }

    if (state.currentView === "financials") {
      renderFinancialsCharts();
    }
  }

  function destroyChart(key) {
    if (state.charts[key]) {
      state.charts[key].destroy();
      delete state.charts[key];
    }
  }

  function renderFinancialsCharts() {
    if (typeof window.Chart === "undefined" || !state.run || !state.run.blackboard) return;
    const bb = state.run.blackboard;
    const fin = ((bb.sections || {}).financials || {}).data || {};
    const val = ((bb.sections || {}).valuation || {}).data || {};
    const rows = fin.rows || [];
    const labels = rows.map((r) => `FY${r.year}`);

    destroyChart("revFcf");
    const c1 = document.getElementById("chart-rev-fcf");
    if (c1 && rows.length) {
      const scale = Math.max(...rows.map((r) => Math.abs(r.revenue || 0))) >= 1e9 ? 1e9 : 1e6;
      const unitLbl = scale === 1e9 ? "$B" : "$M";
      state.charts.revFcf = new window.Chart(c1.getContext("2d"), {
        type: "bar",
        data: {
          labels,
          datasets: [
            { label: `Revenue (${unitLbl})`, data: rows.map((r) => (r.revenue !== null ? r.revenue / scale : null)), backgroundColor: "rgba(79, 156, 249, 0.75)" },
            { label: `Operating Income (${unitLbl})`, data: rows.map((r) => (r.operating_income !== null ? r.operating_income / scale : null)), backgroundColor: "rgba(167, 139, 250, 0.75)" },
            { label: `Net Income (${unitLbl})`, data: rows.map((r) => (r.net_income !== null ? r.net_income / scale : null)), backgroundColor: "rgba(52, 211, 153, 0.75)" },
            { label: `Free Cash Flow (${unitLbl})`, data: rows.map((r) => (r.fcf !== null ? r.fcf / scale : null)), backgroundColor: "rgba(245, 165, 36, 0.8)" },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#cbd5e1", font: { size: 11 } } } },
          scales: {
            x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.5)" } },
            y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.5)" } },
          },
        },
      });
    }

    destroyChart("margins");
    const c2 = document.getElementById("chart-margins");
    if (c2 && rows.length) {
      state.charts.margins = new window.Chart(c2.getContext("2d"), {
        type: "line",
        data: {
          labels,
          datasets: [
            { label: "Gross Margin %", data: rows.map((r) => (r.gross_margin !== null ? +(r.gross_margin * 100).toFixed(1) : null)), borderColor: "#4f9cf9", tension: 0.25 },
            { label: "Operating Margin %", data: rows.map((r) => (r.operating_margin !== null ? +(r.operating_margin * 100).toFixed(1) : null)), borderColor: "#a78bfa", tension: 0.25 },
            { label: "Net Margin %", data: rows.map((r) => (r.net_margin !== null ? +(r.net_margin * 100).toFixed(1) : null)), borderColor: "#34d399", tension: 0.25 },
            { label: "FCF Margin %", data: rows.map((r) => (r.fcf_margin !== null ? +(r.fcf_margin * 100).toFixed(1) : null)), borderColor: "#f5a524", tension: 0.25 },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#cbd5e1", font: { size: 11 } } } },
          scales: {
            x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.5)" } },
            y: { ticks: { color: "#94a3b8", callback: (v) => `${v}%` }, grid: { color: "rgba(36,49,70,0.5)" } },
          },
        },
      });
    }

    destroyChart("prices");
    const pts = val.price_points || [];
    const priceCard = document.getElementById("price-chart-card");
    const c3 = document.getElementById("chart-prices");
    if (c3 && pts.length) {
      priceCard.style.display = "block";
      state.charts.prices = new window.Chart(c3.getContext("2d"), {
        type: "line",
        data: {
          labels: pts.map((p) => p[0]),
          datasets: [
            {
              label: `${state.run.ticker} Close`,
              data: pts.map((p) => p[1]),
              borderColor: "#38bdf8",
              backgroundColor: "rgba(56, 189, 248, 0.12)",
              fill: true,
              pointRadius: 0,
              borderWidth: 2,
            },
          ],
        },
        options: {
          responsive: true,
          maintainAspectRatio: false,
          plugins: { legend: { labels: { color: "#cbd5e1", font: { size: 11 } } } },
          scales: {
            x: { ticks: { color: "#94a3b8", maxTicksLimit: 8 }, grid: { color: "rgba(36,49,70,0.4)" } },
            y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.4)" } },
          },
        },
      });
    } else if (priceCard) {
      priceCard.style.display = "none";
    }
  }

  // ------------------------------------------------------------------ 4. FORENSICS VIEW
  function formatMetricValue(m) {
    if (m.value === null || m.value === undefined) return "n/a";
    if (m.unit === "pct") return fmtPct(m.value, 1);
    if (m.unit === "days") return `${Math.round(m.value)} days`;
    if (m.unit === "x") return fmtRatio(m.value, 2);
    return fmtNum(m.value, 2);
  }

  function renderForensics() {
    const run = state.run;
    if (!run || !run.blackboard) return;
    const bb = run.blackboard;
    const fz = ((bb.sections || {}).forensics || {}).data || {};
    const ae = ((bb.sections || {}).audit_events || {}).data || {};
    const mets = fz.metrics || [];

    const grid = document.getElementById("forensic-cards-grid");
    if (!mets.length) {
      grid.innerHTML = `<div class="empty-state">No forensic metrics computed for this run.</div>`;
    } else {
      grid.innerHTML = mets
        .map((m) => {
          const comps = m.components && Object.keys(m.components).length
            ? `<div style="display:flex;flex-wrap:wrap;gap:6px;margin-top:4px;">` +
              Object.entries(m.components)
                .map(([k, v]) => `<span class="badge badge-grey">${esc(k)}: ${v !== null && v !== undefined ? esc(fmtNum(v, 3)) : "n/a"}</span>`)
                .join("") +
              `</div>`
            : "";
          const inputsList = Object.entries(m.inputs || {})
            .map(([k, v]) => `<tr><td style="font-family:var(--font-mono);">${esc(k)}</td><td class="num-col">${v !== null && v !== undefined ? esc(fmtNum(v, 0)) : "missing"}</td></tr>`)
            .join("");

          return `<div class="forensic-card light-${esc(m.light || "grey")}">
            <div class="forensic-top">
              <div>
                <div style="font-weight:700;font-size:14px;color:#f8fafc;">${esc(m.label)}</div>
                <div class="forensic-meta">${esc(m.citation || "")}</div>
              </div>
              <div style="text-align:right;">
                <div class="forensic-val">${esc(formatMetricValue(m))}</div>
                <span class="badge badge-${esc(m.light || "grey")}">${esc(m.light || "grey")}</span>
              </div>
            </div>
            <div class="forensic-formula">${esc(m.formula)}</div>
            <div class="forensic-meta"><strong>Thresholds:</strong> ${esc(m.thresholds)}</div>
            ${m.note ? `<div class="forensic-meta" style="color:#fcd34d;"><strong>Note:</strong> ${esc(m.note)}</div>` : ""}
            ${comps}
            <details style="margin-top:4px;font-size:12px;color:var(--muted);">
              <summary style="cursor:pointer;color:var(--accent);">Inspect raw inputs (${Object.keys(m.inputs || {}).length})</summary>
              <table class="data-table" style="margin-top:6px;">
                <thead><tr><th>Input</th><th class="num-col">Raw Value</th></tr></thead>
                <tbody>${inputsList}</tbody>
              </table>
            </details>
          </div>`;
        })
        .join("");
    }

    // Audit Events box
    const aeBox = document.getElementById("audit-events-box");
    const audChanges = ae.auditor_changes || [];
    const restats = ae.restatements || [];
    const amends = ae.amended_reports || [];
    const gc = ae.going_concern || {};
    aeBox.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:10px;font-size:13px;">
        <div class="kpi-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>Auditor Changes (8-K Item 4.01)</strong>
            <span class="badge ${audChanges.length ? "badge-amber" : "badge-green"}">${audChanges.length} filing(s)</span>
          </div>
          <div style="color:var(--muted);font-size:12px;margin-top:4px;">
            ${audChanges.length ? audChanges.map((x) => `${esc(x.filed)} (${esc(x.accn)})`).join(", ") : "None in recent EDGAR submissions index."}
          </div>
        </div>
        <div class="kpi-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>Restatements / Non-Reliance (8-K Item 4.02)</strong>
            <span class="badge ${restats.length ? "badge-red" : "badge-green"}">${restats.length} filing(s)</span>
          </div>
          <div style="color:var(--muted);font-size:12px;margin-top:4px;">
            ${restats.length ? restats.map((x) => `${esc(x.filed)} (${esc(x.accn)})`).join(", ") : "None in recent EDGAR submissions index."}
          </div>
        </div>
        <div class="kpi-card">
          <div style="display:flex;justify-content:space-between;align-items:center;">
            <strong>Going-Concern Evaluation (Latest 10-K)</strong>
            <span class="badge ${gc.substantial_doubt ? "badge-red" : "badge-green"}">${gc.substantial_doubt ? "Substantial Doubt Flagged" : "Clean"}</span>
          </div>
          <div style="color:var(--muted);font-size:12px;margin-top:4px;">
            Mentions in 10-K: ${gc.mentions || 0}. ${gc.snippets && gc.snippets[0] ? `<br><em>“${esc(gc.snippets[0].slice(0, 220))}…”</em>` : ""}
          </div>
        </div>
        ${amends.length ? `<div style="font-size:12px;color:var(--muted);">Amended periodic reports: ${amends.map((a) => `${esc(a.form)} (${esc(a.filed)})`).join(", ")}</div>` : ""}
      </div>
    `;

    if (state.currentView === "forensics") {
      renderForensicsChart();
    }
  }

  function renderForensicsChart() {
    if (typeof window.Chart === "undefined" || !state.run || !state.run.blackboard) return;
    destroyChart("beneish");
    const fz = (((state.run.blackboard || {}).sections || {}).forensics || {}).data || {};
    const ben = (fz.metrics || []).find((m) => m.key === "beneish");
    const canvas = document.getElementById("chart-beneish");
    if (!canvas || !ben || !ben.components) return;
    const keys = ["DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI", "TATA"];
    const vals = keys.map((k) => (ben.components[k] !== null && ben.components[k] !== undefined ? +Number(ben.components[k]).toFixed(3) : 0));

    state.charts.beneish = new window.Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: keys,
        datasets: [
          {
            label: `FY${ben.year || ""} Component Index`,
            data: vals,
            backgroundColor: vals.map((v, i) => (keys[i] !== "TATA" && v > 1.15 ? "rgba(239, 68, 68, 0.8)" : "rgba(79, 156, 249, 0.8)")),
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { labels: { color: "#cbd5e1", font: { size: 11 } } } },
        scales: {
          x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.4)" } },
          y: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.4)" } },
        },
      },
    });
  }

  // ------------------------------------------------------------------ 5. RISK & LEGAL VIEW
  function renderLegal() {
    const run = state.run;
    if (!run || !run.blackboard) return;
    const bb = run.blackboard;
    const rf = ((bb.sections || {}).risk_factors || {}).data || {};
    const lp = ((bb.sections || {}).legal_proceedings || {});
    const me = ((bb.sections || {}).material_events || {}).data || {};
    const ins = ((bb.sections || {}).insiders || {}).data || {};
    const comp = ((bb.sections || {}).compensation || {}).data || {};

    // Risk Diff + Legal Proceedings box
    const diff = rf.diff || {};
    const diffBox = document.getElementById("risk-diff-box");
    diffBox.innerHTML = `
      <div style="display:flex;flex-direction:column;gap:12px;">
        <div class="kpi-grid">
          <div class="kpi-card">
            <div class="kpi-label">New Risk Sentences</div>
            <div class="kpi-value">${diff.added_count !== undefined ? diff.added_count : "—"}</div>
            <div class="kpi-sub">Added vs prior 10-K</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Dropped Sentences</div>
            <div class="kpi-value">${diff.removed_count !== undefined ? diff.removed_count : "—"}</div>
            <div class="kpi-sub">Removed vs prior 10-K</div>
          </div>
          <div class="kpi-card">
            <div class="kpi-label">Item 1A Length Change</div>
            <div class="kpi-value">${diff.length_change_pct !== undefined && diff.length_change_pct !== null ? fmtPct(diff.length_change_pct, 1, true) : "—"}</div>
            <div class="kpi-sub">${rf.chars ? `${rf.chars.toLocaleString()} chars total` : ""}</div>
          </div>
        </div>
        ${
          diff.added_examples && diff.added_examples.length
            ? `<div style="font-size:12.5px;"><strong>Top novel sentences added in latest Item 1A:</strong>
               <ul style="margin:6px 0 0 18px;padding:0;color:#cbd5e1;display:flex;flex-direction:column;gap:6px;">
                 ${diff.added_examples.slice(0, 3).map((s) => `<li>${esc(s.slice(0, 260))}${s.length > 260 ? "…" : ""}</li>`).join("")}
               </ul></div>`
            : ""
        }
        <div style="border-top:1px solid var(--border);padding-top:10px;font-size:12.5px;">
          <strong>Item 3 Legal Proceedings Summary:</strong>
          <div style="color:var(--muted);margin-top:4px;max-height:110px;overflow-y:auto;">${esc((lp.narrative || "No Item 3 narrative.").replace(/\[([^\]]*)\]\[S\d+\]/g, "$1").slice(0, 500))}</div>
        </div>
      </div>
    `;

    // Skeptic Counters & Disagreements
    const claimsById = {};
    (bb.claims || []).forEach((c) => {
      claimsById[c.id] = c;
    });
    const counters = bb.counters || [];
    const skWrap = document.getElementById("skeptic-counters-wrap");
    if (!counters.length) {
      skWrap.innerHTML = `<div class="empty-state">No bullish claims / Skeptic counters recorded for this run.</div>`;
    } else {
      skWrap.innerHTML = `<table class="data-table">
        <thead>
          <tr>
            <th>Agent &amp; Bullish Claim</th>
            <th>Skeptic's Counter-Argument</th>
            <th>Evidence That Would Change the Skeptic's Mind</th>
            <th>Severity</th>
          </tr>
        </thead>
        <tbody>
          ${counters
            .map((ctr) => {
              const cl = claimsById[ctr.claim_id] || {};
              return `<tr>
                <td><strong>${esc(cl.agent || "Agent")}:</strong> ${esc(cl.text || ctr.claim_id)}</td>
                <td>${esc(ctr.counter)}</td>
                <td style="color:var(--muted);">${esc(ctr.evidence_to_change_mind)}</td>
                <td><span class="badge badge-${esc(ctr.severity)}">${esc(ctr.severity)}</span></td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
    }

    // 8-K Timeline
    const tl = me.timeline || [];
    const ekWrap = document.getElementById("eightk-table-wrap");
    if (!tl.length) {
      ekWrap.innerHTML = `<div class="empty-state">No Form 8-K filings in the last 24 months.</div>`;
    } else {
      ekWrap.innerHTML = `<table class="data-table">
        <thead>
          <tr><th>Filing Date</th><th>Accession</th><th>Items</th><th>Description</th><th>Material</th></tr>
        </thead>
        <tbody>
          ${tl
            .slice(0, 30)
            .map(
              (e) => `<tr>
                <td style="font-family:var(--font-mono);white-space:nowrap;">${esc(e.filed)}</td>
                <td style="font-family:var(--font-mono);color:var(--muted);">${esc(e.accn)}</td>
                <td style="font-family:var(--font-mono);">${esc((e.items || []).join(", ") || "—")}</td>
                <td>${esc((e.labels || []).join("; "))}</td>
                <td>${e.material ? `<span class="badge badge-amber">⚠ Material</span>` : `<span class="badge badge-grey">Routine</span>`}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    }

    // Insiders Table
    const parsedF4 = ins.parsed || [];
    const insWrap = document.getElementById("insiders-wrap");
    if (!parsedF4.length) {
      insWrap.innerHTML = `<div class="empty-state">No Form 4 XML transactions parsed (${ins.form4_count || 0} filings in 12m index).</div>`;
    } else {
      insWrap.innerHTML = `<table class="data-table">
        <thead>
          <tr><th>Filed</th><th>Reporting Owner</th><th>Role</th><th>Transactions</th></tr>
        </thead>
        <tbody>
          ${parsedF4
            .slice(0, 15)
            .map((p) => {
              const txDesc = (p.transactions || [])
                .slice(0, 3)
                .map((t) => `${t.code} ${fmtCount(t.shares)}${t.price ? " @ $" + fmtNum(t.price, 2) : ""}`)
                .join("; ");
              return `<tr>
                <td style="font-family:var(--font-mono);">${esc(p.filed)}</td>
                <td><strong>${esc(p.owner)}</strong></td>
                <td style="color:var(--muted);">${esc(p.relationship || "—")}</td>
                <td style="font-family:var(--font-mono);">${esc(txDesc || "—")}</td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;
    }

    // Executive Compensation Table
    const compRows = comp.rows || [];
    const compWrap = document.getElementById("comp-wrap");
    if (!compRows.length) {
      compWrap.innerHTML = `<div class="empty-state">Summary Compensation Table not available or not parseable from DEF 14A.</div>`;
    } else {
      compWrap.innerHTML = `<table class="data-table">
        <thead>
          <tr><th>Named Executive</th><th>Year</th><th class="num-col">Total Compensation</th></tr>
        </thead>
        <tbody>
          ${compRows
            .map(
              (r) => `<tr>
                <td><strong>${esc(r.name)}</strong></td>
                <td style="font-family:var(--font-mono);">${esc(r.year)}</td>
                <td class="num-col">${esc(fmtMoney(r.total, "USD"))}</td>
              </tr>`
            )
            .join("")}
        </tbody>
      </table>`;
    }

    if (state.currentView === "legal") {
      renderLegalChart();
    }
  }

  function renderLegalChart() {
    if (typeof window.Chart === "undefined" || !state.run || !state.run.blackboard) return;
    destroyChart("risks");
    const rf = (((state.run.blackboard || {}).sections || {}).risk_factors || {}).data || {};
    const clusters = rf.clusters || [];
    const canvas = document.getElementById("chart-risks");
    if (!canvas || !clusters.length) return;

    state.charts.risks = new window.Chart(canvas.getContext("2d"), {
      type: "bar",
      data: {
        labels: clusters.map((c) => c.cluster),
        datasets: [
          {
            label: "Sentences in Item 1A",
            data: clusters.map((c) => c.sentences),
            backgroundColor: "rgba(167, 139, 250, 0.78)",
          },
        ],
      },
      options: {
        indexAxis: "y",
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { ticks: { color: "#94a3b8" }, grid: { color: "rgba(36,49,70,0.4)" } },
          y: { ticks: { color: "#cbd5e1", font: { size: 11 } }, grid: { display: false } },
        },
      },
    });
  }

  // ------------------------------------------------------------------ 6. SOURCES VIEW
  function renderSources() {
    const wrap = document.getElementById("sources-table-wrap");
    if (!state.run || !state.run.blackboard) {
      wrap.innerHTML = `<div class="empty-state">No sources loaded.</div>`;
      return;
    }
    const sourcesObj = state.run.blackboard.sources || {};
    const q = (document.getElementById("source-filter-input").value || "").trim().toLowerCase();
    const kind = document.getElementById("source-kind-select").value;

    const list = Object.values(sourcesObj).filter((s) => {
      if (kind && s.kind !== kind) return false;
      if (!q) return true;
      const hay = `${s.id} ${s.kind} ${s.label} ${s.concept || ""} ${s.accn || ""} ${s.period || ""}`.toLowerCase();
      return hay.includes(q);
    });

    if (!list.length) {
      wrap.innerHTML = `<div class="empty-state">No sources match the current filter.</div>`;
      return;
    }

    wrap.innerHTML = `<table class="data-table">
      <thead>
        <tr>
          <th>ID</th>
          <th>Kind</th>
          <th>Reference &amp; Filing Link</th>
          <th>XBRL Concept / Inputs</th>
          <th>Period / Filed</th>
        </tr>
      </thead>
      <tbody>
        ${list
          .map(
            (s) => `<tr>
              <td style="font-family:var(--font-mono);font-weight:700;color:var(--accent);">${esc(s.id)}</td>
              <td><span class="badge badge-grey">${esc(s.kind)}</span></td>
              <td><a href="${esc(s.url)}" target="_blank" rel="noopener" style="color:var(--accent);text-decoration:none;">${esc(s.label)} ↗</a></td>
              <td style="font-family:var(--font-mono);font-size:11.5px;color:#cbd5e1;">
                ${esc(s.concept || "")}
                ${s.inputs && s.inputs.length ? `<span style="color:var(--muted);">← ${esc(s.inputs.slice(0, 8).join(", "))}</span>` : ""}
              </td>
              <td style="font-family:var(--font-mono);font-size:11.5px;color:var(--muted);">${esc(s.period || s.filed || "—")}</td>
            </tr>`
          )
          .join("")}
      </tbody>
    </table>`;
  }

  // ------------------------------------------------------------------ 7. HISTORY VIEW
  async function loadHistory() {
    try {
      const data = await apiGet("/api/runs");
      state.activeRuns = data.active || [];
      state.history = data.history || [];
      const total = state.history.length;
      document.getElementById("badge-history").textContent = String(total);

      const wrap = document.getElementById("history-table-wrap");
      if (!state.history.length && !state.activeRuns.length) {
        wrap.innerHTML = `<div class="empty-state">No saved runs in SQLite yet. Run a ticker above to generate and persist a memo.</div>`;
        return;
      }

      const combined = [...state.activeRuns.filter((a) => !state.history.some((h) => h.id === a.id)), ...state.history];
      wrap.innerHTML = `<table class="data-table">
        <thead>
          <tr>
            <th>Ticker</th>
            <th>Company Name</th>
            <th>Depth</th>
            <th>Mode</th>
            <th>Status</th>
            <th>Started</th>
            <th class="num-col">Stats</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          ${combined
            .map((r) => {
              const st = r.stats || {};
              return `<tr>
                <td style="font-family:var(--font-mono);font-weight:700;color:var(--accent);">${esc(r.ticker)}</td>
                <td><strong>${esc(r.name)}</strong> <small style="color:var(--muted);">CIK ${esc(r.cik)}</small></td>
                <td><span class="badge badge-grey">${esc(r.depth)}</span></td>
                <td><span class="badge badge-grey">${esc(r.provider)}</span></td>
                <td><span class="badge badge-${esc(r.status)}">${esc(r.status)}</span></td>
                <td style="font-family:var(--font-mono);font-size:11.5px;color:var(--muted);">${esc(fmtTime(r.started))}</td>
                <td class="num-col">${st.words ? `${st.words}w · ${st.citations || 0} cites` : "—"}</td>
                <td>
                  <div style="display:flex;gap:6px;">
                    <button type="button" class="btn btn-secondary btn-sm open-run-btn" data-id="${esc(r.id)}">Open</button>
                    <button type="button" class="btn btn-danger btn-sm del-run-btn" data-id="${esc(r.id)}">Delete</button>
                  </div>
                </td>
              </tr>`;
            })
            .join("")}
        </tbody>
      </table>`;

      wrap.querySelectorAll(".open-run-btn").forEach((b) => {
        b.onclick = () => loadRunFull(b.dataset.id, "memo");
      });
      wrap.querySelectorAll(".del-run-btn").forEach((b) => {
        b.onclick = async () => {
          try {
            await fetch(`/api/runs/${encodeURIComponent(b.dataset.id)}`, { method: "DELETE" });
            await loadHistory();
          } catch (err) {
            showAlert(`Could not delete run: ${err.message}`);
          }
        };
      });
    } catch (_) {}
  }

  // ------------------------------------------------------------------ 8. SETTINGS VIEW
  async function loadConfig() {
    try {
      const cfg = await apiGet("/api/config");
      state.config = cfg;
      const prov = cfg.default_provider || "offline";
      document.getElementById("provider-select").value = prov;
      document.getElementById("settings-provider").value = prov;
      document.getElementById("settings-model").value = cfg.model || "";
      document.getElementById("badge-settings").textContent = prov;

      const autoApprove = cfg.auto_approve !== null && cfg.auto_approve !== undefined ? Boolean(cfg.auto_approve) : prov === "offline";
      document.getElementById("settings-auto-approve").checked = autoApprove;
      document.getElementById("gate-checkbox").checked = !autoApprove;

      const diag = document.getElementById("env-diagnostics");
      if (diag) {
        const provRows = Object.entries(cfg.providers || {})
          .map(
            ([k, ok]) => `<tr>
              <td style="font-family:var(--font-mono);font-weight:600;">${esc(k)}</td>
              <td><span class="badge ${ok ? "badge-green" : "badge-grey"}">${ok ? "Configured / Ready" : "No API Key in Env"}</span></td>
            </tr>`
          )
          .join("");
        diag.innerHTML = `<table class="data-table">
          <thead><tr><th>Parameter / Provider</th><th>Status</th></tr></thead>
          <tbody>
            <tr>
              <td><strong>SEC EDGAR User-Agent</strong></td>
              <td style="font-family:var(--font-mono);">${esc(cfg.sec_user_agent)} ${cfg.sec_user_agent_is_default ? `<span class="badge badge-amber">default — set SEC_USER_AGENT in .env</span>` : `<span class="badge badge-green">custom</span>`}</td>
            </tr>
            <tr>
              <td><strong>Local Data Directory</strong></td>
              <td style="font-family:var(--font-mono);">${esc(cfg.data_dir)}/ (SQLite + gzip EDGAR cache)</td>
            </tr>
            ${provRows}
          </tbody>
        </table>`;
      }
      await updateBudgetEstimate();
    } catch (_) {}
  }

  // ------------------------------------------------------------------ Search autocomplete
  let searchTimer = null;
  function wireSearch() {
    const input = document.getElementById("ticker-input");
    const dd = document.getElementById("search-dropdown");

    input.addEventListener("input", () => {
      clearTimeout(searchTimer);
      const q = input.value.trim();
      if (q.length < 1) {
        dd.style.display = "none";
        return;
      }
      searchTimer = setTimeout(async () => {
        try {
          const res = await apiGet(`/api/search?q=${encodeURIComponent(q)}`);
          const items = res.results || [];
          if (!items.length) {
            dd.style.display = "none";
            return;
          }
          dd.innerHTML = items
            .map(
              (it) => `<div class="search-item" data-ticker="${esc(it.ticker)}">
                <span class="search-item-ticker">${esc(it.ticker)}</span>
                <span class="search-item-name">${esc(it.name)}</span>
                <span class="search-item-cik">CIK ${esc(it.cik)}</span>
              </div>`
            )
            .join("");
          dd.style.display = "block";
          dd.querySelectorAll(".search-item").forEach((el) => {
            el.onclick = () => {
              input.value = el.dataset.ticker;
              dd.style.display = "none";
            };
          });
        } catch (_) {
          dd.style.display = "none";
        }
      }, 160);
    });

    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") {
        dd.style.display = "none";
        startRun();
      } else if (e.key === "Escape") {
        dd.style.display = "none";
      }
    });

    document.addEventListener("click", (e) => {
      if (!input.contains(e.target) && !dd.contains(e.target)) {
        dd.style.display = "none";
      }
    });
  }

  // ------------------------------------------------------------------ Init
  async function init() {
    document.querySelectorAll(".nav-btn").forEach((btn) => {
      btn.addEventListener("click", () => switchView(btn.dataset.view));
    });

    document.querySelectorAll(".chip-btn").forEach((chip) => {
      chip.addEventListener("click", async () => {
        const tk = chip.dataset.ticker;
        document.getElementById("ticker-input").value = tk;
        // If we already have a completed run for this ticker in SQLite, load it immediately; otherwise start one
        try {
          const lat = await apiGet(`/api/latest?ticker=${encodeURIComponent(tk)}`);
          if (lat && lat.run && lat.run.id) {
            await loadRunFull(lat.run.id);
            return;
          }
        } catch (_) {}
      });
    });

    document.getElementById("depth-select").addEventListener("change", updateBudgetEstimate);
    document.getElementById("provider-select").addEventListener("change", (e) => {
      const p = e.target.value;
      if (p !== "offline") {
        document.getElementById("gate-checkbox").checked = true;
      }
      updateBudgetEstimate();
    });

    document.getElementById("run-btn").addEventListener("click", startRun);
    document.getElementById("approve-btn").addEventListener("click", () => decideApproval(true));
    document.getElementById("reject-btn").addEventListener("click", () => decideApproval(false));
    document.getElementById("refresh-history-btn").addEventListener("click", loadHistory);
    document.getElementById("source-filter-input").addEventListener("input", renderSources);
    document.getElementById("source-kind-select").addEventListener("change", renderSources);

    document.getElementById("settings-form").addEventListener("submit", async (e) => {
      e.preventDefault();
      const provider = document.getElementById("settings-provider").value;
      const model = document.getElementById("settings-model").value.trim();
      const auto_approve = document.getElementById("settings-auto-approve").checked;
      try {
        await apiPost("/api/settings", { provider, model, auto_approve });
        const msg = document.getElementById("settings-saved-msg");
        msg.style.display = "inline";
        setTimeout(() => {
          msg.style.display = "none";
        }, 2500);
        await loadConfig();
      } catch (err) {
        showAlert(`Failed to save settings: ${err.message}`);
      }
    });

    wireSearch();
    await loadConfig();
    renderAgentCards();
    await loadHistory();

    // Auto-load latest completed run if one exists in SQLite
    try {
      const latest = await apiGet("/api/latest");
      if (latest && latest.run && latest.run.id) {
        document.getElementById("ticker-input").value = latest.run.ticker;
        await loadRunFull(latest.run.id);
      }
    } catch (_) {}
  }

  document.addEventListener("DOMContentLoaded", init);
})();
