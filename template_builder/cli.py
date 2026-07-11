"""The `tb` command line.

    tb build      contracts -> template.json + build report   (LLM)
    tb validate   deterministic gates
    tb questions  emit the questionnaire + an answers skeleton
    tb intake     pre-fill answers from a term sheet           (LLM)
    tb render     template + answers -> .md / .docx            (no LLM, ever)
    tb approve    sign off on current content hashes
    tb status     which clauses are approved / stale / unapproved
    tb edit       constrained edits with write-time validation
    tb serve      the web UI (template workbench + document editor)
"""
import argparse
import json
import sys

from . import approve as approve_mod
from . import edit as edit_mod
from . import model
from . import render as render_mod
from . import validate as validate_mod
from .llm import LLMError
from .model import SCHEMA_APPROVAL_ID


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return 1
    try:
        return args.func(args) or 0
    # ValueError covers every domain error (TemplateError, EditError,
    # ConditionError, IngestError, RenderError, JSONDecodeError, ...) — no
    # user input should ever produce a traceback.
    except (ValueError, KeyError, LLMError, OSError) as e:
        print(f"error: {_unquote(e)}", file=sys.stderr)
        return 1


def _unquote(e):
    # KeyError wraps its message in quotes; unwrap for readability.
    return e.args[0] if isinstance(e, KeyError) and e.args else str(e)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tb", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter,
                                     suggest_on_error=True)  # 3.14: typo suggestions
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("build", help="build a template from a corpus of contracts (uses the Claude API)")
    p.add_argument("files", nargs="+", help="contract files (.txt/.md/.docx/.pdf)")
    p.add_argument("--doc-type", required=True, help='e.g. "Share Purchase Agreement"')
    p.add_argument("-o", "--output", required=True, help="where to write template.json")
    p.add_argument("--report", help="where to write the build report (default: <output>.report.md)")
    p.add_argument("--contexts", help="JSON file mapping filename -> deal context note, e.g. "
                                      '{"spa_04.docx": "seller-friendly; W&I insurance"} — '
                                      "used to infer clause conditions")
    p.set_defaults(func=cmd_build)

    p = sub.add_parser("validate", help="run the deterministic validation gates")
    p.add_argument("template")
    p.set_defaults(func=cmd_validate)

    p = sub.add_parser("questions", help="print the questionnaire; optionally write an answers skeleton")
    p.add_argument("template")
    p.add_argument("-o", "--output", help="write an answers-file skeleton (JSON with nulls)")
    p.set_defaults(func=cmd_questions)

    p = sub.add_parser("intake", help="pre-fill answers from a term sheet (uses the Claude API)")
    p.add_argument("template")
    p.add_argument("term_sheet", help="term sheet file (.txt/.md/.docx/.pdf)")
    p.add_argument("-o", "--output", required=True, help="where to write answers.json")
    p.set_defaults(func=cmd_intake)

    p = sub.add_parser("render", help="render a document from a template + answers (no LLM)")
    p.add_argument("template")
    p.add_argument("-a", "--answers", required=True, help="answers JSON file")
    p.add_argument("-o", "--output", required=True, help="output .md or .docx path")
    p.add_argument("--title", help="document title (default: the template's doc_type)")
    p.add_argument("--allow-unapproved", action="store_true",
                   help="render even if some included clauses are not approved")
    p.set_defaults(func=cmd_render)

    p = sub.add_parser("approve", help="validate, then sign off on every clause's current hash")
    p.add_argument("template")
    p.add_argument("--by", required=True, help="who is approving (name/email)")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("status", help="per-clause approval status")
    p.add_argument("template")
    p.set_defaults(func=cmd_status)

    skill = sub.add_parser("skill", help="the learned playbook: distil the journal into SKILL.md")
    ssub = skill.add_subparsers(dest="skill_command")
    skill.set_defaults(func=lambda args, _p=skill: (_p.print_help(), 1)[1])
    sp = ssub.add_parser("update", help="distil journaled decisions into the doc type's playbook "
                                        "(uses the Claude API)")
    sp.add_argument("template")
    sp.set_defaults(func=cmd_skill_update)
    sp = ssub.add_parser("show", help="print the doc type's playbook")
    sp.add_argument("template")
    sp.set_defaults(func=cmd_skill_show)
    sp = ssub.add_parser("replay", help="moot-court replay: score the assistant's predictions "
                                        "against the lawyers' journaled decisions (Claude API)")
    sp.add_argument("template")
    sp.set_defaults(func=cmd_skill_replay)

    p = sub.add_parser("negotiate", help="draft responses to a counterparty markup under the "
                                         "playbook + maturity gate (uses the Claude API)")
    p.add_argument("template")
    p.add_argument("markup", help="the counterparty's markup or asks (.txt/.md/.docx/.pdf)")
    p.add_argument("-o", "--output", help="write the proposals report here (default: stdout)")
    p.add_argument("--apply", action="store_true",
                   help="apply mature, gate-passing counters to the template (journaled as "
                        "actor=assistant); escalations always remain the lawyer's")
    p.add_argument("--matter", help="deal/file reference for the journal")
    p.add_argument("--counterparty", help="counterparty name for the journal")
    p.set_defaults(func=cmd_negotiate)

    m = sub.add_parser("matter", help="live deals: open, ingest counterparty rounds, resolve")
    m.set_defaults(func=lambda args, _p=m: (_p.print_help(), 1)[1])
    msub = m.add_subparsers(dest="matter_command")

    def matter_parser(name, help_text):
        mp = msub.add_parser(name, help=help_text)
        mp.add_argument("-w", "--workspace", default=".",
                        help="workspace directory (default: current directory)")
        return mp

    mp = matter_parser("open", "open a matter: pin the template hash + answers for one deal")
    mp.add_argument("id", help="matter id, e.g. nda-halloway-2026")
    mp.add_argument("--template", required=True, help="template name in the workspace")
    mp.add_argument("-a", "--answers", required=True, help="answers JSON file")
    mp.add_argument("--counterparty", required=True)
    mp.set_defaults(func=cmd_matter_open)

    mp = matter_parser("list", "list matters with status and pending escalations")
    mp.set_defaults(func=cmd_matter_list)

    mp = matter_parser("show", "one matter's rounds, deviations and escalations")
    mp.add_argument("id")
    mp.set_defaults(func=cmd_matter_show)

    mp = matter_parser("round", "ingest the counterparty's returned document as a "
                                "clause-anchored diff (optionally negotiate it)")
    mp.add_argument("id")
    mp.add_argument("file", help="the returned document (.docx/.txt/.md/.pdf)")
    mp.add_argument("--negotiate", action="store_true",
                    help="run the playbook-gated negotiator over the asks (Claude API)")
    mp.add_argument("-o", "--output", help="write the round report here")
    mp.set_defaults(func=cmd_matter_round)

    mp = matter_parser("resolve", "record the human decision on an escalated clause")
    mp.add_argument("id")
    mp.add_argument("clause_id")
    group = mp.add_mutually_exclusive_group(required=True)
    group.add_argument("--accept-theirs", action="store_true",
                       help="agree the counterparty's text from the latest round")
    group.add_argument("--file", help="file containing the agreed text")
    group.add_argument("--hold", action="store_true",
                       help="hold our standard text (rejecting the ask)")
    mp.add_argument("--by", required=True, help="who decided")
    mp.add_argument("--why", required=True,
                    help="rationale (journaled; raises this clause's maturity)")
    mp.set_defaults(func=cmd_matter_resolve)

    mp = matter_parser("close", "close a matter as agreed or abandoned")
    mp.add_argument("id")
    mp.add_argument("--status", required=True, choices=("agreed", "abandoned"))
    mp.add_argument("--by", required=True, help="who decided")
    mp.add_argument("--why", help="optional rationale (journaled)")
    mp.set_defaults(func=cmd_matter_close)

    p = sub.add_parser("exceptions", help="the exception register: every agreed deviation "
                                          "from standard, across all matters")
    p.add_argument("-w", "--workspace", default=".")
    p.add_argument("-o", "--output", help="write the register here (default: stdout)")
    p.set_defaults(func=cmd_exceptions)

    p = sub.add_parser("drift", help="find same-purpose clauses that have drifted apart "
                                     "across the workspace's templates")
    p.add_argument("-w", "--workspace", default=".")
    p.set_defaults(func=cmd_drift)

    p = sub.add_parser("serve", help="serve the web UI for a workspace of templates")
    p.add_argument("workspace", nargs="?", default=".",
                   help="directory of template .json files, or one template file "
                        "(default: current directory)")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8756)
    p.set_defaults(func=cmd_serve)

    edit = sub.add_parser("edit", help="constrained template edits (validated before saving)")
    edit.set_defaults(func=lambda args, _parser=edit: (_parser.print_help(), 1)[1])
    esub = edit.add_subparsers(dest="edit_command")

    def edit_parser(name, help_text):
        ep = esub.add_parser(name, help=help_text)
        ep.add_argument("template")
        ep.add_argument("--force", action="store_true",
                        help="save even if validation reports new errors")
        # the learning signal: every edit is journaled; --why is what the
        # skill distiller learns from
        ep.add_argument("--why", help="rationale for this edit (journaled; feeds `tb skill`)")
        ep.add_argument("--matter", help="deal/file reference for the journal")
        ep.add_argument("--counterparty", help="who this change was negotiated against")
        ep.add_argument("--disposition", choices=["accepted", "countered", "rejected", "conceded"],
                        help="how the negotiation point resolved")
        ep.add_argument("--actor", help="who decided (default: current user)")
        return ep

    ep = edit_parser("replace-text", "replace one variant's text")
    ep.add_argument("clause_id")
    ep.add_argument("variant_id")
    ep.add_argument("--file", required=True, help="file containing the new text")
    ep.set_defaults(func=cmd_edit_replace_text)

    ep = edit_parser("set-condition", "set or clear a clause's include_when condition")
    ep.add_argument("clause_id")
    group = ep.add_mutually_exclusive_group(required=True)
    group.add_argument("--when", help='e.g. \'include_non_solicit\' or \'governing_law == "New York"\'')
    group.add_argument("--always", action="store_true", help="clear the condition (always include)")
    ep.set_defaults(func=cmd_edit_set_condition)

    ep = edit_parser("add-variant", "add an alternative drafting of a clause")
    ep.add_argument("clause_id")
    ep.add_argument("variant_id")
    ep.add_argument("--file", required=True, help="file containing the variant text")
    ep.add_argument("--when", help="condition selecting this variant (omit for the default variant)")
    ep.set_defaults(func=cmd_edit_add_variant)

    ep = edit_parser("remove-variant", "remove one variant of a clause")
    ep.add_argument("clause_id")
    ep.add_argument("variant_id")
    ep.set_defaults(func=cmd_edit_remove_variant)

    ep = edit_parser("add-clause", "add a new clause (a single default variant)")
    ep.add_argument("clause_id")
    ep.add_argument("--heading", required=True)
    ep.add_argument("--file", required=True, help="file containing the clause text")
    ep.add_argument("--after", help="insert after this clause id (default: at the end)")
    ep.add_argument("--when", help="include_when condition (omit to always include)")
    ep.set_defaults(func=cmd_edit_add_clause)

    ep = edit_parser("remove-clause", "remove a clause (refused if others cross-reference it)")
    ep.add_argument("clause_id")
    ep.set_defaults(func=cmd_edit_remove_clause)

    ep = edit_parser("add-dependency", "record that one clause's drafting assumes another")
    ep.add_argument("from_clause", help="the clause whose drafting ASSUMES the other")
    ep.add_argument("to_clause", help="the clause it depends on / is capped by")
    ep.add_argument("--kind", required=True, choices=list(model.DEPENDENCY_KINDS))
    ep.add_argument("--note", required=True,
                    help="WHY (shown verbatim to future editors), e.g. \"indemnity accepted "
                         "uncapped because the aggregate cap still governs it\"")
    ep.set_defaults(func=cmd_edit_add_dependency)

    ep = edit_parser("remove-dependency", "remove a dependency edge")
    ep.add_argument("from_clause")
    ep.add_argument("to_clause")
    ep.add_argument("--kind", choices=list(model.DEPENDENCY_KINDS),
                    help="only this kind (default: all kinds between the two clauses)")
    ep.set_defaults(func=cmd_edit_remove_dependency)

    ep = edit_parser("add-variable", "add a questionnaire variable")
    ep.add_argument("name")
    ep.add_argument("--type", required=True, choices=list(model.VARIABLE_TYPES))
    ep.add_argument("--question", required=True)
    ep.add_argument("--choices", help="comma-separated (choice type only)")
    ep.set_defaults(func=cmd_edit_add_variable)

    return parser


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_build(args):
    from .merge import build_template, documents_from_paths  # needs the anthropic package
    contexts = {}
    if args.contexts:
        with open(args.contexts, encoding="utf-8") as f:
            try:
                contexts = json.load(f)
            except json.JSONDecodeError as e:
                raise model.TemplateError(f"{args.contexts} is not valid JSON: {e}") from None
        if not isinstance(contexts, dict):
            raise model.TemplateError(f"{args.contexts} must be a JSON object "
                                      f"mapping filename -> context note")
    documents = documents_from_paths(args.files, contexts)

    from pathlib import Path

    from . import skill as skill_mod
    playbook = skill_mod.load_playbook(Path(args.output).parent, args.doc_type)
    if playbook:
        print(f"  using learned playbook: {skill_mod.skill_path(Path(args.output).parent, args.doc_type)}")
    template, report, findings = build_template(documents, args.doc_type,
                                                progress=lambda msg: print(f"  {msg}"),
                                                playbook=playbook)
    model.save(template, args.output)
    report_path = args.report or (args.output + ".report.md")
    with open(report_path, "w", encoding="utf-8") as f:
        f.write(report)
    print(f"\nwrote {args.output} ({len(template.clauses)} clauses, "
          f"{len(template.variables)} variables)")
    print(f"wrote {report_path} — read the diagnosis before doing anything else")
    import getpass

    from . import journal as journal_mod
    journal_mod.append(args.output, actor=getpass.getuser(), kind="build",
                       detail=f"built from {len(documents)} contracts"
                              + (" using the learned playbook" if playbook else ""))
    return _print_findings(findings)


