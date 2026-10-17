"""Seed the GraphPilot demo graph (schema + core demo scenario docs).

Run from the project folder:  python backend/seed.py
Safe to run again: the graph is deleted and rebuilt each time.
"""
import os
from datetime import date, timedelta

from falkordb import FalkorDB

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

HOST = os.getenv("FALKORDB_HOST", "localhost")
PORT = int(os.getenv("FALKORDB_PORT", "6379"))
GRAPH = os.getenv("GRAPH_NAME", "graphpilot")

AS_OF = date(2026, 10, 15)  # fixed demo date, not the system clock
SNAP_DATE = AS_OF.isoformat()

# ---------------------------------------------------------------- nodes
suppliers = [
    {"supplier_id": "SUP-003", "name": "Apex Components Ltd", "lead_time_days": 22, "reliability_score": 0.72},
    {"supplier_id": "SUP-005", "name": "SUP-005", "lead_time_days": 9, "reliability_score": 0.90},
]

products = [
    {"product_id": "PRD-101", "name": "USB-C Fast Charger 65W", "category": "Chargers"},
    {"product_id": "PRD-102", "name": "Wireless Earbuds", "category": "Audio"},
    {"product_id": "PRD-103", "name": "Power Bank 10000mAh", "category": "Chargers"},
]

warehouses = [
    {"warehouse_id": "WH-01", "name": "North Warehouse"},
    {"warehouse_id": "WH-02", "name": "South Warehouse"},
]

branches = [
    {"branch_id": "BR-01", "name": "North Branch 1", "city": "Delhi"},
    {"branch_id": "BR-02", "name": "North Branch 2", "city": "Noida"},
    {"branch_id": "BR-03", "name": "North Branch 3", "city": "Gurugram"},
    {"branch_id": "BR-04", "name": "South Branch 1", "city": "Chennai"},
    {"branch_id": "BR-05", "name": "South Branch 2", "city": "Bengaluru"},
]

# status is lifecycle only; "delayed" is derived later in Python
purchase_orders = [
    {
        "po_id": "PO-1042", "supplier": "SUP-003", "warehouse": "WH-01",
        "order_date": "2026-09-20", "expected_date": "2026-10-12",
        "revised_expected_date": "2026-10-26", "actual_delivery_date": None,
        "status": "open", "delay_reason": None,
        "lines": [("PRD-101", 800, 0), ("PRD-102", 600, 0)],
    },
    {  # distractor: already received on time
        "po_id": "PO-1031", "supplier": "SUP-005", "warehouse": "WH-01",
        "order_date": "2026-08-20", "expected_date": "2026-09-10",
        "revised_expected_date": None, "actual_delivery_date": "2026-09-09",
        "status": "received", "delay_reason": None,
        "lines": [("PRD-103", 300, 300)],
    },
    {  # distractor: open but not yet due
        "po_id": "PO-1050", "supplier": "SUP-005", "warehouse": "WH-02",
        "order_date": "2026-10-10", "expected_date": "2026-10-30",
        "revised_expected_date": None, "actual_delivery_date": None,
        "status": "open", "delay_reason": None,
        "lines": [("PRD-103", 150, 0)],
    },
]

# ---------------------------------------------------------- relationships
supplies = [("SUP-003", "PRD-101"), ("SUP-003", "PRD-102"), ("SUP-005", "PRD-101"), ("SUP-005", "PRD-103")]

replenishes = [
    ("WH-01", "BR-01"), ("WH-01", "BR-02"), ("WH-01", "BR-03"),
    ("WH-02", "BR-04"), ("WH-02", "BR-05"),
]

# (branch, product, reorder_level). PRD-102 reorder levels are not in the docs (assumed).
stocks = [
    ("BR-01", "PRD-101", 50), ("BR-02", "PRD-101", 40), ("BR-03", "PRD-101", 30),
    ("BR-04", "PRD-101", 20), ("BR-05", "PRD-101", 15),
    ("BR-01", "PRD-102", 15), ("BR-02", "PRD-102", 10), ("BR-03", "PRD-102", 8),
]

# (product, location, stock_on_hand) as of 2026-10-15
snapshots = [
    ("PRD-101", "WH-01", 420), ("PRD-101", "BR-01", 25), ("PRD-101", "BR-02", 60), ("PRD-101", "BR-03", 150),
    ("PRD-101", "WH-02", 900), ("PRD-101", "BR-04", 40), ("PRD-101", "BR-05", 30),
    ("PRD-102", "WH-01", 300), ("PRD-102", "BR-01", 60), ("PRD-102", "BR-02", 40), ("PRD-102", "BR-03", 30),
]

# constant daily sales for the trailing 28 days (2026-09-18 .. 2026-10-15)
daily_sales = {
    ("PRD-101", "BR-01"): 25, ("PRD-101", "BR-02"): 20, ("PRD-101", "BR-03"): 15,
    ("PRD-101", "BR-04"): 18, ("PRD-101", "BR-05"): 12,
    ("PRD-102", "BR-01"): 5, ("PRD-102", "BR-02"): 4, ("PRD-102", "BR-03"): 3,
}

