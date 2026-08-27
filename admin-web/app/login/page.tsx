"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("admin@origami-platform.com");
  const [displayName, setDisplayName] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, displayName);
    } catch (err) {
      const message =
        err instanceof ApiError
          ? `${err.code}: ${err.message}`
          : "Could not sign in. Is the API reachable and AUTH_DEV_MODE enabled?";
      setError(message);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--farmos-stone)",
      }}
    >
      <form
        onSubmit={handleSubmit}
        className="panel"
        style={{ width: 380 }}
      >
        <div style={{ fontFamily: "var(--font-display)", fontSize: "1.5rem", color: "var(--farmos-cedar)", marginBottom: 4 }}>
          Origami Server
        </div>
        <p className="page-subtitle" style={{ marginBottom: 24 }}>
          Platform admin console — local/dev sign-in
        </p>

        {error && <div className="error-banner">{error}</div>}

        <div className="field-row">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field-row">
          <label htmlFor="displayName">Display name (optional)</label>
          <input
            id="displayName"
            type="text"
            value={displayName}
            onChange={(e) => setDisplayName(e.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={submitting} style={{ width: "100%" }}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        <p style={{ fontSize: "0.75rem", color: "var(--farmos-muted)", marginTop: 16 }}>
          This calls <code>/api/v1/auth/dev-login</code>, which only works when the API has{" "}
          <code>AUTH_DEV_MODE=true</code> — local and staging only. Production uses your OIDC
          provider instead.
        </p>
      </form>
    </div>
  );
}
