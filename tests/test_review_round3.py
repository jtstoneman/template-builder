"""Regression tests for the round-3 critical review.

Each test pins a specific confirmed defect: if one of these fails, a fixed
bug has come back.
"""
import copy
import io
import json
import zipfile

import pytest
from fastapi.testclient import TestClient

from template_builder import conditions, edit, journal, ops, validate
from template_builder.matter import (
    close_matter,
    ingest_round,
    load_matter,
    open_matter,
    record_deviation,
    resolve_escalation,
)
from template_builder.model import TemplateError, template_from_dict
from template_builder.render import plan
from template_builder.richtext import docx_document, parse_html
from template_builder.roundtrip import extract_asks
from template_builder.server import create_app
from tests.conftest import ANSWERS


@pytest.fixture
def workspace(tmp_path, template_dict):
    (tmp_path / "nda.json").write_text(json.dumps(template_dict))
    return tmp_path


# --- richtext ------------------------------------------------------------------

def test_trailing_ampersand_text_is_not_dropped():
    # HTMLParser buffers a potential incomplete charref; without close() the
    # lawyer's "...survives any M&A" vanished from the exported .docx
    assert parse_html("<p>survives any M&A")[0].text() == "survives any M&A"
    assert parse_html("Owner: AT&T")[0].text() == "Owner: AT&T"


def test_each_numbered_list_restarts_at_one():
    blocks = parse_html("<ol><li>a1</li><li>a2</li></ol><p>x</p>"
                        "<ol><li>b1</li></ol>")
    assert [b.starts_list for b in blocks] == [True, False, False, True]
    buf = io.BytesIO()
    docx_document("T", [("H", blocks)]).save(buf)
    numbering = zipfile.ZipFile(buf).read("word/numbering.xml").decode()
    assert numbering.count("startOverride") == 2  # one fresh instance per list


# --- conditions ----------------------------------------------------------------

def test_in_requires_a_list_not_substring_matching():
    with pytest.raises(conditions.ConditionError, match="must be a list"):
        conditions.evaluate('law in "New York"', {"law": "York"})
    assert conditions.evaluate('law in ["New York"]', {"law": "New York"})
    assert not conditions.evaluate('law in ["New York"]', {"law": "York"})


def test_negative_number_literals_are_allowed():
    assert conditions.evaluate("balance > -1", {"balance": 0})
    assert conditions.evaluate("x == -5", {"x": -5})
    with pytest.raises(conditions.ConditionError):
        conditions.evaluate("-x > 1", {"x": -5})  # only literals may be signed


# --- validation ----------------------------------------------------------------

def test_headings_are_validated(template_dict):
    template_dict["clauses"][0]["heading"] = "Parties {{party_1}}"
    findings, _ = validate.validate(template_from_dict(template_dict))
    assert any("heading contains braces" in f.message
               for f in validate.errors(findings))


def test_finding_key_strips_labels_containing_parens():
    finding = validate.Finding("error", "sweep",
                               "boom (e.g. with law=New York (NY), x=True)")
    assert validate.finding_key(finding) == "[error] sweep: boom"


def test_sampled_sweep_is_stable_when_a_variable_is_added(template_dict):
    def variables(extra):
        td = copy.deepcopy(template_dict)
        for i in range(9 + extra):  # 2**9 = 512 > 256 -> sampled regime
            td["variables"].append({"name": f"flag_{i}", "type": "boolean",
                                    "question": f"Flag {i}?"})
        return template_from_dict(td)

    before = validate._configurations(variables(0))
    after = validate._configurations(variables(1))
    assert not before.exhaustive and not after.exhaustive
    # every pre-existing variable keeps its column of picks: old latent
    # errors must not resurface as "NEW" just because a variable was added
    shared = [n for n in before.names]
    projected_before = [{k: c[k] for k in shared} for c in before.configs[:50]]
    projected_after = [{k: c[k] for k in shared} for c in after.configs[:50]]
    assert projected_before == projected_after


