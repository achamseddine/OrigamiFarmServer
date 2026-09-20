"use client";

import { useEffect, useState } from "react";

/** The build stamp, shown on the login page and in the console chrome.
 *
 * Read from the server at runtime (GET /health) rather than baked into
 * this bundle at build time. The two would usually agree — console and API
 * ship in one image — but only one of them answers the question people
 * actually ask, which is whether the thing now running is the thing that
 * was just deployed. A number compiled into the page can be served from a
 * stale cache and lie; a number the server just told us cannot.
 *
 * /health needs no authentication, so this works on the login screen,
 * where it matters most: you can check what is deployed before you can get
 * in, which is exactly the situation where you most need to know.
 */

interface Health {
  status: string;
  version: string;
  built_at: string;
}

/** "checking" is not "down": the pill must not flash a red dot while the
 *  first request is still in flight. */
export type HealthState = "checking" | "live" | "down";

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export function useServerHealth(): { health: Health | null; state: HealthState } {
  const [health, setHealth] = useState<Health | null>(null);
  const [state, setState] = useState<HealthState>("checking");

  useEffect(() => {
    let cancelled = false;
    // cache: no-store — a cached answer here would defeat the point.
    fetch(`${API_BASE}/health`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (cancelled) return;
        if (body && typeof body.version === "string") {
          setHealth(body as Health);
          setState("live");
        } else {
          setState("down");
        }
      })
      .catch(() => {
        if (!cancelled) setState("down");
      });
    return () => {
      cancelled = true;
    };
  }, []);

  return { health, state };
}

export function useServerVersion(): Health | null {
  return useServerHealth().health;
}

export function buildTitle(health: Health): string {
  if (!health.built_at) return "Server build";
  const when = new Date(health.built_at);
  if (Number.isNaN(when.getTime())) return `Built ${health.built_at}`;
  return `Built ${when.toLocaleString()}`;
}

/** One line of small print naming the running build. */
export function VersionStamp({ align = "left" }: { align?: "left" | "center" }) {
  const health = useServerVersion();
  if (!health) return null;

  return (
    <div
      title={buildTitle(health)}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.68rem",
        letterSpacing: "0.02em",
        opacity: 0.65,
        textAlign: align,
      }}
    >
      {health.version}
    </div>
  );
}

/** The top-bar state pill — the console's equivalent of the tablet's sync
 *  pill. Whether the API is answering, said in a word and repeated in the
 *  dot, never in the dot alone. */
export function ServerPill() {
  const { health, state } = useServerHealth();

  const label =
    state === "live" ? "Connected" : state === "down" ? "No answer" : "Checking…";
  const detail =
    state === "live"
      ? health?.version ?? "API reachable"
      : state === "down"
        ? "API unreachable"
        : "Reaching the API";

  return (
    <span
      className="state-pill"
      data-state={state === "live" ? "live" : state === "down" ? "down" : "unknown"}
      title={health ? buildTitle(health) : undefined}
      role="status"
    >
      <span className="dot" aria-hidden="true" />
      <span>
        <span className="t">{label}</span>
        <br />
        <span className="d">{detail}</span>
      </span>
    </span>
  );
}
