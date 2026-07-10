"""Decompilation, steps 2-4: merge atomised contracts into one template.

    Step 1 (decompile.atomise)  — each contract -> list of clauses
                                  [Isaacus extraction, or one LLM call per contract]
    Step 2 (build_outline)      — align clauses across contracts into a
                                  canonical outline                  [one LLM call]
    Step 3 (plan_variables)     — design ONE questionnaire schema up front
                                  from the outline, preambles and deal
                                  contexts                           [one LLM call]
    Step 4 (synthesise_clause)  — merge each canonical clause's source
                                  versions against that shared schema
                                  [one LLM call per clause, in parallel]

Planning the questionnaire before synthesis is what keeps variable naming
consistent across clauses (party_a_name everywhere, not three spellings) —
and it is what makes step 4 safely parallelisable, because clause calls no
longer depend on the variables invented by earlier clauses.

Then plain code consolidates the schema, assembles the Template, runs the
deterministic validation gates, and writes a build report. The LLM proposes;
the deterministic layer disposes.
"""

import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor

from pydantic import BaseModel

from . import validate as validate_mod
from .decompile import AtomisedContract, SourceClause, atomise
from .ingest import read_document
from .llm import complete
from .model import Clause, Dependency, DependencyKind, Template, Variable, VariableType, Variant
from .report import build_report
from .validate import Finding

type Progress = Callable[[str], object]


class SourceDocument(BaseModel):
    """One precedent contract, plus the deal context the uploader supplied.

    The context ("seller-friendly", "included W&I insurance", "distressed
    sale, tight timetable") is what lets the decompiler infer WHY drafting
    differs between sources — and turn that dimension into a questionnaire
    variable that conditions the affected clauses.
    """
    name: str
    text: str
    context: str | None = None


# --------------------------------------------------------------- outline ---

class OutlineMatch(BaseModel):
    file: str
    indices: list[int]


class OutlineEntry(BaseModel):
    id: str
    heading: str
    matches: list[OutlineMatch]


class Outline(BaseModel):
    clauses: list[OutlineEntry]
    notes: list[str]


OUTLINE_SYSTEM = """\
You align clauses across many versions of the same type of contract.

You receive, for several source contracts, a numbered list of their clauses
(heading + opening words). Produce ONE canonical outline for a template of
this document type.

Rules:
- One outline entry per distinct clause *function* (definitions,
  confidentiality obligations, term, governing law, ...). Different drafting
  of the same function belongs to the same entry.
- "id": a short stable kebab-case identifier, e.g. "governing-law".
- Order the outline by the dominant clause order across the sources
  (preamble first, signatures last).
- "matches": for every source file, the indices of its clauses that belong
  to this entry. Every source clause should be matched to exactly one entry;
  if one truly fits nowhere, leave it unmatched and explain in "notes".
"""


def build_outline(atomised: dict[str, list[SourceClause]],
                  contexts: dict[str, str] | None = None) -> Outline:
    contexts = contexts or {}
    lines: list[str] = []
    for filename, clauses in atomised.items():
        label = f" (deal context: {contexts[filename]})" if contexts.get(filename) else ""
        lines.append(f"=== {filename}{label} ===")
        for i, clause in enumerate(clauses):
            snippet = " ".join(clause.text.split())[:140]
            lines.append(f"  [{i}] {clause.heading}: {snippet}")
        lines.append("")
    prompt = "Source contracts and their clauses:\n\n" + "\n".join(lines)
    return complete(OUTLINE_SYSTEM, prompt, Outline)


# ------------------------------------------------------------- synthesis ---

class VariablePlan(BaseModel):
    variables: list["ProposedVariable"]
    notes: list[str]


PLAN_SYSTEM = """\
You design the questionnaire for a contract template that is about to be
assembled from many precedent contracts of the same type.

You receive the canonical clause outline (with how many source contracts each
clause appears in), the preamble of every source contract, and optional
"deal context" notes describing the deal each source came from.

Propose the complete variable schema:
1. DEAL FACTS every rendering needs: party names and descriptions, dates,
   durations, amounts, governing law — snake_case names, string/number/choice
   types, and a "question" a deal lawyer can answer without seeing the
   template. Prefer symmetric names (party_a_name / party_b_name).
2. STRUCTURAL TOGGLES: for each outline clause that appears in only some
   sources and is plausibly optional, a boolean named include_<something>.
3. CONTEXT DIMENSIONS: where the deal contexts differ along a dimension that
   plausibly changed the drafting (seller-friendly vs buyer-friendly, W&I
   insurance, cross-border), a boolean or choice variable capturing it.
4. Keep the schema MINIMAL: one shared variable beats near-duplicates; do
   not propose a variable no clause would plausibly use.

"notes": reasoning a reviewing lawyer should see — especially which context
dimension you inferred from which sources.
"""


