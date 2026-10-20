import { useCallback, useEffect, useRef, useState } from "react";
import {
  downloadItemVendors,
  downloadItemVendorsTemplate,
  importItemVendors,
  listItemVendors,
  saveItemVendors,
} from "../clients/clover.js";
import {
  PAGE_SIZE,
  SHOW_KEY,
  SHOW_OPTIONS,
  addRow,
  editorRows,
  importSummary,
  itemVendorsErrorMessage,
  moveRow,
  previewRows,
  readShow,
  removeRow,
  rowsChanged,
  validateRows,
  vendorSummary,
} from "../settings/itemVendorsModel.js";
import "../platform/platform.css";
import "../settings/trackD.css";
import EmptyState from "../ui/EmptyState.jsx";
import Icon from "../ui/icons.jsx";
import ListHeader from "../ui/ListHeader.jsx";
import PageHeader from "../ui/PageHeader.jsx";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

// Setup > Item vendors (docs/WEBSITE_READY_CONTRACT.md D4): who supplies each
// item, for a store whose POS has no vendor field (Clover, CSV-only). One
// screen: a slim frozen header (search, the three filters, ⓘ), the items as
// rows, an inline editor per item, and the CSV import panel. A Toast store
// sees the list read-only (its inventory file holds the vendors). Track A
// routes it at /setup/item-vendors (lazy, default export, no props).
export default function ItemVendorsPage() {
  const [q, setQ] = useState("");
  const [show, setShowState] = useState(() => readShow(storage()));
  const [data, setData] = useState(null);
  const [items, setItems] = useState([]);
  const [error, setError] = useState(null);
  const [loading, setLoading] = useState(false);
  const [editing, setEditing] = useState(null);
  const [importOpen, setImportOpen] = useState(false);
  const [toast, setToast] = useState(null);
  const reqId = useRef(0);

  const load = useCallback(
    async (offset = 0) => {
      const id = ++reqId.current;
      setLoading(true);
      try {
        const r = await listItemVendors({ q, show, limit: PAGE_SIZE, offset });
        if (id !== reqId.current) return;
        setData(r);
        setItems((prev) => (offset ? [...prev, ...(r.items || [])] : r.items || []));
        setError(null);
      } catch (err) {
        if (id !== reqId.current) return;
        setError(itemVendorsErrorMessage(err));
        if (!offset) setItems([]);
      } finally {
        if (id === reqId.current) setLoading(false);
      }
    },
    [q, show],
  );

  useEffect(() => {
    const t = setTimeout(() => load(0), q ? 250 : 0);
    return () => clearTimeout(t);
  }, [load, q]);

  function setShow(v) {
    setShowState(v);
    setEditing(null);
    try {
      storage()?.setItem(SHOW_KEY, v);
    } catch {
      // not remembered
    }
  }

  function saved(item) {
    setItems((prev) => prev.map((i) => (i.item_id === item.item_id ? { ...i, vendors: item.vendors } : i)));
    setEditing(null);
    setToast(`Saved vendors for ${item.name || item.item_id}.`);
    load(0);
  }

  const editable = !!data?.editable;
  const counts = {
    all: data?.catalog_total ?? null,
    missing: data?.missing_count ?? null,
    with: data ? Math.max(0, (data.catalog_total || 0) - (data.missing_count || 0)) : null,
  };

  return (
    <div className="iv-page">
      <PageHeader
        pageId="item_vendors"
        subtitle="Who supplies each item - used by reorder, New order, purchase orders and Fix Issues."
        actions={
          <>
            <button
              type="button"
              className="btn-secondary"
              onClick={() => downloadItemVendors().catch((e) => setError(itemVendorsErrorMessage(e)))}
              data-testid="iv-export"
            >
              Export CSV
            </button>
            {editable && (
              <button type="button" onClick={() => setImportOpen((o) => !o)} aria-expanded={importOpen} data-testid="iv-import-open">
                Import CSV
              </button>
            )}
          </>
        }
      />

      {data && !editable && (
        <div className="pf-banner neutral iv-banner" role="note" data-testid="iv-read-only">
          {data.read_only_reason}
        </div>
      )}

      {importOpen && editable && (
        <ImportPanel
          onClose={() => setImportOpen(false)}
          onApplied={(r) => {
            setToast(importSummary(r));
            setImportOpen(false);
            load(0);
          }}
        />
      )}

      <ListHeader
        search={q}
        onSearch={setQ}
        placeholder="Search items, barcodes or vendors"
        pageId="item_vendors"
        chips={SHOW_OPTIONS.map((o) => (
          <button
            key={o.value}
            type="button"
            className={`iv-chip${show === o.value ? " is-on" : ""}`}
            aria-pressed={show === o.value}
            onClick={() => setShow(o.value)}
            data-testid={`iv-show-${o.value}`}
          >
            {o.label}
            {counts[o.value] != null && <span className="iv-chip-count">{counts[o.value]}</span>}
          </button>
        ))}
      />

      {toast && (
        <div className="pf-banner ok iv-banner iv-toast" role="status" data-testid="iv-toast">
          <span>{toast}</span>
          <button type="button" className="kc-icon-btn" aria-label="Dismiss" onClick={() => setToast(null)}>
            <Icon name="x" size={16} />
          </button>
        </div>
      )}
      {error && (
        <div className="pf-banner bad iv-banner" role="alert">
          {error}{" "}
          <button type="button" className="link-button" onClick={() => load(0)}>
            Retry
          </button>
        </div>
      )}

      {data === null && !error ? (
        <ul className="iv-list" aria-busy="true" aria-label="Loading items">
          {[0, 1, 2, 3].map((i) => (
            <li key={i} className="iv-row iv-skeleton" />
          ))}
        </ul>
      ) : items.length === 0 && !error ? (
        <div className="card">
          <EmptyState icon="truck" area="setup" title={data?.catalog_total ? "No items match" : "No items yet"}>
            {data?.catalog_total
              ? show === "missing"
                ? "Every item has a vendor."
                : "Try another search or filter."
              : "Items appear here after the first inventory sync or upload."}
          </EmptyState>
        </div>
      ) : (
        <>
          <ul className="iv-list" data-testid="iv-list">
            {items.map((item) => (
              <li key={item.item_id} className={`iv-row${editing === item.item_id ? " is-editing" : ""}`}>
                <div className="iv-row-main">
                  <div className="iv-item">
                    <span className="iv-item-name">{item.name || item.item_id}</span>
                    <span className="iv-item-meta">{[item.barcode, item.category].filter(Boolean).join(" · ") || item.item_id}</span>
                  </div>
                  <div className="iv-vendors" aria-label={`Vendors: ${vendorSummary(item.vendors)}`}>
                    {item.vendors.length === 0 ? (
                      <span className="iv-none">No vendor</span>
                    ) : (
                      item.vendors.map((v, i) => (
                        <span key={v.name} className={`iv-vendor${i === 0 ? " is-first" : ""}`}>
                          {v.name}
                          {v.item_code && <span className="iv-code">{v.item_code}</span>}
                        </span>
                      ))
                    )}
                  </div>
                  {editable && editing !== item.item_id && (
                    <button
                      type="button"
                      className="btn-secondary iv-edit"
                      onClick={() => setEditing(item.item_id)}
                      aria-label={`Edit vendors for ${item.name || item.item_id}`}
                      data-testid={`iv-edit-${item.item_id}`}
                    >
                      Edit
                    </button>
                  )}
                </div>
                {editing === item.item_id && (
                  <VendorEditor item={item} vendorNames={data?.vendor_names || []} onCancel={() => setEditing(null)} onSaved={saved} />
                )}
              </li>
            ))}
          </ul>
          {data && items.length < data.total && (
            <div className="iv-more">
              <button type="button" className="btn-secondary" onClick={() => load(items.length)} disabled={loading}>
                {loading ? "Loading…" : `Show more (${data.total - items.length} left)`}
              </button>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function VendorEditor({ item, vendorNames, onCancel, onSaved }) {
  const [rows, setRows] = useState(() => (item.vendors.length ? editorRows(item.vendors) : addRow([])));
  const [errors, setErrors] = useState({});
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState(null);
  const firstInput = useRef(null);
  const listId = `iv-names-${String(item.item_id).replace(/[^A-Za-z0-9_-]/g, "_")}`;

  useEffect(() => {
    firstInput.current?.focus();
  }, []);

  function set(i, field, value) {
    setRows((rs) => rs.map((r, j) => (j === i ? { ...r, [field]: value } : r)));
  }

  async function save(e) {
    e.preventDefault();
    const { errors: errs, vendors } = validateRows(rows);
    setErrors(errs);
    if (Object.keys(errs).length) return;
    setBusy(true);
    setMessage(null);
    try {
      onSaved(await saveItemVendors(item.item_id, vendors));
    } catch (err) {
      setMessage(itemVendorsErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const changed = rowsChanged(rows, item.vendors);

  return (
    <form
      className="iv-editor"
      onSubmit={save}
      onKeyDown={(e) => {
        if (e.key === "Escape") onCancel();
      }}
      aria-label={`Vendors for ${item.name || item.item_id}`}
      data-testid="iv-editor"
    >
      <datalist id={listId}>
        {vendorNames.map((n) => (
          <option key={n} value={n} />
        ))}
      </datalist>
      <p className="iv-editor-hint">The first vendor is the first choice when ordering.</p>
      <ol className="iv-editor-rows">
        {rows.map((r, i) => (
          <li key={r.key} className="iv-editor-row">
            <span className="iv-rank" aria-hidden="true">
              {i + 1}
            </span>
            <label className="iv-field iv-field-name">
              <span className="iv-label">Vendor</span>
              <input
                ref={i === 0 ? firstInput : undefined}
                type="text"
                list={listId}
                value={r.name}
                maxLength={80}
                aria-invalid={!!errors[i]}
                onChange={(e) => set(i, "name", e.target.value)}
                data-testid={`iv-name-${i}`}
              />
            </label>
            <label className="iv-field iv-field-code">
              <span className="iv-label">Vendor item code (optional)</span>
              <input type="text" value={r.item_code} maxLength={64} onChange={(e) => set(i, "item_code", e.target.value)} />
            </label>
            <div className="iv-row-tools">
              <button
                type="button"
                className="kc-icon-btn iv-up"
                aria-label={`Move ${r.name || "vendor"} up`}
                disabled={i === 0}
                onClick={() => setRows(moveRow(rows, i, -1))}
              >
                <Icon name="chevron-down" size={16} />
              </button>
              <button
                type="button"
                className="kc-icon-btn"
                aria-label={`Move ${r.name || "vendor"} down`}
                disabled={i === rows.length - 1}
                onClick={() => setRows(moveRow(rows, i, 1))}
              >
                <Icon name="chevron-down" size={16} />
              </button>
              <button type="button" className="kc-icon-btn iv-remove" aria-label={`Remove ${r.name || "vendor"}`} onClick={() => setRows(removeRow(rows, i))}>
                <Icon name="trash-2" size={16} />
              </button>
            </div>
            {errors[i] && (
              <span className="field-error iv-row-error" role="alert">
                {errors[i]}
              </span>
            )}
          </li>
        ))}
      </ol>
      {errors.list && (
        <p className="field-error" role="alert">
          {errors.list}
        </p>
      )}
      {message && (
        <p className="pf-banner bad" role="alert">
          {message}
        </p>
      )}
      <div className="iv-editor-actions">
        <button type="button" className="link-button iv-add" onClick={() => setRows(addRow(rows))} disabled={rows.length >= 10}>
          <Icon name="plus" size={14} /> Add vendor
        </button>
        <span className="iv-spacer" />
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Cancel
        </button>
        <button type="submit" disabled={busy || !changed} data-testid="iv-save">
          {busy ? "Saving…" : "Save"}
        </button>
      </div>
    </form>
  );
}

function ImportPanel({ onClose, onApplied }) {
  const [file, setFile] = useState(null);
  const [preview, setPreview] = useState(null);
  const [busy, setBusy] = useState(null);
  const [message, setMessage] = useState(null);

  async function check(f) {
    setFile(f);
    setPreview(null);
    setMessage(null);
    if (!f) return;
    setBusy("preview");
    try {
      setPreview(await importItemVendors(f));
    } catch (err) {
      setMessage(itemVendorsErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  async function apply() {
    setBusy("apply");
    setMessage(null);
    try {
      onApplied(await importItemVendors(file, { apply: true }));
    } catch (err) {
      setMessage(itemVendorsErrorMessage(err));
    } finally {
      setBusy(null);
    }
  }

  const shown = previewRows(preview);

  return (
    <section className="card iv-import" aria-labelledby="iv-import-title" data-testid="iv-import">
      <div className="iv-import-head">
        <h2 id="iv-import-title">Import from a spreadsheet</h2>
        <button type="button" className="kc-icon-btn" aria-label="Close import" onClick={onClose}>
          <Icon name="x" size={16} />
        </button>
      </div>
      <p className="muted iv-import-text">
        One row per item and vendor: <strong>Item ID</strong> (or Barcode, or Item name), <strong>Vendor</strong>, and optionally{" "}
        <strong>Vendor item code</strong> and <strong>Rank</strong> (1 = first choice). An item's rows replace its list. The file is
        checked first; nothing changes until you apply it.{" "}
        <button type="button" className="link-button" onClick={() => downloadItemVendorsTemplate().catch(() => {})}>
          Download the template
        </button>
      </p>
      <label className="iv-file">
        <span className="iv-label">CSV file</span>
        <input type="file" accept=".csv,text/csv" onChange={(e) => check(e.target.files?.[0] || null)} data-testid="iv-file" />
      </label>
      {busy === "preview" && <p className="muted">Checking the file…</p>}
      {message && (
        <p className="pf-banner bad" role="alert">
          {message}
        </p>
      )}
      {preview && (
        <div className="iv-preview" data-testid="iv-preview">
          <p className="iv-preview-summary" role="status">
            {importSummary(preview)}
          </p>
          {shown.length > 0 && (
            <ul className="iv-preview-rows">
              {shown.map((r) => (
                <li key={r.line} className={r.error ? "is-error" : "is-note"}>
                  <span className="iv-line">Row {r.line}</span>
                  <span className="iv-preview-item">{[r.item_name || r.item_id, r.vendor].filter(Boolean).join(" · ") || "-"}</span>
                  <span className="iv-preview-msg">{r.error || r.note}</span>
                </li>
              ))}
            </ul>
          )}
          <div className="iv-editor-actions">
            <span className="iv-spacer" />
            <button type="button" className="btn-secondary" onClick={onClose}>
              Cancel
            </button>
            <button type="button" onClick={apply} disabled={!!busy || !preview.items_to_change} data-testid="iv-apply">
              {busy === "apply"
                ? "Applying…"
                : `Apply to ${preview.items_to_change} ${preview.items_to_change === 1 ? "item" : "items"}`}
            </button>
          </div>
        </div>
      )}
    </section>
  );
}
