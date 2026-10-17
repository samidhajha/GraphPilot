from datetime import date, timedelta
from falkordb import Node, Edge, Path
from app.graph_client import run_readonly
from app import queries as Q
from app.analysis import (WINDOW_DAYS, analyze_product, build_recommendations,
                          classify_delay, effective_eta)


def _node(n):
    return {"gid": n.id, "labels": n.labels, "props": n.properties}


def _edge(e):
    return {"src": e.src_node, "dst": e.dest_node, "type": e.relation, "props": e.properties}


def _empty():
    return {"nodes": [], "edges": [], "paths": [], "tables": [], "notes": []}


def _collect(result):
    nodes, edges, paths = {}, {}, []
    for row in result.result_set:
        for cell in row:
            if isinstance(cell, Node):
                nodes[cell.id] = _node(cell)
            elif isinstance(cell, Edge):
                edges[(cell.src_node, cell.dest_node, cell.relation)] = _edge(cell)
            elif isinstance(cell, Path):
                for n in cell.nodes():
                    nodes[n.id] = _node(n)
                for e in cell.edges():
                    edges[(e.src_node, e.dest_node, e.relation)] = _edge(e)
                paths.append({"node_gids": [n.id for n in cell.nodes()],
                              "rels": [e.relation for e in cell.edges()]})
    ev = _empty()
    ev["nodes"] = list(nodes.values())
    ev["edges"] = list(edges.values())
    ev["paths"] = paths
    return ev


def has_evidence(ev):
    return bool(ev["nodes"] or ev["edges"] or ev["tables"])


def _d(s):
    return date.fromisoformat(s)


# ---------------- tool 1: PO details
def get_purchase_order(po_id: str):
    po_id = po_id.strip().upper()
    ev = _collect(run_readonly(Q.Q_PO, {"po_id": po_id}))
    po = next((n for n in ev["nodes"] if n["props"].get("po_id") == po_id), None)
    if not po:
        return _empty()
    p = po["props"]

    # delay is derived here (status in the graph is lifecycle only)
    if p.get("status") == "open":
        orig = _d(p["expected_date"])
        new = _d(p["revised_expected_date"]) if p.get("revised_expected_date") else orig
        if Q.AS_OF > orig:
            ev["notes"].append(
                f"{po_id} is DELAYED: originally expected {orig}, "
                f"{(Q.AS_OF - orig).days} days overdue as of {Q.AS_OF}. "
                f"Revised delivery {new} ({(new - orig).days} days later than original).")
        if p.get("delay_reason") is None:
            ev["notes"].append("No delay reason is recorded for this PO.")

    # data-quality check: receipts that the PO lines do not reflect
    lines = {r[0]: (r[1], r[2]) for r in run_readonly(Q.Q_PO_LINES, {"po_id": po_id}).result_set}
    receipts = run_readonly(Q.Q_PO_RECEIPTS, {"po_id": po_id}).result_set
    rows = []
    for mid, mdate, qty, prod in receipts:
        rows.append([mid, mdate, prod, qty])
        _, received = lines.get(prod, (None, None))
        if received is not None and received < qty:
            ev["notes"].append(
                f"DATA CONFLICT: {mid} records a receipt of {qty} x {prod} on {mdate}, "
                f"but the PO line shows received_qty={received} and PO status is '{p.get('status')}'.")
    if rows:
        ev["tables"].append({"title": "Receipt movements for this PO",
                             "columns": ["movement", "date", "product", "qty"], "rows": rows})
    ev["tables"].append({"title": "PO lines",
                         "columns": ["product", "ordered_qty", "received_qty"],
                         "rows": [[k, v[0], v[1]] for k, v in lines.items()]})
    return ev


# ---------------- tool 2: which branches are at risk
def _latest_stock(rows):
    best = {}
    for pid, loc, snap_date, qty in rows:
        k = (pid, loc)
        if k not in best or snap_date > best[k][0]:
            best[k] = (snap_date, qty)
    out = {}
    for (pid, loc), (_, qty) in best.items():
        out.setdefault(pid, {})[loc] = qty
    return out


