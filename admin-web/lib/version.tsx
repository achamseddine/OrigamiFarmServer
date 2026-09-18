"use client";

import { useEffect, useState } from "react";

/** The build stamp, shown on the login page and in the console sidebar.
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

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "";

export function useServerVersion(): Health | null {
  const [health, setHealth] = useState<Health | null>(null);

  useEffect(() => {
    let cancelled = false;
    // cache: no-store — a cached answer here would defeat the point.
    fetch(`${API_BASE}/health`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((body) => {
        if (!cancelled && body && typeof body.version === "string") {
          setHealth(body as Health);
        }
      })
      // Silent on purpose: a server too unwell to answer /health will be
      // saying so far more loudly somewhere else on the screen.
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, []);

  return health;
}

function buildTitle(health: Health): string {
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
        fontFamily: "var(--font-mono, ui-monospace, monospace)",
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
