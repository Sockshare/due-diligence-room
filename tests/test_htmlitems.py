"""Unit tests for HTML -> text conversion, 10-K item extraction (largest-span
heuristic skipping Table of Contents), risk factor clustering/diffing, segment
tables, going-concern detection, and DEF 14A Summary Compensation Table."""
from __future__ import annotations

from pathlib import Path

from dd_room import htmlitems

FIXTURE_10K = Path(__file__).parent / "fixtures" / "sample_10k.html"


def test_extract_items_skips_toc_and_drops_ix_header():
    raw_html = FIXTURE_10K.read_text(encoding="utf-8")
    text = htmlitems.html_to_text(raw_html)
    # Hidden ix:header content must be stripped
    assert "contextRef=\"FY2025\"" not in text

    items = htmlitems.extract_items(raw_html, items=("1", "1A", "3", "7"))
    assert items["1"] is not None
    assert items["1"].candidates >= 2  # TOC + actual section
    assert "industrial telemetry hardware" in items["1"].text
    assert len(items["1"].text) > 400

    assert items["1A"] is not None
    assert "sole source semiconductor suppliers" in items["1A"].text
    assert "cybersecurity breach" in items["1A"].text

    assert items["3"] is not None
    assert "securities class action lawsuit" in items["3"].text
    assert "Federal Trade Commission" in items["3"].text

    assert items["7"] is not None
    assert "free cash flow of $1,500 million" in items["7"].text


def test_cluster_and_diff_risk_factors():
    raw_html = FIXTURE_10K.read_text(encoding="utf-8")
    item1a = htmlitems.extract_items(raw_html, items=("1A",))["1A"].text
    clusters = htmlitems.cluster_risk_factors(item1a)
    cluster_names = {c["cluster"] for c in clusters}
    assert "Macro & demand" in cluster_names
    assert "Supply chain & operations" in cluster_names
    assert "Cybersecurity & data" in cluster_names

    prior_1a = (
        "Investing in our common stock involves a high degree of risk. "
        "Adverse macroeconomic conditions, inflation, and higher interest rates could reduce customer spending and demand for our telemetry hardware and cloud subscriptions. "
        "Legacy 2G cellular network sunsetting in rural territories could require hardware replacements."
    )
    diff = htmlitems.diff_risk_factors(item1a, prior_1a)
    assert diff["added_count"] >= 3
    assert diff["removed_count"] == 1
    assert diff["unchanged_count"] >= 1


def test_segment_revenue_and_going_concern_and_compensation():
    raw_html = FIXTURE_10K.read_text(encoding="utf-8")
    segs = htmlitems.segment_revenue_tables(raw_html)
    assert len(segs) == 1
    assert segs[0]["total"] == 10000.0
    assert {r["label"]: r["value"] for r in segs[0]["rows"]} == {
        "Connected Hardware": 6200.0,
        "Cloud Telemetry Subscriptions": 3800.0,
    }

    text = htmlitems.html_to_text(raw_html)
    gc_clean = htmlitems.going_concern_mentions(text)
    assert gc_clean["mentions"] == 1
    assert gc_clean["substantial_doubt"] is False

    gc_doubt = htmlitems.going_concern_mentions(
        "Recurring operating losses raise substantial doubt about the Company's ability to continue as a going concern."
    )
    assert gc_doubt["substantial_doubt"] is True

    def14a_html = """
    <html><body>
      <h2>Executive Compensation — Summary Compensation Table</h2>
      <table>
        <tr><th>Name and Principal Position</th><th>Year</th><th>Salary ($)</th><th>Stock Awards ($)</th><th>Total ($)</th></tr>
        <tr><td>Jane Doe, Chief Executive Officer</td><td>2025</td><td>$850,000</td><td>$6,200,000</td><td>$7,450,000</td></tr>
        <tr><td>Jane Doe, Chief Executive Officer</td><td>2024</td><td>$800,000</td><td>$5,500,000</td><td>$6,700,000</td></tr>
        <tr><td>John Smith, Chief Financial Officer</td><td>2025</td><td>$550,000</td><td>$2,100,000</td><td>$2,950,000</td></tr>
      </table>
    </body></html>
    """
    sct = htmlitems.summary_compensation_table(def14a_html)
    assert len(sct["rows"]) == 2
    assert sct["rows"][0]["name"] == "Jane Doe"
    assert sct["rows"][0]["year"] == 2025
    assert sct["rows"][0]["total"] == 7_450_000.0
