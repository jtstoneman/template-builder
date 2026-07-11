"""Production hardening: HTTP Basic auth, the counterparty intake submit
endpoint, and the security headers every response must carry."""
import base64
import json
from datetime import date

import pytest
from fastapi.testclient import TestClient

from template_builder.server import MAX_ANSWER_CHARS, create_app
from tests.conftest import ANSWERS

AUTH = "firm:s3cret-passphrase"


def basic(creds: str) -> dict[str, str]:
    return {"Authorization": "Basic " + base64.b64encode(creds.encode()).decode()}


@pytest.fixture
def ws(tmp_path, template_dict):
    (tmp_path / "nda.json").write_text(json.dumps(template_dict))
    return tmp_path


@pytest.fixture
def client(ws):
    return TestClient(create_app(str(ws), auth=AUTH))


# ------------------------------------------------------------------- auth ---

def test_firm_surface_requires_credentials(client):
    for path in ("/", "/api/templates", "/api/t/nda", "/api/matters",
                 "/api/escalations", "/api/exceptions"):
        res = client.get(path)
        assert res.status_code == 401, path
        assert res.headers["WWW-Authenticate"].startswith("Basic"), path


def test_wrong_and_malformed_credentials_rejected(client):
    assert client.get("/api/templates", headers=basic("firm:wrong")).status_code == 401
    assert client.get("/api/templates", headers=basic("other:" + AUTH)).status_code == 401
    assert client.get("/api/templates",
                      headers={"Authorization": "Basic !!!not-base64"}).status_code == 401
    assert client.get("/api/templates",
                      headers={"Authorization": "Bearer sometoken"}).status_code == 401


def test_correct_credentials_open_everything(client):
    assert client.get("/api/templates", headers=basic(AUTH)).status_code == 200
    assert client.post("/api/t/nda/validate", headers=basic(AUTH)).status_code == 200


def test_counterparty_surface_needs_no_credentials(client):
    assert client.get("/intake/nda").status_code == 200
    assert client.get("/api/intake/nda").status_code == 200
    res = client.post("/api/intake/nda/submit",
                      json={"counterparty": "Beacon Analytics", "answers": ANSWERS})
    assert res.status_code == 200
    # ...and only that surface: the questionnaire payload must stay minimal
    assert set(client.get("/api/intake/nda").json()) == {"doc_type", "variables"}


def test_health_check_is_open(client):
    body = client.get("/api/config").json()
    assert body["read_only"] is False
    assert "version" in body


def test_bad_auth_format_fails_at_startup(ws):
    with pytest.raises(ValueError, match="user:password"):
        create_app(str(ws), auth="no-colon-here")


# --------------------------------------------------------- intake submits ---

def test_intake_submit_pins_template_status_and_id(client, ws):
    res = client.post("/api/intake/nda/submit",
                      json={"counterparty": "Halloway Marine!", "answers": ANSWERS})
    assert res.status_code == 200
    matter_id = res.json()["id"]
    assert matter_id == f"nda-halloway-marine-{date.today().isoformat()}"
    saved = json.loads((ws / "matters" / f"{matter_id}.json").read_text())
    assert saved["status"] == "intake"
    assert saved["template"] == "nda"
    assert saved["counterparty"] == "Halloway Marine!"


def test_intake_submit_rejects_unknown_variables(client):
    res = client.post("/api/intake/nda/submit",
                      json={"counterparty": "X", "answers": {"not_a_variable": True}})
    assert res.status_code == 422
    assert "not_a_variable" in res.json()["detail"]


def test_intake_submit_rejects_oversized_answers(client):
    answers = dict(ANSWERS)
    key = next(k for k, v in ANSWERS.items() if isinstance(v, str))
    answers[key] = "x" * (MAX_ANSWER_CHARS + 1)
    res = client.post("/api/intake/nda/submit",
                      json={"counterparty": "X", "answers": answers})
    assert res.status_code == 422
    res = client.post("/api/intake/nda/submit",
                      json={"counterparty": "y" * 500, "answers": ANSWERS})
    assert res.status_code == 422  # pydantic max_length on counterparty


def test_intake_submit_duplicate_conflicts(client):
    body = {"counterparty": "Twice Corp", "answers": ANSWERS}
    assert client.post("/api/intake/nda/submit", json=body).status_code == 200
    assert client.post("/api/intake/nda/submit", json=body).status_code == 409


def test_intake_submit_cannot_touch_other_endpoints(client):
    # the exempt prefix must not leak auth-free access to firm endpoints
    assert client.get("/api/t/nda").status_code == 401
    assert client.post("/api/matters", json={
        "id": "sneaky", "template": "nda", "counterparty": "X",
        "answers": ANSWERS, "status": "open"}).status_code == 401


# ------------------------------------------------------- security headers ---

def test_security_headers_on_every_response(ws):
    plain = TestClient(create_app(str(ws)))
    for path in ("/", "/api/templates", "/api/config"):
        headers = plain.get(path).headers
        assert headers["X-Content-Type-Options"] == "nosniff", path
        assert headers["X-Frame-Options"] == "DENY", path
        assert "connect-src 'self'" in headers["Content-Security-Policy"], path


def test_security_headers_on_auth_refusals(client):
    res = client.get("/api/templates")
    assert res.status_code == 401
    assert res.headers["X-Content-Type-Options"] == "nosniff"
