from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, ForeignKey, Enum
from sqlalchemy.orm import declarative_base, sessionmaker, relationship
from datetime import datetime
import os
from enum import Enum as PyEnum

# Get database URL from environment
DATABASE_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:password@localhost:5432/k8s_agent"
)

# Create engine
engine = create_engine(DATABASE_URL)

# Create session factory
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# Base class for all models
Base = declarative_base()

# Status enum
class IncidentStatus(PyEnum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXECUTING = "EXECUTING"
    RESOLVED = "RESOLVED"
    FAILED = "FAILED"

class ActionStatus(PyEnum):
    SCALE_DEPLOYMENT = "SCALE_DEPLOYMENT"
    INCREASE_MEMORY_LIMIT = "INCREASE_MEMORY_LIMIT"
    INVESTIGATE_LOGS = "INVESTIGATE_LOGS"
    RESTART_DEPLOYMENT = "RESTART_DEPLOYMENT"

# ============================================================
# TABLE 1: Incidents
# ============================================================
class Incident(Base):
    __tablename__ = "incidents"
    
    # Primary key
    id = Column(String(36), primary_key=True)  # UUID
    
    # Pod/Deployment info
    pod_name = Column(String(255), nullable=False)
    deployment = Column(String(255), nullable=False)
    namespace = Column(String(255), nullable=False, default="default")
    
    # Risk data
    risk_score = Column(Float, nullable=False)
    risk_level = Column(String(50), nullable=False)  # LOW, MEDIUM, HIGH
    
    # Status
    status = Column(String(50), nullable=False, default="PENDING")
    
    # Recommendation
    recommended_action = Column(String(100))
    confidence = Column(Float)  # 0-100%
    reasoning = Column(String(500))  # Why this action was recommended
    
    # Timestamps
    created_at = Column(DateTime, default=datetime.utcnow)
    approved_at = Column(DateTime, nullable=True)
    resolved_at = Column(DateTime, nullable=True)
    
    # Relationships
    executions = relationship("Execution", back_populates="incident")
    
    def __repr__(self):
        return f"<Incident {self.pod_name} in {self.namespace} - {self.status}>"

# ============================================================
# TABLE 2: Executions
# ============================================================
class Execution(Base):
    __tablename__ = "executions"
    
    # Primary key
    id = Column(String(36), primary_key=True)  # UUID
    
    # Foreign key
    incident_id = Column(String(36), ForeignKey("incidents.id"), nullable=False)
    
    # Action details
    action = Column(String(100), nullable=False)
    status = Column(String(50), nullable=False)  # EXECUTING, SUCCESS, FAILED
    
    # What changed
    old_value = Column(String(255))  # e.g., "200Mi" (old memory limit)
    new_value = Column(String(255))  # e.g., "260Mi" (new memory limit)
    
    # Results
    improvement_percentage = Column(Float, default=0.0)
    error_message = Column(String(500), nullable=True)
    
    # Timestamps
    executed_at = Column(DateTime, default=datetime.utcnow)
    verified_at = Column(DateTime, nullable=True)
    
    # Relationship
    incident = relationship("Incident", back_populates="executions")
    
    def __repr__(self):
        return f"<Execution {self.id} - {self.action} ({self.status})>"

# ============================================================
# TABLE 3: Metrics History
# ============================================================
class MetricsHistory(Base):
    __tablename__ = "metrics_history"
    
    # Primary key (auto-increment)
    id = Column(Integer, primary_key=True)
    
    # Pod info
    pod_name = Column(String(255), nullable=False)
    namespace = Column(String(255), nullable=False, default="default")
    deployment = Column(String(255))
    
    # Metrics
    memory_percent = Column(Float)  # 0-100
    cpu_percent = Column(Float)     # 0-100
    restart_count = Column(Integer, default=0)
    risk_score = Column(Float)
    
    # Timestamp
    recorded_at = Column(DateTime, default=datetime.utcnow, index=True)
    
    def __repr__(self):
        return f"<MetricsHistory {self.pod_name} @ {self.recorded_at}>"

# ============================================================
# Dependency Injection (for FastAPI)
# ============================================================
def get_db():
    """Dependency for FastAPI endpoints"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# ============================================================
# Create all tables
# ============================================================
def init_db():
    """Create all tables in database"""
    Base.metadata.create_all(bind=engine)
