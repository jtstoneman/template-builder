"""Cross-template drift: same-purpose clauses that have quietly diverged.

Canonical clause ids recur across a firm's templates ("governing-law",
"limitation-of-liability"). When a clause is improved in one template and
its sibling in another keeps the old wording, that divergence is invisible —
until it embarrasses someone. This detector is purely mechanical: same
clause id in two templates, meaningfully different default text, with the
journal telling us which side was touched most recently.
"""

import difflib
from dataclasses import dataclass
from pathlib import Path

from . import journal, model

DRIFT_RATIO = 0.90  # below this similarity, same-id clauses count as drifted


@dataclass(slots=True)
class Drift:
    clause_id: str
    template_a: str
    template_b: str
    similarity: float
    fresher: str | None   # the template whose clause was journaled more recently


def _default_text(clause) -> str:
    variant = next((v for v in clause.variants if v.when is None), clause.variants[0])
    return " ".join(variant.text.split())


def _edit_stamps(workspace: Path, template_name: str) -> dict[str, str]:
    """clause_id -> latest ts of a journal entry that CHANGED the template.

    Only kind="edit" counts: matter decisions and negotiations touch a
    clause's journal without touching its text, and must not make the stale
    side look freshly improved.
    """
    stamps: dict[str, str] = {}
    for e in journal.read(str(workspace / f"{template_name}.json")):
        if e.kind == "edit" and e.clause_id:
            stamps[e.clause_id] = max(stamps.get(e.clause_id, ""), e.ts)
    return stamps


def find_drift(workspace: str | Path) -> list[Drift]:
    ws = Path(workspace)
    templates: dict[str, model.Template] = {}
    for path in sorted(ws.glob("*.json")):
        try:
            templates[path.stem] = model.load(str(path))
        except model.TemplateError:
            continue

    clause_index: dict[str, list[tuple[str, str]]] = {}  # clause_id -> [(template, text)]
    for name, template in templates.items():
        for clause in template.clauses:
            clause_index.setdefault(clause.id, []).append((name, _default_text(clause)))

    # each template's journal is read once, not once per drifted pair
    stamps: dict[str, dict[str, str]] = {}

    def last_touch(name: str, clause_id: str) -> str:
        if name not in stamps:
            stamps[name] = _edit_stamps(ws, name)
        return stamps[name].get(clause_id, "")

    drifts: list[Drift] = []
    for clause_id, holders in clause_index.items():
        for i in range(len(holders)):
            for j in range(i + 1, len(holders)):
                (name_a, text_a), (name_b, text_b) = holders[i], holders[j]
                ratio = difflib.SequenceMatcher(None, text_a, text_b).ratio()
                if ratio >= DRIFT_RATIO:
                    continue
                touch_a = last_touch(name_a, clause_id)
                touch_b = last_touch(name_b, clause_id)
                fresher = None
                if touch_a != touch_b:
                    fresher = name_a if touch_a > touch_b else name_b
                drifts.append(Drift(clause_id=clause_id, template_a=name_a,
                                    template_b=name_b, similarity=round(ratio, 2),
                                    fresher=fresher))
    drifts.sort(key=lambda d: d.similarity)
    return drifts


def render_drift_report(workspace: str | Path) -> str:
    drifts = find_drift(workspace)
    lines = ["# Cross-template drift", ""]
    if not drifts:
        lines.append("No same-id clauses have drifted apart across the workspace.")
        return "\n".join(lines) + "\n"
    lines.append(f"{len(drifts)} drifted clause pair(s). Where one side was edited more "
                 f"recently, it is probably the improved wording — consider porting it "
                 f"through the gated edit pipeline.")
    lines.append("")
    for d in drifts:
        hint = (f" — `{d.fresher}` was touched more recently and is probably current"
                if d.fresher else "")
        lines.append(f"- `{d.clause_id}`: {d.template_a} vs {d.template_b} "
                     f"(similarity {d.similarity}){hint}")
    return "\n".join(lines) + "\n"
