from falkordb import FalkorDB

db = FalkorDB(host="localhost", port=6379)
g = db.select_graph("test")
g.query("CREATE (:Test {name: 'hello'})")
print(g.query("MATCH (n:Test) RETURN n.name").result_set)
g.delete()