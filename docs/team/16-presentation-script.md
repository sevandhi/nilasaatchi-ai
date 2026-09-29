# 16 · Final Presentation Script (45 minutes + optional Q&A)

**Event:** FarmwiseAI Campus Product Challenge (TCE), final demo.
**Product:** NilaSaatchi AI (நிலச் சாட்சி), "Paper vs Planet".
**Format:** Part A is 30 minutes of talk with slides. Part B is 15 minutes of live website walkthrough. Then an optional 5-minute Q&A bank that is **not** counted in the 45.

Every number in this script comes from `docs/metrics.md`, `docs/decisions.md`, `docs/progress.md`, `docs/reviewer-scorecard.md`, `docs/architecture.md`, `docs/team/*.md`, `proposal.md` or `eval/agent/demo25.yaml`. The "Sources" table at the end maps each number to its file. Details on terms are in `docs/team/14-domain-terms.md`; a page-by-page UI guide is in `docs/team/15-page-by-page-guide.md`.

Reading rules for the speakers:
- Speak, do not read. The "Say" blocks are written to be spoken: first person plural, short sentences.
- Say the misses out loud. Our project rule is "report measured numbers only".
- Never say a real owner name. Keep **Demo mask ON** in the app the whole time.

## Timing table

| # | Time box | Min | Section | Speaker |
|---|---|---|---|---|
| **Part A: talk (30 min)** | | | | |
| A1 | 00:00–02:00 | 2 | Opening, team, roles and contributions | Shivani |
| A2 | 02:00–05:00 | 3 | The problem | Mohana |
| A3 | 05:00–07:00 | 2 | Who it helps and the value | Preethi |
| A4 | 07:00–10:00 | 3 | How we came up with the idea | Sevandhi |
| A5 | 10:00–12:00 | 2 | How we started: plan, phases, decision log | Shivani |
| A6 | 12:00–16:00 | 4 | What we built, phase by phase | Mohana, Sevandhi, Shivani, Preethi |
| A7 | 16:00–20:00 | 4 | How it works: architecture | Shivani, Sevandhi |
| A8 | 20:00–23:00 | 3 | Stats and metrics | Mohana, Sevandhi |
| A9 | 23:00–26:00 | 3 | Challenges we faced | Preethi, Shivani |
| A10 | 26:00–28:00 | 2 | What is unique, honesty and limits | Sevandhi, Mohana |
| A11 | 28:00–30:00 | 2 | What is next, hand-off to the demo | Preethi |
| **Part B: live demo (15 min)** | | | | |
| B1 | 30:00–31:30 | 1.5 | Overview | Preethi |
| B2 | 31:30–34:00 | 2.5 | Map | Sevandhi |
| B3 | 34:00–36:30 | 2.5 | Parcel timeline and DiD | Sevandhi |
| B4 | 36:30–39:00 | 2.5 | Findings and evidence pack | Mohana |
| B5 | 39:00–41:30 | 2.5 | Agent console (live question) | Shivani |
| B6 | 41:30–43:30 | 2 | Documents, upload, report | Mohana |
| B7 | 43:30–44:00 | 0.5 | Review queue | Mohana |
| B8 | 44:00–45:00 | 1 | Models and routing, close | Shivani |
| | **Total** | **45** | 30 (Part A) + 15 (Part B) | |
| Q&A | optional, after 45:00 | 5 | Reviewer question bank | anyone |

Check: Part A = 2+3+2+3+2+4+4+3+3+2+2 = 30. Part B = 1.5+2.5+2.5+2.5+2.5+2+0.5+1 = 15.

---

# PART A: THE TALK (00:00–30:00)

## A1 · 00:00–02:00 · Opening, team, roles and contributions
**Speaker:** Shivani (introduces all four).
**On screen:** Title slide: "NilaSaatchi AI: Paper vs Planet". Tagline in Tamil and English. Then the team slide (table below).

**Say:**
> Good morning. We are team MindSpark. Our product is NilaSaatchi AI. In Tamil, Nila Saatchi means "witness of the land".
>
> Our idea is simple. Land papers make claims. A satellite shows what really happened on the ground. We put the two side by side. We call it Paper versus Planet.
>
> We chose Task 1, the multi-model, multimodal and agentic AI system. We built it on the Task 2 land-acquisition data.
>
> Let us tell you who we are, and we want to be open about how we worked. Each of us owns a workstream. We set the direction, we review the output, and we accept or reject the results. The implementation was done by AI coding agents, Claude Code, under our supervision. A separate AI evaluator checked each milestone before a human signed it off. We did not write the code by hand, and we will not pretend we did. What we did own is every decision. There are 81 of them in our decision log.
>
> I am Shivani. I lead the team and I own the agentic AI: the planner, the router, verification, the ledger and the quality gates. Mohana owns the Paper side: Tamil and English document reading, claim extraction and accuracy. Sevandhi owns the Planet and Proof side: the satellite pipeline, the land-use model, matching, checks and evidence packs. Preethi owns product, frontend and backend: the workspace, the timeline, the API, exports, documentation and this demo.
>
> Now, Mohana will start with the problem.

**Roles and contributions** (leave this table on screen while Shivani speaks; it matches `proposal.md` §13):

| Member | Role | Owns and reviews | AI agents supervised | Speaks in |
|---|---|---|---|---|
| **Shivani R** | Team lead, agentic AI | Planner, router, verification, ledger, quality gates | Agent architect, QA evaluator | A1, A5, A6, A7, A9, B5, B8 |
| **Mohana G** | Document intelligence (Paper) | Tamil/English OCR, claim extraction, accuracy | Document-intelligence engineer | A2, A6, A8, A10, B4, B6, B7 |
| **Sevandhi S** | GeoAI and satellite (Planet + Proof) | Satellite pipeline, land-use model, matching, checks, evidence packs | Earth-observation, GIS and data engineers | A4, A6, A7, A8, A10, B2, B3 |
| **Preethi Shri R** | Product, frontend and backend | Workspace, timeline, API, exports, documentation, demo | Frontend, backend and docs-writer engineers | A3, A6, A9, A11, B1 |

