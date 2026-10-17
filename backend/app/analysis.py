"""Pure Python calculations (Python computes). No database or LLM in this file."""
import math
from datetime import date, timedelta

SAFETY_DAYS = 14      # A2: a donor keeps 14 days of its own cover
TRANSFER_DAYS = 2     # A3: a transfer takes 2 days
WATCH_DAYS = 5        # WATCH if stockout falls within 5 days after the ETA
MIN_SALES_DAYS = 14   # data-sufficiency rule
WINDOW_DAYS = 28

RISK_ORDER = {"AT_RISK": 3, "WATCH": 2, "UNKNOWN": 1, "OK": 0}


def to_date(value):
    return date.fromisoformat(value) if isinstance(value, str) else value


def effective_eta(po):
    return to_date(po["revised_expected_date"] or po["expected_date"])


def classify_delay(po, as_of):
    """Return (delay_state, delay_days). A delay is confirmed only by a record."""
    expected = to_date(po["expected_date"])
    revised = to_date(po["revised_expected_date"]) if po["revised_expected_date"] else None
    actual = to_date(po["actual_delivery_date"]) if po["actual_delivery_date"] else None

    if po["status"] == "received":
        if actual is None:
            return "DATA_GAP", None
        if actual <= expected:
            return "DELIVERED_ON_TIME", 0
        return "CONFIRMED_DELAY", (actual - expected).days
    if revised and revised > expected:
        return "CONFIRMED_DELAY", (revised - expected).days
    if as_of > effective_eta(po):
        return "OVERDUE_UNCONFIRMED", None
    return "ON_TRACK", 0


def _days_from(start, days):
    return start + timedelta(days=int(math.floor(days + 1e-9)))


def classify_risk(stockout, eta):
    exposure = max(0, (eta - stockout).days)
    if exposure > 0:
        return "AT_RISK", exposure
    if (stockout - eta).days <= WATCH_DAYS:
        return "WATCH", 0
    return "OK", 0


def analyze_product(as_of, eta, warehouse_id, branches, stock, demand):
    """Stockout dates, exposure and unmet units for one product behind one warehouse.

    branches: [{branch_id, branch_name, reorder_level}]
    stock:    {location_id: stock_on_hand}
    demand:   {branch_id: {"qty": total over the window, "days": number of sale days}}
    """
    daily, missing = {}, []
    for b in branches:
        bid = b["branch_id"]
        dm = demand.get(bid)
        if bid not in stock or not dm or dm["days"] < MIN_SALES_DAYS or dm["qty"] <= 0:
            missing.append(bid)
        else:
            daily[bid] = dm["qty"] / WINDOW_DAYS
    wh_stock = stock.get(warehouse_id)

    if missing or wh_stock is None:
        # not enough data: report UNKNOWN, never guess
        return {
            "complete": False, "risk_level": "UNKNOWN", "total_unmet_units": 0,
            "missing": missing + ([warehouse_id] if wh_stock is None else []),
            "warehouse": {"warehouse_id": warehouse_id, "stock_on_hand": wh_stock, "risk_level": "UNKNOWN"},
            "branches": [{"branch_id": b["branch_id"], "branch_name": b["branch_name"],
                          "risk_level": "UNKNOWN", "exposure_days": 0, "unmet_units": 0} for b in branches],
        }

    wh_daily = sum(daily.values())
    wh_cover = wh_stock / wh_daily
    wh_stockout = _days_from(as_of, wh_cover)
    wh_risk, _ = classify_risk(wh_stockout, eta)

    rows = []
    for b in branches:
        bid = b["branch_id"]
        cover = stock[bid] / daily[bid]
        stockout = _days_from(as_of, wh_cover + cover)  # branches rely on own stock after the warehouse runs out
        risk, exposure = classify_risk(stockout, eta)
        rows.append({
            "branch_id": bid, "branch_name": b["branch_name"],
            "stock_on_hand": stock[bid], "reorder_level": b["reorder_level"],
            "below_reorder_level": stock[bid] < b["reorder_level"],
            "daily_demand": daily[bid], "own_cover_days": round(cover, 1),
            "stockout_date": stockout.isoformat(), "exposure_days": exposure,
            "unmet_units": int(round(exposure * daily[bid])), "risk_level": risk,
        })
    rows.sort(key=lambda r: (-r["unmet_units"], r["stockout_date"]))
    for rank, r in enumerate([r for r in rows if r["risk_level"] == "AT_RISK"], start=1):
        r["rank"] = rank

    worst = max((r["risk_level"] for r in rows), key=lambda k: RISK_ORDER[k])
    return {
        "complete": True, "risk_level": worst, "missing": [],
        "total_unmet_units": sum(r["unmet_units"] for r in rows),
        "warehouse": {
            "warehouse_id": warehouse_id, "stock_on_hand": wh_stock, "daily_demand": wh_daily,
            "cover_days": round(wh_cover, 1), "stockout_date": wh_stockout.isoformat(), "risk_level": wh_risk,
        },
        "branches": rows,
    }


