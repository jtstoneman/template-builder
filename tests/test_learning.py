"""The learning loop: journal capture, skill distillation guards, autonomy gate."""
import json

from template_builder import journal
from template_builder.cli import main
from template_builder.journal import ASSISTANT_ACTOR, JournalEntry, maturity
from template_builder.negotiate import ClauseResponse, NegotiationPlan, gate
from template_builder.skill import ClausePlay, SkillDraft, render_skill, slugify, validate_draft


# --- journal -------------------------------------------------------------------

def test_journal_appends_sequential_ids_and_round_trips(tmp_path):
    tpl = tmp_path / "t.json"
    tpl.write_text("{}")
    first = journal.append(str(tpl), actor="jane", kind="edit", clause_id="term",
                           why="counterparty pushed; conceded 2y", disposition="conceded")
    second = journal.append(str(tpl), actor="jane", kind="decision", clause_id="term",
                            why="held the line this time", disposition="rejected")
    assert (first.id, second.id) == (1, 2)
    entries = journal.read(str(tpl))
    assert [e.id for e in entries] == [1, 2]
    assert entries[0].disposition == "conceded"
    # append-only: the file has exactly two lines of JSONL
    assert len(journal.journal_path(str(tpl)).read_text().splitlines()) == 2


def test_maturity_counts_only_human_decisions_with_rationale(tmp_path):
    tpl = tmp_path / "t.json"
    tpl.write_text("{}")
    journal.append(str(tpl), actor="jane", kind="edit", clause_id="cap", why="traded")
    journal.append(str(tpl), actor="jane", kind="edit", clause_id="cap")           # no why
    journal.append(str(tpl), actor=ASSISTANT_ACTOR, kind="edit", clause_id="cap",
                   why="assistant acted")                                          # not human
    journal.append(str(tpl), actor="jane", kind="edit", clause_id="other", why="x")
    entries = journal.read(str(tpl))
    assert maturity(entries, "cap") == 1


def test_cli_edit_journals_with_rationale(tmp_path, template_dict, capsys):
    tpl = tmp_path / "nda.json"
    tpl.write_text(json.dumps(template_dict))
    new_text = tmp_path / "new.txt"
    new_text.write_text("This Agreement continues for {{term_years}} years.")
    code = main(["edit", "replace-text", str(tpl), "term", "default",
                 "--file", str(new_text), "--why", "counterparty required a fixed term",
                 "--counterparty", "Halloway", "--disposition", "accepted",
                 "--actor", "jane@firm.com"])
    assert code == 0
    entries = journal.read(str(tpl))
    assert entries[-1].kind == "edit"
    assert entries[-1].clause_id == "term"
    assert entries[-1].why == "counterparty required a fixed term"
    assert entries[-1].counterparty == "Halloway"
    assert entries[-1].actor == "jane@firm.com"


def test_cli_edit_without_why_warns(tmp_path, template_dict, capsys):
    tpl = tmp_path / "nda.json"
    tpl.write_text(json.dumps(template_dict))
    new_text = tmp_path / "new.txt"
    new_text.write_text("New text.")
    main(["edit", "replace-text", str(tpl), "term", "default", "--file", str(new_text)])
    assert "without --why" in capsys.readouterr().out


# --- skill distillation guards ---------------------------------------------------

def _entries(*ids):
    return [JournalEntry(id=i, ts="2026-07-10T00:00:00", actor="jane", kind="edit",
                         clause_id="term", why="reason") for i in ids]


def test_validate_draft_drops_uncited_and_unknown_plays(template):
    draft = SkillDraft(overview="o", notes=[], plays=[
        ClausePlay(clause_id="term", position="hold 2y", fallbacks=[], red_lines=[],
                   citations=[1]),
        ClausePlay(clause_id="term", position="uncited", fallbacks=[], red_lines=[],
                   citations=[99]),                       # citation doesn't resolve
        ClausePlay(clause_id="ghost", position="x", fallbacks=[], red_lines=[],
                   citations=[1]),                        # clause doesn't exist
    ])
    kept, notes = validate_draft(draft, template, _entries(1, 2))
    assert [p.clause_id for p in kept] == ["term"]
    assert any("uncited" in n for n in notes)
    assert any("ghost" in n for n in notes)