def cmd_validate(args):
    template = model.load(args.template)
    findings, coverage = validate_mod.validate(template)
    code = _print_findings(findings)
    print(f"sweep: {coverage['configurations_tested']} of {coverage['configurations_total']} "
          f"configurations rendered ({'exhaustive' if coverage['exhaustive'] else 'sampled'})")
    return code


def _print_findings(findings):
    for finding in findings:
        print(finding)
    error_count = len(validate_mod.errors(findings))
    warning_count = len(findings) - error_count
    print(f"{error_count} error(s), {warning_count} warning(s)")
    return 1 if error_count else 0


def cmd_questions(args):
    template = model.load(args.template)
    for variable in template.variables:
        extra = f" (one of: {', '.join(variable.choices)})" if variable.type == "choice" else ""
        print(f"{variable.name} [{variable.type}]{extra}")
        print(f"    {variable.question}")
    if args.output:
        skeleton = {v.name: None for v in template.variables}
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(skeleton, f, indent=2)
            f.write("\n")
        print(f"\nwrote {args.output} — replace each null with an answer")
    return 0


def cmd_intake(args):
    from .ingest import read_document
    from .intake import prefill
    template = model.load(args.template)
    answers = prefill(template, read_document(args.term_sheet))
    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(answers, f, indent=2)
        f.write("\n")
    missing = sorted(name for name, value in answers.items() if value is None)
    print(f"wrote {args.output}")
    if missing:
        print(f"not found in the term sheet (left null): {', '.join(missing)}")
    return 0


