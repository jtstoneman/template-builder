"""Tests for the build pipeline's deterministic parts: extractive atomisation
(fake ILGS document, no network), questionnaire planning prompt, and the
build_template orchestration (all LLM/Isaacus calls monkeypatched)."""
from dataclasses import dataclass, field

import pytest

import template_builder.merge as merge
from template_builder.decompile import AtomisedContract, SourceClause, clauses_from_enrichment
from template_builder.merge import (
    Outline,
    OutlineEntry,
    OutlineMatch,
    ProposedVariable,
    ProposedVariant,
    SourceDocument,
    SynthesisedClause,
    VariablePlan,
    build_template,
    variable_plan_prompt,
)


# --- a minimal fake of the Isaacus ILGS document ------------------------------

@dataclass
class Span:
    start: int
    end: int

    def decode(self, text):
        return text[self.start:self.end]


@dataclass
class Seg:
    id: str
    parent: str | None
    children: list = field(default_factory=list)
    span: Span = None
    code: Span = None
    title: Span = None


@dataclass
class Term:
    name: Span
    meaning: Span
    mentions: list = field(default_factory=list)


@dataclass
class FakeDoc:
    text: str
    segments: list
    terms: list = field(default_factory=list)
    junk: list = field(default_factory=list)
    type: str = "contract"


def _fake_doc():
    #          0         1         2         3         4         5         6
    #          0123456789012345678901234567890123456789012345678901234567890
    text = ("PREAMBLE between A and B.\n"          # 0..25  (clause 1)
            "PAGE 1 OF 9\n"                        # 26..37 (junk)
            '1. Definitions "Term" means X.\n'     # 38..68 (clause 2)
            "2. Obligations apply.\n")             # 69..90 (clause 3)
    root = Seg(id="seg:0", parent=None, children=["seg:1", "seg:2", "seg:3"],
               span=Span(0, len(text)))
    preamble = Seg(id="seg:1", parent="seg:0", span=Span(0, 37))  # includes the junk line
    definitions = Seg(id="seg:2", parent="seg:0", span=Span(38, 68),
                      code=Span(38, 40), title=Span(41, 52))       # "1." "Definitions"
    obligations = Seg(id="seg:3", parent="seg:0", span=Span(69, 90))
    term = Term(name=Span(54, 58), meaning=Span(53, 68), mentions=[])  # "Term", unused
    return FakeDoc(text=text, segments=[root, preamble, definitions, obligations],
                   terms=[term], junk=[Span(26, 38)])


def test_extractive_conversion_verbatim_junk_stripped_terms_mapped():
    result = clauses_from_enrichment(_fake_doc(), "spa_01.txt")
    assert isinstance(result, AtomisedContract)
    headings = [c.heading for c in result.clauses]
    assert headings[0].startswith("PREAMBLE")     # heading derived from first line
    assert headings[1] == "1. Definitions"        # code + title spans
    texts = [c.text for c in result.clauses]
    assert "PAGE 1 OF 9" not in texts[0]          # junk stripped
    assert texts[1] == '1. Definitions "Term" means X.'  # verbatim
    assert result.clauses[1].defines == ["Term"]  # term mapped to its defining clause
    # unused defined term flagged in the diagnosis
    assert any("never used" in note for note in result.notes)


def test_extractive_conversion_rejects_empty_document():
    doc = FakeDoc(text="  ", segments=[Seg(id="s", parent=None, span=Span(0, 2))])
    with pytest.raises(ValueError):
        clauses_from_enrichment(doc, "empty.txt")


def test_variable_plan_prompt_shows_appearance_counts_and_contexts():
    outline = Outline(clauses=[
        OutlineEntry(id="warranties", heading="Warranties", matches=[
            OutlineMatch(file="a.txt", indices=[1]),
        ]),
    ], notes=[])
    atomised = {
        "a.txt": [SourceClause(heading="P", text="Between X and Y", defines=[])],
        "b.txt": [SourceClause(heading="P", text="Between P and Q", defines=[])],
    }
    prompt = variable_plan_prompt("SPA", outline, atomised, {"a.txt": "seller-friendly"})
    assert "appears in 1/2: a.txt" in prompt
    assert "- a.txt: seller-friendly" in prompt
    assert "Between P and Q" in prompt  # preambles included


