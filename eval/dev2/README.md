# Development extraction set 2: large AWARD_7_2 schedule tables (9 pages)

`labels.jsonl` has 9 labelled pages, one JSON object per line. `pages.csv` lists the page sources (page_id, doc_type_hint, source_pdf, page_no). The images are `pages/{page_id}.png` (300 dpi) and `pages/{page_id}_preview.png` (110 dpi), rendered with `pdftoppm` **as the PDF presents them**. The sideways pages e07 and e08 are *not* pre-rotated.

> **This is a TUNING set** (repair loop 3). It MAY be shown to the extraction engineer, so its scores are not an unbiased estimate. Report accuracy on `eval/golden/` and `eval/heldout/`.
>
> **Personal data.** `labels.jsonl` and `pages/` hold owner and heir names, patta numbers and amounts (land-domain-knowledge §13). `eval/dev2/.gitignore` ignores both. The lead should also add them to the root `.gitignore`. No bank account numbers appear on these pages, and none were transcribed. This README quotes no owner names.

## Selection
- **Built:** 2026-09-27 by the independent evaluator.
- **e01–e06:** the extraction engineer chose these pages. I verified disjointness by sha256 (byte-identical copies count) against the source PDFs in `eval/heldout/pages.csv`, `eval/golden/mini_pages.csv` and `eval/dev/pages.csv`: **0 overlaps**. Four of these PDFs (ProceedingB2II, Award_9_3_, 72B1, 7_2__Award_Block_7_1_) are in `eval/classify/labels.csv`. That set holds document-level classification labels, not extraction labels, so I kept them. `eval/heldout/labels.jsonl` was not opened.
- **e04 deviation:** `72B1.pdf` p2 has **no table** (prose only), so the requested "7-row table" is not there. I kept p2 as `e04`, a negative case where the expected result is `rows=[]`. I added **`e04b` = p3**, the first table: 4 rows plus a total. No page of this PDF has a 7-row schedule. The largest table is the p8 apportionment table, which I did not label.
- **e07, e08:** I picked these with seed `20260929` (`random.Random(20260929).shuffle` over the sorted paths of `7(2) Award` + `7(2) Awarded`: 323 PDFs, 288 after sha256 exclusion of classify, golden, heldout, dev, e01–e06 and within-pool duplicates). Sideways scans were preferred: I listed the pool PDFs with landscape pages in seeded order (pool ranks 22, 87, 92, 104, 226, 278) and took the first two with a table of 8 or more rows:
  - skipped `72awardb3.pdf` (landscape pp13–16, small tables)
  - skipped `2block9.pdf` (tables of 4 rows or fewer)
  - **`2block5.pdf` p8 = e07** (p7 also qualifies; I took p8 because it has the printed total)
  - skipped `2Block3.pdf` (largest schedule is 7 rows; only its heirs table has 8)
  - skipped `BLOCK7.pdf` (no table of 8 or more rows)
  - **`2Block4.pdf` p8 = e08**
  - Verified afterwards: 0 overlap with any existing set.

| page | source (under `LA_Patta_land_Documents/`) | p | rows (by table) | scan |
|---|---|---|---|---|
| e01 | 7(2) Award/ProceedingB2II.pdf | 3 | 12 parcel | flat scan |
| e02 | 7(2) Awarded/Award_9_3_.pdf | 3 | 1 parcel + 8 heirs | phone photo |
| e03 | 7(2) Awarded/Award_9_3_.pdf | 4 | 5 heirs + 13 compensation | phone photo |
| e04 | 7(2) Award/72B1.pdf | 2 | 0 (prose only) | flat scan |
| e04b | 7(2) Award/72B1.pdf | 3 | 4 parcel | flat scan, slight skew |
| e05 | Amount Disbursed 7(2) & 7(3)/7_2__Award_Block_7_1_.pdf | 6 | 4 schedule (parcel + amounts) | low-res scan |
| e06 | 7(2) Award/72AwardBlock4.pdf | 2 | 1 parcel | digital print |
| e07 | 7(2) Award/2block5.pdf | 8 | 19 compensation (1 continuation) | **sideways** |
| e08 | 7(2) Award/2Block4.pdf | 8 | 11 compensation + 1 parcel | **sideways** |

## Labelling protocol
Same protocol and schema as `eval/golden/README.md`, `eval/dev/README.md` and `eval/heldout/README.md`:
- Labelled **from the page images only**: 110-dpi preview first, then PIL crops of the 300-dpi PNG (rotated in memory for e07/e08). No OCR, no text layer, no model.
- Header fields are `null` when they are not printed on the page. A scheme name is not a village.
- `extent_ha` comes from hectare notation only. `extent_ac` is the printed value. `amount_rs` is an integer.
- Owners are copied as printed, with list numbering removed and co-owners joined by ` ; `. Merged cells are repeated on every row they cover.

Extra keys (a scorer should ignore unknown keys):
- `table`: `parcel`, `schedule` (parcel columns and amount columns in one table), `compensation` (apportionment/payout), `heirs` (new).
- **Heirs rows** (new): `owner` = heir name, `relation` = the printed relation (மனைவி / மகள் / மகன்), `heir_of` = the serial of the deceased co-owner in the patta owner cell. Heirs rows have no amounts.
- **Compensation rows**: `share` = printed share text (wrapped lines joined with a space); `share_fraction` = **my arithmetic** (e.g. 1/2 × 1/8 = 1/16), not printed. `prose_survey` = the survey that the prose above the table names (the table itself has no survey column).
- `owner_block` = the printed heading number (e04b uses the range `1-4`); `continuation`.
- `land_amount_rs`, `tree_amount_rs` (null where the cell is `-`).
- `printed_totals.blocks[]`, `prose_amount_rs`, `prose_extent_*`, `award_extent_*`, `rate_per_acre_rs`, `withheld_*`, `amount_words`, and `scope` (e07: the total covers the whole 41-row table, not this page).

