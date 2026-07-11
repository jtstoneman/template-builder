"""The read-only demo mode: browse and compute, never write, never spend."""
import json

import pytest
from fastapi.testclient import TestClient

from template_builder.server import create_app
from tests.conftest import ANSWERS


@pytest.fixture
def client(tmp_path, template_dict):
    (tmp_path / "nda.json").write_text(json.dumps(template_dict))
    return TestClient(create_app(str(tmp_path), read_only=True))


def test_config_reports_read_only(client):
    assert client.get("/api/config").json()["read_only"] is True


def test_browsing_still_works(client):
    assert client.get("/api/templates").status_code == 200
    assert client.get("/api/t/nda").status_code == 200
    assert client.get("/api/matters").status_code == 200
    assert client.get("/api/exceptions").status_code == 200


def test_pure_compute_posts_are_allowed(client):
    assert client.post("/api/t/nda/validate").status_code == 200
    res = client.post("/api/t/nda/render", json={"answers": ANSWERS})
    assert res.status_code == 200
    clause = res.json()["clauses"][0]
    res = client.post("/api/export-docx", json={
        "title": "NDA", "clauses": [dict(clause, html=None)]})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("application/vnd.openxml")


def test_every_mutating_or_llm_endpoint_is_blocked(client):
    blocked = [
        ("/api/build", {"data": {"doc_type": "X", "name": "x"},
                        "files": {"files": ("a.txt", b"x", "text/plain")}}),
        ("/api/t/nda/edit/replace-text",
         {"json": {"clause_id": "term", "variant_id": "default", "text": "x"}}),
        ("/api/t/nda/approve", {"json": {"by": "mallory"}}),
        ("/api/t/nda/intake", {"json": {"term_sheet": "spend my money"}}),
        ("/api/matters", {"json": {"id": "m", "template": "nda",
                                   "counterparty": "X", "answers": ANSWERS}}),
        ("/api/matters/m/resolve", {"json": {"clause_id": "term",
                                             "decision": "hold", "by": "x", "why": "y"}}),
        ("/api/matters/m/close", {"json": {"status": "abandoned", "by": "x"}}),
        ("/api/intake/nda/submit", {"json": {"counterparty": "X",
                                             "answers": ANSWERS}}),
    ]
    for path, kwargs in blocked:
        res = client.post(path, **kwargs)
        assert res.status_code == 403, f"{path} must be blocked, got {res.status_code}"
        assert "read-only public demo" in res.json()["detail"]


def test_read_only_workspace_files_are_untouched(tmp_path, template_dict):
    (tmp_path / "nda.json").write_text(json.dumps(template_dict))
    before = {p.name: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    client = TestClient(create_app(str(tmp_path), read_only=True))
    client.post("/api/t/nda/validate")
    client.post("/api/t/nda/render", json={"answers": ANSWERS})
    client.post("/api/t/nda/approve", json={"by": "mallory"})
    after = {p.name: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert before == after
