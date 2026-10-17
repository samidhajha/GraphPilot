"""Runs one investigation and shapes the result into the API Contract response.
Cypher retrieves (tools.py), Python computes (analysis.py), the LLM only explains (llm.py)."""
import re
import uuid
from datetime import date

from app import llm, queries as Q, tools
from app.analysis import RISK_ORDER, SAFETY_DAYS, TRANSFER_DAYS

SUPPORTED_EXAMPLES = ["What is affected by the PO-1042 delay?", "Why is PRD-101 at risk at BR-02?"]
ID_KEYS = ("po_id", "supplier_id", "product_id", "warehouse_id", "branch_id", "movement_id")
REL_ORDER = ["HAS_ORDER", "CONTAINS", "DELIVERS_TO", "REPLENISHES", "STOCKS", "SUPPLIES"]
IMPACT_TOOLS = {"find_disruptions", "get_po_impact", "explain_risk"}
LOOKUP_TOOLS = {"get_po_impact", "get_purchase_order", "get_entity_neighbors"}


class GraphUnavailable(Exception):
    pass


# ------------------------------------------------------------------ routing
ID_RE = re.compile(r"\b(?:PO|PRD|SUP|WH|BR|MOV)-\d+\b", re.I)
TOPIC_RE = re.compile(r"\b(disruptions?|delays?|delayed|late|overdue|risk|at risk|impact|affected|affect|"
                      r"recovery|propagat\w*|supply chain|supplier|stock|inventory)\b", re.I)


def deterministic_route(question):
    """Used when the LLM is unavailable. Returns (tool, args) or None for unsupported questions."""
    ids = [m.group(0).upper() for m in ID_RE.finditer(question)]
    low = question.lower()
    prds = [i for i in ids if i.startswith("PRD-")]
    brs = [i for i in ids if i.startswith("BR-")]
    pos = [i for i in ids if i.startswith("PO-")]
    if prds and brs:
        return "explain_risk", {"product_id": prds[0], "branch_id": brs[0]}
    if pos:
        if re.search(r"\b(status|details?|contains?|lines?|receipts?)\b", low) and not re.search(
                r"\b(impact|affect\w*|risk|branch\w*)\b", low):
            return "get_purchase_order", {"po_id": pos[0]}
        return "get_po_impact", {"po_id": pos[0]}
    if len(ids) >= 2 and re.search(r"\b(path|connect\w*|link\w*|route)\b", low):
        return "find_path", {"from_id": ids[0], "to_id": ids[1]}
    if len(ids) == 1 and not TOPIC_RE.search(question):
        return "get_entity_neighbors", {"entity_id": ids[0]}
    if TOPIC_RE.search(question):
        return "find_disruptions", {}
    return None


def _skeleton(question, status, intent=None):
    return {
        "investigation_id": "inv-" + uuid.uuid4().hex[:8], "status": status, "intent": intent,
        "question": question, "as_of_date": Q.AS_OF.isoformat(), "explanation_mode": "deterministic_fallback",
        "risk_level": None, "answer": "", "root_cause": None,
        "verified_facts": [], "calculated_estimates": [], "assumptions": [],
        "affected_entities": [], "not_exposed_entities": [],
        "graph": {"nodes": [], "edges": []}, "evidence_paths": [], "evidence": [],
        "recommendations": [], "data_quality_issues": [], "limitations": [],
        "queries_executed": [], "clarification": None, "supported_examples": [],
    }


# ------------------------------------------------------------------ graph helpers
def _entity_id(props):
    for k in ID_KEYS:
        if k in props:
            return props[k]
    return None


def build_graph(ev, risk_by_id):
    gid, nodes = {}, []
    for n in ev["nodes"]:
        eid = _entity_id(n["props"])
        if not eid:
            continue
        gid[n["gid"]] = eid
        nodes.append({"entity_id": eid, "entity_type": (n["labels"] or [""])[0],
                      "label": n["props"].get("name") or eid, "risk_level": risk_by_id.get(eid)})
    nodes.sort(key=lambda x: x["entity_id"])
    edges = []
    for e in ev["edges"]:
        if e["src"] in gid and e["dst"] in gid:
            edges.append({"from": gid[e["src"]], "relationship": e["type"], "to": gid[e["dst"]],
                          "properties": dict(e["props"] or {})})
    edges.sort(key=lambda x: (REL_ORDER.index(x["relationship"]) if x["relationship"] in REL_ORDER else 99,
                              x["from"], x["to"]))
    out = []
    for i, e in enumerate(edges, 1):
        item = {"edge_id": f"e{i}", "from": e["from"], "relationship": e["relationship"], "to": e["to"]}
        if e["properties"]:
            item["properties"] = e["properties"]
        out.append(item)
    return {"nodes": nodes, "edges": out}


