from fastapi import FastAPI
from pydantic import BaseModel
from fastapi.middleware.cors import CORSMiddleware
from loguru import logger
from app.core.config import settings
from app.services.investigation import InvestigationService
from app.ai.diagnosis import DiagnosisService
from app.kubernetes.kubectl_executor import KubectlExecutor

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


class InvestigationRequest(BaseModel):
    context: str | None = None


@app.get("/health")
def health() -> dict[str, str]:
    logger.info("Health check requested")
    return {"status": "healthy", "service": "ai-kubernetes-agent"}


@app.get("/clusters")
def clusters() -> dict[str, object]:
    result = KubectlExecutor(settings.kubeconfig_path).contexts()
    if not result.success:
        return {"status": "error", "clusters": [], "error": _friendly_kubectl_error(result)}
    return {"status": "success", "clusters": [line for line in result.stdout.splitlines() if line.strip()]}


@app.post("/investigate")
async def investigate(request: InvestigationRequest | None = None) -> dict[str, object]:
    logger.info("Kubernetes investigation requested")
    context = request.context if request else None
    evidence = InvestigationService(context).investigate()
    diagnosis = await DiagnosisService().diagnose(evidence)
    return {"status": "success", "investigation": evidence, "diagnosis": diagnosis}


def _friendly_kubectl_error(result: object) -> str:
    error = getattr(result, "error", None)
    details = getattr(result, "stderr", "").strip()
    if error == "kubectl is not installed or is not on PATH":
        return "kubectl is unavailable in the backend container. Rebuild the backend image."
    if "Unable to connect" in details or "connection refused" in details or "i/o timeout" in details:
        return "Unable to connect to the selected Kubernetes cluster. Verify kubeconfig, cluster access, and kubectl permissions."
    return details or error or "Unable to read kubeconfig contexts."
