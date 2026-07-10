# Build report — Confidentiality Agreement

Built from 4 contracts:

- nda_01.txt — *mutual deal; long-term commercial partners*
- nda_02.txt — *one-way disclosure to an investor doing diligence; key-personnel sensitivity, so a non-solicit was insisted on*
- nda_05.txt — *one-way disclosure to a print vendor; strict destruction certification required by our records policy*
- nda_10.txt — *mutual deal in hospitality tech; counterparty insisted on a residuals carve-out*

## Canonical outline

- `preamble` Preamble and Recitals — 2 variants
- `definitions` Definitions — 2 variants
- `confidentiality-obligations` Confidentiality Obligations
- `exclusions` Exclusions — 2 variants
- `compelled-disclosure` Compelled Disclosure
- `non-solicitation` Non-Solicitation (only when `include_non_solicitation`)
- `residuals` Residuals (only when `include_residuals`)
- `return-destruction` Return or Destruction of Materials
- `no-license-no-warranty` No License; No Warranty; No Obligation — 2 variants
- `term-survival` Term and Survival
- `injunctive-relief-remedies` Injunctive Relief; Remedies — 2 variants
- `governing-law` Governing Law; Jurisdiction
- `entire-agreement-miscellaneous` Entire Agreement; Miscellaneous — 2 variants
- `signature-block` Signature Block

## Questionnaire

- `is_mutual` (boolean): Is this a mutual NDA where both parties may disclose confidential information (as opposed to a one-way disclosure)?
- `effective_date` (string): What is the effective date of the Agreement?
- `party_a_name` (string): What is the full legal name of the first party (the disclosing party in a one-way deal)?
- `party_a_entity` (string): How is the first party's entity described (e.g. 'a Delaware corporation', 'a Delaware limited liability company')?
- `party_a_address` (string): What is the principal place of business of the first party?
- `party_a_defined_term` (string): In a mutual NDA, what short defined term is used for the first party (e.g. 'Meridian', 'Ondine')?
- `party_b_name` (string): What is the full legal name of the second party (the receiving party in a one-way deal)?
- `party_b_entity` (string): How is the second party's entity described (e.g. 'a Delaware corporation', 'a Delaware limited partnership')?
- `party_b_address` (string): What is the principal place of business of the second party?
- `party_b_defined_term` (string): In a mutual NDA, what short defined term is used for the second party (e.g. 'Callowhill', 'Ravel')?
- `purpose_description` (string): Describe the Purpose of the disclosure (e.g. 'a potential business relationship relating to data analytics services' or 'the Recipient's evaluation of a potential investment in the Discloser').
- `confidential_info_examples` (string): List the illustrative categories of Confidential Information for this deal (e.g. 'business plans, financial information, customer lists, pricing, product roadmaps, technical data, software, source code, algorithms and trade secrets').
- `require_written_proof` (boolean): Must the Receiving Party prove that an exclusion applies by written records (a stricter, more discloser-protective evidentiary standard)?
- `include_non_solicitation` (boolean): Should the Agreement include a non-solicitation clause restricting the Receiving Party from soliciting the Disclosing Party's personnel?
- `non_solicit_months` (number): For how many months (after disclosure / last receipt of Confidential Information) does the non-solicitation restriction apply?
- `include_residuals` (boolean): Should the Agreement include a residuals clause permitting the Receiving Party to use information retained in the unaided memory of its personnel?
- `term_years` (number): For how many years does the Agreement remain in effect from the Effective Date (before any earlier termination on notice)?
- `termination_notice_days` (number): How many days' prior written notice may either Party give to terminate the Agreement early?
- `survival_years` (number): For how many years after expiration or termination do the confidentiality obligations survive with respect to Confidential Information disclosed during the term?
- `governing_law_state` (string): Which U.S. state's laws govern the Agreement (e.g. 'New York', 'Delaware')?
- `jurisdiction_venue` (string): Where are the courts of exclusive jurisdiction located (e.g. 'New York County, New York' or 'the State of Delaware')?
- `include_assignment_successor_exception` (boolean): Should the assignment restriction include an exception permitting assignment to a successor in connection with a merger or sale of substantially all assets?
- `party_a_signatory_name` (string): What is the name of the individual signing on behalf of the first party?
- `party_a_signatory_title` (string): What is the title of the individual signing on behalf of the first party?
- `party_b_signatory_name` (string): What is the name of the individual signing on behalf of the second party?
- `party_b_signatory_title` (string): What is the title of the individual signing on behalf of the second party?

## Diagnosis (from decompilation)

- nda_02.txt: Section 5 (Non-Solicitation) contains an internally inconsistent time period: it references both "twelve (12) months following disclosure of Confidential Information identifying the Discloser's personnel" and "in no event longer than twelve (12) months after the Effective Date of the Recipient's last receipt of Confidential Information" — using "Effective Date" loosely in a way that conflicts with the defined Effective Date of January 15, 2022.
- nda_02.txt: Section 8 survival period for Sections 2 through 6 (three years from date of disclosure) potentially conflicts with the overall three-year term; obligations could survive beyond termination for information disclosed late in the term, which may be intended but is not clearly reconciled.
- nda_10.txt: The Section 5 residuals carve-out ('Notwithstanding Section 3') is not cross-referenced from the survival provision (Section 7) or the exclusions (Section 2), which may create tension: residuals use survives but the survival clause only references Sections 3 and 4.
- nda_10.txt: Section 7 survival references only Sections 3 and 4, but Section 6 (Return of Materials) and its continuing obligations are not expressly carried into the survival period.
- outline: Clause order for confidentiality vs. exclusions varies: nda_01/nda_02 place confidentiality obligations before exclusions, while nda_05/nda_10 reverse them. Canonical order follows the preamble-then-definitions grouping.
- outline: nda_10 [6] 'No License; Return of Materials' combines two functions; it is mapped to the return-destruction entry, while its no-license/no-warranty function is separately covered by nda_10 [8].
- outline: Non-solicitation (nda_02 [5]) and residuals (nda_10 [5]) are deal-specific carve-outs appearing in only one source each.

## Validation

All gates passed.

Configuration sweep: 32 of 32 configurations rendered (exhaustive).

## Next steps

1. Fix any [error] findings (`tb edit ...`), re-check with `tb validate`.
2. Have a lawyer review each clause and the questionnaire.
3. Record sign-off: `tb approve <template> --by "Name"`.
4. Generate documents: `tb questions`, fill answers, `tb render`.
