"""Tests for the constrained edit API (template_builder/edit.py) and the CLI
(template_builder/cli.py, driven via cli.main([...]) — no subprocesses, no LLM).

Complements tests/test_core.py: nothing here re-asserts the anchor semantics
(variant selection, numbering, hashing, approval invalidation on the API level).
"""
import json
import re

import pytest

from template_builder import approve as approve_mod
from template_builder import edit, model, validate
from template_builder.cli import main


# ---------------------------------------------------------------------------
# Edit API
# ---------------------------------------------------------------------------

def test_add_variant_conditional_goes_before_default(template):
    # "obligations" is [mutual (conditional), one-way (default)]: the new
    # conditional variant must land before the default, or it is unreachable.
    result = edit.add_variant(template, "obligations", "carve-out",
                              "A narrower obligation.", when="include_non_solicit")
    assert [v.id for v in template.clause("obligations").variants] == \
        ["mutual", "carve-out", "one-way"]
    assert result.touched == ["obligations"]

    # Same rule on a single-default clause: conditional inserted at the front.
    edit.add_variant(template, "term", "evergreen", "It never ends.",
                     when="include_non_solicit")
    assert [v.id for v in template.clause("term").variants] == ["evergreen", "default"]


def test_add_variant_second_default_rejected(template):
    with pytest.raises(edit.EditError, match="already has a default variant"):
        edit.add_variant(template, "term", "another-default", "Alt text.", when=None)
    # refused edit leaves the clause untouched
    assert [v.id for v in template.clause("term").variants] == ["default"]


def test_add_variant_duplicate_id_rejected(template):
    with pytest.raises(edit.EditError, match="already has a variant 'mutual'"):
        edit.add_variant(template, "obligations", "mutual", "Different text.",
                         when="is_mutual")
    assert len(template.clause("obligations").variants) == 2


def test_remove_variant_refuses_removing_the_only_variant(template):
    with pytest.raises(edit.EditError, match="only variant"):
        edit.remove_variant(template, "term", "default")
    assert [v.id for v in template.clause("term").variants] == ["default"]

    # With two variants removal works; the survivor then becomes irremovable.
    edit.remove_variant(template, "obligations", "mutual")
    assert [v.id for v in template.clause("obligations").variants] == ["one-way"]
    with pytest.raises(edit.EditError, match="only variant"):
        edit.remove_variant(template, "obligations", "one-way")


def test_add_clause_after_inserts_in_position(template):
    edit.add_clause(template, "exclusions", "Exclusions",
                    "Information already public is not Confidential Information.",
                    after="definitions")
    ids = [c.id for c in template.clauses]
    assert ids.index("exclusions") == ids.index("definitions") + 1

    # without after= the clause is appended at the end
    edit.add_clause(template, "notices", "Notices", "Notices must be in writing.")
    assert template.clauses[-1].id == "notices"
    # new clauses get a single default variant
    assert [(v.id, v.when) for v in template.clause("notices").variants] == [("default", None)]

    # after= pointing at a nonexistent clause is a KeyError (unknown node)
    with pytest.raises(KeyError):
        edit.add_clause(template, "misc", "Misc", "Text.", after="does-not-exist")


def test_add_clause_duplicate_id_rejected(template):
    before = [c.id for c in template.clauses]
    with pytest.raises(edit.EditError, match="already exists"):
        edit.add_clause(template, "term", "Term (again)", "Duplicate.")
    assert [c.id for c in template.clauses] == before


def test_remove_clause_blocked_by_referrer_then_allowed(template):
    # obligations cross-references definitions, so definitions is locked...
    with pytest.raises(edit.EditError) as exc:
        edit.remove_clause(template, "definitions")
    assert "obligations" in str(exc.value)
    assert any(c.id == "definitions" for c in template.clauses)

    # ...until the referrer goes; approvals for the removed clause are purged.
    coverage = validate.validate(template)[1]
    approve_mod.approve(template, "Reviewer", coverage)
    edit.remove_clause(template, "obligations")
    edit.remove_clause(template, "definitions")
    remaining = {c.id for c in template.clauses}
    assert "definitions" not in remaining and "obligations" not in remaining
    assert not any(a.clause_id in ("definitions", "obligations")
                   for a in template.approvals)


