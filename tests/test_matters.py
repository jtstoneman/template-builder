"""Matters, round-trip diff, exception register, delegation, replay gate,
expiry, drift, intake — the deterministic parts of the automation layer."""
import json

import pytest
from fastapi.testclient import TestClient

from template_builder import journal
from template_builder.matter import (
    Ask,
    ingest_round,
    list_matters,
    load_matter,
    open_matter,
    record_deviation,
)
from template_builder.render import plan
from template_builder.roundtrip import asks_to_markup, extract_asks
from template_builder.server import create_app
from tests.conftest import ANSWERS


@pytest.fixture
def workspace(tmp_path, template_dict):
    (tmp_path / "nda.json").write_text(json.dumps(template_dict))
    return tmp_path


@pytest.fixture
def matter(workspace):
    return open_matter(workspace, "nda-halloway", "nda", dict(ANSWERS), "Halloway Marine")


# --- matters -------------------------------------------------------------------

def test_open_matter_pins_template_hash_and_journals(workspace, matter):
    assert matter.template_hash.startswith("sha256:")
    loaded = load_matter(workspace, "nda-halloway")
    assert loaded.counterparty == "Halloway Marine"
    assert [m.id for m in list_matters(workspace)] == ["nda-halloway"]
    entries = journal.read(str(workspace / "nda.json"))
    assert entries[-1].matter == "nda-halloway"
    with pytest.raises(ValueError):
        open_matter(workspace, "nda-halloway", "nda", {}, "X")  # duplicate id


def test_record_deviation_journals_and_raises_maturity(workspace, matter):
    record_deviation(workspace, matter, clause_id="term", standard_text="3 years",
                     agreed_text="5 years", approved_by="jane@firm.com",
                     rationale="conceded for destruction certification")
    entries = journal.read(str(workspace / "nda.json"))
    assert journal.maturity(entries, "term") == 1
    assert load_matter(workspace, "nda-halloway").deviations[0].agreed_text == "5 years"


# --- round-trip diff -----------------------------------------------------------

def _returned_document(template, answers, mutate):
    """Build a plausible returned document from the deterministic render."""
    planned = plan(template, answers)
    parts = ["NON-DISCLOSURE AGREEMENT", ""]
    for p in planned:
        heading, text = f"{p.number}. {p.clause.heading}", p.rendered_text
        heading, text = mutate(p.clause.id, heading, text)
        if heading is not None:
            parts += [heading, "", text, ""]
    return "\n".join(parts)


def test_extract_asks_finds_modify_and_delete(template, answers):
    def mutate(cid, heading, text):
        if cid == "term":
            return heading, text.replace("3 years", "ten (10) years")
        if cid == "governing-law":
            return None, None  # clause deleted entirely
        return heading, text

    their = _returned_document(template, answers, mutate)
    asks, unanchored = extract_asks(plan(template, answers), their)
    kinds = {a.clause_id: a.kind for a in asks}
    assert kinds == {"term": "modify", "governing-law": "delete"}
    modify = next(a for a in asks if a.clause_id == "term")
    assert "ten (10) years" in modify.their_text
    assert "3 years" in modify.our_text
    assert unanchored == []


def test_extract_asks_ignores_whitespace_and_quote_noise(template, answers):
    def mutate(cid, heading, text):
        return heading, text.replace(" ", "  ").replace('"', "”")

    asks, unanchored = extract_asks(plan(template, answers),
                                    _returned_document(template, answers, mutate))
    assert asks == [] and unanchored == []


def test_extract_asks_refuses_to_guess_on_retyped_documents(template, answers):
    asks, unanchored = extract_asks(plan(template, answers),
                                    "Completely restructured document with no headings.")
    assert asks == []
    assert len(unanchored) == 1  # everything lands in the review bucket


def test_asks_to_markup_mentions_both_sides():
    markup = asks_to_markup(
        [Ask(clause_id="term", kind="modify", our_text="ours", their_text="theirs")],
        ["stray text"])
    assert "OURS:   ours" in markup and "THEIRS: theirs" in markup
    assert "could not anchor" in markup


