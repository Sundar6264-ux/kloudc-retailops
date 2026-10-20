import { Suspense, useCallback, useEffect, useMemo, useState } from "react";
import { useAuth } from "../AuthContext.jsx";
import {
  approveSignup,
  archiveStore,
  enableTotp,
  getPlatformConfig,
  startTotpSetup,
  getAudit,
  getStoreSettings,
  hostingAction,
  listHostingRequests,
  listPacks,
  listPlatformStores,
  listSignups,
  putStoreSettings,
  rejectSignup,
  requestHosting,
  resumeStore,
  retryProvisioning,
  suspendStore,
} from "../api";
import { formatRestaurantTime } from "../format.js";
import {
  HOSTING_SEARCH_FIELDS,
  PLATFORM_TABS,
  SIGNUP_SEARCH_FIELDS,
  SIGNUP_STATUS_FILTERS,
  STORE_SEARCH_FIELDS,
  STORE_STATUS_FILTERS,
  approveBody,
  archiveConfirmed,
  auditDetails,
  auditQuery,
  groupSecret,
  hostingActions,
  hostingListOff,
  hostingStatusChip,
  isHostingDisabled,
  matchesSearch,
  packOptions,
  platformErrorMessage,
  readLastTab,
  rememberTab,
  signupStatusChip,
  storeActions,
  storeStatusChip,
  totpSetupNeeded,
} from "../platform/platformModel.js";
import { cleanTotp } from "../login/loginModel.js";
import MastersTab from "../platform/MastersTab.jsx";
import PasswordChangePanel from "../platform/PasswordChangePanel.jsx";
import PlatformSettingsTab from "../platform/PlatformSettingsTab.jsx";
import StoreDetail from "../platform/StoreDetail.jsx";
import "../platform/console.css";
import { timezoneOptions } from "../signup/signupModel.js";
import "../platform/platform.css";
import AreaChip from "../ui/AreaChip.jsx";
import Icon from "../ui/icons.jsx";
import KloudcLogo from "../ui/KloudcLogo.jsx";
import Menu, { MenuDivider, MenuItem, MenuLabel, MenuLink } from "../ui/Menu.jsx";
import { HelpCenter, InfoButton } from "../ui/helpBridge.jsx";
import { initials, KLOUDC, KLOUDC_LINKS, supportMailto } from "../ui/kloudc.js";
import { THEMES } from "../ui/theme.js";
import useTheme from "../ui/useTheme.js";

function storage() {
  try {
    return window.localStorage;
  } catch {
    return null;
  }
}

function when(iso) {
  return iso ? formatRestaurantTime(iso) : "";
}

const CHIP_ICON = { good: "circle-check", warn: "triangle-alert", bad: "octagon-alert", info: "info", neutral: "circle", off: "circle" };

// Status: colour + icon + word (DESIGN_SYSTEM 1.4).
function Chip({ chip }) {
  return (
    <span className={`pf-chip tone-${chip.tone}`}>
      <Icon name={CHIP_ICON[chip.tone] || "circle"} size={12} />
      {chip.label}
    </span>
  );
}

const asList = (r, key) => (Array.isArray(r) ? r : Array.isArray(r?.[key]) ? r[key] : Array.isArray(r?.items) ? r.items : []);

