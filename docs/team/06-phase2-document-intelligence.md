# 06 · Phase 2: Document Intelligence ("Paper")

**Goal:** read scanned Tamil/English acquisition pages and extract every claim (survey number, subdivision, extent, owner, amount, land class, dates) with evidence, accurately enough to trust.

## 1. The OCR bake-off (Phase 0 spike, `docs/spikes/ocr-bakeoff.md`)
We tested five approaches on **20 golden pages** hand-labelled from the images (103 table rows):

| Approach | Numbers | Survey refs | Owner names | Notes |
|---|---|---|---|---|
| Tesseract full page | 45%* | 47%* | 0% | Good on prose, fails on table cells |
| PaddleOCR full page | 64%* | 68%* | 0% | 28 s/page on CPU |
| Grid detection + per-cell OCR | 12% | 21% | 2% | Column detection unreliable |
| Groq Qwen vision | 26% | 38% | 16% | Only ~97 pages/day on the free tier |
| **Bedrock Ministral 8B, whole page** | **63%** | **83%** | **59%** | ~3.3 s and ~$0.0005 per page |

\*Inflated: full-page OCR gets credit for numbers found anywhere on the page.

**Decision D-010:** Tesseract for headers/prose; **Bedrock Ministral 8B reads each whole page as raw table cells**; our code then maps the columns per document type.

## 2. The extraction pipeline (`pipeline/extract/`)
For each page:
1. **Text layer** if present, otherwise **Tesseract** (headers: village, unit, block, document number, date). Pages scanned sideways are rotated automatically, but only when orientation detection is confident. An early, over-eager version flipped upright pages.
2. **Bedrock Ministral 8B** transcribes every table as raw cell strings (JSON, up to 4,096 output tokens), cached by content hash.
3. **Per-document-type column mapping.** Tamil/English header keywords decide which column is survey, subdivision, hectares, acres, patta, owner, amount or classification. Content overrides labels: a column of `NN/N` values *is* the survey column, and share text ("3-ல் 1 பங்கு") is never an extent.
4. **Normalisers:** H.AA.SS → hectares, Indian money, Tamil numerals, survey patterns, names.
5. **Structure rules learned from real layouts:**
   - merged cells fill down (survey, owner, patta);
   - **compensation groups** (one survey spanning several owner rows);
   - the extent aligned to the group head;
   - payout, heirs and summary tables recognised;
   - totals detected by content, not by position.
6. **Self-consistency checks:**
   - row sums = printed totals;
   - ha↔ac agree (factor 2.47 or 2.47105);
   - amount = acres × block rate;
   - acres present where an acre column exists.
7. **Second read** (a different crop/zoom and prompt variant) when the checks fail. It is accepted only if it improves consistency without losing coverage.
8. **Two-engine owner voting:** Tesseract's words correct close misreads by the vision model.
9. **Review routing (D-033):** a page goes to the human review queue only for failed checks, handwriting (Form E), blurred newsprint, a table invented from prose, or the 10% owner-name sample. Pages that pass their checks are accepted, with a confidence cap if unverified.
10. **Evidence:** every row stores document, page, bounding box, model, confidence, read status and which rule filled it.

## 3. Database load (`make load-extractions`, migration 0007)
Facts go into `extraction` / `parcel_fact` / `acquisition_event`:
- owner names are stored separately, and the agent sees only tokens;
- errata "should read as" rows supersede the "published as" rows;
- bank account numbers are never stored.

## 4. Evaluation: how we kept ourselves honest
| Set | Pages | Purpose |
|---|---|---|
| **Golden** | 20 (13 doc types) | First tuning set |
| **Held-out** | 12 unseen pages | The honest test, hidden from the engineer |
| **Dev** | 6 (awards, disbursements) | Tuning for the failing layouts |
| **Dev2** | 9 (large award schedules, sideways scans, heirs/share tables) | Tuning for the hardest layouts |

**What happened:**
1. **Golden looked perfect but was over-fitted.** Legible pages: survey 95.5%, extent 100%, owner 100%.
2. **The first held-out run exposed it:** survey 74%, extent 71%, owner 39%. The 7(2) award pages scored about 30%.
3. **Three repair loops** fixed the layouts:
   - multi-owner blocks;
   - treasury amounts;
   - rotation;
   - merged survey cells;
   - survey values printed in the serial column;
   - compensation groups;
   - extent alignment.
4. **Final held-out (legible pages):** survey **96.3% ✅** (target 92), owner **90.9% ✅** (target 85), amount 94.3%, headers 93.3%, extent 72.4% (7(2) group extents are still the gap).
5. **Honesty note (D-043):** while diagnosing, we looked at held-out outputs, so that set became "partially seen".
6. **Final honest score: fresh never-seen set `heldout2` (10 pages, legible):** survey **89.5%**, extent (ha) **79.2%**, extent (acres) 100%, owner **83.3%**, land class 93.8%, document type 100%, headers 91.1%. Each is narrowly below target (92/90/85/95). The team accepted these as documented limitations; low-confidence items go to human review (D-055).

## 5. Stage B (bulk extraction)
- **Plan:** a dry run selected **4,526 priority pages** (awards, Form F, LDR, chitta, GO/AS/LPS, disbursement), estimated at **~$4.92**, with a hard budget stop and a stop on fallback models or expired AWS login.
- **Run it:** `make extract-drain RUN=1 TYPES=… BUDGET=5` (dry run by default).
- **Done (27 Sep):** **5,796 pages** (Form F limited to its first 2 table pages), 5 failed, ≈ US$5.6 of Bedrock. It ran with 10 parallel workers (~33 pages/min) under a retry wrapper that survives network drops (16 workers crashed a library). It stops only on budget or a real login expiry.
- **Result in the database:** 26,269 extracted rows, **42,380 parcel facts**, **15,342 acquisition events**, 1,302 items in the human review queue.

## 5b. Matching facts to parcels (early Phase 5 work)
- `make match` links each fact/event to its FMB parcel: exact KIDE → normalised → parent survey → OCR-confusion variants, never across villages. It accepts at score ≥ 0.80 with a 0.10 margin over the runner-up.
- Documents headed with the scheme name "Allikulam" get their real village from unit + block + survey, only when that is unique.
- **Result:** 82% of matchable facts linked; **1,082 of 1,242 parcels (87%)** have document facts; a 50-fact spot check was 100% correct on village + survey.
- **Lifecycle views:** each parcel's stage timeline and stalled flags. Parcel-level evidence is separated from block-level evidence (payment drafts carry no survey numbers).

## 6. Talking points
- **"Why not just use OCR?"** OCR loses table structure; our tests show Tesseract gets 0% of owners and extents.
- **"What is your real accuracy?"** On 10 brand-new pages nobody had seen: survey 89.5%, owner 83.3%, extent 79.2%, headers 91.1%. That is slightly below our targets and reported honestly. The misses are merged extent cells in award group tables and over-split co-owner/heir names; those rows get lower confidence and go to review.
- **"How do you avoid over-fitting?"** Separate held-out sets, an engineer blind to held-out labels, and disclosure when that blindness was broken.
