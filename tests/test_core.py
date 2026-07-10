"""Anchor tests for the semantics that everything else depends on:
variant selection, numbering, hashing, and approval invalidation."""
import pytest

from template_builder import approve, edit, validate
from template_builder.model import clause_hash, template_from_dict
from template_builder.render import RenderError, render_markdown


def test_render_happy_path(template, answers):
    doc = render_markdown(template, answers)
    # mutual variant selected, one-way not
    assert "Each party shall protect" in doc
    assert "receiving party" not in doc
    # non-solicit excluded, so numbering closes the gap: term is clause 4
    assert "**4. Term**" in doc
    assert "Non-Solicitation" not in doc
    # cross-reference resolved to the rendered number of definitions (clause 2)
    assert "as defined in clause 2" in doc
    # variables substituted, no residue
    assert "Acme Corp" in doc and "{{" not in doc
    # NY variant via choice condition
    assert "State of New York" in doc


def test_render_is_deterministic(template, answers):
    assert render_markdown(template, answers) == render_markdown(template, answers)


def test_numbering_shifts_when_optional_clause_included(template, answers):
    answers["include_non_solicit"] = True
    doc = render_markdown(template, answers)
    assert "**4. Non-Solicitation**" in doc
    assert "**5. Term**" in doc
    # non-solicit's forward reference to term now resolves to 5
    assert "term described in clause 5" in doc


def test_render_rejects_missing_and_mistyped_answers(template, answers):
    del answers["party_1"]
    answers["is_mutual"] = "yes"          # wrong type
    answers["governing_law"] = "Texas"    # not a choice
    with pytest.raises(RenderError) as exc:
        render_markdown(template, answers)
    text = str(exc.value)
    assert "party_1" in text and "is_mutual" in text and "governing_law" in text


def test_render_fails_on_ref_to_excluded_clause(template_dict, answers):
    # Make an always-included clause reference the optional non-solicit clause.
    template_dict["clauses"][4]["variants"][0]["text"] += " See {{ref:non-solicit}}."
    template = template_from_dict(template_dict)
    answers["include_non_solicit"] = False
    with pytest.raises(RenderError) as exc:
        render_markdown(template, answers)
    assert "excluded under these answers" in str(exc.value)
    # ...and the validation sweep catches the same thing without any answers file.
    findings, _ = validate.validate(template)
    assert any("non-solicit" in f.message for f in validate.errors(findings))


def test_validate_clean_template_has_no_errors(template):
    findings, coverage = validate.validate(template)
    assert validate.errors(findings) == []
    # 2 booleans (2x2) x 1 two-way choice = 8 configurations
    assert coverage["configurations_total"] == 8
    assert coverage["exhaustive"] is True


def test_validate_flags_orphan_reference(template_dict):
    template_dict["clauses"][0]["variants"][0]["text"] += " See {{ref:does-not-exist}}."
    findings, _ = validate.validate(template_from_dict(template_dict))
    assert any("orphan cross-reference" in f.message for f in validate.errors(findings))


def test_validate_flags_unknown_variable_in_condition(template_dict):
    template_dict["clauses"][3]["include_when"] = "include_nonexistent"
    findings, _ = validate.validate(template_from_dict(template_dict))
    assert any("unknown variable" in f.message for f in validate.errors(findings))


def test_clause_hash_ignores_provenance_but_not_text(template):
    clause = template.clause("obligations")
    before = clause_hash(clause)
    clause.variants[0].provenance.append("somewhere_else.txt")
    assert clause_hash(clause) == before        # metadata: no invalidation
    clause.variants[0].text += " Amended."
    assert clause_hash(clause) != before        # content: invalidation


def test_edit_invalidates_only_touched_clause(template):
    coverage = validate.validate(template)[1]
    approve.approve(template, "Reviewer", coverage)
    assert set(approve.status(template).values()) == {approve.APPROVED}

    edit.replace_text(template, "term", "default", "This Agreement continues indefinitely.")
    status = approve.status(template)
    assert status["term"] == approve.STALE
    untouched = [s for cid, s in status.items() if cid != "term"]
    assert set(untouched) == {approve.APPROVED}


def test_remove_clause_refused_while_referenced(template):
    # non-solicit references term, so removing term must be refused.
    with pytest.raises(edit.EditError) as exc:
        edit.remove_clause(template, "term")
    assert "non-solicit" in str(exc.value)
    # removing the referrer first, then the target, works.
    edit.remove_clause(template, "non-solicit")
    edit.remove_clause(template, "term")
