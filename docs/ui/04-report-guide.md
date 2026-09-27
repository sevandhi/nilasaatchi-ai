# The downloadable deliverable: Document verification report

When someone uploads a land-acquisition document, the application's main output is a **verification report** they can download. It answers four questions about the document:
1. **What is it?**
2. **What does it say?**
3. **Which parcels on the survey map does it concern?**
4. **Is anything inconsistent about those parcels?**

## How to get it
- **In the app:** Documents → *Upload a document* → wait for the 7 steps → click **Download report (PDF)** or **Download rows (CSV)**.
- **Direct link:** `http://localhost:8000/ingest/jobs/<job id>/report?fmt=pdf` (`fmt=csv` for rows, `fmt=html` to view in the browser).
- **Samples:** `data/demo_uploads/working/reports/` has one PDF + CSV for each of the 20 working documents.

Owner names are **never** included in the report, and bank-like fields are stripped.

## The PDF report, section by section
| Section | What it tells you | How to read it |
|---|---|---|
| **Header** | File name, upload job number, document number in the system, time generated | Use the job number to find the upload again in the app (Documents → Recent uploads) |
| **1. Summary** | The outcome in plain sentences: the document type (declared by the uploader, or the AI's guess, with the AI's suggestion shown either way); pages; rows read; facts and acquisition events recorded; how many parcels were linked; open findings on those parcels; pages sent for human review | Read this first. "No parcel could be linked" is an honest result, not an error: the document may have no readable survey numbers, be block-level, or belong to a village outside the map |
| **2. Processing steps** | The 7 steps (file saved → catalogued → document type → tables read → loaded → linked to parcels → checks run), each with its status and detail | A failed step says why in plain words (e.g. a damaged file, or the table reader unavailable) |
| **3. Linked parcels and where they stand** | Each parcel (Village\|survey/subdivision) with its **map area** (measured from the FMB survey map), the **extent this document gives**, its **current legal stage** (e.g. AWARD, POSSESSION, MUTATION), since when, and the next expected stage | Compare map area with document extent: a large gap is what an extent-mismatch finding flags. The stage shows how far acquisition has progressed for that parcel |
| **4. Findings on these parcels** | Every open finding on the linked parcels: severity, category **with a one-line explanation of what it means**, the specific detail (e.g. "Document extent 0.71 ha vs GIS 0.79 ha, 9.7%"), confidence | Findings are **signals for verification, not legal conclusions**. They cover all documents and satellite evidence for those parcels, not only this file. High severity first |
| **5. Rows read from the document** | Every table row the AI read: page, survey number, subdivision, extent (ha/ac), amount, patta number, land class, linked parcel, confidence, check status, review status | "check_status" = whether the page's arithmetic checks passed (sums, ha↔ac, survey sanity). Rows from pages that failed a check are in the Review queue: confirm them against the original before relying on them |
| **6. How to use this report** | Next steps | Open the parcel in the app to see the scanned page with the value boxed, the satellite timeline and the evidence behind each finding |

## The CSV (rows), column by column
One line per table row read from the document. It opens in Excel or Google Sheets.
| Column | Meaning |
|---|---|
| page | Page of the uploaded PDF the row was read from |
| survey_no, sub_div | Survey number and subdivision as printed (e.g. 202 / 1A) |
| extent_ha, extent_ac | Extent in hectares and acres as printed (both are checked against each other) |
| amount_rs | Compensation amount in rupees, where the table has one (awards, Form F) |
| patta_no | Patta (land record) number |
| classification | Land class (e.g. dry / wet), when printed |
| parcel_uid | The map parcel this row was linked to (Village\|survey/subdivision); empty = not linked |
| confidence | The reader's confidence for the row (0–1) |
| check_status | pass / fail / n/a for the page's automatic checks |
| review_status | Whether the row is waiting for, or has had, a human check |

## Why it is useful
- **Land officer:** a new award or LDR becomes, in seconds, a list of parcels, where each stands legally, and what doesn't add up, with the page to check.
- **Auditor / reviewer:** every value carries its page, confidence and check status, so nothing is taken on trust.
- **Business (FarmwiseAI):** the same report works for any uploaded document; nothing is tied to the sample dataset.

## Honest limits
- The tables are read by an AI (AWS Bedrock, Ministral 3 8B). Values can be wrong, which is why checks, confidence and the Review queue exist.
- Automatic document-type detection is weak without folder names. Choose the type when uploading if you know it; the report always shows what the AI suggested.
- Handwritten or blurred pages often yield no survey numbers, so no parcel link; they go to review.
