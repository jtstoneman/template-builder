"""Regression tests for the adversarially-verified review findings (round 2)."""
import copy
import json

import pytest

from template_builder import approve, edit, validate
from template_builder.cli import main
from template_builder.model import TemplateError, load, save, template_from_dict
from template_builder.render import RenderError, render_markdown


# --- sweep-space correctness -------------------------------------------------

def _with_booleans(template_dict, n):
    for i in range(n):
        template_dict["variables"].append(
            {"name": f"flag_{i}", "type": "boolean", "question": f"Flag {i}?"})
    return template_dict


def test_duplicate_choice_values_do_not_hang_the_sampler(template_dict):
    # 6 choice vars with a duplicated value: naive total 3**6=729 > 256 but
    # only 64 distinct configs — this used to loop forever.
    for i in range(6):
        template_dict["variables"].append(
            {"name": f"pick_{i}", "type": "choice", "question": "?",
             "choices": ["A", "B", "A"]})
    findings, coverage = validate.validate(template_from_dict(template_dict))
    assert coverage["configurations_total"] == 8 * 64  # fixture 8 x distinct 2**6
    assert any("duplicate choice" in f.message for f in validate.errors(findings))


def test_empty_choice_list_reports_error_without_crashing(template_dict):
    template_dict["variables"].append(
        {"name": "broken", "type": "choice", "question": "?", "choices": []})
    for i in range(9):  # push past MAX_EXHAUSTIVE so the sampling path runs
        template_dict["variables"].append(
            {"name": f"flag_{i}", "type": "boolean", "question": "?"})
    findings, coverage = validate.validate(template_from_dict(template_dict))
    assert any("at least two choices" in f.message for f in validate.errors(findings))
    assert coverage["configurations_tested"] == 200  # sampled, not crashed


def test_sample_target_capped_by_distinct_space(template_dict):
    # 512 distinct > 256 exhaustive cap, sample capped at SAMPLE_SIZE
    for i in range(6):
        template_dict["variables"].append(
            {"name": f"flag_{i}", "type": "boolean", "question": "?"})
    _, coverage = validate.validate(template_from_dict(template_dict))
    assert coverage["exhaustive"] is False
    assert coverage["configurations_tested"] == 200


# --- term closure ------------------------------------------------------------

def test_term_closure_ignores_placeholder_names_and_substrings(template_dict):
    # Defined term "term" must not match {{term_years}} or "termination".
    template_dict["clauses"][1]["defines"] = ["term"]
    template_dict["clauses"][1]["variants"][0]["text"] += (
        ' "term" means the period of this Agreement.')
    template_dict["clauses"][1]["include_when"] = "is_mutual"
    # neuter the one genuine word-usage of "term" so only the placeholder/
    # substring cases below remain in the swept texts
    template_dict["clauses"][3]["variants"][0]["text"] = (
        "Neither party shall solicit the other's employees while {{ref:term}} runs.")
    template_dict["clauses"][4]["variants"][0]["text"] = (
        "Lasts {{term_years}} years until termination.")
    findings, _ = validate.validate(template_from_dict(template_dict))
    assert not any("uses defined term 'term'" in f.message for f in findings)


def test_term_closure_rejects_definer_whose_selected_text_lacks_the_term(template_dict):
    # Declaring `defines: ["term"]` is not enough — the selected variant's
    # text must actually state the definition.
    template_dict["clauses"][1]["defines"] = ["term"]  # text never says "term"
    findings, _ = validate.validate(template_from_dict(template_dict))
    flagged = [f for f in findings if "uses defined term 'term'" in f.message]
    assert flagged, "false definition declaration must not satisfy closure"


def test_term_closure_still_catches_real_usage(template_dict):
    template_dict["clauses"][1]["include_when"] = "is_mutual"  # gate definitions
    # drop the cross-reference so rendering succeeds and closure itself is tested
    template_dict["clauses"][2]["variants"][1]["text"] = (
        "The receiving party shall protect the disclosing party's Confidential Information.")
    findings, _ = validate.validate(template_from_dict(template_dict))
    # one-way obligations use "Confidential Information" while its definition is excluded
    assert any("uses defined term 'Confidential Information'" in f.message
               for f in validate.errors(findings))


def test_empty_defines_entry_is_static_error_not_spurious_closure(template_dict):
    template_dict["clauses"][1]["defines"].append("")
    findings, _ = validate.validate(template_from_dict(template_dict))
    messages = [f.message for f in validate.errors(findings)]
    assert any("empty or whitespace-only term" in m for m in messages)
    assert not any("uses defined term ''" in m for m in messages)


# --- placeholder grammar -----------------------------------------------------

