from app.tools import get_purchase_order, get_po_impact, find_path

ev = get_purchase_order("PO-1042")
print("NODES:", len(ev["nodes"]))
for n in ev["notes"]:
    print("NOTE:", n)

print("-----")
ev = get_po_impact("PO-1042")
for t in ev["tables"]:
    print(t["title"])
    for r in t["rows"]:
        print(r)
for n in ev["notes"]:
    print("NOTE:", n)

print("-----")
ev = find_path("SUP-003", "BR-01")
print("PATHS:", ev["paths"])

print("-----")
print("Missing PO:", get_purchase_order("PO-9999"))