def build_paths(graph, ctx, analysis):
    wh = ctx["warehouse_id"]
    at_risk = {pid for pid, a in analysis.items() if a["risk_level"] == "AT_RISK"}
    exposed_br = {b["branch_id"] for a in analysis.values() for b in a["branches"]}
    buckets = {"P1": [], "P2": [], "P3": [], "P4": []}
    for e in graph["edges"]:
        rel, f, t = e["relationship"], e["from"], e["to"]
        if rel in ("HAS_ORDER", "DELIVERS_TO"):
            key = "P1"
        elif rel == "CONTAINS":
            key = "P1" if t in at_risk else "P2"
        elif rel == "REPLENISHES":
            key = "P1" if f == wh else "P4"
        elif rel == "STOCKS":
            key = ("P1" if t in at_risk else "P2") if f in exposed_br else "P4"
        elif rel == "SUPPLIES":
            key = "P3"
        else:
            continue
        buckets[key].append(e["edge_id"])
    risky = ", ".join(sorted(at_risk)) or "the ordered products"
    desc = {"P1": f"Disruption propagation: supplier to affected branches for {risky}",
            "P2": "Other products on the same order and their branches (checked, not at risk)",
            "P3": "Alternate supplier for the affected product",
            "P4": "Branches carrying the product outside the disruption path"}
    return [{"path_id": k, "description": desc[k], "edge_ids": v} for k, v in buckets.items() if v]


def _worst(levels):
    levels = [x for x in levels if x]
    return max(levels, key=lambda k: RISK_ORDER[k]) if levels else None


# ------------------------------------------------------------------ impact response
def _join(items):
    items = list(items)
    if len(items) <= 1:
        return "".join(items)
    return ", ".join(items[:-1]) + " and " + items[-1]


