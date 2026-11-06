# User Guide — Due-Diligence Room

Due-Diligence Room turns a stock ticker or SEC CIK into a fully sourced, multi-agent investment committee memo using public SEC EDGAR filings.

---

## 1. Launching the Workstation

From the project directory, run:

```bash
./run.sh
```

This creates `.venv` if needed, installs pinned dependencies, and starts the local server at `http://127.0.0.1:8112`.

### Command-Line Mode

You can also run a full due-diligence pass directly from the terminal without opening a browser:

```bash
.venv/bin/python app.py --ticker AAPL --depth standard --provider offline --out AAPL_memo.md
```

---

## 2. Running a Company Analysis

1. **Choose a Ticker or CIK:** Type any SEC-registered ticker (e.g., `AAPL`, `NVDA`, `SCKT`, `MSFT`) or 10-digit CIK in the top search bar. Live autocomplete searches the SEC company ticker directory.
2. **Select Analysis Depth:**
   - **Quick (`quick`):** 3 fiscal years of XBRL financials, latest 10-K only, skips Form 4 XML parsing, peers, and 13F searches. Fastest turnaround.
   - **Standard (`standard`):** 5 fiscal years, current + prior 10-K for Year-over-Year Item 1A Risk Factor diffing, up to 20 Form 4 insider filings parsed, same-SIC peer comparison, and 13F holder search.
   - **Deep (`deep`):** 10 fiscal years of XBRL financials, current + prior 10-K diff, up to 40 Form 4 filings parsed, peers, and 13F holder search.
3. **Select Mode / LLM Provider:**
   - **Offline (`offline`):** Default zero-key mode. 100% deterministic prose and tables, zero external LLM calls, zero token cost.
   - **Keyed Providers (`gemini`, `anthropic`, `openai`, `ollama`):** Uses the configured LLM strictly to draft narrative paragraphs (Business Overview, Risk-Change Summary, Bear-Case Narrative, Executive Summary). All financial numbers and ratios are still computed deterministically, and if an LLM draft introduces an uncited number, the Editor automatically rejects the LLM prose and reverts to the deterministic cited summary.
4. **Human Approval Gate (`Gate before Editor`):**
   - When checked (default for keyed providers, optional in offline mode), the run pauses after the Analyst, Forensic Accountant, Legal, and Skeptic finish populating the blackboard.
   - A purple banner appears at the top of the screen showing completed sections, claims, counters, and gaps. Click **Approve & Finalise** to let the Editor score confidence and assemble the final memo, or **Reject Run** to stop.

---

## 3. Navigating the Eight Workstation Screens

- **Memo:** Reads the complete 15-section investment memo plus Sources and Methodology. Hover over any highlighted blue figure (`a.cite`) to open the floating **Citation Inspector** showing the exact source ID (`[Sn]`), XBRL concept (`us-gaap:...`), accession number, and period; click any figure to open the SEC EDGAR filing index or `/methodology` formula in a new tab. Use the top buttons to export **Markdown (`.md`)**, **PDF (`.pdf`)**, or open the **Print / Standalone View**.
- **Live Room:** Watch the five agents (**Analyst**, **Forensic Accountant**, **Legal**, **Skeptic**, **Editor**) execute tools in real time over Server-Sent Events (SSE), and inspect the 15-cell **Shared Blackboard** with per-section completeness and confidence bars.
- **Financials:** Interactive Chart.js visualisations of multi-year Revenue, Operating Income, Net Income, and Free Cash Flow; Gross/Operating/Net/FCF margin trajectories; 5-year share price history; valuation multiples; the full XBRL financial model table; and same-SIC peer comparison.
- **Forensics:** Traffic-light cards (`green`, `amber`, `red`, `grey`) for all 8 forensic screens (**Beneish M-Score**, **Sloan Accrual Ratio**, **Altman Z-Score**, **DSO**, **DIO**, **Cash Conversion CFO/NI**, **Share Dilution**, **SBC / Revenue**), complete with a Beneish 8-component bar chart, expandable raw XBRL input tables, 8-K auditor-change/restatement alerts, and 10-K going-concern evaluation.
- **Risk & Legal:** Item 1A Risk Factor theme distribution chart, Year-over-Year 10-K Risk Factor sentence diff (added vs dropped sentences), Item 3 Legal Proceedings, the **Skeptic's Bullish Claims vs Counter-Arguments** table, 24-month Form 8-K material event timeline, Form 4 insider buying/selling table, and DEF 14A Executive Compensation table.
- **Sources:** Searchable and filterable table of every source (`xbrl`, `filing`, `derived`, `market`, `efts`, `index`) registered during the run.
- **History:** Browse, re-open, or delete previous runs persisted in local SQLite (`data/dd_room.db`).
- **Settings:** Set your default LLM provider, model override, and approval-gate preference, and inspect environment diagnostics (SEC User-Agent header and detected API keys).