// The master console (docs/PLATFORM_CONTRACT.md 13.1, 14): one screen, four
// tile tabs (the last one is remembered), a slim frozen header with search,
// a status filter and the info icon.
export default function PlatformConsolePage() {
  const { user, logout } = useAuth();
  const [tab, setTab] = useState(() => readLastTab(storage()));
  const [search, setSearch] = useState("");
  const [filter, setFilter] = useState({ requests: "pending", stores: "", hosting: "", audit: "" });
  const [showInfo, setShowInfo] = useState(false);
  const [counts, setCounts] = useState({});
  const [config, setConfig] = useState(null);
  const [showTotp, setShowTotp] = useState(false);
  const [theme, setTheme] = useTheme();

  const loadConfig = useCallback(() => {
    let live = true;
    getPlatformConfig()
      .then((c) => {
        if (!live) return;
        setConfig(c);
        if (totpSetupNeeded(c)) setShowTotp(true);
      })
      .catch(() => {});
    return () => {
      live = false;
    };
  }, []);
  useEffect(() => loadConfig(), [loadConfig]);
  // A master added with a temporary password chooses their own first (A2).
  const mustChangePassword = !!(config?.user?.must_change_password ?? user?.must_change_password);

  function choose(key) {
    setTab(key);
    rememberTab(storage(), key);
  }

  const filterOptions =
    tab === "requests" ? SIGNUP_STATUS_FILTERS : tab === "stores" ? STORE_STATUS_FILTERS : tab === "hosting" ? ["", "requested", "approved", "migrating", "done", "rejected", "rolled_back"] : null;

  const current = PLATFORM_TABS.find((t) => t.key === tab) || PLATFORM_TABS[0];
  const listTab = tab !== "help" && tab !== "settings";

  return (
    <div className="pf-shell" data-area="platform">
      {/* KloudC band (DESIGN_SYSTEM 2.2): never a store's colours. */}
      <header className="pf-band">
        <div className="pf-band-inner">
          <KloudcLogo tone="light" height={30} />
          <span className="pf-band-pill">Platform</span>
          <h1 className="sr-only">Platform console</h1>
          <div className="pf-band-actions">
            <button
              type="button"
              className="pf-band-btn"
              data-testid="pf-totp-toggle"
              aria-expanded={showTotp}
              onClick={() => setShowTotp((v) => !v)}
            >
              <Icon name="shield" size={16} />
              <span>Two-step</span>
            </button>
            <Menu
              label="Account menu"
              buttonClass="pf-band-avatar"
              testId="user-menu"
              button={<span aria-hidden="true">{initials(user?.display_name || user?.username)}</span>}
            >
              {(close) => (
                <>
                  <div className="kc-menu-head">
                    <span className="topbar-avatar topbar-avatar-lg" aria-hidden="true">
                      {initials(user?.display_name || user?.username)}
                    </span>
                    <span>
                      <strong className="kc-menu-name">{user?.display_name || user?.username}</strong>
                      <span className="kc-menu-sub">KloudC master</span>
                    </span>
                  </div>
                  <MenuDivider />
                  <MenuItem
                    icon="shield"
                    onSelect={() => {
                      close();
                      setShowTotp(true);
                    }}
                  >
                    Two-step sign-in
                  </MenuItem>
                  <MenuItem
                    icon="life-buoy"
                    onSelect={() => {
                      close();
                      choose("help");
                    }}
                  >
                    Help
                  </MenuItem>
                  <MenuDivider />
                  <MenuLabel>Appearance</MenuLabel>
                  <div className="kc-menu-segment" role="group" aria-label="Appearance">
                    {THEMES.map((t) => (
                      <MenuItem
                        key={t.value}
                        icon={t.value === "system" ? "monitor" : t.value === "light" ? "sun" : "moon"}
                        checked={theme === t.value}
                        onSelect={() => setTheme(t.value)}
                      >
                        {t.label}
                      </MenuItem>
                    ))}
                  </div>
                  <MenuDivider />
                  <MenuLink icon="shield" href={KLOUDC_LINKS.privacy} external onSelect={close}>
                    Privacy policy
                  </MenuLink>
                  <MenuItem
                    icon="log-out"
                    danger
                    testId="sign-out"
                    onSelect={() => {
                      close();
                      logout();
                    }}
                  >
                    Sign out
                  </MenuItem>
                </>
              )}
            </Menu>
          </div>
        </div>
      </header>

      <div className="pf-body">
        {mustChangePassword ? (
          <PasswordChangePanel onChanged={loadConfig} />
        ) : (
        <>
        <div className="pf-head">
          {showTotp && (
            <TotpPanel
              enrolled={!!(config?.user?.totp_enrolled ?? user?.totp_enrolled)}
              required={!!config?.require_totp}
              onEnrolled={() => setConfig((c) => (c ? { ...c, user: { ...(c.user || {}), totp_enrolled: true } } : c))}
            />
          )}
          <div className="pf-title-row">
            <div>
              <AreaChip area="platform" label="KloudC RetailOps platform" />
              <div className="kc-page-title-row">
                <h2 className="pf-title">{current.label}</h2>
                <InfoButton pageId={current.pageId} />
              </div>
            </div>
          </div>
          <div className="pf-tabs" role="tablist" aria-label="Console sections">
            {PLATFORM_TABS.map((t) => (
              <button
                key={t.key}
                type="button"
                role="tab"
                aria-selected={tab === t.key}
                className={`pf-tab${tab === t.key ? " active" : ""}`}
                data-testid={`pf-tab-${t.key}`}
                onClick={() => choose(t.key)}
              >
                <span className="pf-tab-icon" aria-hidden="true">
                  <Icon name={t.icon} size={18} />
                </span>
                <span className="pf-tab-text">
                  <span className="pf-tab-label">{t.label}</span>
                  <span className="pf-tab-sub">{t.sub}</span>
                </span>
                {counts[t.key] ? <span className="pf-count">{counts[t.key]}</span> : null}
              </button>
            ))}
          </div>
          {listTab && (
            <div className="pf-head-row pf-list-header">
              <label className="kc-search-field pf-search">
                <Icon name="search" size={16} />
                <input
                  type="search"
                  placeholder={tab === "audit" ? "Store ID" : "Search"}
                  value={tab === "audit" ? filter.auditStore || "" : search}
                  onChange={(e) =>
                    tab === "audit" ? setFilter((f) => ({ ...f, auditStore: e.target.value })) : setSearch(e.target.value)
                  }
                  aria-label="Search"
                />
              </label>
              {filterOptions ? (
                <select
                  aria-label="Filter by status"
                  value={filter[tab]}
                  onChange={(e) => setFilter((f) => ({ ...f, [tab]: e.target.value }))}
                >
                  {filterOptions.map((s) => (
                    <option key={s || "all"} value={s}>
                      {s ? s.replace(/_/g, " ") : "All"}
                    </option>
                  ))}
                </select>
              ) : tab === "audit" ? (
                <input
                  type="text"
                  aria-label="Action"
                  placeholder="Action, e.g. login.failed"
                  style={{ maxWidth: "11rem" }}
                  value={filter.audit}
                  onChange={(e) => setFilter((f) => ({ ...f, audit: e.target.value }))}
                />
              ) : null}
              <button
                type="button"
                className="kc-icon-btn pf-icon"
                aria-label="About this screen"
                aria-expanded={showInfo}
                onClick={() => setShowInfo((v) => !v)}
              >
                <Icon name="info" size={18} />
              </button>
            </div>
          )}
        {showInfo && listTab && (
          <div className="pf-info">
            <p>
              <strong>Requests</strong>: new stores asking to join. Approve sets the store up automatically (database,
              store pack, the owner's admin login); the owner then signs in with the Store ID.
            </p>
            <p>
              <strong>Stores</strong>: every store. Suspend stops sign-in and the store's jobs; Resume undoes it. A
              failed setup can be retried.
            </p>
            <p>
              <strong>Hosting</strong>: requests to move one store to its own server. Nothing moves without your
              approval, and only when dedicated hosting is switched on.
            </p>
            <p>
              <strong>Audit</strong>: everything done on the platform, newest first.
            </p>
            <p>
              <strong>Masters</strong>: everyone who can sign in here. A new master gets a temporary password and
              chooses their own at first sign-in.
            </p>
            <p>
              Open a store's <strong>Details</strong> for its health, usage, settings, data and audit in one place.
            </p>
          </div>
        )}
      </div>

      {tab === "requests" && <RequestsTab status={filter.requests} search={search} onCount={(n) => setCounts((c) => ({ ...c, requests: n }))} />}
      {tab === "stores" && <StoresTab status={filter.stores} search={search} />}
      {tab === "hosting" && <HostingTab status={filter.hosting} search={search} />}
      {tab === "audit" && <AuditTab store={filter.auditStore || ""} action={filter.audit} />}
      {tab === "masters" && <MastersTab search={search} />}
      {tab === "settings" && <PlatformSettingsTab />}
      {tab === "help" && (
        <Suspense fallback={<p className="muted">Loading…</p>}>
          <MasterHelp />
        </Suspense>
      )}
        </>
        )}
      </div>
    </div>
  );
}

// Help tab: the Help Center (track H) when this build has it, else the
// master's start-here steps and how to reach KloudC support.
const MASTER_STEPS = [
  ["Sign in", "Store ID PLATFORM, your username and password, then the 6-digit code from your authenticator app."],
  ["Review requests", "Requests lists new stores. Approve with a starter pack, or reject with a note."],
  ["Watch the set-up", "An approved store is provisioned automatically. A failed set-up can be retried from Stores."],
  ["Look after stores", "Stores shows every store's status. Suspend stops sign-in and jobs; Resume undoes it; Archive is final."],
  ["Hosting", "A store can ask for its own server. Nothing moves until you approve, and a move can be rolled back."],
  ["Audit", "Everything done on the platform, newest first. Filter by Store ID or action."],
  ["Helping a store", "You can see a store's set-up and status, never its passwords. Ask the owner for screenshots when needed."],
];

function MasterHelp() {
  if (HelpCenter) return <HelpCenter audience="master" />;
  return (
    <div className="pf-help">
      <section className="card">
        <h2>Start here</h2>
        <ol className="pf-steps">
          {MASTER_STEPS.map(([title, text]) => (
            <li key={title}>
              <strong>{title}</strong>
              <span className="muted">{text}</span>
            </li>
          ))}
        </ol>
      </section>
      <section className="card">
        <h2>Contact</h2>
        <div className="kc-drawer-links">
          <a className="kc-hub-tile" data-area="help" href={supportMailto({ storeCode: "PLATFORM", page: "Platform console" })}>
            <span className="kc-hub-icon">
              <Icon name="mail" size={18} />
            </span>
            <span>
              <strong>{KLOUDC.email}</strong>
              <span className="muted">Opens your mail app. Never send a password.</span>
            </span>
          </a>
          <a className="kc-hub-tile" data-area="help" href={KLOUDC_LINKS.support} target="_blank" rel="noopener noreferrer">
            <span className="kc-hub-icon">
              <Icon name="external-link" size={18} />
            </span>
            <span>
              <strong>KloudC support site</strong>
              <span className="muted">kloudc.com/kloudc-support</span>
            </span>
          </a>
        </div>
      </section>
    </div>
  );
}

function useList(loader, deps) {
  const [rows, setRows] = useState(null);
  const [error, setError] = useState(null);
  const [errorObj, setErrorObj] = useState(null);
  const load = useCallback(async () => {
    try {
      setRows(await loader());
      setError(null);
      setErrorObj(null);
    } catch (err) {
      setRows([]);
      setError(platformErrorMessage(err));
      setErrorObj(err);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => {
    load();
  }, [load]);
  return { rows, error, errorObj, reload: load };
}

function Feedback({ message }) {
  if (!message) return null;
  return (
    <div className={`pf-banner ${message.tone || ""}`} role="status" style={{ marginBottom: "0.6rem" }}>
      {message.text}
    </div>
  );
}

// --- Requests ---------------------------------------------------------------

function RequestsTab({ status, search, onCount }) {
  const { rows, error, reload } = useList(async () => asList(await listSignups(status), "signups"), [status]);
  const [packs, setPacks] = useState([]);
  const [message, setMessage] = useState(null);
  const zones = useMemo(() => timezoneOptions(), []);

  useEffect(() => {
    listPacks()
      .then((r) => setPacks(packOptions(r)))
      .catch(() => setPacks([]));
  }, []);

  useEffect(() => {
    if (status === "pending" && rows) onCount(rows.length);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [rows, status]);

  const shown = (rows || []).filter((r) => matchesSearch(r, search, SIGNUP_SEARCH_FIELDS));

  return (
    <div>
      <Feedback message={message} />
      {error && <Feedback message={{ tone: "bad", text: error }} />}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted pf-empty">{status === "pending" ? "No requests waiting." : "Nothing here."}</p>
      ) : (
        <div className="pf-list">
          {shown.map((r) => (
            <SignupRow
              key={r.id}
              req={r}
              packs={packs}
              zones={zones}
              onDone={async (text, tone = "ok") => {
                setMessage({ text, tone });
                await reload();
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

function SignupRow({ req, packs, zones, onDone }) {
  const [mode, setMode] = useState(null); // "approve" | "reject"
  const [form, setForm] = useState({ code: req.requested_code || "", packId: "", timezone: req.timezone || "", note: "" });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pf-row" data-testid={`signup-${req.id}`}>
      <div className="pf-row-top">
        <span className="pf-avatar pf-avatar-request" aria-hidden="true">
          {initials(req.store_name)}
        </span>
        <span className="pf-row-title">{req.store_name}</span>
        <span className="pf-code">{req.requested_code}</span>
        <Chip chip={signupStatusChip(req.status)} />
      </div>
      <div className="pf-row-meta">
        {[req.contact_name, req.contact_email, req.contact_phone, (req.pos_provider || "").toUpperCase(), req.timezone, when(req.created_at)]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {req.address && <div className="pf-row-meta">{req.address}</div>}
      {req.notes && <div className="pf-row-meta">“{req.notes}”</div>}
      {req.review_note && <div className="pf-row-meta">Note: {req.review_note}</div>}
      {req.status === "pending" && !mode && (
        <div className="pf-actions">
          <button type="button" onClick={() => setMode("approve")} data-testid={`approve-${req.id}`}>
            Approve
          </button>
          <button type="button" className="btn-secondary" onClick={() => setMode("reject")}>
            Reject
          </button>
        </div>
      )}
      {mode === "approve" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              const r = await approveSignup(req.id, approveBody(form, req));
              await onDone(`Approved - setting up ${r?.store_code || req.requested_code}.`);
            });
          }}
        >
          <label>
            Store ID
            <input value={form.code} onChange={(e) => setForm((f) => ({ ...f, code: e.target.value }))} />
          </label>
          <label>
            Store pack
            <select value={form.packId} onChange={(e) => setForm((f) => ({ ...f, packId: e.target.value }))}>
              <option value="">Default for {(req.pos_provider || "the POS").toUpperCase()}</option>
              {packs.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.label}
                </option>
              ))}
            </select>
          </label>
          <label>
            Time zone
            <select value={form.timezone} onChange={(e) => setForm((f) => ({ ...f, timezone: e.target.value }))}>
              {[...new Set([req.timezone, ...zones].filter(Boolean))].map((z) => (
                <option key={z} value={z}>
                  {z}
                </option>
              ))}
            </select>
          </label>
          <label>
            Note (optional)
            <input value={form.note} onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))} />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy} data-testid={`approve-confirm-${req.id}`}>
              {busy ? "Approving…" : "Approve and set up"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {mode === "reject" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              await rejectSignup(req.id, form.note.trim());
              await onDone(`Rejected ${req.store_name}.`, "");
            });
          }}
        >
          <label style={{ gridColumn: "1 / -1" }}>
            Note for the requester
            <input
              value={form.note}
              onChange={(e) => setForm((f) => ({ ...f, note: e.target.value }))}
              placeholder="Shown on their status page"
            />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy || !form.note.trim()}>
              {busy ? "Rejecting…" : "Reject"}
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)} disabled={busy}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {error && <p className="error" style={{ margin: 0 }}>{error}</p>}
    </div>
  );
}