def test_digit_leading_clause_id_ref_round_trips(template_dict):
    template_dict["clauses"][1]["id"] = "409a-definitions"
    template_dict["clauses"][2]["variants"][0]["text"] = (
        "Each party shall protect the other's Confidential Information "
        "as defined in {{ref:409a-definitions}}.")
    template_dict["clauses"][2]["variants"][1]["text"] = (
        "The receiving party shall protect Confidential Information "
        "as defined in {{ref:409a-definitions}}.")
    template = template_from_dict(template_dict)
    findings, _ = validate.validate(template)
    assert validate.errors(findings) == []


def test_triple_brace_is_a_static_error_and_render_error(template_dict, answers):
    template_dict["clauses"][4]["variants"][0]["text"] = (
        "Continues for {{{term_years}}} years.")
    template = template_from_dict(template_dict)
    findings, _ = validate.validate(template)
    assert any("3+ consecutive braces" in f.message or "3+ braces" in f.message
               for f in validate.errors(findings))
    with pytest.raises(RenderError):
        render_markdown(template, answers)


def test_malformed_placeholder_is_static_error(template_dict):
    template_dict["clauses"][0]["variants"][0]["text"] += " See {{not a placeholder!}}."
    findings, _ = validate.validate(template_from_dict(template_dict))
    assert any("malformed placeholder" in f.message for f in validate.errors(findings))


# --- render robustness -------------------------------------------------------

def test_non_finite_number_is_a_render_error_not_a_crash(template, answers):
    answers["term_years"] = float("inf")
    with pytest.raises(RenderError, match="finite"):
        render_markdown(template, answers)
    answers["term_years"] = float("nan")
    with pytest.raises(RenderError, match="finite"):
        render_markdown(template, answers)


def test_non_mapping_answers_is_a_render_error(template):
    for bad in ([{"party_1": "Acme"}], 42, "text"):
        with pytest.raises(RenderError, match="JSON object"):
            render_markdown(template, bad)


def test_duplicate_clause_ids_refuse_to_render(template_dict, answers):
    clone = copy.deepcopy(template_dict["clauses"][4])
    template_dict["clauses"].append(clone)
    with pytest.raises(RenderError, match="duplicate clause id"):
        render_markdown(template_from_dict(template_dict), answers)


# --- CLI robustness ----------------------------------------------------------

def test_cli_malformed_answers_json_is_clean_error(tmp_path, template_dict, capsys):
    tpl = tmp_path / "t.json"
    tpl.write_text(json.dumps(template_dict))
    bad = tmp_path / "answers.json"
    bad.write_text("{not json")
    code = main(["render", str(tpl), "-a", str(bad), "-o", str(tmp_path / "o.md"),
                 "--allow-unapproved"])
    assert code == 1
    assert "not valid JSON" in capsys.readouterr().err


def test_cli_edit_without_subcommand_shows_edit_help(capsys):
    assert main(["edit"]) == 1
    assert "replace-text" in capsys.readouterr().out


# --- model / approval semantics ----------------------------------------------

def test_unknown_keys_in_template_file_are_rejected(template_dict):
    template_dict["clauses"][0]["variants"][0]["variannt_text"] = "typo"
    with pytest.raises(TemplateError):
        template_from_dict(template_dict)


def test_save_is_atomic_and_round_trips(tmp_path, template):
    path = tmp_path / "t.json"
    save(template, str(path))
    assert load(str(path)).to_dict() == template.to_dict()
    assert not [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]


def test_reapproval_preserves_audit_trail(template):
    coverage = validate.validate(template)[1]
    approve.approve(template, "alice", coverage, date="2026-01-01")
    edit.replace_text(template, "term", "default", "New term text.")
    approve.approve(template, "bob", coverage, date="2026-02-02")
    by_id = {a.clause_id: a for a in template.approvals}
    assert by_id["term"].by == "bob"            # re-approved by bob
    assert by_id["parties"].by == "alice"       # untouched: alice's sign-off kept
    assert by_id["parties"].date == "2026-01-01"


def test_clause_reordering_is_caught_by_structure_check(template):
    coverage = validate.validate(template)[1]
    approve.approve(template, "alice", coverage)
    assert approve.structure_current(template) is True
    template.clauses.append(template.clauses.pop(0))  # reorder: renumbers everything
    status = approve.status(template)
    assert set(status.values()) == {approve.APPROVED}  # per-clause hashes blind to it
    assert approve.structure_current(template) is False  # the certificate is not


def test_unswept_condition_variables_reported(template_dict):
    template_dict["clauses"][3]["include_when"] = "include_non_solicit and term_years >= 3"
    findings, coverage = validate.validate(template_from_dict(template_dict))
    assert coverage["unswept_condition_variables"] == ["term_years"]
    assert any("single sample value" in f.message for f in findings)