def build_recommendations(as_of, eta, delay_state, analysis, donors, alternates):
    """RR1-RR5: transfer, expedite, alternate supplier. Advisory only."""
    at_risk = [b for b in analysis["branches"] if b["exposure_days"] > 0]
    if not at_risk:
        return []
    total = sum(b["unmet_units"] for b in at_risk)
    earliest = min(to_date(b["stockout_date"]) for b in at_risk)
    options = []

    # RR1: transfer from a donor whose surplus covers the need and arrives before the first stockout
    arrival = as_of + timedelta(days=TRANSFER_DAYS)
    best = None
    for dn in donors:
        if dn["stock"] is None or not dn["daily_demand"]:
            continue
        surplus = int(dn["stock"] - dn["daily_demand"] * SAFETY_DAYS)
        if surplus >= total and arrival < earliest and (best is None or surplus > best["surplus"]):
            best = {"donor": dn, "surplus": surplus}
    if best:
        dn = best["donor"]
        options.append({
            "group": 1, "residual": 0, "action_type": "STOCK_TRANSFER",
            "donor_warehouse_id": dn["warehouse_id"], "total_units": total,
            "allocation": {b["branch_id"]: b["unmet_units"] for b in at_risk},
            "donor_surplus_units": best["surplus"],
            "arrival_date": arrival.isoformat(),
            "donor_days_cover_after": round((dn["stock"] - total) / dn["daily_demand"], 1),
            "residual_unmet_units": 0,
        })

    # RR2: expedite the delayed order
    if delay_state == "CONFIRMED_DELAY":
        options.append({
            "group": 2, "residual": 0, "action_type": "EXPEDITE_PO",
            "must_arrive_by": earliest.isoformat(),
            "days_earlier_than_eta": (eta - earliest).days,
            "residual_unmet_units": None,
        })

    # RR3: alternate supplier if it can arrive before the revised ETA
    for alt in alternates:
        arr = as_of + timedelta(days=alt["lead_time_days"])
        if arr >= eta:
            continue
        residual = sum(
            max(0, (arr - to_date(b["stockout_date"])).days) * b["daily_demand"] for b in at_risk
        )
        options.append({
            "group": 3, "residual": int(round(residual)), "action_type": "ALTERNATE_SUPPLIER_ORDER",
            "supplier_id": alt["supplier_id"], "lead_time_days": alt["lead_time_days"],
            "arrival_date": arr.isoformat(), "residual_unmet_units": int(round(residual)),
            "units_reduced": total - int(round(residual)),
        })

    # RR4: ordering. RR5: no cost data, so cost is not used.
    options.sort(key=lambda o: (o["group"], o["residual"]))
    for i, o in enumerate(options, start=1):
        o["rank"] = i
        o["recommendation_id"] = f"R{i}"
        o.pop("group"), o.pop("residual")
        o["requires_human_approval"] = True
    return options