## Layout notes for the parser (no names)
1. **Two-level extent header.** `விஸ்தீரணம்` spans `ஹெக்(டேர்)` | `ஏக்(கர்)` (e01, e05, e06). Other pages use two single-level headers, `விஸ்தீரணம் (ஹெக்டேர்)` and `விஸ்தீரணம் (ஏக்கர்)` (e02, e04b, e08). Map both to ha/ac. e01 names the patta column `கணினி பட்டா எண்`.
2. **Merged patta/owner cells** span 4–12 rows (e01: 12 rows, one company owner; e04b: 4 rows). Repeat them per row. The parcel table's owner cell prints father-first (`X மகன் Y`), while the block heading prints given-first with `த/பெ.` and the honorific `திரு.`.
3. **Heading variants:** survey-list heading `1) புலஎண்கள்: a, b, ... மற்றும் c` (e01); `1. (1) <owner> மற்றும் 3 நபர்கள் :` (e02); a serial range `1-4.` (e04b); `<owner>-1 மற்றும் 2 நபர்கள்` (e06); `4. புல எண்: 225/5 (பார்வை 6-ல் கண்ட ஆணை வரிசை எண்.15)` with a cross-reference (e08).
4. **Apportionment tables** (e03, e07, e08) have 3–4 columns: serial / name / share / amount. Share text is `N-ல் 1 பங்கு`, nested as `2-ல் 1 பங்கில் 8-ல் 1 பங்கு` or `6-ல் 1 பங்கில் (1/6) 5-ல் 1 பங்கு (1/5)`, and wraps to 2 lines. **Amounts sit about half a row below the name line** (they align with the second share line). Amounts are split so each group sums exactly (…584 ×2 + …583 ×3). Validate with `Σ amount = total` and `Σ fraction = 1`.
5. **Trap (e08):** the column headed `இழப்பீட்டு விஸ்தீரணம் (ஏக்கரில்)` holds **share text in every row**. Only the `மொத்தம்` row holds acres (0.667). Never parse shares as extents.
6. **Continuation (e07):** there is no header row on the page. The first row is the tail of row 23 (a name fragment plus a share line), and its amount is on the previous page. The printed total is for rows 1–41 across 3 pages (the shares on this page sum to 2/3).
7. **Heirs tables** (e02, e03): `வ.எண் / வாரிசுதாரர்களின் பெயர் / இறந்தவருக்கான உறவுமுறை`. The deceased co-owner and date of death appear only in the prose above the table. A table at the top of e03 continues the heir list begun in e02's prose.
8. **Owner cells in compensation tables** include the mother's name (`தாய்/பெ`), the husband marker `க/பெ`, and minors with a guardian in brackets (`(மைனர்- கார்டியன் தந்தை …)`). e05 row 1 has 3 numbered co-owners over 6 lines and an alias marker `(எ)`.
9. **Sideways scans** (e07, e08): the PDF page is landscape (842×595 pt) with `/Rotate 0`, and the content is rotated 90°. Rotate 90° clockwise (PIL `rotate(270, expand=True)`) before OCR/VLM. The "Scanned with OKEN Scanner" watermark runs vertically along the edge.
10. **Hectare/acre:** `H.AA.SS` throughout (0.27.50, 1.61.00). Acres have 2 or 3 dp, zero-padded (3.980, 0.800) and are sometimes truncated. e06 fits 2.47105 only; the others fit 2.47.
11. **Rates:** Rs 7,00,000/acre (e01), Rs 9,00,000 (e03, inferred from 3.26 ac → 29,34,000), Rs 5,00,000 (e04, e05, e06, e07, e08).
12. **e05 tree column** is `-` on every row. Amounts have no grouping (875000). The folder says "Amount Disbursed", but the content is a 7(2) award page.

## Document defects the verifier should flag
- **e08:** the prose prints 0.90.50 ha as **2.336 ac**, but the table (and 0.905 × 2.47105) gives 2.236.
- **e05:** a withheld 1.01.00 ha is printed as **(4.49 ac)** for Rs 12,45,000. Both 1.01 ha and the amount at Rs 5,00,000 give 2.49 ac. The digit is blurred, so this is needs_human.
- **e01:** an earlier order of 3.80 ha (9.381 ac) for Rs 66,00,700 does not equal 9.381 × 7,00,000 = 65,66,700. The page does not explain the difference (possibly trees/structures).
- **e04b:** the prose cites old pattas 1724/1526 and a sale deed. Do not take them as the current patta (3179).

## Counts
| doc_type | pages | rows | uncertain fields |
|---|---|---|---|
| AWARD_7_2 | 9 | 79 | 1 |

Per table kind: parcel 19, schedule 4, compensation 43, heirs 13. Total **79 rows** (e04 = 0).

## needs_human items (1 page)
| page | field | why |
|---|---|---|
| e05 | printed_totals.withheld_extent_ac | The prose prints "(4.49 ஏக்கர்)" for 1.01.00 ha / Rs 12,45,000, where 2.49 is expected. The low-res scan makes the first digit hard to confirm. |
