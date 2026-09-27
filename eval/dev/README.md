# Development extraction set (6 pages)

`labels.jsonl` has 6 labelled pages, one JSON object per line. `pages.csv` lists the page sources (page_id, doc_type_hint, source_pdf, page_no). The images are `pages/{page_id}.png` (300 dpi) and `pages/{page_id}_preview.png` (110 dpi).

> **This set MAY be shown to the extraction engineer for tuning** (unlike `eval/heldout/`, which stays hidden). Because it is used for tuning, its scores are **not** an unbiased estimate; report accuracy on `eval/golden/` and `eval/heldout/`.
>
> **Personal data.** `labels.jsonl` and `pages/` hold owner names, patta numbers and amounts (land-domain-knowledge §13). They are git-ignored by `eval/dev/.gitignore` (`pages/`, `labels.jsonl`). The lead should also add `eval/dev/pages/` and `eval/dev/labels.jsonl` to the root `.gitignore` (the evaluator does not edit files outside `eval/`, `tests/`, `docs/metrics.md`, `docs/reviews/`). Bank account numbers were **never** transcribed (IFSC, bank and branch only).

## Selection
- **Built:** 2026-09-26 by the independent evaluator. **Seed `20260928`**, one `random.Random(20260928)` instance, used first for the AWARD_7_2 pool and then for the DISBURSEMENT pool.
- **Pools** (under `Dataset/Land_documents/LA_Patta_land_Documents/`; paths sorted, then shuffled):
  - AWARD_7_2: `7(2) Award` + `7(2) Awarded` (323 PDFs; 293 after exclusions).
  - DISBURSEMENT: `Amount Disbursed Pattadars 7(2) & 7(3)` + `Amount Disbursed 7(2) & 7(3)` + `Amount Disbursed` (358; 230 after exclusions).