// --- Stores -----------------------------------------------------------------

function StoresTab({ status, search }) {
  const { rows, error, reload } = useList(async () => asList(await listPlatformStores(status), "stores"), [status]);
  const [message, setMessage] = useState(null);
  const [detail, setDetail] = useState(null);
  const closeDetail = useCallback(() => setDetail(null), []);
  const shown = (rows || []).filter((r) => matchesSearch(r, search, STORE_SEARCH_FIELDS));
  return (
    <div>
      <Feedback message={message} />
      {error && <Feedback message={{ tone: "bad", text: error }} />}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted pf-empty">No stores{status ? ` ${status.replace(/_/g, " ")}` : ""}.</p>
      ) : (
        <div className="pf-list">
          {shown.map((s) => (
            <StoreRow
              key={s.code}
              store={s}
              onOpen={() => setDetail(s)}
              onDone={async (text, tone = "ok") => {
                setMessage({ text, tone });
                await reload();
              }}
            />
          ))}
        </div>
      )}
      {detail && (
        <StoreDetail
          store={detail}
          onClose={closeDetail}
          onChanged={reload}
          renderSettings={(st) => (
            <StoreSettingsForm
              code={st.code}
              onSaved={async () => {
                setMessage({ text: `Settings of ${st.code} saved.`, tone: "ok" });
                await reload();
              }}
              onCancel={closeDetail}
            />
          )}
          renderAudit={(st) => <AuditTab store={st.code} action="" />}
        />
      )}
    </div>
  );
}

