from datetime import date

AS_OF = date(2026, 10, 15)   # same fixed demo date as seed.py

# ID prefix -> (label, key property). Allowlist, so the AI can't inject labels.
ENTITY_TYPES = {
    "PO-":  ("PurchaseOrder", "po_id"),
    "PRD-": ("Product", "product_id"),
    "SUP-": ("Supplier", "supplier_id"),
    "WH-":  ("Warehouse", "warehouse_id"),
    "BR-":  ("Branch", "branch_id"),
    "MOV-": ("StockMovement", "movement_id"),
}

# Relationships allowed in path search. SUPPLIES, STOCKS and MOVES_PRODUCT are left out on purpose:
# they link by product only, so they create shortcuts (e.g. SUP-003 -> PRD-101 -> BR-04)
# that show a connection but not the real supply route.
PATH_RELS = ("HAS_ORDER|DELIVERS_TO|REPLENISHES|CONTAINS|"
             "RECEIPT_FOR|TO_WAREHOUSE|FROM_WAREHOUSE|TO_BRANCH")


def resolve(entity_id: str):
    eid = (entity_id or "").strip().upper()
    for prefix, (label, key) in ENTITY_TYPES.items():
        if eid.startswith(prefix):
            return eid, label, key
    raise ValueError(f"Unknown ID format: {entity_id}")


def q_neighbors(label, key):
    # Sale nodes are excluded: a product has hundreds of them.
    return (
        f"MATCH (a:{label} {{{key}: $id}})-[r]-(x) "
        f"WHERE NOT 'Sale' IN labels(x) "
        f"RETURN a, r, x LIMIT 60"
    )


def q_path(la, ka, lb, kb):
    return (
        f"MATCH p = (a:{la} {{{ka}: $a}})-[:{PATH_RELS}*1..5]-(b:{lb} {{{kb}: $b}}) "
        f"RETURN p ORDER BY length(p) LIMIT 1"
    )


Q_PO = """
MATCH (po:PurchaseOrder {po_id: $po_id})
OPTIONAL MATCH (po)-[r]-(x) WHERE NOT 'Sale' IN labels(x)
RETURN po, r, x
"""

Q_PO_LINES = """
MATCH (po:PurchaseOrder {po_id: $po_id})-[c:CONTAINS]->(p:Product)
RETURN p.product_id, c.ordered_qty, c.received_qty
"""

Q_PO_RECEIPTS = """
MATCH (m:StockMovement)-[:RECEIPT_FOR]->(po:PurchaseOrder {po_id: $po_id}),
      (m)-[:MOVES_PRODUCT]->(p:Product)
RETURN m.movement_id, m.movement_date, m.quantity, p.product_id
"""

Q_EXPOSURE_PATHS = """
MATCH p = (po:PurchaseOrder {po_id: $po_id})-[:DELIVERS_TO]->(:Warehouse)-[:REPLENISHES]->(:Branch)
RETURN p
"""

# Branch, product, reorder level (the stock column is kept for compatibility; real stock comes from Q_STOCK_ALL)
Q_EXPOSED_BRANCHES = """
MATCH (po:PurchaseOrder {po_id: $po_id})-[:CONTAINS]->(p:Product),
      (po)-[:DELIVERS_TO]->(:Warehouse)-[:REPLENISHES]->(b:Branch)-[st:STOCKS]->(p)
OPTIONAL MATCH (p)<-[:OF_PRODUCT]-(s:InventorySnapshot)-[:AT_BRANCH]->(b)
RETURN p.product_id, b.branch_id, b.city, st.reorder_level, s.stock_on_hand
ORDER BY p.product_id, b.branch_id
"""

# Trailing 28 days of sales: total quantity and number of sale days per product and branch
Q_SALES_28D = """
MATCH (sa:Sale)-[:OF_PRODUCT]->(p:Product), (sa)-[:SOLD_AT]->(b:Branch)
WHERE sa.sale_date >= $since AND sa.sale_date <= $as_of AND p.product_id IN $pids
RETURN p.product_id, b.branch_id, sum(sa.quantity), count(sa)
"""