def variable_plan_prompt(doc_type: str, outline: "Outline",
                         atomised: dict[str, list[SourceClause]],
                         contexts: dict[str, str],
                         playbook: str | None = None) -> str:
    total = len(atomised)
    parts = [f"Document type: {doc_type}", "", "Canonical outline:"]
    for entry in outline.clauses:
        files = sorted({m.file for m in entry.matches})
        parts.append(f"- {entry.id} ({entry.heading}) — appears in {len(files)}/{total}: "
                     f"{', '.join(files)}")
    if contexts:
        parts.append("")
        parts.append("Deal contexts:")
        for name, context in sorted(contexts.items()):
            parts.append(f"- {name}: {context}")
    parts.append("")
    parts.append("Source preambles (parties, dates, purpose usually live here):")
    for name, clauses in sorted(atomised.items()):
        if clauses:
            parts.append(f"--- {name} ---")
            parts.append(clauses[0].text[:800])
    if playbook:
        parts.append("")
        parts.append("Firm playbook (learned positions for this document type — let its "
                     "toggles and dimensions inform the schema):")
        parts.append(playbook[:4000])
    return "\n".join(parts)


def plan_variables(doc_type: str, outline: "Outline",
                   atomised: dict[str, list[SourceClause]],
                   contexts: dict[str, str],
                   playbook: str | None = None) -> VariablePlan:
    prompt = variable_plan_prompt(doc_type, outline, atomised, contexts, playbook)
    return complete(PLAN_SYSTEM, prompt, VariablePlan, max_tokens=8000)


class ProposedVariant(BaseModel):
    id: str
    when: str | None
    text: str
    provenance: list[str]


class ProposedVariable(BaseModel):
    name: str
    type: VariableType
    question: str
    choices: list[str]


VariablePlan.model_rebuild()  # resolve the forward reference to ProposedVariable


class SynthesisedClause(BaseModel):
    include_when: str | None
    defines: list[str]
    variants: list[ProposedVariant]
    new_variables: list[ProposedVariable]
    notes: list[str]


SYNTH_SYSTEM = """\
You merge several source versions of the same contract clause into one
canonical, parameterised template clause.

Output format:
- Clause text may contain {{variable_name}} placeholders and
  {{ref:clause-id}} cross-references. Nothing else is special.
- Separate paragraphs with a blank line. Keep (a), (b), (c) enumerations
  inline within a paragraph or as their own paragraphs.

Rules:
1. PARAMETERISE, don't multiply: if source versions differ only in party
   names, dates, amounts, durations or similar literals, produce ONE variant
   and replace the literal with a {{snake_case}} variable. Reuse the
   existing variables you are given whenever one fits; only add to
   "new_variables" when nothing existing fits.
2. VARIANTS are for substantively different drafting approaches (e.g. mutual
   vs one-way obligations). Gate each with a "when" condition on a boolean
   or choice variable. Exactly one variant must be the default:
   "when": null, listed LAST. If the sources agree, produce a single default
   variant.
3. CONDITIONS use a tiny language: bare boolean names (is_mutual),
   comparisons (governing_law == "New York", term_years >= 3), and/or/not.
   Nothing else.
4. CROSS-REFERENCES: never write "see Section 7". Write {{ref:<id>}} using
   an id from the canonical outline you are given. If a source references
   something with no counterpart in the outline, drop the reference and
   record it in "notes".
5. "include_when": null for a core clause. If this clause appeared in only
   some sources and is plausibly optional, gate it with a new boolean
   variable named include_<something>.
6. No square-bracket placeholders may remain in the text; every {{variable}}
   you use must exist in the given variables or in "new_variables".
7. Choose the best drafting among the sources as the base — clear, complete,
   protective of both parties — rather than the most common one.
8. "provenance": which source files each variant's language is based on.
9. "notes": conflicts between sources, judgement calls you made, and any
   drafting problems (a reviewing lawyer reads these).
10. CONTEXT-DRIVEN CONDITIONING: sources may carry a "deal context" note
    describing the deal they came from. When a drafting difference between
    sources CORRELATES with their contexts — a clause present only in deals
    with W&I insurance, stricter wording in the seller-friendly deals, a
    carve-out that appears only in cross-border deals — capture that context
    dimension as a questionnaire variable (a boolean like has_wi_insurance,
    or a choice like deal_stance) and use it as the "when"/"include_when"
    condition for the affected variant or clause. State the inferred
    correlation in "notes" ("present only in the two W&I deals") so a lawyer
    can confirm or reject the inference. Never invent a correlation the
    contexts don't support.
"""


