import os
from falkordb import FalkorDB

HOST = os.getenv("FALKORDB_HOST", "localhost")
PORT = int(os.getenv("FALKORDB_PORT", "6379"))
GRAPH = os.getenv("FALKORDB_GRAPH", "graphpilot")

products = [
    {"id": "P1", "name": "Laptop", "category": "Computers"},
    {"id": "P2", "name": "Smartphone", "category": "Mobiles"},
    {"id": "P3", "name": "Headphones", "category": "Audio"},
    {"id": "P4", "name": "Monitor", "category": "Computers"},
    {"id": "P5", "name": "Keyboard", "category": "Accessories"},
]

warehouses = [
    {"id": "W1", "name": "Mumbai Hub", "city": "Mumbai"},
    {"id": "W2", "name": "Delhi Hub", "city": "Delhi"},
    {"id": "W3", "name": "Bengaluru Hub", "city": "Bengaluru"},
]

suppliers = [
    {"id": "S1", "name": "TechSource", "country": "China"},
    {"id": "S2", "name": "GlobalParts", "country": "Taiwan"},
    {"id": "S3", "name": "PrimeElectronics", "country": "India"},
]

shipments = [
    {"id": "SH1", "status": "Delayed", "eta": "2026-10-25"},
    {"id": "SH2", "status": "In Transit", "eta": "2026-10-22"},
    {"id": "SH3", "status": "Delivered", "eta": "2026-10-10"},
    {"id": "SH4", "status": "Delayed", "eta": "2026-10-28"},
]

events = [
    {"id": "E1", "type": "Port strike", "description": "Strike at Mumbai port causing cargo delays"},
    {"id": "E2", "type": "Heavy rain", "description": "Flooding on the Delhi route"},
    {"id": "E3", "type": "Customs hold", "description": "Extra inspection of electronics imports"},
]

# (supplier, product)
supplies = [("S1", "P1"), ("S1", "P2"), ("S2", "P4"), ("S2", "P3"), ("S3", "P5"), ("S3", "P3")]

# (product, warehouse, quantity)
stock = [
    ("P1", "W1", 40), ("P1", "W2", 5), ("P2", "W1", 120), ("P2", "W3", 15),
    ("P3", "W2", 60), ("P4", "W3", 8), ("P5", "W1", 200), ("P5", "W2", 90),
]

# (shipment, supplier, product, warehouse)
ship_links = [
    ("SH1", "S1", "P1", "W1"),
    ("SH2", "S3", "P5", "W2"),
    ("SH3", "S2", "P3", "W2"),
    ("SH4", "S2", "P4", "W3"),
]

# (event, shipment)
event_links = [("E1", "SH1"), ("E3", "SH1"), ("E2", "SH4")]


def main():
    db = FalkorDB(host=HOST, port=PORT)
    g = db.select_graph(GRAPH)

    try:
        g.delete()  # makes the script safe to run again
    except Exception:
        pass
    g = db.select_graph(GRAPH)

    g.query("UNWIND $rows AS r CREATE (:Product {id: r.id, name: r.name, category: r.category})", {"rows": products})
    g.query("UNWIND $rows AS r CREATE (:Warehouse {id: r.id, name: r.name, city: r.city})", {"rows": warehouses})
    g.query("UNWIND $rows AS r CREATE (:Supplier {id: r.id, name: r.name, country: r.country})", {"rows": suppliers})
    g.query("UNWIND $rows AS r CREATE (:Shipment {id: r.id, status: r.status, eta: r.eta})", {"rows": shipments})
    g.query("UNWIND $rows AS r CREATE (:Event {id: r.id, type: r.type, description: r.description})", {"rows": events})

    g.query(
        "UNWIND $rows AS r MATCH (s:Supplier {id: r.s}), (p:Product {id: r.p}) CREATE (s)-[:SUPPLIES]->(p)",
        {"rows": [{"s": s, "p": p} for s, p in supplies]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (p:Product {id: r.p}), (w:Warehouse {id: r.w}) "
        "CREATE (p)-[:STORED_IN {quantity: r.q}]->(w)",
        {"rows": [{"p": p, "w": w, "q": q} for p, w, q in stock]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (sh:Shipment {id: r.sh}), (s:Supplier {id: r.s}), "
        "(p:Product {id: r.p}), (w:Warehouse {id: r.w}) "
        "CREATE (sh)-[:FROM]->(s), (sh)-[:CARRIES]->(p), (sh)-[:TO]->(w)",
        {"rows": [{"sh": sh, "s": s, "p": p, "w": w} for sh, s, p, w in ship_links]},
    )
    g.query(
        "UNWIND $rows AS r MATCH (e:Event {id: r.e}), (sh:Shipment {id: r.sh}) CREATE (e)-[:AFFECTS]->(sh)",
        {"rows": [{"e": e, "sh": sh} for e, sh in event_links]},
    )

    nodes = g.query("MATCH (n) RETURN count(n)").result_set[0][0]
    rels = g.query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    print(f"Seeded graph '{GRAPH}': {nodes} nodes, {rels} relationships")


if __name__ == "__main__":
    main()