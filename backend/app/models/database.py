from sqlalchemy import create_engine, Column, String, Integer, Float, DateTime, ForeignKey, Enum
from sqlalchemy import BigInteger, Index, UniqueConstraint, func
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
# TABLE 4: Memory Samples (measured data for forecasting)
# ============================================================
class MemorySample(Base):
    """One container memory reading from the Kubernetes Metrics API.

    Only real measurements are stored: no estimates or fallback values.
    metric_timestamp is when Metrics Server measured the value (not when we
    polled), and the unique constraint stores each reading exactly once.
    """
    __tablename__ = "memory_samples"

    id = Column(BigInteger, primary_key=True)

    namespace = Column(String(255), nullable=False)
    pod = Column(String(255), nullable=False)
    container = Column(String(255), nullable=False)
    deployment = Column(String(255), nullable=True)  # None if not owned by a Deployment

    metric_timestamp = Column(DateTime(timezone=True), nullable=False)
    window_seconds = Column(Float, nullable=True)
    memory_bytes = Column(BigInteger, nullable=False)        # working set
    memory_limit_bytes = Column(BigInteger, nullable=True)   # None if no limit set

    collected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("namespace", "pod", "container", "metric_timestamp",
                         name="uq_memory_samples_reading"),
        Index("ix_memory_samples_workload_time", "namespace", "deployment", "metric_timestamp"),
    )

    def __repr__(self):
        return f"<MemorySample {self.namespace}/{self.pod}/{self.container} @ {self.metric_timestamp}>"

# ============================================================
# TABLE 5: Container Terminations (ground truth for prediction)
# ============================================================
class ContainerTermination(Base):
    """One observed container termination, e.g. an OOMKill.

    Read from the pod status (state.terminated / lastState.terminated). This is
    the ground truth that memory forecasts are scored against. restart_count is
    the value seen at observation time, so gaps (missed terminations between
    polls) can be detected later.
    """
    __tablename__ = "container_terminations"

    id = Column(BigInteger, primary_key=True)

    namespace = Column(String(255), nullable=False)
    pod = Column(String(255), nullable=False)
    container = Column(String(255), nullable=False)
    deployment = Column(String(255), nullable=True)

    reason = Column(String(100), nullable=True)      # e.g. OOMKilled, Completed, Error
    exit_code = Column(Integer, nullable=True)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=False)
    restart_count = Column(Integer, nullable=True)

    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("namespace", "pod", "container", "finished_at",
                         name="uq_container_terminations_event"),
        Index("ix_container_terminations_workload_time", "namespace", "deployment", "finished_at"),
    )

    def __repr__(self):
        return f"<ContainerTermination {self.namespace}/{self.pod}/{self.container} {self.reason} @ {self.finished_at}>"

# ============================================================
# TABLE 6: Signal Samples (any gradual signal, for more predictions)
# ============================================================
class SignalSample(Base):
    """One reading of a gradual signal that can be forecast toward a limit.

    Examples: a pod's ephemeral-storage use (limit = its storage limit), or a
    Job's completed items (limit = completions, with a deadline). observed_at
    is when the collector first saw this value; values are stored when they
    change, plus a periodic re-confirmation (see app/collector/signals.py).
    """
    __tablename__ = "signal_samples"

    id = Column(BigInteger, primary_key=True)

    namespace = Column(String(255), nullable=False)
    workload_kind = Column(String(50), nullable=False)   # Deployment, CronJob, Job, Pod
    workload = Column(String(255), nullable=False)       # e.g. disk-leak, deadline-job
    object_name = Column(String(255), nullable=False)    # pod or job name
    signal = Column(String(100), nullable=False)         # e.g. ephemeral_storage_bytes, job_succeeded

    observed_at = Column(DateTime(timezone=True), nullable=False)
    value = Column(Float, nullable=False)
    limit_value = Column(Float, nullable=True)
    deadline_at = Column(DateTime(timezone=True), nullable=True)  # Jobs: start + activeDeadlineSeconds

    collected_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("namespace", "object_name", "signal", "observed_at", name="uq_signal_samples_reading"),
        Index("ix_signal_samples_workload_time", "namespace", "signal", "workload", "observed_at"),
    )

    def __repr__(self):
        return f"<SignalSample {self.namespace}/{self.object_name} {self.signal}={self.value} @ {self.observed_at}>"


# ============================================================
# TABLE 7: Outcome Events (ground truth for the new predictions)
# ============================================================
class OutcomeEvent(Base):
    """How an object ended: Evicted (pod), DeadlineExceeded or Complete (Job).

    Failures are the ground truth that forecasts are scored against; successes
    (Complete) are the negative cases. One row per object and event type.
    """
    __tablename__ = "outcome_events"

    id = Column(BigInteger, primary_key=True)

    namespace = Column(String(255), nullable=False)
    workload_kind = Column(String(50), nullable=False)
    workload = Column(String(255), nullable=False)
    object_name = Column(String(255), nullable=False)
    event_type = Column(String(100), nullable=False)     # Evicted, DeadlineExceeded, Complete
    occurred_at = Column(DateTime(timezone=True), nullable=False)
    message = Column(String(1000), nullable=True)

    observed_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("namespace", "object_name", "event_type", name="uq_outcome_events_event"),
        Index("ix_outcome_events_workload_time", "namespace", "workload", "occurred_at"),
    )

    def __repr__(self):
        return f"<OutcomeEvent {self.namespace}/{self.object_name} {self.event_type} @ {self.occurred_at}>"

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