**Key numbers to say:** 4 people, 81 logged decisions (D-001 to D-081), Task 1 on Task 2 data.

---

## A2 · 02:00–05:00 · The problem
**Speaker:** Mohana.
**On screen:** Slide with a scanned Tamil award page (owner names blurred), an FMB map sheet, and three bullets: "Locked in paper", "Never checked against the ground", "Must be trustworthy". Source: `docs/team/01-project-overview.md` §2, `docs/team/02-data-and-domain.md`.

**Say:**
> Let us start with the real problem. The site is the Allikulam SIPCOT industrial park in Thoothukudi. Tamil Nadu acquires land for industrial parks under a 1997 Act. It goes through about ten legal stages. Each stage produces documents. Gazette notices. Notices to owners. Price decisions. Awards. Payments. Land delivery certificates. Patta transfer.
>
> We were given about three thousand two hundred PDFs. After we removed duplicates, that is 2,362 unique documents and 12,859 pages. Most are scanned Tamil. Twenty-six percent were duplicates, and a third were filed in the wrong folder. The tables are hard. Merged cells, tree columns, handwriting, sideways scans.
>
> Alongside them we have the FMB, the field measurement book maps. That is 1,242 parcels across seven villages. The map and the papers are not linked. Nothing connects a row in an award to a polygon on the map.
>
> Every one of these documents makes claims about specific parcels. The land class, the extent, the owners, the trees, the possession date. And here is the gap. Nobody checks those claims against what happened on the ground. Was this land really farmed? Did possession change anything? Is the land still idle years later?
>
> There is a third problem. If a computer flags something, an officer must be able to trust it. So every claim needs evidence, a confidence and a caveat. That is what we set out to build.

**Key numbers to say:** 2,362 documents, 12,859 pages, 1,242 parcels, 7 villages, 26% duplicates, 33% misfiled (`docs/team/09-results-and-honesty.md`, `docs/reviewer-scorecard.md` §1).

---

## A3 · 05:00–07:00 · Who it helps and the value it adds
**Speaker:** Preethi.
**On screen:** Slide "Who benefits" with four rows (SIPCOT, acquisition officers, auditors and farmers, FarmwiseAI).

**Say:**
> Who is this for?
>
> SIPCOT can see which possessed land is idle, and which is still being farmed. In our data that is 79 idle blocks, about 834 hectares.
>
> Land acquisition officers get one place for each parcel. Its documents, its legal stage, and its exceptions.
>
> Auditors and farmers benefit from compensation and extent checks. Is the paid amount what the notified rate says? Does the extent on paper match the map?
>
> And for FarmwiseAI, this is a new product built on your own strength, satellite verification. The same engine answers a question you already sell answers to: was a crop grown on this plot in this season? That is the core of crop insurance and loan checks. We call it the agri-claims reuse pack.
>
> The value in one line. Instead of reading thousands of scanned pages by hand, an officer gets the exceptions first, each with proof. And a new document can be uploaded and checked in about a minute, at about a tenth of a US cent per page.

**Key numbers to say:** 79 idle blocks, 834 ha (`docs/team/13-phases5-7-as-built.md` §1); about US$0.001 per page (`docs/reviewer-scorecard.md` §2); 30–100 seconds per new document (same).

---

## A4 · 07:00–10:00 · How we came up with the idea
**Speaker:** Sevandhi.
**On screen:** Timeline slide: "Day 0 idea v1 → market scan → satellite spike → pivot (D-008)". A small table of the spike numbers.

**Say:**
> Our first idea was different. It was a land-OCR copilot. Read the acquisition documents, link them to parcels, answer questions in a chat. That was decision D-001.
>
> Then we asked a hard question. What would other teams build? We scanned the market. Land-record portals with OCR are everywhere. Crop chatbots are everywhere. Many hackathon projects do exactly that.
>
> Then we looked at who is judging. FarmwiseAI is a satellite-verification company. Crop intelligence. Crop-insurance claim checks. Loan-use checks by satellite. A land-acquisition product line. SIPCOT is among your logos.
>
> So we asked, what does nobody do? Nobody checks acquisition documents against the satellite record.
>
> We ran a quick spike to see if it was even possible. We pulled Sentinel-2 images for the parcels. In that first pass, 532 of 1,142 parcels looked crop-like both before and after possession. We reproduced the spike within a small tolerance, 530 versus 532. That told us the signal was there. On that evidence, on day zero, we pivoted. That is decision D-008.
>
> Now, an honest note. That first headline did not survive. We later found that after the monsoon almost every field turns green, crop or weed. So "still cultivated" was not proven. We withdrew the claim, and redesigned the test with a control group. We will show you that. It is our best example of how we work. Measure, find the flaw, fix it in the open.

**Key numbers to say:** spike 532 of 1,142 (restated later to 444 of 1,073 in D-015, then withdrawn as a proof in D-035); reproduction 530 vs 532 (`docs/metrics.md`, row "Satellite spike reproduction"); decisions D-001, D-008, D-015, D-035 (`docs/decisions.md`).

---

## A5 · 10:00–12:00 · How we started: plan, phases, decision log
**Speaker:** Shivani.
**On screen:** Slide with eight phase boxes P0 to P7 in a row, and beside them "plan.md → decisions.md → progress.md → metrics.md".

**Say:**
> Here is how we started. Before any code, we wrote a plan, and a proposal to FarmwiseAI. The plan has eight phases, zero to seven. Bootstrap, data foundation, document intelligence, satellite evidence, the agentic core, verification and analytics, the workspace UI, and validation and demo.
>
> Every phase ends at a quality gate. Tests must pass, the metrics must be written down, and a review is done.
>
> We keep three living files. The decision log, `decisions.md`. Every change from the plan is there with who decided and what we rejected. The progress file. And the metrics file, where every number has the command that produced it.
>
> Our rules were strict from day zero. Free models only, except AWS Bedrock under a cap. No hard-coded questions or answers. Evidence on every extracted value. Owner names masked in anything we show. And we report measured numbers only.
>
> The work was compressed into a two-week prototype window. Most of the build happened from 26 to 30 September.

