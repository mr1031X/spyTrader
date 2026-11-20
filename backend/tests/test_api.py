from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from backend.config import BotSettings
from backend.database import FillRecord, OpportunityDiagnosticRecord, OrderRecord, SessionLocal
from backend.diagnostics import record_scan
from backend.main import bot, create_app


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


def test_diagnostics_empty_and_populated_states(monkeypatch):
    monkeypatch.setattr(bot, "snapshot", lambda: {
        "entries_enabled": False, "clock": {"is_open": False}, "readiness": None,
    })
    api = client()
    empty = api.get("/api/diagnostics")
    assert empty.status_code == 200
    assert empty.json()["symbols"] == []
    assert empty.json()["execution"]["orders_submitted"] == 0
    assert not empty.json()["scan_warning"]

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        record_scan(session, "paper", "SPY", now, {
            "scan_status": "SCANNED", "final_decision": "NO_VALID_SETUP",
            "strategies": [{"direction": "put", "candidate": False}],
            "signals_rejected": 1, "rejection_reasons": ["INSUFFICIENT_VOLUME"],
            "contract_selection": {"discovered": 4, "rejected": 4},
        })
        record_scan(session, "paper", "SPY", now, {
            "scan_status": "SCANNED", "final_decision": "NO_VALID_SETUP",
            "contract_selection": {"discovered": 2, "rejected": 2},
        })
    response = api.get("/api/diagnostics").json()
    item = response["symbols"][0]
    assert item["scans"] == 2
    assert item["bearish_considered"] == 1
    assert item["rejected_signals"] == 1
    assert item["contracts_evaluated"] == 6
    assert item["contracts_rejected"] == 6
    assert item["primary_rejections"] == [{"reason": "INSUFFICIENT_VOLUME", "count": 1}]
    assert response["last_successful_scan"] is not None
    assert response["evaluation_status"] == "EVALUATING"


def test_diagnostics_distinguishes_scan_stall_from_no_setup(monkeypatch):
    monkeypatch.setattr(bot, "snapshot", lambda: {
        "entries_enabled": True, "clock": {"is_open": True}, "readiness": None,
    })
    api = client()
    stalled = api.get("/api/diagnostics").json()
    assert stalled["scan_warning"]
    assert stalled["evaluation_status"] == "UNABLE_TO_EVALUATE"

    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        record_scan(session, "paper", "SPY", now, {
            "scan_status": "SCANNED", "final_decision": "NO_VALID_SETUP",
        })
    evaluated = api.get("/api/diagnostics").json()
    assert not evaluated["scan_warning"]
    assert evaluated["evaluation_status"] == "EVALUATING"

    with SessionLocal() as session:
        item = session.query(OpportunityDiagnosticRecord).one()
        item.last_scan_at = now - timedelta(seconds=121)
        session.commit()
    assert api.get("/api/diagnostics").json()["scan_warning"]


def test_diagnostics_execution_metrics(monkeypatch):
    monkeypatch.setattr(bot, "snapshot", lambda: {
        "entries_enabled": False, "clock": {"is_open": False}, "readiness": None,
    })
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        filled = OrderRecord(
            id="filled-order", client_order_id="filled-client", broker_order_id="broker-filled",
            mode="paper", symbol="SPY261121C00600000", side="buy", position_intent="buy_to_open",
            qty=1, status="filled", created_at=now - timedelta(seconds=2), updated_at=now,
        )
        canceled = OrderRecord(
            id="canceled-order", client_order_id="canceled-client", broker_order_id="broker-canceled",
            mode="paper", symbol="SPY261121C00600000", side="buy", position_intent="buy_to_open",
            qty=1, status="canceled", created_at=now, updated_at=now,
        )
        rejected = OrderRecord(
            id="rejected-order", client_order_id="rejected-client", mode="paper",
            symbol="SPY261121C00600000", side="buy", position_intent="buy_to_open",
            qty=1, status="rejected", created_at=now, updated_at=now,
        )
        session.add_all([filled, canceled, rejected])
        session.flush()
        session.add(FillRecord(
            id="fill", order_id=filled.id, broker_order_id=filled.broker_order_id,
            symbol=filled.symbol, qty=1, price=2.0, cumulative_qty=1, created_at=now - timedelta(seconds=1),
        ))
        session.commit()
    metrics = client().get("/api/diagnostics").json()["execution"]
    assert metrics["orders_submitted"] == 3
    assert metrics["orders_acknowledged"] == 2
    assert metrics["orders_filled"] == 1
    assert metrics["orders_canceled"] == 1
    assert metrics["orders_rejected"] == 1
    assert metrics["average_execution_latency_seconds"] == 1
