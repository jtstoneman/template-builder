"""Offline tests for intake's dynamic answer model (no network)."""
from typing import Literal

import pytest

from template_builder.intake import MAX_FIELDS_PER_CALL, _answer_type, _batches
from template_builder.llm import LLMError
from template_builder.model import Variable


def _var(vtype, choices=()):
    return Variable(name="v", type=vtype, question="?", choices=list(choices))


def test_answer_types_are_nullable_and_typed():
    assert _answer_type(_var("boolean")) == bool | None
    assert _answer_type(_var("number")) == float | None
    assert _answer_type(_var("string")) == str | None
    assert _answer_type(_var("choice", ["NY", "DE"])) == Literal["NY", "DE"] | None


def test_choice_without_choices_is_refused():
    # falling back to an unconstrained str would let answers escape the
    # questionnaire — the module's whole guarantee
    with pytest.raises(LLMError, match="has no choices"):
        _answer_type(_var("choice"))


def test_batches_respect_structured_output_union_limit():
    items = list(range(23))  # the size that hit the API's 16-union limit live
    batches = list(_batches(items, MAX_FIELDS_PER_CALL))
    assert all(len(b) <= MAX_FIELDS_PER_CALL for b in batches)
    assert [x for b in batches for x in b] == items
    assert MAX_FIELDS_PER_CALL <= 16


def test_batches_empty():
    assert list(_batches([], MAX_FIELDS_PER_CALL)) == []