def cmd_render(args):
    template = model.load(args.template)
    with open(args.answers, encoding="utf-8") as f:
        try:
            answers = json.load(f)
        except json.JSONDecodeError as e:
            raise model.TemplateError(f"{args.answers} is not valid JSON: {e}") from None

    # Warn (or refuse) when the sign-off doesn't cover what we're about to render.
    clause_status = approve_mod.status(template)
    planned = render_mod.plan(template, answers)
    not_approved = [p.clause.id for p in planned
                    if clause_status[p.clause.id] != approve_mod.APPROVED]
    if clause_status[SCHEMA_APPROVAL_ID] != approve_mod.APPROVED:
        not_approved.append("(questionnaire/schema)")
    # Per-clause hashes are blind to removals, reordering and dependency
    # rewires; the certificate's template_hash is not. Both must be current.
    if approve_mod.structure_current(template) is False:
        not_approved.append("(template structure — clauses/order/dependencies "
                            "changed since certification)")
    if not_approved and not args.allow_unapproved:
        print("refusing to render: these included clauses lack current approval:", file=sys.stderr)
        for cid in not_approved:
            print(f"  - {cid}", file=sys.stderr)
        print("run `tb approve` after review, or pass --allow-unapproved for a draft.",
              file=sys.stderr)
        return 1

    if args.output.lower().endswith(".docx"):
        render_mod.render_docx(template, answers, args.output, title=args.title)
    else:
        text = render_mod.render_markdown(template, answers, title=args.title)
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text)
    label = " (DRAFT — contains unapproved clauses)" if not_approved else ""
    print(f"wrote {args.output}{label}")
    return 0


