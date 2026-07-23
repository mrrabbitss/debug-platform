import os
import uuid

import pytest


pytestmark = pytest.mark.skipif(
    os.getenv("RUN_EXTERNAL_SERVICE_TESTS") != "1",
    reason="PostgreSQL/Qdrant integration services are not enabled",
)


def test_postgresql_application_startup_and_qdrant_round_trip() -> None:
    from fastapi.testclient import TestClient
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, PointStruct, VectorParams

    from app.main import app

    with TestClient(app) as client:
        readiness = client.get("/api/v1/health/ready")
        assert readiness.status_code == 200, readiness.text
        assert readiness.json()["checks"]["database"] == {"ok": True}
        status = client.get("/api/v1/system/status")
        assert status.status_code == 200, status.text
        assert status.json()["database"]["dialect"] == "postgresql"
        created = client.post(
            "/api/v1/cases",
            json={"title": "PostgreSQL integration", "device_type": "GW", "description": "CI"},
        )
        assert created.status_code == 200, created.text
        case_id = created.json()["id"]
        assert client.get(f"/api/v1/cases/{case_id}").status_code == 200

    qdrant_url = os.environ["QDRANT_URL"]
    qdrant = QdrantClient(url=qdrant_url, timeout=10)
    collection = f"ci_{uuid.uuid4().hex}"
    try:
        qdrant.create_collection(
            collection_name=collection,
            vectors_config=VectorParams(size=4, distance=Distance.COSINE),
        )
        qdrant.upsert(
            collection_name=collection,
            wait=True,
            points=[PointStruct(id=1, vector=[1.0, 0.0, 0.0, 0.0], payload={"source": "ci"})],
        )
        result = qdrant.query_points(
            collection_name=collection,
            query=[1.0, 0.0, 0.0, 0.0],
            limit=1,
        )
        assert result.points[0].id == 1
    finally:
        if qdrant.collection_exists(collection):
            qdrant.delete_collection(collection)
