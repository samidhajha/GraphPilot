import contextvars
from datetime import date, timedelta

from falkordb import Node, Edge, Path

from app import queries as Q
from app.analysis import (WINDOW_DAYS, analyze_product, build_recommendations,
                          classify_delay, effective_eta)
from app.graph_client import run_readonly

# ---- per-request log of executed queries (feeds `queries_executed`)
QUERY_LOG = contextvars.ContextVar("query_log", default=None)


def start_query_log():
    log = []
    QUERY_LOG.set(log)
    return log


def _run(name, query, params=None):
    params = params or {}
    result = run_readonly(query, params)
    log = QUERY_LOG.get()
    if log is not None:
        log.append({"query_id": f"Q{len(log) + 1}", "template": name, "params": params,
                    "row_count": len(result.result_set), "read_only": True})
    return result


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


def _merge(ev, other):
    have_n = {n["gid"] for n in ev["nodes"]}
    ev["nodes"] += [n for n in other["nodes"] if n["gid"] not in have_n]
    have_e = {(e["src"], e["dst"], e["type"]) for e in ev["edges"]}
    ev["edges"] += [e for e in other["edges"] if (e["src"], e["dst"], e["type"]) not in have_e]


def has_evidence(ev):
    return bool(ev["nodes"] or ev["edges"] or ev["tables"])


def _d(s):
    return date.fromisoformat(s)


# ---------------- tool 1: PO details
def get_purchase_order(po_id: str):
    po_id = po_id.strip().upper()
    ev = _collect(_run("po_record", Q.Q_PO, {"po_id": po_id}))
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
    lines = {r[0]: (r[1], r[2]) for r in _run("po_lines", Q.Q_PO_LINES, {"po_id": po_id}).result_set}
    receipts = _run("po_receipts", Q.Q_PO_RECEIPTS, {"po_id": po_id}).result_set
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
    for pid, loc, snap_date, qty, snap_id in rows:
        k = (pid, loc)
        if k not in best or snap_date > best[k][0]:
            best[k] = (snap_date, qty, snap_id)
    stock, meta = {}, []
    for (pid, loc), (snap_date, qty, snap_id) in sorted(best.items()):
        stock.setdefault(pid, {})[loc] = qty
        meta.append({"snapshot_id": snap_id, "product_id": pid, "location_id": loc,
                     "snapshot_date": snap_date, "stock_on_hand": qty})
    return stock, meta