def test_render_skill_is_valid_claude_skill_markdown(template):
    plays = [ClausePlay(clause_id="term", position="Hold a 2-year term.",
                        fallbacks=["3 years with a break right"],
                        red_lines=["never evergreen"], citations=[1, 2])]
    md = render_skill("Non-Disclosure Agreement",
                      SkillDraft(overview="Overview.", plays=plays, notes=["check X"]),
                      plays, entry_count=2, threshold=10)
    assert md.startswith("---\n")
    assert "name: non-disclosure-agreement-playbook" in md
    assert "autonomy_threshold: 10" in md
    assert "### term" in md and "[j:1] [j:2]" in md
    assert "never evergreen" in md
    assert slugify("Non-Disclosure Agreement") == "non-disclosure-agreement"


# --- the autonomy gate ------------------------------------------------------------

def _plan(stance, clause_id="term", red_line=False):
    return NegotiationPlan(summary="s", responses=[ClauseResponse(
        clause_id=clause_id, stance=stance, rationale="because [j:1]",
        proposed_text="new text" if stance == "counter" else None,
        red_line_implicated=red_line)])


PLAYBOOK = "### term\n**Position** — hold [j:1]"
GOOD_REPLAY = {"term": {"agree": 9, "total": 10}}  # earned autonomy evidence


def test_gate_escalates_immature_clauses(template):
    entries = _entries(1, 2)  # maturity 2 < threshold 10
    plan = gate(_plan("counter"), template, PLAYBOOK, entries, threshold=10)
    assert plan.responses[0].stance == "escalate"
    assert "maturity 2/10" in plan.responses[0].rationale
    assert plan.responses[0].proposed_text is None


def test_gate_allows_mature_playbooked_clauses(template):
    entries = _entries(*range(1, 12))  # maturity 11 >= 10
    plan = gate(_plan("counter"), template, PLAYBOOK, entries, threshold=10,
                replay_scores=GOOD_REPLAY)
    assert plan.responses[0].stance == "counter"
    assert plan.responses[0].proposed_text == "new text"


def test_gate_requires_replay_evidence_not_just_maturity(template):
    entries = _entries(*range(1, 12))  # maturity 11 >= 10, but nothing replayed
    plan = gate(_plan("counter"), template, PLAYBOOK, entries, threshold=10)
    assert plan.responses[0].stance == "escalate"
    assert "tb skill replay" in plan.responses[0].rationale


def test_gate_escalates_without_playbook_play(template):
    entries = _entries(*range(1, 12))
    plan = gate(_plan("counter"), template, "### other-clause\nstuff", entries, threshold=10)
    assert plan.responses[0].stance == "escalate"
    assert "no playbook play" in plan.responses[0].rationale


def test_gate_red_line_never_conceded_silently(template):
    entries = _entries(*range(1, 12))
    plan = gate(_plan("counter", red_line=True), template, PLAYBOOK, entries,
                threshold=10, replay_scores=GOOD_REPLAY)
    assert plan.responses[0].stance == "escalate"
    assert "red line" in plan.responses[0].rationale
    # ...but an outright rejection of a red-line ask is allowed to stand
    plan = gate(_plan("reject", red_line=True), template, PLAYBOOK, entries,
                threshold=10, replay_scores=GOOD_REPLAY)
    assert plan.responses[0].stance == "reject"


def test_gate_escalates_hallucinated_clauses(template):
    # a response for a clause that doesn't exist reaches a human, never the bin
    plan = gate(_plan("counter", clause_id="not-a-clause"), template, PLAYBOOK,
                _entries(1), threshold=10)
    assert plan.responses[0].stance == "escalate"
    assert "not a clause" in plan.responses[0].rationale
    assert plan.responses[0].proposed_text is None
