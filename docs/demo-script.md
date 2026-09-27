# 8-minute demo script: NilaSaatchi AI (Paper vs Planet)

**Two ways to show it**
- **Cloud (read-only, no setup):** https://lv7b9630q6.execute-api.ap-south-1.amazonaws.com/. Every page, map, parcel and finding works. Uploading, the live agent and review actions need the full app.
- **Full app (local):** `make demo`, then http://localhost:5173. Before the upload step, run `make aws-login`. After the demo, run `uv run python scripts/demo_uploads.py cleanup`.

Keep **Demo mask ON**. Every number below was measured on the live system.

| Time | Screen | Click / say | What they see |
|---|---|---|---|
| 0:00 | Overview | "Land-acquisition papers make claims; the satellite shows what really happened on the ground. We cross-examine them." | 2,362 documents, 1,242 parcels, 42,380 facts, 1,388 findings, with the SQL behind each number |
| 0:45 | Map | Colour by **Acquisition stage**, then **Land-use state**; drag the season 2021-rabi → 2021-summer | Every parcel's legal stage; green cropped vs beige bare seasons |
| 1:45 | Parcel | Open `/parcel/Melathattaparai%7C233` | **The hook:** NDVI 2019→2026 with the document events on top. After possession, a ploughing signal stronger than never-acquired farmland (DiD +2.15, CI 0.85–3.70), shown as a *lead*, not a verdict |
| 2:45 | Parcel → Documents panel | Click an extraction | The scanned Tamil page with the value boxed, its confidence and checks |
| 3:15 | Findings | Category **COMPENSATION_MISMATCH**, severity **high**, then open **1977** | Evidence pack: survey 227, 2.125 ac should get about ₹10.6 lakh at the page's ₹5 lakh/acre, but was paid far less. The finding shows ₹2,52,108 (land ₹2,29,208 + trees ₹22,900); every other row on the page matches the rate exactly (verified on the scanned page) |
| 4:00 | Agent console (local) | Example "Verify Melathattaparai survey 233…" → **Run** | Live plan, tools, "why this model", verifier ✓, critic from a *different vendor*, judge, answer, hash-chained ledger → **Verify ledger** |
| 5:30 | Documents (local) | Upload `data/demo_uploads/working/06_AWARD_7_2_ramasamypuram.pdf`, type **Award 7(2)** | 7 steps in ~70 s; 10 parcels linked; **Download report (PDF)** |
| 6:30 | Models & routing | Scroll | Each model's role, privacy, health; **0** owner-data calls to models that train on inputs (of 14,202); spend vs the US$100 budget |
| 7:15 | Close | "Built on free models + AWS Bedrock; the cloud demo runs on Lambda, which now calls Bedrock directly. Findings are signals for field verification." | — |

## Fallbacks
- **Agent slow or out of quota:** open a finished run from *Run history* and replay it.
- **AWS login expired:** skip the upload and show a ready report from `data/demo_uploads/working/reports/`.
- **No internet:** the local app still works (only satellite chips and the agent need the internet).

## Questions reviewers may ask
- **Accuracy?** Unseen pages: survey 89.5%, owner 83.3%, extent 79.2%. That is below target, so uncertain rows go to the Review queue.
- **Is it hard-coded?** No. The unseen and Tamil agent questions pass 8/8, and the 20 unseen uploads all completed (387 parcel links).
- **Privacy?** Owner names are masked, bank fields stripped, the agent's database role can't read owners, and PII never goes to a model that trains on inputs.