def test_gated_edit_blocks_error_count_increases(tmp_path, template_dict):
    # a second configuration failing with the SAME finding text used to hide
    # behind the pre-existing finding — the count comparison catches it
    td = copy.deepcopy(template_dict)
    td["variables"].append({"name": "include_definitions", "type": "boolean",
                            "question": "Include the definitions clause?"})
    td["clauses"][1]["include_when"] = "include_definitions"  # definer now optional
    # mutual variant uses the term; one-way doesn't (yet) — so only
    # include_definitions=False & is_mutual=True configs fail at baseline
    td["clauses"][2]["variants"][0]["text"] = \
        "Each party shall protect the other's Confidential Information."
    td["clauses"][2]["variants"][1]["text"] = \
        "The receiving party shall protect the disclosing party's secrets."
    path = tmp_path / "t.json"
    path.write_text(json.dumps(td))
    before = validate.errors(validate.validate(
        template_from_dict(json.loads(path.read_text())))[0])
    count_before = next(f.count for f in before
                        if "Confidential Information" in str(f))

    def widen(template):  # now one-way configs use the term too: same finding
        return edit.replace_text(
            template, "obligations", "one-way",
            "The receiving party shall protect the disclosing party's "
            "Confidential Information.")

    outcome = ops.gated_edit(str(path), widen)
    assert outcome.saved is False
    assert outcome.new_errors, "an increased failing-config count must refuse"
    after = validate.errors(validate.validate(outcome.template)[0])
    count_after = next(f.count for f in after
                       if "Confidential Information" in str(f))
    assert count_after > count_before


# --- edit gate -----------------------------------------------------------------

def test_remove_clause_blocks_sole_definer_of_used_term(template_dict):
    td = copy.deepcopy(template_dict)
    # drop the {{ref:definitions}} cross-references so the (earlier) referrer
    # guard can't fire — the defined-term guard must catch this on its own
    td["clauses"][2]["variants"][0]["text"] = \
        "Each party shall protect the other's Confidential Information."
    td["clauses"][2]["variants"][1]["text"] = \
        "The receiving party shall protect Confidential Information."
    template = template_from_dict(td)
    with pytest.raises(edit.EditError, match="only clause defining"):
        edit.remove_clause(template, "definitions")


# --- journal reliability ---------------------------------------------------------

def test_torn_journal_tail_is_repaired_on_append(tmp_path, template):
    from template_builder import model
    path = tmp_path / "t.json"
    model.save(template, str(path))
    journal.append(str(path), actor="jane", kind="edit", why="first")
    # simulate a crash mid-append: a torn, newline-less fragment at the tail
    with open(journal.journal_path(str(path)), "a", encoding="utf-8") as f:
        f.write('{"id": 2, "ts": "2026-')
    entry = journal.append(str(path), actor="jane", kind="edit", why="second")
    entries = journal.read(str(path))
    assert [e.id for e in entries] == [1, 2]
    assert entry.id == 2  # the torn line never became id 2


def test_corrupt_mid_file_journal_raises_loudly(tmp_path, template):
    from template_builder import model
    path = tmp_path / "t.json"
    model.save(template, str(path))
    journal.append(str(path), actor="jane", kind="edit", why="first")
    raw = journal.journal_path(str(path)).read_text()
    journal.journal_path(str(path)).write_text("GARBAGE\n" + raw)
    with pytest.raises(ValueError, match="line 1 is corrupt"):
        journal.read(str(path))


# --- model guards ----------------------------------------------------------------

def test_future_schema_version_is_refused(template_dict):
    template_dict["schema_version"] = 99
    with pytest.raises(TemplateError, match="schema version 99"):
        template_from_dict(template_dict)


def test_template_file_rejects_traversal(tmp_path):
    from template_builder.model import template_file
    with pytest.raises(TemplateError):
        template_file(tmp_path, "../etc/passwd")


# --- roundtrip anchoring -----------------------------------------------------------

def _their_doc(template, answers, *, toc=False, inserted=None):
    planned = plan(template, answers)
    parts = ["NON-DISCLOSURE AGREEMENT", ""]
    if toc:
        parts += ["Table of Contents", ""]
        parts += [f"{p.number}. {p.clause.heading}" for p in planned]
        parts += [""]
    if inserted:
        parts += [inserted, ""]
    for p in planned:
        parts += [f"{p.number}. {p.clause.heading}", "", p.rendered_text, ""]
    return "\n".join(parts)


def test_table_of_contents_does_not_misanchor(template, answers):
    asks, unanchored = extract_asks(plan(template, answers),
                                    _their_doc(template, answers, toc=True))
    # a ToC used to anchor every clause at its ToC line, producing garbage
    # asks at 100% confidence; the ToC itself lands in review, nothing more
    assert asks == []


