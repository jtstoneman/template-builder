"""The learning record: an append-only journal beside each template.

Every edit, negotiation decision, build and skill update appends one JSONL
line to <template>.journal.jsonl. The journal is the raw material the skill
distiller learns from — a lawyer's first negotiations become precedents by
being recorded here, with their rationale ("why") and outcome (disposition).

Append-only by construction: entries get sequential ids and are never
rewritten, so a skill file's citations ([j:12]) stay resolvable forever.
"""

import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from .fsio import atomic_write_text, locked

ASSISTANT_ACTOR = "assistant"

type Disposition = Literal["accepted", "countered", "rejected", "conceded"]


class JournalEntry(BaseModel):
    id: int
    ts: str
    actor: str                        # who decided — a person, or "assistant"
    kind: Literal["edit", "decision", "build", "skill-update", "negotiation"]
    clause_id: str | None = None
    op: str | None = None             # e.g. "replace-text"
    detail: str | None = None         # what changed, in one line
    why: str | None = None            # the rationale — the learning signal
    matter: str | None = None         # deal / file reference
    counterparty: str | None = None
    disposition: Disposition | None = None

    def compact(self) -> str:
        """One line for prompts: everything the distiller needs, nothing else."""
        bits = [f"[j:{self.id}]", self.ts[:10], self.actor, self.kind]
        if self.clause_id:
            bits.append(f"clause={self.clause_id}")
        if self.counterparty:
            bits.append(f"counterparty={self.counterparty}")
        if self.disposition:
            bits.append(f"disposition={self.disposition}")
        line = " ".join(bits)
        if self.detail:
            line += f"\n    what: {self.detail}"
        if self.why:
            line += f"\n    why: {self.why}"
        return line


def journal_path(template_path: str) -> Path:
    return Path(str(template_path) + ".journal.jsonl")


def read(template_path: str) -> list[JournalEntry]:
    path = journal_path(template_path)
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    entries = []
    for number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            entries.append(JournalEntry.model_validate_json(line))
        except ValueError:
            if number == len(lines):
                # A torn final line is a crash mid-append: the entry was never
                # durable, so skipping it loses nothing — and the next append
                # under the lock writes a fresh, whole line after it.
                continue
            raise ValueError(
                f"{path} line {number} is corrupt — the audit record needs "
                f"human attention before anything else touches this template")
    return entries


def _repair_tail(path: Path) -> None:
    """Make the file end in a complete line before appending after it.

    A crash mid-append leaves a torn or newline-less final line; left in
    place, the next append would bury it mid-file as permanent corruption.
    """
    if not path.exists():
        return
    raw = path.read_text(encoding="utf-8")
    if not raw or raw.endswith("\n"):
        return
    head, _, tail = raw.rpartition("\n")
    try:
        JournalEntry.model_validate_json(tail)
        atomic_write_text(path, raw + "\n")          # complete, just unterminated
    except ValueError:
        atomic_write_text(path, head + "\n" if head else "")  # torn: drop it


def append(template_path: str, **fields) -> JournalEntry:
    # The lock makes read-latest-id -> write one line atomic across the web
    # server and CLI, so ids stay unique and citations stay resolvable.
    with locked(journal_path(template_path)):
        _repair_tail(journal_path(template_path))
        entries = read(template_path)
        entry = JournalEntry(
            id=(entries[-1].id + 1) if entries else 1,
            ts=datetime.datetime.now().isoformat(timespec="seconds"),
            **fields,
        )
        with open(journal_path(template_path), "a", encoding="utf-8") as f:
            f.write(entry.model_dump_json(exclude_none=True) + "\n")
    return entry


def maturity(entries: list[JournalEntry], clause_id: str) -> int:
    """How many journaled HUMAN decisions with rationale touch this clause.

    This is the autonomy gate's currency: the assistant's own actions never
    count towards the experience that would let it act alone.
    """
    return sum(
        1 for e in entries
        if e.clause_id == clause_id and e.actor != ASSISTANT_ACTOR and (e.why or "").strip()
    )