def get_po_impact(po_id: str):
    po_id = po_id.strip().upper()
    ev = _collect(run_readonly(Q.Q_EXPOSURE_PATHS, {"po_id": po_id}))
    po_res = run_readonly(Q.Q_PO, {"po_id": po_id})
    po = next((c.properties for r in po_res.result_set for c in r
               if isinstance(c, Node) and c.properties.get("po_id") == po_id), None)
    if not po:
        return _empty()
    if po.get("status") not in ("open", "partially_received"):
        ev["notes"].append(f"{po_id} is '{po.get('status')}', so there is no delivery risk.")
        return ev

    po_d = {k: po.get(k) for k in ("status", "expected_date", "revised_expected_date", "actual_delivery_date")}
    state, delay_days = classify_delay(po_d, Q.AS_OF)
    eta = effective_eta(po_d)
    wait = (eta - Q.AS_OF).days

    if state == "OVERDUE_UNCONFIRMED":
        ev["notes"].append(
            f"{po_id} is past its expected date {po_d['expected_date']} with no revised date, so a delay is "
            f"not confirmed and exposure cannot be estimated without an assumed delivery date.")
        return ev
    if state != "CONFIRMED_DELAY":
        ev["notes"].append(f"{po_id} shows no delay (state {state}, expected {eta}), so no stock risk is estimated.")
        return ev
    ev["notes"].append(
        f"{po_id} is a CONFIRMED_DELAY of {delay_days} days (revised delivery {eta}, {wait} days from {Q.AS_OF}).")

    supplier_id, wh_id = run_readonly(Q.Q_PO_CONTEXT, {"po_id": po_id}).result_set[0]
    exposed = run_readonly(Q.Q_EXPOSED_BRANCHES, {"po_id": po_id}).result_set
    pids = sorted({r[0] for r in exposed})
    as_of = Q.AS_OF.isoformat()
    since = (Q.AS_OF - timedelta(days=WINDOW_DAYS - 1)).isoformat()

    sales = run_readonly(Q.Q_SALES_28D, {"pids": pids, "since": since, "as_of": as_of}).result_set
    demand = {}
    for pid, br, qty, days in sales:
        demand.setdefault(pid, {})[br] = {"qty": qty, "days": days}
    stock = _latest_stock(run_readonly(Q.Q_STOCK_ALL, {"pids": pids, "as_of": as_of}).result_set)
    donor_rows = run_readonly(Q.Q_DONORS, {"pids": pids, "wh_id": wh_id}).result_set
    alt_rows = run_readonly(Q.Q_ALT_SUPPLIERS, {"pids": pids, "supplier_id": supplier_id}).result_set

    table_rows, results, recs_all = [], {}, []
    for pid in pids:
        branches, seen = [], set()
        for p, br, city, reorder, _stock in exposed:
            if p == pid and br not in seen:
                seen.add(br)
                branches.append({"branch_id": br, "branch_name": f"{br} ({city})", "reorder_level": reorder})
        pdemand = demand.get(pid, {})
        donors = []
        for p, dwh, dbranches in donor_rows:
            if p == pid:
                dd = sum(pdemand[b]["qty"] / WINDOW_DAYS for b in dbranches if b in pdemand)
                donors.append({"warehouse_id": dwh, "stock": stock.get(pid, {}).get(dwh), "daily_demand": dd})
        alts = [{"supplier_id": s, "lead_time_days": lt} for p, s, lt in alt_rows if p == pid and lt is not None]

        a = analyze_product(Q.AS_OF, eta, wh_id, branches, stock.get(pid, {}), pdemand)
        recs = build_recommendations(Q.AS_OF, eta, state, a, donors, alts)
        results[pid] = a
        for r in recs:
            r["product_id"] = pid
            recs_all.append(r)

        if not a["complete"]:
            ev["notes"].append(
                f"{pid}: not enough data to estimate risk (missing stock or fewer than 14 sales days for "
                f"{', '.join(a['missing'])}). Risk is UNKNOWN, not guessed.")
            continue
        w = a["warehouse"]
        ev["notes"].append(
            f"{pid}: {wh_id} holds {w['stock_on_hand']} units and its branches sell {w['daily_demand']:.0f}/day, "
            f"so cover is {w['cover_days']} days and replenishment stops on {w['stockout_date']} "
            f"(delivery {eta}) -> {w['risk_level']}.")
        for b in a["branches"]:
            table_rows.append([pid, b["branch_id"], b["stock_on_hand"], b["reorder_level"],
                               round(b["daily_demand"], 1), b["own_cover_days"], b["stockout_date"],
                               b["exposure_days"], b["unmet_units"],
                               "yes" if b["below_reorder_level"] else "no", b["risk_level"]])
            if b["risk_level"] == "AT_RISK":
                extra = "" if b["below_reorder_level"] else " It is above its reorder level, so a stock-level dashboard would not flag it."
                ev["notes"].append(
                    f"{pid} at {b['branch_id']}: AT_RISK, projected stockout {b['stockout_date']}, "
                    f"{b['exposure_days']} days before delivery, about {b['unmet_units']} units short.{extra}")
            elif b["risk_level"] == "OK":
                ev["notes"].append(f"{pid} at {b['branch_id']}: OK (stockout {b['stockout_date']}, after delivery).")
        if a["total_unmet_units"]:
            ev["notes"].append(f"{pid}: total estimated unmet demand about {a['total_unmet_units']} units.")
        exposed_ids = {b["branch_id"] for b in branches}
        for p, dwh, dbranches in donor_rows:
            if p == pid:
                others = [b for b in dbranches if b not in exposed_ids]
                if others:
                    ev["notes"].append(
                        f"{pid}: {', '.join(others)} carry it but are replenished by {dwh}; "
                        f"no path links them to {po_id}, so they are not exposed.")

    for r in recs_all:
        pid = r["product_id"]
        if r["action_type"] == "STOCK_TRANSFER":
            txt = (f"Transfer {r['total_units']} units of {pid} from {r['donor_warehouse_id']} "
                   f"({r['allocation']}); donor surplus {r['donor_surplus_units']}, arrival {r['arrival_date']}, "
                   f"donor keeps {r['donor_days_cover_after']} days of cover.")
        elif r["action_type"] == "EXPEDITE_PO":
            txt = (f"Ask the supplier to expedite {pid} on {po_id} to arrive by {r['must_arrive_by']} "
                   f"({r['days_earlier_than_eta']} days earlier than the revised date); feasibility is unknown.")
        else:
            txt = (f"Contingency: order {pid} from {r['supplier_id']} (lead time {r['lead_time_days']} days, "
                   f"arrival {r['arrival_date']}); about {r['residual_unmet_units']} units still unmet "
                   f"(reduces shortfall by {r['units_reduced']}).")
        ev["notes"].append(f"Recommendation R{r['rank']} ({pid}): {txt}")

    if recs_all or table_rows:
        ev["notes"].append(
            "Assumptions: demand stays at the 28-day average; branches are replenished from the warehouse until it "
            "runs out; the delivery date is when stock reaches branches; a transfer takes 2 days and a donor keeps "
            "14 days of cover. No cost data exists, so options are not ranked by cost.")
    ev["tables"].append({
        "title": f"Branch exposure to {po_id} (delivery {eta}, {wait} days)",
        "columns": ["product", "branch", "stock", "reorder_lvl", "sales/day", "own_cover_days",
                    "stockout_date", "exposure_days", "unmet_units", "below_reorder", "risk"],
        "rows": table_rows})
    ev["analysis"] = results
    ev["recommendations"] = recs_all
    return ev