**Key numbers to say:** 8 phases (P0 to P7), 81 decisions, 2-week window (D-002); AWS cap set at US$15 (D-028), raised to US$30 (D-079), inside the US$100 event budget.

---

## A6 · 12:00–16:00 · What we built, phase by phase
**Speaker:** Mohana (P1, P2), Sevandhi (P3, P5), Shivani (P4), Preethi (P6 onward). About 30 to 40 seconds each.
**On screen:** One slide per group, or one long slide with a row per phase (P0, P1 … P7, then "after P7"). Show the result number on each row.

**Say (Mohana, P0–P2):**
> Phase zero was bootstrap and spikes. We built the model router, a health check, and a five-way OCR bake-off on 20 labelled pages. AWS Bedrock Ministral 8B vision won clearly. It read 83 percent of survey numbers. Groq vision got 38. Plain Tesseract got about zero on owner names in tables.
>
> Phase one, the data foundation. We catalogued the documents, removed duplicates, and loaded the parcel maps into PostGIS. We built a classifier. Our first version used rules and scored 45 percent. We replaced it with a model-based version at 90 percent on legal stage.
>
> Phase two, document intelligence. We ran the bulk read on 5,796 pages for about US$5.6. That produced 26,269 table rows, 42,380 facts and 15,342 legal events. We repaired the reader in three loops against held-out pages.

**Say (Sevandhi, P3, P5):**
> Phase three, satellite evidence. We built a per-parcel Sentinel-2 time series from 2019 to today. 588 scenes and 382,536 parcel observations. A land-use model. And the control-group comparison.
>
> Phase five, verification and analytics. A matcher that links document facts to parcels, 82 percent of facts linked, 87 percent of parcels have document facts. Then the findings engine. 1,388 findings in eight categories, each with an evidence pack.

**Say (Shivani, P4):**
> Phase four, the agentic core. A LangGraph agent. A planner, tools, a verifier, a critic from a different vendor, a judge and a presenter. Every step goes into a hash-chained ledger.

**Say (Preethi, P6 onward):**
> Phase six, the workspace. Eight pages: overview, map, parcel timeline, findings, agent console, documents, models and routing, and a review queue. Phase seven, validation and the cloud demo.
>
> Then we kept going, because reviewers will want to try new data. You can upload a new document. It goes through seven steps and you download a verification report. In a test, 20 unseen documents all completed. You can refresh the satellite. We added the newest scene from 26 September. We built a read-only demo on AWS Lambda. And as of today, the full app runs on an AWS server. That is what you will see live.

**Key numbers to say:** bake-off 83% vs 38% surveys (`docs/metrics.md` bake-off rows); classifier 45% → 90% (`docs/metrics.md`, 2026-09-27 row); 5,796 pages, US$5.6; 26,269 rows, 42,380 facts, 15,342 events; 588 scenes, 382,536 observations; 82.1% facts linked, 87% parcels; 1,388 findings; 20/20 uploads, 387 parcel links, 806 facts.

---

## A7 · 16:00–20:00 · How it works: architecture
**Speaker:** Shivani (router, agent, ledger, privacy), Sevandhi (data stores, satellite, DiD, evidence packs).
**On screen:** The architecture diagram from `docs/architecture.md` §1 (Paper pipeline, Planet pipeline, knowledge graph, router, agent, API, UI). Then the routing table from §3.

**Say (Shivani):**
> Here is how it works. Two pipelines feed one knowledge graph.
>
> The Paper pipeline. A local Tesseract reads headers for free. Then Bedrock Ministral 8B vision reads whole pages of tables. Arithmetic checks test the numbers. Do the totals add up? Does hectares match acres? Does the amount equal acres times rate? If a check fails, we read again. If it still fails, the page goes to human review.
>
> Every model call goes through our router. The router filters by the kind of input, the privacy tier, the quota, a circuit breaker and a spend guard. It scores the models, logs the reason, checks the JSON that comes back, and falls back if a model fails.
>
> Privacy is built into that router. Owner data is tier PII. PII can only go to providers that do not train on inputs, that is AWS Bedrock and Groq. Gemini's free tier trains on inputs, so it only sees names replaced by tokens, or public data. We measured it. Of 14,202 calls that carried owner data, zero went to a model that trains on inputs.
>
> We use ten routed models from five providers, each with a job. Gemini plans and judges. Cohere is the critic. Bedrock Ministral reads tables and classifies. Groq writes SQL and is a fallback. There is a local model as the last fallback. And our own LightGBM model classifies land use at zero cost.
>
> The agent is a LangGraph graph. Planner, tools, verifier, critic, judge, presenter. The critic must come from a different vendor than the model that produced the claim. It has to propose a test that could prove the claim wrong, and the system runs that test. In our runs, the critic was another vendor every time. The SQL tool is guarded. One SELECT, allowed tables only, read-only database role, no owner table. And every step goes into a SHA-256 hash-chained ledger. One command verifies nothing was altered.

**Say (Sevandhi):**
> The data lives in PostGIS with pgvector. That is spatial SQL and search in one database.
>
> The Planet pipeline reads Sentinel-2 at 10 metres. For each parcel and each date, it computes greenness and bare-soil indices, smooths them, and turns them into seasonal features. A LightGBM student model, taught by labels from a vision model, labels each parcel-season. We validate by holding out whole villages.
>
> The key idea is the control group. Greenness alone cannot separate a crop from a weed after the monsoon. So we take never-acquired farmland in a ring two to six kilometres outside the park. Same rain, same season. Then we run difference-in-differences. Parcel minus controls after possession, minus parcel minus controls before. That removes rain and season effects.
>
> The output is a finding with an evidence pack. The paper side, the planet side, the checks that were run, a verdict, a confidence and caveats. Findings are signals for field verification. They are not legal conclusions.

