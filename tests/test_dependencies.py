"""Semantic dependency edges: model, hashing, validation gates, edit API."""
import pytest

from template_builder import approve, edit, validate
from template_builder.model import Dependency, template_from_dict, template_hash


def _dep(template_dict, from_clause, to_clause, kind, note="because reasons"):
    template_dict.setdefault("dependencies", []).append(
        {"from_clause": from_clause, "to_clause": to_clause, "kind": kind, "note": note})
    return template_dict


# --- model / hashing -----------------------------------------------------------

def test_dependencies_round_trip_and_default_empty(template_dict):
    template = template_from_dict(template_dict)
    assert template.dependencies == []
    _dep(template_dict, "obligations", "definitions", "relies-on")
    template = template_from_dict(template_dict)
    assert template.dependencies[0].describe().startswith("obligations relies-on definitions")


def test_template_hash_covers_dependency_map(template):
    before = template_hash(template)
    template.dependencies.append(Dependency(
        from_clause="obligations", to_clause="definitions",
        kind="subject-to", note="drafted assuming the defined scope"))
    assert template_hash(template) != before
    # ...and the note itself is content: changing it changes the hash
    with_note = template_hash(template)
    template.dependencies[0].note = "a different rationale"
    assert template_hash(template) != with_note


def test_empty_dependency_list_keeps_legacy_hash(template):
    # templates that predate the feature must not have their certificates
    # invalidated by the field's existence
    h1 = template_hash(template)
    template.dependencies = []
    assert template_hash(template) == h1


def test_dependency_edit_invalidates_certificate_not_clauses(template):
    coverage = validate.validate(template)[1]
    approve.approve(template, "alice", coverage)
    edit.add_dependency(template, "obligations", "definitions", "relies-on",
                        "obligations assume the defined scope")
    status = approve.status(template)
    assert set(status.values()) == {approve.APPROVED}       # no clause text changed
    assert approve.structure_current(template) is False     # but the wiring did


# --- validation gates -----------------------------------------------------------

def test_static_checks_reject_bad_edges(template_dict):
    _dep(template_dict, "obligations", "nope", "subject-to")
    _dep(template_dict, "term", "term", "relies-on")
    _dep(template_dict, "parties", "definitions", "subject-to")
    _dep(template_dict, "parties", "definitions", "subject-to")  # duplicate
    _dep(template_dict, "term", "definitions", "relies-on", note="   ")
    findings, _ = validate.validate(template_from_dict(template_dict))
    messages = [str(f) for f in validate.errors(findings)]
    assert any("nonexistent clause 'nope'" in m for m in messages)
    assert any("cannot depend on itself" in m for m in messages)
    assert any("duplicate subject-to dependency" in m for m in messages)
    warnings = [f.message for f in findings if f.level == "warning"]
    assert any("no note" in w for w in warnings)


def test_unknown_kind_rejected_at_load(template_dict):
    _dep(template_dict, "obligations", "definitions", "overrides")
    with pytest.raises(Exception):
        template_from_dict(template_dict)


def test_sweep_refuses_configuration_missing_a_dependency_target(template_dict):
    # term (always included) relies on the OPTIONAL non-solicit clause:
    # any configuration with include_non_solicit=False must be an error.
    _dep(template_dict, "term", "non-solicit", "relies-on",
         note="the term's tail period assumes the non-solicit restriction")
    findings, _ = validate.validate(template_from_dict(template_dict))
    errors = [f.message for f in validate.errors(findings)]
    assert any("'term' is relies-on 'non-solicit', which is excluded" in m for m in errors)
    assert any("include_non_solicit=False" in m for m in errors)
    assert any("assumes the non-solicit restriction" in m for m in errors)  # note surfaced


def test_sweep_trade_off_is_symmetric(template_dict):
    _dep(template_dict, "non-solicit", "term", "trade-off",
         note="short term accepted in exchange for the non-solicit")
    findings, _ = validate.validate(template_from_dict(template_dict))
    # non-solicit excluded leaves term alone -> symmetric closure error
    assert any("one side of a trade-off" in f.message for f in validate.errors(findings))


def test_dependency_respecting_configurations_pass(template_dict):
    # obligations (always) subject-to definitions (always): no config can break it
    _dep(template_dict, "obligations", "definitions", "subject-to")
    findings, _ = validate.validate(template_from_dict(template_dict))
    assert validate.errors(findings) == []


# --- edit API --------------------------------------------------------------------

@pytest.fixture
def wired(template):
    edit.add_dependency(template, "non-solicit", "term", "subject-to",
                        "the restriction period is measured against the term")
    return template


def test_editing_depended_on_clause_flags_dependents_with_note(wired):
    result = edit.replace_text(wired, "term", "default", "The term is now ten years.")
    assert "non-solicit" in result.review
    assert any("review 'non-solicit'" in m and "measured against the term" in m
               for m in result.messages)


def test_editing_the_dependent_does_not_flag_upstream(wired):
    result = edit.replace_text(wired, "non-solicit", "default", "No soliciting, ever.")
    assert "term" not in result.review  # subject-to is directional


def test_trade_off_flags_both_directions(template):
    edit.add_dependency(template, "non-solicit", "term", "trade-off", "package deal")
    for edited, expected in [("term", "non-solicit"), ("non-solicit", "term")]:
        result = edit.replace_text(template, edited, "default", f"{edited} changed.")
        assert expected in result.review


def test_remove_clause_refused_while_edges_touch_it(wired):
    edit.replace_text(wired, "non-solicit", "default", "No refs here.")  # drop {{ref:term}}
    with pytest.raises(edit.EditError, match="dependency edges"):
        edit.remove_clause(wired, "term")
    edit.remove_dependency(wired, "non-solicit", "term")
    edit.remove_clause(wired, "term")  # now allowed


def test_add_dependency_guards(template):
    with pytest.raises(edit.EditError, match="kind"):
        edit.add_dependency(template, "term", "definitions", "overrides", "x")
    with pytest.raises(edit.EditError, match="note"):
        edit.add_dependency(template, "term", "definitions", "subject-to", "  ")
    with pytest.raises(KeyError):
        edit.add_dependency(template, "term", "nope", "subject-to", "x")
    edit.add_dependency(template, "term", "definitions", "subject-to", "x")
    with pytest.raises(edit.EditError, match="already exists"):
        edit.add_dependency(template, "term", "definitions", "subject-to", "x")
    with pytest.raises(edit.EditError, match="no dependency"):
        edit.remove_dependency(template, "definitions", "term")