def assemble_impact(question, tool_name, ev, log):
    ctx, analysis = ev["context"], ev["analysis"]
    po_id, sup, wh, eta = ctx["po_id"], ctx["supplier_id"], ctx["warehouse_id"], date.fromisoformat(ctx["eta"])
    as_of = Q.AS_OF
    intent = "risk_root_cause" if tool_name == "explain_risk" else "disruption_impact"
    resp = _skeleton(question, "completed", intent)

    # risk per entity
    risk = {}
    for pid, a in analysis.items():
        risk[pid] = a["risk_level"]
        risk[wh] = _worst([risk.get(wh), a["warehouse"]["risk_level"]])
        for b in a["branches"]:
            risk[b["branch_id"]] = _worst([risk.get(b["branch_id"]), b["risk_level"]])
    graph = build_graph(ev, risk)
    names = {n["entity_id"]: n["label"] for n in graph["nodes"]}
    resp["graph"] = graph
    resp["evidence_paths"] = build_paths(graph, ctx, analysis)

    complete = [pid for pid, a in analysis.items() if a["complete"]]
    if not complete:
        resp["status"] = "insufficient_evidence"
    at_risk_products = [pid for pid in complete if analysis[pid]["risk_level"] == "AT_RISK"]
    main = (at_risk_products or complete or list(analysis))[0] if analysis else None

    # ---- evidence items
    items, qid = [], {}
    for q in log:
        qid.setdefault(q["template"], q["query_id"])

    def add_ev(kind, rtype, rid, template, **fields):
        eid = f"E{len(items) + 1}"
        item = {"evidence_id": eid, "kind": kind, "record_type": rtype, "record_id": rid}
        item.update(fields)
        item["query_id"] = qid.get(template)
        items.append(item)
        return eid

    e_po = add_ev("record", "PurchaseOrder", po_id, "po_record", properties=ctx["po"])
    e_line = {pid: add_ev("relationship", "CONTAINS", f"{po_id}|CONTAINS|{pid}", "po_lines",
                          properties={"ordered_qty": o, "received_qty": r}) for pid, o, r in ctx["lines"]}
    e_snap = {(s["product_id"], s["location_id"]): add_ev(
        "record", "InventorySnapshot", s["snapshot_id"], "stock_all",
        properties={"snapshot_date": s["snapshot_date"], "stock_on_hand": s["stock_on_hand"]}) for s in ctx["snapshots"]}
    e_dem = {}
    for pid, brs in ctx["demand"].items():
        e_dem[pid] = add_ev("aggregate", "Sale", None, "sales_28d",
                            description=f"Trailing 28-day average daily sales of {pid} per branch",
                            record_count=sum(v["days"] for v in brs.values()),
                            values={b: round(v["qty"] / 28, 1) for b, v in sorted(brs.items())})
    inbound_n = {}
    for po2, pid in ctx["other_inbound"]:
        inbound_n[pid] = inbound_n.get(pid, 0) + 1
    e_inb = {pid: add_ev("aggregate", "PurchaseOrder", None, "alt_inbound",
                         description=f"Other open purchase orders delivering {pid} to {wh}",
                         record_count=inbound_n.get(pid, 0), values={}) for pid in analysis}
    e_mov = {r[0]: add_ev("record", "StockMovement", r[0], "po_receipts",
                          properties={"movement_type": "receipt", "movement_date": r[1], "quantity": r[2]})
             for r in ctx["receipts"]}
    e_alt = {a["supplier_id"]: add_ev("record", "Supplier", a["supplier_id"], "alt_suppliers",
                                      properties={"lead_time_days": a["lead_time_days"]}) for a in ctx["alternates"]}
    resp["evidence"] = items

    # ---- affected entities
    sup_name = names.get(sup, sup)
    ent = [{"entity_type": "Supplier", "entity_id": sup, "name": sup_name, "role": "disruption_source", "risk_level": None},
           {"entity_type": "PurchaseOrder", "entity_id": po_id, "name": po_id, "role": "delayed_order", "risk_level": None}]
    for pid in analysis:
        a = analysis[pid]
        ent.append({"entity_type": "Product", "entity_id": pid, "name": names.get(pid, pid),
                    "role": "at_risk_product" if a["risk_level"] == "AT_RISK" else "exposed_not_at_risk",
                    "risk_level": a["risk_level"], "estimated_unmet_units": a["total_unmet_units"]})
    ent.append({"entity_type": "Warehouse", "entity_id": wh, "name": names.get(wh, wh),
                "role": "at_risk_location" if risk.get(wh) == "AT_RISK" else "exposed_not_at_risk",
                "risk_level": risk.get(wh)})
    branch_unmet, branch_ids = {}, []
    for a in analysis.values():
        for b in a["branches"]:
            if b["branch_id"] not in branch_ids:
                branch_ids.append(b["branch_id"])
            branch_unmet[b["branch_id"]] = branch_unmet.get(b["branch_id"], 0) + b["unmet_units"]
    ranked = sorted([b for b in branch_ids if risk.get(b) == "AT_RISK"], key=lambda b: (-branch_unmet[b], b))
    for b in sorted(branch_ids, key=lambda x: (ranked.index(x) if x in ranked else 99, x)):
        item = {"entity_type": "Branch", "entity_id": b, "name": names.get(b, b),
                "role": "at_risk_branch" if b in ranked else "exposed_not_at_risk",
                "risk_level": risk.get(b), "estimated_unmet_units": branch_unmet[b]}
        if b in ranked:
            item["rank"] = ranked.index(b) + 1
        ent.append(item)
    resp["affected_entities"] = ent

    # branches outside the path (replenished by another warehouse)
    not_exposed, seen = [], set(branch_ids)
    for e in graph["edges"]:
        if e["relationship"] == "REPLENISHES" and e["from"] != wh and e["to"] not in seen:
            seen.add(e["to"])
            not_exposed.append({"entity_type": "Branch", "entity_id": e["to"], "name": names.get(e["to"], e["to"]),
                                "reason": f"Replenished by {e['from']}; no path to {po_id}."})
    resp["not_exposed_entities"] = not_exposed
    resp["risk_level"] = _worst([a["risk_level"] for a in analysis.values()]) if analysis else None

    # ---- findings
    F, C = [], []
    fid = {}

    def addF(key, text, evidence_ids):
        i = f"F{len(F) + 1}"
        F.append({"finding_id": i, "statement": text, "evidence_ids": evidence_ids})
        fid[key] = i

    def addC(key, text, formula, inputs, evidence_ids, assumption_ids):
        i = f"C{len(C) + 1}"
        C.append({"finding_id": i, "statement": text, "formula": formula, "inputs": inputs,
                  "evidence_ids": evidence_ids, "assumption_ids": assumption_ids})
        fid[key] = i

    po = ctx["po"]
    reason = po.get("delay_reason")
    addF("po", f"{po_id}, placed with {sup}, is {po['status']} with original expected date {po['expected_date']} "
               f"and revised expected date {po['revised_expected_date']}. "
               + ("No delay reason is recorded." if reason is None else f"Recorded delay reason: {reason}."), [e_po])
    line_txt = _join(f"{pid} (ordered {o}, received {r})" for pid, o, r in ctx["lines"])
    addF("lines", f"{po_id} contains {line_txt}, and delivers to {wh}.", list(e_line.values()))

    donor_rec = next((r for r in ev["recommendations"] if r["action_type"] == "STOCK_TRANSFER"), None)
    alt_recs = [r for r in ev["recommendations"] if r["action_type"] == "ALTERNATE_SUPPLIER_ORDER"]
    if main and analysis[main]["complete"]:
        a = analysis[main]
        w = a["warehouse"]
        brs = [b["branch_id"] for b in a["branches"]]
        addF("wh", f"{wh} holds {w['stock_on_hand']} units of {main} as of {as_of} and replenishes {_join(sorted(brs))}.",
             [e_snap.get((main, wh))])
        by_id = sorted(a["branches"], key=lambda b: b["branch_id"])
        addF("branch_stock", f"Branch stock of {main} as of {as_of}: "
             + ", ".join(f"{b['branch_id']} {b['stock_on_hand']}" for b in by_id) + ".",
             [e_snap.get((main, b["branch_id"])) for b in by_id])
        below = [b["branch_id"] for b in by_id if b["below_reorder_level"]]
        addF("reorder", f"Reorder levels for {main}: " + ", ".join(f"{b['branch_id']} {b['reorder_level']}" for b in by_id)
             + (f". Only {_join(below)} {'is' if len(below) == 1 else 'are'} below the reorder level." if below
                else ". No branch is below its reorder level."), [])
        if inbound_n.get(main, 0) == 0:
            addF("inbound", f"No other open purchase order delivering {main} to {wh} was found in the dataset.", [e_inb[main]])
        else:
            addF("inbound", f"{inbound_n[main]} other open purchase order(s) deliver {main} to {wh}.", [e_inb[main]])
        if donor_rec:
            d = donor_rec["donor_warehouse_id"]
            donor_stock = next((s["stock_on_hand"] for s in ctx["snapshots"]
                                if s["product_id"] == main and s["location_id"] == d), None)
            if donor_stock is not None:
                addF("donor", f"{d} holds {donor_stock} units of {main} as of {as_of}.", [e_snap.get((main, d))])
        for r in alt_recs:
            addF("alt", f"{r['supplier_id']} also supplies {main}, with a typical lead time of {r['lead_time_days']} days.",
                 [e_alt.get(r["supplier_id"])])
        if not_exposed:
            addF("not_exposed", f"{_join(x['entity_id'] for x in not_exposed)} carry {main} but are replenished by another "
                                f"warehouse. No relationship path links them to {po_id}.", [])
    for f in F:
        f["evidence_ids"] = [x for x in f["evidence_ids"] if x]

    delay_days = ctx["delay_days"]
    overdue = (as_of - date.fromisoformat(po["expected_date"])).days
    addC("delay", f"{po_id}'s revised date is {delay_days} days after its original date. At {as_of} it is already "
                  f"{overdue} days past the original date.",
         "revised_expected_date - expected_date", {"revised_expected_date": po["revised_expected_date"],
                                                   "expected_date": po["expected_date"]}, [e_po], [])
    for pid in complete:
        a = analysis[pid]
        w = a["warehouse"]
        day_gap = (eta - date.fromisoformat(w["stockout_date"])).days
        when = (f"{day_gap} days before the revised ETA" if day_gap > 0 else f"{-day_gap} days after the revised ETA")
        daily = {b["branch_id"]: round(b["daily_demand"], 1) for b in a["branches"]}
        addC(f"dem_{pid}", f"Trailing 28-day demand for {pid} across {wh}'s branches is {w['daily_demand']:.0f} units/day "
                           f"({', '.join(f'{b} {v:g}' for b, v in sorted(daily.items()))}).",
             "sum(quantity over 28 days) / 28 per branch", daily, [e_dem.get(pid)], ["A1"])
        addC(f"wh_{pid}", f"{wh} has about {w['cover_days']} days of {pid} cover, so replenishment of its branches is "
                          f"projected to stop on {w['stockout_date']}, {when}.",
             f"{w['stock_on_hand']} / {w['daily_demand']:.0f} = {w['cover_days']} days; {as_of} + {int(w['cover_days'])} days",
             {"stock_on_hand": w["stock_on_hand"], "daily_demand": round(w["daily_demand"], 1)},
             [e_snap.get((pid, wh)), e_dem.get(pid)], ["A1"])
        if a["risk_level"] == "AT_RISK":
            parts = []
            for b in sorted(a["branches"], key=lambda x: x["branch_id"]):
                if b["exposure_days"] > 0:
                    parts.append(f"{b['branch_id']} is projected to run out on {b['stockout_date']} "
                                 f"({b['exposure_days']} day{'s' if b['exposure_days'] != 1 else ''} before the ETA, "
                                 f"about {b['unmet_units']} units short)")
                else:
                    parts.append(f"{b['branch_id']} is projected to run out on {b['stockout_date']}, after the ETA")
            addC(f"br_{pid}", "; ".join(parts) + ".",
                 "branch_stockout = warehouse_stockout + branch_stock / branch_demand; "
                 "unmet_units = max(0, ETA - branch_stockout) x branch_demand",
                 {b["branch_id"]: {"own_cover_days": b["own_cover_days"], "stockout_date": b["stockout_date"],
                                   "exposure_days": b["exposure_days"], "unmet_units": b["unmet_units"]}
                  for b in a["branches"]},
                 [e_snap.get((pid, b["branch_id"])) for b in a["branches"]] + [e_dem.get(pid)], ["A1", "A5", "A6"])
        else:
            addC(f"ok_{pid}", f"{pid} at {wh} has about {w['cover_days']} days of cover (stockout {w['stockout_date']}), "
                              f"and every branch stockout falls on or after {min(date.fromisoformat(b['stockout_date']) for b in a['branches'])}, "
                              f"after the ETA. It is not at risk.",
                 f"{w['stock_on_hand']} / {w['daily_demand']:.0f} = {w['cover_days']} days",
                 {"stock_on_hand": w["stock_on_hand"], "daily_demand": round(w["daily_demand"], 1)},
                 [e_snap.get((pid, wh)), e_dem.get(pid)], ["A1", "A5", "A6"])
    if donor_rec and main:
        dn = donor_rec["donor_warehouse_id"]
        addC("surplus", f"{dn} holds about {donor_rec['donor_surplus_units']} units of {main} above {SAFETY_DAYS} days of its own cover.",
             f"stock - (daily_demand x {SAFETY_DAYS})", {"donor_surplus_units": donor_rec["donor_surplus_units"]},
             [e_snap.get((main, dn))], ["A1", "A2"])
    for r in alt_recs:
        addC(f"alt_{r['supplier_id']}", f"An order placed with {r['supplier_id']} on {as_of} would arrive about {r['arrival_date']}, "
                                         f"leaving about {r['residual_unmet_units']} units unmet.",
             f"{as_of} + {r['lead_time_days']} days = {r['arrival_date']}", {"lead_time_days": r["lead_time_days"]},
             [e_alt.get(r["supplier_id"])], ["A4", "A6"])
    for c in C:
        c["evidence_ids"] = [x for x in c["evidence_ids"] if x]
    resp["verified_facts"], resp["calculated_estimates"] = F, C

    resp["assumptions"] = [
        {"assumption_id": "A1", "statement": "Demand continues at the trailing 28-day average."},
        {"assumption_id": "A2", "statement": f"A donor location keeps {SAFETY_DAYS} days of its own cover."},
        {"assumption_id": "A3", "statement": f"A transfer takes {TRANSFER_DAYS} days (not in data)."},
        {"assumption_id": "A4", "statement": "The alternate supplier's typical lead time holds and capacity is available (not in data)."},
        {"assumption_id": "A5", "statement": "Branches are replenished from the warehouse until it runs out; branch stock is not drawn down in the meantime."},
        {"assumption_id": "A6", "statement": "The ETA is treated as the date stock reaches branches; warehouse-to-branch transit is ignored."},
        {"assumption_id": "A7", "statement": "The donor warehouse can dispatch directly to the receiving branches (the lane is not modelled in the graph)."},
    ]

    # ---- recommendations
    recs_in = sorted(ev["recommendations"], key=lambda r: (-analysis[r["product_id"]]["total_unmet_units"], r["rank"]))
    recs = []
    for n, r in enumerate(recs_in, 1):
        pid = r["product_id"]
        base = {"recommendation_id": f"R{n}", "rank": n, "action_type": r["action_type"], "requires_human_approval": True}
        if r["action_type"] == "STOCK_TRANSFER":
            alloc = ", ".join(f"{v} to {k}" for k, v in sorted(r["allocation"].items()))
            first = min(date.fromisoformat(b["stockout_date"]) for b in analysis[pid]["branches"] if b["exposure_days"] > 0)
            base.update({
                "action": f"Transfer {r['total_units']} units of {pid} from {r['donor_warehouse_id']} ({alloc}).",
                "reason": f"{r['donor_warehouse_id']} has about {r['donor_surplus_units']} units above {SAFETY_DAYS} days of cover, and the "
                          f"transfer would arrive around {r['arrival_date']}, before the earliest projected stockout on {first}.",
                "supported_by": [fid.get("donor"), fid.get(f"br_{pid}"), fid.get("surplus")],
                "assumption_ids": ["A2", "A3", "A7"],
                "expected_effect": f"Covers the estimated {r['total_units']}-unit shortfall. {r['donor_warehouse_id']} keeps about "
                                   f"{r['donor_days_cover_after']} days of cover.",
                "residual_unmet_units": 0})
        elif r["action_type"] == "EXPEDITE_PO":
            base.update({
                "action": f"Ask {sup} to expedite the {pid} line of {po_id} to arrive by {r['must_arrive_by']}.",
                "reason": f"This addresses the cause. Arrival by {r['must_arrive_by']}, {r['days_earlier_than_eta']} days before the revised ETA, "
                          f"avoids all projected exposure, and each day gained reduces it. Supplier feasibility is unknown.",
                "supported_by": [fid.get("po"), fid.get("delay"), fid.get(f"br_{pid}")],
                "assumption_ids": ["A6"], "expected_effect": "Conditional on supplier agreement.",
                "residual_unmet_units": None})
        else:
            base.update({
                "action": f"Contingency: place an order for {pid} with {r['supplier_id']} (typical lead time {r['lead_time_days']} days).",
                "reason": f"Arrival around {r['arrival_date']} would reduce unmet demand by about {r['units_reduced']} units, leaving "
                          f"about {r['residual_unmet_units']}. It is a partial mitigation only.",
                "supported_by": [fid.get("alt"), fid.get(f"alt_{r['supplier_id']}")],
                "assumption_ids": ["A4", "A6"], "expected_effect": "Partial mitigation.",
                "residual_unmet_units": r["residual_unmet_units"]})
        base["supported_by"] = [x for x in base["supported_by"] if x]
        recs.append(base)
    resp["recommendations"] = recs

    # ---- data quality, root cause, limits
    dq = []
    for d in ev["data_quality"]:
        n = len(dq) + 1
        if d["type"] == "MISSING":
            dq.append({"issue_id": f"DQ{n}", "type": "MISSING", "statement": d["statement"], "evidence_ids": [e_po],
                       "impact_on_conclusion": d["impact_on_conclusion"]})
        else:
            dq.append({"issue_id": f"DQ{n}", "type": "CONTRADICTION", "statement": d["statement"],
                       "evidence_ids": [e_line.get(d["product_id"]), e_mov.get(d["movement_id"])],
                       "impact_on_conclusion": d["impact_on_conclusion"]})
    for d in dq:
        d["evidence_ids"] = [x for x in d["evidence_ids"] if x]
    resp["data_quality_issues"] = dq

    if complete:
        brs_risk = list(ranked)
        support = "supported"
        why = " The reason for the supplier delay is not recorded." if reason is None else ""
        resp["root_cause"] = {
            "statement": f"The confirmed {delay_days}-day delay of {po_id} ({sup}) is the supported driver of the projected "
                         f"{main} shortfall at {_join(sorted(brs_risk))}.{why}" if brs_risk else
                         f"{po_id} ({sup}) is confirmed delayed by {delay_days} days, but no branch is projected to run out before delivery.{why}",
            "support_level": support,
            "supporting_finding_ids": [fid[k] for k in ("po", "lines", "wh", "delay", f"wh_{main}", f"br_{main}") if k in fid]}
    else:
        resp["root_cause"] = {"statement": f"{po_id} is confirmed delayed, but there is not enough stock or sales data to estimate impact.",
                              "support_level": "insufficient_evidence", "supporting_finding_ids": [fid["po"]] if "po" in fid else []}
    lim = ["Estimates assume constant demand and no transit time (A1, A6).",
           "Transfer time, the transfer lane and alternate-supplier capacity are assumptions, not data (A3, A4, A7).",
           "No cost or margin data exists, so recommendations are not ranked by cost.",
           f"Findings are based on synthetic data as of {as_of}."]
    if reason is None:
        lim.insert(0, f"The cause of the supplier delay is not recorded for {po_id}.")
    for pid, a in analysis.items():
        if not a["complete"]:
            lim.append(f"{pid}: stock cover was not estimated because data is missing for {', '.join(a['missing'])}.")
    resp["limitations"] = lim
    return resp