def test_text_inserted_before_first_clause_is_never_dropped(template, answers):
    inserted = ("The parties further agree that all disputes shall be resolved "
                "by binding arbitration in a forum of the counterparty's sole "
                "choosing, and that this provision prevails over anything else "
                "in this Agreement notwithstanding any contrary term.")
    asks, unanchored = extract_asks(
        plan(template, answers),
        _their_doc(template, answers, inserted=inserted))
    assert asks == []
    assert any("binding arbitration" in u for u in unanchored)


def test_counterparty_renumbering_styles_still_anchor(template, answers):
    planned = plan(template, answers)
    parts = ["NDA", ""]
    for p in planned:
        parts += [f"Section {p.number}. {p.clause.heading}", "", p.rendered_text, ""]
    asks, unanchored = extract_asks(planned, "\n".join(parts))
    assert asks == [] and unanchored == []


# --- matter guards ------------------------------------------------------------------

@pytest.fixture
def matter(workspace):
    return open_matter(workspace, "m1", "nda", dict(ANSWERS), "Acme")


def test_record_deviation_rejects_unknown_clause(workspace, matter):
    with pytest.raises(ValueError, match="not a clause"):
        record_deviation(workspace, matter, clause_id="tpyo", standard_text="s",
                         agreed_text="a", approved_by="j", rationale="r")


def test_closed_matter_refuses_rounds_and_resolutions(workspace, matter, tmp_path):
    close_matter(workspace, "m1", status="abandoned", by="jane")
    doc = tmp_path / "r.txt"
    doc.write_text("anything")
    with pytest.raises(ValueError, match="abandoned"):
        ingest_round(workspace, "m1", str(doc))
    with pytest.raises(ValueError, match="abandoned"):
        resolve_escalation(workspace, "m1", clause_id="term", decision="hold",
                           by="j", why="w")


def test_agreed_close_requires_empty_inbox(workspace, matter, template, answers,
                                           tmp_path):
    doc = tmp_path / "r.txt"
    parts = ["NDA", ""]
    for p in plan(template, answers):
        text = p.rendered_text.replace("3 years", "9 years")
        parts += [f"{p.number}. {p.clause.heading}", "", text, ""]
    doc.write_text("\n".join(parts))
    ingest_round(workspace, "m1", str(doc), negotiate=True)  # no playbook -> escalates
    with pytest.raises(ValueError, match="still pending"):
        close_matter(workspace, "m1", status="agreed", by="jane")
    resolve_escalation(workspace, "m1", clause_id="term", decision="hold",
                       by="jane", why="standard holds")
    closed = close_matter(workspace, "m1", status="agreed", by="jane")
    assert closed.status == "agreed"


def test_accepting_a_deletion_is_recorded_in_the_register(workspace, matter,
                                                          template, answers, tmp_path):
    doc = tmp_path / "r.txt"
    parts = ["NDA", ""]
    for p in plan(template, answers):
        if p.clause.id == "governing-law":
            continue  # the counterparty deleted the clause
        parts += [f"{p.number}. {p.clause.heading}", "", p.rendered_text, ""]
    doc.write_text("\n".join(parts))
    _, round_, _ = ingest_round(workspace, "m1", str(doc))
    assert [a.kind for a in round_.asks] == ["delete"]
    resolve_escalation(workspace, "m1", clause_id="governing-law",
                       decision="accept-theirs", by="partner@firm.com",
                       why="counterparty's paper governs this deal")
    refreshed = load_matter(workspace, "m1")
    assert refreshed.deviations[0].agreed_text == ""      # deletion, recorded
    assert refreshed.deviations[0].standard_text          # what was given up
    from template_builder.exceptions import render_register
    assert "CLAUSE DELETED BY AGREEMENT" in render_register(workspace)


def test_intake_matter_promotes_to_open_on_first_round(workspace, template,
                                                       answers, tmp_path):
    open_matter(workspace, "m2", "nda", dict(ANSWERS), "Beta", status="intake")
    doc = tmp_path / "r.txt"
    parts = ["NDA", ""]
    for p in plan(template, answers):
        parts += [f"{p.number}. {p.clause.heading}", "", p.rendered_text, ""]
    doc.write_text("\n".join(parts))
    matter, _, _ = ingest_round(workspace, "m2", str(doc))
    assert matter.status == "open"


