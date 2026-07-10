"""Shared test fixture: a small, valid NDA-shaped template.

Structure (5 clauses, 6 variables):
  parties           always included, uses {{party_1}} / {{party_2}} / {{effective_date}}
  definitions       defines "Confidential Information"
  obligations       2 variants: mutual (when is_mutual) / one-way (default);
                    uses the defined term and {{ref:definitions}}
  non-solicit       optional: include_when include_non_solicit
  term              uses {{term_years}}
  governing-law     2 variants: ny (when governing_law == "New York") / default
"""
import copy

import pytest

from template_builder.model import template_from_dict

TEMPLATE_DICT = {
    "schema_version": 1,
    "doc_type": "Mutual Non-Disclosure Agreement",
    "sources": ["nda_a.txt", "nda_b.txt"],
    "variables": [
        {"name": "party_1", "type": "string", "question": "Full legal name of the first party?"},
        {"name": "party_2", "type": "string", "question": "Full legal name of the second party?"},
        {"name": "effective_date", "type": "string", "question": "Effective date of the agreement?"},
        {"name": "is_mutual", "type": "boolean",
         "question": "Do both parties disclose confidential information?"},
        {"name": "include_non_solicit", "type": "boolean",
         "question": "Include a non-solicitation clause?"},
        {"name": "term_years", "type": "number", "question": "Term of the agreement, in years?"},
        {"name": "governing_law", "type": "choice", "question": "Which law governs?",
         "choices": ["New York", "Delaware"]},
    ],
    "clauses": [
        {
            "id": "parties",
            "heading": "Parties",
            "include_when": None,
            "defines": [],
            "variants": [
                {"id": "default", "when": None, "provenance": ["nda_a.txt"],
                 "text": "This Agreement is made on {{effective_date}} between "
                         "{{party_1}} and {{party_2}}."},
            ],
        },
        {
            "id": "definitions",
            "heading": "Definitions",
            "include_when": None,
            "defines": ["Confidential Information"],
            "variants": [
                {"id": "default", "when": None, "provenance": ["nda_a.txt", "nda_b.txt"],
                 "text": "\"Confidential Information\" means any non-public information "
                         "disclosed by one party to the other."},
            ],
        },
        {
            "id": "obligations",
            "heading": "Confidentiality Obligations",
            "include_when": None,
            "defines": [],
            "variants": [
                {"id": "mutual", "when": "is_mutual", "provenance": ["nda_a.txt"],
                 "text": "Each party shall protect the other's Confidential Information "
                         "as defined in {{ref:definitions}}."},
                {"id": "one-way", "when": None, "provenance": ["nda_b.txt"],
                 "text": "The receiving party shall protect the disclosing party's "
                         "Confidential Information as defined in {{ref:definitions}}."},
            ],
        },
        {
            "id": "non-solicit",
            "heading": "Non-Solicitation",
            "include_when": "include_non_solicit",
            "defines": [],
            "variants": [
                {"id": "default", "when": None, "provenance": ["nda_a.txt"],
                 "text": "Neither party shall solicit the other's employees during the "
                         "term described in {{ref:term}}."},
            ],
        },
        {
            "id": "term",
            "heading": "Term",
            "include_when": None,
            "defines": [],
            "variants": [
                {"id": "default", "when": None, "provenance": ["nda_a.txt", "nda_b.txt"],
                 "text": "This Agreement continues for {{term_years}} years from "
                         "{{effective_date}}."},
            ],
        },
        {
            "id": "governing-law",
            "heading": "Governing Law",
            "include_when": None,
            "defines": [],
            "variants": [
                {"id": "ny", "when": 'governing_law == "New York"', "provenance": ["nda_a.txt"],
                 "text": "This Agreement is governed by the laws of the State of New York."},
                {"id": "other", "when": None, "provenance": ["nda_b.txt"],
                 "text": "This Agreement is governed by the laws of the State of Delaware."},
            ],
        },
    ],
    "approvals": [],
    "certificate": None,
}

ANSWERS = {
    "party_1": "Acme Corp",
    "party_2": "Blue Ridge LLC",
    "effective_date": "1 July 2026",
    "is_mutual": True,
    "include_non_solicit": False,
    "term_years": 3,
    "governing_law": "New York",
}


@pytest.fixture
def template_dict():
    return copy.deepcopy(TEMPLATE_DICT)


@pytest.fixture
def template(template_dict):
    return template_from_dict(template_dict)


@pytest.fixture
def answers():
    return dict(ANSWERS)
