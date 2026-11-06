"""Unit tests for XBRL companyfacts annual series extraction, restatement
detection ("latest filed wins"), concept stitching, and FinancialTable building."""
from __future__ import annotations

import pytest

from dd_room import xbrl


def _sample_companyfacts() -> dict:
    return {
        "cik": 999999,
        "entityName": "Sample Corp",
        "facts": {
            "us-gaap": {
                "Revenues": {
                    "units": {
                        "USD": [
                            # Quarterly fact (90 days) -> must be ignored
                            {"start": "2024-01-01", "end": "2024-03-31", "val": 220.0, "form": "10-Q", "filed": "2024-05-05", "accn": "0000999999-24-000010"},
                            # Annual 2023 original filing
                            {"start": "2023-01-01", "end": "2023-12-31", "val": 800.0, "form": "10-K", "filed": "2024-02-15", "accn": "0000999999-24-000001"},
                            # Annual 2023 restated in 2024 10-K comparative -> latest filed (815.0) must win and mark restated=True
                            {"start": "2023-01-01", "end": "2023-12-31", "val": 815.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001", "frame": "CY2023"},
                        ]
                    }
                },
                "RevenueFromContractWithCustomerExcludingAssessedTax": {
                    "units": {
                        "USD": [
                            # Covers 2024 (most recent year) -> becomes primary concept, stitched with Revenues for 2023
                            {"start": "2024-01-01", "end": "2024-12-31", "val": 950.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001", "frame": "CY2024"},
                        ]
                    }
                },
                "CostOfRevenue": {
                    "units": {
                        "USD": [
                            {"start": "2023-01-01", "end": "2023-12-31", "val": 500.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                            {"start": "2024-01-01", "end": "2024-12-31", "val": 550.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                        ]
                    }
                },
                "Assets": {
                    "units": {
                        "USD": [
                            {"end": "2023-12-31", "val": 1200.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                            {"end": "2024-12-31", "val": 1400.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                        ]
                    }
                },
                "StockholdersEquity": {
                    "units": {
                        "USD": [
                            {"end": "2023-12-31", "val": 700.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                            {"end": "2024-12-31", "val": 850.0, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                        ]
                    }
                },
            },
            "dei": {
                "EntityCommonStockSharesOutstanding": {
                    "units": {
                        "shares": [
                            {"end": "2025-01-31", "val": 50_000_000, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                            {"end": "2025-01-31", "val": 10_000_000, "form": "10-K", "filed": "2025-02-18", "accn": "0000999999-25-000001"},
                        ]
                    }
                }
            },
        },
    }


def test_annual_series_restatement_and_duration_filtering():
    facts = _sample_companyfacts()
    rev = xbrl.annual_series(facts, "Revenues", name="revenue")
    assert list(rev.keys()) == [2023]
    f23 = rev[2023]
    assert f23.value == pytest.approx(815.0)
    assert f23.restated is True
    assert len(f23.superseded) == 1
    assert f23.superseded[0]["value"] == pytest.approx(800.0)
    assert f23.accn == "0000999999-25-000001"


def test_merged_series_and_build_table_derivations():
    facts = _sample_companyfacts()
    tbl = xbrl.build_table(facts, years=5)
    assert tbl.years == [2023, 2024]
    assert tbl.value("revenue", 2023) == pytest.approx(815.0)
    assert tbl.value("revenue", 2024) == pytest.approx(950.0)
    assert len(tbl.concepts_used["revenue"]) == 2

    # Derived gross profit = revenue - cogs
    assert tbl.value("gross_profit", 2023) == pytest.approx(315.0)
    assert tbl.value("gross_profit", 2024) == pytest.approx(400.0)

    # Derived total liabilities = assets - equity
    assert tbl.value("total_liabilities", 2023) == pytest.approx(500.0)
    assert tbl.value("total_liabilities", 2024) == pytest.approx(550.0)

    # Multi-class shares outstanding summed for same end date
    assert tbl.shares_outstanding is not None
    assert tbl.shares_outstanding.value == pytest.approx(60_000_000.0)
