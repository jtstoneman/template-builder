"""Deterministic rendering: template + answers -> finished document.

No LLM is involved anywhere in this module. Given the same template and the
same answers it always produces the same document — that is the property
that lets one sign-off on the template cover every document it generates.

Rendering fails loudly rather than producing a subtly wrong contract: a
missing answer, an unknown placeholder, or a cross-reference to a clause
excluded by the current answers is an error, never silently dropped.
"""
import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from . import conditions
from .model import Clause, Template, Variant

type Answers = Mapping[str, object]

# {{variable_name}} or {{ref:clause-id}}. Variable names are snake_case;
# clause ids are kebab-case and may start with a digit ("409a-compliance") —
# the same grammar validate.CLAUSE_ID_RE and edit.REF_RE accept.
PLACEHOLDER_RE = re.compile(
    r"\{\{\s*(?:(ref:)([A-Za-z0-9][A-Za-z0-9_-]*)|([A-Za-z_][A-Za-z0-9_]*))\s*\}\}")
BRACE_RUN_RE = re.compile(r"\{{3,}|\}{3,}")


def placeholder_parts(match: re.Match) -> tuple[bool, str]:
    """(is_ref, name) for a PLACEHOLDER_RE match."""
    return bool(match.group(1)), match.group(2) or match.group(3)


class RenderError(ValueError):
    def __init__(self, problems: Iterable[str]):
        self.problems = list(problems)
        super().__init__("cannot render:\n" + "\n".join(f"  - {p}" for p in self.problems))


@dataclass(slots=True)
class PlannedClause:
    clause: Clause
    variant: Variant
    number: int
    rendered_text: str = ""


def check_answers(template: Template, answers: Answers) -> list[str]:
    """Type-check the questionnaire answers. Returns a list of problems."""
    if not isinstance(answers, Mapping):
        return [f"answers must be a JSON object mapping variable names to values, "
                f"got {type(answers).__name__}"]
    problems = []
    known = {v.name for v in template.variables}
    for name in answers:
        if name not in known:
            problems.append(f"answer {name!r} does not match any template variable")
    for variable in template.variables:
        if variable.name not in answers:
            problems.append(f"missing answer for {variable.name!r}: {variable.question}")
            continue
        value = answers[variable.name]
        if variable.type == "boolean" and not isinstance(value, bool):
            problems.append(f"{variable.name!r} must be true or false, got {value!r}")
        elif variable.type == "number" and (isinstance(value, bool) or not isinstance(value, (int, float))):
            problems.append(f"{variable.name!r} must be a number, got {value!r}")
        elif variable.type == "number" and isinstance(value, float) and not math.isfinite(value):
            problems.append(f"{variable.name!r} must be a finite number, got {value!r}")
        elif variable.type == "string" and not isinstance(value, str):
            problems.append(f"{variable.name!r} must be a string, got {value!r}")
        elif variable.type == "choice" and value not in variable.choices:
            problems.append(f"{variable.name!r} must be one of {variable.choices}, got {value!r}")
    return problems


def select_variant(clause: Clause, answers: Answers) -> Variant | None:
    """First variant whose condition holds; a variant with when=None always matches."""
    for variant in clause.variants:
        if variant.when is None or conditions.evaluate(variant.when, answers):
            return variant
    return None


def plan(template: Template, answers: Answers) -> list[PlannedClause]:
    """Decide which clauses are included, which variant each uses, and number them.

    Raises RenderError listing *all* problems found, not just the first.
    """
    problems = check_answers(template, answers)
    if problems:
        raise RenderError(problems)

    ids = [c.id for c in template.clauses]
    for duplicate in sorted({cid for cid in ids if ids.count(cid) > 1}):
        problems.append(f"duplicate clause id {duplicate!r} — cross-reference numbering "
                        f"would be ambiguous")
    if problems:
        raise RenderError(problems)

    planned = []
    number = 0
    for clause in template.clauses:
        try:
            if clause.include_when is not None and not conditions.evaluate(clause.include_when, answers):
                continue
            variant = select_variant(clause, answers)
        except conditions.ConditionError as e:
            problems.append(f"clause {clause.id!r}: {e}")
            continue
        if variant is None:
            problems.append(f"clause {clause.id!r}: no variant matches these answers "
                            f"(add a default variant with \"when\": null)")
            continue
        number += 1
        planned.append(PlannedClause(clause=clause, variant=variant, number=number))

    if problems:
        raise RenderError(problems)

    numbers = {p.clause.id: p.number for p in planned}
    all_ids = {c.id for c in template.clauses}
    for p in planned:
        p.rendered_text = _substitute(p.variant.text, p.clause.id, answers, numbers, all_ids, problems)
    if problems:
        raise RenderError(problems)
    return planned


def _format_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, float) and value.is_integer():  # never raises, unlike int(value)
        return str(int(value))
    return str(value)


def _substitute(text: str, clause_id: str, answers: Answers, numbers: dict[str, int],
                all_ids: set[str], problems: list[str]) -> str:
    if BRACE_RUN_RE.search(text):
        problems.append(f"clause {clause_id!r}: malformed placeholder syntax "
                        f"(3+ consecutive braces)")

    def replace(match):
        is_ref, name = placeholder_parts(match)
        if is_ref:
            if name not in numbers:
                reason = ("excluded under these answers" if name in all_ids
                          else "not in the template")
                problems.append(f"clause {clause_id!r} references clause {name!r}, which is {reason}")
                return match.group(0)
            return f"clause {numbers[name]}"
        if name not in answers:
            problems.append(f"clause {clause_id!r} uses unknown variable {name!r}")
            return match.group(0)
        return _format_value(answers[name])

    result = PLACEHOLDER_RE.sub(replace, text)
    # Check the *template text* for unconsumed brace syntax (not the result —
    # a substituted answer value containing braces is the author's business).
    leftover = PLACEHOLDER_RE.sub("", text)
    if "{{" in leftover or "}}" in leftover:
        problems.append(f"clause {clause_id!r}: unresolved placeholder syntax remains "
                        f"after substitution")
    return result


def render_markdown(template: Template, answers: Answers, title: str | None = None) -> str:
    planned = plan(template, answers)
    lines = ["# " + (title or template.doc_type), ""]
    for p in planned:
        lines.append(f"**{p.number}. {p.clause.heading}**")
        lines.append("")
        lines.append(p.rendered_text)
        lines.append("")
    return "\n".join(lines)


def render_docx(template: Template, answers: Answers, path: str,
                title: str | None = None) -> None:
    from .richtext import blocks_from_text, docx_document
    planned = plan(template, answers)
    doc = docx_document(
        title or template.doc_type,
        [(f"{p.number}. {p.clause.heading}", blocks_from_text(p.rendered_text))
         for p in planned])
    doc.save(path)
