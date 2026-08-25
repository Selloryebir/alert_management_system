from fastapi.testclient import TestClient

from algorithm_service.app import app


client = TestClient(app)


def test_health_returns_contract_identity() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "UP",
        "service": "algorithm-service",
        "version": "0.2.0",
        "contract_version": "v2",
    }
