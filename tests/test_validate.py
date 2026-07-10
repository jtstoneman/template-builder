"""Behavioural tests for template_builder.validate.

Complements tests/test_core.py (which anchors orphan refs on clause text,
unknown condition variables, and clean-template coverage totals). Here we
pin down:

  * each static gate's classification — what is an error vs a warning,
    and which clause/variable a finding is attributed to;
  * the sweep's error reporting, including that it names a concrete
    failing configuration;
  * term closure (a defined term used while its defining clause is
    excluded);
  * the deterministic-sampling fallback once the configuration space
    exceeds MAX_EXHAUSTIVE.
"""
import copy


from template_builder import validate
from template_builder.model import template_from_dict


def _validate_dict(template_dict):
    return validate.validate(template_from_dict(template_dict))


def _warnings(findings):
    return [f for f in findings if f.level == "warning"]


# ---------------------------------------------------------------------------
# Static checks: errors
# ---------------------------------------------------------------------------

def test_duplicate_clause_id_is_error(template_dict):
    template_dict["clauses"].append(copy.deepcopy(template_dict["clauses"][4]))  # second "term"
    findings, _ = _validate_dict(template_dict)
    messages = [f.message for f in validate.errors(findings)]
    assert any("duplicate clause id 'term'" in m for m in messages)


def test_duplicate_variable_name_is_error(template_dict):
    template_dict["variables"].append(copy.deepcopy(template_dict["variables"][0]))  # second "party_1"
    findings, _ = _validate_dict(template_dict)
    messages = [f.message for f in validate.errors(findings)]
    assert any("duplicate variable name 'party_1'" in m for m in messages)


def test_choice_variable_with_single_choice_is_error(template_dict):
    template_dict["variables"][6]["choices"] = ["New York"]  # governing_law
    findings, _ = _validate_dict(template_dict)
    bad = [f for f in validate.errors(findings) if f.where == "governing_law"]
    assert bad, "expected an error attributed to the choice variable itself"
    assert any("at least two choices" in f.message for f in bad)


def test_orphan_ref_error_names_clause_and_variant(template_dict):
    # Orphan ref inside a *conditional* variant (not the clause default):
    # the error must be attributed to the owning clause and name the variant.
    template_dict["clauses"][2]["variants"][0]["text"] += " See {{ref:indemnity}}."
    findings, _ = _validate_dict(template_dict)
    orphan = [f for f in validate.errors(findings) if "orphan cross-reference" in f.message]
    assert len(orphan) == 1
    assert orphan[0].where == "obligations"
    assert "'mutual'" in orphan[0].message and "'indemnity'" in orphan[0].message


# ---------------------------------------------------------------------------
# Static checks: warnings
# ---------------------------------------------------------------------------

def test_clean_template_has_no_warnings_either(template):
    # The fixture template is fully well-formed: no false-positive warnings.
    findings, _ = validate.validate(template)
    assert findings == []


def test_variant_after_default_is_unreachable_warning(template_dict):
    variants = template_dict["clauses"][2]["variants"]  # obligations: [mutual, one-way(default)]
    template_dict["clauses"][2]["variants"] = [variants[1], variants[0]]  # default now first
    findings, _ = _validate_dict(template_dict)
    assert validate.errors(findings) == []  # dead drafting, not a broken template
    unreachable = [f for f in _warnings(findings) if "unreachable" in f.message]
    assert len(unreachable) == 1
    assert unreachable[0].where == "obligations"
    assert "'mutual'" in unreachable[0].message


def test_boolean_variable_in_text_is_warning(template_dict):
    template_dict["clauses"][0]["variants"][0]["text"] += " Mutual disclosure: {{is_mutual}}."
    findings, _ = _validate_dict(template_dict)
    # It renders (as true/false), so it must not be an error...
    assert validate.errors(findings) == []
    # ...but the smell is flagged, naming the variable.
    warned = [f for f in _warnings(findings)
              if "boolean variable 'is_mutual' is substituted into text" in f.message]
    assert len(warned) == 1
    assert warned[0].where == "parties"


def test_unused_variable_is_warning(template_dict):
    template_dict["variables"].append(
        {"name": "internal_notes", "type": "string", "question": "Notes for the drafter?"})
    findings, _ = _validate_dict(template_dict)
    assert validate.errors(findings) == []
    warned = [f for f in _warnings(findings) if f.where == "internal_notes"]
    assert len(warned) == 1
    assert "never used" in warned[0].message


def test_unresolved_drafting_bracket_is_warning(template_dict):
    template_dict["clauses"][4]["variants"][0]["text"] += " Liquidated damages: [insert amount]."
    findings, _ = _validate_dict(template_dict)
    assert validate.errors(findings) == []
    warned = [f for f in _warnings(findings) if "drafting bracket" in f.message]
    assert len(warned) == 1
    assert warned[0].where == "term"
    assert "[insert amount]" in warned[0].message