def cmd_approve(args):
    template = model.load(args.template)
    findings, coverage = validate_mod.validate(template)
    if validate_mod.errors(findings):
        _print_findings(findings)
        print("refusing to approve a template with validation errors", file=sys.stderr)
        return 1
    changed = approve_mod.approve(template, args.by, coverage)
    model.save(template, args.template)
    total = coverage["configurations_total"]
    tested = coverage["configurations_tested"]
    print(f"approved {len(changed)} item(s) by {args.by}; "
          f"{len(template.clauses) + 1 - len(changed)} already current")
    unswept = coverage.get("unswept_condition_variables", [])
    if not coverage["exhaustive"]:
        note = "SAMPLED — untested combinations remain; semantics are spot-checked only"
    elif unswept:
        note = (f"exhaustive over boolean/choice toggles, but conditions on "
                f"{', '.join(unswept)} were tested at a single sample value")
    else:
        note = "exhaustive — structural guarantees hold for every configuration"
    print(f"coverage certificate: {tested} of {total} configurations rendered ({note})")
    return 0


def cmd_skill_update(args):
    from . import skill as skill_mod
    template = model.load(args.template)
    path, markdown, notes = skill_mod.update_skill(args.template, template)
    from . import journal as journal_mod
    journal_mod.append(args.template, actor="assistant", kind="skill-update",
                       detail=f"distilled playbook -> {path}")
    print(f"wrote {path}")
    for note in notes:
        print(f"  note: {note}")
    plays = markdown.count("\n### ")
    print(f"{plays} clause play(s) — review the playbook like any other document")
    return 0


