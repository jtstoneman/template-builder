"""The web UI's backend: a thin FastAPI layer over the same modules the CLI uses.

The server hosts a WORKSPACE — a directory of template .json files — so the
frontend can list templates, open one in the workbench, and, crucially,
create a new one: upload a corpus of precedent contracts (with an optional
deal-context note per document), and a background build job runs the same
decompile → align → synthesise pipeline as `tb build`, streaming its
progress. Uploaded sources are kept under <workspace>/sources/<template>/ so
provenance filenames keep pointing at something real.

Nothing here has its own template logic — every endpoint delegates to
model / render / validate / approve / edit / merge, so the browser gets
exactly the CLI's semantics: edits validated before saving, rendering that
refuses silently-wrong output, hash-based approval status.
"""

import io
import json
import sys
import threading
import uuid
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse, Response
from pydantic import BaseModel, Field

from . import approve as approve_mod
from . import journal as journal_mod
from . import edit as edit_mod
from . import model
from . import ops
from . import render as render_mod
from . import skill as skill_mod
from . import validate as validate_mod
from .fsio import atomic_write_text
from .model import SAFE_NAME_RE as NAME_RE
from .model import SCHEMA_APPROVAL_ID

type AnswerValue = str | int | float | bool

STATIC_DIR = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # per uploaded document


# ------------------------------------------------------------ wire models ---

class VariableOut(BaseModel):
    name: str
    type: str
    question: str
    choices: list[str]


class VariantOut(BaseModel):
    id: str
    when: str | None
    text: str
    provenance: list[str]


class ClauseOut(BaseModel):
    id: str
    heading: str
    include_when: str | None
    defines: list[str]
    variants: list[VariantOut]


class DependencyOut(BaseModel):
    from_clause: str
    to_clause: str
    kind: str
    note: str


class TemplateOut(BaseModel):
    name: str
    doc_type: str
    sources: list[str]
    source_contexts: dict[str, str]
    variables: list[VariableOut]
    clauses: list[ClauseOut]
    dependencies: list[DependencyOut]
    status: dict[str, str]
    certificate: dict[str, Any] | None
    # False = clauses were added/removed/reordered since sign-off (per-clause
    # hashes can't see that; the certificate's template_hash can). None = no
    # certificate yet.
    certificate_current: bool | None
    has_report: bool
    has_skill: bool
    schema_approval_id: str


class TemplateSummary(BaseModel):
    name: str
    doc_type: str
    clauses: int
    variables: int
    approved: int
    stale: int
    unapproved: int


class FindingOut(BaseModel):
    level: str
    where: str
    message: str


class ValidateOut(BaseModel):
    findings: list[FindingOut]
    coverage: dict[str, Any]
    error_count: int


class RenderIn(BaseModel):
    answers: dict[str, AnswerValue]


class RenderedClause(BaseModel):
    id: str
    heading: str
    number: int
    text: str
    # export only: the editor's HTML (bold/italic/underline/lists — anything
    # else is stripped server-side by the richtext whitelist)
    html: str | None = None


class RenderOut(BaseModel):
    title: str
    clauses: list[RenderedClause]
    unapproved: list[str]  # included clauses (or the schema) lacking current approval


class ReplaceTextIn(BaseModel):
    clause_id: str
    variant_id: str
    text: str
    why: str | None = None    # journaled rationale — the learning signal
    actor: str | None = None


class EditOut(BaseModel):
    saved: bool
    new_errors: list[str]
    touched: list[str]
    review: list[str]  # blast radius: clauses that cross-reference the touched one
    messages: list[str]
    status: dict[str, str]


class ApproveIn(BaseModel):
    by: str = Field(min_length=1)


class ApproveOut(BaseModel):
    approved: list[str]
    certificate: dict[str, Any]
    status: dict[str, str]


class IntakeIn(BaseModel):
    term_sheet: str = Field(min_length=1)


