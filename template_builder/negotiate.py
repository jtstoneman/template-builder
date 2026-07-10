"""Negotiate against a counterparty markup, under an explicit autonomy gate.

The assistant reads the counterparty's asks, the learned playbook, and each
clause's *maturity* — how many journaled human decisions with rationale
exist for it. The gate is deterministic and per-clause:

    no playbook play for the clause          -> escalate to the lawyer
    maturity < threshold (default 10)        -> escalate to the lawyer
    no replay evidence, or replay agreement
    below 70% on ≥3 replayed decisions       -> escalate to the lawyer
    red line implicated (per the playbook)   -> escalate to the lawyer
    otherwise                                -> accept / counter / reject,
                                                citing playbook + precedents

Escalated clauses are the lawyer's to decide — and their decisions, once
journaled, are exactly what raises maturity. Full automation is not a
switch; it is every clause independently crossing the precedent threshold.

The trust boundary, stated honestly: play existence, maturity, replay
agreement and the delegation matrix are enforced in code; whether an ask
*touches a red line* is a semantic judgment the model self-reports
(red_line_implicated), which the gate can only act on, not verify. What
contains it: replay evidence is earned per clause against real human
decisions (including refusals), and nothing the gate passes is acted on
unless the delegation matrix assigns that tier to the assistant.

Proposals are applied (only with --apply) through the same validated edit
API as any human edit, and journaled as actor="assistant".
"""

import os
import re
from typing import Literal

from pydantic import BaseModel

from .journal import JournalEntry, maturity
from .llm import complete
from .model import Template
from .skill import (
    DEFAULT_DELEGATION,
    DEFAULT_MATURITY_THRESHOLD,
    parse_delegation,
    parse_threshold,
)

# Replay tightening: a mature clause whose replayed predictions disagree with
# the lawyers' actual decisions too often stays escalated. Evidence beats counts.
MIN_REPLAY_SAMPLES = 3
MIN_REPLAY_AGREEMENT = 0.7


class ClauseResponse(BaseModel):
    clause_id: str
    stance: Literal["accept", "counter", "reject", "escalate"]
    rationale: str                 # cites playbook plays and [j:N] precedents
    proposed_text: str | None      # for "counter": full replacement default-variant text
    red_line_implicated: bool
    decider: str = "lawyer"        # stamped by the gate from the delegation matrix


class NegotiationPlan(BaseModel):
    summary: str
    responses: list[ClauseResponse]


NEGOTIATE_SYSTEM = """\
You negotiate contract drafting for the firm, against a counterparty's
markup/asks, strictly within the firm's learned playbook.

You receive: the template's clauses (id + current default text), the firm's
playbook (per-clause positions, fallback ladders, red lines, with [j:N]
precedent citations), each clause's maturity count, and the counterparty's
markup or requests.

For every clause the markup touches, respond with one of:
- "accept"  — the ask is within the playbook's fallback ladder; say which rung.
- "counter" — propose replacement text (the FULL clause text, preserving
  {{variable}} placeholders and {{ref:...}} cross-references exactly as in
  the current text) that moves toward the counterparty only as far as the
  playbook allows. Explain the trade in "rationale".
- "reject"  — the ask crosses a red line or has no playbook support to give.
- "escalate" — you lack playbook coverage or precedent to act. When in
  doubt, escalate: a wrong concession is worse than a slow one.

Rules:
1. NEVER act beyond the playbook: no position it doesn't evidence, no
   concession below its lowest fallback. Set red_line_implicated=true
   whenever the ask touches a stated red line (those always escalate or
   reject — never concede).
2. "rationale" must cite the play and its [j:N] precedents — it will be
   journaled and audited.
3. Respect the dependency logic: if the markup weakens a clause other
   clauses are subject-to, say so in the rationale.
"""


def negotiation_prompt(template: Template, playbook: str, markup: str,
                       maturities: dict[str, int], threshold: int) -> str:
    parts = [f"Document type: {template.doc_type}", "", "Clauses (current default text):"]
    for clause in template.clauses:
        default = next((v for v in clause.variants if v.when is None), clause.variants[0])
        parts.append(f"=== {clause.id} — {clause.heading} "
                     f"(maturity {maturities.get(clause.id, 0)}/{threshold}) ===")
        parts.append(default.text[:1200])
        parts.append("")
    if template.dependencies:
        parts.append("Dependency map:")
        parts.extend(f"- {d.describe()}" for d in template.dependencies)
        parts.append("")
    parts.append("Firm playbook:")
    # never truncated: the gate holds the model to the playbook, so the model
    # must have seen every play and red line it is being held to
    parts.append(playbook)
    parts.append("")
    parts.append("Counterparty markup / asks:")
    parts.append(markup)
    return "\n".join(parts)


def resolve_threshold(playbook_md: str | None = None) -> int:
    """TB_MATURITY_THRESHOLD env override, else the playbook's own value."""
    from_env = os.environ.get("TB_MATURITY_THRESHOLD")
    if from_env is not None:
        return int(from_env)
    return parse_threshold(playbook_md) or DEFAULT_MATURITY_THRESHOLD


