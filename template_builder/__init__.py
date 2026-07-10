"""template_builder — build deterministic contract templates from precedent contracts.

The pipeline in one paragraph:

    tb build     feeds ~20-30 contracts of one type through the decompiler
                 (Isaacus extraction or Claude), aligns them into a clause
                 graph, plans one questionnaire, synthesises each clause, and
                 maps the semantic dependencies between them.
    tb render    is a pure function: template + answers -> finished document.
                 No LLM. Same inputs, same output, every time.
    tb approve   attaches a lawyer's sign-off to per-clause content hashes,
                 so a later edit invalidates only the touched clauses.
    tb edit      constrained, validated-before-save graph operations — every
                 one journaled, with rationale, into the learning record.
    tb skill     distils the journal into a per-doc-type playbook (SKILL.md).
    tb negotiate drafts against a counterparty markup under the playbook and
                 a per-clause autonomy gate.
    tb serve     the web UI: template workbench + rich document editor.

The LLM sits strictly upstream of the approved boundary. Everything
downstream — validation, rendering, hashing, approval, the edit gate — is
plain deterministic code.

Public API for library users:
"""

# NOTE: the `validate` FUNCTION is deliberately not re-exported here — it
# would shadow the `template_builder.validate` submodule. Use
# `from template_builder.validate import validate` for it.
from .model import Template, TemplateError, load, save, template_from_dict
from .ops import GatedOutcome, gated_edit
from .render import RenderError, render_docx, render_markdown
from .validate import Finding

__version__ = "0.1.0"

__all__ = [
    "Finding",
    "GatedOutcome",
    "RenderError",
    "Template",
    "TemplateError",
    "gated_edit",
    "load",
    "render_docx",
    "render_markdown",
    "save",
    "template_from_dict",
]
