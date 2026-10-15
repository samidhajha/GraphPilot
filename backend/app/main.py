import asyncio

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .schemas import ChatRequest, ChatResponse, HealthResponse

app = FastAPI(title=settings.app_name, version=settings.app_version)

# Allow the React dev server to call this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health():
    return HealthResponse(status="ok", version=settings.app_version)


@app.post("/api/chat", response_model=ChatResponse)
async def chat(payload: ChatRequest):
    # Placeholder: real AI logic gets plugged in later.
    await asyncio.sleep(1)  # simulates processing so you can see the loading state
    return ChatResponse(
        reply=f"You asked: {payload.message}. (Placeholder reply, AI not connected yet.)"
    )