**Key numbers to say:** ten routed models, five providers (`docs/reviewer-scorecard.md` §4); 0 of 14,202 PII calls to a training-tier model (`docs/metrics.md` Phase 7 table, Privacy row); critic another vendor 24/24 (`docs/metrics.md`, "Agent end-to-end" row); control ring 2–6 km (D-035); SQL guard 4/4 attacks refused.

---

## A8 · 20:00–23:00 · Stats and metrics
**Speaker:** Mohana (Paper numbers), Sevandhi (Planet and Proof numbers).
**On screen:** Three slides: "What it handles", "How accurate", "Cost and trust". Use the tables in `docs/reviewer-scorecard.md` §1–§3. Leave the misses in red.

**Say (Mohana):**
> The numbers. All measured, all in our metrics file with the command that made them.
>
> Scale first. 2,362 documents. 12,859 pages. 26,269 table rows. 42,380 facts. 15,342 legal events. 1,242 parcels.
>
> Accuracy on pages the system had never seen. We built a fresh set of ten pages, sealed away from tuning. Survey numbers 89.5 percent. Owner names 83.3. Extent 79.2. Acres 100. Land class 93.8. Document type 100. Our targets were 92, 85 and 90 for survey, owner and extent. So we missed on three of them, narrowly. We say so. Uncertain rows go to a human review queue instead.
>
> The classifier: 90 percent on legal stage, against 45 for the first rules-only version. But the document type is only 80 percent. And on uploads without a folder name, the type guess was right on only 11 out of 20. So the uploader declares the type.
>
> Cost. The bulk read cost about US$0.001 per page. In all, about US$8 of AWS by our router's estimate. Everything else runs on free tiers.

**Say (Sevandhi):**
> Matching. 82.1 percent of facts linked to a parcel. A spot check of 50 rows was 50 out of 50 correct. Recall is below our 85 percent target.
>
> Satellite. 588 scenes, 382,536 parcel observations. The land-use model has 93.6 percent accuracy, but macro-F1 is 0.634. Our target was 0.80. So we call its output a signal, not a verdict.
>
> Against the control group: 143 parcels show a significant drop in plant vigour after possession. 1,143 still look like farmland on greenness alone.
>
> Findings. 1,388, in eight categories. We audited them two ways. First, we recomputed a random sample of 37 from the source tables. All 37 follow from their stored evidence. Second, we looked at the scanned page for ten paper-based findings. Extent mismatches: 5 out of 5. Compensation mismatches: only 1 out of 5. The false positives came from merged cells, a missed tree column, a table with a different rate, and rounding. So compensation findings are shown as leads, not conclusions.
>
> The agent. On 25 demo questions after a fix, 24 out of 25 match the database. On our original 8 hard questions it is 5 out of 8. It was 8 of 8 on 27 September and 3 of 8 the next morning, because the free planner model varies. Median time is about 41 seconds, and our target was 25. Tests: 928 backend and 13 browser tests pass.

**Key numbers to say (each is a row in `docs/metrics.md`):**
- Fresh heldout2: survey 89.5, extent 79.2, owner 83.3, headers 91.1 (targets 92/90/85/95). Owner precision was 45% (over-split names).
- Classifier: stage 0.90, type 0.80, scheme 0.96.
- Matching: 82.1% facts linked, 50/50 spot check.
- Land use: accuracy 0.936, macro-F1 0.634 (target 0.80).
- DiD: 143 drop, 1,143 farmland-like, 513 significant rise.
- Findings audit: 37/37; paper check 6/10 (extent 5/5, compensation 1/5).
- Agent: demo25 24/25 (96%); original 8: 5/8; p50 41 s; verified-claim rate 91% on demo25.
- Tests: 928 backend; 13 UI e2e passed, 1 skipped.
- Route comparison: routed 8/8 plans, all-open 0/8, all-proprietary 6/8; routed 15% cheaper at list price (US$0.147 vs US$0.173).

---

## A9 · 23:00–26:00 · Challenges we faced
**Speaker:** Preethi (first half), Shivani (second half).
**On screen:** A two-column slide "What broke → what we did". Keep it as a plain table.

**Say (Preethi):**
> Let us be concrete about what went wrong. There was a lot.
>
> First, free-tier quotas ran out. Gemini and Groq have daily limits. Runs stopped in the middle. We added fallback chains and a circuit breaker. And you will see that one browser test is skipped, because it needs a second provider's quota.
>
> Second, privacy. We learned that Gemini's free tier trains on inputs. So we built privacy tiers into the router. Owner data never goes there.
>
> Third, Amazon Textract, the obvious tool for tables, was denied for our role. So we ran a bake-off and picked Bedrock vision.
>
> Fourth, Lambda concurrency is about ten. Our cloud demo loaded slowly on the first burst, and images failed. We added automatic retries and lazy loading. Sixty of sixty review images load now.
>
> Fifth, our AWS login expires. Long jobs stopped when the session expired. We ran bulk jobs in chunks under two hours and added a retry wrapper. One classifier run spun for nine hours before we noticed there was no cache and the machine was overloaded.

**Say (Shivani):**
> Sixth, hosting the full app. For days, launching a server was blocked. Every server type came back unauthorized. We wrote the exact errors down and sent them to the organisers. Then it was unblocked, and today we had two launches that failed silently. In the first, a text substitution broke every download link. In the second, the database restore ran while the database was still restarting. We fixed both, and the third server set itself up in five minutes. We terminated the two broken ones.
>
> Seventh, Tamil tables. Merged cells, tree columns, sideways scans. Our held-out score started at 74 percent for survey and 39 for owners. It was over-fitted on the golden set. We fixed it in three repair loops. We also admitted that we had looked at held-out outputs once, so we built a fresh set.
>
> Eighth, the satellite headline. We had to withdraw "532 still cultivated". We built the control-group test instead.
>
> Ninth, the compensation findings. Only one in five was confirmed on the scanned page. We now present them as leads.
>
> Tenth, the document classifier is weak without folder names. So uploads take a declared type.
>
> We logged fourteen self-corrections like these in our honesty log. Every one made the product more honest.

