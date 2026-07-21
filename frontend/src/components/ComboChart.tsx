import { useState } from 'react';

export interface ComboPoint {
  date: string;
  bar: number;
  /** Right-axis series; null = gap (undefined that day), not zero */
  line: number | null;
}

interface ComboChartProps {
  title: string;
  points: ComboPoint[];
  barLabel: string;
  lineLabel: string;
  barColor: string;
  lineColor: string;
  /** Fixed right-axis max (e.g. 100 for percentages) or 'auto' */
  lineMax?: number | 'auto';
  /** Suffix for right-axis / tooltip values, e.g. '%' */
  lineSuffix?: string;
}

const W = 560;
const H = 200;
const PAD = { top: 10, right: 38, bottom: 34, left: 34 };

const niceMax = (max: number): number => {
  if (max <= 4) return 4;
  const pow = Math.pow(10, Math.floor(Math.log10(max)));
  for (const m of [1, 2, 2.5, 5, 10]) {
    if (m * pow >= max) return m * pow;
  }
  return 10 * pow;
};

const fmtDate = (iso: string): string => {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
};

/**
 * Daily bars (left axis, counts) with a rate line over them (right axis).
 * The two series are different unit types — the line is a rate derived
 * from activity, so it gets its own scale; encoding (bar vs line), the
 * legend, and per-axis labels keep the scales from being conflated.
 */
