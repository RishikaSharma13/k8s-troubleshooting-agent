from app.api import metrics_routes
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.api import predictor_routes
import logging

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="AI Kubernetes Agent",
    version="0.1.0"
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Include router WITHOUT adding prefix (router already has /api/v1)
app.include_router(predictor_routes.router)

@app.get("/health")
def health():
    return {"status": "ok"}
