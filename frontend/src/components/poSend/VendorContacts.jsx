import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import {
  createVendorContact,
  deleteVendorContact,
  getCart,
  getPoSends,
  getVendorContacts,
  updateVendorContact,
} from "../../api";
import { formatRestaurantTime } from "../../format";
import { SendStatusPill } from "./SendPoDialog.jsx";
import PoDownloadMenu from "../poDownload/PoDownloadMenu.jsx";
import { Term } from "../../glossary.jsx";
import {
  contactForm,
  contactPayload,
  contactProblem,
  groupContacts,
  vendorKey,
  vendorsMissingEmail,
} from "./poSendModel.js";

// Who receives each vendor's purchase order when someone presses Send PO in
// the cart. Admins add / edit / delete; everyone with the cart can look.
// "?vendor=X" (the cart's Contacts links) filters to X and, for an admin
// with no contact for X yet, opens the add form for it.
export default function VendorContacts({ isAdmin }) {
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);
  const [search, setSearch] = useState("");
  const [cartVendors, setCartVendors] = useState([]);
  const [sends, setSends] = useState(null);
  // { id: null | contact id, form } while the add / edit form is open.
  const [editing, setEditing] = useState(null);
  const [formError, setFormError] = useState(null);
  const [saving, setSaving] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(null);
  const [rowError, setRowError] = useState(null);
  const location = useLocation();
  const navigate = useNavigate();
  const handledLink = useRef(null);
  const formRef = useRef(null);

  async function load() {
    try {
      const result = await getVendorContacts();
      setData(result);
      setError(null);
      return result;
    } catch (err) {
      setError(err.message);
      return null;
    }
  }

  useEffect(() => {
    load();
    getCart()
      .then((c) => setCartVendors((c.suppliers || []).map((s) => s.supplier)))
      .catch(() => {});
    getPoSends(null, 15)
      .then((r) => setSends(r.items || []))
      .catch(() => setSends([]));
  }, []);

  // "?vendor=X": filter to X; an admin with no contact for X gets the form.
  useEffect(() => {
    if (!data || handledLink.current === location.key) return;
    handledLink.current = location.key;
    const vendor = new URLSearchParams(location.search).get("vendor");
    if (!vendor) return;
    setSearch(vendor);
    const has = data.items.some((c) => vendorKey(c.vendor) === vendorKey(vendor));
    if (isAdmin && !has) openForm(null, vendor);
    navigate("/vendor-contacts", { replace: true });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, location.key]);

  useEffect(() => {
    if (editing) formRef.current?.querySelector("input, select, textarea")?.focus({ preventScroll: false });
    // Focus once when the form opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editing?.id, editing === null]);

  function openForm(contact, vendor = "") {
    setFormError(null);
    setRowError(null);
    setEditing({ id: contact ? contact.id : null, form: contactForm(contact, vendor) });
  }

  function setField(field, value) {
    setEditing((prev) => ({ ...prev, form: { ...prev.form, [field]: value } }));
  }

  async function save(e) {
    e.preventDefault();
    if (!editing || saving) return;
    const problem = contactProblem(editing.form);
    if (problem) {
      setFormError(problem);
      return;
    }
    setSaving(true);
    setFormError(null);
    try {
      const payload = contactPayload(editing.form);
      if (editing.id) await updateVendorContact(editing.id, payload);
      else await createVendorContact(payload);
      setEditing(null);
      setSearch((s) => s || payload.vendor);
      await load();
    } catch (err) {
      setFormError(err.message);
    } finally {
      setSaving(false);
    }
  }

  async function remove(contact) {
    setRowError(null);
    try {
      await deleteVendorContact(contact.id);
      setConfirmDelete(null);
      await load();
    } catch (err) {
      setRowError(`${contact.vendor}: ${err.message}`);
    }
  }

  const groups = useMemo(() => groupContacts(data?.items || [], search), [data, search]);
  const missing = useMemo(() => vendorsMissingEmail(cartVendors, data?.items || []), [cartVendors, data]);

  if (!data && !error) {
    return (
      <section className="card">
        <p className="muted">Loading contacts…</p>
      </section>
    );
  }

  return (
    <>
      <section className="card vc-card" aria-labelledby="vc-title">
        <div className="vc-head">
          <h2 id="vc-title" className="vc-title">
            Contacts
          </h2>
          <div className="vc-tools">
            <input
              type="search"
              className="vc-search"
              placeholder="Search vendor, name, email…"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              aria-label="Search contacts"
            />
            {isAdmin && (
              <button type="button" className="vc-add" onClick={() => openForm(null, search.trim() && groups.length === 0 ? search.trim() : "")}>
                Add contact
              </button>
            )}
          </div>
        </div>

        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}

        {data && (
          <p className="pos-muted-small vc-status">
            Email: {data.email_configured ? "set up" : "not set up for this store yet - Send PO can't email until it is"}.{" "}
            WhatsApp: {data.whatsapp_enabled ? "on" : "off"}
            {!data.whatsapp_enabled && data.whatsapp_off_reason ? ` - ${data.whatsapp_off_reason}` : "."}
          </p>
        )}
        {!isAdmin && <p className="pos-muted-small">Only an admin can add or change contacts.</p>}

        {missing.length > 0 && (
          <div className="vc-missing">
            <span className="pos-muted-small">In the cart with no email on file:</span>
            {missing.map((v) =>
              isAdmin ? (
                <button key={v} type="button" className="vc-chip" onClick={() => openForm(null, v)}>
                  + {v}
                </button>
              ) : (
                <span key={v} className="vc-chip is-static">
                  {v}
                </span>
              ),
            )}
          </div>
        )}

        {editing && (
          <form className="vc-form" onSubmit={save} ref={formRef} aria-label={editing.id ? "Edit contact" : "Add contact"}>
            <h3 className="vc-form-title">{editing.id ? "Edit contact" : "Add contact"}</h3>
            <div className="vc-form-grid">
              <label>
                Vendor
                <input
                  type="text"
                  list="vc-vendor-list"
                  value={editing.form.vendor}
                  onChange={(e) => setField("vendor", e.target.value)}
                  maxLength={200}
                  required
                />
              </label>
              <label>
                Contact name <span className="pos-muted-small">(optional)</span>
                <input
                  type="text"
                  value={editing.form.contact_name}
                  onChange={(e) => setField("contact_name", e.target.value)}
                  maxLength={120}
                />
              </label>
              <label>
                Email
                <input
                  type="email"
                  value={editing.form.email}
                  onChange={(e) => setField("email", e.target.value)}
                  maxLength={254}
                  autoComplete="off"
                />
              </label>
              <label>
                WhatsApp number or group id <span className="pos-muted-small">(optional)</span>
                <input
                  type="text"
                  value={editing.form.whatsapp}
                  onChange={(e) => setField("whatsapp", e.target.value)}
                  maxLength={60}
                  placeholder="(860) 555-0101"
                />
              </label>
              <label className="vc-form-wide">
                Default CC <span className="pos-muted-small">(optional - up to 10 emails, separated by commas)</span>
                <input
                  type="text"
                  inputMode="email"
                  value={editing.form.cc}
                  onChange={(e) => setField("cc", e.target.value)}
                  maxLength={2600}
                  autoComplete="off"
                  placeholder="owner@example.com, accounts@vendor.com"
                  aria-describedby="vc-cc-help"
                />
                <span id="vc-cc-help" className="pos-muted-small">
                  Copied on this contact's POs. Send PO fills these in; they can be changed for one send.
                </span>
              </label>
              <label className="vc-form-wide">
                Notes <span className="pos-muted-small">(optional, only seen here)</span>
                <textarea
                  rows={2}
                  value={editing.form.notes}
                  onChange={(e) => setField("notes", e.target.value)}
                  maxLength={1000}
                />
              </label>
              <label className="pos-check vc-form-wide">
                <input
                  type="checkbox"
                  checked={editing.form.send_po}
                  onChange={(e) => setField("send_po", e.target.checked)}
                />
                Send this vendor's POs to this email (ticked by default in Send PO)
              </label>
            </div>
            <datalist id="vc-vendor-list">
              {data.vendors.map((v) => (
                <option key={v} value={v} />
              ))}
            </datalist>
            {formError && (
              <p className="error" role="alert">
                {formError}
              </p>
            )}
            <div className="vc-form-actions">
              <button type="button" className="btn-secondary" onClick={() => setEditing(null)} disabled={saving}>
                Cancel
              </button>
              <button type="submit" className="vc-save" disabled={saving}>
                {saving ? "Saving…" : "Save contact"}
              </button>
            </div>
          </form>
        )}

        {rowError && (
          <p className="error" role="alert">
            {rowError}
          </p>
        )}

        {data && data.items.length === 0 && !editing && (
          <p className="muted vc-empty">
            No vendor contacts yet.{isAdmin ? " Add one so Send PO in the cart knows where to email." : ""}
          </p>
        )}
        {data && data.items.length > 0 && groups.length === 0 && <p className="muted">No contacts match "{search}".</p>}

        <div className="vc-groups">
          {groups.map((g) => (
            <div key={vendorKey(g.vendor)} className="vc-group">
              <h3 className="vc-vendor">{g.vendor}</h3>
              <ul className="vc-list">
                {g.contacts.map((c) => (
                  <li key={c.id} className="vc-row">
                    <div className="vc-row-main">
                      <span className="vc-name">{c.contact_name || "No name"}</span>
                      {c.email && <span className="vc-email">{c.email}</span>}
                      {c.whatsapp && <span className="vc-wa">WhatsApp {c.whatsapp}</span>}
                      {c.email && (
                        <Term k="vendor_contact_gets_pos" label="Gets POs">
                          <span className={`pill ${c.send_po ? "pill-success" : "pill-neutral"} vc-pill`}>
                            {c.send_po ? "Gets POs" : "Not on POs"}
                          </span>
                        </Term>
                      )}
                      {c.cc?.length > 0 && <span className="vc-cc">CC {c.cc.join(", ")}</span>}
                      {c.notes && <span className="vc-notes">{c.notes}</span>}
                    </div>
                    {isAdmin && (
                      <div className="vc-row-actions">
                        {confirmDelete === c.id ? (
                          <span role="group" aria-label={`Delete ${c.contact_name || c.email || "contact"}`}>
                            <span className="pos-muted-small">Delete?</span>{" "}
                            <button type="button" className="link-button" onClick={() => setConfirmDelete(null)}>
                              Cancel
                            </button>{" "}
                            <button type="button" className="po-confirm-danger" onClick={() => remove(c)}>
                              Yes, delete
                            </button>
                          </span>
                        ) : (
                          <>
                            <button type="button" className="link-button" onClick={() => openForm(c)}>
                              Edit
                            </button>
                            <button type="button" className="link-button link-danger" onClick={() => setConfirmDelete(c.id)}>
                              Delete
                            </button>
                          </>
                        )}
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      </section>

      <section className="card vc-card" aria-labelledby="vc-sends-title">
        <h2 id="vc-sends-title" className="vc-title">
          Recent PO sends
        </h2>
        {sends === null && <p className="muted">Loading…</p>}
        {sends && sends.length === 0 && <p className="muted">No POs sent from the app yet.</p>}
        {sends && sends.length > 0 && (
          <div className="table-wrap">
            <table className="kc-table--stack">
              <thead>
                <tr>
                  <th>When</th>
                  <th>Vendor</th>
                  <th>To</th>
                  <th>Status</th>
                  <th>By</th>
                  <th>
                    <span className="sr-only">Download</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {sends.map((s) => (
                  <tr key={s.id}>
                    <td>{formatRestaurantTime(s.created_at)}</td>
                    <td>{s.supplier}</td>
                    <td className="cell-wrap">
                      {(s.email_to || []).join(", ") || "-"}
                      {s.email_cc?.length > 0 && <span className="vc-send-cc"> · CC {s.email_cc.join(", ")}</span>}
                      {s.whatsapp_to ? ` · WhatsApp ${s.whatsapp_to}` : ""}
                    </td>
                    <td>
                      <SendStatusPill status={s.status} />
                      {(s.email_error || s.whatsapp_error) && s.status !== "sent" && (
                        <span className="pos-muted-small vc-send-error"> {s.email_error || s.whatsapp_error}</span>
                      )}
                    </td>
                    <td>{s.sent_by_name || "-"}</td>
                    <td>
                      {s.item_count > 0 && (
                        <PoDownloadMenu
                          target={{ kind: "send", id: s.id }}
                          supplier={s.supplier}
                          buttonClass="link-button po-dl-link"
                          fixed
                        />
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <p className="pos-muted-small">
          A sent PO is also on <Link to="/placed-orders">Placed Orders</Link>.
        </p>
      </section>
    </>
  );
}
