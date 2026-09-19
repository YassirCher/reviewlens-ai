"use client";

import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { flexRender, getCoreRowModel, useReactTable, type ColumnDef } from "@tanstack/react-table";
import { adminGet } from "@/lib/admin";

export function useAdminData<T>(path: string | null) {
  const [result, setResult] = useState<{ path: string; data: T | null; error: string } | null>(null);
  const [busy, setBusy] = useState(false);
  const reload = useCallback(async () => {
    if (!path) return;
    setBusy(true);
    try { setResult({ path, data: await adminGet<T>(path), error: "" }); }
    catch (value) { setResult({ path, data: null, error: value instanceof Error ? value.message : "Data unavailable." }); }
    finally { setBusy(false); }
  }, [path]);
  useEffect(() => {
    if (!path) return;
    let active = true;
    adminGet<T>(path).then(data => { if (active) setResult({ path, data, error: "" }); })
      .catch(value => { if (active) setResult({ path, data: null, error: value instanceof Error ? value.message : "Data unavailable." }); });
    return () => { active = false; };
  }, [path]);
  return { data: path && result?.path === path ? result.data : null,
    loading: Boolean(path) && (busy || result?.path !== path),
    error: path && result?.path === path ? result.error : "", reload };
}

export function AdminHeading({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: React.ReactNode }) {
  return <div className="admin-heading"><div><span className="admin-eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions && <div className="admin-actions">{actions}</div>}</div>;
}

export function Status({ value }: { value: string }) { return <span className={`admin-status admin-status-${value.replace(/[^a-z]/g, "")}`}>{value.replaceAll("_", " ")}</span>; }

export function AdminState({ loading, error, empty, children }: { loading: boolean; error: string; empty?: boolean; children: React.ReactNode }) {
  if (loading) return <div className="admin-banner" role="status">Loading admin data…</div>;
  if (error) return <div className="admin-banner admin-banner-error" role="alert">{error}</div>;
  if (empty) return <div className="admin-banner">No records match these filters.</div>;
  return <>{children}</>;
}

export function DataTable<T>({ data, columns, caption }: { data: T[]; columns: ColumnDef<T, unknown>[]; caption: string }) {
  // TanStack's table instance is intentionally mutable; React Compiler skips this hook.
  // eslint-disable-next-line react-hooks/incompatible-library
  const table = useReactTable({ data, columns, getCoreRowModel: getCoreRowModel() });
  return <div className="admin-table-scroll"><table className="admin-table"><caption className="v2-sr-only">{caption}</caption><thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => <th key={header.id} scope="col">{header.isPlaceholder ? null : flexRender(header.column.columnDef.header, header.getContext())}</th>)}</tr>)}</thead><tbody>{table.getRowModel().rows.map(row => <tr key={row.id}>{row.getVisibleCells().map(cell => <td key={cell.id}>{flexRender(cell.column.columnDef.cell, cell.getContext())}</td>)}</tr>)}</tbody></table></div>;
}

export function ConfirmAction({ label, title, description, confirmation, danger, onConfirm, disabled }: {
  label: string; title: string; description: string; confirmation: string; danger?: boolean;
  onConfirm: () => Promise<void>; disabled?: boolean;
}) {
  const [open, setOpen] = useState(false); const [typed, setTyped] = useState("");
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  async function confirm() { setBusy(true); setError(""); try { await onConfirm(); setOpen(false); setTyped(""); } catch (value) { setError(value instanceof Error ? value.message : "Action failed."); } finally { setBusy(false); } }
  return <Dialog.Root open={open} onOpenChange={setOpen}><Dialog.Trigger asChild><button className={danger ? "admin-danger" : "admin-secondary"} disabled={disabled}>{label}</button></Dialog.Trigger><Dialog.Portal><Dialog.Overlay className="admin-dialog-overlay" /><Dialog.Content className="admin-dialog"><Dialog.Title>{title}</Dialog.Title><Dialog.Description>{description}</Dialog.Description><label className="admin-field">Type <strong>{confirmation}</strong> to continue<input value={typed} onChange={event => setTyped(event.target.value)} autoComplete="off" /></label><p className="admin-form-error" role="alert">{error}</p><div className="admin-dialog-actions"><Dialog.Close className="admin-secondary">Cancel</Dialog.Close><button className={danger ? "admin-danger" : "admin-primary"} disabled={typed !== confirmation || busy} onClick={confirm}>{busy ? "Working…" : label}</button></div><Dialog.Close className="admin-dialog-close" aria-label="Close"><X size={18} /></Dialog.Close></Dialog.Content></Dialog.Portal></Dialog.Root>;
}
