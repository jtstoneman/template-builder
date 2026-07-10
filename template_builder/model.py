"""Data model for contract templates — Pydantic models over a JSON file.

A template is a small deterministic program:

    variables (the questionnaire) + clauses (atomic units of contract text)
        -> render(answers) -> finished contract

Clause text may contain two kinds of placeholder:

    {{variable_name}}     substituted with the questionnaire answer
    {{ref:clause-id}}     substituted with the rendered number of that clause

A clause can have several *variants* (alternative drafting approaches); the
first variant whose ``when`` condition is true is used, and a variant with
``when = None`` is the default. A clause with ``include_when`` set is only
included when that condition is true.

Every clause has a content hash over the text a lawyer actually approves
(heading, conditions, variant texts — not provenance metadata). Approvals
attach to those hashes, so editing one clause invalidates only that clause's
sign-off, never the whole template's.
"""

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .fsio import atomic_write_text

SCHEMA_VERSION = 1

# Template names and matter ids share one rule: a safe filename stem.
SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")

type VariableType = Literal["string", "number", "boolean", "choice"]
VARIABLE_TYPES: tuple[str, ...] = ("string", "number", "boolean", "choice")

# The approvals entry that covers the variable schema + questionnaire itself.
SCHEMA_APPROVAL_ID = "__schema__"


class TemplateError(ValueError):
    """Raised when a template file is malformed."""


class _Node(BaseModel):
    # extra="forbid": a template is a sign-off object — silently dropping an
    # unrecognised key on the next save would be silent data loss, so unknown
    # keys are rejected loudly at load time instead.
    model_config = ConfigDict(extra="forbid")


class Variable(_Node):
    name: str
    type: VariableType
    question: str
    choices: list[str] = Field(default_factory=list)  # only used when type == "choice"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class Variant(_Node):
    id: str
    text: str
    when: str | None = None  # condition expression; None = default variant
    provenance: list[str] = Field(default_factory=list)  # source contract filenames

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class Clause(_Node):
    id: str
    heading: str
    variants: list[Variant] = Field(min_length=1)
    include_when: str | None = None  # condition expression; None = always included
    defines: list[str] = Field(default_factory=list)  # defined terms introduced here

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


type DependencyKind = Literal["subject-to", "relies-on", "trade-off"]
DEPENDENCY_KINDS: tuple[str, ...] = ("subject-to", "relies-on", "trade-off")


class Dependency(_Node):
    """A semantic edge between clauses — the graph's 'consequential change' wiring.

    Kinds:
      subject-to  — from_clause operates subject to to_clause; to_clause takes
                    precedence (an indemnity drafted at a given exposure
                    because the aggregate liability cap governs it).
      relies-on   — from_clause assumes to_clause's machinery or content to
                    work (a remedy relying on the notice clause's mechanics).
      trade-off   — the two clauses were negotiated as a package; changing
                    one disturbs the accepted balance of the other.

    ``note`` is mandatory and is shown VERBATIM to whoever later edits either
    endpoint — it is the audit record of why the edge exists.
    """
    from_clause: str
    to_clause: str
    kind: DependencyKind
    note: str

    def describe(self) -> str:
        joiner = "trade-off with" if self.kind == "trade-off" else self.kind
        return f"{self.from_clause} {joiner} {self.to_clause}: {self.note}"


class Approval(_Node):
    clause_id: str
    hash: str
    by: str
    date: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


class Template(_Node):
    doc_type: str
    variables: list[Variable]
    clauses: list[Clause]
    dependencies: list[Dependency] = Field(default_factory=list)
    approvals: list[Approval] = Field(default_factory=list)
    certificate: dict[str, Any] | None = None  # written by `tb approve`; see approve.py
    sources: list[str] = Field(default_factory=list)  # contracts the template was built from
    # Deal context supplied per source at build time ("seller-friendly, W&I
    # insurance", ...) — metadata, deliberately outside every content hash.
    source_contexts: dict[str, str] = Field(default_factory=dict)
    schema_version: int = SCHEMA_VERSION

    def clause(self, clause_id: str) -> Clause:
        for c in self.clauses:
            if c.id == clause_id:
                return c
        raise KeyError(f"no clause with id {clause_id!r}")

    def variable(self, name: str) -> Variable:
        for v in self.variables:
            if v.name == name:
                return v
        raise KeyError(f"no variable named {name!r}")

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


# ---------------------------------------------------------------------------
# Hashing — what a sign-off attaches to
# ---------------------------------------------------------------------------

