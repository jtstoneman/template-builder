"""Moot-court replay: does the assistant reproduce the lawyers' actual decisions?

A maturity count says a clause has been negotiated ten times; it does not say
the assistant would have decided those ten the same way. Replay hides each
historical outcome, shows the model the situation and the playbook, asks it
to predict the disposition, and scores agreement per clause. The gate then
requires BOTH maturity and replay agreement before acting alone.

Only decisions the distiller never saw are replayed (journal ids after the
playbook's `distilled_through` stamp): predicting an outcome the playbook
was itself written from would test memory, not judgment — the scores would
be optimistically inflated exactly where they matter.

Honest limitation: for hand-written journal entries the "situation" is the
first segment of the lawyer's rationale (the part before ';'), because the
rest usually states the outcome. Rounds ingested via the round-trip diff
carry clean, structured asks and replay much more faithfully over time.
"""

from typing import Literal

from pydantic import BaseModel

from .journal import ASSISTANT_ACTOR, JournalEntry
from .llm import complete
from .model import Template
from .skill import parse_distilled_through

MAX_REPLAYS_PER_CLAUSE = 10


class ReplayPrediction(BaseModel):
    predicted: Literal["accepted", "countered", "rejected", "conceded"]
    reasoning: str


REPLAY_SYSTEM = """\
You are replaying a historical contract negotiation decision for scoring.

You receive the firm's playbook for the document type, the clause in
question, and the situation as it presented itself (the counterparty and
what was being asked). Predict how the firm actually disposed of the point:

- "accepted"  — the firm took the counterparty's position as asked
- "countered" — the firm proposed something between the positions
- "rejected"  — the firm refused and held its standard
- "conceded"  — the firm gave ground beyond its opening, typically in a trade

Decide strictly from the playbook; do not hedge. This prediction is compared
against what the firm's lawyer actually did.
"""


def _situation(entry: JournalEntry) -> str:
    # the first segment of the rationale is usually the ask; the rest the outcome
    ask = (entry.why or "").split(";")[0].strip()
    parts = [f"Clause: {entry.clause_id}"]
    if entry.counterparty:
        parts.append(f"Counterparty: {entry.counterparty}")
    parts.append(f"Situation: {ask}")
    return "\n".join(parts)


def replayable(entries: list[JournalEntry]) -> dict[str, list[JournalEntry]]:
    """Human decisions with rationale AND disposition, grouped by clause."""
    grouped: dict[str, list[JournalEntry]] = {}
    for entry in entries:
        if (entry.actor != ASSISTANT_ACTOR and entry.clause_id and entry.disposition
                and (entry.why or "").strip()):
            grouped.setdefault(entry.clause_id, []).append(entry)
    return grouped


def run_replay(template: Template, playbook: str,
               entries: list[JournalEntry],
               progress=None) -> dict[str, dict]:
    """Returns {clause_id: {"agree": n, "total": n, "misses": [entry ids]}}."""
    say = progress or (lambda msg: None)
    distilled_through = parse_distilled_through(playbook)
    unseen = [e for e in entries if e.id > distilled_through]
    if len(unseen) < len(entries):
        say(f"replaying only decisions after [j:{distilled_through}] — the "
            f"distiller saw the earlier ones, so they cannot count as evidence")
    scores: dict[str, dict] = {}
    for clause_id, decisions in replayable(unseen).items():
        sample = decisions[-MAX_REPLAYS_PER_CLAUSE:]
        agree, misses = 0, []
        for entry in sample:
            prompt = (f"Firm playbook for {template.doc_type}:\n{playbook}\n\n"
                      f"{_situation(entry)}\n\nPredict the firm's disposition.")
            prediction = complete(REPLAY_SYSTEM, prompt, ReplayPrediction,
                                  max_tokens=2000)
            if prediction.predicted == entry.disposition:
                agree += 1
            else:
                misses.append(entry.id)
        scores[clause_id] = {"agree": agree, "total": len(sample), "misses": misses}
        say(f"{clause_id}: {agree}/{len(sample)} predictions matched the lawyer")
    return scores