def cmd_skill_show(args):
    from pathlib import Path

    from . import skill as skill_mod
    template = model.load(args.template)
    playbook = skill_mod.load_playbook(Path(args.template).parent, template.doc_type)
    if playbook is None:
        print("no playbook yet — journal decisions (edit --why ...) then run "
              "`tb skill update`", file=sys.stderr)
        return 1
    print(playbook)
    return 0


def cmd_skill_replay(args):
    from pathlib import Path

    from . import journal as journal_mod
    from . import skill as skill_mod
    from .negotiate import MIN_REPLAY_AGREEMENT
    from .replay import run_replay

    template = model.load(args.template)
    workspace = Path(args.template).parent
    playbook = skill_mod.load_playbook(workspace, template.doc_type)
    if playbook is None:
        print("no playbook yet — run `tb skill update` first", file=sys.stderr)
        return 1
    entries = journal_mod.read(args.template)
    scores = run_replay(template, playbook, entries,
                        progress=lambda msg: print(f"  {msg}"))
    if not scores:
        print("nothing to replay — journal decisions with --why and a --disposition first")
        return 1
    path = skill_mod.save_replay(workspace, template.doc_type, scores)
    print(f"wrote {path}")
    for clause_id, score in scores.items():
        rate = score["agree"] / score["total"]
        verdict = "may act when mature" if rate >= MIN_REPLAY_AGREEMENT else \
                  "WILL STAY ESCALATED despite maturity"
        print(f"  {clause_id}: {score['agree']}/{score['total']} ({rate:.0%}) — {verdict}")
    journal_mod.append(args.template, actor="assistant", kind="skill-update",
                       detail=f"replay scored {len(scores)} clause(s)")
    return 0


