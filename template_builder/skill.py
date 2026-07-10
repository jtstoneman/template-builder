"""Learned skills: distil the journal into a per-doc-type playbook SKILL.md.

The skill file is a real Claude skill (YAML frontmatter + markdown body)
living at <workspace>/skills/<doc-type-slug>/SKILL.md. Its body is a
per-clause playbook — position, fallback ladder, red lines — distilled by an
LLM from the template's journal, under two hard rules enforced in code:

1. Every play must cite journal entries ([j:12]); citations that don't
   resolve, or plays for clauses that don't exist, are dropped.
2. The rendering is deterministic, so successive updates diff cleanly and a
   partner can review the playbook like any other document.

The playbook then feeds forward: `tb build` injects it into synthesis (new
templates of the type start from the firm's learned positions) and
`tb negotiate` drafts against it.
"""

import datetime
import re
from pathlib import Path

from pydantic import BaseModel

from .fsio import atomic_write_text
from .journal import ASSISTANT_ACTOR, JournalEntry, read as read_journal
from .llm import complete
from .model import Template

DEFAULT_MATURITY_THRESHOLD = 10  # "the first 10 times a lawyer negotiates"

# The delegation-of-authority matrix: who may decide at each risk tier.
# Firms adopt automation as GRADED delegation, not a switch — this is the dial.
DEFAULT_DELEGATION = {
    "red_line": "partner",     # asks that touch a red line
    "immature": "lawyer",      # clauses below the maturity threshold / without a play
    "mature": "assistant",     # gate-passing decisions on well-precedented clauses
}


def frontmatter(playbook_md: str | None) -> dict[str, str]:
    """The flat key: value pairs between the playbook's --- delimiters."""
    fields: dict[str, str] = {}
    if not playbook_md:
        return fields
    in_frontmatter = False
    for line in playbook_md.splitlines():
        if line.strip() == "---":
            if in_frontmatter:
                break  # end of frontmatter
            in_frontmatter = True
            continue
        if not in_frontmatter:
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def parse_delegation(playbook_md: str | None) -> dict[str, str]:
    """Read delegation_* keys from the playbook frontmatter (flat, greppable)."""
    delegation = dict(DEFAULT_DELEGATION)
    for key, value in frontmatter(playbook_md).items():
        tier = key.removeprefix("delegation_")
        if key.startswith("delegation_") and tier in delegation and value:
            delegation[tier] = value
    return delegation


def parse_threshold(playbook_md: str | None) -> int | None:
    """The playbook's own autonomy_threshold, if stated (authoritative)."""
    value = frontmatter(playbook_md).get("autonomy_threshold", "")
    return int(value) if value.isdigit() else None


def parse_distilled_through(playbook_md: str | None) -> int:
    """The last journal id the distiller saw — replay only trusts newer entries."""
    value = frontmatter(playbook_md).get("distilled_through", "")
    return int(value) if value.isdigit() else 0


