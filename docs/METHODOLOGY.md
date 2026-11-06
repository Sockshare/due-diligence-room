# Methodology, Formulas & Academic Citations

**Research aid, not investment advice.** Due-Diligence Room computes financial trends, valuation multiples, and forensic accounting screens directly from public SEC EDGAR filings (`submissions` and `companyfacts` XBRL APIs) and an optional Yahoo Finance price feed. Every derived number in an investment memo links to the specific section below and lists the exact XBRL facts from which it was computed.

---

## 1. XBRL Fact Selection & Restatement Policy

Public companies tag hundreds of concepts across multiple 10-K, 10-Q, and 8-K filings. The same fiscal year is typically reported in three consecutive 10-K filings (as current, prior, and two-years-prior comparatives). Due-Diligence Room constructs a clean annual series per concept as follows:

1. **Annual forms only:** Only facts reported on forms `10-K`, `10-K/A`, `10-KT`, `10-KT/A`, `20-F`, `20-F/A`, `40-F`, and `40-F/A` are eligible.
2. **Duration filtering:** Flow concepts (revenue, income, cash flows) must span `340` to `390` calendar days so quarterly and year-to-date facts never leak into annual series. Balance-sheet concepts must be instant facts (`start` absent).
3. **Fiscal year labelling:** Each period is keyed by its period end date (`YYYY-MM-DD`) and labelled by the calendar year of that end date (for example, a fiscal year ending `2024-09-28` is labelled `FY2024`).
4. **Latest filed wins (restatement tracking):** When multiple filings report the same period end date, the fact from the **latest filing date** is selected. If an earlier filing reported a numerically different value for the same period end, the fact is flagged as `restated = True` and all superseded values (with their accession numbers and filing dates) are preserved in the source citation.
5. **Concept priority stitching:** Because filers migrate across US-GAAP tags over time (e.g., `Revenues` to `RevenueFromContractWithCustomerExcludingAssessedTax`), each financial line item uses a deterministic priority list of standard `us-gaap` concepts. The concept covering the most recent fiscal year takes priority, and earlier years are backfilled only if missing. Whenever multiple concepts are stitched across years, the Data Quality section flags the seam.

---

## 2. Financial Trend & Valuation Definitions

<a id="cagr"></a>
### Compound Annual Growth Rate (`#cagr`)

$$\text{CAGR} = \left(\frac{V_{t}}{V_{t-n}}\right)^{\frac{1}{n}} - 1$$

Computed only when $n \ge 1$ and both the starting value $V_{t-n}$ and ending value $V_{t}$ are strictly positive. Year-over-year change is computed as $(V_t - V_{t-1}) / |V_{t-1}|$.

<a id="margins"></a>
### Profit Margins (`#margins`)

- **Gross margin:** $\text{Gross Profit} / \text{Revenue}$. If `us-gaap:GrossProfit` is not explicitly tagged, it is derived as $\text{Revenue} - \text{Cost of Revenue}$.
- **Operating margin:** $\text{Operating Income} / \text{Revenue}$ (`us-gaap:OperatingIncomeLoss`).
- **Net margin:** $\text{Net Income} / \text{Revenue}$ (`us-gaap:NetIncomeLoss`).
- Multi-year margin change is reported in percentage points ($\text{Margin}_t - \text{Margin}_{t-n}$).

<a id="fcf"></a>
### Free Cash Flow (`#fcf`)

$$\text{FCF} = \text{Cash From Operating Activities (CFO)} - |\text{Capital Expenditure}|$$

$$\text{FCF Margin} = \frac{\text{FCF}}{\text{Revenue}}$$

Note: This is standard unlevered-style cash flow after capital expenditure (`PaymentsToAcquirePropertyPlantAndEquipment`). It does **not** deduct stock-based compensation (`ShareBasedCompensation`) or cash acquisitions; the Skeptic agent explicitly checks whether high SBC distorts headline FCF.

<a id="net-debt"></a>
### Net Debt (`#net-debt`)

