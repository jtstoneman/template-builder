"""Tests for the condition mini-language: parse, evaluate, variables_in.

Conditions are a strict whitelist subset of Python expressions (names,
constants, boolean ops, comparisons, list/tuple literals). Everything else
must raise ConditionError — never eval(), never a raw TypeError.
"""
import ast

import pytest

from template_builder.conditions import ConditionError, evaluate, parse, variables_in


# ---------------------------------------------------------------------------
# parse
# ---------------------------------------------------------------------------

def test_parse_returns_ast_expression():
    tree = parse('is_mutual and term_years >= 3')
    assert isinstance(tree, ast.Expression)


def test_parse_syntax_error_becomes_condition_error():
    with pytest.raises(ConditionError) as exc:
        parse("term_years ==")
    assert "term_years ==" in str(exc.value)


@pytest.mark.parametrize("bad", ["", "   ", "\n\t"])
def test_parse_rejects_empty_and_blank_strings(bad):
    with pytest.raises(ConditionError, match="non-empty string"):
        parse(bad)


@pytest.mark.parametrize("bad", [None, 5, 3.14, True, ["is_mutual"], {"expr": "x"}, b"is_mutual"])
def test_parse_rejects_non_string_input(bad):
    with pytest.raises(ConditionError, match="non-empty string"):
        parse(bad)


# ---------------------------------------------------------------------------
# boolean logic: and / or / not, bare names, truthiness
# ---------------------------------------------------------------------------

def test_bare_boolean_name(answers):
    assert evaluate("is_mutual", answers) is True
    assert evaluate("include_non_solicit", answers) is False


def test_not(answers):
    assert evaluate("not include_non_solicit", answers) is True
    assert evaluate("not is_mutual", answers) is False


@pytest.mark.parametrize("expr, expected", [
    ("is_mutual and term_years >= 3", True),
    ("is_mutual and include_non_solicit", False),
    ("is_mutual or include_non_solicit", True),
    ("include_non_solicit or term_years > 10", False),
    ("not include_non_solicit and is_mutual", True),
    ("is_mutual and is_mutual and not include_non_solicit", True),
])
def test_and_or_not_combinations(answers, expr, expected):
    assert evaluate(expr, answers) is expected


def test_and_or_short_circuit_like_python(answers):
    # The right operand is never evaluated, so its unknown name never errors.
    assert evaluate("include_non_solicit and no_such_var", answers) is False
    assert evaluate("is_mutual or no_such_var", answers) is True


def test_result_is_coerced_to_bool(answers):
    # a truthy non-boolean answer still comes back as a real bool
    assert evaluate("term_years", answers) is True
    assert evaluate("0", {}) is False
    assert evaluate("''", {}) is False


# ---------------------------------------------------------------------------
# comparison operators
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr, expected", [
    ("term_years == 3", True),
    ("term_years == 4", False),
    ("term_years != 4", True),
    ("term_years != 3", False),
    ("term_years < 5", True),
    ("term_years < 3", False),
    ("term_years <= 3", True),
    ("term_years <= 2", False),
    ("term_years > 2", True),
    ("term_years > 3", False),
    ("term_years >= 3", True),
    ("term_years >= 4", False),
])
def test_numeric_comparisons(answers, expr, expected):
    assert evaluate(expr, answers) is expected


def test_string_equality(answers):
    assert evaluate('governing_law == "New York"', answers) is True
    assert evaluate('governing_law != "Delaware"', answers) is True
    assert evaluate('governing_law == "Delaware"', answers) is False


def test_constant_on_the_left(answers):
    assert evaluate('"New York" == governing_law', answers) is True
    assert evaluate("3 == term_years", answers) is True


def test_float_constants(answers):
    assert evaluate("term_years > 2.5", answers) is True
    assert evaluate("term_years < 2.5", answers) is False


# ---------------------------------------------------------------------------
# in / not in with list and tuple literals
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr, expected", [
    ('governing_law in ["New York", "Delaware"]', True),
    ('governing_law in ["Texas", "Delaware"]', False),
    ('governing_law not in ["Texas"]', True),
    ('governing_law not in ["New York"]', False),
    ("term_years in [1, 2, 3]", True),
    ('governing_law in ("New York", "Delaware")', True),  # tuple literal
])
def test_membership(answers, expr, expected):
    assert evaluate(expr, answers) is expected


def test_membership_list_may_contain_variables(answers):
    assert evaluate('party_1 in [party_1, "Someone Else"]', answers) is True


def test_in_with_non_container_raises_condition_error(answers):
    with pytest.raises(ConditionError):
        evaluate("term_years in 5", answers)


# ---------------------------------------------------------------------------
# chained comparisons
# ---------------------------------------------------------------------------

def test_chained_comparison_true(answers):
    assert evaluate("1 <= term_years <= 5", answers) is True