function StoreRow({ store, onDone, onOpen }) {
  const [mode, setMode] = useState(null); // suspend | archive | settings | hosting
  const [text, setText] = useState("");
  const [extra, setExtra] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const actions = storeActions(store);

  async function act(fn) {
    setBusy(true);
    setError(null);
    try {
      await fn();
      setMode(null);
      setText("");
      setExtra("");
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function open(m) {
    setError(null);
    setText("");
    setExtra("");
    setMode((cur) => (cur === m ? null : m));
  }

  return (
    <div className="pf-row" data-testid={`store-${store.code}`}>
      <div className="pf-row-top">
        <span className="pf-avatar" aria-hidden="true">
          {initials(store.name)}
        </span>
        <span className="pf-row-title">{store.name}</span>
        <span className="pf-code">{store.code}</span>
        <Chip chip={storeStatusChip(store.status)} />
        {store.hosting === "dedicated" && <span className="pf-chip tone-info">Own server</span>}
        {onOpen && (
          <button type="button" className="btn-secondary pf-row-open" onClick={onOpen} data-testid={`store-details-${store.code}`} style={{ marginLeft: "auto" }}>
            Details
          </button>
        )}
      </div>
      <div className="pf-row-meta">
        {[
          (store.pos_provider || store.pos || "").toUpperCase(),
          store.timezone,
          store.approved_at ? `approved ${when(store.approved_at)}` : store.created_at ? `created ${when(store.created_at)}` : "",
          store.schema_revision ? `schema ${store.schema_revision}` : "",
        ]
          .filter(Boolean)
          .join(" · ")}
      </div>
      {store.status_reason && <div className="pf-row-meta">{store.status_reason}</div>}
      {actions.length > 0 && (
        <div className="pf-actions">
          {actions.includes("suspend") && (
            <button type="button" className="btn-secondary" onClick={() => open("suspend")}>
              Suspend
            </button>
          )}
          {actions.includes("resume") && (
            <button
              type="button"
              disabled={busy}
              onClick={() => act(async () => (await resumeStore(store.code), onDone(`${store.code} is active again.`)))}
            >
              Resume
            </button>
          )}
          {actions.includes("retry") && (
            <button
              type="button"
              disabled={busy}
              onClick={() => act(async () => (await retryProvisioning(store.code), onDone(`Retrying the set-up of ${store.code}.`)))}
            >
              Retry set-up
            </button>
          )}
          {actions.includes("settings") && (
            <button type="button" className="btn-secondary" onClick={() => open("settings")}>
              Settings
            </button>
          )}
          {actions.includes("hosting") && (
            <button type="button" className="btn-secondary" onClick={() => open("hosting")}>
              Own server…
            </button>
          )}
          {actions.includes("archive") && (
            <button type="button" className="link-button link-danger" onClick={() => open("archive")}>
              Archive
            </button>
          )}
        </div>
      )}
      {mode === "suspend" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => (await suspendStore(store.code, text.trim()), onDone(`${store.code} suspended.`, "")));
          }}
        >
          <label style={{ gridColumn: "1 / -1" }}>
            Reason (shown to the store's staff)
            <input value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy || !text.trim()}>
              Suspend
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {mode === "archive" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => (await archiveStore(store.code, store.code), onDone(`${store.code} archived.`, "")));
          }}
        >
          <label style={{ gridColumn: "1 / -1" }}>
            Type {store.code} to archive (sign-in stops; the data is kept until purged)
            <input value={text} onChange={(e) => setText(e.target.value)} autoComplete="off" />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy || !archiveConfirmed(store, text)}>
              Archive
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {mode === "hosting" && (
        <form
          className="pf-inline-form"
          onSubmit={(e) => {
            e.preventDefault();
            act(async () => {
              await requestHosting(store.code, {
                reason: text.trim(),
                ...(extra.trim() ? { target_api_base_url: extra.trim() } : {}),
              });
              await onDone(`Own-server request made for ${store.code} - approve it on Hosting.`);
            });
          }}
        >
          <label>
            Why
            <input value={text} onChange={(e) => setText(e.target.value)} />
          </label>
          <label>
            New server address (optional)
            <input value={extra} onChange={(e) => setExtra(e.target.value)} placeholder="https://xyz.example.com" />
          </label>
          <div className="pf-actions">
            <button type="submit" disabled={busy || !text.trim()}>
              Request
            </button>
            <button type="button" className="btn-secondary" onClick={() => setMode(null)}>
              Cancel
            </button>
          </div>
        </form>
      )}
      {mode === "settings" && <StoreSettingsForm code={store.code} onSaved={(t) => onDone(t)} onCancel={() => setMode(null)} />}
      {error && <p className="error" style={{ margin: 0 }}>{error}</p>}
    </div>
  );
}