function ComboChart({
  title, points, barLabel, lineLabel, barColor, lineColor,
  lineMax = 'auto', lineSuffix = '',
}: ComboChartProps) {
  const [hover, setHover] = useState<number | null>(null);

  const plotW = W - PAD.left - PAD.right;
  const plotH = H - PAD.top - PAD.bottom;
  const n = points.length;
  const slot = plotW / n;
  const barW = Math.max(2, Math.min(14, slot - 2)); // ≥2px gap between bars

  const yBarMax = niceMax(Math.max(...points.map(p => p.bar), 1));
  const lineVals = points.map(p => p.line).filter((v): v is number => v !== null);
  const yLineMax = lineMax === 'auto' ? niceMax(Math.max(...lineVals, 1)) : lineMax;

  const yBar = (v: number) => PAD.top + plotH * (1 - v / yBarMax);
  const yLine = (v: number) => PAD.top + plotH * (1 - v / yLineMax);
  const cx = (i: number) => PAD.left + slot * i + slot / 2;

  // Line segments, broken at null gaps
  const segments: string[] = [];
  let current: string[] = [];
  points.forEach((p, i) => {
    if (p.line === null) {
      if (current.length > 1) segments.push(current.join(' '));
      current = [];
    } else {
      current.push(`${cx(i)},${yLine(p.line)}`);
    }
  });
  if (current.length > 1) segments.push(current.join(' '));

  const isEmpty = points.every(p => p.bar === 0) && lineVals.length === 0;
  const labelIdx = new Set<number>();
  for (let i = n - 1; i >= 0; i -= 7) labelIdx.add(i);

  const fmtLine = (v: number) =>
    `${Number.isInteger(v) ? v : v.toFixed(1)}${lineSuffix}`;

  return (
    <div className="chart-card">
      <h4 className="chart-title">{title}</h4>
      <div className="chart-body">
        <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label={title}>
          {/* Recessive grid from the bar (left) scale only */}
          {[yBarMax / 2, yBarMax].map(v => (
            <g key={v}>
              <line
                x1={PAD.left} x2={W - PAD.right} y1={yBar(v)} y2={yBar(v)}
                stroke="var(--border)" strokeWidth="1" strokeDasharray="3 4" opacity="0.5"
              />
              <text x={PAD.left - 6} y={yBar(v) + 3} textAnchor="end" className="chart-axis-text">
                {v}
              </text>
            </g>
          ))}
          {/* Right-axis ticks for the line scale */}
          {[yLineMax / 2, yLineMax].map(v => (
            <text
              key={v} x={W - PAD.right + 6} y={yLine(v) + 3} textAnchor="start"
              className="chart-axis-text"
            >
              {fmtLine(v)}
            </text>
          ))}
          {/* Baseline */}
          <line
            x1={PAD.left} x2={W - PAD.right} y1={yBar(0)} y2={yBar(0)}
            stroke="var(--border)" strokeWidth="1"
          />

          {/* Bars: rounded data-end anchored to the baseline */}
          {points.map((p, i) => p.bar > 0 && (
            <path
              key={p.date}
              d={`M ${cx(i) - barW / 2} ${yBar(0)}
                  L ${cx(i) - barW / 2} ${yBar(p.bar) + 4}
                  Q ${cx(i) - barW / 2} ${yBar(p.bar)} ${cx(i) - barW / 2 + 4} ${yBar(p.bar)}
                  L ${cx(i) + barW / 2 - 4} ${yBar(p.bar)}
                  Q ${cx(i) + barW / 2} ${yBar(p.bar)} ${cx(i) + barW / 2} ${yBar(p.bar) + 4}
                  L ${cx(i) + barW / 2} ${yBar(0)} Z`}
              fill={barColor}
              opacity={hover === null || hover === i ? 0.9 : 0.4}
            />
          ))}

          {/* Rate line: 2px, with a surface ring where it crosses bars */}
          {segments.map((seg, i) => (
            <polyline
              key={i} points={seg} fill="none"
              stroke="var(--surface)" strokeWidth="4" opacity="0.6"
            />
          ))}
          {segments.map((seg, i) => (
            <polyline
              key={i} points={seg} fill="none"
              stroke={lineColor} strokeWidth="2"
              strokeLinejoin="round" strokeLinecap="round"
            />
          ))}

          {/* Hover marker on the line (≥8px) */}
          {hover !== null && points[hover].line !== null && (
            <circle
              cx={cx(hover)} cy={yLine(points[hover].line!)} r="4.5"
              fill={lineColor} stroke="var(--surface)" strokeWidth="2"
            />
          )}

          {/* Full-column hover hit targets */}
          {points.map((p, i) => (
            <rect
              key={p.date}
              x={PAD.left + slot * i} y={PAD.top} width={slot} height={plotH}
              fill="transparent"
              onMouseEnter={() => setHover(i)}
              onMouseLeave={() => setHover(null)}
            />
          ))}

          {/* Sparse x labels */}
          {points.map((p, i) => labelIdx.has(i) && (
            <text key={p.date} x={cx(i)} y={H - 18} textAnchor="middle" className="chart-axis-text">
              {fmtDate(p.date)}
            </text>
          ))}
        </svg>

        {/* Legend — identity is never color-alone: bar swatch vs line swatch */}
        <div className="chart-legend">
          <span className="legend-item">
            <span className="legend-swatch-bar" style={{ background: barColor }} />
            {barLabel}
          </span>
          <span className="legend-item">
            <span className="legend-swatch-line" style={{ background: lineColor }} />
            {lineLabel}
          </span>
        </div>

        {hover !== null && (
          <div
            className="chart-tooltip"
            style={{ left: `${(cx(hover) / W) * 100}%` }}
          >
            <span className="chart-tooltip-date">{fmtDate(points[hover].date)}</span>
            <span className="chart-tooltip-value">
              <span className="chart-tooltip-swatch" style={{ background: barColor }} />
              {points[hover].bar}
            </span>
            {points[hover].line !== null && (
              <span className="chart-tooltip-value">
                <span className="chart-tooltip-swatch chart-tooltip-swatch-line" style={{ background: lineColor }} />
                {fmtLine(points[hover].line!)}
              </span>
            )}
          </div>
        )}

        {isEmpty && <div className="chart-empty">No data yet</div>}
      </div>
    </div>
  );
}

export default ComboChart;