**Key numbers to say:** 1/5 compensation confirmed; 11/20 type guess; held-out started 74.1 / 70.7 / 38.6% (survey/extent/owner, `docs/metrics.md`); fresh set 89.5 / 79.2 / 83.3; 60/60 review images load (D-078); 14 corrections in `docs/team/09-results-and-honesty.md` §2; EC2 third server ready in 5 minutes (D-081).

---

## A10 · 26:00–28:00 · What is unique, honesty and limits
**Speaker:** Sevandhi (unique), Mohana (limits).
**On screen:** Slide "Six things that are different" then slide "What we say first" (red bullets from `docs/reviewer-scorecard.md`, "Known limits").

**Say (Sevandhi):**
> What is different about us? Six things.
>
> One. Paper versus Planet. We found no other system that checks acquisition papers against the satellite record, parcel by parcel.
>
> Two. A control group, not just greenness. We compare with never-acquired farmland nearby, so rain and season cancel out.
>
> Three. A critic from another vendor. A different company's model must try to prove each claim wrong.
>
> Four. Privacy tiers inside the router, proven by logs.
>
> Five. Evidence on every value. Click any number and see it boxed on the scanned Tamil page.
>
> Six. It works on new data, at almost no cost. Upload a document. Get a report.

**Say (Mohana):**
> And here are our limits. We say them first.
>
> Extraction is below target on fresh pages. Handwriting and blurred newsprint go to a human. The compensation findings are leads. The satellite model's macro-F1 is 0.634, below our 0.80 target, and we have no field ground truth. At ten-metre resolution, small parcels mix with their neighbours and ploughing is hard to see. The agent takes about 40 to 50 seconds and its accuracy varies with the free model. The findings are signals for field verification, not verdicts. And one honest caveat on today's server: it has no Bedrock access, so uploads there are read by a free model. The accuracy numbers we quoted were measured with Bedrock.

**Key numbers to say:** macro-F1 0.634 vs 0.80; agent p50 ~41 s (demo25) to ~50 s (original eval); composite questions weakest (5/8).

---

## A11 · 28:00–30:00 · What is next, hand-off to the demo
**Speaker:** Preethi.
**On screen:** "Next" slide with five bullets, then the two URLs.

**Say:**
> What is next? Five things.
>
> One. A field check. Take a sample of the findings to the site and measure precision on the ground.
>
> Two. Better award-table extraction. That is where the extent and owner gaps are.
>
> Three. A faster agent. Caching and parallel tools, to reach the 25-second target.
>
> Four. Bedrock on the server. When an instance role is allowed, the full app can use the same reader as our measured numbers.
>
> Five. The agri-claims pack. Was a crop grown on this plot this season, for insurance and loans. That is where this engine meets FarmwiseAI's daily work.
>
> Now let us show you the real thing. Everything you will see is running live, on an AWS server in Mumbai. Sevandhi and Mohana will drive.

**Key numbers to say:** agent target 25 s vs ~41 s measured; nothing new is claimed here.

---

# PART B: LIVE WEBSITE WALKTHROUGH (30:00–45:00)

## Before you start (do this 10 minutes before the session)
1. **Start the full app:** `make aws-app-start`. Check with `make aws-app-status`. The server **stops itself 3 hours after each start**, and the public URL **may change** after a restart. Use the URL printed by `status`. Last known: http://ec2-13-127-61-19.ap-south-1.compute.amazonaws.com/
2. **Open the read-only cloud demo in a second tab** as the safety net: https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/
3. Confirm **Demo mask is ON** (owner names hidden). Do not turn it off.
4. Pre-open the upload file in a file dialog: `data/demo_uploads/working/06_AWARD_7_2_ramasamypuram.pdf`.
5. Run one throw-away agent question 5 minutes before, to warm the models and check quota. If it fails, go to the fallback plan below.
6. Have the pre-made report ready: `data/demo_uploads/working/reports/`.

## B1 · 30:00–31:30 · Overview
**Speaker:** Preethi.
**Click path:** open the site root. It lands on **Overview**.
**Point at:** the live counts (documents, parcels, facts, findings), the pipeline diagram, and the satellite-freshness card ("Check for new satellite images").

**Say:**
> This is the Overview. Every number here is live from the database. Click any of them and you can see the SQL behind it. 2,362 documents. 1,242 parcels. 42,380 facts. 1,388 findings.
>
> Here is the satellite freshness card. It says which scene is the latest, 26 September 2026. The button fetches newer scenes when they exist, and only updates the parcels that are affected. Re-running with nothing new takes three seconds. Adding a new scene to all 1,242 parcels took 4.6 minutes.
>
> Now let us see the map.

**Core technical point:** the UI shows the product, not our process: live counts with their SQL, incremental satellite refresh with a resumable journal.
**Key numbers:** 2,362 / 1,242 / 42,380 / 1,388; refresh 4.6 min, no-op 3 s (`docs/reviewer-scorecard.md` §2).

## B2 · 31:30–34:00 · Map
**Speaker:** Sevandhi.
**Click path:** **Map**. In the colour control choose **Acquisition stage by season (slider)**. Drag the slider to a rabi season, then press **Play**. Switch colour to **Change vs never-acquired farmland**. Turn on the **control-group** layer (the ring of never-acquired cells).
**Point at:** the legend staying stable while seasons change; grey for "no data"; the control cells outside the park boundary.

**Say:**
> This is the map. 1,242 parcels, all from the FMB survey sheets. I am colouring them by legal stage, season by season. Watch the stages advance as I press Play. Each colour is the furthest stage a parcel had reached by the end of that season. The slider starts in 2018, and early seasons are partial.
>
> Now I switch to change versus never-acquired farmland. This colours each parcel by how it behaves compared with the controls.
>
> And here is the control group. These cells are farmland in a ring two to six kilometres outside the park, never acquired. Same rain, same climate. They are what makes the comparison fair.

