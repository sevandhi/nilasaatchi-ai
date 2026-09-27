# 10 · Reviewer Q&A

Short answers you can give confidently. The doc number in brackets is where to read more.

## Idea and value
1. **What does NilaSaatchi do in one sentence?** It reads land-acquisition documents, links them to parcels, and checks what the papers claim against 7 years of satellite evidence, with proof and confidence. (01)
2. **Why not a normal land-records dashboard?** Many teams build that. FarmwiseAI's strength is satellite verification, and nobody we found checks acquisition papers against the satellite record. (01)
3. **Who uses it?** SIPCOT (idle vs farmed land after possession), acquisition officers (stages and exceptions), auditors (compensation), and farmers indirectly (under-compensation). (01)
4. **How is it reusable?** The same engine answers "was a crop grown on this plot this season?" for insurance and loans (the agri-claims pack). (01, 08)

## Data
5. **How many documents?** 3,212 PDFs, 2,361 unique, about 12.5k unique pages, mostly scanned Tamil. (02)
6. **What problems did you find in the data?** 26% duplicates, 33% misfiled, mixed gazettes, proposal-vs-sanction drift, 366 overlapping FMB parcels, amount-in-words errors. (02)
7. **Why is the parcel key "Village|KIDE"?** The FMB KIDE repeats across villages; 86 KIDEs collide. (02, 05)
8. **What is H.AA.SS?** Hectare-are-square-metre notation: 0.95.50 = 0.955 ha. (02)

## Models and the router
9. **Which models and why?** Gemini (planning, judging, satellite teacher; non-personal data only), Cohere (critic, second opinion), Bedrock Ministral 8B/3B (tables, classification; private), Groq Qwen/gpt-oss (SQL, fallback), Titan (embeddings), Tesseract (local OCR), and our LightGBM student. (04)
10. **How does the router choose?** It filters by modality, privacy, quota, breaker and spend guard, scores by capability, confidence, latency and cost, logs the reason, validates the JSON, and falls back. (04)
11. **How do you protect owner privacy?** Privacy tiers: PII goes only to non-training providers (AWS Bedrock, Groq). There is a pseudonymisation gateway, a pseudonymised DB view, API masking, and bank numbers are never stored. (04)
12. **What does it cost?** About US$7.9 of AWS in total (router ledger) (Stage B ≈ US$5.6 for 5,796 pages, about US$0.001/page); everything else is free tier. The cap is US$15. (04, 09)
13. **What happens if a model fails?** Retry, then the next model, then a terminal step (review queue / clarify). Circuit breakers and chaos testing cover it. (04)
14. **Why did you drop Mistral?** Its API needs a billing method even on the free plan. (04)

## Documents
15. **Why not just OCR?** Tesseract got 0% of owners and extents from tables; table structure needs a vision model. (06)
16. **How did you choose Bedrock Ministral 8B?** A 5-way bake-off on 20 labelled pages; it won clearly (83% surveys vs 38% for the next best). (06)
17. **How do you know extraction is correct?** Arithmetic self-checks (totals, ha↔ac, amount = acres × rate), second reads, two-engine owner voting, a review queue, and measured accuracy on held-out pages. (06)
18. **What accuracy?** On 10 fresh, never-seen pages: survey 89.5%, owner 83.3%, extent 79.2%, acres 100%, document type 100%. That's slightly below target and reported honestly; the gaps are award group layouts and over-split co-owner names. (06, 09)
19. **What's a repair loop?** Measure on held-out → diagnose the failing layout → fix it on dev sets → re-measure. We did three. (06)
20. **Isn't that over-fitting?** We used separate dev sets and kept held-out blind. Where we broke that, we disclosed it, and the final score will use a fresh set. (06, 09)
21. **What goes to human review?** Failed checks, handwriting, blurred newsprint, and a 10% owner-name sample. (06)

## Satellite
22. **What data?** Sentinel-2, 10 m, free, 2019→2026: 587 acquisitions, 307 usable over the park. (07)
23. **What is NDVI / BSI?** Greenness / bare-soil indices from spectral bands. (07)
24. **How do you classify land use?** Gemini labels 300 samples from chips (the teacher); a LightGBM "student" learns from seasonal features and labels all 41k parcel-seasons. Validation is by held-out village. (07)
25. **Can a satellite prove someone is farming?** Not on greenness alone; after the monsoon everything is green. That's why we compare with never-acquired farmland nearby (a control group, difference-in-differences) and look for a ploughing signature. (07)
26. **What did you find?** 143 parcels changed significantly vs controls after possession; 1,143 still behave like farmland; demo parcel 233 shows a significant ploughing-like signal after handover (one season, so field check needed). (07)
27. **What is DiD?** (Parcel − controls after the event) − (parcel − controls before it). It removes rainfall and seasonal effects that hit both groups. (07)
28. **Why 10 m limitations?** Small parcels mix with neighbours (flagged `mixed_pixel`), and ploughing texture is below the resolution. (07)

## Engineering
29. **Why PostGIS?** Spatial SQL and vector search in one database, and LLMs write good PostGIS SQL. (03)
30. **How is the work reproducible?** Every step is an idempotent `make` target, with content-hash caches, logs, and about 750 automated tests. (03)
31. **How do you track decisions?** `docs/decisions.md` (D-001…), `docs/metrics.md`, `docs/progress.md`. (03)
32. **How does AWS fit?** Bedrock for private table reading, plus S3/Lambda for batch processing and the planned deployment, within the event rules (Mumbai, `fai-tce-team49-*` names, no EC2/RDS). (04)
33. **Why the Lambda batch worker?** SSO sessions last 2 hours; a Lambda uses its own role credentials. The code is ready, and we're waiting on FarmwiseAI for the Bedrock permission. (04)

## Agent (Task 1 core)
34. **Where is the planner/verifier?** The design and contract are done, and the API already streams runs. The graph, tools and ledger are partly built; the full loop was deferred while we completed Phases 1–3. (08)
35. **What makes the critic "adversarial"?** It must come from a different vendor than the producer, and it must propose a *testable* reason the claim is wrong, which the system then runs. (08)
36. **What's the ledger for?** A hash chain of every step, so saved results can't be altered unnoticed. (08)

## Honesty and limits
37. **What didn't work?** Rule-based classification (45%), local OCR on tables, and our first satellite headline. Each was measured and replaced. (09)
38. **What are the limitations?** The extent gap on award groups, handwriting, no field ground truth, crop vs weed at 10 m, AWS session limits. (09)
39. **What would you do next?** The findings engine and evidence packs, the React workspace, the full agent loop on real facts, the AWS deployment, and a fresh held-out evaluation. (09)
40. **How did your team work?** Each member owned a workstream and directed and reviewed AI coding agents against measured quality gates; the decision log shows every call we made and why. (00)