def cmd_negotiate(args):
    from pathlib import Path

    from . import journal as journal_mod
    from . import skill as skill_mod
    from .ingest import read_document
    from .negotiate import negotiate, render_report, resolve_threshold

    template = model.load(args.template)
    playbook = skill_mod.load_playbook(Path(args.template).parent, template.doc_type)
    if playbook is None:
        print("no playbook yet — in the learning phase every point is the lawyer's. "
              "Journal decisions (tb edit ... --why) and run `tb skill update` first.",
              file=sys.stderr)
        return 1
    markup = read_document(args.markup)
    entries = journal_mod.read(args.template)
    threshold = resolve_threshold(playbook)
    replay_scores = skill_mod.load_replay(Path(args.template).parent, template.doc_type)
    plan = negotiate(template, markup, playbook, entries, threshold, replay_scores)
    report = render_report(plan, template, entries, threshold)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.output}")
    else:
        print(report)
    journal_mod.append(args.template, actor=journal_mod.ASSISTANT_ACTOR, kind="negotiation",
                       detail=plan.summary[:300], matter=args.matter,
                       counterparty=args.counterparty)

    escalations = [r for r in plan.responses if r.stance == "escalate"]
    counters = [r for r in plan.responses if r.stance == "counter" and r.proposed_text]
    print(f"\n{len(plan.responses)} clauses addressed — {len(counters)} counters, "
          f"{len(escalations)} escalated to the lawyer")

    if not args.apply:
        return 0
    from .ops import gated_edit
    applied = 0
    for response in counters:
        if response.decider != journal_mod.ASSISTANT_ACTOR:
            # the delegation matrix assigns this tier to a human — the draft
            # is in the report, but the assistant does not write it
            print(f"NOT applied {response.clause_id} — the delegation matrix "
                  f"requires {response.decider} to decide")
            continue
        def counter_op(template, _r=response):
            clause = template.clause(_r.clause_id)
            default = next((v for v in clause.variants if v.when is None), clause.variants[0])
            return edit_mod.replace_text(template, _r.clause_id, default.id, _r.proposed_text)

        outcome = gated_edit(args.template, counter_op, journal_meta={
            "actor": journal_mod.ASSISTANT_ACTOR, "op": "replace-text",
            "why": response.rationale, "matter": args.matter,
            "counterparty": args.counterparty, "disposition": "countered",
        })
        if not outcome.saved:
            print(f"NOT applied {response.clause_id} — would introduce validation errors:")
            for finding in outcome.new_errors:
                print(f"  {finding}")
            continue
        applied += 1
        print(f"applied counter to {response.clause_id} (approval now stale — "
              f"human sign-off still required)")
    print(f"{applied} counter(s) applied; escalated clauses await the lawyer, whose "
          f"journaled decisions raise maturity")
    return 0


def cmd_matter_open(args):
    from .matter import open_matter
    with open(args.answers, encoding="utf-8") as f:
        try:
            answers = json.load(f)
        except json.JSONDecodeError as e:
            raise model.TemplateError(f"{args.answers} is not valid JSON: {e}") from None
    matter = open_matter(args.workspace, args.id, args.template, answers, args.counterparty)
    print(f"opened matter {matter.id!r} against {matter.counterparty} "
          f"on {matter.template}@{matter.template_hash[:19]}…")
    return 0


def cmd_matter_list(args):
    from .matter import list_matters
    matters = list_matters(args.workspace)
    if not matters:
        print("no matters yet — `tb matter open ...`")
        return 0
    for m in matters:
        pending = len(m.pending_escalations())
        print(f"  {m.id:<28} {m.status:<9} {m.counterparty:<24} "
              f"rounds={len(m.rounds)} deviations={len(m.deviations)}"
              + (f" ESCALATIONS={pending}" if pending else ""))
    return 0


