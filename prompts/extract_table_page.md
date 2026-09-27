This is one scanned page of a Tamil Nadu land-acquisition document (SIPCOT, Thoothukudi district). It may be in Tamil, English or both.

Transcribe the page into the JSON schema. Rules:

1. `tables`: transcribe EVERY table that is actually printed on this page, in reading order (left table before right table, top before bottom). If the page is prose, a letter or a form with no ruled or aligned table, return `"tables": []`. Never build a table out of prose sentences, and never output any value that is not visible on this page.
   - `columns`: one label per column, exactly as printed. If the header has two levels (for example "விஸ்தீரணம்" over "ஹெக்டேர் | ஏக்கர்", or "Patta (Dry)" over "In Hect | In Acre"), join them as "parent / child" so that there is exactly one label per data column. Do not output a header row as a data row.
   - `rows`: one list per printed row, with exactly one cell per column, in column order. Copy each cell exactly as printed, character for character: keep Tamil script; keep every dot in hectare extents printed as H.AA.SS (three groups separated by two dots) or H - AA.SS; keep acre decimals, rupee amounts with their commas and "/-", and survey numbers with their "/" and sub-division letters or comma lists. Never convert units, never compute, never correct.
   - If survey number and sub-division are printed in separate columns, keep them in separate cells.
   - If one cell is merged across several rows (for example one owner or patta number for five survey rows), repeat its text in every row it spans.
   - Several owners in one cell: keep their printed numbering and put each owner on its own line (`\n`).
   - Include printed total rows (மொத்தம் / Total) as rows.
   - Use null for an empty, dashed-out or illegible cell. Never guess a value you cannot read.
2. `title`: the main heading line(s) of the page as printed (for example "படிவம் - E", "LAND DELIVERY CERTIFICATE", "FORM - F"), or null.
3. `header`: fields printed in the page heading or reference lines, as printed: village (கிராமம்), taluk (வட்டம்), unit (அலகு), block (பிளாக் / தொகுதி), document or proceedings number (ந.க.எண், Roc.No., Lr.No., G.O. No.) and its date (நாள் / Date). Use null when not printed.
4. `handwritten`: true if any table value is handwritten. `legibility`: "clear", "partly_blurred" or "blurred".

{doc_hint}
Return JSON only.