def test_chained_comparison_false_each_side(answers):
    answers["term_years"] = 7
    assert evaluate("1 <= term_years <= 5", answers) is False
    answers["term_years"] = 0
    assert evaluate("1 <= term_years <= 5", answers) is False


def test_chained_comparison_mixed_ops(answers):
    assert evaluate("1 < term_years < 5 != 6", answers) is True


# ---------------------------------------------------------------------------
# unknown variables
# ---------------------------------------------------------------------------

def test_unknown_variable_raises_condition_error(answers):
    with pytest.raises(ConditionError) as exc:
        evaluate("no_such_var", answers)
    assert "no_such_var" in str(exc.value)


def test_unknown_variable_inside_comparison(answers):
    with pytest.raises(ConditionError, match="unknown variable"):
        evaluate("mystery == 3", answers)


def test_unknown_variable_with_empty_answers():
    with pytest.raises(ConditionError, match="unknown variable"):
        evaluate("is_mutual", {})


# ---------------------------------------------------------------------------
# disallowed syntax — each must raise ConditionError at parse time
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("expr", [
    "len(x)",                 # function call
    "x.y",                    # attribute access
    "x[0]",                   # subscript
    "lambda: True",           # lambda
    'f"{x}"',                 # f-string
    "x + 1",                  # arithmetic BinOp
    "x - 1",
    "x * 2",
    "(x := 1)",               # walrus
    "-x",                     # unary minus (only 'not' is allowed)
    "x if y else z",          # ternary
    "x is None",              # 'is' comparator not whitelisted
    "{1: 2}",                 # dict literal
    "[i for i in x]",         # comprehension
    "__import__('os')",       # the classic escape attempt
], ids=lambda e: e)
def test_disallowed_syntax_rejected_by_parse(expr):
    with pytest.raises(ConditionError):
        parse(expr)


def test_disallowed_syntax_rejected_by_evaluate_and_variables_in(answers):
    # evaluate() and variables_in() go through parse(), so they reject too.
    with pytest.raises(ConditionError):
        evaluate("term_years + 1 == 4", answers)
    with pytest.raises(ConditionError):
        variables_in("term_years + 1 == 4")


def test_none_constant_rejected():
    with pytest.raises(ConditionError):
        parse("x == None")


# ---------------------------------------------------------------------------
# incompatible comparisons surface as ConditionError, not TypeError
# ---------------------------------------------------------------------------

def test_comparing_incompatible_constants_raises_condition_error():
    with pytest.raises(ConditionError) as exc:
        evaluate('"a" < 1', {})
    assert "cannot compare" in str(exc.value)


def test_comparing_incompatible_variable_raises_condition_error(answers):
    # term_years is a number; comparing to a string must not leak a TypeError
    with pytest.raises(ConditionError, match="cannot compare"):
        evaluate('term_years < "3"', answers)


def test_incompatible_comparison_is_not_a_type_error(answers):
    try:
        evaluate('"a" < 1', answers)
    except ConditionError:
        pass
    else:  # pragma: no cover
        pytest.fail("expected ConditionError")


# ---------------------------------------------------------------------------
# variables_in
# ---------------------------------------------------------------------------

def test_variables_in_simple():
    assert variables_in("is_mutual and term_years >= 3") == {"is_mutual", "term_years"}


def test_variables_in_membership_and_chained():
    assert variables_in('governing_law in ["New York", "Delaware"]') == {"governing_law"}
    assert variables_in("1 <= term_years <= 5") == {"term_years"}


def test_variables_in_collapses_duplicates():
    assert variables_in("x == 1 or x == 2 or x == 3") == {"x"}


def test_variables_in_finds_names_inside_list_literals():
    assert variables_in("party_1 in [party_2, other]") == {"party_1", "party_2", "other"}


def test_variables_in_constants_only_is_empty():
    assert variables_in("True") == set()
    assert variables_in('"New York" == "New York"') == set()


def test_variables_in_validates_input():
    with pytest.raises(ConditionError):
        variables_in("")
    with pytest.raises(ConditionError):
        variables_in(None)


# ---------------------------------------------------------------------------
# constants and edge inputs through evaluate
# ---------------------------------------------------------------------------

def test_boolean_constants_evaluate_without_answers():
    assert evaluate("True", {}) is True
    assert evaluate("False", {}) is False
    assert evaluate("not False", {}) is True


def test_evaluate_rejects_empty_and_non_string_input():
    for bad in ("", "   ", None, 7):
        with pytest.raises(ConditionError):
            evaluate(bad, {})


def test_realistic_variant_condition_from_fixture(answers):
    # the exact condition the NDA template uses for its NY variant
    assert evaluate('governing_law == "New York"', answers) is True
    answers["governing_law"] = "Delaware"
    assert evaluate('governing_law == "New York"', answers) is False