def cmd_matter_show(args):
    from .matter import load_matter
    m = load_matter(args.workspace, args.id)
    print(f"{m.id} — {m.doc_type} vs {m.counterparty} [{m.status}]")
    print(f"template {m.template}@{m.template_hash[:19]}… opened {m.opened}")
    for r in m.rounds:
        print(f"\nround {r.number} ({r.source}, {r.received}): {len(r.asks)} asks"
              + (f", {len(r.unanchored)} unanchored" if r.unanchored else ""))
        for ask in r.asks:
            print(f"  - {ask.clause_id}: {ask.kind}")
    if m.deviations:
        print("\ndeviations from standard:")
        for d in m.deviations:
            print(f"  - {d.clause_id} (round {d.round}, {d.approved_by}): {d.rationale}")
    pending = m.pending_escalations()
    if pending:
        print(f"\nPENDING ESCALATIONS ({len(pending)}):")
        for e in pending:
            print(f"  - {e.clause_id} (round {e.round}, requires {e.requires})")
    return 0


def cmd_matter_round(args):
    from .matter import ingest_round

    matter, round_, report = ingest_round(args.workspace, args.id, args.file,
                                          negotiate=args.negotiate, say=print)
    for ask in round_.asks:
        print(f"  - {ask.clause_id}: {ask.kind}")
    if report and args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(report)
        print(f"wrote {args.output}")
    pending = len(matter.pending_escalations())
    if pending:
        print(f"{pending} escalation(s) pending (`tb matter resolve` or the web inbox)")
    return 0


def cmd_matter_resolve(args):
    from .matter import resolve_escalation

    if args.hold:
        decision, agreed_text = "hold", None
    elif args.accept_theirs:
        decision, agreed_text = "accept-theirs", None
    else:
        decision, agreed_text = "custom", _read_text_file(args.file)
    resolve_escalation(args.workspace, args.id, clause_id=args.clause_id,
                       decision=decision, by=args.by, why=args.why,
                       agreed_text=agreed_text)
    if decision == "hold":
        print(f"held the standard text on {args.clause_id} (journaled, maturity +1)")
    else:
        print(f"recorded deviation on {args.clause_id} (journaled, maturity +1); "
              f"it will appear in the exception register")
    return 0


def cmd_matter_close(args):
    from .matter import close_matter

    matter = close_matter(args.workspace, args.id, status=args.status,
                          by=args.by, why=args.why or "")
    print(f"matter {matter.id!r} closed as {matter.status}")
    return 0


def cmd_exceptions(args):
    from .exceptions import render_register
    register = render_register(args.workspace)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(register)
        print(f"wrote {args.output}")
    else:
        print(register)
    return 0


def cmd_drift(args):
    from .drift import render_drift_report
    print(render_drift_report(args.workspace))
    return 0


def cmd_serve(args):
    import os

    import uvicorn

    from .server import create_app
    auth = os.environ.get("TB_AUTH") or None
    app = create_app(args.workspace, auth=auth)  # fails fast on a bad path
    note = "  (HTTP Basic auth on; the intake surface stays open)" if auth else ""
    print(f"template-builder UI → http://{args.host}:{args.port}  (Ctrl-C to stop){note}")
    if auth and args.host not in ("127.0.0.1", "localhost", "::1"):
        print("NOTE: Basic auth sends credentials with every request — put TLS "
              "(a reverse proxy) in front of any non-localhost deployment.")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")
    return 0


def cmd_status(args):
    template = model.load(args.template)
    clause_status = approve_mod.status(template)
    width = max([len(c.id) for c in template.clauses] + [len("(questionnaire/schema)")])
    for clause in template.clauses:
        print(f"  {clause.id.ljust(width)}  {clause_status[clause.id]}")
    print(f"  {'(questionnaire/schema)'.ljust(width)}  {clause_status[SCHEMA_APPROVAL_ID]}")
    stale = [cid for cid, s in clause_status.items() if s == approve_mod.STALE]
    unapproved = [cid for cid, s in clause_status.items() if s == approve_mod.UNAPPROVED]
    if stale or unapproved:
        print(f"\n{len(stale)} stale, {len(unapproved)} unapproved — run `tb approve` after review")
    elif approve_mod.structure_current(template) is False:
        # Every clause matches its own hash, but the template's structure
        # (order / membership) changed — numbering, and so the rendered
        # document, differs from what was signed off.
        print("\nall clauses individually approved, BUT the template structure changed "
              "since sign-off (clauses added, removed or reordered) — run `tb approve` "
              "after review")
    else:
        cert = template.certificate or {}
        print(f"\nall approved (by {cert.get('by', '?')} on {cert.get('date', '?')})")
    age = approve_mod.certificate_age_days(template)
    if age is not None and age > approve_mod.REVIEW_AFTER_DAYS:
        print(f"⚠ the sign-off is {age} days old — templates rot; schedule a re-review")
    return 0