# ---------------- tool 3: neighbors
def get_entity_neighbors(entity_id: str):
    eid, label, key = Q.resolve(entity_id)
    return _collect(run_readonly(Q.q_neighbors(label, key), {"id": eid}))


# ---------------- tool 4: path
def find_path(from_id: str, to_id: str):
    a, la, ka = Q.resolve(from_id)
    b, lb, kb = Q.resolve(to_id)
    return _collect(run_readonly(Q.q_path(la, ka, lb, kb), {"a": a, "b": b}))


TOOLS = {"get_purchase_order": get_purchase_order, "get_po_impact": get_po_impact,
         "get_entity_neighbors": get_entity_neighbors, "find_path": find_path}


def _decl(name, desc, **props):
    return {"name": name, "description": desc,
            "parameters": {"type": "object",
                           "properties": {k: {"type": "string"} for k in props},
                           "required": list(props)}}


TOOL_DECLS = [
    _decl("get_purchase_order",
          "Details of a purchase order (PO-xxxx): supplier, warehouse, product lines, receipts, "
          "derived delay and data conflicts. Use for status, delay and contents questions.", po_id=""),
    _decl("get_po_impact",
          "Which branches are exposed if a purchase order is late: stock, reorder level, "
          "days of cover vs delivery date, estimated unmet units and ranked recovery options. "
          "Use for impact and risk questions.", po_id=""),
    _decl("get_entity_neighbors",
          "Records directly connected to any entity ID (PO-, PRD-, SUP-, WH-, BR-, MOV-).", entity_id=""),
    _decl("find_path",
          "Shortest relationship path between two entity IDs.", from_id="", to_id=""),
]


def run_tool(name, args):
    if name not in TOOLS:
        raise ValueError(f"Unapproved tool: {name}")
    return TOOLS[name](**args)