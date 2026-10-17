import os
from dotenv import load_dotenv, find_dotenv
from falkordb import FalkorDB

load_dotenv(find_dotenv())

_db = FalkorDB(host=os.getenv("FALKORDB_HOST", "localhost"),
               port=int(os.getenv("FALKORDB_PORT", "6379")))
graph = _db.select_graph(os.getenv("GRAPH_NAME", "graphpilot"))
QUERY_TIMEOUT_MS = int(os.getenv("QUERY_TIMEOUT_MS", "5000"))


class GraphError(Exception):
    pass


def run_readonly(cypher: str, params: dict | None = None):
    """Read-only execution: FalkorDB rejects any write in ro_query."""
    try:
        return graph.ro_query(cypher, params or {}, timeout=QUERY_TIMEOUT_MS)
    except Exception as e:
        if "timeout" in str(e).lower():
            raise GraphError("The graph query timed out.")
        raise GraphError(f"Graph query failed: {str(e)[:200]}")