# 01 · Project Overview

## 1. The challenge
FarmwiseAI's Campus Product Challenge offered six tasks. We chose **Task 1 (Multi-Model, Multimodal and Agentic AI System)** and implemented it on top of the **Task 2 dataset** (land documents plus cadastral maps). Task 1 has no dataset of its own, so the brief tells us to use other tasks' data.

Task 1 requires:
- a planner
- a model router
- ≥ 2 proprietary models and ≥ 1 open-weight model, each with a justified role
- tool calling
- structured outputs
- verification
- fallback
- shared state
- latency and cost comparison
- one complete use case with no hard-coded answers

## 2. The real-world problem
Tamil Nadu acquires land for industrial parks (here, the **Allikulam SIPCOT park** in Thoothukudi) through about 10 legal stages under the **TN Acquisition of Land for Industrial Purposes Act, 1997 (Act 10 of 1999)**. Each stage produces documents: gazette notices, notices to owners, price-committee decisions, awards, compensation apportionment, payments, land-delivery certificates, and patta transfer. Every document makes **claims about specific parcels**:
- the land class (dry, wet or poramboke)
- the extent
- the owners
- trees and wells
- the possession date

Three problems follow:
1. **The facts are locked in paper.** The documents are 3,212 PDFs (mostly scanned Tamil), filed in 23 stage folders, many duplicated or misfiled, and nothing links them to the parcel map.
2. **The claims are never checked against the ground.** Was the land really cultivated? Did possession change anything? Were compensated trees real?
3. **Any automated finding must be trustworthy:** evidence, confidence and caveats are required before anyone can act on it.

## 3. Our idea: "Paper vs Planet"
We looked at what other teams would likely build and found that "OCR plus parcel matching plus dashboard" land portals are very common (many Smart India Hackathon 2026 projects, including the official SIH26016 problem). FarmwiseAI itself is a **GeoAI/satellite company**:
- MapsAI crop intelligence
- crop-insurance claim verification
- loan-utilisation checks by satellite
- a GIS land-acquisition product line
- SIPCOT among its logos

No product we found checks acquisition **documents** against the **satellite record**. So we built three pillars:

| Pillar | What it does | Status |
|---|---|---|
| **Paper** | Reads documents, extracts claims with evidence (page and box), links them to FMB parcels, builds each parcel's legal-stage timeline | P1/P2 |
| **Planet** | A per-parcel Sentinel-2 time series from 2019 to now, seasonal features, a land-use model, and a **control-group comparison** | P3 ✅ |
| **Proof** | An agent that plans, routes, verifies and presents findings with evidence packs | P4/P5 (partial) |

**The reuse story:** the same engine answers "was a crop grown on this plot this season?", the core question behind FarmwiseAI's crop-insurance and loan checks (the agri-claims domain pack).

## 4. Why this is useful to FarmwiseAI
- **SIPCOT:** which possessed land is idle, still farmed, or encroached.
- **Land Acquisition officers:** one place for each parcel's documents, stage and exceptions.
- **Farmers:** under-compensation checks (trees visible but not paid for, wrong land class).
- **FarmwiseAI:** a new product on its satellite-verification strengths, plus cost-controlled multi-model AI (a capability it is hiring for).

## 5. Key design principles (say these in the review)
1. **Cheapest reliable step first:** local OCR before paid AI, and a small model before a big one.
2. **Evidence or it didn't happen:** every extracted value stores document, page, bounding box, model and confidence.
3. **Privacy-tiered routing:** owner names never go to AI providers that train on free-tier data.
4. **Honesty:** we report measured numbers only, and we corrected our own claims when data disagreed (doc 09).
5. **No hard-coded answers:** evaluated on held-out and unseen data.
6. **Free models, and AWS under a strict cap:** ₹0 model cost except AWS Bedrock (spend so far ≈ US$7.9 of a US$15 cap, mostly the bulk Stage B run).

## 6. Timeline so far
- **Day 0 (26 Sep):** plan, dataset analysis, proposal, pivot to Paper vs Planet (after a satellite spike).
- **Day 0–1:** environment, model router, OCR bake-off, satellite spike reproduction (Phase 0 ✅).
- **Day 1:** catalog, GIS load, rasters, satellite inventory and extraction, classifier v1 (failed) → v2 (passed).
- **Day 1–2:** document extraction pipeline, golden/held-out/dev evaluation sets, 3 repair loops, API layer, control-group DiD (Phase 3 ✅).