// The store's whitelisted settings (contract 8.2) as key/value rows; only
// changed values are sent. Values are JSON scalars on the server.
function StoreSettingsForm({ code, onSaved, onCancel }) {
  const [settings, setSettings] = useState(null);
  const [edits, setEdits] = useState({});
  const [error, setError] = useState(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    getStoreSettings(code)
      .then((r) => setSettings(r?.settings && typeof r.settings === "object" ? r.settings : r || {}))
      .catch((err) => {
        setSettings({});
        setError(platformErrorMessage(err));
      });
  }, [code]);

  if (settings === null) return <p className="muted">Loading settings…</p>;
  const keys = Object.keys(settings).sort();

  function parse(v) {
    const s = String(v).trim();
    if (s === "true") return true;
    if (s === "false") return false;
    if (s !== "" && !Number.isNaN(Number(s)) && /^-?\d+(\.\d+)?$/.test(s)) return Number(s);
    return v;
  }

  async function save(e) {
    e.preventDefault();
    const changed = Object.fromEntries(Object.entries(edits).map(([k, v]) => [k, parse(v)]));
    if (!Object.keys(changed).length) return onCancel();
    setBusy(true);
    setError(null);
    try {
      await putStoreSettings(code, changed);
      onSaved(`Saved ${Object.keys(changed).length} setting(s) for ${code}.`);
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <form className="pf-inline-form" onSubmit={save}>
      {keys.length === 0 && <p className="muted">No settings saved for this store yet.</p>}
      {keys.map((k) => (
        <label key={k}>
          {k}
          <input
            value={k in edits ? edits[k] : settings[k] == null ? "" : String(settings[k])}
            onChange={(e) => setEdits((s) => ({ ...s, [k]: e.target.value }))}
          />
        </label>
      ))}
      <div className="pf-actions" style={{ gridColumn: "1 / -1" }}>
        <button type="submit" disabled={busy}>
          Save
        </button>
        <button type="button" className="btn-secondary" onClick={onCancel}>
          Close
        </button>
      </div>
      {error && <p className="error" style={{ margin: 0, gridColumn: "1 / -1" }}>{error}</p>}
    </form>
  );
}

