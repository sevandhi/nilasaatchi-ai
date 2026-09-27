# Doc-type and scheme-relevance evaluation set (Phase 1, T1.6)

`labels.csv` has 100 PDFs, one row each. Columns: `sha256, path, folder_label, doc_type, scheme_relevance, confidence (high|medium|low), note`. Each note starts with the sample id (`[c000]`–`[c099]`).

## Protocol
- The independent evaluator labelled these on 2026-09-26.
- **Sampling.** Python `random.Random(20260926)`. **Seed = 20260926.**
  - Folders were processed in sorted order. Each folder's sorted file list was shuffled, then drawn from.
  - A file was skipped if its sha256 was already in the sample. So all 100 rows are distinct contents; the set is deduplicated across folders.
  - Forced includes: `3(1) Published/527_Gazette_Block_1.pdf` and `3(1) Published/36_Ex_VI_1_2025__1___1_.pdf`.
- **Allocation.** All 26 folders are covered (23 stage folders + GO/AS/LPS).
  - GO, AS and LPS: 1 each. That is the whole folder.
  - Amount Disbursed: 10.
  - 6 each: 7(2) Award, Court Deposit, DLPNC, Exemption GO Approved, Exemption Initiated.
  - SLPNC: 5. The folder has only 5 unique contents; its 113 files are copies of them.
  - 4 each: 3(1) Published, 3(2) Errata, Amount Disbursed 7(2)&7(3), Form E, Form F.
  - 3(2) Noticed: 2. That is the whole folder.
  - 3 each: every other folder.
- **Rendering.** `pdftoppm -r 90 -f 1 -l 2` gave the first 2 pages. I read those images with the Read tool and labelled from the images only. I used no OCR, no text layer and no model output.
  - For 527_Gazette I also read pages 3–4, because the solar notice begins on p4.
  - `Form F/F_form_201_1A (2).pdf` is **truncated**: there is no xref or trailer, and pdftoppm/pdfimages fail. I carved the embedded JPEG streams out of the raw bytes and labelled from those.
- **Doc-level rule.** `doc_type` is what the first pages of the file mainly are. Composite files are described in the note.
- **Personal data.** I removed individual demand-draft and payment amounts from the notes. Per-acre rates and scheme-level totals are kept. No owner names appear. The sha256 and path columns are not PII.
- **Few-shot disjointness.** GO.pdf, AS.pdf and LPS.pdf are also in the golden mini set (`eval/golden`); they are the only files in their folders. None of these 100 files may be used as a prompt few-shot example.

## Class counts (n = 100)
| doc_type | n | | doc_type | n |
|---|---|---|---|---|
| AWARD_7_2 | 16 | | SEC32_ERRATA | 4 |
| SLPNC | 11 | | FORM_E | 4 |
| SEC32_NOTICE | 7 | | AWARD_7_3 | 3 |
| EXEMPTION_PROPOSAL | 7 | | EXEMPTION_GO | 3 |
| SEC31_GAZETTE | 6 | | FUNDS | 3 |
| FORM_F | 6 | | CHITTA | 3 |
| BANK_INSTRUMENT | 6 | | OTHER | 3 |
| COURT_DEPOSIT | 6 | | LAND_VALUE | 2 |
| LDR | 6 | | GO / AS / DLPNC / DISBURSEMENT | 1 each |

Classes with **0** examples: LPS and COVERING_LETTER. LPS.pdf opens with the AS letter; see below.

- Confidence: 68 high, 32 medium, 0 low.
- scheme_relevance at doc level: 100 `allikulam`, 0 `other_scheme`, 0 `unknown`. See "Scheme relevance" below.

## Folder vs type disagreements: 33 of 100
The expected type is the folder's nominal type. LDR Issued and Possession Taken both map to LDR.

| id | folder | labelled | why |
|---|---|---|---|
| c000 | 3(1) Publish | SEC32_NOTICE | Individual s.3(2) Form A notices. A byte-copy also sits in 3(2) Notice |
| c021 | 7(2) Award | FORM_F | FformBlock42SurNo406 (skill §3 example) |
| c022 | 7(2) Awarded | FORM_F | Form F agreement |
| c028 | AS | OTHER | Tamil AS *proposal* (911.39.50 ha), not the AS letter (golden g02) |
| c029–c031, c034, c036, c038 | Amount Disbursed | BANK_INSTRUMENT | IOB demand drafts payable to the Principal District Judge, i.e. court deposits |
| c032, c035, c037 | Amount Disbursed | AWARD_7_2 | s.7(2) award proceedings |
| c039 | Amount Disbursed 7(2)&7(3) | COURT_DEPOSIT | Letter to the District Judge depositing 7(3) compensation |
| c040–c042 | Amount Disbursed 7(2)&7(3) | AWARD_7_2 | 7(2) awards |
| c043–c045 | Amount Disbursed Pattadars | AWARD_7_2 | 7(2) awards. c043 also has a byte-copy in LDR Issued |
| c051 | Court Deposit | OTHER | Compensation calculation sheet (an enclosure) |
| c052–c057 | DLPNC | SLPNC | "Notes for approval of land value by SLPNC as determined by DLPNC", plus the Collector's SLPNC proposal letter. The DLPNC folder holds no DLPNC minutes in this sample |
| c058, c062 | Exemption GO Approved | EXEMPTION_PROPOSAL | SIPCOT letters (recommendation or objection remarks), not a GO |
| c060 | Exemption GO Approved | SEC32_NOTICE | 3(2) newspaper schedule. A copy also sits in 3(2) Notice |
| c066 | Exemption Initiated | OTHER | Collector order reclassifying a pond survey as poramboke |
| c085 | LPS | AS | pp1–2 of LPS.pdf are the English AS letter (904.40.0 ha). The LPS follows |
| c086 | Land Value Fixation | DLPNC | Collector proceedings recording a DLPNC meeting of 12.11.2024 that fixes ₹6,88,000/acre |

