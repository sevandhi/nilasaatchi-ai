# NilaSaatchi AI: Scorecard in numbers

*Paper vs Planet: land-acquisition documents cross-examined against 7 years of satellite evidence.*
Every number below was measured on the running system (sources in `docs/metrics.md`). Live demo: https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/ · Code: https://github.com/sevandhi/nilasaatchi-ai

## 1. What it handles
| | |
|---|---|
| **2,362** land-acquisition documents · **12,859** pages | mostly scanned Tamil; 26% were duplicates, 33% misfiled, all detected automatically |
| **26,269** table rows → **42,380** facts → **15,342** legal events | every value keeps its page, box, model and confidence |
| **1,242** parcels · **87%** linked to documents | on the FMB survey map, with each parcel's legal stage |
| **382,536** satellite observations · **2019 → 26 Sep 2026** | Sentinel-2 per parcel; new scenes added incrementally |
| **1,388** findings in 8 categories | each with an evidence pack (paper + planet + checks) |
| **20/20** unseen documents uploaded end to end | **387** parcel links, **806** facts, with a downloadable verification report |

## 2. Efficiency and cost
| Metric | Number |
|---|---|
| Cost to read a scanned page (AWS Bedrock vision) | **≈ US$0.001** (5,796 pages for ≈ US$5.6) |
| Total AI spend for the whole project | **≈ US$8** of the US$100 budget; every other model on free tiers |
| One new document, upload → linked parcels + report | **30–100 s** |
| Add a new satellite scene to all 1,242 parcels | **4.6 min**; a re-run with nothing new: **3 s** |
| Cloud demo response (Lambda) | **< 1 s** per page of data; Bedrock from Lambda **167 ms** |
| Routed vs all-proprietary AI cost (list prices, same 8 questions) | **15% cheaper** (US$0.147 vs US$0.173), actual cost **US$0** |
| Whole app on a new laptop | one **2.3 GB** zip; Windows via double-click |

## 3. Accuracy (measured on data the system had not seen)
| Metric | Number | Comparison |
|---|---|---|
| Document classifier: legal stage | **90%** | rules-only baseline 45% |
| Table reading (OCR bake-off, survey numbers) | **83%** Bedrock vision | Groq vision 38%, Tesseract ~0% on owners |
| Table reading on fresh pages: survey / owner / extent | **89.5% / 83.3% / 79.2%** | targets 92 / 85 / 90; uncertain rows go to human review |
| Parcel matching | **82.1%** of facts linked; spot check **50/50** correct | — |
| Land-use model (LightGBM, village-held-out) | **93.6%** accuracy (macro-F1 0.634) | thousands of predictions at $0 |
| Findings re-computed from source data | **37/37 (100%)** | random sample, all 8 categories |
| Findings checked against the scanned page | extent **5/5**, compensation **1/5** | compensation findings are shown as leads |
| Agent answers vs SQL ground truth (8 incl. unseen + Tamil) | **8/8** on 27 Sep; **3/8** on a re-run (28 Sep) | the free planner model varies; see §4 |

## 4. Multi-model routing (Task 1 core)
- **10 routed models from 5 providers**, each with a job: Gemini (planning, judging), Cohere (critic), Bedrock Ministral 8B/3B (tables, classification), Groq Qwen/gpt-oss (SQL, fallback), a local Qwen fallback. Our own LightGBM model classifies land use locally.
- **16,173** routed calls logged, each with the reason for the model choice, cost and latency.
- **Privacy:** **0 of 14,202** owner-data calls reached a model that trains on inputs.
- **Critic from a different vendor: 100%** of challenges (24–26 per 8 questions).

| Same 8 questions, 3 routing policies | Valid plans | Verified claims | Critic checks | Median time |
|---|---|---|---|---|
| **Routed (ours)** | **8/8** | 84.9% | **26** | **41 s** |
| All open-weight | 0/8 (fell back to a default plan) | 0% | 0 | 86 s |
| All proprietary | 6/8 | 90% | 24 | 55 s |

**Reading:** routing gives the most reliable plans, the most cross-checking and the fastest answers. Open-only models could not plan or critique at all. Answer correctness still depends on the free planner model filling in optional filters, so our fix (automatic filters from the question) is the next step.

## 5. Trust and safety
| | |
|---|---|
| **928** backend + **14** UI automated tests | offline, no quota spent |
| SQL guard | **4/4** attacks refused (DROP, DELETE, stacked statements, reading owners); the agent's database role is read-only |
| Tamper-evident ledger | every agent step hash-chained; one command verifies it |
| Owner names | masked in the UI, never in reports or the repository; bank fields stripped |
| **12** self-corrections logged | e.g. a "100%" re-upload result that came from the cache; a check that missed row serials |

## 6. What makes it different
1. **Paper vs Planet:** we found no other system that checks acquisition papers against the satellite record, parcel by parcel.
2. **A control group, not just greenness:** activity after possession is compared with never-acquired farmland nearby (difference-in-differences), which removes rainfall and season effects.
3. **A critic from another vendor:** a different company's model must try to prove each claim wrong with a testable check.
4. **Privacy tiers built into routing:** owner data can only go to non-training providers, proven by the logs.
5. **Evidence for every number:** click any value to see it boxed on the scanned Tamil page.
6. **Works on new data, almost free:** upload a document or refresh the satellite, get a report, at about US$0.001 a page.

## Known limits (we say these first)
- **Extraction** is below target on fresh pages, and **compensation findings** often trip on merged cells and tree columns, so they are leads for review.
- **Agent speed** is ~41–55 s per question, and **answer accuracy varies** with the free planner model (8/8 → 3/8 between runs).
- The satellite model's macro-F1 is **0.634** (target 0.80). Outputs are signals for field verification, not verdicts.
- The **cloud version is read-only.** Uploads and the live agent run in the full app, because the event rules allow no database server.
