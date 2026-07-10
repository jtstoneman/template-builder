# Build report — Confidentiality Agreement

Built from 4 contracts:

- nda_01.txt — *mutual deal; long-term commercial partners*
- nda_02.txt — *one-way disclosure to an investor doing diligence; key-personnel sensitivity, so a non-solicit was insisted on*
- nda_05.txt — *one-way disclosure to a print vendor; strict destruction certification required by our records policy*
- nda_10.txt — *mutual deal in hospitality tech; counterparty insisted on a residuals carve-out*

## Canonical outline

- `preamble` Preamble and Purpose — 2 variants
- `definitions` Definitions — 2 variants
- `confidentiality-obligations` Confidentiality Obligations
- `exclusions` Exclusions — 2 variants
- `compelled-disclosure` Compelled Disclosure
- `non-solicitation` Non-Solicitation (only when `include_non_solicitation`)
- `residuals` Residuals (only when `include_residuals`)
- `return-destruction` Return or Destruction of Materials (only when `include_return_destruction`) — 2 variants
- `no-license-no-warranty` No License; No Warranty; No Obligation — 2 variants
- `term-survival` Term and Survival — 2 variants
- `remedies-injunctive-relief` Remedies; Injunctive Relief — 2 variants
- `governing-law` Governing Law; Jurisdiction
- `entire-agreement-miscellaneous` Entire Agreement; Miscellaneous — 2 variants
- `signature-block` Signature Block

## Questionnaire

- `party_a_name` (string): What is the full legal name of the first party (the disclosing party in a one-way NDA)?
- `party_a_short_name` (string): What defined short name is used for the first party (e.g., 'Meridian', or 'Discloser')?
- `party_a_description` (string): How is the first party described (e.g., 'a Delaware corporation')?
- `party_a_address` (string): What is the principal place of business of the first party (leave blank if not stated)?
- `party_b_name` (string): What is the full legal name of the second party (the receiving party in a one-way NDA)?
- `party_b_short_name` (string): What defined short name is used for the second party (e.g., 'Callowhill', or 'Recipient')?
- `party_b_description` (string): How is the second party described (e.g., 'a Delaware limited liability company')?
- `party_b_address` (string): What is the principal place of business of the second party (leave blank if not stated)?
- `effective_date` (string): What is the effective date of the Agreement?
- `purpose_description` (string): How is the Purpose described (e.g., 'a potential business relationship relating to data analytics services')?
- `disclosure_direction` (choice): Is confidential information disclosed by both parties (mutual) or only by one party (one-way)? — one of ['mutual', 'one-way']
- `governing_law_state` (string): Which U.S. state's law governs the Agreement and provides the forum for disputes?
- `term_years` (number): How many years does the Agreement remain in effect (the term, before survival of obligations)?
- `confidentiality_survival_years` (number): For how many years after termination/expiry do the confidentiality obligations survive?
- `include_non_solicitation` (boolean): Should the Agreement include a Non-Solicitation clause (e.g., where key-personnel sensitivity exists)?
- `include_residuals` (boolean): Should the Agreement include a Residuals carve-out clause?
- `include_return_destruction` (boolean): Should the Agreement include a Return or Destruction of Materials clause?
- `require_destruction_certification` (boolean): Must the receiving party provide written certification of destruction of confidential materials?
- `confidential_information_examples` (string): What illustrative categories of Confidential Information should be listed (comma-separated, e.g., 'business plans, financial information, customer lists, pricing, product roadmaps, technical data, software, source code, algorithms, trade secrets')?
- `non_solicitation_months` (number): For how many months after disclosure of personnel-identifying Confidential Information does the non-solicitation restriction apply?
- `allow_assignment_to_successor` (boolean): Should the no-assignment restriction include a carve-out permitting assignment to a successor in connection with a merger or sale of substantially all assets?
- `party_a_signatory_name` (string): What is the name of the individual signing on behalf of the first party?
- `party_a_signatory_title` (string): What is the title of the individual signing on behalf of the first party?
- `party_b_signatory_name` (string): What is the name of the individual signing on behalf of the second party?
- `party_b_signatory_title` (string): What is the title of the individual signing on behalf of the second party?

## Diagnosis (from decompilation)

- nda_02.txt: Section 5 non-solicit contains an internal inconsistency: the period runs "for twelve (12) months following disclosure of Confidential Information identifying the Discloser's personnel" but is then capped at "no event longer than twelve (12) months after the Effective Date of the Recipient's last receipt of Confidential Information" — the phrase "Effective Date of the Recipient's last receipt" is confused/awkward wording conflating the defined Effective Date with the date of last receipt.
- nda_02.txt: Section 8 survival: obligations under Sections 2 through 6 survive, but Section 5 (Non-Solicitation) has its own shorter 12-month term, creating a potential conflict with the 3-year survival period stated in Section 8.
- outline: nda_10.txt combines the return/destruction of materials obligation into its 'No License; Return of Materials' clause [6] and adds a separate 'No Warranty; No Obligation' clause [8]; both are grouped under the no-license-no-warranty entry, so nda_10 has no standalone return-destruction clause.
- outline: Non-solicitation (nda_02[5]) and residuals (nda_10[5]) are deal-specific clauses appearing in only one source each but retained as distinct optional template sections.
- questionnaire-plan: Party naming is symmetric (party_a_* / party_b_*). In the one-way sources (nda_02, nda_05) party_a maps to the 'Discloser' and party_b to the 'Recipient'; in mutual sources (nda_01, nda_10) they are simply the two Parties. The disclosure_direction choice drives whether the drafting uses mutual or Discloser/Recipient language and controls which party's obligations are reciprocal.
- questionnaire-plan: disclosure_direction inferred: nda_01 and nda_10 are mutual ('each Party may disclose'); nda_02 (investor diligence) and nda_05 (print vendor) are one-way ('the Discloser may disclose').
- questionnaire-plan: include_non_solicitation: clause appears only in nda_02, driven by that deal's key-personnel sensitivity during investor diligence. Optional toggle.
- questionnaire-plan: include_residuals: clause appears only in nda_10, where the hospitality-tech counterparty insisted on a residuals carve-out. Optional toggle.
- questionnaire-plan: include_return_destruction: clause appears in 3/4 sources (absent from nda_10, which relies on residuals instead). Optional toggle.
- questionnaire-plan: require_destruction_certification: separate from the return/destruction toggle because nda_05's records policy demanded strict written certification — a drafting dimension that intensifies an otherwise standard clause. Only meaningful when include_return_destruction is true.
- questionnaire-plan: party_a_address / party_b_address are optional (blank when not stated) — nda_01 and nda_05 omit addresses while nda_02 and nda_10 include principal place of business.
- questionnaire-plan: Governing law captured as a single state variable; all four sources use Delaware entities but the forum state may differ (Delaware/New York/Massachusetts appear across preambles), so the lawyer should confirm the actual governing-law state.
- questionnaire-plan: Kept term_years and confidentiality_survival_years as separate numbers because NDAs commonly set a short term but a longer survival period for confidentiality obligations.

## Validation

All gates passed.

Configuration sweep: 64 of 64 configurations rendered (exhaustive).

## Next steps

1. Fix any [error] findings (`tb edit ...`), re-check with `tb validate`.
2. Have a lawyer review each clause and the questionnaire.
3. Record sign-off: `tb approve <template> --by "Name"`.
4. Generate documents: `tb questions`, fill answers, `tb render`.
