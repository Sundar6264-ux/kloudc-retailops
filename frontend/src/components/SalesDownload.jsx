import { useState } from "react";
import { storeTodayStr } from "../format.js";
import { downloadSales } from "../api";
import { useAuth } from "../AuthContext.jsx";

function todayStr() {
  return storeTodayStr();
}

// Not shown at all to a "Hide sales numbers" user (the export 403s for them).
export default function SalesDownload() {
  const { canSeeSales } = useAuth();
  if (!canSeeSales) return null;
  return <SalesDownloadCard />;
}

function SalesDownloadCard() {
  const [start, setStart] = useState(todayStr());
  const [end, setEnd] = useState(todayStr());
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(null);

  async function handleDownload(format) {
    if (!start || !end) return;
    setBusy(true);
    setError(null);
    try {
      await downloadSales(start, end, format);
    } catch (err) {
      setError(err.message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Download sales data</h2>
      <div className="date-range">
        <label>
          Start
          <input type="date" value={start} onChange={(e) => setStart(e.target.value)} />
        </label>
        <label>
          End
          <input type="date" value={end} onChange={(e) => setEnd(e.target.value)} />
        </label>
      </div>
      <div className="download-buttons">
        <button disabled={busy} onClick={() => handleDownload("csv")}>
          Download CSV
        </button>
        <button className="btn-secondary" disabled={busy} onClick={() => handleDownload("pdf")}>
          Download PDF
        </button>
      </div>
      {error && <p className="error">{error}</p>}
    </section>
  );
}