def play_ids(playbook: str) -> set[str]:
    """The clause ids the playbook actually has plays for (exact, not substring)."""
    return set(re.findall(r"^###\s+(\S+)\s*$", playbook, re.MULTILINE))


def gate(plan: NegotiationPlan, template: Template, playbook: str,
         entries: list[JournalEntry], threshold: int,
         *, delegation: dict[str, str] | None = None,
         replay_scores: dict[str, dict] | None = None) -> NegotiationPlan:
    """The deterministic autonomy gate.

    Enforced in code: the play must exist (exact clause id), maturity and
    replay agreement must be earned, unknown clause ids escalate, and every
    response is stamped with its *decider* from the delegation matrix —
    red-line asks belong to the red_line tier, gated escalations to the
    immature tier, gate-passing decisions to the mature tier. Callers act on
    a response only when its decider is the assistant itself.

    NOT enforceable here: whether an ask semantically touches a red line —
    that is the model's self-report (see the module docstring).
    """
    delegation = delegation or dict(DEFAULT_DELEGATION)
    replay_scores = replay_scores or {}
    known = {c.id for c in template.clauses}
    plays = play_ids(playbook)
    gated: list[ClauseResponse] = []
    for response in plan.responses:
        if response.clause_id not in known:
            # a hallucinated clause id is a model error a human should see,
            # never something to drop silently
            gated.append(response.model_copy(update={
                "stance": "escalate", "proposed_text": None,
                "decider": delegation["immature"],
                "rationale": f"{response.rationale} [GATED: the model referenced "
                             f"{response.clause_id!r}, which is not a clause of this "
                             f"template — review the markup manually]",
            }))
            continue
        m = maturity(entries, response.clause_id)
        score = replay_scores.get(response.clause_id)
        replayed = (score.get("total", 0), score.get("agree", 0)) if score else (0, 0)
        reason = None
        if response.stance != "escalate":
            if response.clause_id not in plays:
                reason = "no playbook play for this clause"
            elif m < threshold:
                reason = f"maturity {m}/{threshold} — insufficient journaled precedent"
            elif replayed[0] < MIN_REPLAY_SAMPLES:
                reason = (f"only {replayed[0]} replayed decision(s) (need "
                          f"{MIN_REPLAY_SAMPLES}) — run `tb skill replay` so the gate "
                          f"can verify the playbook reproduces the lawyers' decisions")
            elif replayed[1] / replayed[0] < MIN_REPLAY_AGREEMENT:
                reason = (f"replay agreement {replayed[1]}/{replayed[0]} is below "
                          f"{MIN_REPLAY_AGREEMENT:.0%} — the assistant does not yet "
                          f"reproduce the lawyers' actual decisions here")
            elif response.red_line_implicated and response.stance != "reject":
                reason = "red line implicated — a human must decide"
        if response.red_line_implicated:
            decider = delegation["red_line"]
        elif reason or response.stance == "escalate":
            decider = delegation["immature"]
        else:
            decider = delegation["mature"]
        if reason:
            response = response.model_copy(update={
                "stance": "escalate",
                "proposed_text": None,
                "rationale": f"{response.rationale} [GATED: {reason}; the lawyer's "
                             f"decision will be journaled and raise maturity]",
            })
        gated.append(response.model_copy(update={"decider": decider}))
    return NegotiationPlan(summary=plan.summary, responses=gated)


def negotiate(template: Template, markup: str, playbook: str,
              entries: list[JournalEntry], threshold: int,
              replay_scores: dict[str, dict] | None = None) -> NegotiationPlan:
    maturities = {c.id: maturity(entries, c.id) for c in template.clauses}
    prompt = negotiation_prompt(template, playbook, markup, maturities, threshold)
    plan = complete(NEGOTIATE_SYSTEM, prompt, NegotiationPlan)
    return gate(plan, template, playbook, entries, threshold,
                delegation=parse_delegation(playbook), replay_scores=replay_scores)


def render_report(plan: NegotiationPlan, template: Template,
                  entries: list[JournalEntry], threshold: int) -> str:
    lines = [f"# Negotiation proposals — {template.doc_type}", "", plan.summary, ""]
    escalations = [r for r in plan.responses if r.stance == "escalate"]
    lines.append(f"{len(plan.responses)} clauses addressed; {len(escalations)} escalated "
                 f"to the lawyer (threshold: {threshold} journaled decisions per clause).")
    lines.append("")
    for r in plan.responses:
        m = maturity(entries, r.clause_id)
        flag = " ⚠ RED LINE" if r.red_line_implicated else ""
        lines.append(f"## {r.clause_id} — {r.stance.upper()}{flag} "
                     f"(maturity {m}/{threshold}; requires: {r.decider})")
        lines.append("")
        lines.append(r.rationale)
        if r.proposed_text:
            lines.append("")
            lines.append("Proposed replacement text:")
            lines.append("")
            lines.extend(f"> {line}" for line in r.proposed_text.splitlines())
        lines.append("")
    return "\n".join(lines)
