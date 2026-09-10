"use client";

import { useState } from "react";
import { useAuth } from "@/lib/auth";
import { ApiError } from "@/lib/api";

export default function LoginPage() {
  const { login } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      await login(email, password);
    } catch (err) {
      // 401 is the expected "wrong credentials" case and the API returns
      // the same message whether the address exists or not; anything else
      // is a real fault worth showing verbatim.
      if (err instanceof ApiError && err.status === 401) {
        setError("Incorrect email or password.");
      } else if (err instanceof ApiError) {
        setError(`${err.code}: ${err.message}`);
      } else {
        setError("Could not reach the server. Check your connection and try again.");
      }
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
      <form onSubmit={handleSubmit} className="panel" style={{ width: 380 }}>
        <div
          style={{
            fontFamily: "var(--font-display)",
            fontSize: "1.5rem",
            color: "var(--farmos-cedar)",
            marginBottom: 4,
          }}
        >
          Origami Server
        </div>
        <p className="page-subtitle" style={{ marginBottom: 24 }}>
          Platform admin console
        </p>

        {error && <div className="error-banner">{error}</div>}

        <div className="field-row">
          <label htmlFor="email">Email</label>
          <input
            id="email"
            type="email"
            autoComplete="username"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
          />
        </div>
        <div className="field-row">
          <label htmlFor="password">Password</label>
          <input
            id="password"
            type="password"
            autoComplete="current-password"
            required
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <button
          type="submit"
          className="btn btn-primary"
          disabled={submitting}
          style={{ width: "100%" }}
        >
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        <p style={{ fontSize: "0.75rem", color: "var(--farmos-muted)", marginTop: 16 }}>
          Staff accounts are created with{" "}
          <code>scripts/create_platform_admin.py</code>, which is also how a lost password is
          reset.
        </p>
      </form>
    </div>
  );
}
