"""Matters: one live deal, as a first-class object.

A matter pins everything about one negotiation in one auditable file at
<workspace>/matters/<id>.json:

    template @ hash  +  answers  +  negotiation rounds (clause-anchored asks,
    the negotiator's responses, escalations)  +  deviations from the standard.

The template hash is captured when the matter opens, and every round records
the hash it was diffed against — so it is always known exactly which
signed-off generator any comparison ran on.

Matter events are journaled to the TEMPLATE's journal with matter=<id>, so
deal decisions feed the same learning loop as template edits.

Concurrency: the functions that mutate a matter (open_matter, ingest_round,
resolve_escalation, close_matter, record_deviation via its callers) hold the
matter file's lock, so the web server and CLI can operate on the same matter
without losing updates. Helpers below them never take the lock themselves.
"""

import datetime
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import journal, model
from .fsio import atomic_write_text, locked
from .model import SAFE_NAME_RE, Template, template_hash

type AnswerValue = str | int | float | bool

OPEN_STATUSES = ("intake", "open")
CLOSED_STATUSES = ("agreed", "abandoned")


class Ask(BaseModel):
    """One clause-anchored delta from a counterparty's returned document."""
    clause_id: str          # "(unanchored)" when the diff could not place it
    kind: Literal["modify", "delete", "add"]
    our_text: str           # what the deterministic render said
    their_text: str         # what came back ("" for delete)


class Response(BaseModel):
    """One gated negotiator response, recorded on its round for audit.

    Every stance is kept — including gate-passing counters and rejects that
    a human still has to transmit — so the round is a complete record of
    what the assistant concluded, not just what it escalated.
    """
    clause_id: str
    stance: str             # accept / counter / reject / escalate
    rationale: str
    proposed_text: str | None = None
    decider: str            # who the delegation matrix says may decide


class Escalation(BaseModel):
    """A negotiation point the autonomy gate handed to a human."""
    clause_id: str
    round: int
    stance: str             # the gated stance, always "escalate" while pending
    analysis: str           # the assistant's full drafted rationale
    proposed_text: str | None
    requires: str           # who may decide, per the delegation matrix
    resolved: bool = False


class Round(BaseModel):
    number: int
    received: str           # ISO timestamp
    source: str             # filename of the counterparty document
    asks: list[Ask]
    unanchored: list[str] = Field(default_factory=list)  # text the diff couldn't place
    template_hash: str = ""          # the template version this diff ran against
    responses: list[Response] = Field(default_factory=list)
    plan_summary: str | None = None


class Deviation(BaseModel):
    """An agreed departure from the approved standard — the exception register's row."""
    clause_id: str
    round: int
    standard_text: str
    agreed_text: str        # "" means the clause was deleted by agreement
    approved_by: str
    rationale: str
    date: str


class Matter(BaseModel):
    id: str
    template: str           # template name in the workspace
    template_hash: str      # the generator this deal runs on, pinned at open
    doc_type: str
    counterparty: str
    answers: dict[str, AnswerValue]
    status: Literal["intake", "open", "agreed", "abandoned"] = "open"
    opened: str = ""
    updated: str = ""
    rounds: list[Round] = Field(default_factory=list)
    escalations: list[Escalation] = Field(default_factory=list)
    deviations: list[Deviation] = Field(default_factory=list)

    def pending_escalations(self) -> list[Escalation]:
        return [e for e in self.escalations if not e.resolved]


def _now() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def matters_dir(workspace: str | Path) -> Path:
    return Path(workspace) / "matters"


def matter_path(workspace: str | Path, matter_id: str) -> Path:
    if not SAFE_NAME_RE.match(matter_id):
        raise ValueError(f"matter id must be letters/digits/._- , not {matter_id!r}")
    return matters_dir(workspace) / f"{matter_id}.json"


def load_matter(workspace: str | Path, matter_id: str) -> Matter:
    path = matter_path(workspace, matter_id)
    if not path.exists():
        raise FileNotFoundError(f"no matter named {matter_id!r}")
    return Matter.model_validate_json(path.read_text(encoding="utf-8"))