# Supplier and destination warehouse of a PO
Q_PO_CONTEXT = """
MATCH (s:Supplier)-[:HAS_ORDER]->(po:PurchaseOrder {po_id: $po_id})-[:DELIVERS_TO]->(w:Warehouse)
RETURN s.supplier_id, w.warehouse_id
"""

# All snapshots (branches and warehouses) on or before as_of; Python keeps the latest per location
Q_STOCK_ALL = """
MATCH (s:InventorySnapshot)-[:OF_PRODUCT]->(p:Product)
WHERE p.product_id IN $pids AND s.snapshot_date <= $as_of
OPTIONAL MATCH (s)-[:AT_BRANCH]->(b:Branch)
OPTIONAL MATCH (s)-[:AT_WAREHOUSE]->(w:Warehouse)
RETURN p.product_id, coalesce(b.branch_id, w.warehouse_id), s.snapshot_date, s.stock_on_hand, s.snapshot_id
"""

# Other warehouses serving branches that stock the same products (transfer donors / not-exposed branches)
Q_DONORS = """
MATCH (w:Warehouse)-[:REPLENISHES]->(b:Branch)-[:STOCKS]->(p:Product)
WHERE p.product_id IN $pids AND w.warehouse_id <> $wh_id
RETURN p.product_id, w.warehouse_id, collect(b.branch_id)
"""

# Other suppliers of the same products
Q_ALT_SUPPLIERS = """
MATCH (s:Supplier)-[:SUPPLIES]->(p:Product)
WHERE p.product_id IN $pids AND s.supplier_id <> $supplier_id
RETURN p.product_id, s.supplier_id, s.lead_time_days
"""

# Every purchase order with its supplier (used to scan for disruptions)
Q_ALL_POS = """
MATCH (s:Supplier)-[:HAS_ORDER]->(po:PurchaseOrder)
RETURN po.po_id, po.status, po.expected_date, po.revised_expected_date, po.actual_delivery_date, s.supplier_id, s.name
ORDER BY po.po_id
"""

# Open POs that deliver a product to the warehouse feeding a branch (symptom -> cause)
Q_UPSTREAM_POS = """
MATCH (b:Branch {branch_id: $bid})<-[:REPLENISHES]-(:Warehouse)<-[:DELIVERS_TO]-(po:PurchaseOrder)-[:CONTAINS]->(p:Product {product_id: $pid})
WHERE po.status IN ['open', 'partially_received']
RETURN po.po_id, po.status, po.expected_date, po.revised_expected_date, po.actual_delivery_date
ORDER BY po.po_id
"""

Q_BRANCH_PRODUCT_EXISTS = """
MATCH (p:Product {product_id: $pid}), (b:Branch {branch_id: $bid})
RETURN count(*)
"""

# Other open orders bringing the same products to the same warehouse
Q_ALT_INBOUND = """
MATCH (po:PurchaseOrder)-[:CONTAINS]->(p:Product), (po)-[:DELIVERS_TO]->(:Warehouse {warehouse_id: $wh_id})
WHERE p.product_id IN $pids AND po.po_id <> $po_id AND po.status IN ['open', 'partially_received']
RETURN po.po_id, p.product_id
"""

# Graph pieces for the UI (nodes and edges returned together)
Q_IMPACT_GRAPH = """
MATCH (s:Supplier)-[ho:HAS_ORDER]->(po:PurchaseOrder {po_id: $po_id})-[c:CONTAINS]->(p:Product),
      (po)-[d:DELIVERS_TO]->(w:Warehouse)-[rp:REPLENISHES]->(b:Branch)-[st:STOCKS]->(p)
RETURN s, ho, po, c, p, d, w, rp, b, st
"""

Q_ALT_GRAPH = """
MATCH (s:Supplier)-[r:SUPPLIES]->(p:Product)
WHERE p.product_id IN $pids AND s.supplier_id <> $supplier_id
RETURN s, r, p
"""

Q_OTHER_WH_GRAPH = """
MATCH (w:Warehouse)-[rp:REPLENISHES]->(b:Branch)-[st:STOCKS]->(p:Product)
WHERE p.product_id IN $pids AND w.warehouse_id <> $wh_id
RETURN w, rp, b, st, p
"""