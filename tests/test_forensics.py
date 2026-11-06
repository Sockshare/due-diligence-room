"""Unit tests for forensic accounting formulas (Beneish M-Score, Sloan accruals,
Altman Z-Score, working-capital days, cash conversion, dilution, SBC) against
hand-computed fixtures."""
from __future__ import annotations

import pytest

from dd_room import forensics


def test_beneish_m_score_neutral_baseline():
    """When two consecutive years are identical and NI == CFO, every ratio index
    (DSRI, GMI, AQI, SGI, DEPI, SGAI, LVGI) equals 1.0 and TATA equals 0.0.
    Hand computation:
      M = -4.84 + 0.920(1) + 0.528(1) + 0.404(1) + 0.892(1) + 0.115(1) - 0.172(1) + 4.679(0) - 0.327(1)
        = -4.84 + 2.360 = -2.480  -> GREEN (<= -2.22).
    """
    yr = {
        "revenue": 1000.0,
        "cogs": 600.0,
        "receivables": 120.0,
        "current_assets": 400.0,
        "ppe": 300.0,
        "securities": 50.0,
        "total_assets": 1000.0,
        "da": 50.0,
        "sga": 150.0,
        "ltd": 200.0,
        "current_liabilities": 150.0,
        "net_income": 110.0,
        "cfo": 110.0,
    }
    m = forensics.beneish_m_score(yr, dict(yr), year=2025, prior_year=2024)
    assert m.light == forensics.GREEN
    assert m.value == pytest.approx(-2.48, abs=1e-6)
    for k in ("DSRI", "GMI", "AQI", "SGI", "DEPI", "SGAI", "LVGI"):
        assert m.components[k] == pytest.approx(1.0, abs=1e-6)
    assert m.components["TATA"] == pytest.approx(0.0, abs=1e-6)


def test_beneish_m_score_manipulator_profile():
    """Hand-computed aggressive accruals + receivable surge + gross margin drop:
      prev: rev=1000, cogs=500 (GM=0.50), rec=100, CA=400, PPE=400, sec=0, TA=1000 (AQ=0.20),
            da=50 (dep_rate=50/450=1/9), sga=100, ltd=100, CL=100, NI=120, CFO=120
      cur:  rev=1250, cogs=750 (GM=0.40), rec=250, CA=450, PPE=350, sec=0, TA=1200 (AQ=1-800/1200=1/3),
            da=35 (dep_rate=35/385=1/11), sga=125, ltd=120, CL=120, NI=200, CFO=20
      Indices:
        DSRI = (250/1250) / (100/1000) = 0.20 / 0.10 = 2.0
        GMI  = 0.50 / 0.40 = 1.25
        AQI  = (1/3) / 0.20 = 5/3 = 1.6666667
        SGI  = 1250 / 1000 = 1.25
        DEPI = (1/9) / (1/11) = 11/9 = 1.2222222
        SGAI = (125/1250) / (100/1000) = 1.0
        LVGI = (240/1200) / (200/1000) = 0.20 / 0.20 = 1.0
        TATA = (200 - 20) / 1200 = 0.15
      M = -4.84 + 0.920(2.0) + 0.528(1.25) + 0.404(5/3) + 0.892(1.25)
          + 0.115(11/9) - 0.172(1.0) + 4.679(0.15) - 0.327(1.0)
        = -4.84 + 1.84 + 0.66 + 0.6733333 + 1.115 + 0.1405556 - 0.172 + 0.70185 - 0.327
        = -0.2082611 -> RED (> -1.78).
    """
    prev = {
        "revenue": 1000.0, "cogs": 500.0, "receivables": 100.0, "current_assets": 400.0,
        "ppe": 400.0, "securities": 0.0, "total_assets": 1000.0, "da": 50.0, "sga": 100.0,
        "ltd": 100.0, "current_liabilities": 100.0, "net_income": 120.0, "cfo": 120.0,
    }
    cur = {
        "revenue": 1250.0, "cogs": 750.0, "receivables": 250.0, "current_assets": 450.0,
        "ppe": 350.0, "securities": 0.0, "total_assets": 1200.0, "da": 35.0, "sga": 125.0,
        "ltd": 120.0, "current_liabilities": 120.0, "net_income": 200.0, "cfo": 20.0,
    }
    m = forensics.beneish_m_score(cur, prev, year=2025, prior_year=2024)
    assert m.light == forensics.RED
    assert m.value == pytest.approx(-0.2082611, abs=1e-5)


