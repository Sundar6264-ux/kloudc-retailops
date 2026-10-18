// Small inline-SVG charts for the Insights pages - no chart library (the site
// has none, and the contract asks not to add a heavy one).
//
// Every chart: a crosshair/column tooltip on hover AND keyboard focus (the
// chart itself is one tab stop; arrow keys move along it, Home/End jump),
// an aria-label summary, a legend for 2+ series (with dash patterns, so
// identity never rests on colour alone - also in print), and a "Show as
// table" view so every value is reachable without hovering.
import { useCallback, useEffect, useId, useLayoutEffect, useRef, useState } from "react";
import { heatInk, heatStep, HEAT_RAMP, isNum, labelEvery, niceTicks, showXLabel } from "./insightsModel.js";

export function useElementWidth() {
  const ref = useRef(null);
  const [width, setWidth] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return undefined;
    setWidth(el.clientWidth);
    if (typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver((entries) => {
      const w = Math.round(entries[0].contentRect.width);
      setWidth((prev) => (prev === w ? prev : w));
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, width];
}

// Keyboard + pointer cursor over `n` x positions.
function useCursor(n) {
  const [active, setActive] = useState(null);
  useEffect(() => {
    if (active != null && active >= n) setActive(n ? n - 1 : null);
  }, [n, active]);
  const onKeyDown = useCallback(
    (e) => {
      if (!n) return;
      let next = null;
      if (e.key === "ArrowRight" || e.key === "ArrowDown") next = active == null ? 0 : Math.min(n - 1, active + 1);
      else if (e.key === "ArrowLeft" || e.key === "ArrowUp") next = active == null ? n - 1 : Math.max(0, active - 1);
      else if (e.key === "Home") next = 0;
      else if (e.key === "End") next = n - 1;
      else if (e.key === "Escape") {
        setActive(null);
        return;
      }
      if (next != null) {
        e.preventDefault();
        setActive(next);
      }
    },
    [n, active],
  );
  return {
    active,
    setActive,
    keyProps: {
      tabIndex: 0,
      onKeyDown,
      onFocus: () => setActive((a) => (a == null && n ? n - 1 : a)),
      onBlur: () => setActive(null),
    },
  };
}

function Tooltip({ x, width, title, rows }) {
  const ref = useRef(null);
  const [left, setLeft] = useState(x);
  useLayoutEffect(() => {
    const w = ref.current?.offsetWidth || 160;
    let l = x - w / 2;
    l = Math.max(0, Math.min(width - w, l));
    setLeft(l);
  }, [x, width, title, rows]);
  return (
    <div className="ins-tooltip" ref={ref} style={{ left }} role="status" aria-live="polite">
      <div className="ins-tooltip-title">{title}</div>
      {rows.map((r) => (
        <div className="ins-tooltip-row" key={r.key}>
          {r.color && (
            <svg width="16" height="8" aria-hidden="true" className="ins-tooltip-key">
              <line x1="0" y1="4" x2="16" y2="4" stroke={r.color} strokeWidth="3" strokeDasharray={r.dash || undefined} />
            </svg>
          )}
          <strong className="ins-tooltip-value">{r.value}</strong>
          <span className="ins-tooltip-label">{r.label}</span>
        </div>
      ))}
    </div>
  );
}

export function Legend({ items, shape = "line" }) {
  if (!items || items.length < 2) return null;
  return (
    <ul className="ins-legend" aria-label="Legend">
      {items.map((it) => (
        <li key={it.key}>
          <svg width="20" height="10" aria-hidden="true">
            {shape === "rect" ? (
              <rect x="2" y="0" width="16" height="10" rx="2" fill={it.color} />
            ) : (
              <line x1="0" y1="5" x2="20" y2="5" stroke={it.color} strokeWidth="2.5" strokeDasharray={it.dash || undefined} />
            )}
          </svg>
          {it.label}
        </li>
      ))}
    </ul>
  );
}

// "Show as table" disclosure. Open in print so the numbers are on paper.
export function ChartTable({ caption, columns, rows, defaultOpen = false }) {
  return (
    <details className="ins-chart-table" open={defaultOpen || undefined}>
      <summary>Show as table</summary>
      <div className="table-wrap">
        <table>
          <caption className="ins-sr-only">{caption}</caption>
          <thead>
            <tr>
              {columns.map((c) => (
                <th key={c.key} scope="col" className={c.num ? "num" : undefined}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r, i) => (
              <tr key={r.key ?? i}>
                {columns.map((c, j) =>
                  j === 0 ? (
                    <th key={c.key} scope="row" className="ins-row-head">
                      {r[c.key]}
                    </th>
                  ) : (
                    <td key={c.key} className={c.num ? "num" : undefined}>
                      {r[c.key]}
                    </td>
                  ),
                )}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </details>
  );
}

const PAD = { l: 52, r: 14, t: 14, b: 30 };


// Lines over a shared x axis (labels[i]). series: [{key, label, color, dash,
// values: [number|null], width?}]. One y axis, starting at 0 by default.
export function LineChart({ labels, series, format, axisFormat, height = 220, ariaLabel, zeroBase = true }) {
  const [ref, measured] = useElementWidth();
  const n = labels.length;
  const { active, setActive, keyProps } = useCursor(n);
  const width = Math.max(measured || 0, 240);
  const innerW = width - PAD.l - PAD.r;
  const innerH = height - PAD.t - PAD.b;

  const all = series.flatMap((s) => s.values.filter(isNum));
  const lo = all.length ? Math.min(...all) : 0;
  const hi = all.length ? Math.max(...all) : 1;
  const ticks = niceTicks(zeroBase ? Math.min(0, lo) : lo, hi, height < 180 ? 3 : 4);
  const t0 = ticks[0];
  const t1 = ticks[ticks.length - 1] || 1;
  const x = (i) => PAD.l + (n <= 1 ? innerW / 2 : (i * innerW) / (n - 1));
  const y = (v) => PAD.t + innerH - ((v - t0) / (t1 - t0 || 1)) * innerH;
  const every = labelEvery(n, innerW, 52);
  const fmtAxis = axisFormat || format || ((v) => String(v));

  function pathFor(values) {
    let d = "";
    let pen = false;
    values.forEach((v, i) => {
      if (!isNum(v)) {
        pen = false;
        return;
      }
      d += `${pen ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`;
      pen = true;
    });
    return d;
  }

  function onPointer(e) {
    if (!n) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const px = e.clientX - rect.left;
    const i = n <= 1 ? 0 : Math.round(((px - PAD.l) / innerW) * (n - 1));
    setActive(Math.max(0, Math.min(n - 1, i)));
  }

  return (
    <div className="ins-chart" ref={ref}>
      <svg
        className="ins-chart-svg"
        width={width}
        height={height}
        role="img"
        aria-label={`${ariaLabel}. Use the left and right arrow keys to read each point.`}
        onPointerMove={onPointer}
        onPointerLeave={() => setActive(null)}
        {...keyProps}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className="ins-grid" x1={PAD.l} x2={width - PAD.r} y1={y(t)} y2={y(t)} />
            <text className="ins-axis" x={PAD.l - 8} y={y(t)} dy="0.32em" textAnchor="end">
              {fmtAxis(t)}
            </text>
          </g>
        ))}
        {labels.map((lab, i) =>
          showXLabel(i, n, every) ? (
            <text key={i} className="ins-axis" x={x(i)} y={height - 8} textAnchor={n > 1 && i === n - 1 ? "end" : n > 1 && i === 0 ? "start" : "middle"}>
              {lab}
            </text>
          ) : null,
        )}
        {active != null && <line className="ins-crosshair" x1={x(active)} x2={x(active)} y1={PAD.t} y2={PAD.t + innerH} />}
        {series.map((s) => (
          <path
            key={s.key}
            d={pathFor(s.values)}
            fill="none"
            stroke={s.color}
            strokeWidth={s.width || 2}
            strokeDasharray={s.dash || undefined}
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        {series.map((s) =>
          // Lone points (a value between two gaps) would be invisible as a path.
          s.values.map((v, i) =>
            isNum(v) && !isNum(s.values[i - 1]) && !isNum(s.values[i + 1]) ? (
              <circle key={`${s.key}-${i}`} cx={x(i)} cy={y(v)} r="3" fill={s.color} />
            ) : null,
          ),
        )}
        {active != null &&
          series.map((s) =>
            isNum(s.values[active]) ? (
              <circle key={s.key} cx={x(active)} cy={y(s.values[active])} r="4.5" fill={s.color} stroke="#fff" strokeWidth="2" />
            ) : null,
          )}
      </svg>
      {active != null && (
        <Tooltip
          x={x(active)}
          width={width}
          title={labels[active]}
          rows={series.map((s) => ({
            key: s.key,
            label: s.label,
            color: s.color,
            dash: s.dash,
            value: isNum(s.values[active]) ? format(s.values[active]) : "-",
          }))}
        />
      )}
    </div>
  );
}

// Stacked columns: rows [{label, values: {key: number}}], keys [{key, label,
// color}]. mode "share" stacks each column to 100%.
export function StackedBars({ rows, keys, format, axisFormat, height = 240, ariaLabel, mode = "value", shareFormat }) {
  const [ref, measured] = useElementWidth();
  const n = rows.length;
  const { active, setActive, keyProps } = useCursor(n);
  const width = Math.max(measured || 0, 240);
  const innerW = width - PAD.l - PAD.r;
  const innerH = height - PAD.t - PAD.b;
  const totals = rows.map((r) => keys.reduce((a, k) => a + (isNum(r.values[k.key]) ? Math.max(0, r.values[k.key]) : 0), 0));
  const share = mode === "share";
  const ticks = share ? [0, 0.25, 0.5, 0.75, 1] : niceTicks(0, Math.max(1, ...totals), 4);
  const top = ticks[ticks.length - 1] || 1;
  const slot = innerW / Math.max(1, n);
  const barW = Math.max(6, Math.min(56, slot * 0.62));
  const y = (v) => PAD.t + innerH - (v / top) * innerH;
  const every = labelEvery(n, innerW, 48);
  const fmtAxis = share ? (v) => `${Math.round(v * 100)}%` : axisFormat || format;

  function onPointer(e) {
    if (!n) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const i = Math.floor((e.clientX - rect.left - PAD.l) / slot);
    setActive(i < 0 || i >= n ? null : i);
  }

  return (
    <div className="ins-chart" ref={ref}>
      <svg
        className="ins-chart-svg"
        width={width}
        height={height}
        role="img"
        aria-label={`${ariaLabel}. Use the left and right arrow keys to read each column.`}
        onPointerMove={onPointer}
        onPointerLeave={() => setActive(null)}
        {...keyProps}
      >
        {ticks.map((t) => (
          <g key={t}>
            <line className="ins-grid" x1={PAD.l} x2={width - PAD.r} y1={y(t)} y2={y(t)} />
            <text className="ins-axis" x={PAD.l - 8} y={y(t)} dy="0.32em" textAnchor="end">
              {fmtAxis(t)}
            </text>
          </g>
        ))}
        {rows.map((r, i) => {
          const cx = PAD.l + slot * i + slot / 2;
          const total = totals[i];
          let acc = 0;
          const segs = keys
            .map((k) => {
              const raw = isNum(r.values[k.key]) ? Math.max(0, r.values[k.key]) : 0;
              const v = share ? (total > 0 ? raw / total : 0) : raw;
              const y0 = y(acc);
              acc += v;
              const y1 = y(acc);
              return { k, y0, y1 };
            })
            .filter((s) => s.y0 - s.y1 > 0.5);
          return (
            <g key={i} className={active === i ? "ins-bar ins-bar-active" : "ins-bar"}>
              {active === i && <rect className="ins-bar-hover" x={PAD.l + slot * i} y={PAD.t} width={slot} height={innerH} />}
              {segs.map((s, j) => {
                const isTop = j === segs.length - 1;
                const h = Math.max(0.5, s.y0 - s.y1 - (isTop ? 0 : 2));
                return (
                  <rect
                    key={s.k.key}
                    x={cx - barW / 2}
                    y={s.y1 + (isTop ? 0 : 2)}
                    width={barW}
                    height={h}
                    rx={isTop ? 3 : 0}
                    fill={s.k.color}
                  />
                );
              })}
              {showXLabel(i, n, every) && (
                <text className="ins-axis" x={cx} y={height - 8} textAnchor="middle">
                  {r.label}
                </text>
              )}
            </g>
          );
        })}
      </svg>
      {active != null && rows[active] && (
        <Tooltip
          x={PAD.l + slot * active + slot / 2}
          width={width}
          title={rows[active].tooltipTitle || rows[active].label}
          rows={[...keys].reverse().map((k) => {
            const raw = rows[active].values[k.key];
            const tot = totals[active];
            const sh = rows[active].shares?.[k.key] ?? (tot > 0 && isNum(raw) ? raw / tot : null);
            return {
              key: k.key,
              label: k.label,
              color: k.color,
              value: share
                ? `${shareFormat(sh)}${isNum(raw) && format ? ` · ${format(raw)}` : ""}`
                : `${isNum(raw) ? format(raw) : "-"}${isNum(sh) && shareFormat ? ` · ${shareFormat(sh)}` : ""}`,
            };
          })}
        />
      )}
    </div>
  );
}

// Day x hour colour grid. A real grid: one tab stop, arrow keys move between
// cells (roving tabindex), and the focused / hovered cell shows its numbers.
export function HeatGrid({ rows, hours, cellFor, valueOf, max, describe, rowLabel, colLabel, ariaLabel }) {
  const [focus, setFocus] = useState(null); // [r, c]
  const [hover, setHover] = useState(null);
  const gridRef = useRef(null);
  const tipId = useId();
  const shown = hover || focus;

  function move(r, c) {
    const rr = Math.max(0, Math.min(rows.length - 1, r));
    const cc = Math.max(0, Math.min(hours.length - 1, c));
    setFocus([rr, cc]);
    const el = gridRef.current?.querySelector(`[data-cell="${rr}-${cc}"]`);
    el?.focus();
  }

  function onKeyDown(e, r, c) {
    const map = {
      ArrowRight: [r, c + 1],
      ArrowLeft: [r, c - 1],
      ArrowDown: [r + 1, c],
      ArrowUp: [r - 1, c],
      Home: [r, 0],
      End: [r, hours.length - 1],
    };
    if (map[e.key]) {
      e.preventDefault();
      move(...map[e.key]);
    } else if (e.key === "Escape") {
      setFocus(null);
      e.currentTarget.blur();
    }
  }

  const tabCell = focus || [0, 0];
  const shownCell = shown ? cellFor(rows[shown[0]], hours[shown[1]]) : null;

  return (
    <div className="ins-heat">
      <div className="ins-heat-scroll">
        <table className={`ins-heat-grid${hours.length > 10 ? " is-dense" : ""}`} ref={gridRef} role="grid" aria-label={ariaLabel} aria-describedby={tipId}>
          <thead>
            <tr>
              <th scope="col" className="ins-heat-corner">
                <span className="ins-sr-only">Day</span>
              </th>
              {hours.map((h, c) => (
                <th key={h} scope="col" className={`ins-heat-hour${c % 2 ? " ins-heat-hour-alt" : ""}`}>
                  {colLabel(h)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, r) => (
              <tr key={row}>
                <th scope="row" className="ins-heat-day">
                  {rowLabel(row)}
                </th>
                {hours.map((h, c) => {
                  const cell = cellFor(row, h);
                  const v = cell ? valueOf(cell) : null;
                  const step = heatStep(v, max);
                  const isTab = tabCell[0] === r && tabCell[1] === c;
                  const on = shown && shown[0] === r && shown[1] === c;
                  return (
                    <td
                      key={h}
                      role="gridcell"
                      data-cell={`${r}-${c}`}
                      tabIndex={isTab ? 0 : -1}
                      className={`ins-heat-cell${on ? " is-on" : ""}`}
                      style={{ background: HEAT_RAMP[step], color: heatInk(step) }}
                      aria-label={describe(row, h, cell)}
                      onFocus={() => setFocus([r, c])}
                      onBlur={() => setFocus(null)}
                      onKeyDown={(e) => onKeyDown(e, r, c)}
                      onPointerEnter={() => setHover([r, c])}
                      onPointerLeave={() => setHover(null)}
                    />
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <p className="ins-heat-readout" id={tipId} aria-live="polite">
        {shown ? describe(rows[shown[0]], hours[shown[1]], shownCell) : "Point at or tab to a square to see its numbers. Arrow keys move between squares."}
      </p>
    </div>
  );
}

// Tiny hourly bars (prep forecast). Decorative next to its text summary.
export function MiniBars({ data, hours, barWidth = 4, height = 18 }) {
  if (!Array.isArray(data) || !data.length) return null;
  const byHour = new Map(data.map((d) => [d.hour, d.qty]));
  const list = hours || data.map((d) => d.hour);
  const max = Math.max(...list.map((h) => byHour.get(h) || 0), 0.0001);
  const w = barWidth;
  const gap = barWidth > 5 ? 2 : 1;
  return (
    <svg className="ins-minibars" width={list.length * (w + gap)} height={height} aria-hidden="true">
      {list.map((h, i) => {
        const v = byHour.get(h) || 0;
        const bh = v > 0 ? Math.max(1.5, (v / max) * height) : 0;
        return <rect key={h} x={i * (w + gap)} y={height - bh} width={w} height={bh} rx="1" fill="#2a78d6" />;
      })}
    </svg>
  );
}