def synthesis_prompt(
    entry: OutlineEntry,
    sources: list[tuple[str, SourceClause]],
    outline_ids: list[str],
    variables: list[Variable],
    contexts: dict[str, str],
    all_files: list[str],
    playbook: str | None = None,
) -> str:
    parts = [
        f"Canonical clause: id={entry.id!r}, heading={entry.heading!r}",
        "",
        "Valid {{ref:...}} targets (the full canonical outline):",
        "  " + ", ".join(outline_ids),
        "",
        "Existing variables (reuse these where possible):",
    ]
    if variables:
        for v in variables:
            choices = f" choices={v.choices}" if v.type == "choice" else ""
            parts.append(f"  - {v.name} ({v.type}){choices}: {v.question}")
    else:
        parts.append("  (none yet)")
    parts.append("")
    parts.append("Source versions of this clause:")
    for filename, clause in sources:
        label = f" (deal context: {contexts[filename]})" if contexts.get(filename) else ""
        parts.append(f"--- from {filename}{label} ---")
        parts.append(clause.text)
        parts.append("")
    files = sorted({filename for filename, _ in sources})
    parts.append(f"This clause appears in {len(files)} of the {len(all_files)} source "
                 f"contracts: {', '.join(files)}.")
    missing = sorted(set(all_files) - set(files))
    if missing:
        parts.append(f"It does NOT appear in: {', '.join(missing)}." + (
            " If the deal contexts explain its presence/absence, gate it accordingly (rule 10)."
            if contexts else ""))
    if playbook:
        parts.append("")
        parts.append("Firm playbook (learned positions — when choosing the base drafting "
                     "under rule 7, prefer the position the playbook records):")
        parts.append(playbook[:4000])
    return "\n".join(parts)


def synthesise_clause(
    entry: OutlineEntry,
    sources: list[tuple[str, SourceClause]],
    outline_ids: list[str],
    variables: list[Variable],
    contexts: dict[str, str] | None = None,
    all_files: list[str] | None = None,
    playbook: str | None = None,
) -> SynthesisedClause:
    """Merge the matched source versions of one canonical clause."""
    prompt = synthesis_prompt(entry, sources, outline_ids, variables, contexts or {},
                              all_files or sorted({f for f, _ in sources}), playbook)
    return complete(SYNTH_SYSTEM, prompt, SynthesisedClause)


# ----------------------------------------------------------- dependencies ---

class ProposedDependency(BaseModel):
    from_clause: str
    to_clause: str
    kind: DependencyKind
    note: str


class DependencyMap(BaseModel):
    dependencies: list[ProposedDependency]
    notes: list[str]


DEPENDENCY_SYSTEM = """\
You map the semantic dependencies between the clauses of a contract template —
the relationships that make changes CONSEQUENTIAL, so that a future editor who
touches one clause is warned about the others it disturbs.

Kinds (use no others):
- "subject-to": from_clause operates subject to to_clause; to_clause takes
  precedence. The canonical example: an indemnity is drafted at its level of
  exposure BECAUSE the aggregate limitation of liability caps it — the
  indemnity is subject-to the limitation clause.
- "relies-on": from_clause assumes to_clause's machinery or content to
  operate (a remedy that relies on the notice clause's mechanics; a payment
  obligation that relies on a completion-accounts clause).
- "trade-off": the two clauses were negotiated as a package — one side's
  generosity was accepted because of the other's protection; changing either
  disturbs the balance.

Rules:
1. Record ONLY edges a lawyer would want to be warned about when editing.
   Do not record trivialities: everything relies on the definitions clause;
   mere {{ref:...}} cross-references are already tracked mechanically.
2. Every edge needs a specific, self-contained "note" saying WHY — it is
   shown verbatim to future editors and audited by a reviewing lawyer.
   "clause A relates to clause B" is useless; "the uncapped IP indemnity was
   accepted because the aggregate cap in limitation-of-liability still
   governs it" is right.
3. Direction matters: from_clause is the clause whose drafting ASSUMES
   to_clause. For trade-off, direction is nominal (both sides are warned).
4. Prefer few, load-bearing edges over exhaustive wiring.
"notes": observations about the dependency structure worth a lawyer's
attention (e.g. an assumption you could not verify from the text).
"""


