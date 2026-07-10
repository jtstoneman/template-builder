"""Deterministic validation gates.

Two layers:

1. Static checks over the clause graph — reference integrity, condition
   syntax, variable usage, duplicate ids, missing defaults.
2. A configuration sweep ("branch render tests") — actually render the
   template under every combination of boolean/choice answers (exhaustive
   when the space is small, deterministic sampling when it is not) and check
   that every configuration renders and that every defined term used in a
   configuration has its defining clause included.

Be honest about coverage: structural invariants are proven over all swept
configurations; the *semantics* of any single rendered document are only
sampled — and conditions on number/string variables are only exercised at
one fixed sample value, which the coverage record calls out explicitly.
The coverage record returned here is stored in the approval certificate so
"approved" always means "approved generator + this coverage".
"""

import hashlib
import itertools
import json
import re
from dataclasses import dataclass
from typing import Any

from . import conditions
from .model import SCHEMA_APPROVAL_ID, Template
from .render import BRACE_RUN_RE, PLACEHOLDER_RE, PlannedClause, RenderError, placeholder_parts, plan

MAX_EXHAUSTIVE = 256   # sweep every configuration up to this many
SAMPLE_SIZE = 200      # otherwise sample this many, deterministically

VARIABLE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
CLAUSE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
DRAFT_BRACKET_RE = re.compile(r"\[[^\[\]\n]{0,80}\]")
ANY_PLACEHOLDER_RE = re.compile(r"\{\{[^{}]*\}\}")

# `tb edit` bakes sweep example-labels into finding text; strip them when
# comparing before/after findings so a label shift doesn't read as a new error.
# Greedy to the final ')' so labels containing ')' (a choice like "New York
# (NY)") still strip cleanly.
_EXAMPLE_LABEL_RE = re.compile(r" \(e\.g\. with .*\)$")


@dataclass(slots=True)
class Finding:
    level: str    # "error" | "warning"
    where: str    # clause id, variable name, or "template"
    message: str
    # How many swept configurations exhibit this problem. Not part of str()
    # or finding_key (identity must stay stable), but gated_edit compares it:
    # an edit that breaks MORE configurations of an already-failing clause
    # must not hide behind the pre-existing finding.
    count: int = 1

    def __str__(self) -> str:
        return f"[{self.level}] {self.where}: {self.message}"


def finding_key(finding: Finding) -> str:
    """Stable identity of a finding across runs (example labels stripped)."""
    return _EXAMPLE_LABEL_RE.sub("", str(finding))


def validate(template: Template) -> tuple[list[Finding], dict[str, Any]]:
    """Run all gates. Returns (findings, coverage)."""
    findings: list[Finding] = []
    space = _configurations(template)
    condition_variables = _static_checks(template, findings, exhaustive=space.exhaustive)
    coverage = _sweep(template, findings, space, condition_variables)
    return findings, coverage


def errors(findings: list[Finding]) -> list[Finding]:
    return [f for f in findings if f.level == "error"]


# ---------------------------------------------------------------------------
# Static checks
# ---------------------------------------------------------------------------

def _static_checks(template: Template, findings: list[Finding], exhaustive: bool) -> set[str]:
    """Graph-level gates. Returns the set of variables used in conditions."""
    checker = _StaticChecker(template, findings, exhaustive)
    checker.run()
    return checker.condition_variables


