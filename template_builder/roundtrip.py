"""The round-trip diff: a counterparty's returned document -> clause-anchored asks.

Because rendering is deterministic, we know exactly what we sent for
(template@hash, answers). This module diffs what came back against that
ground truth — no LLM, no guesswork:

1. Anchor their document to our clauses by the numbered headings a returned
   .docx almost always preserves ("7. Term and Survival"). Anchoring is
   sequential — each heading is searched for after the previous anchor — and
   when a heading appears more than once (a table of contents, a heading
   quoted in body text), the candidate whose following text best matches our
   rendered clause wins.
2. Per anchored clause, compare normalised text (whitespace, smart quotes,
   dashes). ANY remaining difference becomes a "modify" Ask carrying both
   versions verbatim — a one-digit change to a term is exactly the redline
   that matters most, so nothing is dismissed as "close enough".
3. A clause whose heading vanished becomes a "delete" Ask; text we cannot
   place — including anything ahead of the first clause that is more than a
   title block — goes into the round's `unanchored` bucket for a human to
   read. Never silently dropped.

If too few headings anchor (they retyped the document), the diff refuses to
guess and reports everything as unanchored: a wrong anchor is worse than no
anchor. Reordered clauses lose their anchors and surface the same way.
"""

import difflib
import re

from .matter import Ask
from .render import PlannedClause

# Below this fraction of anchored headings, per-clause diffing is unsafe.
MIN_ANCHOR_FRACTION = 0.5
# Leading text at most this big is taken to be the title block, not content.
TITLE_BLOCK_LINES, TITLE_BLOCK_CHARS = 3, 200

_NOISE = str.maketrans({"“": '"', "”": '"', "‘": "'", "’": "'",
                        "–": "-", "—": "-", " ": " "})


def _normalise(text: str) -> str:
    return " ".join(text.translate(_NOISE).split())


def _heading_pattern(number: int, heading: str) -> re.Pattern:
    # "7. Term and Survival" — tolerate renumbering and common numbering
    # styles ("7)", "Section 7.", "7.1"), plus case/whitespace drift.
    words = [re.escape(w) for w in heading.split()]
    return re.compile(
        r"^\s*(?:(?:section|clause|article)\s+)?(?:\d+(?:\.\d+)*[.)]?)?\s*"
        + r"\s+".join(words) + r"\s*[.:]?\s*$",
        re.IGNORECASE | re.MULTILINE)


def _best_candidate(candidates: list[re.Match], their_text: str,
                    rendered: str) -> re.Match:
    """Among duplicate heading matches, pick the one followed by clause text.

    A table-of-contents line is followed by more headings; the real heading
    is followed by something resembling our rendered clause.
    """
    expected = _normalise(rendered)[:400]
    def score(m: re.Match) -> float:
        after = _normalise(their_text[m.end():m.end() + 2 * len(expected)])[:400]
        return difflib.SequenceMatcher(None, expected, after).ratio()
    return max(candidates, key=score)


def extract_asks(planned: list[PlannedClause],
                 their_text: str) -> tuple[list[Ask], list[str]]:
    """Diff the returned document against the deterministic render.

    Returns (asks, unanchored_segments).
    """
    anchors: list[tuple[int, int, PlannedClause]] = []  # (start, end_of_heading, clause)
    position = 0
    for p in planned:
        pattern = _heading_pattern(p.number, p.clause.heading)
        candidates = [m for m in pattern.finditer(their_text)
                      if m.start() >= position]
        if not candidates:
            continue
        match = (candidates[0] if len(candidates) == 1
                 else _best_candidate(candidates, their_text, p.rendered_text))
        anchors.append((match.start(), match.end(), p))
        position = match.end()

    if planned and len(anchors) / len(planned) < MIN_ANCHOR_FRACTION:
        return [], [their_text.strip() or "(empty document)"]

    anchored_ids = {p.clause.id for _, _, p in anchors}

    asks: list[Ask] = []
    unanchored: list[str] = []

    # Text before the first anchor: a short title block is expected; anything
    # more (an inserted clause, an edited party block) goes to human review.
    if anchors:
        leading = their_text[:anchors[0][0]].strip()
        lines = [l for l in leading.splitlines() if l.strip()]
        if leading and (len(lines) > TITLE_BLOCK_LINES
                        or len(leading) > TITLE_BLOCK_CHARS):
            unanchored.append(leading)

    for i, (start, heading_end, p) in enumerate(anchors):
        segment_end = anchors[i + 1][0] if i + 1 < len(anchors) else len(their_text)
        theirs = their_text[heading_end:segment_end].strip()
        ours = p.rendered_text
        if _normalise(theirs) == _normalise(ours):
            continue
        asks.append(Ask(clause_id=p.clause.id, kind="modify",
                        our_text=ours, their_text=theirs))

    for p in planned:
        if p.clause.id not in anchored_ids:
            asks.append(Ask(clause_id=p.clause.id, kind="delete",
                            our_text=p.rendered_text, their_text=""))

    return asks, unanchored


def asks_to_markup(asks: list[Ask], unanchored: list[str]) -> str:
    """Render asks as the markup text the negotiator consumes."""
    parts = ["Counterparty markup, diffed clause-by-clause against our sent draft:"]
    for ask in asks:
        parts.append("")
        if ask.kind == "delete":
            parts.append(f"== {ask.clause_id}: DELETED ENTIRELY ==")
            parts.append(f"Our text was: {ask.our_text[:600]}")
        else:
            parts.append(f"== {ask.clause_id}: MODIFIED ==")
            parts.append(f"OURS:   {ask.our_text[:800]}")
            parts.append(f"THEIRS: {ask.their_text[:800]}")
    for segment in unanchored:
        parts.append("")
        parts.append("== text we could not anchor to a clause (review manually) ==")
        parts.append(segment[:800])
    return "\n".join(parts)