def dependency_prompt(clauses: list[Clause]) -> str:
    parts = ["Clauses of the template (id, heading, default text):", ""]
    for clause in clauses:
        default = next((v for v in clause.variants if v.when is None), clause.variants[0])
        gate = f" [only included when {clause.include_when}]" if clause.include_when else ""
        parts.append(f"=== {clause.id} — {clause.heading}{gate} ===")
        text = default.text
        parts.append(text[:1500] + ("…" if len(text) > 1500 else ""))
        parts.append("")
    return "\n".join(parts)


def map_dependencies(clauses: list[Clause]) -> DependencyMap:
    return complete(DEPENDENCY_SYSTEM, dependency_prompt(clauses), DependencyMap,
                    max_tokens=8000)


def _filter_dependencies(proposed: list[ProposedDependency], clauses: list[Clause],
                         notes: list[str]) -> list[Dependency]:
    """The LLM proposes; deterministic code disposes — drop anything malformed."""
    known = {c.id for c in clauses}
    kept: list[Dependency] = []
    seen: set[tuple[str, str, str]] = set()
    for p in proposed:
        key = (p.from_clause, p.to_clause, p.kind)
        if p.from_clause not in known or p.to_clause not in known:
            notes.append(f"dependency-map: dropped edge naming unknown clause "
                         f"({p.from_clause} -> {p.to_clause})")
        elif p.from_clause == p.to_clause or key in seen:
            notes.append(f"dependency-map: dropped degenerate/duplicate edge "
                         f"({p.from_clause} -> {p.to_clause})")
        elif not p.note.strip():
            notes.append(f"dependency-map: dropped note-less edge "
                         f"({p.from_clause} -> {p.to_clause}) — every edge must say why")
        else:
            seen.add(key)
            kept.append(Dependency(from_clause=p.from_clause, to_clause=p.to_clause,
                                   kind=p.kind, note=p.note.strip()))
    return kept


# ---------------------------------------------------------- orchestration ---

def documents_from_paths(paths: list[str],
                         contexts: dict[str, str] | None = None) -> list[SourceDocument]:
    """CLI convenience: read files into SourceDocuments (contexts keyed by basename)."""
    contexts = contexts or {}
    documents = []
    for path in paths:
        name = os.path.basename(path)
        documents.append(SourceDocument(name=name, text=read_document(path),
                                        context=contexts.get(name)))
    return documents