class _StaticChecker:
    """One method per concern; shared state (known names, usage sets) held once."""

    def __init__(self, template: Template, findings: list[Finding], exhaustive: bool):
        self.template = template
        self.findings = findings
        self.exhaustive = exhaustive
        self.known_variables = {v.name for v in template.variables}
        self.known_clauses = {c.id for c in template.clauses}
        self.used_variables: set[str] = set()
        self.condition_variables: set[str] = set()

    def err(self, where: str, message: str) -> None:
        self.findings.append(Finding("error", where, message))

    def warn(self, where: str, message: str) -> None:
        self.findings.append(Finding("warning", where, message))

    def run(self) -> None:
        self._check_template_shape()
        for variable in self.template.variables:
            self._check_variable(variable)
        for clause in self.template.clauses:
            self._check_clause_identity(clause)
            self._check_defines(clause)
            self._check_conditions(clause)
            self._check_variant_order(clause)
            for variant in clause.variants:
                self._check_variant_text(clause, variant)
        for name in sorted(self.known_variables - self.used_variables):
            self.warn(name, "variable is never used in any clause text or condition")
        self._check_dependencies()

    def _check_template_shape(self) -> None:
        if not self.template.clauses:
            self.err("template", "template has no clauses — nothing to render")
        for name in _duplicates([v.name for v in self.template.variables]):
            self.err("template", f"duplicate variable name {name!r}")
        for cid in _duplicates([c.id for c in self.template.clauses]):
            self.err("template", f"duplicate clause id {cid!r}")

    def _check_variable(self, variable) -> None:
        if not VARIABLE_NAME_RE.match(variable.name):
            self.err(variable.name,
                     "variable name must look like snake_case (letters, digits, underscores)")
        if variable.type == "choice":
            if len(variable.choices) < 2:
                self.err(variable.name, "a choice variable needs at least two choices")
            for choice in _duplicates(variable.choices):
                self.err(variable.name, f"duplicate choice {choice!r}")

    def _check_clause_identity(self, clause) -> None:
        if not CLAUSE_ID_RE.match(clause.id):
            self.err(clause.id, "clause id must be kebab-case (letters, digits, hyphens)")
        if clause.id == SCHEMA_APPROVAL_ID:
            self.err(clause.id, f"clause id {SCHEMA_APPROVAL_ID!r} is reserved for the "
                                f"questionnaire's own approval entry")
        # Headings are emitted verbatim into the signed document, so they get
        # the same scrutiny as clause text: no placeholders, no stray braces.
        if not clause.heading.strip():
            self.err(clause.id, "clause heading is empty")
        elif "\n" in clause.heading:
            self.err(clause.id, "clause heading contains a line break")
        elif "{" in clause.heading or "}" in clause.heading:
            self.err(clause.id, f"clause heading contains braces — placeholders are "
                                f"not substituted in headings and would render "
                                f"literally: {clause.heading!r}")

    def _check_defines(self, clause) -> None:
        for term in clause.defines:
            if not term.strip():
                self.err(clause.id, "defines contains an empty or whitespace-only term")
        for term in _duplicates(clause.defines):
            self.warn(clause.id, f"duplicate defined term {term!r}")

    def _check_conditions(self, clause) -> None:
        labelled = [(clause.include_when, "include_when")] + [
            (v.when, f"variant {v.id!r} condition") for v in clause.variants]
        for expr, label in labelled:
            if expr is None:
                continue
            try:
                names = conditions.variables_in(expr)
            except conditions.ConditionError as e:
                self.err(clause.id, f"{label}: {e}")
                continue
            self.used_variables |= names
            self.condition_variables |= names
            for name in sorted(names - self.known_variables):
                self.err(clause.id, f"{label} uses unknown variable {name!r}")

    def _check_variant_order(self, clause) -> None:
        for name in _duplicates([v.id for v in clause.variants]):
            self.err(clause.id, f"duplicate variant id {name!r}")
        # Variant order semantics: first match wins, when=None always matches.
        default_seen = False
        for variant in clause.variants:
            if default_seen:
                self.warn(clause.id, f"variant {variant.id!r} is unreachable — it comes after "
                                     f"the default variant, which always matches")
            if variant.when is None:
                default_seen = True
        if not default_seen:
            if self.exhaustive:
                tail = "the sweep below will say which"
            else:
                tail = ("the sweep below is SAMPLED and may not hit the failing "
                        "combinations — add a default variant to be safe")
            self.warn(clause.id, f"no default variant (\"when\": null) — some answer combinations "
                                 f"may leave this clause with no text ({tail})")

    def _check_variant_text(self, clause, variant) -> None:
        matched_spans = []
        for match in PLACEHOLDER_RE.finditer(variant.text):
            matched_spans.append(match.span())
            is_ref, name = placeholder_parts(match)
            if is_ref:
                if name not in self.known_clauses:
                    self.err(clause.id, f"variant {variant.id!r} has an orphan cross-reference "
                                        f"to nonexistent clause {name!r}")
            else:
                self.used_variables.add(name)
                if name not in self.known_variables:
                    self.err(clause.id, f"variant {variant.id!r} uses unknown variable {name!r}")
                elif self.template.variable(name).type == "boolean":
                    self.warn(clause.id, f"boolean variable {name!r} is substituted into text — "
                                         f"did you mean to gate a variant with it instead?")
        # Catch-all: {{...}}-looking text the placeholder grammar can't parse
        # (bad characters, stray spaces around a hyphenated variable, ...).
        for match in ANY_PLACEHOLDER_RE.finditer(variant.text):
            if match.span() not in matched_spans:
                self.err(clause.id, f"variant {variant.id!r} contains malformed placeholder "
                                    f"{match.group(0)!r}")
        if BRACE_RUN_RE.search(variant.text):
            self.err(clause.id, f"variant {variant.id!r} contains a run of 3+ braces — "
                                f"malformed placeholder syntax")
        for bracket in DRAFT_BRACKET_RE.findall(variant.text):
            if not PLACEHOLDER_RE.search(bracket):
                self.warn(clause.id, f"variant {variant.id!r} contains {bracket!r} — "
                                     f"possible unresolved drafting bracket")

    def _check_dependencies(self) -> None:
        seen_edges = set()
        for dep in self.template.dependencies:
            where = f"{dep.from_clause} -> {dep.to_clause}"
            for endpoint in (dep.from_clause, dep.to_clause):
                if endpoint not in self.known_clauses:
                    self.err(where, f"dependency ({dep.kind}) names nonexistent clause {endpoint!r}")
            if dep.from_clause == dep.to_clause:
                self.err(where, "a clause cannot depend on itself")
            key = (dep.from_clause, dep.to_clause, dep.kind)
            if key in seen_edges:
                self.err(where, f"duplicate {dep.kind} dependency")
            seen_edges.add(key)
            if not dep.note.strip():
                self.warn(where, f"{dep.kind} dependency has no note — record WHY the edge "
                                 f"exists; the note is what future editors see")