def get_po_impact(po_id: str):
    po_id = po_id.strip().upper()
    ev = _collect(_run("exposure_paths", Q.Q_EXPOSURE_PATHS, {"po_id": po_id}))
    po_res = _run("po_record", Q.Q_PO, {"po_id": po_id})
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

    ctx_rows = _run("po_context", Q.Q_PO_CONTEXT, {"po_id": po_id}).result_set
    if not ctx_rows:
        ev["notes"].append(f"{po_id} has no linked supplier or destination warehouse, so impact cannot be traced.")
        return ev
    supplier_id, wh_id = ctx_rows[0]
    exposed = _run("exposed_branches", Q.Q_EXPOSED_BRANCHES, {"po_id": po_id}).result_set
    pids = sorted({r[0] for r in exposed})
    as_of = Q.AS_OF.isoformat()
    since = (Q.AS_OF - timedelta(days=WINDOW_DAYS - 1)).isoformat()

    sales = _run("sales_28d", Q.Q_SALES_28D, {"pids": pids, "since": since, "as_of": as_of}).result_set
    demand = {}
    for pid, br, qty, days in sales:
        demand.setdefault(pid, {})[br] = {"qty": qty, "days": days}
    stock, snap_meta = _latest_stock(_run("stock_all", Q.Q_STOCK_ALL, {"pids": pids, "as_of": as_of}).result_set)
    donor_rows = _run("donors", Q.Q_DONORS, {"pids": pids, "wh_id": wh_id}).result_set
    alt_rows = _run("alt_suppliers", Q.Q_ALT_SUPPLIERS, {"pids": pids, "supplier_id": supplier_id}).result_set
    lines = [list(r) for r in _run("po_lines", Q.Q_PO_LINES, {"po_id": po_id}).result_set]
    receipts = [list(r) for r in _run("po_receipts", Q.Q_PO_RECEIPTS, {"po_id": po_id}).result_set]
    inbound = _run("alt_inbound", Q.Q_ALT_INBOUND, {"pids": pids, "wh_id": wh_id, "po_id": po_id}).result_set

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
                day_word = "day" if b["exposure_days"] == 1 else "days"
                ev["notes"].append(
                    f"{pid} at {b['branch_id']}: AT_RISK, projected stockout {b['stockout_date']}, "
                    f"{b['exposure_days']} {day_word} before delivery, about {b['unmet_units']} units short.{extra}")
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

    # data-quality findings
    dq = []
    if po.get("delay_reason") is None:
        dq.append({"type": "MISSING", "statement": f"{po_id} has no recorded delay reason.",
                   "impact_on_conclusion": "The agent reports that the order is late, not why."})
        ev["notes"].append("No delay reason is recorded for this PO.")
    line_received = {ln[0]: ln[2] for ln in lines}
    for mid, mdate, qty, prod in receipts:
        rq = line_received.get(prod)
        if rq is not None and rq < qty:
            dq.append({"type": "CONTRADICTION", "movement_id": mid, "product_id": prod,
                       "statement": (f"{mid} records a receipt of {qty} units of {prod} for {po_id} on {mdate}, but the "
                                     f"{prod} line shows received_qty {rq} and the PO status is {po.get('status')}."),
                       "impact_on_conclusion": f"None for the other products. {prod} is judged on its own stock and demand."})
            ev["notes"].append(
                f"DATA CONFLICT: {mid} records a receipt of {qty} x {prod} on {mdate}, but the PO line shows "
                f"received_qty={rq} and PO status is '{po.get('status')}'.")

    # graph pieces for the UI
    for name, q, params in (("impact_graph", Q.Q_IMPACT_GRAPH, {"po_id": po_id}),
                            ("alt_graph", Q.Q_ALT_GRAPH, {"pids": pids, "supplier_id": supplier_id}),
                            ("other_wh_graph", Q.Q_OTHER_WH_GRAPH, {"pids": pids, "wh_id": wh_id})):
        _merge(ev, _collect(_run(name, q, params)))

    ev["tables"].append({
        "title": f"Branch exposure to {po_id} (delivery {eta}, {wait} days)",
        "columns": ["product", "branch", "stock", "reorder_lvl", "sales/day", "own_cover_days",
                    "stockout_date", "exposure_days", "unmet_units", "below_reorder", "risk"],
        "rows": table_rows})
    ev["analysis"] = results
    ev["recommendations"] = recs_all
    ev["data_quality"] = dq
    ev["context"] = {
        "po_id": po_id, "supplier_id": supplier_id, "warehouse_id": wh_id,
        "delay_state": state, "delay_days": delay_days, "eta": eta.isoformat(),
        "po": {k: po.get(k) for k in ("po_id", "order_date", "expected_date", "revised_expected_date",
                                       "actual_delivery_date", "status", "delay_reason")},
        "lines": lines, "snapshots": snap_meta, "demand": demand, "receipts": receipts,
        "other_inbound": [[r[0], r[1]] for r in inbound],
        "alternates": [{"supplier_id": s, "product_id": p, "lead_time_days": lt} for p, s, lt in alt_rows],
    }
    return ev


# ---------------- tool 3: find the disruption when nothing is named
def find_disruptions():
    rows = _run("all_pos", Q.Q_ALL_POS, {}).result_set
    ev = _empty()
    if not rows:
        return ev
    table, confirmed, overdue, clear = [], [], [], []
    for po_id, status, exp, rev, act, sup_id, sup_name in rows:
        d = {"status": status, "expected_date": exp, "revised_expected_date": rev, "actual_delivery_date": act}
        state, days = classify_delay(d, Q.AS_OF)
        table.append([po_id, sup_id, status, exp, rev, state, days])
        if status in ("open", "partially_received") and state == "CONFIRMED_DELAY":
            confirmed.append((po_id, days))
        elif state == "OVERDUE_UNCONFIRMED":
            overdue.append(po_id)
        else:
            clear.append((po_id, state))

    if not confirmed:
        ev["notes"].append("No confirmed supply disruption was found among the purchase orders.")
        if overdue:
            ev["notes"].append("Overdue but unconfirmed (no revised date or reason): " + ", ".join(overdue) + ".")
        ev["tables"].append({"title": "Purchase orders and delay state",
                             "columns": ["po", "supplier", "status", "expected", "revised", "delay_state", "delay_days"],
                             "rows": table})
        return ev

    # several confirmed delays: investigate the one with the largest estimated unmet demand
    best = None
    for po_id, _ in confirmed:
        impact = get_po_impact(po_id)
        unmet = sum(a["total_unmet_units"] for a in impact.get("analysis", {}).values())
        if best is None or unmet > best[0]:
            best = (unmet, impact)
    impact = best[1]
    impact["notes"].insert(0, "Discovered by scanning all purchase orders: confirmed delayed order(s) "
                              + ", ".join(f"{p} ({d} days)" for p, d in confirmed) + ".")
    if clear:
        impact["notes"].insert(1, "Checked and not flagged: " + ", ".join(f"{p} ({s})" for p, s in clear) + ".")
    if overdue:
        impact["notes"].insert(2, "Overdue but unconfirmed: " + ", ".join(overdue) + ".")
    impact["tables"].insert(0, {"title": "Purchase orders and delay state",
                                "columns": ["po", "supplier", "status", "expected", "revised", "delay_state", "delay_days"],
                                "rows": table})
    return impact