$$\text{Total Debt} = \text{Long-Term Debt (non-current)} + \text{Short-Term / Current Debt}$$

$$\text{Cash \& Short-Term Investments} = \text{Cash \& Equivalents} + \text{Short-Term Marketable Securities}$$

$$\text{Net Debt} = \text{Total Debt} - (\text{Cash \& Short-Term Investments})$$

A negative Net Debt value indicates a **net cash** position. Operating lease liabilities are not included unless the filer combines them into `LongTermDebtAndCapitalLeaseObligations`.

<a id="roic"></a>
### Return on Invested Capital (`#roic`)

$$\text{Effective Tax Rate } (\tau) = \min\left(\max\left(\frac{\text{Income Tax Expense}}{\text{Pre-Tax Income}},\, 0\right),\, 0.50\right)$$

$$\text{NOPAT} = \text{Operating Income} \times (1 - \tau) \quad (\text{defaults to } \tau = 21\% \text{ if tax or pre-tax income is missing})$$

$$\text{Invested Capital} = \text{Shareholders' Equity} + \text{Total Debt} - \text{Cash \& Short-Term Investments}$$

$$\text{ROIC} = \frac{\text{NOPAT}}{\text{Invested Capital}} \quad (\text{defined when Invested Capital} > 0)$$

*Limitation:* Aggressive share buybacks reduce book equity and can artificially inflate book-based ROIC. The Skeptic agent flags this whenever buybacks are large relative to revenue.

<a id="valuation"></a>
### Valuation Snapshot (`#valuation`)

- **Market Capitalisation:** $\text{Latest Share Price} \times \text{Shares Outstanding}$ (`dei:EntityCommonStockSharesOutstanding`, falling back to diluted weighted-average shares if `dei` is untagged).
- **Enterprise Value (EV):** $\text{Market Cap} + \text{Total Debt} - \text{Cash \& Short-Term Investments}$.
- **EBITDA:** $\text{Operating Income} + \text{Depreciation \& Amortization}$.
- **Multiples:** Trailing $\text{P/E} = \text{Market Cap} / \text{Net Income}$ (omitted when $\text{Net Income} \le 0$); $\text{P/S} = \text{Market Cap} / \text{Revenue}$; $\text{EV/EBITDA} = \text{EV} / \text{EBITDA}$ (falls back to $\text{EV/EBIT}$ when D&A is not separately tagged); $\text{FCF Yield} = \text{FCF} / \text{Market Cap}$.

---

## 3. Forensic Accounting Screens

<a id="beneish-m-score"></a>
### Beneish M-Score (`#beneish-m-score`)

**References:**
- Beneish, M. D. (1999). *"The Detection of Earnings Manipulation."* Financial Analysts Journal, 55(5), 24–36.
- Beneish, M. D., Lee, C. M. C., & Nichols, D. C. (2013). *"Earnings Manipulation and Expected Returns."* Financial Analysts Journal, 69(2), 57–82.

**Eight component indices** comparing fiscal year $t$ to $t-1$:

1. **DSRI (Days' Sales in Receivables Index):**
   $$\text{DSRI} = \frac{\text{Receivables}_t / \text{Revenue}_t}{\text{Receivables}_{t-1} / \text{Revenue}_{t-1}}$$
2. **GMI (Gross Margin Index):**
   $$\text{GMI} = \frac{(\text{Revenue}_{t-1} - \text{COGS}_{t-1}) / \text{Revenue}_{t-1}}{(\text{Revenue}_t - \text{COGS}_t) / \text{Revenue}_t}$$
3. **AQI (Asset Quality Index):**
   $$\text{AQ}_k = 1 - \frac{\text{Current Assets}_k + \text{PP\&E}_k + \text{Securities}_k}{\text{Total Assets}_k}, \qquad \text{AQI} = \frac{\text{AQ}_t}{\text{AQ}_{t-1}}$$
4. **SGI (Sales Growth Index):**
   $$\text{SGI} = \frac{\text{Revenue}_t}{\text{Revenue}_{t-1}}$$
5. **DEPI (Depreciation Index):**
   $$\text{DepRate}_k = \frac{\text{D\&A}_k}{\text{D\&A}_k + \text{PP\&E}_k}, \qquad \text{DEPI} = \frac{\text{DepRate}_{t-1}}{\text{DepRate}_t}$$
6. **SGAI (SG&A Expenses Index):**
   $$\text{SGAI} = \frac{\text{SG\&A}_t / \text{Revenue}_t}{\text{SG\&A}_{t-1} / \text{Revenue}_{t-1}}$$
7. **LVGI (Leverage Index):**
   $$\text{LVGI} = \frac{(\text{LTD}_t + \text{Current Liabilities}_t) / \text{Total Assets}_t}{(\text{LTD}_{t-1} + \text{Current Liabilities}_{t-1}) / \text{Total Assets}_{t-1}}$$
8. **TATA (Total Accruals to Total Assets):**
   $$\text{TATA} = \frac{\text{Net Income}_t - \text{CFO}_t}{\text{Total Assets}_t}$$

**Composite Score:**

$$M = -4.84 + 0.920\cdot\text{DSRI} + 0.528\cdot\text{GMI} + 0.404\cdot\text{AQI} + 0.892\cdot\text{SGI} + 0.115\cdot\text{DEPI} - 0.172\cdot\text{SGAI} + 4.679\cdot\text{TATA} - 0.327\cdot\text{LVGI}$$

**Traffic-light thresholds:**
- **Green:** $M \le -2.22$ (original 1999 conservative non-manipulator zone)
- **Amber:** $-2.22 < M \le -1.78$ (borderline zone)
- **Red:** $M > -1.78$ (Beneish, Lee & Nichols 2013 probabilistic cut-off)
- **Grey:** One or more required component inputs are not tagged in XBRL.

<a id="sloan-accrual-ratio"></a>
### Sloan Balance-Sheet / Cash-Flow Accrual Ratio (`#sloan-accrual-ratio`)

**Reference:**
- Sloan, R. G. (1996). *"Do Stock Prices Fully Reflect Information in Accruals and Cash Flows about Future Earnings?"* The Accounting Review, 71(3), 289–315.

**Formula:**

$$\text{Accrual Ratio} = \frac{\text{Net Income}_t - \text{CFO}_t}{\frac{1}{2}(\text{Total Assets}_t + \text{Total Assets}_{t-1})}$$

**Traffic-light thresholds (practitioner convention):**
- **Green:** $\le +5.0\%$
- **Amber:** $+5.0\% < \text{Accrual Ratio} \le +10.0\%$
- **Red:** $> +10.0\%$ (earnings running substantially ahead of operating cash flow)

<a id="altman-z-score"></a>
### Altman Z-Score (`#altman-z-score`)

**Reference:**
- Altman, E. I. (1968). *"Financial Ratios, Discriminant Analysis and the Prediction of Corporate Bankruptcy."* The Journal of Finance, 23(4), 589–609.

**Formula (public non-financial corporations):**

$$Z = 1.2\cdot X_1 + 1.4\cdot X_2 + 3.3\cdot X_3 + 0.6\cdot X_4 + 1.0\cdot X_5$$

where:
- $X_1 = (\text{Current Assets} - \text{Current Liabilities}) / \text{Total Assets}$
- $X_2 = \text{Retained Earnings} / \text{Total Assets}$
- $X_3 = \text{EBIT (Operating Income)} / \text{Total Assets}$
- $X_4 = \text{Market Value of Equity} / \text{Total Liabilities}$
- $X_5 = \text{Sales (Revenue)} / \text{Total Assets}$

**Traffic-light thresholds (Altman 1968):**
- **Green (Safe zone):** $Z > 2.99$
- **Amber (Grey zone):** $1.81 \le Z \le 2.99$
- **Red (Distress zone):** $Z < 1.81$
- **Grey:** Omitted for financial-sector SIC codes (`6000–6799`) where balance-sheet leverage is structural, or when market capitalisation is unavailable.

<a id="dso-dio"></a>
### Working-Capital Days: DSO & DIO (`#dso-dio`)

$$\text{DSO} = \frac{\text{Accounts Receivable}}{\text{Revenue}} \times 365, \qquad \text{DIO} = \frac{\text{Inventory}}{\text{Cost of Revenue}} \times 365$$

Because absolute working-capital days vary widely by industry, traffic lights evaluate the **year-over-year percentage change**:
- **Green:** YoY increase $\le +10\%$
- **Amber:** YoY increase between $+10\%$ and $+20\%$
- **Red:** YoY increase $> +20\%$ (receivables or inventory growing much faster than sales/COGS)

<a id="cash-conversion"></a>
### Cash Conversion Ratio (`#cash-conversion`)

$$\text{Cash Conversion} = \frac{\text{Cash From Operations (CFO)}}{\text{Net Income}} \quad (\text{computed when Net Income} > 0)$$

- **Green:** $\ge 1.00\times$
- **Amber:** $0.80\times \le \text{CFO/NI} < 1.00\times$
- **Red:** $< 0.80\times$
- **Grey:** Net income is zero or negative (ratio is not meaningful).

<a id="dilution"></a>
### Share-Count Dilution (`#dilution`)

$$\text{Annualised Dilution} = \left(\frac{\text{Diluted Weighted Shares}_t}{\text{Diluted Weighted Shares}_{t-n}}\right)^{\frac{1}{n}} - 1$$

- **Green:** $\le +2.0\% / \text{yr}$ (or negative, indicating net share shrink via buybacks)
- **Amber:** $+2.0\%$ to $+5.0\% / \text{yr}$
- **Red:** $> +5.0\% / \text{yr}$

<a id="sbc"></a>
### Stock-Based Compensation Intensity (`#sbc`)

$$\text{SBC Ratio} = \frac{\text{Stock-Based Compensation}}{\text{Revenue}}$$

- **Green:** $\le 5.0\%$
- **Amber:** $5.0\%$ to $10.0\%$
- **Red:** $> 10.0\%$

---

## 4. Legal, Insider & Governance Extraction

<a id="insiders"></a>
### Insider Form 4 Aggregation (`#insiders`)

Form 4 XML filings from the last 12 months (up to 20 filings in `standard` depth, 40 in `deep` depth) are parsed for non-derivative transactions (`<nonDerivativeTransaction>`). Only open-market **purchases (`code = P`)** and open-market **sales (`code = S`)** are summed into buy/sell share and dollar totals ($\text{shares} \times \text{reported price}$). Option exercises (`M`), awards (`A`), tax withholdings (`F`), and gifts (`G`) are tallied separately so routine vesting is not misclassified as open-market buying.

<a id="compensation"></a>
### Executive Compensation (`#compensation`)

The most recent `DEF 14A` proxy statement is scanned for the Summary Compensation Table (matching headers containing `name`/`principal position`, `salary`, and `total`). Named Executive Officer total compensation is compared against latest fiscal-year revenue and net income.

---

## 5. Section Confidence Scoring

Each section's confidence score $C \in [0, 1]$ begins at its **data completeness** ratio (fraction of expected series, filings, or metrics successfully extracted) and applies explicit penalties:
- **Unresolved Skeptic counters:** $-8\%$ per high-severity counter and $-4\%$ per medium-severity counter attached to bullish claims in that section (capped at $-30\%$).
- **Cross-agent disagreements:** $-5\%$ per unresolved disagreement touching the section.
- **Logged data gaps:** $-5\%$ per missing concept or skipped filing logged in that section (capped at $-25\%$).

Scores map to **high** ($\ge 0.75$), **medium** ($0.45\text{–}0.74$), and **low** ($< 0.45$).
