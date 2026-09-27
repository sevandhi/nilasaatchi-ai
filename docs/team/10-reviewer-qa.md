# 10 · Reviewer Q&A

Short answers you can give confidently. The doc number in brackets is where to read more.

## Idea and value
1. **What does NilaSaatchi do in one sentence?** It reads land-acquisition documents, links them to parcels, and checks what the papers claim against 7 years of satellite evidence, with proof and confidence. (01)
2. **Why not a normal land-records dashboard?** Many teams build that. FarmwiseAI's strength is satellite verification, and nobody we found checks acquisition papers against the satellite record. (01)
3. **Who uses it?** SIPCOT (idle vs farmed land after possession), acquisition officers (stages and exceptions), auditors (compensation), and farmers indirectly (under-compensation). (01)
4. **How is it reusable?** The same engine answers "was a crop grown on this plot this season?" for insurance and loans (the agri-claims pack). (01, 08)

## Data
5. **How many documents?** 3,212 PDFs, 2,362 unique, 12,859 pages, mostly scanned Tamil. New documents can also be uploaded. (02, 13)
6. **What problems did you find in the data?** 26% duplicates, 33% misfiled, mixed gazettes, proposal-vs-sanction drift, 366 overlapping FMB parcels, amount-in-words errors. (02)
7. **Why is the parcel key "Village|KIDE"?** The FMB KIDE repeats across villages; 86 KIDEs collide. (02, 05)
8. **What is H.AA.SS?** Hectare-are-square-metre notation: 0.95.50 = 0.955 ha. (02)

## Models and the router
9. **Which models and why?** Gemini (planning, judging, satellite teacher; non-personal data only), Cohere (critic, second opinion), Bedrock Ministral 8B/3B (tables, classification; private), Groq Qwen/gpt-oss (SQL, fallback), Titan (embeddings), Tesseract (local OCR), and our LightGBM student. (04)
10. **How does the router choose?** It filters by modality, privacy, quota, breaker and spend guard, scores by capability, confidence, latency and cost, logs the reason, validates the JSON, and falls back. (04)
11. **How do you protect owner privacy?** Privacy tiers: PII goes only to non-training providers (AWS Bedrock, Groq). There is a pseudonymisation gateway, a pseudonymised DB view, API masking, and bank numbers are never stored. (04)
12. **What does it cost?** About US$8 of AWS by our router's estimate (Stage B ≈ US$5.6 for 5,796 pages, ~US$0.001/page; Cost Explorer shows US$0.79), out of the US$100 event budget; everything else is free tier. (04, 09)
13. **What happens if a model fails?** Retry, then the next model, then a terminal step (review queue / clarify). Circuit breakers and chaos testing cover it. (04)
14. **Why did you drop Mistral?** Its API needs a billing method even on the free plan. (04)

## Documents
15. **Why not just OCR?** Tesseract got 0% of owners and extents from tables; table structure needs a vision model. (06)
16. **How did you choose Bedrock Ministral 8B?** A 5-way bake-off on 20 labelled pages; it won clearly (83% surveys vs 38% for the next best). (06)
17. **How do you know extraction is correct?** Arithmetic self-checks (totals, ha↔ac, amount = acres × rate), second reads, two-engine owner voting, a review queue, and measured accuracy on held-out pages. (06)
18. **What accuracy?** On 10 fresh, never-seen pages: survey 89.5%, owner 83.3%, extent 79.2%, acres 100%, document type 100%. That's slightly below target and reported honestly; the gaps are award group layouts and over-split co-owner names. (06, 09)
19. **What's a repair loop?** Measure on held-out → diagnose the failing layout → fix it on dev sets → re-measure. We did three. (06)
20. **Isn't that over-fitting?** We used separate dev sets and kept held-out blind. Where we broke that, we disclosed it, and the final score uses a fresh, never-seen set (heldout2). (06, 09)
21. **What goes to human review?** Failed checks, handwriting, blurred newsprint, and a 10% owner-name sample. (06)

## Satellite
22. **What data?** Sentinel-2, 10 m, free, 2019 → 2026-09-26: 588 scenes, 382,536 parcel observations. It refreshes with new scenes. (07, 13)
23. **What is NDVI / BSI?** Greenness / bare-soil indices from spectral bands. (07)
24. **How do you classify land use?** Gemini labels 300 samples from chips (the teacher); a LightGBM "student" learns from seasonal features and labels all 41k parcel-seasons. Validation is by held-out village. (07)
25. **Can a satellite prove someone is farming?** Not on greenness alone; after the monsoon everything is green. That's why we compare with never-acquired farmland nearby (a control group, difference-in-differences) and look for a ploughing signature. (07)
26. **What did you find?** 143 parcels changed significantly vs controls after possession; 1,143 still behave like farmland; demo parcel 233 shows a significant ploughing-like signal after handover (one season, so field check needed). (07)
27. **What is DiD?** (Parcel − controls after the event) − (parcel − controls before it). It removes rainfall and seasonal effects that hit both groups. (07)
28. **Why 10 m limitations?** Small parcels mix with neighbours (flagged `mixed_pixel`), and ploughing texture is below the resolution. (07)

