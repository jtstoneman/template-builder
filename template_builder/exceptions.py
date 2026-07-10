"""The exception register: every agreed departure from standard, in one place.

This is the compliance artifact firms reconstruct by hand today: per clause,
across all live and closed matters — what was conceded, to whom, by whose
authority, and why. It is a pure rollup of matter deviations; nothing here
is new state, so the register can never disagree with the matters.
"""

from collections import defaultdict
from pathlib import Path

from .matter import list_matters


def exception_rows(workspace: str | Path) -> list[dict]:
    rows = []
    for matter in list_matters(workspace):
        for deviation in matter.deviations:
            rows.append({
                "template": matter.template,
                "doc_type": matter.doc_type,
                "clause_id": deviation.clause_id,
                "matter": matter.id,
                "counterparty": matter.counterparty,
                "approved_by": deviation.approved_by,
                "rationale": deviation.rationale,
                "date": deviation.date,
                "round": deviation.round,
                # standard_text present but agreed_text empty = agreed deletion
                "deleted": bool(deviation.standard_text) and not deviation.agreed_text,
            })
    rows.sort(key=lambda r: (r["doc_type"], r["clause_id"], r["date"]))
    return rows


def render_register(workspace: str | Path) -> str:
    rows = exception_rows(workspace)
    lines = ["# Exception register", ""]
    if not rows:
        lines.append("No deviations from standard recorded across any matter.")
        return "\n".join(lines) + "\n"

    lines.append(f"{len(rows)} deviation(s) across "
                 f"{len({r['matter'] for r in rows})} matter(s). Every row is an agreed "
                 f"departure from the approved standard, with its authority and rationale.")
    by_clause: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        by_clause[(row["doc_type"], row["clause_id"])].append(row)

    for (doc_type, clause_id), clause_rows in by_clause.items():
        lines.append("")
        lines.append(f"## {doc_type} — `{clause_id}` ({len(clause_rows)} deviation(s))")
        lines.append("")
        for row in clause_rows:
            deleted = " — CLAUSE DELETED BY AGREEMENT" if row["deleted"] else ""
            lines.append(f"- **{row['matter']}** vs {row['counterparty']} "
                         f"(round {row['round']}, {row['date']}) — approved by "
                         f"**{row['approved_by']}**: {row['rationale']}{deleted}")
    lines.append("")
    lines.append("A clause that appears here repeatedly is telling you something: "
                 "either the standard is wrong (fix the template through the gated "
                 "edit pipeline) or the playbook needs a fallback rung (`tb skill update`).")
    return "\n".join(lines) + "\n"
