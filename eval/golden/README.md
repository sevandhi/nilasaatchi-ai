# Mini golden set (Phase 0, T0.5)

`mini.jsonl` has 20 labelled pages, one JSON object per line. `mini_pages.csv` lists the page sources.

> **Personal data.** `mini.jsonl` holds owner names, patta numbers and amounts copied from the page images. Under land-domain-knowledge §13 it must stay local and must **not** be committed to git or sent to a training provider. Add `eval/golden/mini.jsonl` and `eval/golden/pages/` to `.gitignore` before any commit.

## Labelling protocol
- Labelled by the independent evaluator on 2026-09-26, **from the page images only**. I read `pages/{id}_preview.png` first, then zoomed on crops of the 300 dpi `pages/{id}.png` using a PIL crop script. No OCR, no text layer and no model output was used to produce any value.
- **`doc_type`** is my judgement of the content. Where it disagrees with `doc_type_hint` or the folder, the `notes` field says so.
- **Header fields:**
  - `village` uses the canonical name from skill §7.
  - `unit_no` and `block_no` are Arabic-numeral strings, so "IV" becomes "4".
  - A date is only given when it is printed complete. If the day or month is blank, the field is `null` and the note explains why.
  - A reference number printed on the page that belongs to a different document (the award behind a Form F, or the original notice behind an errata) goes in the extra keys `ref_doc_no` and `ref_doc_date`, not in `doc_no`.
- **Rows:**
  - Every table row visible on the page is labelled. No page reached the 40-row cap.
  - `extent_ha` is converted only from hectare notations: `H.AA.SS` or `H.AA.S`, and chitta `H - AA.SS`.
  - `extent_ac` and `cents` are **only the printed values**. I did not convert between units: a row printed only in cents has `extent_ha = null`.
  - `amount_rs` is an integer.
  - Owners are copied exactly as printed, in the printed script. List numbering ("1.", "2.") is removed and multiple owners are joined with ` ; `.
  - `classification` is DRY for புன்செய் or புஞ்சை or "Dry Land", and OTHER for வீட்டுமனை. The raw printed term is kept in `classification_raw` when it is not புன்செய்.
- **Extra keys** (a scorer should ignore any key it does not know):
  - Rows: `village`, `patta_ha`, `poramboke_ha`, `patta_ac`, `poramboke_ac` for the village-level GO/AS tables. `table` where a page has two table kinds. `part` for "(பகுதி)". `plot_no`. `trees`. `boundaries`. `tirvai_rs` (தீர்வை). `payee` and `instrument`.
  - `printed_totals`: extra acre and poramboke totals.
- **Uncertainty:** a field I was not confident about still has my best reading, but it is listed in `fields_uncertain` and the page has `needs_human = true`. I also used `fields_uncertain: ["doc_type"]` where the value is clear but the correct label depends on a taxonomy decision.
- **Few-shot disjointness:** none of these 20 pages may be used as a few-shot example in any prompt.

## Counts
| doc_type | pages | rows | uncertain fields |
|---|---|---|---|
| GO | 1 | 7 | 0 |
| AS | 1 | 7 | 0 |
| OTHER (AS proposal) | 1 | 7 | 1 |
| AWARD_7_2 | 4 | 8 | 0 |
| AWARD_7_3 | 2 | 19 | 3 |
| FORM_F | 2 | 2 | 1 |
| FORM_E | 2 | 2 | 4 |
| SEC32_NOTICE | 1 | 19 | 7 |
| SEC32_ERRATA | 1 | 6 | 6 |
| COURT_DEPOSIT | 1 | 1 | 2 |
| LDR | 2 | 16 | 1 |
| CHITTA | 1 | 9 | 1 |
| DLPNC | 1 | 0 | 1 |
| **Total** | **20** | **103** | **27** |

Pages where my content label differs from `doc_type_hint`:
- g02: hint AS, labelled OTHER.
- g03: hint LPS, labelled AS.
- g15: hint DISBURSEMENT, labelled AWARD_7_2.
- g16: hint DISBURSEMENT, labelled COURT_DEPOSIT.
- g17: hint POSSESSION_CERT, labelled LDR.

g20 keeps the DLPNC label, but its content is an SLPNC approval note.

## needs_human items (12 pages)
| page | fields | why |
|---|---|---|
| g02 | doc_type | A Tamil proposal seeking AS, not the AS letter. What taxonomy label should it get? |
| g07 | rows[5].owner | 4th owner is a transliterated North-Indian name (name redacted) |
| g08 | header.village, header.block_no | Not printed. The surveys match g07 (Umarikottai B1), but the file is named B_5 |
| g10 | header.ref_doc_date | The day "24" is handwritten |
| g11 | rows[0].owner, plot_no, cents | Handwritten (plot 124 or 125; cents 4; owner <redacted>) |
| g12 | rows[0].owner | Handwritten (owner name redacted)|
| g13 | header.doc_no; owners of rows 1, 7, 11, 16, 17, 19 | Blurred newspaper halftone |
| g14 | owners of all 6 rows | Very small newspaper print |
| g16 | header.doc_date, doc_type | DD date boxes are blank. The payee is the District Judge: is that COURT_DEPOSIT or DISBURSEMENT? |
| g17 | doc_type | Same "Land Delivery Certificate" form as g18. Should LDR and POSSESSION_CERT be merged? |
| g19 | header.village | Not printed. The rows match Peroorani U5 B1 (g13) exactly |
| g20 | doc_type | SLPNC approval note. The enum has no SLPNC value |