def _duplicates(items):
    seen, dupes = set(), []
    for item in items:
        if item in seen and item not in dupes:
            dupes.append(item)
        seen.add(item)
    return dupes


# ---------------------------------------------------------------------------
# Configuration sweep (branch render tests)
# ---------------------------------------------------------------------------

@dataclass(slots=True)
class _Space:
    names: list[str]
    configs: list[dict]
    total: int
    exhaustive: bool


def _sample_answers(template: Template) -> dict:
    """Fixed sample values for variables that don't branch (strings, numbers)."""
    fixed = {}
    for variable in template.variables:
        if variable.type == "string":
            fixed[variable.name] = f"Example {variable.name}"
        elif variable.type == "number":
            fixed[variable.name] = 10
    return fixed


def _configurations(template: Template) -> _Space:
    # Empty choice lists are a static error (reported separately); excluding
    # them here keeps the sweep well-defined instead of crashing or looping.
    toggles = [v for v in template.variables
               if v.type == "boolean" or (v.type == "choice" and v.choices)]
    # dict.fromkeys dedupes while preserving order — duplicated choice values
    # must not inflate the space (that once made the sampler loop forever).
    domains = [[True, False] if v.type == "boolean" else list(dict.fromkeys(v.choices))
               for v in toggles]
    total = 1
    for domain in domains:
        total *= len(domain)
    names = [v.name for v in toggles]

    if total <= MAX_EXHAUSTIVE:
        configs = [dict(zip(names, combo)) for combo in itertools.product(*domains)]
        return _Space(names=names, configs=configs, total=total, exhaustive=True)

    # Sampled regime. Each variable's pick for sample k depends only on
    # (variable name, k) — never on which other variables exist — so adding
    # or removing one variable leaves every other column of the sample
    # unchanged, and gated_edit's before/after comparison stays meaningful.
    target = min(SAMPLE_SIZE, total)
    seen = set()
    configs = []
    k = 0
    while len(configs) < target and k < 100 * target:
        combo = tuple(_stable_pick(name, domain, k)
                      for name, domain in zip(names, domains))
        k += 1
        if combo in seen:
            continue
        seen.add(combo)
        configs.append(dict(zip(names, combo)))
    return _Space(names=names, configs=configs, total=total, exhaustive=False)


def _stable_pick(name: str, domain: list, k: int):
    digest = hashlib.sha256(f"{name}|{k}".encode()).digest()
    return domain[int.from_bytes(digest[:4]) % len(domain)]


def term_pattern(term: str) -> re.Pattern:
    # Word-boundary lookarounds so "Term" never matches inside "Termination",
    # and re.escape so multi-word terms match literally.
    return re.compile(r"(?<![A-Za-z0-9_])" + re.escape(term) + r"(?![A-Za-z0-9_])")


