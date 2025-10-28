from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import API_NAME, CORS_ORIGINS
from .routers import ingest as ingest_router
from .routers import render as render_router

app = FastAPI(title=API_NAME)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    return {"ok": True}

# Register ingest router
app.include_router(ingest_router.router)
app.include_router(render_router.router)
