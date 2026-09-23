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
    executor = KubectlExecutor(settings.kubeconfig_path)
    result = executor.context_details()
    if not result.success:
        return {"status": "error", "clusters": [], "error": _friendly_kubectl_error(result)}
    config = result.json() or {}
    current = config.get("current-context", "")
    clusters_by_name = {item.get("name"): item.get("cluster", {}) for item in config.get("clusters", [])}
    details = []
    for item in config.get("contexts", []):
        context_name = item.get("name", "")
        context = item.get("context", {})
        cluster_name = context.get("cluster", "")
        cluster = clusters_by_name.get(cluster_name, {})
        details.append({"name": context_name, "context": context_name, "cluster": cluster_name, "server": cluster.get("server", ""), "namespace": context.get("namespace", "default"), "current": context_name == current})
    return {"status": "success", "clusters": details}


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
