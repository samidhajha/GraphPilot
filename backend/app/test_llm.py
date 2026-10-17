from app import llm

print("MODEL:", llm.MODEL, "| configured:", llm.is_configured())

print("\n--- write_answer ---")
try:
    print(llm.write_answer("What colour is the sky?", "The sky is blue."))
except Exception as e:
    print("ERROR:", repr(e))

print("\n--- choose_tool ---")
decls = [{
    "name": "get_po",
    "description": "Get a purchase order by id",
    "parameters": {"type": "OBJECT",
                   "properties": {"po_id": {"type": "STRING"}},
                   "required": ["po_id"]},
}]
try:
    print(llm.choose_tool("Tell me about PO-1042", decls))
except Exception as e:
    print("ERROR:", repr(e))