def save_matter(workspace: str | Path, matter: Matter) -> Path:
    matter.updated = _now()
    path = matter_path(workspace, matter.id)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, matter.model_dump_json(indent=2) + "\n")
    return path


def list_matters(workspace: str | Path) -> list[Matter]:
    directory = matters_dir(workspace)
    if not directory.is_dir():
        return []
    matters = []
    for path in sorted(directory.glob("*.json")):
        try:
            matters.append(Matter.model_validate_json(path.read_text(encoding="utf-8")))
        except Exception as e:
            # Never silently: a corrupt matter would otherwise vanish from the
            # inbox and the exception register at once.
            sys.stderr.write(f"warning: skipping unreadable matter file {path}: {e}\n")
    return matters


def open_matter(workspace: str | Path, matter_id: str, template_name: str,
                answers: dict[str, AnswerValue], counterparty: str,
                status: str = "open") -> Matter:
    template_file = model.template_file(workspace, template_name)
    template: Template = model.load(str(template_file))
    path = matter_path(workspace, matter_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    with locked(path):
        if path.exists():
            raise ValueError(f"a matter named {matter_id!r} already exists")
        matter = Matter(
            id=matter_id,
            template=template_name,
            template_hash=template_hash(template),
            doc_type=template.doc_type,
            counterparty=counterparty,
            answers=answers,
            status=status,  # type: ignore[arg-type]
            opened=_now(),
        )
        save_matter(workspace, matter)
    journal.append(str(template_file), actor="system", kind="decision",
                   detail=f"matter {matter_id!r} opened against {counterparty} "
                          f"(template hash pinned)",
                   matter=matter_id, counterparty=counterparty)
    return matter


def close_matter(workspace: str | Path, matter_id: str, *, status: str,
                 by: str, why: str = "") -> Matter:
    """End a matter as 'agreed' or 'abandoned'. Agreement requires an empty inbox."""
    if status not in CLOSED_STATUSES:
        raise ValueError(f"status must be one of {CLOSED_STATUSES}, not {status!r}")
    ws = Path(workspace)
    with locked(matter_path(ws, matter_id)):
        matter = load_matter(ws, matter_id)
        pending = matter.pending_escalations()
        if status == "agreed" and pending:
            raise ValueError(f"{len(pending)} escalation(s) still pending — "
                             f"resolve them before closing as agreed")
        matter.status = status  # type: ignore[assignment]
        save_matter(ws, matter)
    journal.append(str(model.template_file(ws, matter.template)), actor=by,
                   kind="decision", detail=f"matter {matter_id!r} closed as {status}",
                   why=why or None, matter=matter_id, counterparty=matter.counterparty)
    return matter


def ingest_round(workspace: str | Path, matter_id: str, source_path: str,
                 *, negotiate: bool = False, say=None) -> tuple[Matter, Round, str | None]:
    """Ingest a counterparty's returned document as a clause-anchored round.

    The one implementation behind both `tb matter round` and the web UI.
    With negotiate=True, runs the playbook-gated negotiator over the asks:
    every response is recorded on the round; gate-passing accepts become
    deviations (attributed to the assistant), escalations land in the
    matter's queue, and counters/rejects wait for a human to transmit them.
    Returns (matter, round, negotiation_report_or_None).
    """
    from .ingest import read_document
    from .roundtrip import asks_to_markup, extract_asks

    say = say or (lambda msg: None)
    ws = Path(workspace)
    with locked(matter_path(ws, matter_id)):
        matter = load_matter(ws, matter_id)
        if matter.status in CLOSED_STATUSES:
            raise ValueError(f"matter {matter_id!r} is {matter.status} — "
                             f"no further rounds can be ingested")
        template_file = model.template_file(ws, matter.template)
        template = model.load(str(template_file))
        current_hash = template_hash(template)
        if current_hash != matter.template_hash:
            say("note: the template has evolved since this matter opened — the diff "
                "runs against the CURRENT template (each round records its hash)")

        from . import render as render_mod
        planned = render_mod.plan(template, matter.answers)
        their_text = read_document(source_path)
        asks, unanchored = extract_asks(planned, their_text)
        round_ = Round(number=len(matter.rounds) + 1, received=_now(),
                       source=Path(source_path).name, asks=asks,
                       unanchored=unanchored, template_hash=current_hash)
        matter.rounds.append(round_)
        if matter.status == "intake":
            matter.status = "open"       # a returned document means negotiation began
        say(f"round {round_.number}: {len(asks)} clause-anchored asks"
            + (f", {len(unanchored)} unanchored segment(s) — review manually"
               if unanchored else ""))

        if not negotiate:
            save_matter(ws, matter)
            return matter, round_, None

        from . import skill as skill_mod
        from .negotiate import negotiate as run_negotiation
        from .negotiate import render_report, resolve_threshold

        playbook = skill_mod.load_playbook(ws, matter.doc_type)
        if playbook is None:
            # No learned playbook yet: everything is the lawyer's, by construction.
            for ask in asks:
                matter.escalations.append(Escalation(
                    clause_id=ask.clause_id, round=round_.number, stance="escalate",
                    analysis="no playbook for this document type yet — the lawyer decides; "
                             "the decision will be journaled and feed `tb skill update`",
                    proposed_text=None, requires="lawyer"))
            round_.plan_summary = f"no playbook — all {len(asks)} asks escalated"
            save_matter(ws, matter)
            say(round_.plan_summary)
            return matter, round_, None

        entries = journal.read(str(template_file))
        threshold = resolve_threshold(playbook)
        plan = run_negotiation(template, asks_to_markup(asks, unanchored), playbook,
                               entries, threshold,
                               replay_scores=skill_mod.load_replay(ws, matter.doc_type))
        round_.plan_summary = plan.summary
        asks_by_clause = {a.clause_id: a for a in asks}
        for response in plan.responses:
            round_.responses.append(Response(
                clause_id=response.clause_id, stance=response.stance,
                rationale=response.rationale, proposed_text=response.proposed_text,
                decider=response.decider))
            ask = asks_by_clause.get(response.clause_id)
            if response.stance == "escalate" or (response.stance == "accept"
                                                 and ask is None) \
                    or (response.stance == "accept"
                        and response.decider != journal.ASSISTANT_ACTOR):
                # escalate when the gate said so, when the model "accepted" a
                # clause nobody asked about, or when the delegation matrix
                # assigns even gate-passing decisions to a human tier
                matter.escalations.append(Escalation(
                    clause_id=response.clause_id, round=round_.number,
                    stance="escalate", analysis=response.rationale,
                    proposed_text=response.proposed_text, requires=response.decider))
            elif response.stance == "accept":
                # gate-passed acceptance — including of a deletion — recorded
                # as a deviation, attributed to the assistant (assistant
                # decisions never raise maturity)
                record_deviation(ws, matter, clause_id=response.clause_id,
                                 standard_text=ask.our_text,
                                 agreed_text=ask.their_text,
                                 approved_by=journal.ASSISTANT_ACTOR,
                                 rationale=response.rationale,
                                 disposition="accepted",
                                 round_number=round_.number)
                say(f"accepted {response.clause_id} per playbook (recorded as deviation)")
        save_matter(ws, matter)
        journal.append(str(template_file), actor=journal.ASSISTANT_ACTOR,
                       kind="negotiation", detail=plan.summary[:300],
                       matter=matter.id, counterparty=matter.counterparty)
        return matter, round_, render_report(plan, template, entries, threshold)


def resolve_escalation(workspace: str | Path, matter_id: str, *, clause_id: str,
                       decision: str, by: str, why: str,
                       agreed_text: str | None = None) -> Matter:
    """Record the human decision on a negotiation point — THE one implementation.

    decision:
      'accept-theirs' — the counterparty's text (or deletion) becomes a deviation
      'hold'          — our standard stands; journaled as a rejected ask
      'custom'        — `agreed_text` becomes the deviation
    Resolves the pending escalation for the clause if there is one; a round
    ingested without --negotiate has asks but no escalations, and can still
    be resolved here.
    """
    ws = Path(workspace)
    with locked(matter_path(ws, matter_id)):
        matter = load_matter(ws, matter_id)
        if matter.status in CLOSED_STATUSES:
            raise ValueError(f"matter {matter_id!r} is {matter.status}")
        ask = next((a for r in reversed(matter.rounds) for a in r.asks
                    if a.clause_id == clause_id), None)
        escalation = next((e for e in matter.escalations
                           if e.clause_id == clause_id and not e.resolved), None)
        if ask is None and escalation is None:
            raise ValueError(f"nothing to resolve on {clause_id!r}: no ask in any "
                             f"round and no pending escalation")
        round_number = escalation.round if escalation else None

        match decision:
            case "hold":
                if escalation is not None:
                    escalation.resolved = True
                save_matter(ws, matter)
                journal.append(str(model.template_file(ws, matter.template)),
                               actor=by, kind="decision", clause_id=clause_id,
                               why=why, matter=matter.id,
                               counterparty=matter.counterparty,
                               disposition="rejected",
                               detail=f"held standard text in matter {matter.id!r}")
            case "accept-theirs":
                if ask is None:
                    raise ValueError(f"no counterparty ask for {clause_id!r} in any "
                                     f"round — use 'custom' with the agreed text")
                record_deviation(ws, matter, clause_id=clause_id,
                                 standard_text=ask.our_text,
                                 agreed_text=ask.their_text, approved_by=by,
                                 rationale=why, disposition="accepted",
                                 round_number=round_number)
            case "custom":
                if not (agreed_text or "").strip():
                    raise ValueError("a custom resolution needs the agreed text")
                record_deviation(ws, matter, clause_id=clause_id,
                                 standard_text=ask.our_text if ask else "",
                                 agreed_text=agreed_text or "", approved_by=by,
                                 rationale=why, disposition="countered",
                                 round_number=round_number)
            case _:
                raise ValueError("decision must be accept-theirs, hold or custom")
        return matter


def record_deviation(workspace: str | Path, matter: Matter, *, clause_id: str,
                     standard_text: str, agreed_text: str, approved_by: str,
                     rationale: str, disposition: str = "conceded",
                     round_number: int | None = None) -> Deviation:
    """The agreed departure from standard — journaled as a learning decision.

    Validates the clause against the template (a typo must fail loudly, not
    create a bogus exception-register row) and attributes the deviation to
    the round that actually asked for it.
    """
    ws = Path(workspace)
    template_file = model.template_file(ws, matter.template)
    template = model.load(str(template_file))
    if all(c.id != clause_id for c in template.clauses):
        raise ValueError(f"{clause_id!r} is not a clause of template "
                         f"{matter.template!r}")
    if round_number is None:
        round_number = next(
            (r.number for r in reversed(matter.rounds)
             if any(a.clause_id == clause_id for a in r.asks)),
            matter.rounds[-1].number if matter.rounds else 0)
    deviation = Deviation(
        clause_id=clause_id,
        round=round_number,
        standard_text=standard_text,
        agreed_text=agreed_text,
        approved_by=approved_by,
        rationale=rationale,
        date=_now()[:10],
    )
    matter.deviations.append(deviation)
    for escalation in matter.escalations:
        if escalation.clause_id == clause_id and not escalation.resolved:
            escalation.resolved = True
    save_matter(ws, matter)
    journal.append(str(template_file), actor=approved_by, kind="decision",
                   clause_id=clause_id, why=rationale, matter=matter.id,
                   counterparty=matter.counterparty, disposition=disposition,
                   detail=f"deviation agreed in matter {matter.id!r}")
    return deviation