def deterministic_impact_answer(resp, ev):
    ctx, analysis = ev["context"], ev["analysis"]
    po_id, sup = ctx["po_id"], ctx["supplier_id"]
    sup_name = next((e["name"] for e in resp["affected_entities"] if e["entity_id"] == sup), sup)
    reason = ctx["po"].get("delay_reason")
    parts = [f"{po_id} from {sup_name} ({sup}) is confirmed delayed by {ctx['delay_days']} days (revised ETA {ctx['eta']}); "
             + ("the reason for the delay is not recorded." if reason is None else f"the recorded reason is: {reason}.")]
    ranked = [e for e in resp["affected_entities"] if e["role"] == "at_risk_branch"]
    risky = [p for p, a in analysis.items() if a["risk_level"] == "AT_RISK"]
    if risky:
        main = risky[0]
        w = analysis[main]["warehouse"]
        short = _join(f"{e['entity_id']} (about {e['estimated_unmet_units']} units short)" for e in ranked)
        parts.append(f"{w['warehouse_id']} is projected to run out of {main} on {w['stockout_date']}, putting {short} at risk.")
        above = [b["branch_id"] for b in analysis[main]["branches"] if b["risk_level"] == "AT_RISK" and not b["below_reorder_level"]]
        if above:
            parts.append(f"{_join(above)} {'is' if len(above) == 1 else 'are'} above its reorder level, so a stock-level dashboard would not flag it.")
        safe = [p for p, a in analysis.items() if a["risk_level"] == "OK"]
        if safe:
            parts.append(f"{_join(safe)} shares the order but {'is' if len(safe) == 1 else 'are'} not at risk.")
        ne = [e["entity_id"] for e in resp["not_exposed_entities"]]
        if ne:
            parts.append(f"{_join(ne)} {'is' if len(ne) == 1 else 'are'} not exposed.")
        if resp["recommendations"]:
            r = resp["recommendations"][0]
            parts.append(f"The recommended action is: {r['action']} {r['expected_effect']}")
    else:
        parts.append("No product or branch is projected to run out before the revised delivery date.")
    for d in resp["data_quality_issues"]:
        if d["type"] == "CONTRADICTION":
            parts.append("One record conflict is noted below: " + d["statement"] + " " + d["impact_on_conclusion"])
    return " ".join(parts)