# --- skill / replay hygiene -----------------------------------------------------

def test_update_skill_preserves_delegation_and_invalidates_replay(
        workspace, template, monkeypatch):
    from template_builder import skill
    from template_builder.skill import ClausePlay, SkillDraft

    template_path = str(workspace / "nda.json")
    journal.append(template_path, actor="jane", kind="decision", clause_id="term",
                   why="held term at 3 years", disposition="rejected")
    journal.append(template_path, actor=journal.ASSISTANT_ACTOR, kind="edit",
                   clause_id="term", why="assistant applied a counter")

    skill_dir = workspace / "skills" / skill.slugify(template.doc_type)
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "---\nname: x\nautonomy_threshold: 5\ndelegation_mature: senior-associate\n"
        "---\n# old\n")
    (skill_dir / "replay.json").write_text('{"term": {"agree": 9, "total": 10}}')

    seen_prompts = {}

    def fake_complete(system, prompt, output, **kw):
        seen_prompts["prompt"] = prompt
        return SkillDraft(overview="o", notes=[], plays=[
            ClausePlay(clause_id="term", position="hold", fallbacks=[],
                       red_lines=[], citations=[1])])

    monkeypatch.setattr(skill, "complete", fake_complete)
    path, markdown, notes = skill.update_skill(template_path, template)

    assert "delegation_mature: senior-associate" in markdown  # hand-tuning kept
    assert "autonomy_threshold: 5" in markdown
    assert "distilled_through: 1" in markdown        # only the HUMAN entry
    assert "assistant applied a counter" not in seen_prompts["prompt"]
    assert not (skill_dir / "replay.json").exists()  # stale evidence removed
    assert any("replay.json invalidated" in n for n in notes)


def test_replay_only_scores_decisions_the_distiller_never_saw(template, monkeypatch):
    from template_builder import replay as replay_mod
    from template_builder.journal import JournalEntry
    from template_builder.replay import ReplayPrediction, run_replay

    entries = [
        JournalEntry(id=1, ts="t", actor="jane", kind="decision", clause_id="term",
                     why="in-sample; the playbook was written from this",
                     disposition="rejected"),
        JournalEntry(id=2, ts="t", actor="jane", kind="decision", clause_id="term",
                     why="out-of-sample decision", disposition="countered"),
    ]
    monkeypatch.setattr(replay_mod, "complete",
                        lambda *a, **k: ReplayPrediction(predicted="countered",
                                                         reasoning="r"))
    playbook = "---\ndistilled_through: 1\n---\n### term\n**Position** — hold [j:1]"
    scores = run_replay(template, playbook, entries)
    assert scores == {"term": {"agree": 1, "total": 1, "misses": []}}


# --- server surfaces ---------------------------------------------------------------

def test_intake_endpoint_exposes_questionnaire_only(workspace):
    client = TestClient(create_app(str(workspace)))
    payload = client.get("/api/intake/nda").json()
    assert set(payload.keys()) == {"doc_type", "variables"}
    body = json.dumps(payload)
    for secret in ("variants", "provenance", "certificate", "include_when"):
        assert secret not in body


def test_invalid_edit_is_422_not_404(workspace):
    client = TestClient(create_app(str(workspace)))
    res = client.post("/api/t/nda/edit/replace-text", json={
        "clause_id": "definitions", "variant_id": "no-such-variant", "text": "x"})
    assert res.status_code == 422  # EditError -> client error, not "not found"
    res = client.post("/api/t/nda/edit/replace-text", json={
        "clause_id": "no-such-clause", "variant_id": "default", "text": "x"})
    assert res.status_code == 404  # KeyError -> the clause doesn't exist


def test_matter_close_endpoint(workspace):
    client = TestClient(create_app(str(workspace)))
    client.post("/api/matters", json={"id": "web-m", "template": "nda",
                                      "counterparty": "Acme", "answers": ANSWERS})
    res = client.post("/api/matters/web-m/close",
                      json={"status": "abandoned", "by": "jane"})
    assert res.status_code == 200 and res.json()["status"] == "abandoned"
    res = client.post("/api/matters/web-m/close",
                      json={"status": "nonsense", "by": "jane"})
    assert res.status_code == 422