# --- orchestration (all model calls stubbed) ----------------------------------

@pytest.fixture
def stubbed_pipeline(monkeypatch):
    def fake_atomise(text, filename, context=None):
        return AtomisedContract(clauses=[
            SourceClause(heading="Preamble", text=f"Between parties ({filename}).", defines=[]),
            SourceClause(heading="Payment", text="Buyer pays the Price.", defines=["Price"]),
            SourceClause(heading="Odd One", text="Unmatched text.", defines=[]),
        ], notes=[f"{filename}-note"])

    def fake_outline(atomised, contexts=None):
        return Outline(clauses=[
            OutlineEntry(id="preamble", heading="Preamble", matches=[
                OutlineMatch(file=name, indices=[0]) for name in atomised]),
            OutlineEntry(id="payment", heading="Payment", matches=[
                OutlineMatch(file=name, indices=[1]) for name in atomised]),
        ], notes=[])
        # note: clause index 2 ("Odd One") is deliberately never matched

    def fake_plan(doc_type, outline, atomised, contexts, playbook=None):
        return VariablePlan(variables=[
            ProposedVariable(name="party_a_name", type="string", question="First party?", choices=[]),
            ProposedVariable(name="never_used", type="string", question="?", choices=[]),
        ], notes=["planned from contexts"])

    def fake_synthesise(entry, sources, outline_ids, variables, contexts=None,
                        all_files=None, playbook=None):
        # the planned schema must be visible to every synthesis call
        assert any(v.name == "party_a_name" for v in variables)
        return SynthesisedClause(
            include_when=None,
            defines=["Price"] if entry.id == "payment" else [],
            variants=[ProposedVariant(id="default", when=None,
                                      text=f"{entry.heading} with {{{{party_a_name}}}}.",
                                      provenance=[f for f, _ in sources])],
            new_variables=[],
            notes=[],
        )

    def fake_map_dependencies(clauses):
        from template_builder.merge import DependencyMap, ProposedDependency
        return DependencyMap(dependencies=[
            ProposedDependency(from_clause="payment", to_clause="preamble",
                               kind="relies-on", note="payment assumes the parties"),
            ProposedDependency(from_clause="payment", to_clause="ghost-clause",
                               kind="subject-to", note="hallucinated edge"),
        ], notes=["one observation"])

    monkeypatch.setattr(merge, "atomise", fake_atomise)
    monkeypatch.setattr(merge, "build_outline", fake_outline)
    monkeypatch.setattr(merge, "plan_variables", fake_plan)
    monkeypatch.setattr(merge, "synthesise_clause", fake_synthesise)
    monkeypatch.setattr(merge, "map_dependencies", fake_map_dependencies)


def test_build_template_orchestration(stubbed_pipeline):
    documents = [
        SourceDocument(name="a.txt", text="doc a", context="seller-friendly"),
        SourceDocument(name="b.txt", text="doc b", context=None),
    ]
    template, report, findings = build_template(documents, "Share Purchase Agreement")

    assert [c.id for c in template.clauses] == ["preamble", "payment"]  # outline order kept
    assert template.sources == ["a.txt", "b.txt"]
    assert template.source_contexts == {"a.txt": "seller-friendly"}
    # planned variable kept because clauses use it; unused one pruned with a note
    assert [v.name for v in template.variables] == ["party_a_name"]
    assert "dropped 1 planned variable(s) no clause used: never_used" in report
    # deterministic unmatched-clause detection made it into the diagnosis
    assert "'Odd One'" in report and "NOT matched" in report
    # the plan's reasoning is surfaced for the lawyer
    assert "planned from contexts" in report
    # dependency mapping: the valid edge is kept, the hallucinated one dropped
    assert [d.describe() for d in template.dependencies] == [
        "payment relies-on preamble: payment assumes the parties"]
    assert "dropped edge naming unknown clause" in report
    assert "`payment` **relies-on** `preamble` — payment assumes the parties" in report
    # a clean stub pipeline produces a template that passes the gates
    from template_builder import validate
    assert validate.errors(findings) == []


# --- atomiser dispatch --------------------------------------------------------

