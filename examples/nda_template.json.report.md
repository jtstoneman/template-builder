# Build report — Non-Disclosure Agreement

Built from 12 contracts: nda_01.txt, nda_02.txt, nda_03.txt, nda_04.txt, nda_05.txt, nda_06.txt, nda_07.txt, nda_08.txt, nda_09.txt, nda_10.txt, nda_11.txt, nda_12.txt

## Canonical outline

- `preamble` Preamble, Recitals and Purpose — 2 variants
- `definitions` Definitions — 2 variants
- `confidentiality-obligations` Confidentiality Obligations
- `exclusions` Exclusions from Confidential Information
- `compelled-disclosure` Compelled Disclosure
- `non-solicitation` Non-Solicitation (only when `include_non_solicitation`) — 2 variants
- `residuals` Residuals (only when `include_residuals`)
- `no-license` No License; Ownership of Confidential Information
- `return-destruction` Return or Destruction of Materials
- `no-warranty-no-obligation` No Warranty; No Obligation to Disclose or Proceed
- `term-survival` Term and Survival
- `remedies-injunctive-relief` Remedies; Injunctive/Equitable Relief — 2 variants
- `governing-law` Governing Law; Jurisdiction
- `miscellaneous` Entire Agreement; Miscellaneous / General Provisions — 2 variants
- `signature-block` Signature Block

## Questionnaire

- `is_mutual` (boolean): Is this a mutual NDA (both parties may disclose), rather than a one-way NDA with a designated Discloser and Recipient?
- `effective_date` (string): What is the effective date of the Agreement?
- `party_a_name` (string): What is the full legal name of the first party (the Discloser in a one-way NDA)?
- `party_a_description` (string): How is the first party described (entity type/jurisdiction and, if used, principal place of business)? E.g. 'a Delaware corporation with its principal place of business at 400 Beacon Wharf Drive, Wilmington, Delaware 19801'.
- `party_a_short_name` (string): What defined short name is used for the first party in a mutual NDA (e.g. 'Meridian')?
- `party_b_name` (string): What is the full legal name of the second party (the Recipient in a one-way NDA)?
- `party_b_description` (string): How is the second party described (entity type/jurisdiction and, if used, principal place of business)?
- `party_b_short_name` (string): What defined short name is used for the second party in a mutual NDA (e.g. 'Callowhill')?
- `purpose_description` (string): What is the Purpose, phrased to follow the lead-in (e.g. 'a potential business relationship relating to data analytics services' or 'a potential investment in the Discloser')?
- `confidential_information_categories` (string): What is the industry-specific, non-exhaustive list of example categories of Confidential Information (phrased to follow 'including without limitation ...'; do not include the existence/terms of the discussions, which the template already appends)? E.g. 'business plans, financial statements and projections, pricing, customer and supplier lists, technical data, software, source code, and algorithms'.
- `include_non_solicitation` (boolean): Should the Agreement include a non-solicitation clause restricting the hiring/solicitation of the other party's employees?
- `non_solicitation_months` (number): For how many months after expiration or termination of the Agreement does the non-solicitation restriction last?
- `include_residuals` (boolean): Should the Agreement include a residuals clause permitting use of information retained in the unaided memory of the Receiving Party's personnel?
- `term_years` (number): For how many years does the Agreement continue from the Effective Date (before any earlier termination)?
- `termination_notice_days` (number): How many days' prior written notice must a Party give to terminate the Agreement early?
- `survival_years` (number): For how many years after expiration or termination do the confidentiality obligations survive (for information other than trade secrets)?
- `governing_law_state` (string): Which U.S. state's law governs the Agreement (state name only, e.g. 'New York')?
- `courts_venue` (string): Where are the exclusive-jurisdiction courts located (e.g. 'New York County, New York', 'New Castle County, Delaware', or 'the State of Delaware')?
- `include_assignment_successor_exception` (boolean): Should the anti-assignment provision include an exception permitting assignment to a successor in connection with a merger or sale of substantially all assets?
- `party_a_signatory_name` (string): What is the name of the individual signing on behalf of the first party?
- `party_a_signatory_title` (string): What is the title of the individual signing on behalf of the first party (e.g. 'Chief Operating Officer')?
- `party_b_signatory_name` (string): What is the name of the individual signing on behalf of the second party?
- `party_b_signatory_title` (string): What is the title of the individual signing on behalf of the second party (e.g. 'Managing Member')?

## Diagnosis (from decompilation)

- nda_02.txt: Section 5 (Non-Solicitation) contains an internally inconsistent duration: it states the restriction lasts for twelve months following disclosure but 'in no event longer than twelve (12) months after the Effective Date of the Recipient's last receipt of Confidential Information' — a confusingly worded and potentially contradictory time limit.
- nda_02.txt: Section 8 provides a three-year survival period for Sections 2 through 6 while the Agreement's own term is three years, and the survival is measured from date of disclosure, creating potential ambiguity with the compelled-disclosure and return obligations.
- nda_07.txt: Section 6 refers to certifying destruction "in accordance with Section 12," but there is no Section 12 in this Agreement (the document ends at Section 9). The correct cross-reference is likely to Section 6 itself or a nonexistent certification section.
- nda_09.txt: The term "Representatives" is defined in Section 1.2 but never used elsewhere in the Agreement; the confidentiality obligations in Section 2 do not permit disclosure to or address Representatives.
- nda_12.txt: Section 6 (Term and Survival) contains a leftover placeholder '[NOTE: confirm survival period]' where the survival period should be specified.
- outline: nda_04 has no compelled-disclosure clause; nda_11 has no standalone remedies/injunctive clause.
- outline: nda_07 clause [8] 'Remedies; Governing Law' combines two functions; assigned to remedies-injunctive-relief, so nda_07 has no separate governing-law match (governing law is covered within that combined clause).
- outline: nda_09 [5] and nda_10 [6] are combined 'No License; Return of Materials' clauses; assigned to no-license, and thus also carry the return/destruction function for those two documents.
- outline: Combined 'No License; No Obligation' / 'No License; No Warranty' headings are grouped under no-license; standalone 'No Obligation; No Warranty' / 'No Warranty; No Obligation' clauses (nda_03[8], nda_10[8]) are treated as a distinct no-warranty-no-obligation function.
- outline: residuals is a rare function appearing only in nda_10.

## Validation

All gates passed.

Configuration sweep: 16 of 16 configurations rendered (exhaustive).

## Next steps

1. Fix any [error] findings (`tb edit ...`), re-check with `tb validate`.
2. Have a lawyer review each clause and the questionnaire.
3. Record sign-off: `tb approve <template> --by "Name"`.
4. Generate documents: `tb questions`, fill answers, `tb render`.
