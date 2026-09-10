"use client";

import { useCallback, useEffect, useState } from "react";
import { ApiError, apiFetch } from "./api";

/** Fetches a GET endpoint and tracks its loading/error state.
 *
 * Every screen in the console does the same three things with a response,
 * and repeating that by hand is how one of them ends up silently swallowing
 * an error.
 */
export function useResource<T>(path: string | null) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const reload = useCallback(async () => {
    if (path === null) {
      setLoading(false);
      return;
    }
    setLoading(true);
    try {
      setData(await apiFetch<T>(path));
      setError(null);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Request failed");
    } finally {
      setLoading(false);
    }
  }, [path]);

  useEffect(() => {
    reload();
  }, [reload]);

  return { data, error, loading, reload };
}

export function describeError(err: unknown): string {
  return err instanceof ApiError ? `${err.code}: ${err.message}` : "Something went wrong";
}

export function PageHeader({ title, subtitle }: { title: string; subtitle?: string }) {
  return (
    <div>
      <h1 className="page-title">{title}</h1>
      {subtitle && <p className="page-subtitle">{subtitle}</p>}
    </div>
  );
}

export function Loading({ what }: { what: string }) {
  return <div className="empty-note">Loading {what}…</div>;
}

export function ErrorBanner({ message }: { message: string | null }) {
  if (!message) return null;
  return <div className="error-banner">{message}</div>;
}

export function StatTile({ value, label, href }: { value: number | string; label: string; href?: string }) {
  const body = (
    <>
      <div className="value">{value}</div>
      <div className="label">{label}</div>
    </>
  );
  return href ? (
    <a className="stat-card" href={href}>
      {body}
    </a>
  ) : (
    <div className="stat-card">{body}</div>
  );
}

export function formatDateTime(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleString();
}

export function formatDate(value: string | null | undefined): string {
  if (!value) return "—";
  return new Date(value).toLocaleDateString();
}

/** Days until a timestamp, negative once it has passed. */
export function daysUntil(value: string): number {
  return Math.round((new Date(value).getTime() - Date.now()) / 86_400_000);
}

// --- charts -----------------------------------------------------------
//
// Both charts below plot a single series, so there is no legend: the title
// names the measure, and colour carries magnitude rather than identity.

interface Tip {
  x: number;
  y: number;
  text: string;
}

function useTooltip() {
  const [tip, setTip] = useState<Tip | null>(null);
  const show = (e: React.MouseEvent, text: string) => setTip({ x: e.clientX, y: e.clientY, text });
  const hide = () => setTip(null);
  const node = tip ? (
    <div className="chart-tooltip" style={{ left: tip.x + 12, top: tip.y - 34 }} role="status">
      {tip.text}
    </div>
  ) : null;
  return { show, hide, node };
}

export interface Point {
  label: string;
  value: number;
  detail?: string;
}

/** Counts over evenly spaced periods — days, months. */
export function TimeBars({
  points,
  title,
  note,
  unit,
}: {
  points: Point[];
  title: string;
  note?: string;
  unit: string;
}) {
  const { show, hide, node } = useTooltip();
  const max = Math.max(1, ...points.map((p) => p.value));

  return (
    <div className="chart">
      <div className="chart-title">{title}</div>
      {note && <div className="chart-note">{note}</div>}
      <div className="time-bars">
        {points.map((point) => (
          <div
            key={point.label}
            className="slot"
            onMouseEnter={(e) => show(e, `${point.detail ?? point.label}: ${point.value} ${unit}`)}
            onMouseMove={(e) => show(e, `${point.detail ?? point.label}: ${point.value} ${unit}`)}
            onMouseLeave={hide}
          >
            <div
              className={`mark${point.value === 0 ? " empty" : ""}`}
              style={{ height: `${Math.max(2, (point.value / max) * 100)}%` }}
            />
          </div>
        ))}
      </div>
      <div className="time-axis">
        <span>{points[0]?.detail ?? points[0]?.label}</span>
        <span>peak {max}</span>
        {/* One point would otherwise print the same label at both ends. */}
        {points.length > 1 && (
          <span>{points[points.length - 1]?.detail ?? points[points.length - 1]?.label}</span>
        )}
      </div>
      {node}
    </div>
  );
}

/** Magnitude across named categories, largest first. */
export function RankedBars({
  points,
  title,
  note,
  unit,
  limit = 12,
}: {
  points: Point[];
  title: string;
  note?: string;
  unit: string;
  limit?: number;
}) {
  const { show, hide, node } = useTooltip();
  const ranked = [...points].sort((a, b) => b.value - a.value).slice(0, limit);
  const max = Math.max(1, ...ranked.map((p) => p.value));

  if (ranked.length === 0) {
    return (
      <div className="chart">
        <div className="chart-title">{title}</div>
        <div className="empty-note">Nothing recorded yet.</div>
      </div>
    );
  }

  return (
    <div className="chart">
      <div className="chart-title">{title}</div>
      {note && <div className="chart-note">{note}</div>}
      <div className="ranked-bars">
        {ranked.map((point) => (
          <div
            key={point.label}
            className="ranked-row"
            onMouseEnter={(e) => show(e, `${point.label}: ${point.value} ${unit}`)}
            onMouseMove={(e) => show(e, `${point.label}: ${point.value} ${unit}`)}
            onMouseLeave={hide}
          >
            <div className="label" title={point.label}>
              {point.label}
            </div>
            <div className="track">
              <div className="mark" style={{ width: `${(point.value / max) * 100}%` }} />
            </div>
            <div className="value">{point.value}</div>
          </div>
        ))}
      </div>
      {node}
    </div>
  );
}