def _sweep(template: Template, findings: list[Finding], space: _Space,
           condition_variables: set[str]) -> dict[str, Any]:
    fixed = _sample_answers(template)

    definers_by_term: dict[str, set[str]] = {}
    for clause in template.clauses:
        for term in clause.defines:
            if not term.strip():
                continue  # reported as a static error; don't poison the closure check
            definers_by_term.setdefault(term, set()).add(clause.id)

    # Which terms each variant's text mentions is configuration-independent:
    # compute it once, so the per-config closure check is pure set algebra
    # (the regex work used to run again for every swept configuration).
    mentions: dict[tuple[str, str], set[str]] = {}
    patterns = {term: term_pattern(term) for term in definers_by_term}
    for clause in template.clauses:
        for variant in clause.variants:
            # placeholders stripped so a term never "matches" inside a
            # placeholder name like {{term_years}}
            haystack = PLACEHOLDER_RE.sub(" ", variant.text)
            mentions[(clause.id, variant.id)] = {
                term for term, pattern in patterns.items()
                if pattern.search(haystack)}

    first_label: dict[str, str] = {}   # problem -> example config that showed it
    config_count: dict[str, int] = {}  # problem -> how many configs exhibit it
    digests = []
    for config in space.configs:
        answers = dict(fixed)
        answers.update(config)
        label = _describe(config)
        digests.append(hashlib.sha256(
            json.dumps(config, sort_keys=True, default=str).encode()).hexdigest()[:12])

        hit: set[str] = set()
        try:
            planned = plan(template, answers)  # the exact code path rendering uses
        except RenderError as e:
            hit.update(e.problems)
        except conditions.ConditionError as e:
            hit.add(str(e))
        else:
            _check_term_closure(planned, definers_by_term, mentions, hit.add)
            _check_dependency_closure(template, {p.clause.id for p in planned},
                                      hit.add)
        for problem in hit:
            first_label.setdefault(problem, label)
            config_count[problem] = config_count.get(problem, 0) + 1

    for problem, label in first_label.items():
        findings.append(Finding("error", "sweep", f"{problem} (e.g. with {label})",
                                count=config_count[problem]))

    # Honesty about coverage: conditions over number/string variables are only
    # exercised at one fixed sample value — say so, in findings AND coverage.
    unswept = sorted(
        name for name in condition_variables
        if any(v.name == name and v.type in ("string", "number") for v in template.variables)
    )
    if unswept:
        findings.append(Finding(
            "warning", "sweep",
            f"conditions reference {', '.join(repr(n) for n in unswept)} — number/string "
            f"variables are swept at a single sample value, so those branches are NOT "
            f"exhaustively tested"))

    return {
        "toggle_variables": space.names,
        "configurations_total": space.total,
        "configurations_tested": len(space.configs),
        "exhaustive": space.exhaustive,
        "unswept_condition_variables": unswept,
        "config_digests": digests,
    }


def _check_term_closure(planned: list[PlannedClause],
                        definers_by_term: dict[str, set[str]],
                        mentions: dict[tuple[str, str], set[str]],
                        record) -> None:
    """Every defined term used in this configuration must be defined in it.

    Works from render.plan's own output, so inclusion/variant selection can
    never drift from rendering. A declared definer only counts if the variant
    this configuration actually SELECTS states the term — declaring
    `defines: [X]` while the chosen variant's text lacks X is a broken
    definition, not a satisfied one.
    """
    selected = {p.clause.id: p.variant.id for p in planned}
    for p in planned:
        for term in mentions[(p.clause.id, p.variant.id)]:
            definers = definers_by_term[term]
            if p.clause.id in definers:
                continue
            if not any(definer in selected
                       and term in mentions[(definer, selected[definer])]
                       for definer in definers):
                record(f"clause {p.clause.id!r} uses defined term {term!r} but no "
                       f"included clause's selected text defines it")


def _check_dependency_closure(template: Template, included_ids: set[str],
                              record) -> None:
    """No configuration may include a clause without the clauses it depends on.

    An indemnity drafted on the assumption that an aggregate liability cap
    governs it must never render into a document that omits the cap. For
    subject-to / relies-on the check is directional; a trade-off is a
    negotiated package, so rendering either side alone is an error too.
    """
    for dep in template.dependencies:
        if dep.from_clause in included_ids and dep.to_clause not in included_ids:
            record(f"clause {dep.from_clause!r} is {dep.kind} {dep.to_clause!r}, "
                   f"which is excluded ({dep.note})")
        elif (dep.kind == "trade-off"
              and dep.to_clause in included_ids and dep.from_clause not in included_ids):
            record(f"clause {dep.to_clause!r} is one side of a trade-off with "
                   f"{dep.from_clause!r}, which is excluded ({dep.note})")


def _describe(config: dict) -> str:
    if not config:
        return "the only configuration"
    return ", ".join(f"{k}={v}" for k, v in sorted(config.items()))
