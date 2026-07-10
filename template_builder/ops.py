"""The one write gate: every mutation of a template on disk goes through here.

CLI edits, web-UI edits and assistant-applied negotiation counters all share
the same semantics because they share this function:

    load -> snapshot pre-existing errors -> apply the operation ->
    re-validate -> save only if no NEW errors (or force) -> journal

Pre-existing errors are compared by ``finding_key`` (sweep example-labels
stripped) so a label shift in an old error never blocks an unrelated edit —
but by COUNT per key, so an edit that breaks additional configurations of an
already-failing clause still counts as introducing new errors.
Refused edits touch neither the template file nor the journal.
"""

from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass

from . import journal, model, validate
from .edit import EditResult
from .fsio import locked
from .model import Template


@dataclass(slots=True)
class GatedOutcome:
    saved: bool
    result: EditResult
    new_errors: list[str]     # errors this edit would introduce (empty when saved cleanly)
    template: Template        # post-operation template (unsaved when refused)


def gated_edit(
    template_path: str,
    operation: Callable[[Template], EditResult],
    *,
    force: bool = False,
    journal_meta: dict | None = None,
) -> GatedOutcome:
    """Apply one constrained edit under the write-time validation gate.

    ``journal_meta`` (actor / op / why / matter / counterparty / disposition)
    makes the saved edit part of the learning record; pass None to skip
    journaling (e.g. for callers that journal in their own way).
    """
    with locked(template_path):
        template = model.load(template_path)
        errors_before: Counter[str] = Counter()
        for finding in validate.errors(validate.validate(template)[0]):
            errors_before[validate.finding_key(finding)] += finding.count
        result = operation(template)
        findings, _ = validate.validate(template)
        seen: Counter[str] = Counter()
        new_errors = []
        for finding in validate.errors(findings):
            key = validate.finding_key(finding)
            seen[key] += finding.count
            if seen[key] > errors_before[key]:
                new_errors.append(str(finding))

        saved = force or not new_errors
        if saved:
            model.save(template, template_path)
            if journal_meta is not None:
                journal.append(
                    template_path,
                    kind="edit",
                    clause_id=result.touched[0] if result.touched else None,
                    detail=result.messages[0] if result.messages else None,
                    **journal_meta,
                )
    return GatedOutcome(saved=saved, result=result, new_errors=new_errors,
                        template=template)