Folders whose sample agreed 100% with the folder name: 3(1) Published, 3(2) Errata/Notice/Noticed, 7(3) Awarded, Form E, Form F, Funds-Allocated, LDR Issued, Possession Taken, Patta Transferred, SLPNC, and GO.
- "Amount Disbursed*" agreed **1 of 17**. Only c033, a treasury payment advice with owner bank credits, is a true DISBURSEMENT.
- DLPNC agreed **0 of 6**.

## Scheme relevance: the 527 gazette is mixed, not purely other_scheme
- **c003 `527_Gazette_Block_1.pdf`**, byte-identical to `31Published_Gazette_Block_1.pdf`. It is Gazette No.527 dated 28.11.2022.
  - p1 is an unrelated Chennai (Tondiarpet railway) errata.
  - pp2–4 are a Form C s.3(1) notice for **SIPCOT Allikulam Scheme, Ramasamypuram, Unit 2, Block 1, 10.2300 ha**.
  - From p4 the notice is for **"SIPCOT Establishment of Solar Power Plant"**, Tirunelveli district, Manur taluk, Chittarchatram village, Unit 02 Block 02, 9.7500 ha.
  - Labelled `allikulam` / medium / needs_human. A single doc-level value cannot represent this file. **Scheme relevance must be tagged per notification (per page range), not per file.**
- c001 (No.527, p1 Chennai errata) and c002 (No.525, p1 Tiruchirappalli highway ROB notice) also carry non-SIPCOT notices on the cover page. Their Allikulam notices are on later pages.
- c004 (Gazette No.36, 24.01.2025) names the project **"Formation of Oil Refinery Project by SIPCOT"** instead of "SIPCOT Allikulam Scheme". It is still Allikulam (Allikulam village, U9 B1). Scheme matching must accept both names.
- Among the 100 files, none is **wholly** other_scheme.

## Low-confidence and needs_human list
There are no `low` labels. These `medium` labels depend on a taxonomy decision rather than on reading the page:

| ids | question |
|---|---|
| c003 | Doc-level scheme label for a mixed gazette issue. Recommend per-notification tagging |
| c028 | Tamil AS proposal: OTHER, AS, or a new AS_PROPOSAL class? |
| c029–c031, c034, c036, c038 | DD to the District Judge: BANK_INSTRUMENT (form) or COURT_DEPOSIT (function)? Golden g16 used COURT_DEPOSIT |
| c079, c080 | E-challan and treasury payment advice for fund transfer: FUNDS or BANK_INSTRUMENT? |
| c046, c048, c050 (and c039) | Files named "Covering_Letter" that are court-deposit letters to the Judge: COURT_DEPOSIT or COVERING_LETTER? |
| c052–c057, c095, c097–c099 | "SLPNC approval notes as determined by DLPNC" and the SLPNC proposal letter: SLPNC or DLPNC? |
| c096 | G.O.(Ms) No.29 (08.03.2024) approving SLPNC flat rates: SLPNC, GO or LAND_VALUE? |
| c086 | Collector proceedings recording DLPNC and fixing the rate: DLPNC or LAND_VALUE? |
| c051 | Compensation calculation sheet: OTHER or COURT_DEPOSIT annex? |
| c066 | Land-classification change order: OTHER, or does it belong under EXEMPTION? |
| c010 | Composite file: original 3(2) newspaper notice (p1) plus errata (p2) |
| c012 | 3(2) schedule without the notice text |
| c085 | LPS.pdf opens with the AS letter, so LPS has no pure example |
| c062 | SIPCOT remarks on 3(2) objections filed as "Exemption GO Approved Letter" |

## Taxonomy problems found
1. **Form vs function.** BANK_INSTRUMENT overlaps COURT_DEPOSIT and DISBURSEMENT. FUNDS overlaps treasury instruments.
   - Recommendation: `doc_type` should be the form (BANK_INSTRUMENT), with a separate `payee_kind` field (court / owner / treasury) that derives the stage.
   - Alternatively, fold DDs to the District Judge into COURT_DEPOSIT, as golden g16 did.
2. **The DLPNC / SLPNC / LAND_VALUE boundary is blurred.** Each SLPNC note says "as determined by DLPNC". GO 29 records the SLPNC outcome. Land-value-fixation proceedings cite the DLPNC. The committee or level should be a field, not three classes.
3. **Missing classes:**
   - an AS *proposal*
   - a compensation calculation sheet
   - a land-classification change order
   - SIPCOT correspondence on objections
   These are currently OTHER. Consider AS_PROPOSAL and CORRESPONDENCE.
4. **Composite files are common.** Examples: notice + errata (c010), AS letter + LPS (c085), multi-notification gazettes (c001–c003). The classifier needs page-level output, with a doc-level majority vote.
5. **COVERING_LETTER is ambiguous.** Files named "Covering_Letter_*" are substantive court-deposit letters. With no clear examples, this class may be unnecessary.
6. **Heavy byte duplication.** Examples: SLPNC has 5 unique contents in 113 files, and GO29 alone has 76 copies. DLPNC has 10 unique in 78; Funds-Allocated 3 unique in 17.
   - Evaluate classifier accuracy on unique sha256, not on files, or copies will inflate the score.