movements = [
    {"id": "MOV-0001", "date": "2026-09-09", "type": "receipt", "qty": 300, "product": "PRD-103", "po": "PO-1031", "to_wh": "WH-01"},
    {"id": "MOV-0002", "date": "2026-10-13", "type": "dispatch", "qty": 50, "product": "PRD-101", "from_wh": "WH-01", "to_br": "BR-01"},
    {"id": "MOV-0003", "date": "2026-10-13", "type": "dispatch", "qty": 40, "product": "PRD-101", "from_wh": "WH-01", "to_br": "BR-02"},
    {"id": "MOV-0004", "date": "2026-10-13", "type": "dispatch", "qty": 30, "product": "PRD-101", "from_wh": "WH-01", "to_br": "BR-03"},
    {"id": "MOV-0005", "date": "2026-10-13", "type": "dispatch", "qty": 36, "product": "PRD-101", "from_wh": "WH-02", "to_br": "BR-04"},
    {"id": "MOV-0006", "date": "2026-10-13", "type": "dispatch", "qty": 24, "product": "PRD-101", "from_wh": "WH-02", "to_br": "BR-05"},
    # DQ2: receipt of PRD-102 for PO-1042, while the PO line says received_qty 0 and status open
    {"id": "MOV-0007", "date": "2026-10-14", "type": "receipt", "qty": 100, "product": "PRD-102", "po": "PO-1042", "to_wh": "WH-01"},
]


def compact(text):
    return text.replace("-", "")


def build_snapshot_rows():
    rows = []
    for product, loc, qty in snapshots:
        rows.append({
            "snapshot_id": f"SNP-{compact(loc)}-{compact(product)}-{compact(SNAP_DATE)}",
            "snapshot_date": SNAP_DATE, "stock_on_hand": qty,
            "product": product, "loc": loc,
        })
    return rows


def build_sale_rows():
    rows = []
    for (product, branch), qty in daily_sales.items():
        for i in range(28):
            d = (AS_OF - timedelta(days=i)).isoformat()
            rows.append({
                "sale_id": f"SAL-{compact(branch)}-{compact(product)}-{compact(d)}",
                "sale_date": d, "quantity": qty, "product": product, "branch": branch,
            })
    return rows


def validate():
    """Fail the load if the seed data breaks a rule from the schema (section 8)."""
    for po in purchase_orders:
        assert po["order_date"] <= po["expected_date"], f"{po['po_id']}: order_date after expected_date"
        if po["revised_expected_date"]:
            assert po["revised_expected_date"] >= po["order_date"], f"{po['po_id']}: bad revised date"
        if po["status"] == "received":
            assert po["actual_delivery_date"], f"{po['po_id']}: received but no actual_delivery_date"
        assert po["lines"], f"{po['po_id']}: no product lines"
        for product, ordered, received in po["lines"]:
            assert 0 <= received <= ordered, f"{po['po_id']}/{product}: bad quantities"
    seen = set()
    for product, loc, _ in snapshots:
        assert (product, loc) not in seen, f"duplicate snapshot {product} {loc}"
        seen.add((product, loc))


def create_nodes(g, label, rows):
    g.query(f"UNWIND $rows AS r CREATE (n:{label}) SET n += r", {"rows": rows})


