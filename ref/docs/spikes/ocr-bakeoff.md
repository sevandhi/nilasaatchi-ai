# OCR bake-off (Phase 0, T0.5): routing decision D-010

**Date:** 2026-09-26.
**Set:** 20 golden pages across 13 document types, labelled from the images by the qa-evaluator (`eval/golden/mini.jsonl`: 103 rows, 194 numeric values, 78 survey refs, 56 owner names).
**Harness:** `spikes/ocr_bakeoff/` (96 tests).
**Commands:** `make ocr-bakeoff`, `python -m spikes.ocr_bakeoff.fullpage_vlm`, `python -m spikes.ocr_bakeoff.fullpage_recall`.

## Results

**Schema-agnostic reading recall:** is each gold value found among the engine's output for that page?

| Candidate | Numbers | Survey refs | Owner names | s/page | Capacity |
|---|---|---|---|---|---|
| (a) Tesseract tam+eng, full page, psm4 | 45%* | 47%* | 0% | 4.9 | unlimited (local) |
| (b) PaddleOCR PP-OCRv5 ta, full page | 64%* | 68%* | 0% | 28.1 | unlimited (local), 8.2 h per corpus |
| (c) OpenCV grid + per-cell OCR | 12% | 21% | 2% | 12–15 | unlimited (local) |
| (e) Groq Qwen3.8 vision, row bands | 26% | 38% | 16% | 2.6 | **~97 pages/day** (200K TPD) |
| (e′) Bedrock Ministral 3 8B, row bands | 38% | 49% | 16% | 6.4 | cost-bound |
| **(g) Bedrock Ministral 3 8B, whole page, raw-cell JSON** | **63%** | **83%** | **59%** | **3.3** | cost-bound, **~$0.0005/page** |

\* Full-page OCR gets credit for any matching number or survey token anywhere on the page, including prose, so (a) and (b) are inflated. Their table-field exact match (extent, owner) was **0%** on every page.

**Field-mapped exact match** (the original scorer): the best local candidate reached extent_ha 2% and owner 2%; Groq reached extent 26% and owner 14%.

**Why the mapped scores understate (g):** one generic schema across 13 document types put correct values into the wrong fields. For example, GO village totals landed in extent_ha while the gold has patta_ha. P2 fixes this with per-document-type column mapping.

**Where (g) still misses:**
- blurred newsprint 3(2) notices and errata (g13, g14): owners 6/25;
- handwritten Form E (g11, g12);
- chitta pages, where survey and subdivision sit in separate columns (a scorer artefact: 0/9);
- the GO village table (7/13, a mix of acre columns and rounding).

**Failure mode seen:** big tables (≥ 15 rows) were truncated at 1,024 output tokens and failed schema validation. With max_tokens 4096, all 20 of 20 pages succeed.

## Decision D-010: production routing for P2
1. **Text layer first** where present (gazettes, chitta, possession certificates).
2. **Headers and prose:** Tesseract tam+eng, local, ~5 s/page. Dates were 100% right and units 75%. It also supplies the full text for search. The VLM header fields act as a second vote.
3. **Tables:** **Bedrock Ministral 3 8B, whole-page raw-cell transcription** (JPEG, long side 1800 px, `max_tokens` 4096, JSON schema `{header, tables[{columns, rows[[cells]]}]}`). Then deterministic per-doc-type column mapping, the normalisers, and the self-consistency checks (totals, ha↔ac, amount = ac × rate).
4. **Fallback:** Groq Qwen vision (~97 pages/day), then the review queue.
5. **Straight to review:** handwriting (Form E) and blurred newsprint pages. The golden set flags them `needs_human`.
6. **Dropped from bulk:** PaddleOCR (28 s/page, no table gain) and grid/cell OCR. The grid detector is kept only to locate tables for evidence bboxes.
7. **Budget:** priority table pages (~7k) ≈ **$3.5**; the whole corpus (12,859 pages) ≈ $6.3; about 4 h at 4 parallel workers. This is within the $15 cap (D-028), and `make aws-cost` runs before and after each batch.

## P2 targets stay as in plan §8, measured with per-doc-type mapping
Survey + subdivision ≥ 92%, extent ≥ 90%, owner ≥ 85% on **legible** pages. Pages marked needs_human are reported separately.