# ---------------- tool 4: why is this product at risk at this branch
def explain_risk(product_id: str, branch_id: str):
    pid, bid = product_id.strip().upper(), branch_id.strip().upper()
    Q.resolve(pid), Q.resolve(bid)
    exists = _run("entity_check", Q.Q_BRANCH_PRODUCT_EXISTS, {"pid": pid, "bid": bid}).result_set
    if not exists or not exists[0][0]:
        raise LookupError(f"{pid} or {bid} was not found in the graph.")
    rows = _run("upstream_pos", Q.Q_UPSTREAM_POS, {"pid": pid, "bid": bid}).result_set
    if not rows:
        ev = _empty()
        ev["notes"].append(f"No open purchase order delivers {pid} to the warehouse that replenishes {bid}, "
                           f"so no supply disruption explains a risk there.")
        ev["tables"].append({"title": "Open purchase orders upstream", "columns": ["po"], "rows": []})
        return ev
    chosen = rows[0][0]
    for po_id, status, exp, rev, act in rows:
        d = {"status": status, "expected_date": exp, "revised_expected_date": rev, "actual_delivery_date": act}
        if classify_delay(d, Q.AS_OF)[0] == "CONFIRMED_DELAY":
            chosen = po_id
            break
    ev = get_po_impact(chosen)
    if "context" in ev:
        ev["context"]["focus"] = {"product_id": pid, "branch_id": bid}
        a = ev["analysis"].get(pid)
        row = next((b for b in a["branches"] if b["branch_id"] == bid), None) if a and a["complete"] else None
        if row:
            ctx = ev["context"]
            ev["notes"].insert(0, (
                f"{bid} is replenished by {ctx['warehouse_id']}, which receives {pid} on {chosen} from "
                f"{ctx['supplier_id']}. {pid} at {bid}: {row['risk_level']}, projected stockout "
                f"{row['stockout_date']}, about {row['unmet_units']} units short."))
    return ev


# ---------------- tool 5: neighbors
def get_entity_neighbors(entity_id: str):
    eid, label, key = Q.resolve(entity_id)
    return _collect(_run("neighbors", Q.q_neighbors(label, key), {"id": eid}))


# ---------------- tool 6: path
def find_path(from_id: str, to_id: str):
    a, la, ka = Q.resolve(from_id)
    b, lb, kb = Q.resolve(to_id)
    return _collect(_run("path", Q.q_path(la, ka, lb, kb), {"a": a, "b": b}))


TOOLS = {"find_disruptions": find_disruptions, "explain_risk": explain_risk,
         "get_purchase_order": get_purchase_order, "get_po_impact": get_po_impact,
         "get_entity_neighbors": get_entity_neighbors, "find_path": find_path}


def _decl(name, desc, **props):
    d = {"name": name, "description": desc}
    if props:
        d["parameters"] = {"type": "OBJECT",
                           "properties": {k: {"type": "STRING"} for k in props},
                           "required": list(props)}
    return d


TOOL_DECLS = [
    _decl("find_disruptions",
          "Scan all purchase orders, find the confirmed supplier disruption and return the impact on "
          "products and branches, the propagation path and ranked recovery options. Use when the question "
          "asks about a supplier disruption, delays or recovery WITHOUT naming a specific purchase order."),
    _decl("explain_risk",
          "Explain why one product is at risk at one branch by tracing upstream to the warehouse, purchase "
          "order and supplier. Use for 'why is PRD-xxx at risk at BR-xx' questions.",
          product_id="", branch_id=""),
    _decl("get_po_impact",
          "Impact of one named purchase order (PO-xxxx) being late: affected branches, stock cover, "
          "estimated unmet units and ranked recovery options.", po_id=""),
    _decl("get_purchase_order",
          "Details of one purchase order (PO-xxxx): supplier, warehouse, product lines, receipts, "
          "derived delay and data conflicts. Use for status and contents questions.", po_id=""),
    _decl("get_entity_neighbors",
          "Records directly connected to any entity ID (PO-, PRD-, SUP-, WH-, BR-, MOV-).", entity_id=""),
    _decl("find_path",
          "Shortest relationship path between two entity IDs.", from_id="", to_id=""),
]


def run_tool(name, args):
    if name not in TOOLS:
        raise ValueError(f"Unapproved tool: {name}")
    return TOOLS[name](**(args or {}))