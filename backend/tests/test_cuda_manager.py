import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.services.cuda_manager import cuda_manager

client = TestClient(app)


def test_cuda_manager_status():
    status = cuda_manager.get_cuda_status(force_refresh=True)
    assert isinstance(status, dict)
    assert "has_nvidia_gpu" in status
    assert "gpu_name" in status
    assert "torch_version" in status
    assert "torch_cuda_available" in status
    assert "is_ready" in status
    assert "status_code" in status
    assert "install_state" in status


def test_system_gpu_api():
    res = client.get("/api/system/gpu-status")
    assert res.status_code == 200
    data = res.json()
    assert "has_nvidia_gpu" in data
    assert "gpu_name" in data
    assert "is_ready" in data


def test_health_check_gpu_info():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert "gpu" in data
    assert "name" in data["gpu"]
    assert "cuda_available" in data["gpu"]