# ------------------------------------------------------------------ generic response
def assemble_generic(question, tool_name, ev, log):
    intent = "disruption_impact" if tool_name in ("get_purchase_order", "find_disruptions") else None
    resp = _skeleton(question, "completed", intent)
    resp["graph"] = build_graph(ev, {})
    g = resp["graph"]
    ent_by_gid = {}
    for n in ev["nodes"]:
        ent_by_gid[n["gid"]] = _entity_id(n["props"])
    edge_ids = {(e["from"], e["relationship"], e["to"]): e["edge_id"] for e in g["edges"]}
    for i, p in enumerate(ev["paths"], 1):
        ids = [ent_by_gid.get(x) for x in p["node_gids"]]
        eids = [edge_ids.get((a, r, b)) or edge_ids.get((b, r, a)) for a, b, r in zip(ids, ids[1:], p["rels"])]
        resp["evidence_paths"].append({"path_id": f"P{i}", "description": " -> ".join(str(x) for x in ids),
                                       "edge_ids": [x for x in eids if x]})
    qid = {}
    for q in log:
        qid.setdefault(q["template"], q["query_id"])
    for n in ev["nodes"]:
        eid = _entity_id(n["props"])
        if eid:
            resp["evidence"].append({"evidence_id": f"E{len(resp['evidence']) + 1}", "kind": "record",
                                     "record_type": (n["labels"] or [""])[0], "record_id": eid,
                                     "properties": n["props"], "query_id": log[-1]["query_id"] if log else None})
    for t in ev["tables"]:
        resp["evidence"].append({"evidence_id": f"E{len(resp['evidence']) + 1}", "kind": "aggregate",
                                 "record_type": "Table", "record_id": None, "description": t["title"],
                                 "record_count": len(t["rows"]), "values": {"columns": t["columns"], "rows": t["rows"]},
                                 "query_id": log[-1]["query_id"] if log else None})
    for note in ev["notes"]:
        low = note.lower()
        if "no delay reason is recorded" in low:
            resp["data_quality_issues"].append({"issue_id": f"DQ{len(resp['data_quality_issues']) + 1}", "type": "MISSING",
                                                "statement": note, "evidence_ids": [],
                                                "impact_on_conclusion": "The agent reports that the order is late, not why."})
        elif "data conflict" in low:
            resp["data_quality_issues"].append({"issue_id": f"DQ{len(resp['data_quality_issues']) + 1}", "type": "CONTRADICTION",
                                                "statement": note, "evidence_ids": [], "impact_on_conclusion": "See the statement."})
        else:
            resp["verified_facts"].append({"finding_id": f"F{len(resp['verified_facts']) + 1}", "statement": note, "evidence_ids": []})
    resp["limitations"] = [f"Findings are based on synthetic data as of {Q.AS_OF}."]
    return resp