def test_add_variable_rejects_bad_type_and_choiceless_choice(template):
    with pytest.raises(edit.EditError, match="unknown variable type"):
        edit.add_variable(template, "count", "integer", "How many?")
    with pytest.raises(edit.EditError, match="at least two choices"):
        edit.add_variable(template, "venue", "choice", "Which venue?")
    with pytest.raises(edit.EditError, match="at least two choices"):
        edit.add_variable(template, "venue", "choice", "Which venue?", choices=["NY"])
    with pytest.raises(edit.EditError, match="already exists"):
        edit.add_variable(template, "party_1", "string", "Name again?")
    # none of the refused edits appended anything
    assert [v.name for v in template.variables if v.name in ("count", "venue")] == []

    edit.add_variable(template, "venue", "choice", "Which venue?",
                      choices=["New York", "London"])
    assert template.variable("venue").choices == ["New York", "London"]


def test_referrers_correctness(template):
    assert edit.referrers(template, "definitions") == ["obligations"]
    assert edit.referrers(template, "term") == ["non-solicit"]
    assert edit.referrers(template, "parties") == []
    # a clause referencing itself is not its own referrer
    template.clause("term").variants[0].text += " See {{ref:term}}."
    assert edit.referrers(template, "term") == ["non-solicit"]
    # replace_text reports the same set as its blast radius
    result = edit.replace_text(template, "definitions", "default", "New definition.")
    assert result.review == ["obligations"]


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

@pytest.fixture
def tpl_path(tmp_path, template_dict):
    path = tmp_path / "template.json"
    path.write_text(json.dumps(template_dict), encoding="utf-8")
    return path


@pytest.fixture
def answers_path(tmp_path, answers):
    path = tmp_path / "answers.json"
    path.write_text(json.dumps(answers), encoding="utf-8")
    return path


def test_cli_validate_clean_template_exits_zero(tpl_path, capsys):
    assert main(["validate", str(tpl_path)]) == 0
    out = capsys.readouterr().out
    assert "0 error(s)" in out
    assert "8 of 8 configurations rendered (exhaustive)" in out


def test_cli_questions_writes_null_skeleton(tpl_path, tmp_path, template, capsys):
    skeleton_path = tmp_path / "answers-skeleton.json"
    assert main(["questions", str(tpl_path), "-o", str(skeleton_path)]) == 0
    skeleton = json.loads(skeleton_path.read_text(encoding="utf-8"))
    assert list(skeleton) == [v.name for v in template.variables]
    assert all(value is None for value in skeleton.values())
    out = capsys.readouterr().out
    for variable in template.variables:
        assert variable.name in out
        assert variable.question in out


def test_cli_render_refuses_without_approval(tpl_path, answers_path, tmp_path, capsys):
    out_path = tmp_path / "nda.md"
    assert main(["render", str(tpl_path), "-a", str(answers_path),
                 "-o", str(out_path)]) == 1
    err = capsys.readouterr().err
    assert "refusing to render" in err
    assert "(questionnaire/schema)" in err
    assert not out_path.exists()


def test_cli_render_allow_unapproved_writes_draft(tpl_path, answers_path, tmp_path, capsys):
    out_path = tmp_path / "nda.md"
    assert main(["render", str(tpl_path), "-a", str(answers_path),
                 "-o", str(out_path), "--allow-unapproved"]) == 0
    assert "DRAFT" in capsys.readouterr().out
    doc = out_path.read_text(encoding="utf-8")
    assert "Acme Corp" in doc and "{{" not in doc


def test_cli_approve_then_render_exits_zero(tpl_path, answers_path, tmp_path, capsys):
    assert main(["approve", str(tpl_path), "--by", "Reviewer"]) == 0
    out_path = tmp_path / "nda.md"
    assert main(["render", str(tpl_path), "-a", str(answers_path),
                 "-o", str(out_path)]) == 0
    assert out_path.exists()
    # a fully approved render is not labelled a draft
    assert "DRAFT" not in capsys.readouterr().out


