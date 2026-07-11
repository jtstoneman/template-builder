# Build report — Non-Disclosure Agreement

Built from 12 contracts:

- nda_01.txt
- nda_02.txt
- nda_03.txt
- nda_04.txt
- nda_05.txt
- nda_06.txt
- nda_07.txt
- nda_08.txt
- nda_09.txt
- nda_10.txt
- nda_11.txt
- nda_12.txt

## Canonical outline

- `preamble` Preamble and Purpose — 2 variants
- `definitions` Definitions — 4 variants
- `confidentiality-obligations` Confidentiality Obligations — 2 variants
- `exclusions` Exclusions — 3 variants
- `compelled-disclosure` Compelled Disclosure (only when `include_compelled_disclosure`)
- `non-solicitation` Non-Solicitation (only when `include_non_solicitation`) — 2 variants
- `return-destruction` Return or Destruction of Materials (only when `include_return_destruction`) — 3 variants
- `no-license` No License; Proprietary Rights — 2 variants
- `no-obligation-no-warranty` No Obligation to Disclose; No Warranty (only when `include_no_obligation_no_warranty`)
- `residuals` Residuals (only when `include_residuals`)
- `term-survival` Term and Survival — 3 variants
- `remedies` Remedies; Injunctive Relief (only when `include_remedies`) — 2 variants
- `governing-law` Governing Law; Jurisdiction (only when `include_governing_law`)
- `miscellaneous` Entire Agreement; Miscellaneous — 3 variants
- `signature` Signature Block

## Questionnaire

