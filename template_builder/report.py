"""The build report: the human-readable record of what a build did and found.

The report is the first deliverable of every build — a lawyer reads the
diagnosis and the dependency map here before touching anything else.

Everything interpolated below that originated in an LLM (which itself read
untrusted uploaded contracts) is flattened to a single line first: a newline
smuggled through a heading or note must never be able to forge a report
section like a fake "Validation: All gates passed."
"""

from .model import Template
from .validate import Finding


def _flat(value: object) -> str:
    return " ".join(str(value).split())


def build_report(template: Template, notes: list[str],
                  findings: list[Finding], coverage: dict) -> str:
    lines = [f"# Build report — {_flat(template.doc_type)}", ""]
    lines.append(f"Built from {len(template.sources)} contracts:")
    lines.append("")
    for source in template.sources:
        context = template.source_contexts.get(source)
        lines.append(f"- {_flat(source)}" + (f" — *{_flat(context)}*" if context else ""))
    lines.append("")

    lines.append("## Canonical outline")
    lines.append("")
    for clause in template.clauses:
        gate = f" (only when `{_flat(clause.include_when)}`)" if clause.include_when else ""
        variants = "" if len(clause.variants) == 1 else f" — {len(clause.variants)} variants"
        lines.append(f"- `{_flat(clause.id)}` {_flat(clause.heading)}{gate}{variants}")
    lines.append("")

    lines.append("## Questionnaire")
    lines.append("")
    if template.variables:
        for v in template.variables:
            choices = f" — one of {_flat(v.choices)}" if v.type == "choice" else ""
            lines.append(f"- `{_flat(v.name)}` ({v.type}): {_flat(v.question)}{choices}")
    else:
        lines.append("(no variables — the template is fully static)")
    lines.append("")

    lines.append("## Dependency map (consequential-change wiring)")
    lines.append("")
    if template.dependencies:
        for dep in template.dependencies:
            lines.append(f"- `{_flat(dep.from_clause)}` **{dep.kind}** "
                         f"`{_flat(dep.to_clause)}` — {_flat(dep.note)}")
        lines.append("")
        lines.append("Editing a depended-on clause will flag its dependents for review with "
                     "these notes; no configuration can render a clause without the clauses "
                     "it is subject-to.")
    else:
        lines.append("(no semantic dependencies recorded)")
    lines.append("")

    lines.append("## Diagnosis (from decompilation)")
    lines.append("")
    if notes:
        lines.extend(f"- {_flat(note)}" for note in notes)
    else:
        lines.append("(nothing flagged)")
    lines.append("")

    lines.append("## Validation")
    lines.append("")
    if findings:
        lines.extend(f"- {_flat(finding)}" for finding in findings)
    else:
        lines.append("All gates passed.")
    lines.append("")
    lines.append(f"Configuration sweep: {coverage['configurations_tested']} of "
                 f"{coverage['configurations_total']} configurations rendered "
                 f"({'exhaustive' if coverage['exhaustive'] else 'sampled'}).")
    lines.append("")
    lines.append("## Next steps")
    lines.append("")
    lines.append("1. Fix any [error] findings (`tb edit ...`), re-check with `tb validate`.")
    lines.append("2. Have a lawyer review each clause and the questionnaire.")
    lines.append("3. Record sign-off: `tb approve <template> --by \"Name\"`.")
    lines.append("4. Generate documents: `tb questions`, fill answers, `tb render`.")
    return "\n".join(lines) + "\n"
