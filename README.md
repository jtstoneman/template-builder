# template-builder

Feed in ~20 contracts of one type; get back a **template you can sign off on
once and reuse forever** — a questionnaire plus a clause graph that renders
finished contracts deterministically.

## The idea

The whole design collapses into one question: *what object does a lawyer's
sign-off attach to?* If the answer is "a prompt", the guarantee is fake —
runtime variance means the lawyer approved nothing. So here:

- **The template is a deterministic program.** `template.json` holds a
  variable schema (the questionnaire), a list of atomic clauses with
  alternative variants, and boolean conditions over the variables.
  Rendering is a pure function: `answers -> document`. **Zero LLM at render
  time.** Same template + same answers = the same document text, every time
  (byte-identical for markdown; `.docx` output has identical content but is a
  zip container whose internal timestamps vary).
- **The LLM sits strictly upstream of the approved boundary.** It is used
  once, at build time, to decompile the corpus of precedent contracts into
  the template (and optionally to pre-fill the questionnaire from a term
  sheet — structured answers only, never document text).
- **Approval attaches to content hashes.** Every clause has a hash over its
  approvable content. `tb approve` records those hashes; a later edit makes
  exactly the touched clauses show as *stale*, and only they need
  re-approval.
- **Edits are constrained graph operations**, never whole-template
  regeneration — approved clauses cannot drift silently. Every edit is
  validated *before* saving (write time, not output time), and reports its
  blast radius (which clauses cross-reference the edited one).

## Install

Requires Python 3.14+.

```sh
python3.14 -m venv .venv
.venv/bin/pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...   # your own key — only the LLM features
                                      # (build / intake / skill / negotiate) use it
```

**Bring your own keys.** Nothing in this repo ships or hosts an API key.
The LLM features read `ANTHROPIC_API_KEY` (and optionally `ISAACUS_API_KEY`
for extractive atomisation) from *your* environment; everything downstream
of the approval boundary — render, validate, hash, approve, edit, the
round-trip diff — runs with no key at all.

## The public demo, and hosting your own

The hosted instance is a **read-only showcase**: browse the example
templates, matters, escalation inbox and registers, generate documents and
download .docx — but nothing writes and nothing calls an LLM, so the server
holds no API key and no visitor can change (or spend money on) anything.
For real work, run it locally with your own key (above) — your contracts
never leave your machine.

Deploy your own demo anywhere that runs a Dockerfile:

```sh
docker build -t template-builder .
docker run -p 8000:8000 template-builder        # read-only demo of examples/
```

On **Render**: New → Blueprint → point it at this repo; `render.yaml`
provisions the free-tier service. The same image works on Fly.io, Railway or
Heroku.

### Running it at the firm (a production checklist)

A full-featured instance is the same image with three env vars flipped:

```sh
docker run -p 8000:8000 \
  -e TB_READ_ONLY= -e ANTHROPIC_API_KEY=sk-ant-... \
  -e TB_WORKSPACE=/data -v /srv/templates:/data \
  -e TB_AUTH='firm:choose-a-long-passphrase' \
  template-builder
```

