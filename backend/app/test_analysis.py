"""No database needed. Run from the backend folder:  python -m app.test_analysis"""
from datetime import date

from app.analysis import analyze_product, build_recommendations, classify_delay, effective_eta

AS_OF = date(2026, 10, 15)

po1042 = {"status": "open", "expected_date": "2026-10-12", "revised_expected_date": "2026-10-26", "actual_delivery_date": None}
po1050 = {"status": "open", "expected_date": "2026-10-30", "revised_expected_date": None, "actual_delivery_date": None}

state, days = classify_delay(po1042, AS_OF)
assert (state, days) == ("CONFIRMED_DELAY", 14), (state, days)
assert classify_delay(po1050, AS_OF)[0] == "ON_TRACK"
eta = effective_eta(po1042)

# ---- PRD-101 behind WH-01
branches = [
    {"branch_id": "BR-01", "branch_name": "North Branch 1", "reorder_level": 50},
    {"branch_id": "BR-02", "branch_name": "North Branch 2", "reorder_level": 40},
    {"branch_id": "BR-03", "branch_name": "North Branch 3", "reorder_level": 30},
]
stock = {"WH-01": 420, "BR-01": 25, "BR-02": 60, "BR-03": 150}
demand = {"BR-01": {"qty": 25 * 28, "days": 28}, "BR-02": {"qty": 20 * 28, "days": 28}, "BR-03": {"qty": 15 * 28, "days": 28}}
a = analyze_product(AS_OF, eta, "WH-01", branches, stock, demand)

assert a["warehouse"]["cover_days"] == 7.0
assert a["warehouse"]["stockout_date"] == "2026-10-22"
by = {b["branch_id"]: b for b in a["branches"]}
assert (by["BR-01"]["stockout_date"], by["BR-01"]["exposure_days"], by["BR-01"]["unmet_units"], by["BR-01"]["risk_level"]) == ("2026-10-23", 3, 75, "AT_RISK")
assert (by["BR-02"]["stockout_date"], by["BR-02"]["exposure_days"], by["BR-02"]["unmet_units"], by["BR-02"]["risk_level"]) == ("2026-10-25", 1, 20, "AT_RISK")
assert (by["BR-03"]["stockout_date"], by["BR-03"]["unmet_units"], by["BR-03"]["risk_level"]) == ("2026-11-01", 0, "OK")
assert by["BR-02"]["below_reorder_level"] is False and by["BR-01"]["below_reorder_level"] is True  # N1
assert a["total_unmet_units"] == 95 and a["risk_level"] == "AT_RISK"
print("PRD-101 OK: 7.0 days, stockout 2026-10-22, BR-01 75, BR-02 20, BR-03 OK, total 95")

# ---- PRD-102 negative control (N2)
stock2 = {"WH-01": 300, "BR-01": 60, "BR-02": 40, "BR-03": 30}
demand2 = {"BR-01": {"qty": 5 * 28, "days": 28}, "BR-02": {"qty": 4 * 28, "days": 28}, "BR-03": {"qty": 3 * 28, "days": 28}}
a2 = analyze_product(AS_OF, eta, "WH-01", branches, stock2, demand2)
assert a2["warehouse"]["stockout_date"] == "2026-11-09" and a2["warehouse"]["cover_days"] == 25.0
assert a2["total_unmet_units"] == 0 and a2["risk_level"] == "OK"
print("PRD-102 OK: not at risk, WH-01 stockout 2026-11-09")

# ---- missing data -> UNKNOWN, never a guess
a3 = analyze_product(AS_OF, eta, "WH-01", branches, stock, {**demand, "BR-03": {"qty": 30, "days": 5}})
assert a3["risk_level"] == "UNKNOWN" and a3["total_unmet_units"] == 0
print("Insufficient data OK: UNKNOWN")

# ---- recommendations R1, R2, R3
donors = [{"warehouse_id": "WH-02", "stock": 900, "daily_demand": 30.0}]
alternates = [{"supplier_id": "SUP-005", "lead_time_days": 9}]
recs = build_recommendations(AS_OF, eta, "CONFIRMED_DELAY", a, donors, alternates)
assert [r["action_type"] for r in recs] == ["STOCK_TRANSFER", "EXPEDITE_PO", "ALTERNATE_SUPPLIER_ORDER"], recs
r1, r2, r3 = recs
assert r1["donor_warehouse_id"] == "WH-02" and r1["allocation"] == {"BR-01": 75, "BR-02": 20}
assert r1["donor_surplus_units"] == 480 and r1["donor_days_cover_after"] == 26.8 and r1["arrival_date"] == "2026-10-17"
assert r2["must_arrive_by"] == "2026-10-23" and r2["days_earlier_than_eta"] == 3
assert r3["arrival_date"] == "2026-10-24" and r3["residual_unmet_units"] == 25
assert all(r["requires_human_approval"] for r in recs)
print("Recommendations OK: R1 transfer 95 (WH-02 keeps 26.8 days), R2 by 2026-10-23, R3 residual 25")
print("\nALL CHECKS PASSED")