def test_atomise_falls_back_and_latches_on_dead_isaacus_key(monkeypatch):
    import template_builder.decompile as decompile

    class AuthenticationError(Exception):
        pass

    calls = {"extractive": 0, "llm": 0}

    def failing_extractive(text, filename):
        calls["extractive"] += 1
        raise AuthenticationError("401")

    def fake_llm(text, filename, context=None):
        calls["llm"] += 1
        return AtomisedContract(clauses=[SourceClause(heading="H", text="T", defines=[])],
                                notes=[])

    monkeypatch.setenv("ISAACUS_API_KEY", "dead-key")
    monkeypatch.setenv("TB_ATOMISER", "auto")
    monkeypatch.setattr(decompile, "atomise_extractive", failing_extractive)
    monkeypatch.setattr(decompile, "atomise_llm", fake_llm)
    monkeypatch.setattr(decompile, "_extraction_disabled", False)

    first = decompile.atomise("text", "a.txt")
    assert any("fell back" in n for n in first.notes)
    decompile.atomise("text", "b.txt")
    decompile.atomise("text", "c.txt")
    assert calls["extractive"] == 1   # auth failure latched after the first document
    assert calls["llm"] == 3


def test_atomise_llm_mode_never_touches_isaacus(monkeypatch):
    import template_builder.decompile as decompile

    monkeypatch.setenv("ISAACUS_API_KEY", "whatever")
    monkeypatch.setenv("TB_ATOMISER", "llm")
    monkeypatch.setattr(decompile, "atomise_extractive",
                        lambda *a: (_ for _ in ()).throw(AssertionError("must not be called")))
    monkeypatch.setattr(decompile, "atomise_llm",
                        lambda *a, **k: AtomisedContract(clauses=[
                            SourceClause(heading="H", text="T", defines=[])], notes=[]))
    result = decompile.atomise("text", "a.txt")
    assert result.clauses[0].heading == "H"


# --- divergence scan: where the corpus disagrees with itself ------------------

def _divergence_fixture():
    atomised = {
        "a.txt": [
            SourceClause(heading="Term", defines=[],
                         text="This Agreement lasts three (3) years from the Effective Date."),
            SourceClause(heading="Remedies", defines=[],
                         text="The Disclosing Party is entitled to injunctive relief, "
                              "specific performance and recovery of all legal costs on "
                              "an indemnity basis, without proof of actual damage."),
        ],
        "b.txt": [
            SourceClause(heading="Term", defines=[],
                         text="This Agreement lasts five (5) years from the Effective Date."),
            SourceClause(heading="Remedies", defines=[],
                         text="Nothing in this Agreement limits the remedies available "
                              "to either party at law; each party bears its own costs."),
        ],
    }
    outline = Outline(clauses=[
        OutlineEntry(id="term", heading="Term", matches=[
            OutlineMatch(file="a.txt", indices=[0]),
            OutlineMatch(file="b.txt", indices=[0])]),
        OutlineEntry(id="remedies", heading="Remedies", matches=[
            OutlineMatch(file="a.txt", indices=[1]),
            OutlineMatch(file="b.txt", indices=[1])]),
    ], notes=[])
    return outline, atomised


def test_divergent_versions_flags_positions_not_literals():
    outline, atomised = _divergence_fixture()
    found = merge.divergent_versions(outline, atomised)
    # "three (3) years" vs "five (5) years" is a deal fact, not a divergence
    assert [d.clause_id for d in found] == ["remedies"]
    assert found[0].similarity < 0.75
    assert {found[0].file_a, found[0].file_b} == {"a.txt", "b.txt"}


def test_divergent_versions_needs_two_versions():
    outline, atomised = _divergence_fixture()
    atomised["b.txt"] = []  # every clause now has a single version
    assert merge.divergent_versions(outline, atomised) == []


def test_variable_plan_prompt_surfaces_disagreements():
    outline, atomised = _divergence_fixture()
    prompt = variable_plan_prompt("NDA", outline, atomised, {})
    assert "DISAGREE" in prompt
    assert "--- remedies (similarity" in prompt
    assert "injunctive relief" in prompt        # excerpt from a.txt's position
    assert "bears its own costs" in prompt      # excerpt from b.txt's position
    assert "--- term (similarity" not in prompt
