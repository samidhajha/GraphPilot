"""Runs the demo questions against the real graph.
From the backend folder:
    python -m app.test_chat             (uses Gemini if GEMINI_API_KEY is set)
    python -m app.test_chat --no-llm    (forces the deterministic path)
"""
import sys

from app import investigate as inv
from app import llm, sessions

if "--no-llm" in sys.argv:
    llm.API_KEY = ""

PRIMARY = ("A supplier disruption has occurred. Which products and branches are most at risk because of it, "
           "how will the disruption propagate through our supply chain, what evidence supports the root cause, "
           "and what is the best recovery action?")

QUESTIONS = [
    ("PRIMARY", PRIMARY),
    ("FOLLOW-UP", "Why is PRD-101 at risk at BR-02?"),
    ("UNSUPPORTED", "What will our revenue be next quarter?"),
    ("MISSING PO", "What is affected by PO-9999?"),
]

print("LLM configured:", llm.is_configured())
for label, question in QUESTIONS:
    r = inv.investigate(question)
    sessions.save(r)
    print(f"\n=== {label}: status={r['status']} mode={r['explanation_mode']} risk={r['risk_level']} id={r['investigation_id']}")
    print(r["answer"])
    if r["status"] == "completed" and r["affected_entities"]:
        print("  at risk:", [(e["entity_id"], e.get("estimated_unmet_units")) for e in r["affected_entities"] if e["role"] == "at_risk_branch"])
        print("  not exposed:", [e["entity_id"] for e in r["not_exposed_entities"]])
        print("  recommendations:", [(x["recommendation_id"], x["action_type"]) for x in r["recommendations"]])
        print("  data issues:", [d["issue_id"] + " " + d["type"] for d in r["data_quality_issues"]])
        print("  graph:", len(r["graph"]["nodes"]), "nodes,", len(r["graph"]["edges"]), "edges; paths:", [p["path_id"] for p in r["evidence_paths"]])
        print("  queries:", len(r["queries_executed"]), "all read-only:", all(q["read_only"] for q in r["queries_executed"]))

print("\nSaved investigations:", [h["investigation_id"] for h in sessions.list_recent(10)])