**Core technical point:** the control group replaces the naive "green means farmed" claim (D-035). Season layers are cached and prefetched so Play is smooth.
**Key numbers:** 143 significant drops; 1,143 farmland-like; control ring 2–6 km.

## B3 · 34:00–36:30 · Parcel: Melathattaparai | 233
**Speaker:** Sevandhi.
**Click path:** click a parcel, or open `/parcel/Melathattaparai%7C233`. Scroll to the **Paper vs Planet timeline**, then to the **DiD table**. Click one linked extraction to open the scanned page.
**Point at:** the NDVI line from 2019 to 2026 with document events on top; the possession date 2025-03-21; the DiD table rows for vigour and ploughing; the "1 post season" caveat.

**Say:**
> This is our key screen. One parcel: Melathattaparai, survey 233. The line is greenness from the satellite, 2019 to 2026. The markers on top are events from the documents. Notice the page ties paper and planet together on one timeline.
>
> Possession was on 21 March 2025. Look at the table. Vigour: plus 0.35, but the confidence interval crosses zero, so we do not claim that. Ploughing signal: plus 2.15, with an interval from 0.85 to 3.70. That is significantly stronger than never-acquired farmland after handover.
>
> Now the honest part. There is only one season after possession. So this is a lead for a field visit. It is not a verdict.
>
> And every extracted value is clickable. Here is the scanned Tamil page, with the value boxed in red, its confidence and the checks it passed.

**Core technical point:** difference-in-differences with confidence intervals, and an explicit caveat. Evidence on every value.
**Key numbers:** vigour DiD +0.35 [−0.13, 0.90]; plough DiD +2.15 [0.85, 3.70]; possession 2025-03-21; one post season (`docs/metrics.md`, "Demo parcel" row).

## B4 · 36:30–39:00 · Findings and evidence pack
**Speaker:** Mohana.
**Click path:** **Findings**. Category dropdown: **Compensation mismatch**; severity: **high**. Open **finding 1977**. Show **In simple words**. Click **View document** to open the popup with the red evidence box. Optionally switch the box to Tamil.
**Point at:** the evidence pack centred at the top; the paper side and the planet side; the checks; the caveats; the red box on the scanned page.

**Say:**
> The Findings page lists 1,388 findings in eight categories. Filters are live dropdowns fed from the data. I choose compensation mismatch, severity high. There are 43 high ones out of 127.
>
> Let us open finding 1977. Melathattaparai, survey 227. This box, "In simple words", is written for an officer and not for an engineer. It says what was found, explains each number, why it was flagged, other possible explanations, how sure we are, and what to do next. It can also answer in Tamil.
>
> The facts. The extent is 2.125 acres. At the page's rate of five lakh rupees an acre, we would expect about ten and a half lakh. The payment shown is 2 lakh 29 thousand for the land, and 2 lakh 52 thousand with trees. Every other row on the page pays exactly five lakh an acre.
>
> Now I click View document. The scanned page opens, and the red box is on the row. We checked this one by eye on the page, and it is real.
>
> We are honest about this category. Of five compensation findings we checked on the scanned page, only one was confirmed. The other four were false positives: a merged cell, a missed tree column, a table with a different rate, and rounding. This one, 1977, we verified by eye and it is real. So we show these as leads. Extent mismatches were five out of five.

**Core technical point:** an evidence pack is the paper side, the planet side, the checks, and a verdict with a confidence and caveats. Plain-language summaries use only the finding's own facts, never owner rows.
**Key numbers:** 127 compensation (43 high); finding 1977; extent 5/5, compensation 1/5.

## B5 · 39:00–41:30 · Agent console (live question)
**Speaker:** Shivani.
**Click path:** **Agent console**. Type or pick the question below and press **Run**. While it works, narrate the progress timeline. When it finishes, open **How the answer was checked**, then **Verify ledger**.

Pick one, from `eval/agent/demo25.yaml`:
- **Primary (q01):** "Verify Melathattaparai survey 233: what do the documents claim, and what does the satellite show?" It ties into the parcel we just saw.
- **Short, reliable backup (q07):** "How many high severity compensation mismatches are there?" Expected 43 from the database.
- **Optional Tamil (q25):** the same question about parcel 233 in Tamil.

**Point at:** the 7-stage progress timeline; the answer card first; claim tags; "Keep in mind" caveats; the "second opinion" from a different vendor; the ledger hash.

**Say:**
> Now the agent. Same parcel, asked in plain English. We do not pre-write any answer. There is no hard-coded query in the code.
>
> While it runs, look at the timeline. First, a planner decides which tools to call. Then the tools run, against the database, through a guarded read-only role. Then the verifier checks every claim against the data. Then the critic. That is a model from a different company than the one that made the claim. It tries to find a test that proves the claim wrong. Then a judge, and the presenter.
>
> It takes a while. It is about 40 seconds on the free models. That is above our 25-second target. We will not hide it.
>
> Here is the answer, first. Each claim is a tag with its evidence. Under "How the answer was checked" you see the checks passed and the second opinion. And this last button verifies the hash chain of every step. If someone changed a saved run, this would fail.

**Core technical point:** planner, verifier, cross-vendor critic, judge, ledger. Evaluation: 24 of 25 of these demo questions match the database. The agent does not use Bedrock by default; it runs on free models.
**Key numbers:** demo25 24/25 (96%), p50 41 s, verified 91%, tools 100%; a live run on this server on 30 Sep answered "71 high-severity findings" and the database said 71 (D-081); q07 expects 43.

## B6 · 41:30–43:30 · Documents, upload and report
**Speaker:** Mohana.
**Click path:** **Documents**. Show the catalogue and the dropdown filters. Click a row to open the popup viewer. Then **Upload**: choose `06_AWARD_7_2_ramasamypuram.pdf`, declare the type **Award 7(2)**, start. Watch the 7 steps. Click **Download report (PDF)**.
**Point at:** the AI's type guess ("GO") next to the declared type; the step-by-step progress; the parcel links; the report.

