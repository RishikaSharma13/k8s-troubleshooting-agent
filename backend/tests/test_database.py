import pytest
from sqlalchemy import inspect, text
from app.models.database import (
    engine,
    SessionLocal,
    Base,
    Incident,
    Execution,
    MetricsHistory,
    init_db
)
import uuid
from datetime import datetime

# ============================================================
# Test 1: Database Connection
# ============================================================
def test_database_connection():
    """Verify database connection works"""
    with engine.connect() as connection:
        result = connection.execute(text("SELECT 1"))
        assert result.fetchone()[0] == 1


# ============================================================
# Test 2: Tables Created
# ============================================================
def test_tables_exist():
    """Verify all tables were created"""
    inspector = inspect(engine)
    tables = inspector.get_table_names()
    
    required_tables = ["incidents", "executions", "metrics_history"]
    for table in required_tables:
        assert table in tables, f"Table {table} not found in database"


# ============================================================
# Test 3: Create Incident
# ============================================================
def test_create_incident():
    """Test creating and querying incident"""
    db = SessionLocal()
    
    # Create
    incident = Incident(
        id=str(uuid.uuid4()),
        pod_name="test-pod",
        deployment="test-deployment",
        namespace="default",
        risk_score=75.5,
        risk_level="HIGH",
        status="PENDING",
        recommended_action="SCALE_DEPLOYMENT",
        confidence=85.0,
        reasoning="High memory utilization detected"
    )
    
    db.add(incident)
    db.commit()
    
    # Query back
    retrieved = db.query(Incident).filter_by(pod_name="test-pod").first()
    assert retrieved is not None
    assert retrieved.risk_score == 75.5
    assert retrieved.status == "PENDING"
    
    # Cleanup
    db.delete(retrieved)
    db.commit()
    db.close()


# ============================================================
# Test 4: Create Execution
# ============================================================
def test_create_execution():
    """Test creating execution linked to incident"""
    db = SessionLocal()
    
    # Create incident first
    incident_id = str(uuid.uuid4())
    incident = Incident(
        id=incident_id,
        pod_name="test-pod-exec",
        deployment="test-deployment",
        namespace="default",
        risk_score=80.0,
        risk_level="HIGH",
        status="EXECUTING",
        recommended_action="SCALE_DEPLOYMENT",
        confidence=90.0
    )
    db.add(incident)
    db.commit()
    
    # Create execution
    execution = Execution(
        id=str(uuid.uuid4()),
        incident_id=incident_id,
        action="SCALE_DEPLOYMENT",
        status="SUCCESS",
        old_value="2 replicas",
        new_value="3 replicas",
        improvement_percentage=25.5
    )
    db.add(execution)
    db.commit()
    
    # Query
    retrieved_exec = db.query(Execution).filter_by(action="SCALE_DEPLOYMENT").first()
    assert retrieved_exec is not None
    assert retrieved_exec.status == "SUCCESS"
    assert retrieved_exec.incident_id == incident_id
    
    # Cleanup
    db.delete(retrieved_exec)
    db.delete(incident)
    db.commit()
    db.close()


# ============================================================
# Test 5: Store Metrics History
# ============================================================
def test_store_metrics_history():
    """Test storing historical metrics"""
    db = SessionLocal()
    
    # Store multiple metrics snapshots
    for i in range(5):
        metric = MetricsHistory(
            pod_name="memory-test-app",
            namespace="default",
            deployment="memory-test-app",
            memory_percent=30 + (i * 5),  # 30%, 35%, 40%, ...
            cpu_percent=10.0 + i,
            restart_count=0,
            risk_score=20 + (i * 10)
        )
        db.add(metric)
    
    db.commit()
    
    # Query all metrics for this pod
    metrics = db.query(MetricsHistory).filter_by(
        pod_name="memory-test-app"
    ).order_by(MetricsHistory.recorded_at).all()
    
    assert len(metrics) == 5
    assert metrics[0].memory_percent == 30.0
    assert metrics[4].memory_percent == 50.0
    
    # Cleanup
    for m in metrics:
        db.delete(m)
    db.commit()
    db.close()


# ============================================================
# Test 6: Incident Status Transitions
# ============================================================
def test_incident_status_transitions():
    """Test that incident status can be updated"""
    db = SessionLocal()
    
    incident_id = str(uuid.uuid4())
    incident = Incident(
        id=incident_id,
        pod_name="status-test",
        deployment="status-test",
        namespace="default",
        risk_score=60.0,
        risk_level="MEDIUM",
        status="PENDING"
    )
    db.add(incident)
    db.commit()
    
    # Transition: PENDING → APPROVED
    incident.status = "APPROVED"
    incident.approved_at = datetime.utcnow()
    db.commit()
    
    # Verify
    updated = db.query(Incident).filter_by(id=incident_id).first()
    assert updated.status == "APPROVED"
    assert updated.approved_at is not None
    
    # Transition: APPROVED → EXECUTING
    incident.status = "EXECUTING"
    db.commit()
    
    # Verify
    updated = db.query(Incident).filter_by(id=incident_id).first()
    assert updated.status == "EXECUTING"
    
    # Cleanup
    db.delete(updated)
    db.commit()
    db.close()


# ============================================================
# Test 7: Query by Namespace
# ============================================================
def test_query_by_namespace():
    """Test filtering incidents by namespace"""
    db = SessionLocal()
    
    # Create incidents in different namespaces
    for ns in ["default", "kube-system", "monitoring"]:
        incident = Incident(
            id=str(uuid.uuid4()),
            pod_name=f"pod-{ns}",
            deployment=f"deploy-{ns}",
            namespace=ns,
            risk_score=50.0,
            risk_level="MEDIUM",
            status="PENDING"
        )
        db.add(incident)
    
    db.commit()
    
    # Query by namespace
    default_incidents = db.query(Incident).filter_by(namespace="default").all()
    assert len(default_incidents) >= 1
    assert any(inc.pod_name == "pod-default" for inc in default_incidents)
    
    # Cleanup
    all_incidents = db.query(Incident).filter(
        Incident.namespace.in_(["default", "kube-system", "monitoring"])
    ).all()
    for incident in all_incidents:
        db.delete(incident)
    db.commit()
    db.close()
