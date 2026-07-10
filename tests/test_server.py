"""API tests for the web UI backend (no LLM, no network)."""
import json

import pytest
from fastapi.testclient import TestClient

from template_builder.server import create_app
from tests.conftest import ANSWERS, TEMPLATE_DICT


@pytest.fixture
def workspace(tmp_path, template_dict):
    (tmp_path / "nda.json").write_text(json.dumps(template_dict))
    return tmp_path


@pytest.fixture
def client(workspace):
    return TestClient(create_app(str(workspace)))


def test_index_serves_frontend(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "template-builder" in res.text and "Questionnaire" in res.text


def test_create_app_accepts_single_template_file(workspace):
    app_client = TestClient(create_app(str(workspace / "nda.json")))
    assert app_client.get("/api/t/nda").status_code == 200


def test_create_app_rejects_missing_path(tmp_path):
    with pytest.raises(Exception):
        create_app(str(tmp_path / "nope"))


def test_list_templates(client):
    data = client.get("/api/templates").json()
    assert [s["name"] for s in data] == ["nda"]
    assert data[0]["doc_type"] == TEMPLATE_DICT["doc_type"]
    assert data[0]["unapproved"] == len(TEMPLATE_DICT["clauses"]) + 1  # + schema


def test_get_template_shape(client):
    data = client.get("/api/t/nda").json()
    assert data["doc_type"] == TEMPLATE_DICT["doc_type"]
    assert {c["id"] for c in data["clauses"]} == {c["id"] for c in TEMPLATE_DICT["clauses"]}
    assert data["status"]["parties"] == "unapproved"
    assert data["schema_approval_id"] in data["status"]
    assert data["has_report"] is False


def test_unknown_template_is_404(client):
    assert client.get("/api/t/nope").status_code == 404
    assert client.get("/api/t/..%2Fetc").status_code == 404


def test_validate_endpoint(client):
    data = client.post("/api/t/nda/validate").json()
    assert data["error_count"] == 0
    assert data["coverage"]["exhaustive"] is True


def test_render_happy_path(client):
    data = client.post("/api/t/nda/render", json={"answers": ANSWERS}).json()
    numbers = [c["number"] for c in data["clauses"]]
    assert numbers == list(range(1, len(numbers) + 1))
    ids = [c["id"] for c in data["clauses"]]
    assert "non-solicit" not in ids  # excluded by the fixture answers
    obligations = next(c for c in data["clauses"] if c["id"] == "obligations")
    assert "Each party" in obligations["text"]      # mutual variant
    assert "{{" not in obligations["text"]
    assert set(data["unapproved"]) == set(ids) | {"__schema__"}


def test_render_reports_problems_as_422(client):
    bad = dict(ANSWERS)
    del bad["party_1"]
    res = client.post("/api/t/nda/render", json={"answers": bad})
    assert res.status_code == 422
    assert any("party_1" in p for p in res.json()["detail"])


def test_edit_saves_and_goes_stale(client, workspace):
    client.post("/api/t/nda/approve", json={"by": "reviewer"})
    out = client.post("/api/t/nda/edit/replace-text", json={
        "clause_id": "term", "variant_id": "default",
        "text": "This Agreement continues indefinitely.",
    }).json()
    assert out["saved"] is True
    assert out["status"]["term"] == "stale"
    assert out["status"]["parties"] == "approved"
    saved = json.loads((workspace / "nda.json").read_text())
    term = next(c for c in saved["clauses"] if c["id"] == "term")
    assert term["variants"][0]["text"] == "This Agreement continues indefinitely."


def test_edit_refused_when_it_breaks_validation(client, workspace):
    before = (workspace / "nda.json").read_text()
    out = client.post("/api/t/nda/edit/replace-text", json={
        "clause_id": "term", "variant_id": "default",
        "text": "See {{ref:does-not-exist}}.",
    }).json()
    assert out["saved"] is False
    assert any("orphan" in e for e in out["new_errors"])
    assert (workspace / "nda.json").read_text() == before


def test_approve_then_render_clean(client):
    out = client.post("/api/t/nda/approve", json={"by": "reviewer"}).json()
    assert out["certificate"]["exhaustive"] is True
    assert set(out["status"].values()) == {"approved"}
    data = client.post("/api/t/nda/render", json={"answers": ANSWERS}).json()
    assert data["unapproved"] == []


def test_export_docx_returns_wordml(client):
    res = client.post("/api/export-docx", json={
        "title": "Test NDA",
        "clauses": [
            {"id": "a", "number": 1, "heading": "Parties",
             "text": "First paragraph.\n\nSecond paragraph — hand-edited."},
        ],
    })
    assert res.status_code == 200
    assert res.content[:2] == b"PK"
    assert 'filename="test-nda.docx"' in res.headers["content-disposition"]


# --- the build flow (LLM mocked) ----------------------------------------

@pytest.fixture
def build_client(workspace, monkeypatch, template):
    """A client whose build pipeline is replaced by a fake that records its
    inputs and returns the fixture template."""
    recorded = {}

    def fake_build(documents, doc_type, progress=None, playbook=None):
        recorded["documents"] = documents
        recorded["doc_type"] = doc_type
        recorded["playbook"] = playbook
        if progress:
            progress("atomised fake.txt: 3 clauses")
        return template, "# Build report — fake\n", []

    import template_builder.merge as merge_mod
    monkeypatch.setattr(merge_mod, "build_template", fake_build)
    return TestClient(create_app(str(workspace))), recorded


def _run_build(client, name="spa", contexts=None):
    files = [
        ("files", ("spa_01.txt", b"AGREEMENT one. Section 1. Terms.", "text/plain")),
        ("files", ("spa_02.txt", b"AGREEMENT two. Section 1. Terms.", "text/plain")),
    ]
    res = client.post("/api/build", data={
        "doc_type": "Share Purchase Agreement",
        "name": name,
        "contexts": json.dumps(contexts if contexts is not None
                               else ["seller-friendly; W&I insurance", ""]),
    }, files=files)
    assert res.status_code == 200, res.text
    job_id = res.json()["id"]
    for _ in range(100):
        status = client.get(f"/api/build/{job_id}").json()
        if status["state"] != "running":
            return status
    raise AssertionError("build job never finished")


def test_build_job_passes_documents_and_contexts(build_client, workspace):
    client, recorded = build_client
    status = _run_build(client)
    assert status["state"] == "done", status
    assert status["template"] == "spa"
    docs = recorded["documents"]
    assert [d.name for d in docs] == ["spa_01.txt", "spa_02.txt"]
    assert docs[0].context == "seller-friendly; W&I insurance"
    assert docs[1].context is None            # empty context becomes None
    assert recorded["doc_type"] == "Share Purchase Agreement"
    # template + report written; sources persisted for provenance
    assert (workspace / "spa.json").exists()
    assert (workspace / "spa.json.report.md").exists()
    assert (workspace / "sources" / "spa" / "spa_01.txt").exists()
    # progress lines made it into the job log
    assert any("atomised" in line for line in status["log"])
    # and the new template is now listed + has a report
    names = [s["name"] for s in client.get("/api/templates").json()]
    assert "spa" in names
    assert client.get("/api/t/spa/report").text.startswith("# Build report")


def test_build_refuses_duplicate_name(build_client):
    client, _ = build_client
    res = client.post("/api/build", data={
        "doc_type": "X", "name": "nda", "contexts": "[]",
    }, files=[("files", ("a.txt", b"text", "text/plain"))])
    assert res.status_code == 409


def test_synthesis_prompt_includes_context():
    from template_builder.merge import OutlineEntry, synthesis_prompt
    from template_builder.decompile import SourceClause

    entry = OutlineEntry(id="warranties", heading="Warranties", matches=[])
    sources = [("spa_01.txt", SourceClause(heading="W", text="Seller warrants...", defines=[]))]
    prompt = synthesis_prompt(
        entry, sources, ["warranties", "wi-insurance"], [],
        contexts={"spa_01.txt": "seller-friendly; W&I insurance"},
        all_files=["spa_01.txt", "spa_02.txt"],
    )
    assert "deal context: seller-friendly; W&I insurance" in prompt
    assert "does NOT appear in: spa_02.txt" in prompt
    assert "gate it accordingly" in prompt