- **Authentication** — `TB_AUTH="user:password"` requires HTTP Basic
  credentials on every request (the browser prompts natively; `curl -u`
  works). The only exception is the counterparty-facing intake surface:
  the `/intake/<template>` page, its questionnaire, and its submit
  endpoint — which is deliberately narrow (the server pins the template
  and the `intake` status, derives the matter reference itself, and
  rejects answers that don't belong to the questionnaire). Without
  `TB_AUTH` the app trusts the network, which is only acceptable on
  localhost.
- **TLS** — Basic auth sends credentials with every request, so any
  non-localhost deployment must sit behind a TLS reverse proxy (Caddy is
  two lines; nginx works too). The container passes `--proxy-headers`.
- **One process** — build jobs and the duplicate-name guard are held
  in memory, so run a single uvicorn process per workspace (the default).
  Scale by giving each practice group its own workspace and container.
- **Backups** — the entire state is plain files in the workspace
  (templates, journals, matters, skills). Snapshot the directory;
  restoring it is copying it back.
- **Headers** — every response carries a content-security policy,
  `nosniff`, and frame-denial headers out of the box; there is nothing
  to configure.

## Workflow

```sh
# 1. Build: decompile your contracts into a template (LLM; a few minutes)
tb build contracts/*.docx --doc-type "Mutual Non-Disclosure Agreement" -o nda.json

# 2. Read the build report: the canonical outline, the questionnaire, and a
#    diagnosis of the source documents (orphan cross-references, terms
#    defined but unused, inconsistencies the model flagged).
open nda.json.report.md

# 3. Iterate with constrained edits — validated before saving
tb edit replace-text nda.json obligations mutual --file better_obligations.txt
tb edit set-condition nda.json non-solicit --when include_non_solicit
tb edit remove-clause nda.json some-clause    # refused if others reference it

# 4. Check the deterministic gates any time
tb validate nda.json

# 5. Lawyer reviews, then signs off on the current content hashes
tb approve nda.json --by "jane@firm.com"
tb status  nda.json          # approved / stale / unapproved, per clause

# 6. Generate contracts — pure assembly, no LLM
tb questions nda.json -o answers.json    # emits the questionnaire skeleton
$EDITOR answers.json                     # ...or pre-fill it from a term sheet:
tb intake nda.json termsheet.txt -o answers.json
tb render nda.json -a answers.json -o acme_nda.docx
```

`tb render` refuses (without `--allow-unapproved`) when any included clause —
or the questionnaire itself — lacks a current approval.

## The web UI

```sh
tb serve templates/     # a workspace directory of template .json files
                        # → http://127.0.0.1:8756
```

A single-page, no-build frontend over the same modules the CLI uses:

- **Home** — every template in the workspace, plus **New template from
  precedent contracts**: upload your 20–30 agreed documents (.txt/.md/.docx/
  .pdf), optionally attach a **deal context** note to each one
  ("seller-friendly", "W&I insurance", "distressed sale, tight timetable"),
  and build. A background job streams the decompile → align → synthesise
  progress; the diagnosis report opens when it finishes. The context notes
  are how the system infers *why* drafting differs between sources — where a
  difference correlates with context, that dimension becomes a questionnaire
  variable conditioning the affected clauses (e.g. a clause present only in
  the W&I deals gets `include_when has_wi_insurance`), with the inferred
  correlation stated in the report for a lawyer to confirm or reject.
  Uploaded sources are kept under `<workspace>/sources/<template>/` so
  provenance keeps pointing at real files. (CLI equivalent:
  `tb build ... --contexts contexts.json`.)
- **Template view** — every clause with its variants, conditions, per-clause
  approval chips (approved / stale / unapproved), and node-level provenance.
  Editing a variant goes through the same constrained, validated-before-save
  edit API as `tb edit`; refused edits show exactly which validation errors
  they would introduce, and saved edits report their blast radius.
- **Questionnaire** — a form generated from the variable schema, with
  optional term-sheet pre-fill (the LLM intake; answers only, never text).
- **Document view** — the deterministic render as an editable page: click
  any clause and type to make bespoke, per-deal manual changes, with a small
  formatting toolbar (**bold**, *italic*, underline, bullet and numbered
  lists — the docx essentials, and deliberately nothing more), then
  **Download .docx**: formatting exports as real Word runs and list styles,
  via a server-side whitelist that strips everything else contenteditable
  or pasting might produce. Hand-edited clauses — including formatting-only
  changes — are flagged *"outside the template's approval — route to
  review"*: the UI never pretends the template's sign-off covers text a
  human just changed.
- **Escalation inbox** — the home page leads with every clause across every
  matter that is waiting on a human, each with the assistant's full analysis
  attached and two-click resolution (**accept theirs** records a deviation;
  **hold our standard** records the refusal). Both decisions demand a *who*
  and a *why*, are journaled with a disposition, and therefore raise the
  clause's maturity — resolving the inbox *is* training the system.
- **Matters** — every live negotiation with its round count, deviations and
  pending escalations; **Ingest round…** uploads the counterparty's returned
  document straight from the list. **Exception register** and **Drift
  report** buttons render the cross-matter and cross-template views below.
- **Intake links** — each template card has a copyable client-facing
  questionnaire URL (`/intake/<template>`); a submission opens a matter in
  `intake` status with the template hash pinned, no lawyer involved. The
  page is served the questionnaire ONLY (`/api/intake/<name>`): the firm's
  fallback variants, conditions, provenance and approver identities never
  reach the other side of the table.

## The learning loop — from lawyer to skill file

The build and edit processes *learn*. Three artifacts, all auditable:

1. **The journal** (`<template>.journal.jsonl`, append-only). Every edit is
   journaled through the constrained edit API — who, what, and crucially
   **why** (`tb edit ... --why "..." --counterparty X --disposition conceded`;
   the web UI's editor has a "why" field). A lawyer's first negotiations are
   not overhead — they are the training data, recorded as they happen.
2. **The skill** (`skills/<doc-type>/SKILL.md`, a real Claude skill file).
   `tb skill update` distils the journal into a per-clause playbook —
   position, fallback ladder, red lines — where **every play must cite
   journal entries** (`[j:12]`); uncited plays are dropped in code, so the
   playbook can never contain a position no human ever took. The playbook is
   diffable markdown a partner reviews like any document, and it feeds
   forward: `tb build` synthesises new templates of that doc type from the
   firm's learned positions.
3. **The negotiator** (`tb negotiate <template> <counterparty-markup>`).
   Reads the markup, the playbook, the dependency map, and each clause's
   **maturity** — the count of journaled human decisions with rationale.
   The autonomy gate is deterministic, per-clause, and conservative:

   | condition | outcome |
   |---|---|
   | no playbook play for the clause (exact id match) | escalate to the lawyer |
   | maturity < threshold (default **10**) | escalate to the lawyer |
   | no replay evidence, or agreement < 70% on ≥3 replays | escalate — maturity alone is not evidence |
   | ask touches a red line | reject or escalate — never conceded silently |
   | response names a clause that doesn't exist | escalate — model errors reach a human, never the bin |
   | otherwise | accept / counter with drafted text, citing `[j:N]` precedents |

   Every response is stamped with its **decider** from the playbook's
   delegation matrix (frontmatter: `delegation_red_line: partner`,
   `delegation_immature: lawyer`, `delegation_mature: assistant`) — and the
   decider is *enforced*: the assistant auto-applies or auto-records a
   gate-passing decision only when the matrix assigns that tier to the
   assistant itself; anything else becomes an escalation to the named tier.
   A partner's hand-tuned frontmatter (delegation rungs, autonomy_threshold)
   survives re-distillation.

   **Moot-court replay** (`tb skill replay`) is the evidence for autonomy:
   it re-runs journaled human decisions against the current playbook —
   situation in, predicted disposition out — and scores agreement per
   clause. Only decisions the distiller never saw are replayed (the playbook
   stamps `distilled_through`), so the score measures judgment, not memory;
   re-distilling deletes the old scores, and the gate escalates mature
   clauses until new evidence is earned.

   The trust boundary, stated honestly: play existence, maturity, replay and
   delegation are enforced in code; whether an ask *touches a red line* is
   the model's self-report, which the gate can act on but not verify.

   Escalations arrive with the full drafted analysis attached, so the lawyer
   decides rather than drafts — and that decision, journaled, is what raises
   maturity. With `--apply`, gate-passing counters are applied through the
   same validated edit API and journaled as `actor: assistant` (assistant
   actions never count toward maturity, and human sign-off of the template
   is still required). Full automation is not a switch: it is every clause
   independently earning its way past the threshold until nothing escalates.

## Running matters — the deal-by-deal layer

A **matter** is one live negotiation: template hash and questionnaire
answers pinned at open, then rounds, escalations and agreed deviations
accumulate against it. Matter files live in `<workspace>/matters/` and every
event is journaled to the template — the learning loop and the deal work are
the same record.

```sh
tb matter open nda-acme-2026 --template nda -a answers.json --counterparty "Acme"
tb matter round nda-acme-2026 their_redline.docx --negotiate
tb matter resolve nda-acme-2026 term-survival accept-theirs \
    --by jane@firm.com --why "seven-year tail acceptable here"
tb matter list          # every matter, status, pending escalations
tb matter close nda-acme-2026 --status agreed --by jane@firm.com
                        # 'agreed' requires an empty escalation inbox
tb exceptions           # the exception register (also GET /api/exceptions)
tb drift                # cross-template drift report (also GET /api/drift)
```

The **round-trip diff** (`tb matter round`) is deterministic: because we
know exactly what we sent (template@hash + answers), the returned document
is anchored back to clauses by its numbered headings and diffed against the
ground-truth render. After normalising whitespace/smart-quote noise, *any*
difference is an ask — a one-digit change to a term is precisely the redline
that matters, so nothing is dismissed as close enough. A clause whose
heading vanished is a `delete` ask; text that can't be anchored goes to an
explicit review bucket, and if too few headings anchor (they retyped the
document) the diff refuses to guess entirely. No LLM touches this path;
`--negotiate` then feeds the asks to the gated negotiator above.

The **exception register** rolls every matter's agreed deviations up by
clause — each row with counterparty, round, authority and rationale. A
clause that appears repeatedly means the standard is wrong (fix the template
through the gated pipeline) or the playbook needs a fallback rung. The
**drift report** compares same-id clauses across the workspace's templates
and flags divergence, using journal recency to suggest which side is the
improved wording; `tb status` also warns when a sign-off certificate is
older than a year.

## What the validator proves (and what it doesn't)

`tb validate` runs two layers of deterministic gates:

1. **Static checks** on the clause graph: cross-reference integrity, orphan
   `{{ref:...}}` targets, condition syntax, unknown/unused variables,
   duplicate ids, missing default variants, leftover `[drafting brackets]`.
2. **A configuration sweep**: it actually renders the template under every
   combination of boolean/choice answers (exhaustive up to 256
   configurations, deterministic sampling above that), checking that every
   configuration renders, every cross-reference points at an included
   clause, and every defined term used has its definition included.

Honesty about coverage: structural invariants are proven across the swept
space; the *legal semantics* of any one rendered document are only sampled.
That's why `tb approve` writes a **coverage certificate** into the template —
"approved once" means "approved generator + this certificate", and the
certificate says whether the sweep was exhaustive or sampled. A template
with 20 booleans has ~10⁶ configurations; nobody read 10⁶ renders, and this
tool never pretends otherwise.

## The template format

`template.json` is meant to be read by humans:

```jsonc
{
  "doc_type": "Mutual Non-Disclosure Agreement",
  "variables": [   // the questionnaire
    {"name": "is_mutual", "type": "boolean", "question": "Do both parties disclose?"}
  ],
  "clauses": [
    {
      "id": "obligations",
      "heading": "Confidentiality Obligations",
      "include_when": null,              // condition, or null = always included
      "defines": [],                     // defined terms this clause introduces
      "variants": [                      // first matching variant wins
        {"id": "mutual",  "when": "is_mutual", "text": "Each party shall ... {{ref:definitions}}.",
         "provenance": ["nda_03.docx"]}, // node-level provenance: where the language came from
        {"id": "one-way", "when": null,  "text": "The receiving party shall ...",
         "provenance": ["nda_11.docx"]}  // when: null = the default variant
      ]
    }
  ],
  "approvals": [ /* per-clause {clause_id, hash, by, date} */ ],
  "certificate": { /* who approved, when, and with what sweep coverage */ }
}
```

Placeholders in clause text: `{{variable_name}}` (answer substitution) and
`{{ref:clause-id}}` (rendered as "clause 4", renumbered automatically as
clauses are included/excluded). Conditions are a deliberately tiny language —
bare boolean names, `==`/`!=`/`<`/`>`/`in`, `and`/`or`/`not`, evaluated by an
AST whitelist, never `eval()`.

## Semantic dependencies — consequential changes

In complex contracts, changes are consequential: an uncapped indemnity may
have been accepted *because* the aggregate limitation of liability governs
it. `{{ref:...}}` only tracks numbering; the **dependency map** tracks the
negotiated logic:

```jsonc
"dependencies": [
  {"from_clause": "indemnity", "to_clause": "limitation-of-liability",
   "kind": "subject-to",
   "note": "the uncapped IP indemnity was accepted because the aggregate cap still governs it"}
]
```

Three kinds, deliberately few: **subject-to** (the target takes precedence
over the source's drafting), **relies-on** (the source assumes the target's
machinery), **trade-off** (a negotiated package — both sides warned). Every
edge carries a mandatory `note` saying *why*, shown verbatim to future
editors — that note is the audit record.

The map is inferred at build time (one whole-graph pass, every inference
written into the build report for a lawyer to confirm), then owned by humans
via `tb edit add-dependency / remove-dependency`. It is enforced, not
decorative:

- **Edit-time**: editing a depended-on clause flags its dependents in the
  blast radius, with each edge's note ("review `residuals` — subject-to
  no-license: …").
- **Render-time**: the validation sweep refuses any configuration that
  includes a clause while excluding a clause it is subject-to or relies on
  (and either side of a broken trade-off) — a document can never omit the
  cap an indemnity was priced against.
- **Sign-off**: the map is covered by the certificate's `template_hash`, so
  rewiring dependencies invalidates approval like reordering clauses does.
- Removing a clause is refused while edges record that others assume it.

## Layout

```
template_builder/
  deterministic core — never calls an LLM:
    fsio.py        crash-safe primitives: atomic writes (fsync+rename), file locks
    model.py       Pydantic models for the template file, content hashing
    conditions.py  the condition language + safe AST-whitelist evaluator (cached)
    render.py      pure rendering: template + answers -> document
    richtext.py    the bounded rich-text model; THE docx emitter
    validate.py    static gates (_StaticChecker) + configuration sweep
    approve.py     hash-based sign-off + status
    edit.py        constrained edit operations + semantic blast radius
    ops.py         THE write gate: load -> operate -> validate -> save -> journal
    journal.py     append-only learning record beside each template
    report.py      the build report a lawyer reads first
    ingest.py      .txt/.md/.docx/.pdf -> text
    matter.py      one negotiation: pinned template+answers, rounds, deviations
    roundtrip.py   returned document -> clause-anchored asks (pure diff)
    exceptions.py  the cross-matter exception register
    drift.py       same-id clauses diverging across templates
  LLM boundary (all typed via messages.parse -> Pydantic):
    llm.py         the ONLY module that talks to Claude
    decompile.py   atomise one contract (Isaacus extraction, LLM fallback)
    merge.py       the build pipeline: outline -> plan -> synthesise -> dependencies
    intake.py      term sheet -> questionnaire answers
    skill.py       journal -> SKILL.md playbook (citation-checked) + delegation
    negotiate.py   counterparty markup -> gated proposals
    replay.py      moot-court: replay past decisions against the playbook
  surfaces:
    cli.py         the `tb` command
    server.py      FastAPI backend for the web UI
    static/        the single-file frontend (+ the client intake page)
examples/          a synthetic NDA corpus to try the pipeline on
tests/             263 tests, all offline (LLM calls are stubbed)
```

Three invariants hold everywhere: every template mutation goes through
`ops.gated_edit` (one write gate — CLI, web UI and the negotiation assistant
share identical validate-before-save semantics and journaling); every .docx
is emitted by `richtext.docx_document` (one formatter); and every piece of
persisted state (templates, matters, journals, skills) is written through
`fsio` — atomic renames so a crash never tears a file, advisory locks so the
web server and CLI never lose each other's writes.

### How a build works

1. **Atomise** each contract into clauses. With `ISAACUS_API_KEY` set, this
   uses the **Isaacus Kanon 2 Enricher** — extractive, span-anchored legal
   segmentation, so clause text is verbatim *by construction*; defined terms
   (with definitions and mentions) come out deterministically, and
   header/footer junk is stripped. Without a key (or on failure) it falls
   back to a Claude call per contract that is *asked* to preserve wording.
   Override with `TB_ATOMISER=auto|isaacus|llm`.
2. **Align** clauses across the corpus into one canonical outline.
3. **Plan the questionnaire** — one call designs the whole variable schema
   from the outline, the source preambles, and the deal contexts. Planning
   first keeps naming consistent across clauses (one `party_a_name`, not
   three spellings) and makes the next step parallelisable.
4. **Synthesise** each canonical clause against that frozen schema — in
   parallel — merging variants, inferring conditions from deal-context
   correlations, and writing notes for the reviewing lawyer. Variables the
   plan proposed but nothing used are pruned deterministically.

Every boundary is typed and validated: the template file is parsed by
Pydantic models on load, and every LLM call goes through
`client.messages.parse` with a Pydantic output model — the API constrains
generation to that schema and the SDK validates the response, so the
pipeline never handles raw JSON. Term-sheet intake builds its answer model
dynamically from the questionnaire (choice variables become `Literal`
types), making out-of-schema answers unrepresentable.

## Deliberate limitations (v1)

- Flat clause list — no nested sub-clause numbering; (a)(b)(c) enumerations
  live inside clause text.
- `.docx` ingestion reads paragraph text only (tables, headers/footers
  skipped); `.docx` output is plain heading + paragraphs, no house style.
- Defined-term detection relies on the build-time LLM plus exact-string
  matching at validation time; a term used but never defined *anywhere* in
  the corpus is flagged in the build report, not by the validator.
- Approvals don't expire on their own. Templates rot — statutes change under
  them — so pair this with a periodic review; the certificate records the
  date for exactly that reason.
- Importing negotiated language from a client matter into a firm-wide
  template has confidentiality implications. That's why provenance is
  tracked per variant — review it before sharing a template beyond the
  matters it was built from.
