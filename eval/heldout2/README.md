# Held-out extraction set 2 (10 pages, fresh)

`labels.jsonl` holds 10 labelled pages, one JSON object per line. `pages.csv` lists the page sources (page_id, doc_type_hint, source_pdf, page_no). The images are `pages/{id}.png` (300 dpi) and `pages/{id}_preview.png` (110 dpi), rendered with `pdftoppm` as the PDF presents them. **k01 is not pre-rotated**: rotate it 90° clockwise (PIL `rotate(270, expand=True)`).

> **HELD-OUT.** Nobody but the evaluator has seen these pages. Do not show them or their labels to any engineer or prompt, and do not use them as few-shot examples. Report scores only.
>
> **Personal data.** `labels.jsonl` and `pages/` hold owner names, patta numbers and amounts (land-domain-knowledge §13). `eval/heldout2/.gitignore` ignores both. The page images also show an Aadhaar number (k07) and a bank account number (k10). **Neither was transcribed.** This README quotes no owner names.

## Selection (built 2026-09-27 by the independent evaluator)
- **Pools:** sorted PDF paths under `Dataset/Land_documents/LA_Patta_land_Documents/`, grouped by folder:
  - AWARD_7_2: `7(2) Award` + `7(2) Awarded`
  - AWARD_7_3: `7(3) Awarded`
  - FORM_F: `Form F`
  - LDR: `LDR Issued` + `Possession Taken`
  - CHITTA: `Patta Transferred`
  - DISBURSEMENT: the three `Amount Disbursed*` folders
- **Exclusions:** any PDF whose sha256 matches `eval/golden/mini_pages.csv`, `eval/heldout/pages.csv`, `eval/dev/pages.csv`, `eval/dev2/pages.csv` or `eval/classify/labels.csv`. Within-pool byte duplicates were also dropped.
- **Order:** each pool was shuffled with `random.Random(20260930)`. I walked the shuffled list and took the first PDFs with a table page (for Form F, the table had to be on page 1 or 2).
- **Checked afterwards:** 0 sha256 overlaps with the excluded sets, and the 10 PDFs are pairwise distinct.
- **Skipped:**
  - `Form F/F_form_215_1.pdf`: pdfinfo cannot read it.
  - `Form F/b_5_s.pdf`: no table on p1 or p2.
  - `7(3) Awarded/Block_7__Final_Award.pdf`: the scan is illegible at digit level even at 300 dpi.
- **Page choice:** within 3block5.pdf I took p4 (three table kinds) instead of p5 (a summary continuation).

| page | doc_type | source (under `LA_Patta_land_Documents/`) | p | rows (by table) |
|---|---|---|---|---|
| k01 | AWARD_7_2 | 7(2) Award/2Block3.pdf | 3 | 1 parcel with **16 co-owners in one cell** + 3 heirs (sideways) |
| k02 | AWARD_7_2 | 7(2) Awarded/Block__7__3___1_.pdf | 11 | 4 block_summary |
| k03 | AWARD_7_2 | 7(2) Awarded/3block5.pdf | 4 | 1 trees + 2 compensation + 2 block_summary |
| k04 | AWARD_7_3 | 7(3) Awarded/Block__7_final_award.pdf | 9 | 1 parcel + 7 valuation |
| k05 | AWARD_7_3 | 7(3) Awarded/Block_9__Final_Award_F_C.pdf | 10 | 15 valuation (amounts with paise) |
| k06 | FORM_F | Form F/FormF2.pdf | 2 | 3 compensation |
| k07 | FORM_F | Form F/135.pdf | 1 | 1 compensation |
| k08 | LDR | Possession Taken/Block__1_24.pdf | 1 | 14 parcel with boundaries |
| k09 | CHITTA | Patta Transferred/3572Bnewpatta.pdf | 1 | 1 |
| k10 | DISBURSEMENT | Amount Disbursed/B567 (2).pdf | 3 | 1 beneficiary (ECS) |
| **Total** | | | | **56 rows** |

## Labelling protocol
The protocol and schema are the same as `eval/golden/README.md` and `eval/dev2/README.md`:
- **Sources:** labelled from the page images only. I read the 110-dpi preview, then PIL crops of the 300-dpi PNG. No OCR, no text layer, no model output, and no `data/` extraction output was opened.
- **Header fields:** `null` when not printed on the page. A scheme name is not a village. Unit and block numbers taken from a referenced award (k06, k07) are marked in `notes`.
- **Units:** `extent_ha` comes only from hectare notation (H.AA.SS, H.AA.SSS, H.AA.SSSS, or chitta `H - AA.SS`), or from a column whose header is in hectares. `extent_ac` is the printed value. `amount_rs` is an integer.
- **k05 paise:** `amount_rs` is the printed value rounded to an integer, and `amount_raw` keeps the paise.
- **Owners:** copied as printed, with list numbering removed and co-owners joined by ` ; `.
- **Extra keys** (a scorer should ignore unknown keys):
  - `table`: `parcel`, `heirs`, `trees`, `compensation`, `block_summary`, `valuation` (new: calculation rows with `item`), and `beneficiary`.
  - Heirs rows: `relation`, `heir_of`.
  - Compensation rows: `land_amount_rs`, `tree_amount_rs`, `prose_survey`.
  - Block summary rows: `item`.
  - Trees rows: `trees`, `tree_count`, `tree_value_rs`, `solatium_rs`.
  - LDR rows: `boundaries{north,east,south,west}`.
  - Chitta rows: `tirvai_rs`, `remarks`.
  - Beneficiary rows: `payee`, `ifsc`, `bank`, `payment_type`.
  - Other: `owner_count`, `printed_totals.*`, `ref_doc_no` and `ref_doc_date`, `gazette_no` and `gazette_date`, `rate_per_ha_rs`, `multiplier`.

## needs_human (1 page)
| page | field | question |
|---|---|---|
| k10 | doc_type | A treasury ECS report whose only beneficiary is the SDRO (LA) itself, so funds move to the LA officer and not to an owner. Should it be DISBURSEMENT, or a funds-allocation/OTHER label? |

## Findings for the verifier and extractor (no names)
1. **k09 (CHITTA in `Patta Transferred`)** names an individual owner, not SIPCOT. The folder does not prove that mutation has happened.
2. **k05** is a deposit under s.77(2) into the Single Nodal Account, not a payment, and the plot is an unapproved house plot with a 33.33% deduction. The rate is Rs 36,50,879/ha. **k04**: Rs 3,00,000/ha × 1.25 multiplier + 100% solatium.
3. **k06** spells the father's name two ways across rows. Owner matching needs fuzzy tolerance.
4. **k02** prints three-decimal ares (5.64.255) and a short total (12.05.5). All column sums check on k02, k03, k05, k06 and k08.
5. **k01** puts 16 co-owners in one merged cell for one 0.605-ha parcel. The heirs of the deceased co-owner are given only in the prose and the heirs table.