def _canonical(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha(payload: Any) -> str:
    return "sha256:" + hashlib.sha256(_canonical(payload)).hexdigest()


def clause_hash(clause: Clause) -> str:
    """Hash of a clause's approvable content.

    Covers everything a lawyer signs off on: heading, inclusion condition,
    defined terms, and each variant's condition + text. Deliberately excludes
    provenance — knowing *where* text came from is metadata, not content.
    """
    return _sha({
        "id": clause.id,
        "heading": clause.heading,
        "include_when": clause.include_when,
        "defines": sorted(clause.defines),
        "variants": [{"id": v.id, "when": v.when, "text": v.text} for v in clause.variants],
    })


def schema_hash(template: Template) -> str:
    """Hash of the variable schema + questionnaire wording.

    The questionnaire is part of the sign-off object: a changed question can
    change which answers people give, so it must invalidate approval too.
    """
    return _sha({
        "doc_type": template.doc_type,
        "variables": [v.model_dump() for v in sorted(template.variables, key=lambda v: v.name)],
    })


def template_hash(template: Template) -> str:
    """Hash of the whole template (clause order included — order is content).

    The dependency map is covered too: it encodes negotiated logic, so
    rewiring it must invalidate the certificate exactly like reordering
    clauses does. (Omitted when empty, so templates predating the feature
    keep their hashes.)
    """
    payload: dict[str, Any] = {
        "schema": schema_hash(template),
        "clauses": [clause_hash(c) for c in template.clauses],
    }
    if template.dependencies:
        payload["dependencies"] = sorted(
            (d.model_dump() for d in template.dependencies),
            key=lambda d: (d["from_clause"], d["to_clause"], d["kind"]),
        )
    return _sha(payload)


# ---------------------------------------------------------------------------
# Serialisation — Pydantic validation at every load boundary
# ---------------------------------------------------------------------------

def _readable(error: ValidationError, where: str) -> str:
    parts = []
    for detail in error.errors()[:5]:
        location = ".".join(str(piece) for piece in detail["loc"]) or where
        parts.append(f"{location}: {detail['msg']}")
    more = len(error.errors()) - 5
    if more > 0:
        parts.append(f"... and {more} more problem(s)")
    return f"{where} is malformed — " + "; ".join(parts)


def variable_from_dict(d: dict[str, Any]) -> Variable:
    if not isinstance(d, dict):
        raise TemplateError(f"a variable must be a JSON object, got {type(d).__name__}")
    try:
        return Variable.model_validate(d)
    except ValidationError as e:
        raise TemplateError(_readable(e, f"variable {d.get('name', '?')!r}")) from None


def clause_from_dict(d: dict[str, Any]) -> Clause:
    if not isinstance(d, dict):
        raise TemplateError(f"a clause must be a JSON object, got {type(d).__name__}")
    try:
        return Clause.model_validate(d)
    except ValidationError as e:
        raise TemplateError(_readable(e, f"clause {d.get('id', '?')!r}")) from None


def template_from_dict(d: Any) -> Template:
    if not isinstance(d, dict):
        raise TemplateError("template file must contain a JSON object")
    try:
        template = Template.model_validate(d)
    except ValidationError as e:
        raise TemplateError(_readable(e, "template")) from None
    if template.schema_version > SCHEMA_VERSION:
        raise TemplateError(
            f"template is schema version {template.schema_version}, but this "
            f"installation reads up to version {SCHEMA_VERSION} — upgrade "
            f"template_builder rather than risk reinterpreting the file")
    return template


def load(path: str) -> Template:
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except json.JSONDecodeError as e:
            raise TemplateError(f"{path} is not valid JSON: {e}") from None
        except UnicodeDecodeError:
            raise TemplateError(f"{path} is not valid UTF-8 text") from None
    try:
        return template_from_dict(data)
    except TemplateError as e:
        raise TemplateError(f"{path}: {e}") from None


def save(template: Template, path: str) -> None:
    # Crash-safe: a failure mid-write can never destroy a signed template.
    atomic_write_text(path, template.model_dump_json(indent=2) + "\n")


def template_file(workspace: str | Path, name: str) -> Path:
    """The one place `<workspace>/<name>.json` is spelled.

    Rejects names that are not safe filename stems, so a hostile or mistyped
    name can never traverse outside the workspace.
    """
    if not SAFE_NAME_RE.match(name):
        raise TemplateError(
            f"template name must be letters/digits/._- , not {name!r}")
    return Path(workspace) / f"{name}.json"
