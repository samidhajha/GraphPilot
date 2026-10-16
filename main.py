
import os

from fastapi import FastAPI
from falkordb import FalkorDB
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(title="GraphPilot API")

db = FalkorDB(
    host=os.getenv("FALKORDB_HOST", "localhost"),
    port=int(os.getenv("FALKORDB_PORT", "6379")),
)

graph = db.select_graph("GraphPilot")


@app.get("/health")
def health():
    try:
        result = graph.query("RETURN 1 AS ok")
        return {
            "status": "healthy",
            "database": "connected",
            "result": result.result_set,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }


@app.get("/")
def home():
    return {"message": "GraphPilot API is running"}

@app.get("/api/investigate")
def investigate():
    query = """
    MATCH (s:Supplier)-[:RECEIVES_ORDER]->(po:PurchaseOrder)
          -[:ORDERS_PRODUCT]->(p:Product)
          -[:STORED_AT]->(b:Branch)
          -[:HAS_INVENTORY]->(i:Inventory)
    WHERE po.status = 'Delayed'
      AND i.stock_on_hand < i.reorder_level
    RETURN
        s.name AS supplier,
        po.id AS purchase_order,
        po.status AS order_status,
        p.name AS product,
        b.name AS branch,
        i.stock_on_hand AS current_stock,
        i.reorder_level AS reorder_level
    """
    try:
        result = graph.query(query)
        columns = [
            "supplier",
            "purchase_order",
            "order_status",
            "product",
            "branch",
            "current_stock",
            "reorder_level",
        ]

        findings = [
            dict(zip(columns, row))
            for row in result.result_set
        ]
        for finding in findings:
            finding["risk"] = (
                "HIGH"
                if finding["current_stock"] < finding["reorder_level"]
                and finding["order_status"] == "Delayed"
                else "CHECK"
            )
            finding["evidence"] = (
                f"Purchase order {finding['purchase_order']} is "
                f"{finding['order_status']}. Current stock is "
                f"{finding['current_stock']}, below reorder level "
                f"{finding['reorder_level']}."
            )

        return {
            "status": "success",
            "finding_count": len(findings),
            "findings": findings,
        }
    except Exception as e:
        return {
            "status": "error",
            "message": str(e),
        }

from pydantic import BaseModel


class ChatRequest(BaseModel):
    question: str


@app.post("/api/chat")
def chat(request: ChatRequest):
    query = """
    MATCH (s:Supplier)-[:RECEIVES_ORDER]->(po:PurchaseOrder)
          -[:ORDERS_PRODUCT]->(p:Product)
          -[:STORED_AT]->(b:Branch)
          -[:HAS_INVENTORY]->(i:Inventory)
    WHERE po.status = 'Delayed'
      AND i.stock_on_hand < i.reorder_level
    RETURN s.name AS supplier,
           po.id AS purchase_order,
           po.status AS order_status,
           p.name AS product,
           b.name AS branch,
           i.stock_on_hand AS current_stock,
           i.reorder_level AS reorder_level
    """
    try:
        result = graph.query(query)
        findings = [
            {
                "supplier": row[0],
                "purchase_order": row[1],
                "order_status": row[2],
                "product": row[3],
                "branch": row[4],
                "current_stock": row[5],
                "reorder_level": row[6],
            }
            for row in result.result_set
        ]

        if not findings:
            answer = (
                "No matching delayed-order and low-stock risk "
                "was found in the current graph."
            )
        else:
            f = findings[0]
            answer = (
                f"{f['branch']} is at risk. Supplier "
                f"{f['supplier']} has a delayed purchase order "
                f"({f['purchase_order']}) for {f['product']}. "
                f"Current stock is {f['current_stock']}, below "
                f"the reorder level of {f['reorder_level']}. "
                "This is a risk indicated by the test data."
            )

        return {
            "question": request.question,
            "answer": answer,
            "evidence": findings,
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}