def test_ingest_round_without_playbook_escalates_everything(workspace, matter,
                                                            template, answers, tmp_path):
    def mutate(cid, heading, text):
        return (heading, text.replace("3 years", "9 years")) if cid == "term" \
            else (heading, text)

    returned = tmp_path / "returned.txt"
    returned.write_text(_returned_document(template, answers, mutate))
    updated, round_, report = ingest_round(workspace, matter.id, str(returned),
                                           negotiate=True)
    assert report is None  # no playbook -> no LLM call was needed
    assert [a.clause_id for a in round_.asks] == ["term"]
    assert round_.template_hash == matter.template_hash  # each round pins its hash
    refreshed = load_matter(workspace, "nda-halloway")
    assert [e.clause_id for e in refreshed.pending_escalations()] == ["term"]
    assert refreshed.escalations[0].requires == "lawyer"


# --- delegation + replay gating ---------------------------------------------------

def test_parse_delegation_defaults_and_frontmatter():
    from template_builder.skill import parse_delegation
    assert parse_delegation(None)["red_line"] == "partner"
    playbook = ("---\nname: x\ndelegation_red_line: senior-partner\n"
                "delegation_mature: paralegal\n---\n# body\ndelegation_immature: nope")
    parsed = parse_delegation(playbook)
    assert parsed["red_line"] == "senior-partner"
    assert parsed["mature"] == "paralegal"
    assert parsed["immature"] == "lawyer"  # body lines are not frontmatter


def test_gate_stamps_deciders_and_replay_blocks_mature_clauses(template):
    from template_builder.journal import JournalEntry
    from template_builder.negotiate import ClauseResponse, NegotiationPlan, gate

    entries = [JournalEntry(id=i, ts="2026-07-10T00:00:00", actor="jane", kind="edit",
                            clause_id="term", why="r") for i in range(1, 12)]
    playbook = "### term\n**Position** — hold [j:1]"

    def plan_for(stance, red=False):
        return NegotiationPlan(summary="s", responses=[ClauseResponse(
            clause_id="term", stance=stance, rationale="r [j:1]",
            proposed_text="t" if stance == "counter" else None, red_line_implicated=red)])

    delegation = {"red_line": "partner", "immature": "associate", "mature": "assistant"}
    good_replay = {"term": {"agree": 9, "total": 10}}
    ok = gate(plan_for("counter"), template, playbook, entries, 10,
              delegation=delegation, replay_scores=good_replay)
    assert (ok.responses[0].stance, ok.responses[0].decider) == ("counter", "assistant")

    red = gate(plan_for("counter", red=True), template, playbook, entries, 10,
               delegation=delegation, replay_scores=good_replay)
    assert (red.responses[0].stance, red.responses[0].decider) == ("escalate", "partner")

    # maturity alone is not evidence: no replay scores -> escalate
    unproven = gate(plan_for("counter"), template, playbook, entries, 10,
                    delegation=delegation)
    assert unproven.responses[0].stance == "escalate"
    assert "tb skill replay" in unproven.responses[0].rationale

    # replay disagreement blocks a clause that maturity alone would allow
    blocked = gate(plan_for("counter"), template, playbook, entries, 10,
                   delegation=delegation,
                   replay_scores={"term": {"agree": 3, "total": 10}})
    assert blocked.responses[0].stance == "escalate"
    assert "replay agreement" in blocked.responses[0].rationale
    assert blocked.responses[0].decider == "associate"

    # exact play matching: a clause whose id merely prefixes a play id has no play
    prefix_plan = NegotiationPlan(summary="s", responses=[ClauseResponse(
        clause_id="term", stance="counter", rationale="r [j:1]",
        proposed_text="t", red_line_implicated=False)])
    no_play = gate(prefix_plan, template, "### term-survival\n**Position** — hold [j:1]",
                   entries, 10, delegation=delegation, replay_scores=good_replay)
    assert no_play.responses[0].stance == "escalate"
    assert "no playbook play" in no_play.responses[0].rationale


