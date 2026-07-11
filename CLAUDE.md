# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

`template-builder` decompiles a corpus of precedent contracts (via LLM, at build time only) into a deterministic template: a questionnaire + clause graph that renders finished contracts with **zero LLM at render time**. Lawyer sign-off attaches to content hashes, so the approval guarantee is real. The README explains the product design in depth — read it before making architectural changes.

## Commands

```sh
# Setup (Python 3.14+ required; PEP 695 generics are used)
python3.14 -m venv .venv && .venv/bin/pip install -e ".[dev]"

# Tests — entire suite is offline (no network, no API keys), ~268 tests
.venv/bin/python -m pytest tests
.venv/bin/python -m pytest tests/test_server.py::test_render_happy_path   # single test
.venv/bin/python -m pytest tests -k "build_template" -q                   # by keyword

# Run the web UI (workspace = directory of template .json files)
.venv/bin/tb serve examples --port 8756        # http://127.0.0.1:8756

# CLI (entry point: tb = template_builder.cli:main)
tb build / edit / validate / approve / status / questions / intake / render
tb matter open|round|resolve|list|close / tb negotiate / tb skill update|replay

# Read-only demo container (what Render runs; no API key needed)
docker build -t template-builder . && docker run -p 8000:8000 template-builder
```

There is no linter or formatter configured.

## Architecture

