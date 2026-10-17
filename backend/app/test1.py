from app.graph_client import run_readonly

print(run_readonly("MATCH (po:PurchaseOrder) RETURN po.po_id ORDER BY po.po_id").result_set)