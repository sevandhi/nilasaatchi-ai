import { useMemo, useState } from "react";
import { flexRender, getCoreRowModel, getSortedRowModel, useReactTable } from "@tanstack/react-table";

/**
 * Generic sortable table with CSV export and a row-click hook, driven by a `spec.tables`
 * column list ({key,label,type}) plus rows — never hand-coded per query.
 * @param {{columns: {key:string,label:string,type?:string}[], rows: object[], onRowClick?: (row:object)=>void, selectedKey?: string, rowKey?: (row:object)=>string, csvName?: string}} props
 */
export function DataTable({ columns, rows, onRowClick, selectedKey, rowKey = (r) => JSON.stringify(r), csvName = "export" }) {
  const [sorting, setSorting] = useState([]);
  const colDefs = useMemo(
    () =>
      columns.map((c) => ({
        id: c.key,
        accessorKey: c.key,
        header: c.label,
        cell: (info) => formatCell(info.getValue(), c.type),
      })),
    [columns]
  );
  const table = useReactTable({
    data: rows,
    columns: colDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  function exportCsv() {
    const header = columns.map((c) => c.label).join(",");
    const body = rows
      .map((r) => columns.map((c) => csvEscape(r[c.key])).join(","))
      .join("\n");
    const blob = new Blob([`${header}\n${body}`], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${csvName}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  }

  return (
    <div>
      <div className="mb-2 flex justify-end">
        <button onClick={exportCsv} className="rounded border border-gray-300 px-2 py-1 text-xs font-medium text-gray-600 hover:bg-gray-50" data-testid="export-csv">
          Export CSV
        </button>
      </div>
      <div className="overflow-auto rounded-lg border border-gray-200">
        <table className="min-w-full divide-y divide-gray-200 text-sm">
          <thead className="bg-gray-50">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id}>
                {hg.headers.map((h) => (
                  <th
                    key={h.id}
                    onClick={h.column.getToggleSortingHandler()}
                    className="cursor-pointer select-none px-3 py-2 text-left font-medium text-gray-600"
                  >
                    {flexRender(h.column.columnDef.header, h.getContext())}
                    {{ asc: " ▲", desc: " ▼" }[h.column.getIsSorted()] || ""}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody className="divide-y divide-gray-100 bg-white">
            {table.getRowModel().rows.map((row) => {
              const key = rowKey(row.original);
              return (
                <tr
                  key={row.id}
                  onClick={() => onRowClick?.(row.original)}
                  className={`${onRowClick ? "cursor-pointer hover:bg-emerald-50" : ""} ${selectedKey === key ? "bg-emerald-100" : ""}`}
                >
                  {row.getVisibleCells().map((cell) => (
                    <td key={cell.id} className="px-3 py-1.5 text-gray-700">
                      {flexRender(cell.column.columnDef.cell, cell.getContext())}
                    </td>
                  ))}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function formatCell(v, type) {
  if (v === null || v === undefined) return "—";
  if (type === "number") return typeof v === "number" ? v.toLocaleString("en-IN") : v;
  if (type === "bool") return v ? "yes" : "no";
  return String(v);
}

function csvEscape(v) {
  if (v === null || v === undefined) return "";
  const s = String(v);
  return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
}
