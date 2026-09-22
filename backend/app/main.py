from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.core.config import settings
from app.services.investigation import InvestigationService
from app.ai.diagnosis import DiagnosisService

app = FastAPI(title="AI Kubernetes Agent")

# Load environment-backed settings at startup; feature-specific use comes later.
_ = settings

app.add_middleware(
    CORSMiddleware,
    allow_origins=[settings.frontend_url],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health() -> dict[str, str]:
    logger.info("Health check requested")
    return {"status": "healthy", "service": "ai-kubernetes-agent"}


@app.post("/investigate")
async def investigate() -> dict[str, object]:
    logger.info("Kubernetes investigation requested")
    evidence = InvestigationService().investigate()
    diagnosis = await DiagnosisService().diagnose(evidence)
    return {"status": "success", "investigation": evidence, "diagnosis": diagnosis}
