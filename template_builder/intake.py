"""Optional LLM intake: pre-fill the questionnaire from a term sheet.

The LLM stays strictly upstream of the approved boundary — it outputs
structured *answers*, never document text. A Pydantic model is built
dynamically from the template's variable schema (choice variables become
Literal types), so the API cannot return an answer outside the questionnaire.
Anything not found in the term sheet is left null for a human to fill in.
"""

from collections.abc import Iterator
from typing import Literal

from pydantic import create_model

from .llm import LLMError, complete
from .model import Template, Variable

INTAKE_SYSTEM = """\
You fill in a contract questionnaire from a term sheet or deal description.

Rules:
- Answer ONLY from what the term sheet states or clearly implies. If a value
  is not stated, answer null. Never invent names, dates, amounts or terms.
- Answer every question, with the exact JSON type requested.
"""

# Every answer field is nullable ("not stated in the term sheet"), and the
# structured-outputs API caps union-typed parameters at 16 per schema — so
# large questionnaires are answered in batches.
MAX_FIELDS_PER_CALL = 14


def _answer_type(variable: Variable) -> object:
    match variable.type:
        case "boolean":
            return bool | None
        case "number":
            return float | None
        case "choice" if variable.choices:
            return Literal[tuple(variable.choices)] | None
        case "choice":
            # an unconstrained str here would break this module's guarantee
            # that answers cannot fall outside the questionnaire
            raise LLMError(f"choice variable {variable.name!r} has no choices — "
                           f"fix the template (tb validate) before intake")
        case _:
            return str | None


def _batches[T](items: list[T], size: int) -> Iterator[list[T]]:
    for start in range(0, len(items), size):
        yield items[start:start + size]


def prefill(template: Template, term_sheet_text: str) -> dict[str, object]:
    """Returns {variable_name: answer_or_None} for every template variable."""
    answers: dict[str, object] = {}
    for batch in _batches(template.variables, MAX_FIELDS_PER_CALL):
        try:
            answers_model = create_model(
                "TermSheetAnswers",
                **{v.name: (_answer_type(v), ...) for v in batch},
            )
        except Exception as e:
            names = ", ".join(v.name for v in batch)
            raise LLMError(
                f"could not build an intake model for variables [{names}] — a variable "
                f"name probably collides with a reserved Pydantic name (e.g. 'model_*') "
                f"or starts with an underscore; rename it in the template ({e})") from None
        questions = "\n".join(
            f"- {v.name} ({v.type}"
            + (f", one of {v.choices}" if v.type == "choice" else "")
            + f"): {v.question}"
            for v in batch
        )
        prompt = (f"Questionnaire for a {template.doc_type}:\n{questions}\n\n"
                  f"Term sheet:\n\n{term_sheet_text}")
        result = complete(INTAKE_SYSTEM, prompt, answers_model, max_tokens=8000)
        answers.update(result.model_dump())
    return answers
