This image is a zoomed crop of the table region of one scanned page from a Tamil Nadu land-acquisition document (SIPCOT, Thoothukudi district). Text may be Tamil, English or both. A first transcription of this page failed its arithmetic checks, so read every cell slowly and independently.

Transcribe the table(s) into the JSON schema. Rules:

1. `tables`: every table visible in the crop, top to bottom. If the crop shows no table, return `"tables": []`. Never output a value that is not visible in the crop.
   - Column order: output the columns in this canonical order when they are present — serial number; survey number (புல எண்); sub-division (உட்பிரிவு); extent in hectares; extent in acres (or cents); patta number; owner / interested persons; classification; amount; then every remaining column in printed left-to-right order. Label each column with its printed header text (join two header levels as "parent / child"). Each row's cells must follow the same column order as `columns`.
   - Copy each cell exactly as printed: keep Tamil script; keep both dots of hectare extents printed as H.AA.SS, or the H - AA.SS form; keep acre decimals, rupee amounts with their commas and "/-", and survey numbers with "/" and sub-division letters or comma lists. Never convert, compute or correct.
   - A cell merged across several rows: repeat its text in every row it spans. Several owners in one cell: one owner per line (`\n`), keeping their printed numbering.
   - Include printed total rows (மொத்தம் / Total). Use null for an empty, dashed or illegible cell; never guess.
2. `title` and `header`: null unless printed inside the crop.
3. `handwritten`: true if any table value is handwritten. `legibility`: "clear", "partly_blurred" or "blurred".

{doc_hint}
Return JSON only.