def main():
    validate()
    db = FalkorDB(host=HOST, port=PORT)
    try:
        db.select_graph(GRAPH).delete()
    except Exception:
        pass
    g = db.select_graph(GRAPH)

    for label, key in [
        ("Supplier", "supplier_id"), ("Product", "product_id"), ("PurchaseOrder", "po_id"),
        ("Warehouse", "warehouse_id"), ("Branch", "branch_id"), ("InventorySnapshot", "snapshot_id"),
        ("Sale", "sale_id"), ("StockMovement", "movement_id"),
    ]:
        try:
            g.query(f"CREATE INDEX FOR (n:{label}) ON (n.{key})")
        except Exception:
            pass

    create_nodes(g, "Supplier", suppliers)
    create_nodes(g, "Product", products)
    create_nodes(g, "Warehouse", warehouses)
    create_nodes(g, "Branch", branches)

    po_nodes = [{k: v for k, v in po.items() if k not in ("supplier", "warehouse", "lines")} for po in purchase_orders]
    create_nodes(g, "PurchaseOrder", po_nodes)

    snap_rows = build_snapshot_rows()
    create_nodes(g, "InventorySnapshot", [{k: r[k] for k in ("snapshot_id", "snapshot_date", "stock_on_hand")} for r in snap_rows])

    sale_rows = build_sale_rows()
    create_nodes(g, "Sale", [{k: r[k] for k in ("sale_id", "sale_date", "quantity")} for r in sale_rows])

    mov_nodes = [{"movement_id": m["id"], "movement_date": m["date"], "movement_type": m["type"], "quantity": m["qty"]} for m in movements]
    create_nodes(g, "StockMovement", mov_nodes)

    # ---- relationships
    g.query(
        "UNWIND $rows AS r MATCH (s:Supplier {supplier_id: r.s}), (p:Product {product_id: r.p}) CREATE (s)-[:SUPPLIES]->(p)",
        {"rows": [{"s": s, "p": p} for s, p in supplies]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (s:Supplier {supplier_id: r.s}), (po:PurchaseOrder {po_id: r.po}) CREATE (s)-[:HAS_ORDER]->(po)",
        {"rows": [{"s": po["supplier"], "po": po["po_id"]} for po in purchase_orders]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (po:PurchaseOrder {po_id: r.po}), (w:Warehouse {warehouse_id: r.w}) CREATE (po)-[:DELIVERS_TO]->(w)",
        {"rows": [{"po": po["po_id"], "w": po["warehouse"]} for po in purchase_orders]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (po:PurchaseOrder {po_id: r.po}), (p:Product {product_id: r.p}) "
        "CREATE (po)-[:CONTAINS {ordered_qty: r.o, received_qty: r.rc}]->(p)",
        {"rows": [{"po": po["po_id"], "p": p, "o": o, "rc": rc} for po in purchase_orders for p, o, rc in po["lines"]]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (w:Warehouse {warehouse_id: r.w}), (b:Branch {branch_id: r.b}) CREATE (w)-[:REPLENISHES]->(b)",
        {"rows": [{"w": w, "b": b} for w, b in replenishes]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (b:Branch {branch_id: r.b}), (p:Product {product_id: r.p}) "
        "CREATE (b)-[:STOCKS {reorder_level: r.rl}]->(p)",
        {"rows": [{"b": b, "p": p, "rl": rl} for b, p, rl in stocks]},
    )

    g.query(
        "UNWIND $rows AS r MATCH (s:InventorySnapshot {snapshot_id: r.id}), (p:Product {product_id: r.p}) CREATE (s)-[:OF_PRODUCT]->(p)",
        {"rows": [{"id": r["snapshot_id"], "p": r["product"]} for r in snap_rows]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (s:InventorySnapshot {snapshot_id: r.id}), (b:Branch {branch_id: r.l}) CREATE (s)-[:AT_BRANCH]->(b)",
        {"rows": [{"id": r["snapshot_id"], "l": r["loc"]} for r in snap_rows if r["loc"].startswith("BR-")]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (s:InventorySnapshot {snapshot_id: r.id}), (w:Warehouse {warehouse_id: r.l}) CREATE (s)-[:AT_WAREHOUSE]->(w)",
        {"rows": [{"id": r["snapshot_id"], "l": r["loc"]} for r in snap_rows if r["loc"].startswith("WH-")]},
    )

    g.query(
        "UNWIND $rows AS r MATCH (s:Sale {sale_id: r.id}), (p:Product {product_id: r.p}), (b:Branch {branch_id: r.b}) "
        "CREATE (s)-[:OF_PRODUCT]->(p), (s)-[:SOLD_AT]->(b)",
        {"rows": [{"id": r["sale_id"], "p": r["product"], "b": r["branch"]} for r in sale_rows]},
    )

    g.query(
        "UNWIND $rows AS r MATCH (m:StockMovement {movement_id: r.id}), (p:Product {product_id: r.p}) CREATE (m)-[:MOVES_PRODUCT]->(p)",
        {"rows": [{"id": m["id"], "p": m["product"]} for m in movements]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (m:StockMovement {movement_id: r.id}), (po:PurchaseOrder {po_id: r.po}) CREATE (m)-[:RECEIPT_FOR]->(po)",
        {"rows": [{"id": m["id"], "po": m["po"]} for m in movements if m.get("po")]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (m:StockMovement {movement_id: r.id}), (w:Warehouse {warehouse_id: r.w}) CREATE (m)-[:TO_WAREHOUSE]->(w)",
        {"rows": [{"id": m["id"], "w": m["to_wh"]} for m in movements if m.get("to_wh")]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (m:StockMovement {movement_id: r.id}), (w:Warehouse {warehouse_id: r.w}) CREATE (m)-[:FROM_WAREHOUSE]->(w)",
        {"rows": [{"id": m["id"], "w": m["from_wh"]} for m in movements if m.get("from_wh")]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (m:StockMovement {movement_id: r.id}), (b:Branch {branch_id: r.b}) CREATE (m)-[:TO_BRANCH]->(b)",
        {"rows": [{"id": m["id"], "b": m["to_br"]} for m in movements if m.get("to_br")]},
    )

    # ---- summary and sanity check
    nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    rels = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    print(f"Seeded graph '{GRAPH}': {nodes} nodes, {rels} relationships")

    exposed = g.query(
        "MATCH (po:PurchaseOrder {po_id: 'PO-1042'})-[:CONTAINS]->(p:Product {product_id: 'PRD-101'}), "
        "(po)-[:DELIVERS_TO]->(:Warehouse)-[:REPLENISHES]->(b:Branch)-[:STOCKS]->(p) "
        "RETURN b.branch_id ORDER BY b.branch_id"
    ).result_set
    print("Branches exposed to PO-1042 via PRD-101:", [row[0] for row in exposed], "(expected BR-01, BR-02, BR-03)")


if __name__ == "__main__":
    main()