def build_template(documents: list[SourceDocument], doc_type: str,
                   progress: Progress | None = None,
                   playbook: str | None = None) -> tuple[Template, str, list[Finding]]:
    """The full build pipeline. Returns (Template, report_markdown, findings)."""
    say: Progress = progress or (lambda msg: None)
    notes: list[str] = []

    names = [d.name for d in documents]
    for name in {n for n in names if names.count(n) > 1}:
        raise ValueError(f"two input documents share the name {name!r}; rename one")
    contexts = {d.name: d.context for d in documents if d.context and d.context.strip()}
    say(f"read {len(documents)} contracts"
        + (f" ({len(contexts)} with deal context)" if contexts else ""))

    # Step 1: atomise each contract (parallel — independent LLM calls)
    atomised: dict[str, list[SourceClause]] = {}
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {d.name: pool.submit(atomise, d.text, d.name, contexts.get(d.name))
                   for d in documents}
        for name, future in futures.items():
            result: AtomisedContract = future.result()
            atomised[name] = result.clauses
            notes.extend(f"{name}: {note}" for note in result.notes)
            say(f"atomised {name}: {len(result.clauses)} clauses")

    # Step 2: align clauses across contracts into a canonical outline
    outline = build_outline(atomised, contexts)
    notes.extend(f"outline: {note}" for note in outline.notes)
    _dedupe_outline_ids(outline, notes)
    _note_unmatched_sources(outline, atomised, notes)
    _note_double_matched_sources(outline, notes)
    outline_ids = [entry.id for entry in outline.clauses]
    say(f"outline: {len(outline_ids)} canonical clauses")

    # Step 3: design the questionnaire once, up front
    plan = plan_variables(doc_type, outline, atomised, contexts, playbook)
    variables: list[Variable] = []
    _merge_variables(variables, plan.variables, "questionnaire-plan", notes)
    notes.extend(f"questionnaire-plan: {note}" for note in plan.notes)
    say(f"planned questionnaire: {len(variables)} variables")

    # Gather each canonical clause's matched source versions
    entries: list[tuple[OutlineEntry, list[tuple[str, SourceClause]]]] = []
    for entry in outline.clauses:
        sources: list[tuple[str, SourceClause]] = []
        for match in entry.matches:
            source_clauses = atomised.get(match.file, [])
            for index in match.indices:
                if 0 <= index < len(source_clauses):
                    sources.append((match.file, source_clauses[index]))
                else:
                    notes.append(f"outline: {entry.id} matched a nonexistent clause "
                                 f"index {index} in {match.file} — skipped")
        if sources:
            entries.append((entry, sources))
        else:
            notes.append(f"outline: {entry.id} matched no source clauses — skipped")

    # Step 4: synthesise clauses in parallel against the FROZEN planned schema
    # (that immutability is what makes parallelism safe — no call depends on
    # variables another clause invented). Deep-copied: _merge_variables below
    # mutates the live Variable objects while other workers still read the plan.
    planned = [v.model_copy(deep=True) for v in variables]
    all_files = sorted(names)
    clauses: list[Clause] = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [(entry, sources,
                    pool.submit(synthesise_clause, entry, sources, outline_ids,
                                planned, contexts, all_files, playbook))
                   for entry, sources in entries]
        for entry, sources, future in futures:
            try:
                result = future.result()
            except Exception as first_error:
                # one flaky call must not discard the whole build unretried
                say(f"synthesis of {entry.id} failed ({first_error}) — retrying once")
                try:
                    result = synthesise_clause(entry, sources, outline_ids,
                                               planned, contexts, all_files, playbook)
                except Exception as e:
                    raise RuntimeError(
                        f"clause {entry.id!r} failed to synthesise twice ({e}) — "
                        f"{len(clauses)} clause(s) had already succeeded; re-run "
                        f"the build") from e
            _merge_variables(variables, result.new_variables, entry.id, notes)
            clause = _build_clause(entry, result, notes)
            clauses.append(clause)
            say(f"synthesised {entry.id} "
                f"({len(clause.variants)} variant{'s' if len(clause.variants) != 1 else ''})")

    _prune_unused_variables(variables, clauses, notes)

    # Step 5: map the semantic dependency graph — which clauses assume which,
    # so future edits warn about their consequences
    dependencies: list[Dependency] = []
    if len(clauses) > 1:
        dep_map = map_dependencies(clauses)
        dependencies = _filter_dependencies(dep_map.dependencies, clauses, notes)
        notes.extend(f"dependency-map: {note}" for note in dep_map.notes)
        say(f"mapped {len(dependencies)} clause dependencies")

    template = Template(doc_type=doc_type, variables=variables, clauses=clauses,
                        dependencies=dependencies,
                        sources=all_files, source_contexts=contexts)

    # Deterministic gates — the LLM's output is proposed, not trusted.
    findings, coverage = validate_mod.validate(template)
    report = build_report(template, notes, findings, coverage)
    return template, report, findings


def _dedupe_outline_ids(outline: Outline, notes: list[str]) -> None:
    """The model is told to produce unique ids; enforce it anyway."""
    seen: set[str] = set()
    for entry in outline.clauses:
        if entry.id in seen:
            suffix = 2
            while f"{entry.id}-{suffix}" in seen:  # never rename ONTO a taken id
                suffix += 1
            renamed = f"{entry.id}-{suffix}"
            notes.append(f"outline: duplicate canonical id {entry.id!r} renamed to {renamed!r} — "
                         f"review whether the two entries should be one clause")
            entry.id = renamed
        seen.add(entry.id)


