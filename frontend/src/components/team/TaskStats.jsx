import { useEffect, useState } from "react";
import { getTaskStats } from "../../api";
import { formatMinutes, pct } from "./teamAdvancedModel";

// Admins: who finishes their tasks, on time, over the last N days (W5).
// Counts tasks CREATED in the window; cancelled ones are left out.

const RANGES = [7, 14, 30, 90];

export default function TaskStats() {
  const [days, setDays] = useState(30);
  const [data, setData] = useState(null);
  const [error, setError] = useState(null);

  useEffect(() => {
    let live = true;
    setData(null);
    setError(null);
    getTaskStats(days)
      .then((d) => live && setData(d))
      .catch((err) => live && setError(err.message));
    return () => {
      live = false;
    };
  }, [days]);

  return (
    <div className="task-stats">
      <div className="issue-toolbar">
        <span className="muted">Tasks created in the last</span>
        <div className="range-pills" role="radiogroup" aria-label="Period">
          {RANGES.map((d) => (
            <button
              key={d}
              type="button"
              role="radio"
              aria-checked={days === d}
              className={`range-pill${days === d ? " active" : ""}`}
              onClick={() => setDays(d)}
            >
              {d} days
            </button>
          ))}
        </div>
      </div>
      {error && <p className="error">{error}</p>}
      {!data && !error && <p className="muted">Loading…</p>}
      {data && (
        <>
          <div className="stats-tiles">
            <div className="stats-tile">
              <span className="stats-tile-num">{data.totals.tasks}</span>
              <span className="muted">tasks</span>
            </div>
            <div className="stats-tile">
              <span className="stats-tile-num">{data.totals.done}</span>
              <span className="muted">done</span>
            </div>
            <div className="stats-tile">
              <span className="stats-tile-num">{data.totals.open}</span>
              <span className="muted">still open</span>
            </div>
            <div className={`stats-tile${data.totals.overdue ? " stats-tile-warn" : ""}`}>
              <span className="stats-tile-num">{data.totals.overdue}</span>
              <span className="muted">overdue</span>
            </div>
            <div className="stats-tile">
              <span className="stats-tile-num">{data.totals.cant_do}</span>
              <span className="muted">can't do</span>
            </div>
          </div>

          <h3 className="stats-head">By person</h3>
          {data.people.length === 0 ? (
            <p className="muted">No tasks in this period.</p>
          ) : (
            <div className="table-wrap">
              <table className="stats-table">
                <thead>
                  <tr>
                    <th>Person</th>
                    <th className="num" title="Tasks assigned to them">Assigned</th>
                    <th className="num" title="Of their own tasks, how many are done">Completed</th>
                    <th className="num" title="Everything they finished, their teams' tasks included">Finished</th>
                    <th className="num">On time</th>
                    <th className="num">Late</th>
                    <th className="num">Overdue now</th>
                    <th className="num">Can't do</th>
                    <th className="num" title="Median time from created to done">Typical time</th>
                  </tr>
                </thead>
                <tbody>
                  {data.people.map((p) => (
                    <tr key={p.user_id}>
                      <td className="cell-name">{p.name}</td>
                      <td className="num">{p.assigned}</td>
                      <td className="num">
                        {p.assigned_done} <span className="muted">({pct(p.completion_rate)})</span>
                      </td>
                      <td className="num">{p.done}</td>
                      <td className="num">
                        {p.on_time} <span className="muted">({pct(p.on_time_rate)})</span>
                      </td>
                      <td className={`num${p.late ? " text-negative" : ""}`}>{p.late}</td>
                      <td className={`num${p.overdue ? " text-negative" : ""}`}>{p.overdue}</td>
                      <td className="num">{p.cant_do}</td>
                      <td className="num">{formatMinutes(p.median_minutes_to_done)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          {data.teams.length > 0 && (
            <>
              <h3 className="stats-head">Team tasks</h3>
              <div className="table-wrap">
                <table className="stats-table">
                  <thead>
                    <tr>
                      <th>Team</th>
                      <th className="num">Tasks</th>
                      <th className="num">Done</th>
                      <th className="num">Open</th>
                      <th className="num">Overdue now</th>
                      <th className="num">Can't do</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.teams.map((t) => (
                      <tr key={t.team_id}>
                        <td className="cell-name">
                          <span className="task-team-dot" style={{ background: t.color || "#6b7280" }} aria-hidden="true" />
                          {t.name}
                        </td>
                        <td className="num">{t.total}</td>
                        <td className="num">{t.done}</td>
                        <td className="num">{t.open}</td>
                        <td className={`num${t.overdue ? " text-negative" : ""}`}>{t.overdue}</td>
                        <td className="num">{t.cant_do}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </>
          )}
          <p className="muted stats-foot">
            On time = finished by its due time (no due time counts as on time). A team task counts for whoever finished it.
          </p>
        </>
      )}
    </div>
  );
}
