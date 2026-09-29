import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.core.database import init_db

client = TestClient(app)

def test_settings_api():
    init_db()

    # 1. Get default settings
    res = client.get("/api/settings")
    assert res.status_code == 200
    data = res.json()
    assert data["rule_exact_dup_enabled"] is True
    assert data["phash_threshold"] == 6
    assert data["blur_threshold"] == 100.0

    # 2. Update settings (adjust blur threshold and disable screenshot)
    update_res = client.put("/api/settings", json={
        "settings": {
            "blur_threshold": 150.0,
            "rule_screenshot_enabled": True,
            "phash_threshold": 4
        },
        "re_evaluate": False
    })
    assert update_res.status_code == 200

    # Verify updated settings
    get_res = client.get("/api/settings")
    assert get_res.status_code == 200
    updated_data = get_res.json()
    assert updated_data["blur_threshold"] == 150.0
    assert updated_data["rule_screenshot_enabled"] is True
    assert updated_data["phash_threshold"] == 4

    # 3. Reset settings
    reset_res = client.post("/api/settings/reset")
    assert reset_res.status_code == 200

    # Verify reset
    after_reset = client.get("/api/settings").json()
    assert after_reset["blur_threshold"] == 100.0
    assert after_reset["phash_threshold"] == 6