Three layers inside `template_builder/` (the README's Layout section maps every module):

1. **Deterministic core** — `model`, `conditions`, `render`, `richtext`, `validate`, `approve`, `edit`, `ops`, `journal`, `fsio`, `matter`, `roundtrip`, `exceptions`, `drift`, `ingest`, `report`. Never calls an LLM. Rendering is a pure function `answers -> document`.
2. **LLM boundary** — `llm.py` is the **only** module that imports `anthropic`. Everything upstream of approval (`decompile`, `merge`, `intake`, `skill`, `negotiate`, `replay`) calls `llm.complete(system, prompt, OutputModel)`, which uses `client.messages.parse` with a Pydantic output model — the pipeline never handles raw JSON. Keep new LLM calls behind `llm.complete`; never add one to render/validate/approve/hash paths.
3. **Surfaces** — `cli.py` (plain argparse; subcommands are `cmd_*(args) -> int` registered via `p.set_defaults(func=...)`; heavy imports done lazily inside commands; domain errors must subclass `ValueError` to be caught by `main()`), `server.py` (FastAPI, all routes inside the `create_app(workspace)` factory), `static/` (two hand-written single-file HTML pages, no build step, no frameworks).

### The three write invariants

- **Every template mutation goes through `ops.gated_edit`** (CLI, web UI, and negotiator `--apply` all use it): load → snapshot validation findings → apply operation → re-validate → save only if no *new* errors → journal. Refused edits touch neither file nor journal. Deliberate exceptions that call `model.save` directly: initial build, and `approve` (which mutates only approvals/certificate metadata, never clause content).
- **Every output .docx goes through `richtext.docx_document`** — the single python-docx emitter (`render.render_docx` and the server's `/api/export-docx` both route through it).
- **Every piece of persisted state** (templates, matters, journals, skills) is written via `fsio.atomic_write_text` / under `fsio.locked`. Plain `open(...,'w')` is acceptable only for user-directed exports to paths the user chose (reports, rendered markdown, answer skeletons).

### Hashing and approval

`model.py` hashes canonical JSON (`sort_keys`, compact separators). `clause_hash` covers id, heading, condition, defines, and variant `{id, when, text}` — **provenance is deliberately excluded** (safe to edit without staleness); renaming a clause id invalidates its approval. `schema_hash` covers the questionnaire (changing question wording invalidates schema approval, sentinel id `__schema__`). `template_hash` covers schema + ordered clause hashes + dependencies, with the `dependencies` key **omitted when empty** so pre-feature templates keep their hashes — follow that omit-when-empty pattern when extending any hash payload, because adding a field silently invalidates every existing certificate. `approve()` preserves the original approver/date for unchanged clauses; the audit trail is never rewritten.

### Journal and learning loop

`<template>.json.journal.jsonl` is append-only; entry ids are sequential and **never rewritten** (skill playbook citations `[j:N]` depend on it). `maturity()` counts only *human* decisions with rationale — `actor: assistant` entries never raise maturity. The negotiation autonomy gate (playbook play existence, maturity threshold, replay agreement, delegation matrix) is enforced in code; keep it deterministic.

### Server modes

`create_app(workspace, read_only=..., auth=...)`; the container entry point is `uvicorn --factory template_builder.server:app_from_env`, configured by `TB_WORKSPACE` / `TB_READ_ONLY` / `TB_AUTH` env vars. Read-only mode is an HTTP middleware that 403s every non-GET **except** an allowlist of pure-compute paths (`/api/export-docx`, paths ending `/validate` or `/render`). Two consequences when adding endpoints: a new POST endpoint is demo-blocked by default unless added to `_compute_only`, and a mutating endpoint must never be named with a `/render` or `/validate` suffix. Auth mode (`TB_AUTH="user:password"`, honoured by both `app_from_env` and `tb serve`) is HTTP Basic via middleware with a **counterparty exemption**: `/api/config` plus every path under `/intake/` and `/api/intake/` stays open — never mount a firm-facing endpoint under those prefixes. `GET /api/intake/{name}` serves the questionnaire **only** — fallback variants, conditions, provenance, and approver identities are the firm's confidential playbook and must never reach the counterparty-facing intake page. `POST /api/intake/{name}/submit` is the counterparty's **only** write: it pins the template and the `intake` status, derives the matter id server-side, and rejects answers outside the questionnaire — the broad `POST /api/matters` is firm-only. Build jobs and the `building` set are in-memory: run the server as a single process.

### Environment variables

`ANTHROPIC_API_KEY` (LLM features only), `ISAACUS_API_KEY` (extractive atomiser), `TB_MODEL` (default `claude-opus-4-8`), `TB_ATOMISER` (`auto|isaacus|llm`), `TB_WORKSPACE`, `TB_READ_ONLY`, `TB_AUTH` (`user:password` → HTTP Basic on everything except the intake surface), `TB_MATURITY_THRESHOLD`. Nothing downstream of the approval boundary needs any key.

## Test conventions

- The suite is fully offline: LLM/Isaacus calls are stubbed with plain `monkeypatch.setattr` — there is no mocking library and no shared mock fixture.
- **Patch at the consuming-module seam, not `llm.complete`**: pipeline tests patch `template_builder.merge.atomise/build_outline/plan_variables/synthesise_clause/map_dependencies` (see `stubbed_pipeline` in `tests/test_generator.py`); server-level tests patch `template_builder.merge.build_template` wholesale (see `build_client` in `tests/test_server.py`).
- Shared fixtures in `tests/conftest.py`: `template_dict` (deep copy — mutate freely), `template`, `answers`. The module constants `TEMPLATE_DICT`/`ANSWERS` are imported directly by some tests — copy before mutating.
- Stubbed pipeline outputs must be internally consistent (variables referenced by variants exist, `{{ref:...}}` targets resolve) or `build_template`'s validation gates reject them.
- Atomiser fallback latches: after an Isaacus auth failure, `decompile._extraction_disabled` stays `True` process-wide — tests touching it must reset it via `monkeypatch.setattr`.
- Approval counts include the schema sentinel: a fresh template has `len(clauses) + 1` unapproved items (`__schema__` is one of them).

## Traps

- `fsio.locked` is **not re-entrant** — take locks at entry points only. `gated_edit` already holds the template lock, so operation callables passed to it must never lock the template path themselves.
- `gated_edit` mutates the loaded `Template` in place; on a refused edit, `GatedOutcome.template` is the mutated *unsaved* object — reload from disk if you need real state (server.py does).
- `validate.finding_key` and finding message stability are load-bearing: `gated_edit` compares before/after findings by key, so rewording a validation message can break write-gate semantics (comments in `validate.py` mark this).
- Pydantic models use `extra="forbid"`; unknown template keys fail loudly at load. `model.template_file()` is the single place workspace paths are built and enforces `SAFE_NAME_RE` against traversal — don't construct `<workspace>/<name>.json` paths by hand.
- In `llm.py`, `_get_client()` must be called before `import anthropic` in any new path so a missing package raises `LLMError`, not `ImportError`; both API clients are thread-locked module singletons because `merge` fans out over a `ThreadPoolExecutor`.
- Untrusted document text embedded in prompts is wrapped in `<document>` tags with `</document>` escaped (see `decompile.atomise_llm`) — reuse that pattern for any new call that embeds user-supplied files.
- The condition language is evaluated by an AST whitelist — never `eval()`.
- `.claude/` is gitignored because `launch.json` holds a real API key; never commit anything under it.
