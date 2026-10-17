from fastapi import FastAPI, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from . import investigate as inv
from . import llm, queries as Q, sessions, tools
from .config import settings
from .graph_client import run_readonly
from .schemas import ChatRequest, ChatResponse, InvestigationSummary

app = FastAPI(title=settings.app_name, version=settings.app_version)

# Allow the React dev server to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class ApiError(Exception):
    def __init__(self, status_code, code, message, details=None):
        self.status_code, self.code, self.message, self.details = status_code, code, message, details


def _envelope(code, message, details=None):
    body = {"error": {"code": code, "message": message}}
    if details:
        body["error"]["details"] = details
    return body


@app.exception_handler(ApiError)
async def api_error_handler(_: Request, exc: ApiError):
    return JSONResponse(status_code=exc.status_code, content=_envelope(exc.code, exc.message, exc.details))


@app.exception_handler(RequestValidationError)
async def validation_handler(_: Request, exc: RequestValidationError):
    details = [{"field": ".".join(str(p) for p in e["loc"][1:]) or "body", "issue": e["msg"]} for e in exc.errors()]
    return JSONResponse(status_code=422, content=_envelope(
        "INVALID_REQUEST", "A non-empty question of at most 500 characters is required.", details))


@app.exception_handler(inv.GraphUnavailable)
async def graph_handler(_: Request, exc: inv.GraphUnavailable):
    return JSONResponse(status_code=503, content=_envelope("GRAPH_UNAVAILABLE", "The graph database is unreachable."))


@app.exception_handler(Exception)
async def unexpected_handler(_: Request, exc: Exception):
    return JSONResponse(status_code=500, content=_envelope("INTERNAL_ERROR", "Unexpected backend failure."))


@app.get("/health")
def health():
    try:
        run_readonly("RETURN 1", {})
        seeded = bool(run_readonly("MATCH (po:PurchaseOrder) RETURN count(po)", {}).result_set[0][0])
    except Exception:
        raise ApiError(503, "GRAPH_UNAVAILABLE", "FalkorDB is unreachable.")
    configured = llm.is_configured()
    return {
        "status": "ok" if configured else "degraded",
        "service": "graphpilot",
        "version": settings.app_version,
        "as_of_date": Q.AS_OF.isoformat(),
        "dependencies": {"falkordb": "ok", "graph_seeded": seeded,
                         "llm": "configured" if configured else "not_configured"},
    }


@app.post("/api/chat", response_model=ChatResponse)
def chat(payload: ChatRequest):
    result = inv.investigate(payload.question)
    sessions.save(result)
    return result


@app.get("/api/investigations", response_model=list[InvestigationSummary])
def list_investigations(limit: int = Query(20, ge=1, le=50)):
    return sessions.list_recent(limit)


@app.get("/api/investigations/{investigation_id}", response_model=ChatResponse)
def get_investigation(investigation_id: str):
    found = sessions.get(investigation_id)
    if not found:
        raise ApiError(404, "INVESTIGATION_NOT_FOUND", f"No stored investigation with id {investigation_id}.")
    return found


@app.get("/api/graph/entity/{entity_id}")
def graph_entity(entity_id: str, limit: int = Query(25, ge=1, le=100)):
    try:
        detail = inv.entity_detail(entity_id, limit)
    except ValueError:
        detail = None
    except Exception as exc:
        if inv._is_graph_error(exc):
            raise inv.GraphUnavailable(str(exc))
        raise
    if not detail:
        raise ApiError(404, "ENTITY_NOT_FOUND", f"No entity with id {entity_id}.")
    return detail