from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.core.config import settings

app = FastAPI(title="AI Kubernetes Agent")

# Load environment-backed settings at startup; feature-specific use comes later.
_ = settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    logger.info("Health check requested")
    return {"status": "healthy", "service": "ai-kubernetes-agent"}