# ------------------------------------------------------------------ answers
NUM_RE = re.compile(r"\d+(?:\.\d+)?")


def numbers_supported(answer, facts_text):
    allowed = set(NUM_RE.findall(facts_text))
    allowed |= {n.split(".")[0] for n in list(allowed) if n.endswith(".0")}
    return all(n in allowed or n.rstrip("0").rstrip(".") in allowed for n in NUM_RE.findall(answer))


CAUSE_RE = re.compile(r"\b(because of|due to|caused by|owing to)\s+(?:the\s+|a\s+|an\s+)?"
                      r"(?:supplier|vendor|apex|sup-\d+|production|manufactur\w*|quality|logistics|transport\w*|"
                      r"labou?r|staff\w*|machine\w*|equipment|raw material\w*|material\w*)", re.I)
CAUSE_WORDS = re.compile(r"\b(strike|weather|storm|rain|flood\w*|customs|fire|breakdown|factory|"
                         r"holiday|congestion|accident)\b", re.I)


def invents_cause(answer, ev):
    """A cause must never be stated unless a delay reason is recorded in the data."""
    reason = (ev.get("context") or {}).get("po", {}).get("delay_reason")
    if reason:
        return False
    return bool(CAUSE_RE.search(answer) or CAUSE_WORDS.search(answer))


