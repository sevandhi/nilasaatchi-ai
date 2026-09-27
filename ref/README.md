# ref/: reference material, not part of the working application

Moved here on 2026-09-27 (decision D-066) so the working app contains only what it runs. Nothing in `app/`, `pipeline/`, `planet/`, `web/`, `tests/` or the Makefile imports anything here (the full test suite, lint and web build pass without it). To restore an item, move it back to its original path.

| Item (original path) | What it is | Why it is here |
|---|---|---|
| planet/spike.py, spikes/s2_ndvi_spike.py | Phase-0 Sentinel-2 NDVI feasibility spikes | Superseded by planet/ (stac, extract, features, refresh); the `make s2-spike` target was removed |
| planet/s1/ | Empty package reserved for Sentinel-1 radar gap-fill | Never implemented (optional in the plan) |
| scripts/stageb_loop.sh | Retry loop used once for the Stage-B bulk extraction | One-off batch run, finished |
| generated_doc/ | An early draft of the FarmwiseAI proposal | Superseded by proposal.md |
| web/src/pages/Evaluation.jsx | "Evaluation & honesty" page | Removed from the UI at the user's request (D-062) |
| docs/spikes/ | Spike write-ups | Historical; the results are in docs/decisions.md |
| data/pages300/ (12 GB) | 300-dpi page renders of the original dataset used during bulk extraction | Only needed to re-extract the original dataset; new uploads render into a fresh `data/pages300/` |
| data/s2/S2*_L2A/ (590 folders, ~8.8 GB) | Per-scene Sentinel-2 image crops + per-scene parquet | Their values are consolidated in data/s2/*.parquet and the database; chips are fetched from the online archive; the refresh only writes folders for new scenes |
| data/bakeoff*, data/tmp_test_pdfs, *.log | OCR model bake-off outputs, old logs | Evaluation history (results in docs/metrics.md) |

**Kept in place on purpose:** `spikes/ocr_bakeoff/` (the upload classifier still uses its Tesseract wrapper), `infra/bedrock_batch/` (it can tear down the AWS Lambda/S3 resources: `make aws-down`), and `eval/` (golden and held-out sets used by `make eval-*`).

`ref/data/` is git-ignored. The rest of `ref/` is small and can be committed.
