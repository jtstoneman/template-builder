"""Constrained edit operations on a template.

The agent (or a human) never regenerates the whole template — that would let
clauses a lawyer already approved drift silently. Instead, edits go through
this small API: each operation touches named nodes, reports its blast radius
(which other clauses cross-reference the touched one), and leaves hash-based
approval invalidation to do its job.

The CLI runs full validation after every edit and refuses to save a template
that gained errors — validation at write time, not output time.
"""
import re
from dataclasses import dataclass, field

from . import conditions
from .model import (
    DEPENDENCY_KINDS,
    VARIABLE_TYPES,
    Clause,
    Dependency,
    Template,
    Variable,
    Variant,
)

REF_RE = re.compile(r"\{\{\s*ref:([A-Za-z0-9_-]+)\s*\}\}")


class EditError(ValueError):
    pass


@dataclass(slots=True)
class EditResult:
    touched: list[str] = field(default_factory=list)   # clause ids whose content changed
    review: list[str] = field(default_factory=list)    # clauses that cross-reference a touched clause
    messages: list[str] = field(default_factory=list)


def referrers(template: Template, clause_id: str) -> list[str]:
    """Clauses whose text cross-references the given clause."""
    result = []
    for clause in template.clauses:
        if clause.id == clause_id:
            continue
        for variant in clause.variants:
            if clause_id in REF_RE.findall(variant.text):
                result.append(clause.id)
                break
    return result


def semantic_dependents(template: Template, clause_id: str) -> list[tuple[str, Dependency]]:
    """Clauses whose *content assumptions* are disturbed when clause_id changes.

    Directional: changing the clause a subject-to/relies-on edge points AT
    disturbs the clause that depends on it (change the liability cap ->
    review the indemnity). A trade-off disturbs its counterpart from either
    side — it is a negotiated package.
    """
    hits = []
    for dep in template.dependencies:
        if dep.to_clause == clause_id:
            hits.append((dep.from_clause, dep))
        elif dep.kind == "trade-off" and dep.from_clause == clause_id:
            hits.append((dep.to_clause, dep))
    return hits


def _blast_radius(template: Template, clause_id: str) -> tuple[list[str], list[str]]:
    """(clauses to review, human-readable reasons) after clause_id changed."""
    review = referrers(template, clause_id)
    reasons = []
    for dependent, dep in semantic_dependents(template, clause_id):
        if dependent not in review:
            review.append(dependent)
        reasons.append(f"review {dependent!r} — {dep.describe()}")
    return review, reasons


def _get_variant(clause, variant_id):
    for variant in clause.variants:
        if variant.id == variant_id:
            return variant
    raise EditError(f"clause {clause.id!r} has no variant {variant_id!r}")


def _check_condition(expr):
    if expr is not None:
        conditions.parse(expr)  # raises ConditionError on bad syntax


def replace_text(template, clause_id, variant_id, new_text) -> EditResult:
    clause = template.clause(clause_id)
    variant = _get_variant(clause, variant_id)
    variant.text = new_text
    review, reasons = _blast_radius(template, clause_id)
    return EditResult(
        touched=[clause_id],
        review=review,
        messages=[f"replaced text of {clause_id}/{variant_id}"] + reasons,
    )


def set_condition(template, clause_id, expr) -> EditResult:
    """Set (or with expr=None, clear) a clause's include_when condition."""
    _check_condition(expr)
    clause = template.clause(clause_id)
    clause.include_when = expr
    review, reasons = _blast_radius(template, clause_id)
    return EditResult(
        touched=[clause_id],
        review=review,
        messages=[f"{clause_id}: include_when = {expr!r} — clauses that reference it may now "
                  f"point at an excluded clause in some configurations; validation will check"]
                 + reasons,
    )


def add_variant(template, clause_id, variant_id, text, when) -> EditResult:
    _check_condition(when)
    clause = template.clause(clause_id)
    if any(v.id == variant_id for v in clause.variants):
        raise EditError(f"clause {clause_id!r} already has a variant {variant_id!r}")
    # Conditional variants go before the default so they are reachable.
    variant = Variant(id=variant_id, text=text, when=when)
    if when is not None:
        default_index = next((i for i, v in enumerate(clause.variants) if v.when is None),
                             len(clause.variants))
        clause.variants.insert(default_index, variant)
    else:
        if any(v.when is None for v in clause.variants):
            raise EditError(f"clause {clause_id!r} already has a default variant; "
                            f"give the new variant a 'when' condition")
        clause.variants.append(variant)
    review, reasons = _blast_radius(template, clause_id)
    return EditResult(touched=[clause_id], review=review,
                      messages=[f"added variant {variant_id!r} to {clause_id}"] + reasons)


def remove_variant(template, clause_id, variant_id) -> EditResult:
    clause = template.clause(clause_id)
    variant = _get_variant(clause, variant_id)
    if len(clause.variants) == 1:
        raise EditError(f"cannot remove the only variant of clause {clause_id!r}; "
                        f"remove the clause instead")
    clause.variants.remove(variant)
    review, reasons = _blast_radius(template, clause_id)
    return EditResult(touched=[clause_id], review=review,
                      messages=[f"removed variant {variant_id!r} from {clause_id}"] + reasons)


