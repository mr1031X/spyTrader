from fastapi.testclient import TestClient

from backend.config import BotSettings
from backend.main import create_app


def client():
    return TestClient(create_app())


def test_health_and_dashboard_endpoints():
    api = client()
    assert api.get("/api/health").status_code == 200
    status = api.get("/api/status")
    assert status.status_code == 200
    assert status.json()["mode"] == "paper"
    assert status.json()["account"] is None
    for path in ("/api/positions", "/api/orders", "/api/trades", "/api/signals", "/api/events", "/api/performance", "/api/backtests"):
        response = api.get(path)
        assert response.status_code == 200, path


def test_settings_persist_and_mode_cannot_be_changed_through_general_update():
    api = client()
    settings = BotSettings().model_dump()
    settings["trailing_distance_percent"] = 12
    settings["trading_mode"] = "live"
    response = api.put("/api/settings", json=settings)
    assert response.status_code == 200
    assert response.json()["trailing_distance_percent"] == 12
    assert response.json()["trading_mode"] == "paper"
    assert api.get("/api/settings").json()["trailing_distance_percent"] == 12


def test_invalid_score_weights_return_json_safe_validation_errors():
    api = client()
    settings = BotSettings().model_dump()
    settings["score_weights"]["momentum"] = 0
    response = api.put("/api/settings", json=settings)
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail[0]["loc"] == ["score_weights"]
    assert "weights must total 100" in detail[0]["msg"]
    assert "ctx" not in detail[0]
    assert api.get("/api/settings").json()["score_weights"]["momentum"] == 20


def test_control_validation_and_emergency_confirmation():
    api = client()
    assert api.post("/api/control/start", json={"acknowledge_risk": False}).status_code == 400
    assert api.post("/api/control/pause", json={}).status_code == 200
    assert api.post("/api/emergency", json={"confirmation": "wrong"}).status_code == 400


def test_backtest_requires_real_quote_files():
    api = client()
    response = api.post("/api/backtests", files={"underlying_file": ("a.csv", b"bad\n1\n"), "options_file": ("b.csv", b"bad\n2\n")})
    assert response.status_code == 422