// --- Hosting ----------------------------------------------------------------

function HostingTab({ status, search }) {
  // The real server answers {"enabled", "requests"} (200) and 409
  // dedicated_hosting_disabled on the write routes; either says "off".
  const { rows: reply, error, errorObj, reload } = useList(() => listHostingRequests(status), [status]);
  const rows = reply === null ? null : asList(reply, "requests");
  const [message, setMessage] = useState(null);
  const disabled = isHostingDisabled(errorObj) || hostingListOff(reply);
  const shown = (rows || []).filter((r) => matchesSearch(r, search, HOSTING_SEARCH_FIELDS));

  if (disabled) {
    return (
      <div className="pf-row" data-testid="hosting-disabled">
        <span className="pf-row-title">Dedicated servers are switched off</span>
        <span className="pf-row-meta">
          Every store runs on the shared service. To let one store move to its own server, the platform's
          DEDICATED_HOSTING_ENABLED setting must be turned on first (see the dedicated hosting runbook). Moves still
          need your approval here.
        </span>
      </div>
    );
  }

  return (
    <div>
      <Feedback message={message} />
      {error && <Feedback message={{ tone: "bad", text: error }} />}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : shown.length === 0 ? (
        <p className="muted pf-empty">No own-server requests.</p>
      ) : (
        <div className="pf-list">
          {shown.map((r) => (
            <HostingRow
              key={r.id}
              req={r}
              onDone={async (text, tone = "ok") => {
                setMessage({ text, tone });
                await reload();
              }}
            />
          ))}
        </div>
      )}
    </div>
  );
}