def test_run_replay_scores_agreement(template, monkeypatch):
    from template_builder import replay as replay_mod
    from template_builder.journal import JournalEntry
    from template_builder.replay import ReplayPrediction, run_replay

    entries = [
        JournalEntry(id=1, ts="t", actor="jane", kind="decision", clause_id="term",
                     why="asked for 9 years; countered at 5", disposition="countered"),
        JournalEntry(id=2, ts="t", actor="jane", kind="decision", clause_id="term",
                     why="asked to delete; refused", disposition="rejected"),
        JournalEntry(id=3, ts="t", actor="assistant", kind="edit", clause_id="term",
                     why="assistant acted", disposition="accepted"),  # never replayed
    ]
    predictions = iter(["countered", "conceded"])  # one hit, one miss

    monkeypatch.setattr(replay_mod, "complete",
                        lambda *a, **k: ReplayPrediction(predicted=next(predictions),
                                                         reasoning="because"))
    scores = run_replay(template, "playbook text", entries)
    assert scores == {"term": {"agree": 1, "total": 2, "misses": [2]}}


# --- expiry + drift ----------------------------------------------------------------

def test_certificate_age_days(template):
    from template_builder import approve, validate
    assert approve.certificate_age_days(template) is None
    approve.approve(template, "jane", validate.validate(template)[1], date="2025-01-01")
    assert approve.certificate_age_days(template, today="2026-07-10") == 555


def test_drift_detects_diverged_same_id_clauses(workspace, template_dict):
    import copy

    from template_builder.drift import find_drift
    other = copy.deepcopy(template_dict)
    other["doc_type"] = "Master Services Agreement"
    other["clauses"][5]["variants"][1]["text"] = (
        "This Agreement is governed by the laws of England and Wales, and the parties "
        "submit to arbitration in London under the LCIA Rules.")
    (workspace / "msa.json").write_text(json.dumps(other))
    drifts = find_drift(workspace)
    assert [d.clause_id for d in drifts] == ["governing-law"]
    assert {drifts[0].template_a, drifts[0].template_b} == {"msa", "nda"}


# --- exception register + server surfaces -------------------------------------------

def test_exception_register_rolls_up_matters(workspace, matter):
    from template_builder.exceptions import render_register
    record_deviation(workspace, matter, clause_id="term", standard_text="s",
                     agreed_text="a", approved_by="partner@firm.com", rationale="traded")
    register = render_register(workspace)
    assert "`term`" in register and "partner@firm.com" in register and "traded" in register


def test_server_matter_lifecycle_and_inbox(workspace, template, answers, tmp_path):
    client = TestClient(create_app(str(workspace)))

    res = client.post("/api/matters", json={
        "id": "web-matter", "template": "nda", "counterparty": "Acme",
        "answers": ANSWERS, "status": "intake"})
    assert res.status_code == 200
    assert [m["id"] for m in client.get("/api/matters").json()] == ["web-matter"]

    # upload a round with one modification; no playbook -> everything escalates
    def mutate(cid, heading, text):
        return (heading, text.replace("3 years", "8 years")) if cid == "term" \
            else (heading, text)

    returned = _returned_document(template, answers, mutate)
    res = client.post("/api/matters/web-matter/round",
                      data={"negotiate": "true"},
                      files={"file": ("returned.txt", returned.encode(), "text/plain")})
    assert res.status_code == 200, res.text
    inbox = client.get("/api/escalations").json()
    assert [(e["matter"], e["clause_id"], e["requires"]) for e in inbox] == \
        [("web-matter", "term", "lawyer")]

    # resolve it: accept theirs -> deviation + journal + inbox drains
    res = client.post("/api/matters/web-matter/resolve", json={
        "clause_id": "term", "decision": "accept-theirs",
        "by": "jane@firm.com", "why": "eight years acceptable for this client"})
    assert res.status_code == 200
    assert client.get("/api/escalations").json() == []
    register = client.get("/api/exceptions").text
    assert "web-matter" in register and "eight years acceptable" in register
    entries = journal.read(str(workspace / "nda.json"))
    assert journal.maturity(entries, "term") == 1  # the lawyer's decision counts

    # drift + intake surfaces respond
    assert client.get("/api/drift").status_code == 200
    assert "New matter" in client.get("/intake/nda").text
    assert client.get("/intake/nope").status_code == 404