- `nda_type` (choice): Is this a mutual NDA (both parties disclose and receive) or a one-way NDA (one party discloses, the other only receives)? This governs whether obligations, non-solicitation and remedies run reciprocally or only protect the disclosing party. — one of ['mutual', 'unilateral']
- `party_a_name` (string): Full legal name of the first party (the disclosing party in a one-way NDA).
- `party_a_description` (string): Entity description of the first party, e.g. 'a Delaware corporation' or 'a Texas limited liability company'.
- `party_a_address` (string): Principal place of business (full address) of the first party, if stated.
- `party_a_defined_term` (string): Short defined term used for the first party in the body of the agreement, e.g. 'Meridian' or 'Discloser'.
- `party_b_name` (string): Full legal name of the second party (the receiving party in a one-way NDA).
- `party_b_description` (string): Entity description of the second party, e.g. 'a New York corporation'.
- `party_b_address` (string): Principal place of business (full address) of the second party, if stated.
- `party_b_defined_term` (string): Short defined term used for the second party in the body of the agreement, e.g. 'Callowhill' or 'Recipient'.
- `effective_date` (string): Effective date of the agreement (as it should appear, e.g. 'March 3, 2023').
- `purpose` (string): Description of the Purpose — the business relationship the parties are exploring (e.g. 'a potential investment in the Discloser' or 'a potential commercial printing and distribution relationship').
- `governing_law_state` (string): Which U.S. state's law governs the agreement (e.g. New York, Delaware, California, Texas)?
- `jurisdiction_venue` (string): The courts / county the parties submit to for exclusive jurisdiction (e.g. 'the state and federal courts located in New Castle County, Delaware').
- `non_solicitation_months` (number): Non-solicitation period in months (e.g. 12). Used only if the non-solicitation clause is included.
- `party_a_signatory_name` (string): Name of the individual signing for the first party.
- `party_a_signatory_title` (string): Title of the individual signing for the first party.
- `party_b_signatory_name` (string): Name of the individual signing for the second party.
- `party_b_signatory_title` (string): Title of the individual signing for the second party.
- `drafting_posture` (choice): Overall balance of the agreement between protecting the disclosing party and protecting the receiving party. 'Discloser-favourable' = strict obligations, narrow exclusions/carve-outs (e.g. public-knowledge exclusion lost only if disclosure is NOT the recipient's fault AND in breach), no residuals, strong one-sided remedies. 'Recipient-favourable' = broader exclusions (public knowledge excluded regardless of breach), residuals rights, lighter obligations. 'Balanced' = middle ground. This drives synthesis of the exclusions, confidentiality-obligations, compelled-disclosure and remedies clauses. — one of ['discloser-favourable', 'balanced', 'recipient-favourable']
- `include_compelled_disclosure` (boolean): Include a Compelled Disclosure clause (permitting disclosure required by law/court order with notice to the disclosing party)? Present in 11 of 12 sources.
- `include_non_solicitation` (boolean): Include a Non-Solicitation clause restricting solicitation/hiring of the other party's personnel? Present in only 5 of 12 sources.
- `include_return_destruction` (boolean): Include a standalone Return or Destruction of Materials clause? Present in 10 of 12 sources.
- `include_no_obligation_no_warranty` (boolean): Include a 'No Obligation to Disclose; No Warranty' clause (information provided 'AS IS', no duty to disclose or to proceed with a transaction)? Present in only 2 of 12 sources.
- `include_residuals` (boolean): Include a Residuals clause (allowing use of information retained in unaided memory)? Strongly recipient-favourable; present in only 1 of 12 sources.
- `include_remedies` (boolean): Include a Remedies / Injunctive Relief clause (acknowledgment of irreparable harm and entitlement to injunctive relief)? Present in 11 of 12 sources.
- `include_governing_law` (boolean): Include a Governing Law; Jurisdiction clause? Present in 11 of 12 sources.
- `include_representatives` (boolean): Include a defined term for "Representatives" (a party's directors, officers, employees and professional advisors who need to know Confidential Information)? Present in only 3 of 12 sources, but commonly relied on by the confidentiality-obligations clause.
- `agreement_term` (string): What is the stated term of the agreement, measured from the Effective Date (e.g. 'two (2) years', 'three (3) years')?
- `obligation_survival_period` (string): For how long after expiration or termination do the confidentiality obligations survive (e.g. 'three (3) years')? Not used where survival runs for so long as the information remains Confidential Information.
- `include_assignment_merger_exception` (boolean): Should the anti-assignment provision include a carve-out permitting assignment to a successor in connection with a merger or sale of substantially all assets (without the other party's consent)?

## Dependency map (consequential-change wiring)

- `confidentiality-obligations` **relies-on** `term-survival` — The confidentiality obligations have no built-in duration; their temporal scope is set entirely by the survival clause (obligation_survival_period, with the perpetual carve-out for trade secrets). Shortening or deleting the survival provision silently limits how long the core obligations bite.
- `confidentiality-obligations` **subject-to** `residuals` — When included, the residuals clause overrides the confidentiality obligations 'notwithstanding' them, letting personnel freely use information retained in unaided memory. Any tightening of the confidentiality obligations is materially undercut by this carve-out; the two must be edited together.
- `remedies` **relies-on** `confidentiality-obligations` — The injunctive-relief remedy presupposes the substantive duties of confidentiality and permitted-use in the obligations clause; if the scope of those obligations narrows, the 'breach' the remedy attaches to narrows with it.
- `remedies` **relies-on** `governing-law` — The entitlement to injunctive relief and specific performance 'without the necessity of posting a bond or proving actual damages' depends on the chosen governing law and forum; some states will not honour a contractual waiver of bond or of the irreparable-harm showing, so changing the governing-law state can defeat this clause.
- `non-solicitation` **relies-on** `governing-law` — Enforceability and permissible duration of the employee non-solicitation covenant vary sharply by jurisdiction; the non_solicitation_months period is drafted against the governing-law state, so a change of state may render it void or require reduction.
- `no-license` **relies-on** `confidentiality-obligations` — The no-license clause preserves all IP but expressly carves out 'the limited right to use the Confidential Information for the Purpose as permitted under' the obligations clause; if the permitted-use scope in the obligations changes, the sole license exception here changes with it.

Editing a depended-on clause will flag its dependents for review with these notes; no configuration can render a clause without the clauses it is subject-to.

## Diagnosis (from decompilation)

- nda_02.txt: Section 5 non-solicitation contains an internally inconsistent time frame: it references both 'twelve (12) months following disclosure of Confidential Information identifying the Discloser's personnel' and 'in no event longer than twelve (12) months after the Effective Date of the Recipient's last receipt of Confidential Information' — the phrase 'Effective Date of the Recipient's last receipt' is ambiguous and appears to conflate the defined Effective Date with the date of last receipt.
- nda_02.txt: Section 8 survival of 'three (3) years from the date of disclosure' may extend beyond the three-year term of the Agreement, creating potential tension with the term clause.
- nda_07.txt: Section 6 references certification 'in accordance with Section 12', but there is no Section 12 in this Agreement (the document ends at Section 9).
- nda_07.txt: Section 6 requires certification of destruction 'in writing' but the referenced Section 12 that would govern this does not exist.
- nda_09.txt: The term "Representatives" is defined in Section 1.2 but is never used anywhere else in the Agreement.
- nda_09.txt: Section 2's confidentiality obligations permit disclosure only with prior written consent, but the definition of Representatives suggests an intended (but absent) carve-out allowing disclosure to Representatives.
- nda_12.txt: Section 6 contains a leftover placeholder/instruction: the survival period is stated as "[NOTE: confirm survival period]" rather than an actual duration.
- outline: nda_07[8] 'Remedies; Governing Law' combines two functions into one clause; it is mapped to the remedies entry, so nda_07 has no separate governing-law match.
- outline: nda_09[5] and nda_10[6] are titled 'No License; Return of Materials' and combine the no-license and return/destruction functions; they are mapped to the no-license entry, so those two sources have no separate return-destruction match.
- outline: nda_03 and nda_10 contain a distinct 'No Obligation; No Warranty' clause separate from their 'No License' clause, so a dedicated no-obligation-no-warranty entry was created (only two sources have it as a standalone clause; other sources fold this content into their combined no-license clauses).
- outline: nda_10[5] 'Residuals' is unique to that source and has its own entry.
- outline: nda_04 has no compelled-disclosure clause and nda_11 has no remedies/injunctive-relief clause; these entries simply lack matches for those sources.
- questionnaire-plan: Posture axis 'drafting_posture' read primarily from the exclusions divergence (nda_04 loses the public-knowledge exclusion only where disclosure is 'in breach of this Agreement' — narrower, discloser-favourable — vs nda_11 which excludes public knowledge regardless of breach — broader, recipient-favourable), reinforced by residuals appearing only in nda_10 and the 'may cause / would cause irreparable harm' variation in remedies (nda_01 vs nda_12). One shared axis covers exclusions, confidentiality-obligations, compelled-disclosure and remedies rather than a per-clause variable.
- questionnaire-plan: Most clause disagreements flagged at ~0.00 similarity are actually just terminology (Discloser/Recipient vs Disclosing Party/Receiving Party) and section-numbering/style differences, plus embedded deal facts (return certification period '10 days', governing-law state) — these are captured as deal-fact variables (return_certification_days, governing_law_state, jurisdiction_venue), not postures.
- questionnaire-plan: 'nda_type' (mutual vs unilateral) is the biggest structural axis: nda_02, nda_05, nda_08 and nda_11 are drafted one-directionally (a single Discloser/Recipient), while the rest are mutual. It also explains the non-solicitation divergence (nda_02 one-sided, protecting only the Discloser's personnel, vs nda_06 reciprocal 'neither Party shall'), so no separate non-solicitation posture variable is needed.
- questionnaire-plan: Non-solicitation duration differs (nda_02 vs nda_06) but that is a deal fact captured by non_solicitation_months, not a posture.
- questionnaire-plan: Governing law varies across NY, Delaware, California and Texas per preambles; kept as free-text state rather than a choice to avoid excluding a jurisdiction. Venue captured separately.
- questionnaire-plan: Deal-context dimension (investment/financing evaluation in nda_02/nda_03 vs commercial/supply relationships elsewhere) did not produce materially different drafting beyond the Purpose text and the mutual-vs-unilateral split, so it is captured by the 'purpose' string and 'nda_type' rather than a separate context variable.
- questionnaire-plan: Signatory name/title variables included solely because the signature block requires them; combine or drop if signatures are completed outside the template.
- questionnaire-plan: dropped 2 planned variable(s) no clause used: confidentiality_term, return_certification_days
- dependency-map: This is styled as a MUTUAL NDA, but the no-license clause fixes party_a as 'the Disclosing Party' and states all Confidential Information is party_a's property — inconsistent with the mutual, bilateral disclosure structure in the definitions and obligations clauses; verify whether one-way ownership is intended.
- dependency-map: There is no limitation-of-liability or indemnity clause in this template, so no classic subject-to cap relationship exists; the remedies clause is the principal enforcement lever and its efficacy is entirely contingent on the governing-law selection.
- dependency-map: Compelled-disclosure, exclusions and return-destruction each carve out or interact with the confidentiality obligations via explicit {{ref}} cross-references, so those mechanical links are not duplicated here, but an editor narrowing the obligations should still reconsider all three carve-outs.

## Validation

- [error] sweep: clause 'non-solicitation' is relies-on 'governing-law', which is excluded (Enforceability and permissible duration of the employee non-solicitation covenant vary sharply by jurisdiction; the non_solicitation_months period is drafted against the governing-law state, so a change of state may render it void or require reduction.) (e.g. with drafting_posture=discloser-favourable, include_assignment_merger_exception=True, include_compelled_disclosure=False, include_governing_law=False, include_no_obligation_no_warranty=True, include_non_solicitation=True, include_remedies=True, include_representatives=True, include_residuals=False, include_return_destruction=True, nda_type=unilateral)
- [error] sweep: clause 'remedies' is relies-on 'governing-law', which is excluded (The entitlement to injunctive relief and specific performance 'without the necessity of posting a bond or proving actual damages' depends on the chosen governing law and forum; some states will not honour a contractual waiver of bond or of the irreparable-harm showing, so changing the governing-law state can defeat this clause.) (e.g. with drafting_posture=discloser-favourable, include_assignment_merger_exception=True, include_compelled_disclosure=False, include_governing_law=False, include_no_obligation_no_warranty=True, include_non_solicitation=True, include_remedies=True, include_representatives=True, include_residuals=False, include_return_destruction=True, nda_type=unilateral)
- [error] sweep: clause 'confidentiality-obligations' is subject-to 'residuals', which is excluded (When included, the residuals clause overrides the confidentiality obligations 'notwithstanding' them, letting personnel freely use information retained in unaided memory. Any tightening of the confidentiality obligations is materially undercut by this carve-out; the two must be edited together.) (e.g. with drafting_posture=discloser-favourable, include_assignment_merger_exception=True, include_compelled_disclosure=False, include_governing_law=False, include_no_obligation_no_warranty=True, include_non_solicitation=True, include_remedies=True, include_representatives=True, include_residuals=False, include_return_destruction=True, nda_type=unilateral)

Configuration sweep: 200 of 3072 configurations rendered (sampled).

## Next steps

1. Fix any [error] findings (`tb edit ...`), re-check with `tb validate`.
2. Have a lawyer review each clause and the questionnaire.
3. Record sign-off: `tb approve <template> --by "Name"`.
4. Generate documents: `tb questions`, fill answers, `tb render`.