def test_beneish_missing_inputs_returns_grey():
    cur = {"revenue": 1000.0, "cogs": None}
    prev = {"revenue": 900.0, "cogs": None}
    m = forensics.beneish_m_score(cur, prev, year=2025, prior_year=2024)
    assert m.value is None
    assert m.light == forensics.GREY
    assert "Not computable" in m.note


def test_sloan_accrual_ratio_zones():
    # Green: (100 - 110) / ((1000 + 1000)/2) = -0.01 (-1.0%)
    g = forensics.sloan_accrual_ratio(100.0, 110.0, 1000.0, 1000.0, year=2025)
    assert g.value == pytest.approx(-0.01)
    assert g.light == forensics.GREEN

    # Amber: (180 - 100) / ((1000 + 1000)/2) = +0.08 (+8.0%)
    a = forensics.sloan_accrual_ratio(180.0, 100.0, 1000.0, 1000.0, year=2025)
    assert a.value == pytest.approx(0.08)
    assert a.light == forensics.AMBER

    # Red: (250 - 100) / ((1100 + 900)/2) = +0.15 (+15.0%)
    r = forensics.sloan_accrual_ratio(250.0, 100.0, 1100.0, 900.0, year=2025)
    assert r.value == pytest.approx(0.15)
    assert r.light == forensics.RED

    # Missing input -> Grey
    grey = forensics.sloan_accrual_ratio(100.0, None, 1000.0, 1000.0, year=2025)
    assert grey.value is None
    assert grey.light == forensics.GREY


def test_altman_z_hand_computed():
    """Hand computation:
      TA = 1000, WC = 200 (X1=0.20), RE = 300 (X2=0.30), EBIT = 150 (X3=0.15),
      MarketCap = 1200, TL = 400 (X4=3.00), Sales = 1000 (X5=1.00)
      Z = 1.2(0.20) + 1.4(0.30) + 3.3(0.15) + 0.6(3.00) + 1.0(1.00)
        = 0.24 + 0.42 + 0.495 + 1.80 + 1.00 = 3.955 -> GREEN (> 2.99)
    """
    z = forensics.altman_z(200.0, 300.0, 150.0, 1200.0, 400.0, 1000.0, 1000.0, year=2025)
    assert z.value == pytest.approx(3.955, abs=1e-6)
    assert z.light == forensics.GREEN
    assert z.components["X4 MVE/TL"] == pytest.approx(3.0)

    # Distress zone (< 1.81)
    z_low = forensics.altman_z(-100.0, -200.0, -50.0, 100.0, 800.0, 400.0, 1000.0, year=2025)
    assert z_low.value < 1.81
    assert z_low.light == forensics.RED

    # Financial SIC not applicable -> Grey
    assert forensics.is_financial_sic("6021") is True
    assert forensics.is_financial_sic("3571") is False
    z_fin = forensics.altman_z(200.0, 300.0, 150.0, 1200.0, 400.0, 1000.0, 1000.0, applicable=False, why_not="Financial SIC")
    assert z_fin.value is None
    assert z_fin.light == forensics.GREY


def test_working_capital_and_dilution_metrics():
    # DSO: 100 / 1000 * 365 = 36.5 days; vs prior 28 days (+30.4%) -> RED
    dso = forensics.days_sales_outstanding(100.0, 1000.0, prev_dso=28.0, year=2025)
    assert dso.value == pytest.approx(36.5)
    assert dso.light == forensics.RED

    # DIO: 50 / 500 * 365 = 36.5 days; vs prior 35 days (+4.3%) -> GREEN
    dio = forensics.days_inventory_outstanding(50.0, 500.0, prev_dio=35.0, year=2025)
    assert dio.value == pytest.approx(36.5)
    assert dio.light == forensics.GREEN

    # Cash conversion: negative NI -> grey
    cc_neg = forensics.cash_conversion(50.0, -10.0, year=2025)
    assert cc_neg.value is None
    assert cc_neg.light == forensics.GREY
    cc_pos = forensics.cash_conversion(120.0, 100.0, year=2025)
    assert cc_pos.value == pytest.approx(1.2)
    assert cc_pos.light == forensics.GREEN

    # Dilution: 121 shares vs 100 shares over 2 years -> +10%/yr -> RED
    dil = forensics.share_dilution(121.0, 100.0, year=2025, years_apart=2)
    assert dil.value == pytest.approx(0.10)
    assert dil.light == forensics.RED

    # SBC ratio: 120 / 1000 = 12% -> RED
    sbc = forensics.sbc_ratio(120.0, 1000.0, year=2025)
    assert sbc.value == pytest.approx(0.12)
    assert sbc.light == forensics.RED