def facts_for_llm(resp, ev, det_answer):
    lines = [det_answer, ""]
    lines += [f"- {f['statement']}" for f in resp["verified_facts"]]
    lines += [f"- {c['statement']}" for c in resp["calculated_estimates"]]
    lines += [f"- Recommendation {r['recommendation_id']}: {r['action']} {r['reason']}" for r in resp["recommendations"]]
    lines += [f"- Data issue: {d['statement']}" for d in resp["data_quality_issues"]]
    lines += [f"- {n}" for n in ev.get("notes", []) if not resp["calculated_estimates"]]
    return "\n".join(lines)


# ------------------------------------------------------------------ main entry
def _is_graph_error(exc):
    return type(exc).__module__.split(".")[0] in ("redis", "falkordb") or isinstance(exc, (ConnectionError, TimeoutError))


def _clarify(question, reason, answer, options=None):
    r = _skeleton(question, "needs_clarification")
    r["answer"] = answer
    r["clarification"] = {"reason": reason, "options": options or []}
    r["supported_examples"] = SUPPORTED_EXAMPLES
    return r


def investigate(question):
    question = (question or "").strip()
    log = tools.start_query_log()
    llm_ok = llm.is_configured()

    choice, router_failed = None, False
    if llm_ok:
        try:
            choice = llm.choose_tool(question, tools.TOOL_DECLS)
        except Exception as exc:
            print("ROUTER ERROR:", repr(exc), flush=True)
            router_failed = True
    if not llm_ok or router_failed:
        choice = deterministic_route(question)
    if choice is None:
        r = _skeleton(question, "unsupported_question")
        r["answer"] = "GraphPilot investigates supplier disruptions and stock risk. It cannot answer that question."
        r["supported_examples"] = SUPPORTED_EXAMPLES
        r["explanation_mode"] = "deterministic_fallback" if (router_failed or not llm_ok) else "llm"
        return r

    name, args = choice
    try:
        ev = tools.run_tool(name, args)
    except (ValueError, LookupError, TypeError) as exc:
        return _clarify(question, "ENTITY_NOT_FOUND", f"I could not find what you referred to: {exc}")
    except Exception as exc:
        if _is_graph_error(exc):
            raise GraphUnavailable(str(exc)) from exc
        raise

    log = list(tools.QUERY_LOG.get() or [])
    if not tools.has_evidence(ev):
        # no records: the LLM is never asked to guess
        if name in LOOKUP_TOOLS:
            ident = next(iter(args.values()), "that entity")
            return _clarify(question, "ENTITY_NOT_FOUND", f"No records were found for {ident}. Check the ID and try again.")
        r = _skeleton(question, "insufficient_evidence", "risk_root_cause" if name == "explain_risk" else None)
        r["answer"] = "No evidence was found in the graph for that question, so GraphPilot will not guess."
        r["limitations"] = ["No matching records were retrieved.", f"Findings are based on synthetic data as of {Q.AS_OF}."]
        r["queries_executed"] = log
        r["supported_examples"] = SUPPORTED_EXAMPLES
        r["explanation_mode"] = "deterministic_fallback" if (router_failed or not llm_ok) else "llm"
        return r

    if "context" in ev and "analysis" in ev:
        resp = assemble_impact(question, name, ev, log)
        det = deterministic_impact_answer(resp, ev)
    else:
        resp = assemble_generic(question, name, ev, log)
        det = " ".join(ev["notes"]) or f"Found {len(resp['graph']['nodes'])} connected records."
        if not ev["notes"] and ev["paths"]:
            det = "Path found: " + resp["evidence_paths"][0]["description"] + "."
        if name == "find_path" and not ev["paths"]:
            resp["status"] = "insufficient_evidence"
            det = "No relationship path was found between those two entities."
    resp["queries_executed"] = log
    resp["answer"] = det
    resp["explanation_mode"] = "deterministic_fallback"

    if llm_ok and not router_failed:
        try:
            facts = facts_for_llm(resp, ev, det)
            text = llm.write_answer(question, facts)
            nums_ok = numbers_supported(text, facts + " " + question)
            cause_bad = invents_cause(text, ev)
            print("GUARD: numbers_ok =", nums_ok, "| invents_cause =", cause_bad, flush=True)
            if nums_ok and not cause_bad:
                resp["answer"], resp["explanation_mode"] = text, "llm"
        except Exception as exc:
            print("ANSWER ERROR:", repr(exc), flush=True)  # keep the deterministic answer
    return resp


# ------------------------------------------------------------------ entity endpoint (P1)
def entity_detail(entity_id, limit=25):
    eid, label, key = Q.resolve(entity_id)
    ev = tools.get_entity_neighbors(eid)
    by_gid = {n["gid"]: n for n in ev["nodes"]}
    me = next((n for n in ev["nodes"] if n["props"].get(key) == eid), None)
    if not me:
        return None
    conns = []
    for e in ev["edges"]:
        if e["src"] == me["gid"] or e["dst"] == me["gid"]:
            outgoing = e["src"] == me["gid"]
            other = by_gid.get(e["dst"] if outgoing else e["src"])
            if not other:
                continue
            conns.append({"relationship": e["type"], "direction": "outgoing" if outgoing else "incoming",
                          "entity_id": _entity_id(other["props"]), "entity_type": (other["labels"] or [""])[0],
                          "name": other["props"].get("name") or _entity_id(other["props"]),
                          "relationship_properties": dict(e["props"] or {})})
    return {"entity_id": eid, "entity_type": label, "properties": me["props"],
            "connections": conns[:limit], "record_counts": {}, "truncated": len(conns) > limit}