- **Disjointness by sha256** (byte-identical copies count): excluded (a) every sha256 in `eval/classify/labels.csv`, (b) the sha256 of every source PDF in `eval/golden/mini_pages.csv`, (c) the sha256 of every source PDF in `eval/heldout/pages.csv`, (d) duplicates within the pool. Verified after the build: 0 overlaps with (a), (b), (c); 6 unique PDFs. `eval/heldout/labels.jsonl` was not opened.
- **Page rule.** Walk each pool in seeded order, look at a 28-dpi contact sheet of each PDF:
  - AWARD_7_2: take the PDF if it has the owner-block layout (numbered owner-name heading `N. <name> மகன்/த/பெ <name>:` followed by a small survey / ha / ac / patta / owner table). Take the page with the most owner blocks; ties go to the page with a payout/amount table. Skipped in order: `FPACLb_10.pdf` (English PNC agreement), `Block___2__6__13_Mar_2025_5_23_pm.pdf` (one multi-owner table under a survey heading), `block_9_3.pdf` (headings are `N. புல எண் <survey>` - survey-first variant, not owner-first).
  - DISBURSEMENT: take the PDF if a page has a per-owner payment table with 2+ payee rows. Skipped in order: `3029029.pdf`, `129408.pdf` (single demand drafts), `7_2__Award_Block_1_2_.pdf`, `7_2__Award_Block_5_2_.pdf` (award orders misfiled as disbursed), `Covering_Letter (2).pdf` (court-deposit covering letter), `DD_5 (4).pdf` (single DD), `B6710.pdf` (only payee is the SDRO's own account, not owners).

| page | hint | source (under `LA_Patta_land_Documents/`) | p |
|---|---|---|---|
| d01 | AWARD_7_2 | 7(2) Awarded/b_7__1_a.pdf | 3 |
| d02 | AWARD_7_2 | 7(2) Awarded/b_6_2_.pdf | 5 |
| d03 | AWARD_7_2 | 7(2) Award/BLOCK5.pdf | 12 |
| d04 | AWARD_7_2 | 7(2) Award/Block7award (2).pdf | 4 |
| d05 | DISBURSEMENT | Amount Disbursed/bill5.pdf | 3 |
| d06 | DISBURSEMENT | Amount Disbursed/billblock1.pdf | 2 |

## Labelling protocol
Same protocol and schema as `eval/golden/README.md` / `eval/heldout/README.md`: labelled **from the page images only** (110-dpi preview, then PIL crops of the 300-dpi PNG); no OCR, text layer or model output. Header fields are `null` when not printed on the page (a village read from an earlier page is only mentioned in `notes`). `extent_ha` from hectare notation only; `extent_ac` is the printed value; integer `amount_rs`; owners as printed with numbering removed and co-owners joined by ` ; `; merged cells repeated on each row they cover.

Extra keys used here (a scorer should ignore unknown keys):
- Rows: `table` (`parcel`, `compensation`, `trees`, `beneficiary`); `owner_block` (the number of the owner heading the row belongs to); `continuation` (row closes a block that started on the previous page); `land_amount_rs`, `tree_amount_rs`; trees: `item`, `tree_count`, `tree_value_rs`, `solatium_rs`; beneficiary: `payee`, `ifsc`, `bank`, `branch`, `payment_type`.
- `printed_totals.blocks`: one object per owner block with its printed total row and, where the amount is only in prose, `prose_amount_rs`.
- Treasury pages: `doc_no` = Treasury Reference Number, `doc_date` = Treasury Reference Date.

## Layout notes per page
| page | owner blocks | tables on page | where totals / amounts sit |
|---|---|---|---|
| d01 | 2 new (headings 2, 3) + tail of block 1 | payout (block 1) - parcel (2) - payout (2) - parcel (3, 3 rows) | `மொத்தம்` row in each parcel table; amounts only in the 3-column payout tables (வ.எண் / பெயர் / இழப்பீட்டுத் தொகை), also repeated in prose with amount-in-words. Block 3's payout is on the next page. |
| d02 | 1 new (heading `2. (12) ...`) + tail of block 1 | 4-column payout (land / trees+structures / total) - parcel (2 rows) | `மொத்தம்` row; block amount of block 2 is on a later page. |
| d03 | 2 (headings 11, 12), each 1 row | parcel, parcel (no payout tables) | `மொத்தம` row per block; block amount only in the last sentence of the prose after each table; award-wide total (ha, ac, land Rs, trees Rs) in prose at the page foot, continuing onto the next page. |
| d04 | 2 (headings 3, 4) | parcel (2 rows) - trees table - parcel (1 row) | Block 3 total row has no `மொத்தம்` label; trees table has its own `மொத்தம்`; land + trees + grand amount in prose; block 4 has no total row on this page. |
| d05 | - | 1 beneficiary table, 8 rows | `Total` row at the bottom right; clean digital print, landscape. |
| d06 | - | 1 beneficiary table, 12 rows | `Total` row; skewed photo, amounts sit about half a row above their row. |

## Parser observations (no names quoted)
1. **Owner heading forms vary:** `N. name மகன் name:`, `N. name த/பெ. name:`, `N. name த/பெ. name மற்றும் 1 நபர்:` ("and 1 person"), `N. name மகன் name மற்றும் 2 நபர்கள்:`, a bracketed second serial `N. (12) name ...`, and a heading with no trailing colon. Headings are underlined; the block number continues across pages (d03 is at 11-12).
2. **One page mixes 3-4 table kinds**: parcel tables (6 columns: வ.எண் / புல எண் / விஸ்தீரணம் ஹெக். / விஸ்தீரணம் ஏக். / பட்டா எண் / நில உரிமையாளர்(கள்)), 3- or 4-column payout tables, and a 6-column trees table. Classify each table by header before mapping columns; a payout table at the top of a page belongs to the block that started on the previous page.
3. **Merged cells**: owner (and in d04 patta) span all rows of a block; co-owners inside one cell are numbered `1.` `2.` `3.` on separate lines.
4. **Hectare notation**: `H.AA.SS` (1.04.50) and single-digit `H.AA.S` (0.88.5) on the same kind of page; a block total can be a plain decimal (1.695); leading zeros occur (02.83.50).
5. **Acres**: 2 or 3 decimals; both 2.47 and 2.47105 on one page (d03); values truncated to 2 dp then zero-padded (0.835 ha -> 2.060).
6. **Rates seen**: Rs 9,00,000/acre (d01, d02), Rs 5,00,000 (d03), Rs 7,00,000 (d04); every checked amount = acres x rate exactly.
7. **Document defect**: d03 block 12 table patta differs from the patta in its own prose (prose copied from the previous block). Prefer the table; flag the conflict.
8. **Name order differs between parcel and payout tables** for the same person (father-first `X மகன் Y` vs given-first `Y த/பெ. X`, spacing/spelling changes). Father-name marker appears as `த/பெ.` and `த.பெ.`.
9. **Company owner** (d02 block 1: `... பி.லிட்.`) - no relation marker.
10. **Treasury tables**: Latin-script names with initials before or after, cell text wrapped over 2 lines, amounts without Indian grouping and with `.00`, date `dd-mm-yyyy`, repeated IFSCs, footer page number can disagree with the PDF page number (d06 prints "Page 3 of 3" on PDF p2). No survey, village or block: link to award owners by amount + transliterated name. Never extract the Bank Account No column.

## Counts
| doc_type | pages | rows | uncertain fields |
|---|---|---|---|
| AWARD_7_2 | 4 | 17 (11 parcel, 3 compensation, 3 trees) | 2 |
| DISBURSEMENT | 2 | 20 | 0 |
| **Total** | **6** | **37** | **2** |

## needs_human items (1 page)
| page | fields | why |
|---|---|---|
| d04 | rows[0].owner, rows[1].owner | Block-3 owner's given name is in a legacy-font glyph sequence (reads as ஸ்டீபன்); father's name ends `...சீர்வாதம்` or `...சிர்வாதம்` (long/short vowel unclear). |