class IntakeOut(BaseModel):
    answers: dict[str, AnswerValue | None]


class ExportIn(BaseModel):
    title: str
    clauses: list[RenderedClause]


class MatterSummary(BaseModel):
    id: str
    template: str
    doc_type: str
    counterparty: str
    status: str
    rounds: int
    deviations: int
    pending_escalations: int


class MatterOpenIn(BaseModel):
    id: str
    template: str
    counterparty: str
    answers: dict[str, AnswerValue]
    status: str = "open"  # "open" | "intake"


class EscalationOut(BaseModel):
    matter: str
    counterparty: str
    doc_type: str
    clause_id: str
    round: int
    analysis: str
    proposed_text: str | None
    requires: str
    our_text: str = ""     # what our standard says (from the round's ask)
    their_text: str = ""   # what "Accept theirs" would commit ("" = deletion)
    ask_kind: str = ""     # modify | delete | "" when no ask was recorded


class ResolveIn(BaseModel):
    clause_id: str
    decision: str                  # "accept-theirs" | "hold" | "custom"
    agreed_text: str | None = None  # required for "custom"
    by: str = Field(min_length=1)
    why: str = Field(min_length=1)


class CloseIn(BaseModel):
    status: str                    # "agreed" | "abandoned"
    by: str = Field(min_length=1)
    why: str = ""


class IntakeSchemaOut(BaseModel):
    """The counterparty-facing subset of a template: the questionnaire only."""
    doc_type: str
    variables: list[VariableOut]


class BuildJobOut(BaseModel):
    id: str
    state: str  # "running" | "done" | "error"
    log: list[str]
    error: str | None
    template: str | None  # template name, once done


class _BuildJob:
    def __init__(self):
        self.id = uuid.uuid4().hex[:12]
        self.state = "running"
        self.log: list[str] = []
        self.error: str | None = None
        self.template: str | None = None

    def out(self) -> BuildJobOut:
        return BuildJobOut(id=self.id, state=self.state, log=list(self.log),
                           error=self.error, template=self.template)


# ------------------------------------------------------------------- app ---