def test_cli_status_shows_stale_after_edit(tpl_path, tmp_path, capsys):
    assert main(["approve", str(tpl_path), "--by", "Reviewer"]) == 0
    assert main(["status", str(tpl_path)]) == 0
    assert "all approved (by Reviewer" in capsys.readouterr().out

    new_text = tmp_path / "term.txt"
    new_text.write_text("This Agreement continues indefinitely.", encoding="utf-8")
    assert main(["edit", "replace-text", str(tpl_path), "term", "default",
                 "--file", str(new_text)]) == 0
    assert "approval invalidated for: term" in capsys.readouterr().out

    assert main(["status", str(tpl_path)]) == 0
    out = capsys.readouterr().out
    assert re.search(r"^\s*term\s+stale\s*$", out, re.M)
    assert "1 stale, 0 unapproved" in out
    # only the edited clause lost its approval
    assert len(re.findall(r"\bapproved\s*$", out, re.M)) == 6


def test_cli_edit_orphan_ref_refused_without_force(tpl_path, tmp_path, capsys):
    bad_text = tmp_path / "bad.txt"
    bad_text.write_text("The parties acknowledge {{ref:phantom}}.", encoding="utf-8")
    before = tpl_path.read_bytes()

    assert main(["edit", "replace-text", str(tpl_path), "parties", "default",
                 "--file", str(bad_text)]) == 1
    err = capsys.readouterr().err
    assert "edit NOT saved" in err
    assert "phantom" in err
    assert tpl_path.read_bytes() == before  # file untouched on refusal

    assert main(["edit", "replace-text", str(tpl_path), "parties", "default",
                 "--file", str(bad_text), "--force"]) == 0
    assert "saved WITH" in capsys.readouterr().out
    saved = model.load(str(tpl_path))
    assert "{{ref:phantom}}" in saved.clause("parties").variants[0].text


def test_cli_edit_unknown_clause_id_is_an_error_not_a_traceback(tpl_path, tmp_path, capsys):
    text = tmp_path / "text.txt"
    text.write_text("Anything.", encoding="utf-8")
    before = tpl_path.read_bytes()
    assert main(["edit", "replace-text", str(tpl_path), "ghost", "default",
                 "--file", str(text)]) == 1
    err = capsys.readouterr().err
    assert err.startswith("error:")
    assert "no clause with id 'ghost'" in err
    assert "Traceback" not in err
    assert tpl_path.read_bytes() == before

    # same contract for the other node-addressing edit commands
    assert main(["edit", "remove-clause", str(tpl_path), "ghost"]) == 1
    assert "no clause with id 'ghost'" in capsys.readouterr().err


def test_remove_variable_unused_succeeds(template):
    edit.add_variable(template, "orphan_toggle", "boolean", "Ever used?")
    result = edit.remove_variable(template, "orphan_toggle")
    assert "removed variable 'orphan_toggle'" in result.messages[0]
    assert not any(v.name == "orphan_toggle" for v in template.variables)


def test_remove_variable_refuses_when_used_in_text(template):
    # every conftest template variant references party_1 in its text
    used = next(v.name for v in template.variables
                if any(f"{{{{{v.name}}}}}" in var.text
                       for c in template.clauses for var in c.variants))
    with pytest.raises(edit.EditError, match="still used by"):
        edit.remove_variable(template, used)


def test_remove_variable_refuses_when_used_in_condition(template):
    clause = template.clauses[0]
    edit.add_variable(template, "gate_me", "boolean", "Gate?")
    edit.set_condition(template, clause.id, "gate_me")
    with pytest.raises(edit.EditError, match=clause.id):
        edit.remove_variable(template, "gate_me")
    edit.set_condition(template, clause.id, None)
    edit.remove_variable(template, "gate_me")  # now unused -> allowed


def test_remove_variable_unknown_name_is_key_error(template):
    with pytest.raises(KeyError):
        edit.remove_variable(template, "never_existed")
