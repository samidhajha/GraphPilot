# GraphPilot

An explainable AI agent powered by FalkorDB that connects inventory data, traces root causes, and delivers evidence-based business insights.

## Project structure

```
GraphPilot/
├── backend/      FastAPI app (app/main.py, config.py, schemas.py)
├── frontend/     React + Vite chat interface
├── docs/         API_CONTRACT.md
└── .env.example  Template for environment variables
```

## Prerequisites
- Python 3.10+
- Node.js 18+ and npm

## Setup (Windows PowerShell)

### 1. Environment variables
```powershell
copy .env.example .env
```
Then create `frontend\.env` containing:
```
VITE_API_URL=http://localhost:8000
```

### 2. Backend
```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -r backend\requirements.txt
cd backend
uvicorn app.main:app --reload --port 8000
```
Check it works: http://localhost:8000/health and http://localhost:8000/docs

### 3. Frontend (in a second terminal)
```powershell
cd frontend
npm install
npm run dev
```
Open http://localhost:5173

## Troubleshooting
- **Scripts are disabled in PowerShell:** run `Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned` in that terminal.
- **CORS error in the browser:** `CORS_ORIGINS` in `.env` must match the frontend URL exactly. Restart uvicorn after changing it.
- **"Can't reach the server" in the UI:** make sure the backend is running on port 8000.
- **Frontend ignores `.env` changes:** restart `npm run dev`.
- **`npm install` times out:** retry with `npm install --no-audit --no-fund --fetch-timeout=120000 --fetch-retries=5`.

## API
See [docs/API_CONTRACT.md](docs/API_CONTRACT.md).