def _note_unmatched_sources(outline: Outline, atomised: dict[str, list[SourceClause]],
                            notes: list[str]) -> None:
    """A source clause the outline never matched would vanish silently from the
    template. Compute the gap deterministically instead of trusting the model
    to confess it."""
    matched: dict[str, set[int]] = {name: set() for name in atomised}
    for entry in outline.clauses:
        for match in entry.matches:
            if match.file in matched:
                matched[match.file].update(match.indices)
    for name, clauses in atomised.items():
        missing = sorted(set(range(len(clauses))) - matched[name])
        for index in missing:
            notes.append(f"{name}: clause [{index}] {clauses[index].heading!r} was NOT matched "
                         f"to any canonical clause — its language is absent from the template")


def _note_double_matched_sources(outline: Outline, notes: list[str]) -> None:
    """The mirror-image gap: one source clause matched into SEVERAL canonical
    entries duplicates that contract language across the template."""
    matched_into: dict[tuple[str, int], list[str]] = {}
    for entry in outline.clauses:
        for match in entry.matches:
            for index in match.indices:
                matched_into.setdefault((match.file, index), []).append(entry.id)
    for (name, index), entry_ids in sorted(matched_into.items()):
        if len(entry_ids) > 1:
            notes.append(f"{name}: clause [{index}] was matched into "
                         f"{len(entry_ids)} canonical clauses ({', '.join(entry_ids)}) "
                         f"— the same language may now appear twice in the template")


def _prune_unused_variables(variables: list[Variable], clauses: list[Clause],
                            notes: list[str]) -> None:
    """Drop planned variables no clause ended up using.

    The planner works from the outline alone, so it can over-propose; keeping
    an unused variable would put a pointless question in the questionnaire
    (and a warning in every validation run).
    """
    from . import conditions
    from .render import PLACEHOLDER_RE, placeholder_parts

    used: set[str] = set()
    for clause in clauses:
        expressions = [clause.include_when] + [v.when for v in clause.variants]
        for expr in expressions:
            if expr is not None:
                try:
                    used |= conditions.variables_in(expr)
                except conditions.ConditionError:
                    pass  # validation reports bad conditions; not our job here
        for variant in clause.variants:
            for match in PLACEHOLDER_RE.finditer(variant.text):
                is_ref, name = placeholder_parts(match)
                if not is_ref:
                    used.add(name)
    dropped = [v.name for v in variables if v.name not in used]
    if dropped:
        variables[:] = [v for v in variables if v.name in used]
        notes.append(f"questionnaire-plan: dropped {len(dropped)} planned variable(s) "
                     f"no clause used: {', '.join(dropped)}")


def _merge_variables(variables: list[Variable], proposed: list[ProposedVariable],
                     clause_id: str, notes: list[str]) -> None:
    existing = {v.name: v for v in variables}
    for new in proposed:
        if new.name in existing:
            current = existing[new.name]
            if current.type != new.type:
                notes.append(f"{clause_id}: proposed variable {new.name!r} as {new.type} but it "
                             f"already exists as {current.type} — kept the existing definition")
            elif current.type == "choice":
                # Union the choices so every clause's options stay expressible.
                for choice in new.choices:
                    if choice not in current.choices:
                        current.choices.append(choice)
            continue
        variable = Variable(name=new.name, type=new.type, question=new.question,
                            choices=list(new.choices))
        variables.append(variable)
        existing[new.name] = variable


def _build_clause(entry: OutlineEntry, result: SynthesisedClause,
                  notes: list[str]) -> Clause:
    variants = [Variant(id=p.id, text=p.text, when=p.when, provenance=list(p.provenance))
                for p in result.variants]
    if not variants:
        notes.append(f"{entry.id}: model produced no variants — inserted an empty default "
                     f"for a lawyer to fill in")
        variants = [Variant(id="default", text="", when=None)]
    if not any(v.when is None for v in variants):
        variants[-1].when = None
        notes.append(f"{entry.id}: model produced no default variant — made the last "
                     f"variant ({variants[-1].id!r}) the default")
    return Clause(
        id=entry.id,
        heading=entry.heading,
        include_when=result.include_when,
        defines=list(result.defines),
        variants=variants,
    )
