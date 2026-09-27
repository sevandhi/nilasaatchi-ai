# Held-out extraction test set (12 pages)

`labels.jsonl` has 12 labelled pages, one JSON object per line. `pages.csv` lists the page sources. The images are `pages/{page_id}.png` (300 dpi) and `pages/{page_id}_preview.png` (110 dpi).

> **Personal data and secrecy.**
> - `labels.jsonl` and `pages/` contain owner names, patta numbers and amounts. Both are git-ignored (`.gitignore` lines `eval/heldout/pages/` and `eval/heldout/labels.jsonl`) and must stay local (land-domain-knowledge §13).
> - **This is a held-out set.** Do not show these labels to any implementing agent. Do not put them, or these pages, into a prompt or a few-shot example. Do not tune on them. Only the evaluator runs the scorer against them.

## Selection
- **Built:** 2026-09-26 by the independent evaluator. **Seed `20260927`** (`random.Random(20260927)`).
- **Candidate pools**, one per type, taken from folders under `Dataset/Land_documents/LA_Patta_land_Documents/`. Paths were sorted, then shuffled with the seeded RNG, in this order:
  - AWARD_7_2: `7(2) Award` + `7(2) Awarded`
  - AWARD_7_3: `7(3) Awarded`
  - FORM_F: `Form F`
  - LDR: `LDR Issued` + `Possession Taken`
  - CHITTA: `Patta Transferred`
  - DISBURSEMENT: `Court Deposit` + `Amount Disbursed Pattadars 7(2) & 7(3)` + `Amount Disbursed 7(2) & 7(3)` + `Amount Disbursed`
- **Disjointness.** A PDF was skipped if its sha256 matched any of the following:
  - (a) any sha256 in `eval/classify/labels.csv`
  - (b) the sha256 of any source PDF in `eval/golden/mini_pages.csv`, which also catches byte-identical copies in other folders
  - (c) a PDF already taken for this set

  Verified after the build: 0 overlaps with (a), 0 with (b), and 12 unique PDFs. Nothing in `prompts/`, `app/` or `pipeline/` references these files.
- **Page choice.** Each type takes the first N surviving PDFs in seeded order. I looked at a 30-dpi contact sheet of each PDF and took the page with the main land, compensation or payment table. Prose-only pages were skipped. Two page choices were deliberate:
  - h07: the Form F *schedule* page (survey/extent), not the apportionment page, because the mini set already has two apportionment pages (g09, g10).
  - h12: the beneficiary table (p3), not the treasury cover (p1).

| page | hint | source (under `LA_Patta_land_Documents/`) | p |
|---|---|---|---|
| h01 | AWARD_7_2 | 7(2) Award/awardblock9.pdf | 4 |
| h02 | AWARD_7_2 | 7(2) Awarded/b_2_a6_.pdf | 12 |
| h03 | AWARD_7_2 | 7(2) Awarded/UNIT_7_Proceedings_Block_5__Award_3.pdf | 5 |
| h04 | AWARD_7_3 | 7(3) Awarded/Block_9_final_Draft.pdf | 5 |
| h05 | AWARD_7_3 | 7(3) Awarded/Block__6.pdf | 4 |
| h06 | FORM_F | Form F/3Fformblock4.pdf | 2 |
| h07 | FORM_F | Form F/49.pdf | 5 |
| h08 | LDR | LDR Issued/Block__8_LDR_1.pdf | 1 |
| h09 | LDR | LDR Issued/LDR__3____21 (7).pdf | 1 |
| h10 | CHITTA | Patta Transferred/3626_patta_28 (5).pdf | 2 |
| h11 | COURT_DEPOSIT | Court Deposit/Covering_Letter_214_2.pdf | 7 |
| h12 | DISBURSEMENT | Amount Disbursed/Bill2 (4).pdf | 3 |

## Labelling protocol
This set uses the same protocol and schema as `eval/golden/README.md`.
- Labelled **from the page images only**. I read the 110-dpi preview, then zoomed into crops of the 300-dpi PNG. No OCR, no text layer and no model was used.
- h05 is a continuation page with no column headers. For h05 only, I also viewed the previous page's header image (p3 of the same PDF) to learn what the columns mean.
- The row schema, the header fields, `extent_ha` from hectare notation only, integer `amount_rs`, owners joined with ` ; `, and DRY for புன்செய் / "Dry Land" all follow the golden README.
- A merged table cell is repeated on every row it covers:
  - h02 repeats survey and extent across the owner rows. **Do not sum `extent_ha` over h02 rows.** Use `printed_totals`.
