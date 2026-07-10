"""Sign-off: approvals attach to content hashes, not to a document.

`approve()` records, per clause, the hash of what the reviewer saw. A later
edit changes that clause's hash, so `status()` reports it as *stale* —
approval is invalidated for exactly the touched clauses, nothing else.

The certificate is honest about coverage: it records how many configurations
the validation sweep rendered and whether that was exhaustive. "Approved
once" means "approved generator + this coverage certificate"; the residual
risk lives in untested combinations, and the certificate says how big that
space is.
"""

import datetime
from enum import StrEnum
from typing import Any

from .model import (
    SCHEMA_APPROVAL_ID,
    Approval,
    Template,
    clause_hash,
    schema_hash,
    template_hash,
)


class ApprovalStatus(StrEnum):
    APPROVED = "approved"
    STALE = "stale"          # approved once, but the content changed since
    UNAPPROVED = "unapproved"


APPROVED = ApprovalStatus.APPROVED
STALE = ApprovalStatus.STALE
UNAPPROVED = ApprovalStatus.UNAPPROVED


def _compare(recorded: str | None, current: str) -> ApprovalStatus:
    if recorded is None:
        return ApprovalStatus.UNAPPROVED
    return ApprovalStatus.APPROVED if recorded == current else ApprovalStatus.STALE


def status(template: Template) -> dict[str, ApprovalStatus]:
    """Per-clause approval status, plus the variable schema's, keyed by id."""
    approved_hashes = {a.clause_id: a.hash for a in template.approvals}
    result = {
        clause.id: _compare(approved_hashes.get(clause.id), clause_hash(clause))
        for clause in template.clauses
    }
    result[SCHEMA_APPROVAL_ID] = _compare(approved_hashes.get(SCHEMA_APPROVAL_ID),
                                          schema_hash(template))
    return result


def approve(template: Template, by: str, coverage: dict[str, Any],
            date: str | None = None) -> list[str]:
    """Approve the current content of every clause and the variable schema.

    Returns the ids whose approval was created or refreshed. Items already
    approved at their current hash keep their ORIGINAL approver and date —
    re-running approve must not rewrite the audit trail. The caller is
    expected to have run validation first and refused on errors.
    """
    date = date or datetime.date.today().isoformat()
    before = status(template)
    changed = [cid for cid, s in before.items() if s is not ApprovalStatus.APPROVED]
    existing = {a.clause_id: a for a in template.approvals}

    def entry(item_id: str, current_hash: str) -> Approval:
        kept = existing.get(item_id)
        if kept is not None and kept.hash == current_hash:
            return kept  # unchanged content: keep who approved it, and when
        return Approval(clause_id=item_id, hash=current_hash, by=by, date=date)

    approvals = [entry(c.id, clause_hash(c)) for c in template.clauses]
    approvals.append(entry(SCHEMA_APPROVAL_ID, schema_hash(template)))
    template.approvals = approvals
    template.certificate = {
        "by": by,
        "date": date,
        "template_hash": template_hash(template),
        "configurations_total": coverage.get("configurations_total"),
        "configurations_tested": coverage.get("configurations_tested"),
        "exhaustive": coverage.get("exhaustive"),
        "unswept_condition_variables": coverage.get("unswept_condition_variables", []),
    }
    return changed


def certificate_age_days(template: Template, today: str | None = None) -> int | None:
    """Days since sign-off, or None if never approved. Templates rot — statutes
    move under them — so an old certificate is itself a finding."""
    if not template.certificate or not template.certificate.get("date"):
        return None
    signed = datetime.date.fromisoformat(template.certificate["date"])
    reference = datetime.date.fromisoformat(today) if today else datetime.date.today()
    return (reference - signed).days


REVIEW_AFTER_DAYS = 365


def structure_current(template: Template) -> bool | None:
    """Does the approval certificate cover the template's current structure?

    Per-clause hashes can't see clause REORDERING or REMOVAL (every surviving
    clause still matches its own hash, but the numbering — and so the rendered
    document — changed). The certificate's template_hash can. Returns None
    when there is no certificate yet.
    """
    if not template.certificate:
        return None
    return template.certificate.get("template_hash") == template_hash(template)
