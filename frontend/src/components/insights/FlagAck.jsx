// Acknowledge a Loss watch / Odd checks flag (admins). The server's flag
// rows carry {id, acked, acked_at, acked_by}; POST /loss-watch/{id}/ack.
import { useCallback, useState } from "react";
import { ackLossWatch } from "../../api";
import { formatRestaurantTime } from "../../format.js";
import { Term } from "../../glossary.jsx";
import { storeTime } from "./insightsModel.js";

export function ackId(row) {
  return row?.id ?? null;
}

export function isAcked(row) {
  return !!(row?.acked || row?.acked_at);
}

// Acks made on this page, laid over the loaded rows until the next load.
export function useAcks() {
  const [acked, setAcked] = useState({});
  const done = useCallback((id, res) => {
    setAcked((a) => ({
      ...a,
      [id]: { acked_at: res?.acked_at || new Date().toISOString(), acked_by: res?.acked_by || null },
    }));
  }, []);
  const apply = useCallback(
    (r) => {
      const id = ackId(r);
      return id != null && acked[id] ? { ...r, acked: true, ...acked[id] } : r;
    },
    [acked],
  );
  // Acked on this page: kept in a "to look at" list until the next load, so
  // a row doesn't jump away the moment it's acknowledged.
  const recent = useCallback((r) => ackId(r) != null && !!acked[ackId(r)], [acked]);
  return { apply, done, recent };
}

export function AckButton({ row, onDone, demo }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState(null);
  const id = ackId(row);
  if (isAcked(row)) {
    const when = storeTime(row.acked_at, null, { withDate: true });
    return (
      <span className="ins-acked">
        <Term k="insights_loss_acknowledged">
          <span className="pill pill-success ins-ack-pill" title={formatRestaurantTime(row.acked_at) || undefined}>
            Acknowledged
          </span>
        </Term>
        {(row.acked_by || when) && (
          <span className="ins-acked-by">
            {row.acked_by ? `by ${row.acked_by}` : ""}
            {row.acked_by && when ? ", " : ""}
            {when || ""}
          </span>
        )}
      </span>
    );
  }
  if (id == null) return <span className="muted">-</span>;
  async function ack() {
    setBusy(true);
    setErr(null);
    try {
      const res = await ackLossWatch(id);
      onDone(id, res);
    } catch (e) {
      setErr(e?.status === 404 ? "This flag no longer exists - reload the page." : e.message);
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="ins-ack">
      <button
        type="button"
        className="btn-secondary ins-ack-btn"
        onClick={ack}
        disabled={busy || demo}
        title={demo ? "Not available on the demo login" : "Mark this check as looked at"}
      >
        {busy ? "Saving…" : "Acknowledge"}
      </button>
      {err && (
        <span className="error ins-inline-error" role="alert">
          {err}
        </span>
      )}
    </span>
  );
}