**Say:**
> This is the document catalogue. 2,362 documents. Filters by village, type and legal stage.
>
> Now we upload a document the system is reading for the first time. It is a rescan of an award. I declare its type, Award 7(2). Why do I have to? Because the AI's own guess, without folder names, is right on only 11 of 20. Look here: it guessed something else. We show that guess, and we let the clerk override it.
>
> Now the seven steps. Save. Catalogue. Classify. Read the tables. Load. Link to parcels. Run checks. This file took about 70 seconds in our tests and linked 10 parcels. Uncertain rows go to the review queue.
>
> When it finishes, I download the verification report. A plain summary, the steps, the linked parcels with their legal stage, findings explained in simple words, and every row read. Owner names are never in it.
>
> One note. This server has no Bedrock access, so this read uses a free model, and it can be slower or less accurate than the numbers we quoted.

**Core technical point:** the pipeline works on new data. Duplicates are recognised by MD5. A check that sends serial numbers mistaken for survey numbers to a second read was added after a fresh-read test found the bug.
**Key numbers:** 20/20 unseen uploads, 387 parcel links, 806 facts; AI type guess right 11/20; file 06 = 10 parcels linked, 21 facts, 12 rows (`data/demo_uploads/working/RESULTS.md`, from cache).

## B7 · 43:30–44:00 · Review queue (two-step)
**Speaker:** Mohana.
**Click path:** **Review queue**. Open one item. Edit one value and press **Save corrections**. Then press **Approve page**.
**Point at:** the AI's original value being kept; the "pending" note after saving.

**Say:**
> The review queue is where uncertain pages go. There are 1,302 of them. Review is two steps. Save corrections only saves. The AI's original value is kept. Then Approve page finishes the review. Corrected rows stay corrected. Every correction updates the facts that the findings use.

**Core technical point:** human in the loop, with the AI original preserved for audit (D-072, D-074).
**Key numbers:** 1,302 review items (`docs/metrics.md`, Knowledge graph row).

## B8 · 44:00–45:00 · Models and routing, close
**Speaker:** Shivani.
**Click path:** **Models & routing**. Scroll: each model's role and privacy flag, task chains, usage and cost, privacy audit.
**Point at:** the privacy flag per model; fallbacks; the spend bar against the budget; the audit line "0 owner-data calls to training-tier models".

**Say:**
> Last page. Models and routing. Every model has a job, a privacy flag and a health status. For each task you see the chain, first choice and fallbacks. This is the multi-model requirement of Task 1, shown live.
>
> Here is the privacy audit. Of 14,202 calls that carried owner data, zero went to a model that trains on inputs. Here is the spend against the event budget. About eleven dollars of the hundred, by AWS billing, and most of that was the one-off bulk read of 5,796 pages with Bedrock.
>
> To close. We built a system that reads the paper, watches the planet, and shows its proof. We tell you where it is weak, because that is how you can trust where it is strong. Thank you. We are happy to take questions.

**Core technical point:** privacy tiers and cost are visible and auditable, not claimed.
**Key numbers:** 0 of 14,202; 16,173 routed calls total; ~US$8 of US$100.

---

## Fallback plan (if a live step fails)

| Problem | What to do |
|---|---|
| **Agent call fails or is out of quota** (Gemini or Groq daily limit) | Say it plainly: "the free tier ran out, this is exactly why we have fallback chains." Open a finished run from **Run history** and replay it. Or run the short question q07 (43 expected). Metrics evidence: the fallback works when a second provider has quota (`docs/metrics.md`, Robustness row). |
| **Upload fails, or is slow** (server reads with a free model; no Bedrock) | Skip the live upload. Open the ready report from `data/demo_uploads/working/reports/`, and show the results table in `data/demo_uploads/working/RESULTS.md` (20/20). |
| **AWS server not up, or URL changed** | Run `make aws-app-status`; if stopped, `make aws-app-start` (setup progress at `/setup-status.txt`). It auto-stops 3 hours after start, so restart if the slot is long. |
| **Server unreachable at all** | Switch to the cloud read-only demo https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/. All pages, map, parcels, findings and "In simple words" work there (written by Bedrock from Lambda). Uploads, the live agent and review actions return "read-only". Say so. |
| **Cloud demo pages or images load slowly** | Lambda concurrency is about 10. Wait a moment. The UI retries throttled requests by itself. |
| **Laptop fallback** | `make demo`, then http://localhost:5173 (run `make aws-login` first for Bedrock). |

---

# OPTIONAL Q&A BANK (about 5 minutes, not counted in the 45)

Short, honest answers. More are in `docs/team/10-reviewer-qa.md`.