def slugify(doc_type: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", doc_type.lower()).strip("-") or "document"


def skill_path(workspace: str | Path, doc_type: str) -> Path:
    return Path(workspace) / "skills" / slugify(doc_type) / "SKILL.md"


def load_playbook(workspace: str | Path, doc_type: str) -> str | None:
    path = skill_path(workspace, doc_type)
    return path.read_text(encoding="utf-8") if path.exists() else None


def replay_path(workspace: str | Path, doc_type: str) -> Path:
    return skill_path(workspace, doc_type).parent / "replay.json"


def load_replay(workspace: str | Path, doc_type: str) -> dict[str, dict]:
    import json
    path = replay_path(workspace, doc_type)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def save_replay(workspace: str | Path, doc_type: str, scores: dict[str, dict]) -> Path:
    import json
    path = replay_path(workspace, doc_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, json.dumps(scores, indent=2) + "\n")
    return path


# ------------------------------------------------------------- distillation --

class ClausePlay(BaseModel):
    clause_id: str
    position: str          # the firm's preferred stance, one short paragraph
    fallbacks: list[str]   # ordered concession ladder, best first
    red_lines: list[str]   # never concede
    citations: list[int]   # journal entry ids that support this play


class SkillDraft(BaseModel):
    overview: str          # doc-type-level strategy learned so far
    plays: list[ClausePlay]
    notes: list[str]       # distiller observations for the reviewing lawyer


DISTIL_SYSTEM = """\
You maintain a law firm's drafting and negotiation playbook for one contract
type. You receive the current playbook (possibly empty), the template's
clause ids, and the journal: dated, attributed decisions with their
rationale ("why"), counterparty and disposition.

Produce the updated playbook:
- One play per clause THAT THE JOURNAL SAYS SOMETHING ABOUT. "position" is
  the firm's preferred stance; "fallbacks" the ordered concession ladder
  actually used or stated; "red_lines" what the journal shows was refused.
- EVERY play must cite the journal entry ids supporting it. Never invent a
  position the journal does not evidence — plays without citations will be
  discarded by the pipeline.
- Keep prior playbook content when the journal does not contradict it, and
  carry its citations forward; refine it when new entries add nuance.
- Write for a lawyer who has never seen these deals: plain, specific,
  self-contained ("cap survives at 12 months if counterparty concedes
  interest carve-out — traded in [j:14]").
- "notes": anything a reviewing partner should sanity-check.
"""


def distil(template: Template, entries: list[JournalEntry],
           existing_md: str | None) -> SkillDraft:
    parts = [f"Document type: {template.doc_type}",
             "Clause ids: " + ", ".join(c.id for c in template.clauses), ""]
    parts.append("Current playbook:" if existing_md else "Current playbook: (none yet)")
    if existing_md:
        parts.append(existing_md[:6000])
    parts.append("")
    parts.append("Journal (the decisions to learn from):")
    for entry in entries:
        parts.append(entry.compact())
    return complete(DISTIL_SYSTEM, "\n".join(parts), SkillDraft)


def validate_draft(draft: SkillDraft, template: Template,
                   entries: list[JournalEntry]) -> tuple[list[ClausePlay], list[str]]:
    """The LLM proposes; deterministic code disposes."""
    known_clauses = {c.id for c in template.clauses}
    known_ids = {e.id for e in entries}
    kept, notes = [], []
    for play in draft.plays:
        citations = [c for c in play.citations if c in known_ids]
        if play.clause_id not in known_clauses:
            notes.append(f"dropped play for unknown clause {play.clause_id!r}")
        elif not citations:
            notes.append(f"dropped uncited play for {play.clause_id!r} — every position "
                         f"must trace to journal entries")
        else:
            play.citations = citations
            kept.append(play)
    kept.sort(key=lambda p: [c.id for c in template.clauses].index(p.clause_id))
    return kept, notes


# --------------------------------------------------------------- rendering --

def render_skill(doc_type: str, draft: SkillDraft, plays: list[ClausePlay],
                 entry_count: int, threshold: int, *,
                 delegation: dict[str, str] | None = None,
                 distilled_through: int = 0) -> str:
    delegation = delegation or DEFAULT_DELEGATION
    slug = slugify(doc_type)
    today = datetime.date.today().isoformat()
    lines = [
        "---",
        f"name: {slug}-playbook",
        f"description: Learned drafting and negotiation playbook for {doc_type} templates. "
        f"Distilled from {entry_count} journaled decisions; [j:N] cites the template journal. "
        f"Clauses need {threshold}+ journaled human decisions before the assistant may act "
        f"on them without escalation.",
        f"updated: {today}",
        f"distilled_from_entries: {entry_count}",
        f"distilled_through: {distilled_through}",
        f"autonomy_threshold: {threshold}",
        f"delegation_red_line: {delegation['red_line']}",
        f"delegation_immature: {delegation['immature']}",
        f"delegation_mature: {delegation['mature']}",
        "---",
        "",
        f"# {doc_type} — negotiation playbook",
        "",
        draft.overview.strip(),
        "",
        "## Clause playbook",
        "",
    ]
    for play in plays:
        cites = " ".join(f"[j:{c}]" for c in play.citations)
        lines.append(f"### {play.clause_id}")
        lines.append("")
        lines.append(f"**Position** — {play.position.strip()} {cites}")
        if play.fallbacks:
            lines.append("")
            lines.append("**Fallbacks (in order):**")
            lines.extend(f"{i}. {fb}" for i, fb in enumerate(play.fallbacks, 1))
        if play.red_lines:
            lines.append("")
            lines.append("**Red lines:**")
            lines.extend(f"- {rl}" for rl in play.red_lines)
        lines.append("")
    if draft.notes:
        lines.append("## Distiller notes for the reviewing lawyer")
        lines.append("")
        lines.extend(f"- {note}" for note in draft.notes)
        lines.append("")
    return "\n".join(lines)


# ------------------------------------------------------------ orchestration --

def update_skill(template_path: str, template: Template,
                 threshold: int = DEFAULT_MATURITY_THRESHOLD) -> tuple[Path, str, list[str]]:
    """Distil the journal into the doc type's SKILL.md. Returns (path, md, notes).

    The assistant's own journaled actions are never learning material — the
    playbook must trace to human decisions only, or the loop would launder
    the model's choices into "precedent".
    """
    entries = read_journal(template_path)
    learnable = [e for e in entries
                 if e.actor != ASSISTANT_ACTOR and (e.why or "").strip()]
    if not learnable:
        raise ValueError("the journal has no entries with rationale yet — edit with --why "
                         "(or record decisions) before distilling a skill")
    workspace = Path(template_path).parent
    existing = load_playbook(workspace, template.doc_type)
    draft = distil(template, learnable, existing)
    # Citations must resolve against what the distiller was shown — an id
    # that exists in the journal but wasn't learnable is still a fabrication.
    plays, notes = validate_draft(draft, template, learnable)
    markdown = render_skill(
        template.doc_type, draft, plays, len(learnable),
        # A partner's hand-tuned frontmatter survives re-distillation.
        parse_threshold(existing) or threshold,
        delegation=parse_delegation(existing),
        distilled_through=max(e.id for e in learnable))
    path = skill_path(workspace, template.doc_type)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(path, markdown)
    # Replay scores were earned against the OLD playbook; the gate must not
    # credit them to the new one. Deleting them forces `tb skill replay`.
    replay_file = replay_path(workspace, template.doc_type)
    if replay_file.exists():
        replay_file.unlink()
        notes = [*notes, "replay.json invalidated — run `tb skill replay` to "
                         "re-earn autonomy evidence against the new playbook"]
    return path, markdown, notes