## Engineering
29. **Why PostGIS?** Spatial SQL and vector search in one database, and LLMs write good PostGIS SQL. (03)
30. **How is the work reproducible?** Every step is an idempotent `make` target, with content-hash caches, logs, 928 backend tests and 14 UI tests. The whole app runs on a new laptop from one zip. (03, 12)
31. **How do you track decisions?** `docs/decisions.md` (D-001…), `docs/metrics.md`, `docs/progress.md`. (03)
32. **How does AWS fit?** Bedrock is the private table reader (13,000+ calls). A read-only cloud demo runs on API Gateway + Lambda + private S3, and Lambda calls Bedrock directly. All within the event rules (Mumbai, `fai-tce-team49-*` names, no EC2/RDS). (04, 13)
33. **What happened to the Lambda batch worker?** It was built to beat the 2-hour login limit. It was removed when the Lambda role lacked Bedrock, and bulk extraction was finished in 2-hour chunks. FarmwiseAI has since enabled Bedrock for Lambda, and our cloud demo proves it works. (04)

## Agent (Task 1 core)
34. **Where is the planner/verifier?** Built and evaluated: planner → tools → verifier → cross-vendor critic → judge → presenter, streamed live in the Agent console with a hash-chained ledger. 8/8 answers match SQL references, including unseen and Tamil questions. (08)
35. **What makes the critic "adversarial"?** It must come from a different vendor than the producer, and it must propose a *testable* reason the claim is wrong, which the system then runs. (08)
36. **What's the ledger for?** A hash chain of every step, so saved results can't be altered unnoticed. (08)

## Honesty and limits
37. **What didn't work?** Rule-based classification (45%), local OCR on tables, and our first satellite headline. Each was measured and replaced. (09)
38. **What are the limitations?** Extraction below target on fresh pages, handwriting, no field ground truth, crop vs weed at 10 m, agent speed (~50 s), a weak automatic document-type guess on uploads, and a read-only cloud version. (09)
39. **What would you do next?** A findings-precision audit with field checks, better award-layout extraction, a faster agent (caching and parallel tools), running the full app in AWS if RDS or containers are allowed, and the agri-claims pack (crop presence on farmers' plots). (09)
40. **How did your team work?** Each member owned a workstream and directed and reviewed AI coding agents against measured quality gates; the decision log shows every call we made and why. (00)

## Phases 5–7 and the product (added 2026-09-28)
41. **What are "findings"?** 1,388 evidence-backed issues in 8 categories: compensation and extent mismatches, document drift, FMB map quality, land-class conflicts, activity after possession vs controls, idle land (79 blocks, 834 ha), and extraction errors. Each comes with an evidence pack. (13)
42. **Show one finding.** Finding 1977: Melathattaparai 227, compensation ≠ acres × rate, with the scanned page and the numbers side by side. (13, demo script)
43. **Does it only work on your dataset?** No. Upload any PDF on the Documents page and it is read, linked to parcels and checked. 20 unseen documents all completed (387 parcel links). The satellite refresh fetches new scenes as they appear. (13)
44. **What is the downloadable deliverable?** A Document verification report (PDF) plus the rows (CSV): a summary, the steps, linked parcels with their legal stage, findings explained in plain words, and every row read. Owner names are never included. (13, docs/ui/04)
45. **Why do you ask the user for the document type?** Without folder names the AI's type guess was right on only 11/20. A clerk knows what they are uploading; the report still shows what the AI suggested. (09, 13)
46. **Is it deployed?** Yes, read-only on AWS: https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/ (API Gateway → Lambda → private S3). Uploads and the live agent run in the full app, because the rules allow no database server or containers. (13)
47. **How do images load if the bucket is private?** The Lambda returns short-lived presigned links; the bucket itself is never public. (13)
48. **What if the cloud page is slow the first time?** The account allows ~10 Lambdas at once. The first burst can be throttled for a moment, and the app retries automatically. (13)
49. **Can we run it ourselves?** Yes, from one zip. Linux/macOS: `bash scripts/demo_setup.sh`, then `demo_run.sh`. Windows: double-click `SETUP-WINDOWS.cmd`, then `START-WINDOWS.cmd`. (12)
50. **Where is the code?** https://github.com/sevandhi/nilasaatchi-ai (public). The dataset, keys and real owner names are not in it; we checked before pushing. (13)
51. **How do you prove privacy?** The router log: 0 of 14,202 owner-data calls went to a model that trains on inputs. Owners are masked in the UI, bank fields stripped, and the agent's database role can't read owners. (04, 09)
52. **Can the agent damage the database?** No. The SQL guard allows one SELECT on allowed tables only (DROP, DELETE, stacked statements and owner reads are refused), and it runs under a read-only role. (08, 09)