const HOSTING_LABELS = { approve: "Approve", reject: "Reject", complete: "Mark moved", rollback: "Roll back" };

function HostingRow({ req, onDone }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const actions = hostingActions(req);
  const code = req.store_code || req.code || "";

  async function act(action) {
    let body = {};
    if (action === "reject" || action === "rollback") {
      const note = window.prompt(action === "reject" ? "Why not? (shown to the store)" : "Why roll back?");
      if (note === null) return;
      body = { note };
    } else if (action === "complete" && !window.confirm(`Mark ${code} as moved? Its staff are sent to the new server from now on.`)) {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await hostingAction(req.id, action, body);
      await onDone(`${HOSTING_LABELS[action]}: ${code}.`);
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pf-row">
      <div className="pf-row-top">
        <span className="pf-row-title">{code}</span>
        <Chip chip={hostingStatusChip(req.status)} />
      </div>
      <div className="pf-row-meta">
        {[req.reason, req.target_api_base_url, req.requested_by, when(req.created_at)].filter(Boolean).join(" · ")}
      </div>
      {req.review_note && <div className="pf-row-meta">Note: {req.review_note}</div>}
      {actions.length > 0 && (
        <div className="pf-actions">
          {actions.map((a) => (
            <button
              key={a}
              type="button"
              className={a === "approve" || a === "complete" ? undefined : "btn-secondary"}
              disabled={busy}
              onClick={() => act(a)}
            >
              {HOSTING_LABELS[a]}
            </button>
          ))}
        </div>
      )}
      {error && <p className="error" style={{ margin: 0 }}>{error}</p>}
    </div>
  );
}

// --- Audit ------------------------------------------------------------------

function AuditTab({ store, action }) {
  const query = auditQuery({ store, action });
  const { rows, error } = useList(async () => asList(await getAudit(query), "entries"), [query]);
  return (
    <div>
      {error && <Feedback message={{ tone: "bad", text: error }} />}
      {rows === null ? (
        <p className="muted">Loading…</p>
      ) : rows.length === 0 ? (
        <p className="muted pf-empty">Nothing recorded{store || action ? " for this filter" : ""}.</p>
      ) : (
        <div className="pf-list">
          {rows.map((r) => (
            <div className="pf-row" key={r.id}>
              <div className="pf-row-top">
                <span className="pf-code" style={{ color: "var(--text)" }}>
                  {r.action}
                </span>
                {r.store_code && <span className="pf-chip">{r.store_code}</span>}
                <span className="pf-row-meta" style={{ marginLeft: "auto" }}>
                  {when(r.at)}
                </span>
              </div>
              <div className="pf-row-meta">
                {[r.actor_name || r.actor_type, r.target, auditDetails(r.details), r.client_ip].filter(Boolean).join(" · ")}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

// Two-step sign-in for this master: a new secret (shown once) to add to an
// authenticator app, then the app's 6-digit code to switch it on. From then
// on the login page asks for the code after the password.
function TotpPanel({ enrolled, required, onEnrolled }) {
  const [setup, setSetup] = useState(null);
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);
  const [done, setDone] = useState(false);

  async function begin() {
    setBusy(true);
    setError(null);
    try {
      setSetup(await startTotpSetup());
    } catch (err) {
      setError(platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function enable(e) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await enableTotp(setup.pending, code);
      setDone(true);
      setSetup(null);
      onEnrolled();
    } catch (err) {
      setError(err?.status === 400 || err?.status === 401 ? "That code didn't match. Try the current one." : platformErrorMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="pf-info" data-testid="pf-totp">
      {done || (enrolled && !setup) ? (
        <p>
          <strong>Two-step sign-in is on.</strong> The login page asks for your authenticator app's code after the
          password.{enrolled && !done ? " Set it up again to move it to a new phone." : ""}
        </p>
      ) : (
        <p>
          <strong>Two-step sign-in</strong>
          {required ? " is required on this platform." : " adds a code from an authenticator app to your sign-in."}
        </p>
      )}
      {!setup ? (
        <button type="button" className="btn-secondary" style={{ width: "auto" }} disabled={busy} onClick={begin}>
          {enrolled || done ? "Set up again" : "Set up two-step sign-in"}
        </button>
      ) : (
        <form onSubmit={enable}>
          {setup.qr_svg && (
            <div style={{ margin: "0.25rem 0 0.75rem" }}>
              <p style={{ marginBottom: "0.4rem" }}>
                Scan this with your authenticator app (Google Authenticator, Microsoft Authenticator, Authy or the
                iPhone Passwords app):
              </p>
              <img
                src={setup.qr_svg}
                alt="QR code for your authenticator app"
                data-testid="pf-totp-qr"
                width={200}
                height={200}
                style={{ background: "white", padding: "6px", borderRadius: "8px", display: "block" }}
              />
            </div>
          )}
          <p>
            {setup.qr_svg ? "Can't scan? Add this key instead (shown once): " : "Add this key to your authenticator app (shown once): "}
            <code data-testid="pf-totp-secret" style={{ wordBreak: "break-all" }}>
              {groupSecret(setup.secret)}
            </code>
          </p>
          {setup.otpauth_uri && (
            <p className="muted" style={{ fontSize: "0.78rem", wordBreak: "break-all" }}>
              Or open: <a href={setup.otpauth_uri}>{setup.otpauth_uri}</a>
            </p>
          )}
          <label className="login-field" style={{ maxWidth: "12rem" }}>
            6-digit code
            <input
              type="text"
              inputMode="numeric"
              autoComplete="one-time-code"
              data-testid="pf-totp-code"
              value={code}
              onChange={(e) => setCode(cleanTotp(e.target.value))}
            />
          </label>
          <button type="submit" style={{ width: "auto" }} disabled={busy || code.length !== 6}>
            Switch on
          </button>
        </form>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}