- Header fields are `null` when they are not printed on the page. A scheme name ("அல்லிக்குளம் எண்ணெய் சுத்திகரிப்பு ஆலை உருவாக்கம்") is **not** a village. A file name is never used as a source.
- New extra keys (a scorer should ignore any key it does not know):
  - `table` (`trees`, `compensation`, `parcel`, `block_summary`, `pattadar`, `schedule`)
  - `share`, `land_amount_rs`, `tree_amount_rs`
  - `item`
  - `notified_ha`
  - `continuation`, `continued_next_page`
  - `mutation_ref`, `signed_by`, `signed_date` (chitta remarks; these give the mutation dates)
  - `owner_raw`, `award_ref`
  - `ifsc`, `bank`
  - `printed_totals.amount_words`, `pattadar_amount_words`
- **Privacy:** bank account numbers on h12 were deliberately **not** transcribed.

## Counts
| doc_type | pages | rows | uncertain fields |
|---|---|---|---|
| AWARD_7_2 | 3 | 29 | 0 |
| AWARD_7_3 | 2 | 14 | 0 |
| FORM_F | 2 | 9 | 1 |
| LDR | 2 | 24 | 1 |
| CHITTA | 1 | 7 | 0 |
| COURT_DEPOSIT | 1 | 1 | 1 |
| DISBURSEMENT | 1 | 7 | 0 |
| **Total** | **12** | **91** | **3** |

Every content label matches its `doc_type_hint`. h04 is a 7(3) award page that tabulates the lands **already paid under 7(2)**, the same trap as g08.

## needs_human items (3 pages)
| page | field | why |
|---|---|---|
| h06 | header.block_no | The page prints "No.A5/24 /2024 - Block 4 - 1, SIPCOT, Unit-5". I labelled the block as `4`. Is the "- 1" a sub-block or a batch/award suffix? |
| h08 | header.doc_no | Handwritten "Roc No: A6/02/2021". The "A6/02" reading is uncertain, and a year of 2021 is odd on a receipt dated 02.12.2024. |
| h11 | rows[0].owner | The owner cell is generic ("persons shown in the patta and persons claiming title"). I set `owner = null` and put the text in `owner_raw`. Please confirm this convention. |

## Findings for the scorer and verifier (document traps)
1. **Amount in words ≠ figures:**
   - h03: the words give ₹25,48,000 but the figures give ₹25,41,000.
   - h11: the words give ₹85,527 but the figures give ₹53,771.

   Both are labelled with the figures. A words-vs-figures check would catch them.
2. **Column sum ≠ printed total:** in h02 the 17 amounts sum to ₹27,53,600, but the printed total is ₹27,60,700.
3. **Hectare/acre conversion:**
   - h02: 0.295 ha is printed as 0.726 ac. This fits neither factor (0.729). Acre totals equal the sum of the rounded row values (h02 2.663, h04 14.398).
   - h01: 0.93 ha → 2.298 ac uses 2.47105, while other pages use 2.47. h04 prints the same parcel as 2.30.
4. **Per-block rates:** ₹9,00,000/acre (h01, block 9; h02), and **₹7,00,000/acre** (h03, Unit 7 Block 5). That makes a third rate after the ₹5 lakh and ₹9 lakh rates already seen.
5. **Two hectare columns:** h05 has two extent columns, and both are in **hectares**. Neither is in acres.
6. **Numbering defects:**
   - h08 prints serial "6" twice, and one row has a blank serial and survey cell (the survey carries over from the row above).
   - h05 starts with a continuation fragment and ends on a row that continues onto the next page.
7. **Village spelling and unit:**
   - "Perurani" (h09).
   - "மேலதட்டப்பாறை" without த் (h08 boundary).
   - "கீழதட்டப்பாறை" and "உமரிக்கோட்டை" (h11).
   - Peroorani appears under **Unit 4** in h11 but under Unit 5 in h09 and g04.
8. **Cross-document links (useful for matching tests):**
   - h10 (chitta) has exactly the same surveys and extents as g07 (Umarikottai U1 B1).
   - h09's 172/3–172/8 extents equal g19.
   - h01 and h04 cover the same Block 9 parcels (52/2, 53/1, 54/1, 54/2).
9. **Official date formats:**
   - The chitta remarks give mutation dates as dd/mm/yyyy (08/01/2025, 27/03/2025).
   - The treasury date is dd-mm-yyyy (04-10-2024).