## Contradictions with `land-domain-knowledge`
Escalate these to `docs/decisions.md` (evaluator does not edit it).

1. **Acre conversion factor (§5).** The skill says 1 ha = 2.47105 ac, and the agreement check is ±0.01 ac. Almost every document uses **2.47**:
   - GO 897.495 → 2216.81.
   - AS 186.455 → 460.54 (at 2.47105 this would be 460.74).
   - Other examples: DLPNC 104.31 → 257.65, LDR 0.605 → 1.494, awards 0.86 → 2.12.
   - One page (g06, 11.31 ha → 27.947 ac) uses 2.47105.
   - Some acre values are truncated rather than rounded (g04: 0.955 ha → 2.35 ac, where rounding gives 2.36). The ±0.01 check at 2.47105 will produce false positives on large extents. Accept either factor.
2. **"AS letter total 911.39.50" (§9).** The actual English AS letter (Lr.No.LA/TUT/Allikulam/2020 dated 31.3.2021, g03, found in LPS.pdf) gives **904.40.0 ha (2233.86 ac)**, identical to the GO. The 911.39.50 / 2251.14 ac figures come from a Tamil proposal (g02, AS.pdf p2) that asks for sanction. So the "GO vs AS" discrepancy is really "GO/AS vs a later proposal". That page's text (Keelathattaparai poramboke +1.54.0, patta −1.54.0) also contradicts its own table (poramboke unchanged at 3.50.50, patta +2.33.0).
3. **GO Umarikottai value (§9).** The skill has 106.01.0. The GO prints **106.01.01**, which is likely a typo because the column total needs 106.01.00.
4. **Hectare notation (§5).** The square-metre part is not always 00 or 50. Seen: 3.96.32, 0.73.68, 2.34.82, 0.30.60, 0.48.06, 1.50.20. Single-digit forms ("185.96.5") and a stray space ("104.31 .00") also occur, and a colon for a dot ("0:10.50").
5. **Timeline dates (§11) are per block, not global.**
   - DLPNC meetings seen: 01.02.2023 & 24.08.2023 (Peroorani U5B1), 24.02.2023 & 23.08.2023 (Allikulam U8B1), 09.08.2023 (Umarikottai), and 17.02.2023 & **17.08.2023** (Unit IV SLPNC note). The skill lists 17.02.2023 & 24.08.2023.
   - SLPNC: 10.11.2023.
   - 3(2) newspaper dates are 17.12.2021 or 18.12.2021. The errata date is 21.08.2022 or **23.08.2022** (g04).
6. **Gazette No.527 (§3).** The skill says 527 is a Solar Power Plant gazette. In g06 and in Form E (g11, g12), Gazette **No.527 dated 28.11.2022** is cited for Allikulam scheme lands, so the number alone must not mark a document out of scope. Also, the Form F for the same owner and proceeding (g09) cites **No.506 dated 03.11.2022**, while its Form E (g11) cites 527 dated 28.11.2022.
7. **Compensation rate (§2, §5).** The skill gives ₹5,00,000/acre as the example. Allikulam U8B1 (g06) uses **₹9,00,000/acre**, so rates are per block. g06 also fails the amount check: 10.52 ac × 9,00,000 = 94,68,000, but ₹95,64,800 is printed.
8. **Vocabulary missing from §8:**
   - தொகுதி = block (used as well as பிளாக்).
   - புஞ்சை = dry (colloquial for புன்செய்).
   - வீட்டுமனை = house site (Form E in Keelathattaparai 376 has house plots with a பிளாட் எண் column and extents in cents).
   - (பகுதி) = part of a survey.
   - வாரிசுதாரர்கள் = legal heirs.
   - படிவம்-பி / படிவம்-E = Form B / Form E.
   - திருத்திய அறிவிப்பு = errata notice.
   - பெறுநர் = recipient.
   - கிணறு = well.
9. **Village aliases missing from §7:**
   - அல்லிக்குளம் (with க், in the GO).
   - கீழதட்டப்பாறை (LDR boundary).
   - Perurani (DLPNC).
   - Neighbouring non-scheme village கூட்டுடன்காடு appears in boundaries.
10. **Survey patterns (§6).** A sub-division can itself be a list (`1` / `6,7,8,10A` as one parcel, g18). Also watch for run-together text like `173/20.86.00` (survey 173/2 followed by extent 0.86.00, g05).
11. **Stage and doc-type mapping (§2, §3).**
    - The "Amount Disbursed" folders contain award orders (g15) and a demand draft payable to the Principal District Judge, which is a court deposit (g16).
    - "LDR Issued" and "Possession Taken" hold the same Land Delivery Certificate form.
    - A 7(3) award page (g08) tabulates the lands **already paid under 7(2)**. A parser must not treat every table in a 7(3) award as 7(3) lands.
12. **Proceeding numbers are batch ids.** Two different owners' Form E (g11, g12) carry the same number, ந.க.அ7/10/அலகு-7-பிளாக் 9/2024-(4). Also, many Form E/F/DLPNC dates are printed with the day blank (".02.2025", ".2023").