def test_placeholder_inside_brackets_is_not_flagged_as_drafting_bracket(template_dict):
    # "[{{term_years}} years]" resolves at render time — no bracket warning.
    template_dict["clauses"][4]["variants"][0]["text"] += " [{{term_years}} years]"
    findings, _ = _validate_dict(template_dict)
    assert not any("drafting bracket" in f.message for f in findings)


# ---------------------------------------------------------------------------
# The configuration sweep
# ---------------------------------------------------------------------------

def test_missing_default_variant_warns_and_sweep_names_failing_config(template_dict):
    # obligations keeps only its conditional variant: no text when is_mutual is false.
    template_dict["clauses"][2]["variants"] = [template_dict["clauses"][2]["variants"][0]]
    findings, coverage = _validate_dict(template_dict)

    warned = [f for f in _warnings(findings)
              if f.where == "obligations" and "no default variant" in f.message]
    assert len(warned) == 1

    sweep_errors = [f for f in validate.errors(findings) if f.where == "sweep"]
    assert len(sweep_errors) == 1  # one distinct problem, deduplicated across configs
    message = sweep_errors[0].message
    assert "'obligations'" in message and "no variant matches" in message
    # The error names a concrete configuration that fails...
    assert "is_mutual=False" in message
    # ...and the sweep still covered the whole (small) space.
    assert coverage["exhaustive"] is True


def test_term_used_while_its_definition_is_excluded_is_sweep_error(template_dict):
    # Gate the definitions clause behind a new boolean, and drop the
    # {{ref:definitions}} cross-references so the *only* remaining link
    # between obligations and definitions is the defined term itself.
    template_dict["variables"].append(
        {"name": "include_definitions", "type": "boolean",
         "question": "Include the definitions clause?"})
    template_dict["clauses"][1]["include_when"] = "include_definitions"
    obligations = template_dict["clauses"][2]
    obligations["variants"][0]["text"] = \
        "Each party shall protect the other's Confidential Information."
    obligations["variants"][1]["text"] = \
        "The receiving party shall protect the disclosing party's Confidential Information."

    findings, coverage = _validate_dict(template_dict)

    sweep_errors = [f for f in validate.errors(findings) if f.where == "sweep"]
    assert len(sweep_errors) == 1
    message = sweep_errors[0].message
    assert "'obligations'" in message
    assert "'Confidential Information'" in message
    assert "no included clause's selected text defines it" in message
    assert "include_definitions=False" in message  # names the failing configuration
    # 3 booleans x 1 two-way choice = 16 configurations, still exhaustive.
    assert coverage["configurations_total"] == 16
    assert coverage["exhaustive"] is True


def test_config_digests_match_configurations_tested_and_are_stable(template):
    findings, coverage = validate.validate(template)
    assert coverage["configurations_tested"] == 8
    assert len(coverage["config_digests"]) == coverage["configurations_tested"]
    assert len(set(coverage["config_digests"])) == 8  # one digest per distinct config
    # Digests are a function of the template alone: same template, same record.
    assert validate.validate(template)[1]["config_digests"] == coverage["config_digests"]


# ---------------------------------------------------------------------------
# Sampling fallback for large configuration spaces
# ---------------------------------------------------------------------------

def _many_boolean_template_dict(n_flags=9):
    """A well-formed template whose toggle space (2**n) exceeds MAX_EXHAUSTIVE."""
    return {
        "schema_version": 1,
        "doc_type": "Feature-flagged Agreement",
        "sources": [],
        "variables": [
            {"name": f"flag_{i}", "type": "boolean", "question": f"Enable feature {i}?"}
            for i in range(n_flags)
        ],
        "clauses": [
            {
                "id": "body",
                "heading": "Body",
                "include_when": None,
                "defines": [],
                "variants": [
                    {"id": "all-on",
                     "when": " and ".join(f"flag_{i}" for i in range(n_flags)),
                     "provenance": [],
                     "text": "Every feature applies."},
                    {"id": "default", "when": None, "provenance": [],
                     "text": "Only the selected features apply."},
                ],
            },
        ],
        "approvals": [],
        "certificate": None,
    }


def test_large_config_space_falls_back_to_deterministic_sampling():
    template = template_from_dict(_many_boolean_template_dict(9))  # 2**9 = 512 > 256
    findings, coverage = validate.validate(template)  # must terminate, not enumerate 512

    assert validate.errors(findings) == []
    assert coverage["configurations_total"] == 512
    assert coverage["exhaustive"] is False
    assert coverage["configurations_tested"] == 200
    assert len(coverage["config_digests"]) == 200
    assert len(set(coverage["config_digests"])) == 200  # samples are distinct configs

    # Sampling is seeded: revalidating the same template tests the same configs.
    coverage_again = validate.validate(template)[1]
    assert coverage_again["config_digests"] == coverage["config_digests"]
