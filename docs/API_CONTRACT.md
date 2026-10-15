# API Contract (v0.1.0)

- Base URL (local): `http://localhost:8000`
- Interactive docs: `http://localhost:8000/docs`

## GET /health
Checks that the service is running.

**Response 200**
```json
{ "status": "ok", "version": "0.1.0" }
```

## POST /api/chat
Sends a user message and returns a reply.

**Request body**
```json
{ "message": "string (1-2000 characters)" }
```

**Response 200**
```json
{ "reply": "string" }
```

**Errors**

| Status | Meaning |
|--------|---------|
| 422 | Validation error (empty or too-long message) |
| 500 | Unexpected server error |

## Notes
- CORS allows the origins listed in `CORS_ORIGINS` (default `http://localhost:5173`).
- `/api/chat` currently returns a placeholder reply. The response shape will stay the same when real logic is added.