# ---------------------------------------------------------------------------
# Edit commands — apply, validate, save (or refuse)
# ---------------------------------------------------------------------------

def _apply_edit(args, operation):
    import getpass

    from .ops import gated_edit
    outcome = gated_edit(
        args.template, operation, force=args.force,
        journal_meta={
            "actor": getattr(args, "actor", None) or getpass.getuser(),
            "op": args.edit_command,
            "why": getattr(args, "why", None),
            "matter": getattr(args, "matter", None),
            "counterparty": getattr(args, "counterparty", None),
            "disposition": getattr(args, "disposition", None),
        },
    )

    if not outcome.saved:
        print("edit NOT saved — it would introduce validation errors:", file=sys.stderr)
        for finding in outcome.new_errors:
            print(f"  {finding}", file=sys.stderr)
        print("fix the edit, or pass --force to save anyway.", file=sys.stderr)
        return 1

    result = outcome.result
    for message in result.messages:
        print(message)
    if result.review:
        print(f"blast radius — these clauses cross-reference the edited clause, review them too: "
              f"{', '.join(result.review)}")
    if outcome.new_errors:
        print(f"saved WITH {len(outcome.new_errors)} new validation error(s) (--force)")
    if not (getattr(args, "why", None) or "").strip():
        print("journaled without --why — rationale is what `tb skill` learns from")
    if result.touched:
        clause_status = approve_mod.status(outcome.template)
        stale = [cid for cid in result.touched
                 if clause_status.get(cid) == approve_mod.STALE]
        if stale:
            print(f"approval invalidated for: {', '.join(stale)} (only these need re-approval)")
    return 0


def _read_text_file(path):
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        raise edit_mod.EditError(f"{path} is empty — refusing to save empty clause text")
    return text


def cmd_edit_replace_text(args):
    text = _read_text_file(args.file)
    return _apply_edit(args, lambda t: edit_mod.replace_text(t, args.clause_id, args.variant_id, text))


def cmd_edit_set_condition(args):
    expr = None if args.always else args.when
    return _apply_edit(args, lambda t: edit_mod.set_condition(t, args.clause_id, expr))


def cmd_edit_add_variant(args):
    text = _read_text_file(args.file)
    return _apply_edit(args, lambda t: edit_mod.add_variant(t, args.clause_id, args.variant_id,
                                                            text, args.when))


def cmd_edit_remove_variant(args):
    return _apply_edit(args, lambda t: edit_mod.remove_variant(t, args.clause_id, args.variant_id))


def cmd_edit_add_clause(args):
    text = _read_text_file(args.file)
    return _apply_edit(args, lambda t: edit_mod.add_clause(t, args.clause_id, args.heading, text,
                                                           after=args.after,
                                                           include_when=args.when))


def cmd_edit_remove_clause(args):
    return _apply_edit(args, lambda t: edit_mod.remove_clause(t, args.clause_id))


def cmd_edit_add_dependency(args):
    return _apply_edit(args, lambda t: edit_mod.add_dependency(
        t, args.from_clause, args.to_clause, args.kind, args.note))


def cmd_edit_remove_dependency(args):
    return _apply_edit(args, lambda t: edit_mod.remove_dependency(
        t, args.from_clause, args.to_clause, args.kind))


def cmd_edit_add_variable(args):
    choices = None
    if args.choices:
        choices = [c.strip() for c in args.choices.split(",") if c.strip()]
    return _apply_edit(args, lambda t: edit_mod.add_variable(t, args.name, args.type,
                                                             args.question, choices))


if __name__ == "__main__":
    sys.exit(main())