def create_app(workspace: str, *, read_only: bool = False) -> FastAPI:
    """Serve a workspace directory of template .json files.

    For convenience (and backward compatibility) `workspace` may also be a
    single template file — its parent directory becomes the workspace.

    With read_only=True the app is a public showcase: browsing, validation,
    rendering and .docx export work, but nothing writes to the workspace and
    nothing calls an LLM — so the instance needs no API key and no visitor
    can change (or run up a bill on) the shared demo data.
    """
    ws = Path(workspace)
    if ws.is_file():
        model.load(str(ws))  # fail fast on a malformed template
        ws = ws.parent
    if not ws.is_dir():
        raise NotADirectoryError(f"{workspace} is not a directory or template file")

    app = FastAPI(title="template-builder", docs_url=None, redoc_url=None)

    if read_only:
        from fastapi.responses import JSONResponse

        # Pure-compute POSTs are safe: they read the workspace, write nothing.
        def _compute_only(path: str) -> bool:
            return (path == "/api/export-docx"
                    or path.endswith(("/validate", "/render")))

        @app.middleware("http")
        async def read_only_guard(request, call_next):
            if (request.method not in ("GET", "HEAD", "OPTIONS")
                    and not _compute_only(request.url.path)):
                return JSONResponse(status_code=403, content={"detail":
                    "This is a read-only public demo — edits, matters and LLM "
                    "calls are disabled. Clone the repo and run `tb serve` "
                    "locally with your own API key for the full product."})
            return await call_next(request)

    @app.get("/api/config")
    def config() -> dict:
        return {"read_only": read_only}
    jobs: dict[str, _BuildJob] = {}
    building: set[str] = set()          # template names with an in-flight build
    building_lock = threading.Lock()

    def template_path(name: str) -> Path:
        if not NAME_RE.match(name) or ".." in name:
            raise HTTPException(status_code=404, detail=f"no template named {name!r}")
        path = ws / f"{name}.json"
        if not path.is_file():
            raise HTTPException(status_code=404, detail=f"no template named {name!r}")
        return path

    def report_file(name: str) -> Path:
        return ws / f"{name}.json.report.md"

    async def read_upload(upload: UploadFile) -> bytes:
        data = await upload.read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, f"{upload.filename or 'upload'} exceeds "
                                     f"{MAX_UPLOAD_BYTES // (1024 * 1024)} MB")
        return data

    def load(name: str) -> tuple[model.Template, Path]:
        path = template_path(name)
        try:
            return model.load(str(path)), path
        except model.TemplateError as e:
            raise HTTPException(status_code=500, detail=str(e)) from None

    def status_map(template: model.Template) -> dict[str, str]:
        return {cid: str(s) for cid, s in approve_mod.status(template).items()}

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    # ------------------------------------------------------------ listing --

    @app.get("/api/templates")
    def list_templates() -> list[TemplateSummary]:
        summaries = []
        for path in sorted(ws.glob("*.json")):
            if not NAME_RE.match(path.stem):
                continue  # unopenable via the API; don't list what 404s
            try:
                template = model.load(str(path))
            except model.TemplateError as e:
                # never silently: a corrupt template disappearing from the
                # workspace list looks like deletion
                sys.stderr.write(f"warning: skipping unloadable template "
                                 f"{path.name}: {e}\n")
                continue
            counts = {"approved": 0, "stale": 0, "unapproved": 0}
            for state in approve_mod.status(template).values():
                counts[str(state)] += 1
            summaries.append(TemplateSummary(
                name=path.stem, doc_type=template.doc_type,
                clauses=len(template.clauses), variables=len(template.variables),
                **counts))
        return summaries

    # -------------------------------------------------------------- build --

    @app.post("/api/build")
    async def start_build(
        doc_type: str = Form(min_length=1),
        name: str = Form(min_length=1),
        contexts: str = Form(default="[]"),  # JSON array, parallel to files
        files: list[UploadFile] = File(min_length=1),
    ) -> BuildJobOut:
        from .ingest import read_document
        from .merge import SourceDocument, build_template

        if not NAME_RE.match(name):
            raise HTTPException(422, "template name must be letters, digits, . _ -")
        # The name is claimed while the build runs, so two overlapping builds
        # can't both pass the exists() check and silently overwrite each other.
        with building_lock:
            if (ws / f"{name}.json").exists() or name in building:
                raise HTTPException(409, f"a template named {name!r} already exists")
            building.add(name)
        try:
            try:
                context_list = json.loads(contexts)
            except ValueError:
                context_list = None
            if (not isinstance(context_list, list)
                    or not all(isinstance(c, str) for c in context_list)):
                raise HTTPException(422, "contexts must be a JSON array of strings")
            context_list += [""] * (len(files) - len(context_list))

            # Persist the uploads so provenance filenames keep pointing at real files.
            source_dir = ws / "sources" / name
            source_dir.mkdir(parents=True, exist_ok=True)
            documents = []
            seen = set()
            for upload, context in zip(files, context_list):
                filename = Path(upload.filename or "document.txt").name
                if filename in seen:
                    raise HTTPException(422, f"two uploads share the name {filename!r}")
                seen.add(filename)
                (source_dir / filename).write_bytes(await read_upload(upload))
                try:
                    text = read_document(str(source_dir / filename))
                except Exception as e:
                    raise HTTPException(422, f"{filename}: {e}") from None
                documents.append(SourceDocument(name=filename, text=text,
                                                context=context.strip() or None))
        except BaseException:
            with building_lock:
                building.discard(name)
            raise

        job = _BuildJob()
        jobs[job.id] = job

        def run():
            # Same semantics as `tb build`: learned playbook in, build journaled.
            try:
                playbook = skill_mod.load_playbook(ws, doc_type)
                if playbook:
                    job.log.append("using the learned playbook for this document type")
                template, report, findings = build_template(
                    documents, doc_type, progress=job.log.append, playbook=playbook)
                model.save(template, str(ws / f"{name}.json"))
                atomic_write_text(report_file(name), report)
                journal_mod.append(str(ws / f"{name}.json"), actor="web-ui",
                                   kind="build",
                                   detail=f"built from {len(documents)} contracts"
                                          + (" using the learned playbook"
                                             if playbook else ""))
                errs = len(validate_mod.errors(findings))
                job.log.append(f"validation: {errs} error(s), "
                               f"{len(findings) - errs} warning(s)")
                job.template = name
                job.state = "done"
            except Exception as e:
                job.error = str(e)
                job.state = "error"
            finally:
                with building_lock:
                    building.discard(name)

        threading.Thread(target=run, daemon=True).start()
        return job.out()

    @app.get("/api/build/{job_id}")
    def build_status(job_id: str) -> BuildJobOut:
        job = jobs.get(job_id)
        if job is None:
            raise HTTPException(404, "no such build job")
        return job.out()

    # ----------------------------------------------------- template routes --

    @app.get("/api/t/{name}")
    def get_template(name: str) -> TemplateOut:
        template, path = load(name)
        return TemplateOut(
            name=name,
            doc_type=template.doc_type,
            sources=template.sources,
            source_contexts=template.source_contexts,
            variables=[VariableOut(**v.model_dump()) for v in template.variables],
            clauses=[ClauseOut(**c.model_dump()) for c in template.clauses],
            dependencies=[DependencyOut(**d.model_dump()) for d in template.dependencies],
            status=status_map(template),
            certificate=template.certificate,
            certificate_current=approve_mod.structure_current(template),
            has_report=report_file(name).exists(),
            has_skill=skill_mod.skill_path(ws, template.doc_type).exists(),
            schema_approval_id=SCHEMA_APPROVAL_ID,
        )

    @app.get("/api/t/{name}/skill", response_class=PlainTextResponse)
    def get_skill(name: str) -> str:
        template, _ = load(name)
        playbook = skill_mod.load_playbook(ws, template.doc_type)
        if playbook is None:
            raise HTTPException(404, "no playbook yet — journal decisions and run "
                                     "`tb skill update`")
        return playbook

    @app.get("/api/t/{name}/report", response_class=PlainTextResponse)
    def get_report(name: str) -> str:
        template_path(name)  # 404 on unknown template
        if not report_file(name).exists():
            raise HTTPException(404, "this template has no build report")
        return report_file(name).read_text(encoding="utf-8")

    @app.post("/api/t/{name}/validate")
    def run_validate(name: str) -> ValidateOut:
        template, _ = load(name)
        findings, coverage = validate_mod.validate(template)
        return ValidateOut(
            findings=[FindingOut(level=f.level, where=f.where, message=f.message)
                      for f in findings],
            coverage=coverage,
            error_count=len(validate_mod.errors(findings)),
        )

    @app.post("/api/t/{name}/render")
    def render(name: str, body: RenderIn) -> RenderOut:
        template, _ = load(name)
        try:
            planned = render_mod.plan(template, body.answers)
        except render_mod.RenderError as e:
            raise HTTPException(status_code=422, detail=e.problems) from None
        clause_status = approve_mod.status(template)
        unapproved = [p.clause.id for p in planned
                      if clause_status[p.clause.id] is not approve_mod.APPROVED]
        if clause_status[SCHEMA_APPROVAL_ID] is not approve_mod.APPROVED:
            unapproved.append(SCHEMA_APPROVAL_ID)
        elif approve_mod.structure_current(template) is False:
            unapproved.append("(template structure changed since sign-off)")
        return RenderOut(
            title=template.doc_type,
            clauses=[RenderedClause(id=p.clause.id, heading=p.clause.heading,
                                    number=p.number, text=p.rendered_text)
                     for p in planned],
            unapproved=unapproved,
        )

    @app.post("/api/t/{name}/edit/replace-text")
    def replace_text(name: str, body: ReplaceTextIn) -> EditOut:
        # The same write gate (and journaling) as the CLI — see ops.gated_edit.
        _, path = load(name)
        try:
            outcome = ops.gated_edit(
                str(path),
                lambda t: edit_mod.replace_text(t, body.clause_id, body.variant_id, body.text),
                journal_meta={"actor": body.actor or "web-ui", "op": "replace-text",
                              "why": body.why},
            )
        except KeyError as e:
            # unknown clause/variant id: the resource doesn't exist
            raise HTTPException(status_code=404,
                                detail=e.args[0] if e.args else str(e)) from None
        except edit_mod.EditError as e:
            # the edit is invalid, not missing — a client error
            raise HTTPException(status_code=422, detail=str(e)) from None
        return EditOut(
            saved=outcome.saved,
            new_errors=outcome.new_errors,
            touched=outcome.result.touched,
            review=outcome.result.review,
            messages=outcome.result.messages,
            status=status_map(outcome.template if outcome.saved else load(name)[0]),
        )

    @app.post("/api/t/{name}/approve")
    def do_approve(name: str, body: ApproveIn) -> ApproveOut:
        template, path = load(name)
        findings, coverage = validate_mod.validate(template)
        if validate_mod.errors(findings):
            raise HTTPException(
                status_code=409,
                detail=[str(f) for f in validate_mod.errors(findings)],
            )
        changed = approve_mod.approve(template, body.by, coverage)
        model.save(template, str(path))
        return ApproveOut(approved=changed, certificate=template.certificate or {},
                          status=status_map(template))

    @app.post("/api/t/{name}/intake")
    def intake(name: str, body: IntakeIn) -> IntakeOut:
        from .intake import prefill
        from .llm import LLMError
        template, _ = load(name)
        try:
            return IntakeOut(answers=prefill(template, body.term_sheet))
        except LLMError as e:
            raise HTTPException(status_code=502, detail=str(e)) from None

    # -------------------------------------------------------------- matters --

    from . import matter as matter_mod

    @app.get("/api/matters")
    def matters() -> list[MatterSummary]:
        return [MatterSummary(
            id=m.id, template=m.template, doc_type=m.doc_type,
            counterparty=m.counterparty, status=m.status, rounds=len(m.rounds),
            deviations=len(m.deviations),
            pending_escalations=len(m.pending_escalations()),
        ) for m in matter_mod.list_matters(ws)]

    @app.post("/api/matters")
    def open_matter(body: MatterOpenIn) -> matter_mod.Matter:
        if body.status not in ("open", "intake"):
            raise HTTPException(422, "status must be 'open' or 'intake'")
        template_path(body.template)  # 404 on unknown template
        try:
            return matter_mod.open_matter(ws, body.id, body.template, body.answers,
                                          body.counterparty, status=body.status)
        except ValueError as e:
            raise HTTPException(409, str(e)) from None

    @app.get("/api/matters/{matter_id}")
    def get_matter(matter_id: str) -> matter_mod.Matter:
        try:
            return matter_mod.load_matter(ws, matter_id)
        except (FileNotFoundError, ValueError) as e:
            raise HTTPException(404, str(e)) from None

    @app.post("/api/matters/{matter_id}/round")
    async def upload_round(matter_id: str,
                           file: UploadFile = File(),
                           negotiate: bool = Form(default=False)) -> matter_mod.Matter:
        matter = get_matter(matter_id)
        rounds_dir = ws / "matters" / "rounds" / matter_id
        rounds_dir.mkdir(parents=True, exist_ok=True)
        filename = Path(file.filename or "returned.txt").name
        target = rounds_dir / f"r{len(matter.rounds) + 1}-{filename}"
        target.write_bytes(await read_upload(file))
        try:
            matter, _, _ = matter_mod.ingest_round(ws, matter_id, str(target),
                                                   negotiate=negotiate)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None
        except Exception as e:
            raise HTTPException(422, f"could not ingest the round: {e}") from None
        return matter

    @app.post("/api/matters/{matter_id}/resolve")
    def resolve(matter_id: str, body: ResolveIn) -> matter_mod.Matter:
        get_matter(matter_id)  # 404 before 422
        try:
            return matter_mod.resolve_escalation(
                ws, matter_id, clause_id=body.clause_id, decision=body.decision,
                by=body.by, why=body.why, agreed_text=body.agreed_text)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.post("/api/matters/{matter_id}/close")
    def close(matter_id: str, body: CloseIn) -> matter_mod.Matter:
        get_matter(matter_id)
        try:
            return matter_mod.close_matter(ws, matter_id, status=body.status,
                                           by=body.by, why=body.why)
        except ValueError as e:
            raise HTTPException(422, str(e)) from None

    @app.get("/api/escalations")
    def escalations() -> list[EscalationOut]:
        pending = []
        for m in matter_mod.list_matters(ws):
            for e in m.escalations:
                if e.resolved:
                    continue
                ask = next((a for r in reversed(m.rounds) for a in r.asks
                            if a.clause_id == e.clause_id), None)
                pending.append(EscalationOut(
                    matter=m.id, counterparty=m.counterparty, doc_type=m.doc_type,
                    clause_id=e.clause_id, round=e.round, analysis=e.analysis,
                    proposed_text=e.proposed_text, requires=e.requires,
                    our_text=ask.our_text if ask else "",
                    their_text=ask.their_text if ask else "",
                    ask_kind=ask.kind if ask else ""))
        return pending

    @app.get("/api/exceptions", response_class=PlainTextResponse)
    def exception_register() -> str:
        from .exceptions import render_register
        return render_register(ws)

    @app.get("/api/drift", response_class=PlainTextResponse)
    def drift_report() -> str:
        from .drift import render_drift_report
        return render_drift_report(ws)

    @app.get("/intake/{name}", include_in_schema=False)
    def intake_page(name: str) -> FileResponse:
        template_path(name)  # 404 before serving the page
        return FileResponse(STATIC_DIR / "intake.html")

    @app.get("/api/intake/{name}")
    def intake_schema(name: str) -> IntakeSchemaOut:
        """The questionnaire ONLY — this endpoint faces the counterparty.

        The full /api/t/{name} payload (fallback variants, negotiation
        conditions, provenance, approver identities) is the firm's playbook
        in all but name and must never reach the other side of the table.
        """
        template, _ = load(name)
        return IntakeSchemaOut(
            doc_type=template.doc_type,
            variables=[VariableOut(**v.model_dump()) for v in template.variables])

    # -------------------------------------------------------------- export --

    @app.post("/api/export-docx")
    def export_docx(body: ExportIn) -> Response:
        from . import richtext
        doc = richtext.docx_document(
            body.title,
            [(f"{c.number}. {c.heading}",
              richtext.parse_html(c.html) if c.html else richtext.blocks_from_text(c.text))
             for c in body.clauses])
        buffer = io.BytesIO()
        doc.save(buffer)
        slug = skill_mod.slugify(body.title)
        return Response(
            content=buffer.getvalue(),
            media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            headers={"Content-Disposition": f'attachment; filename="{slug}.docx"'},
        )

    return app


def app_from_env() -> FastAPI:
    """Uvicorn factory for container deployments — configured via env:

        TB_WORKSPACE   workspace directory (default: ./examples)
        TB_READ_ONLY   any non-empty value serves the read-only public demo

    Run: uvicorn --factory template_builder.server:app_from_env
    """
    import os
    return create_app(os.environ.get("TB_WORKSPACE", "examples"),
                      read_only=bool(os.environ.get("TB_READ_ONLY")))