def add_clause(template, clause_id, heading, text, after=None, include_when=None) -> EditResult:
    _check_condition(include_when)
    if any(c.id == clause_id for c in template.clauses):
        raise EditError(f"a clause with id {clause_id!r} already exists")
    clause = Clause(id=clause_id, heading=heading, include_when=include_when,
                    variants=[Variant(id="default", text=text, when=None)])
    if after is None:
        template.clauses.append(clause)
    else:
        target = template.clause(after)  # raises KeyError if missing
        template.clauses.insert(template.clauses.index(target) + 1, clause)
    return EditResult(touched=[clause_id],
                      messages=[f"added clause {clause_id!r} (unapproved until sign-off)"])


def remove_clause(template, clause_id) -> EditResult:
    clause = template.clause(clause_id)  # raises KeyError if missing
    blocking = referrers(template, clause_id)
    if blocking:
        raise EditError(
            f"cannot remove {clause_id!r}: it is cross-referenced by {', '.join(blocking)}. "
            f"Edit those clauses first."
        )
    edges = [d for d in template.dependencies
             if clause_id in (d.from_clause, d.to_clause)]
    if edges:
        raise EditError(
            f"cannot remove {clause_id!r}: dependency edges record why other clauses "
            f"assume it exists — remove them first (remove-dependency):\n"
            + "\n".join(f"  - {d.describe()}" for d in edges)
        )
    # Removing the sole definer of a term other clauses use would strip the
    # term from validation's universe — the gap must be blocked here, because
    # the sweep can no longer see it afterwards.
    from .render import PLACEHOLDER_RE
    from .validate import term_pattern
    for term in clause.defines:
        others = [c for c in template.clauses if c.id != clause_id]
        if any(term in c.defines for c in others):
            continue  # another clause still defines it
        pattern = term_pattern(term)
        users = [c.id for c in others
                 if any(pattern.search(PLACEHOLDER_RE.sub(" ", v.text))
                        for v in c.variants)]
        if users:
            raise EditError(
                f"cannot remove {clause_id!r}: it is the only clause defining "
                f"{term!r}, which {', '.join(users)} still use{'s' if len(users) == 1 else ''} "
                f"— move the definition or edit those clauses first")
    template.clauses.remove(clause)
    template.approvals = [a for a in template.approvals if a.clause_id != clause_id]
    return EditResult(touched=[clause_id], messages=[f"removed clause {clause_id!r}"])


def add_variable(template, name, vtype, question, choices=None) -> EditResult:
    if vtype not in VARIABLE_TYPES:
        raise EditError(f"unknown variable type {vtype!r} (expected one of {VARIABLE_TYPES})")
    if any(v.name == name for v in template.variables):
        raise EditError(f"a variable named {name!r} already exists")
    if vtype == "choice" and not (choices and len(choices) >= 2):
        raise EditError("a choice variable needs at least two choices")
    if vtype != "choice" and choices:
        raise EditError(f"choices only make sense for a choice variable, not {vtype!r}")
    template.variables.append(Variable(name=name, type=vtype, question=question,
                                       choices=list(choices or [])))
    return EditResult(messages=[f"added variable {name!r} — the questionnaire changed and "
                                f"needs (re-)approval"])


def add_dependency(template, from_clause, to_clause, kind, note) -> EditResult:
    template.clause(from_clause)  # raise KeyError early on unknown endpoints
    template.clause(to_clause)
    if kind not in DEPENDENCY_KINDS:
        raise EditError(f"unknown dependency kind {kind!r} (expected one of {DEPENDENCY_KINDS})")
    if from_clause == to_clause:
        raise EditError("a clause cannot depend on itself")
    if not (note and note.strip()):
        raise EditError("a dependency needs a note explaining WHY — it is the audit "
                        "record shown to future editors")
    if any((d.from_clause, d.to_clause, d.kind) == (from_clause, to_clause, kind)
           for d in template.dependencies):
        raise EditError(f"that {kind} dependency already exists")
    dep = Dependency(from_clause=from_clause, to_clause=to_clause, kind=kind,
                     note=note.strip())
    template.dependencies.append(dep)
    return EditResult(messages=[f"added dependency: {dep.describe()}",
                                "the dependency map is part of the sign-off object — "
                                "the certificate now needs re-approval"])


def remove_dependency(template, from_clause, to_clause, kind=None) -> EditResult:
    matches = [d for d in template.dependencies
               if d.from_clause == from_clause and d.to_clause == to_clause
               and (kind is None or d.kind == kind)]
    if not matches:
        raise EditError(f"no dependency from {from_clause!r} to {to_clause!r}"
                        + (f" of kind {kind!r}" if kind else ""))
    for dep in matches:
        template.dependencies.remove(dep)
    return EditResult(messages=[f"removed dependency: {d.describe()}" for d in matches]
                      + ["the certificate now needs re-approval"])