1. **Did you write the code?** No. AI coding agents (Claude Code) implemented it. Each of us owns a workstream, directs and reviews the agents, and accepts or rejects results against measured gates. All decisions are logged (D-001 to D-081).
2. **Is anything hard-coded?** No. Answers are computed from the database at run time. The unseen and Tamil questions pass, and 20 unseen uploads completed. The agent accuracy varies, though: 24/25 on demo questions, 5/8 on the original hard set.
3. **How accurate is the extraction?** On 10 never-seen pages: survey 89.5, owner 83.3, extent 79.2. Below our targets of 92, 85 and 90. Uncertain rows go to a review queue.
4. **Can a satellite prove farming?** Not on greenness alone. That is why we use a control group and difference-in-differences. Even then, our output is a signal for field verification.
5. **Why is the land-use model weak?** Macro-F1 is 0.634 against a target of 0.80, because the classes are imbalanced and we have no field ground truth. Accuracy is 93.6%.
6. **How reliable are the findings?** 37 of 37 follow from their stored evidence when recomputed. Against the scanned page, extent mismatches were 5 of 5, compensation mismatches 1 of 5. We treat compensation findings as leads.
7. **How do you protect owner data?** Privacy tiers in the router (0 of 14,202 owner-data calls to a training-tier model), names masked in the UI, bank fields stripped, and the agent's database role cannot read owners. The SQL guard refused 4 of 4 attacks.
8. **What does it cost?** About US$8 of AWS by our router's estimate, of the US$100 event budget. About US$0.001 per page for reading. Every other model is on a free tier.
9. **Why is the agent slow?** About 41 to 50 seconds on free models, and our target is 25. It runs planner, tools, verifier, critic, judge and presenter in series. Caching and parallel tools are next.
10. **Why is the cloud demo read-only, and what is the EC2 server?** The cloud demo runs on Lambda with no database server. Writes return 405. The full app runs on one small EC2 server that auto-stops after 3 hours. It has no Bedrock access yet, so it uses a free model for table reading.
11. **What is the difference between the routed policy and all-proprietary?** On the same 8 questions: routed made 8/8 valid plans with 26 critic checks in 41 s. All-open-weight made 0/8 valid plans. All-proprietary made 6/8 plans in 55 s, and cost 15% more at list price. Actual cost was US$0 in all three.
12. **What would you do with another month?** A field check on a sample of findings, better award-table extraction, a faster agent, Bedrock on the server, and the agri-claims pack for FarmwiseAI.
13. **Which resources did FarmwiseAI give you, and which did you source?** FarmwiseAI provided the Task 2 documents and FMB dataset, and the AWS account with approved services (S3, Lambda, API Gateway, DynamoDB, CloudWatch Logs, Bedrock Ministral and Titan, and later small EC2). We sourced the free model tiers, the open Sentinel-2 archive (Earth Search catalogue, no account), the OpenFreeMap basemap, and public reference layers such as DEM and WorldCover. We generated the labels, the LightGBM model, the knowledge graph, the findings and the reports ourselves.

---

# Sources for the numbers

| Number | Source |
|---|---|
| 2,362 documents, 12,859 pages, 1,242 parcels, 7 villages, 26% duplicates, 33% misfiled | `docs/reviewer-scorecard.md` §1; `docs/team/09-results-and-honesty.md` §1; `docs/metrics.md` (KG re-verified row) |
| 26,269 rows, 42,380 facts, 15,342 events, 1,302 review items, 87% parcels | `docs/metrics.md` (Knowledge graph after Stage B) |
| 5,796 pages, about US$5.6, 3 repair loops | `docs/metrics.md` (Stage B complete); D-038, D-053 |
| Bake-off 83% / 38%, Tesseract about 0% owners | `docs/metrics.md` (Bake-off rows); `docs/reviewer-scorecard.md` §3 |
| Classifier stage 0.90 (v1 0.45), type 0.80, scheme 0.96 | `docs/metrics.md` (2026-09-27 rows); D-030 |
| Held-out first score 74.1 / 70.7 / 38.6 | `docs/metrics.md` (Held-out extraction row, 2026-09-26) |
| Fresh heldout2: survey 89.5, extent 79.2, owner 83.3, headers 91.1, class 93.8, doc type 100, acres 100; targets 92/90/85/95 | `docs/metrics.md` (FINAL extraction score row) |
| Matching 82.1%, spot check 50/50 | `docs/metrics.md` (Matcher after D-052) |
| 588 scenes, 382,536 observations, refresh 4.6 min / 3 s | `docs/metrics.md` (Phase 7 table, `docs/team/13`); `docs/reviewer-scorecard.md` §2 |
| Land-use accuracy 0.936, macro-F1 0.634, target 0.80 | `docs/metrics.md` (Phase 7 table, Planet row) |
| DiD: 143 drop, 1,143 farmland-like, 513 rise | `docs/metrics.md` (P3 DiD row) |
| Parcel 233: +0.35 [−0.13, 0.90], +2.15 [0.85, 3.70], possession 2025-03-21 | `docs/metrics.md` (Demo parcel row) |
| Spike 532 of 1,142; reproduction 530 vs 532; restated 444 of 1,073; withdrawn | `docs/metrics.md` (spike row); D-008, D-015, D-035 |
| 1,388 findings; 127 (43 high) compensation; 79 blocks 834 ha idle | `docs/metrics.md` (Findings engine row); `docs/team/13` §1 |
| Findings audit 37/37; paper check 6/10, extent 5/5, compensation 1/5; finding 1977 real | `docs/metrics.md` (2026-09-28 findings audit rows) |
| Agent: demo25 24/25, p50 41 s, verified 91%; original 8: 5/8 (8/8 on 27 Sep, 3/8 on 28 Sep) | `docs/metrics.md` (2026-09-28 Agent rows); `eval/agent/demo25.yaml` |
| Critic another vendor 24/24; plan validity 8/8 | `docs/metrics.md` (Agent end-to-end row) |
| Routing comparison (8/8, 0/8, 6/8; US$0.147 vs US$0.173; 26 critic checks) | `docs/metrics.md` (Routing row); `docs/reviewer-scorecard.md` §4 |
| 0 of 14,202 PII calls to training-tier; 16,173 routed calls | `docs/metrics.md` (Privacy row) |
| Ten routed models, five providers | `docs/reviewer-scorecard.md` §4 |
| SQL guard 4/4 | `docs/metrics.md` (Robustness row) |
| 928 backend tests; 13 UI e2e, 1 skipped | `docs/metrics.md` (Phase 7 Tests rows) |
| 20/20 uploads, 387 links, 806 facts, type guess 11/20, 30–100 s | `docs/metrics.md` (New data row); `docs/reviewer-scorecard.md` §1–2 |
| About US$11 AWS by Cost Explorer (US$10.72 on 2026-09-30; router estimate about US$8); US$100 event budget; cap US$15 → US$30; US$10.72 spent before EC2 | `docs/metrics.md` (Cost row); D-028, D-079, D-081 |
| 14 self-corrections | `docs/team/09-results-and-honesty.md` §2 |
| EC2: two failed launches, third ready in 5 min, live agent answered 71 (DB 71), auto-stop 3 h | D-080, D-081 |
| 60/60 review images load, Lambda concurrency about 10 | D-078, D-069 |
| Cloud read-only demo, Bedrock from Lambda 167 ms | `docs/metrics.md` (Cloud rows) |
| Team, roles | `proposal.md` §13 |
