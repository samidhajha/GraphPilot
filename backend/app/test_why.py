from app import investigate as I, llm, tools

q = "Why is PRD-101 at risk at BR-02?"
tools.start_query_log()
try:
    choice = llm.choose_tool(q, tools.TOOL_DECLS)
    print("router:", choice)
except Exception as e:
    print("ROUTER ERROR:", repr(e))
    choice = I.deterministic_route(q)

name, args = choice
ev = tools.run_tool(name, args)
log = list(tools.QUERY_LOG.get() or [])
resp = I.assemble_impact(q, name, ev, log)
det = I.deterministic_impact_answer(resp, ev)
facts = I.facts_for_llm(resp, ev, det)

text = llm.write_answer(q, facts)
print("\nGEMINI ANSWER:\n", text)

allowed = set(I.NUM_RE.findall(facts + " " + q))
print("\nnumbers_supported:", I.numbers_supported(text, facts + " " + q))
print("numbers not in facts:", [n for n in I.NUM_RE.findall(text) if n not in allowed])
print("invents_cause:", I.invents_cause(text, ev))
m = I.CAUSE_RE.search(text) or I.CAUSE_WORDS.search(text)
print("cause match:", m.group(0) if m else None)