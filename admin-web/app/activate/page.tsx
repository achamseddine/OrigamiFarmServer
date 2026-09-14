"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiFetch } from "@/lib/api";

/** Where an invitation link lands.
 *
 * Public and outside the console shell on purpose: the person opening this
 * is a farm owner or worker who has no account yet, is very likely on a
 * phone, and must not meet a platform admin login screen. The token in the
 * query string is their only credential.
 */

const MIN_LENGTH = 8;

interface CheckResult {
  valid: boolean;
  email?: string | null;
  display_name?: string | null;
  tenant_name?: string | null;
  message?: string | null;
}

function Shell({ children }: { children: React.ReactNode }) {
  return (
    <div
      style={{
        minHeight: "100vh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--farmos-stone)",
        padding: 16,
      }}
    >
      <div className="panel" style={{ width: "100%", maxWidth: 420 }}>
        <div
          style={{
            fontFamily: "var(--font-display)",
            fontSize: "1.5rem",
            color: "var(--farmos-cedar)",
            marginBottom: 20,
          }}
        >
          Origami
        </div>
        {children}
      </div>
    </div>
  );
}

function ActivateForm() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [check, setCheck] = useState<CheckResult | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [done, setDone] = useState<{ display_name: string; tenant_name: string } | null>(null);

  // Checked before anything is typed: a page that could only discover an
  // expired link by trying to use it would burn the token to show an error.
  const verify = useCallback(async () => {
    if (!token) {
      setCheck({ valid: false, message: "This link is incomplete. Use the full link you were sent." });
      return;
    }
    try {
      setCheck(await apiFetch<CheckResult>("/api/v1/auth/invitation/check", {
        method: "POST",
        body: { token },
      }));
    } catch {
      setCheck({ valid: false, message: "Could not reach the server. Try again in a moment." });
    }
  }, [token]);

  useEffect(() => {
    verify();
  }, [verify]);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    if (password !== confirm) {
      setError("The two passwords do not match.");
      return;
    }
    setSubmitting(true);
    try {
      const result = await apiFetch<{ display_name: string; tenant_name: string }>(
        "/api/v1/auth/invitation/accept",
        { method: "POST", body: { token, password } }
      );
      setDone(result);
    } catch (err) {
      setError(
        err instanceof Error && err.message
          ? err.message
          : "Something went wrong. Ask for a new invitation."
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (done) {
    return (
      <Shell>
        <h1 className="page-title" style={{ fontSize: "1.3rem" }}>
          You&rsquo;re all set, {done.display_name}
        </h1>
        <p className="page-subtitle">
          Your password is saved for {done.tenant_name}. Open the Origami app on your tablet or
          phone and sign in with your email address and the password you just chose.
        </p>
      </Shell>
    );
  }

  if (check === null) {
    return (
      <Shell>
        <p className="page-subtitle" style={{ margin: 0 }}>
          Checking your invitation…
        </p>
      </Shell>
    );
  }

  if (!check.valid) {
    return (
      <Shell>
        <h1 className="page-title" style={{ fontSize: "1.3rem" }}>
          This link doesn&rsquo;t work
        </h1>
        <p className="page-subtitle">{check.message}</p>
        <p className="page-subtitle" style={{ fontSize: "0.85rem" }}>
          Ask whoever set up your account to send you a new invitation.
        </p>
      </Shell>
    );
  }

  return (
    <Shell>
      <h1 className="page-title" style={{ fontSize: "1.3rem" }}>
        Welcome{check.display_name ? `, ${check.display_name}` : ""}
      </h1>
      <p className="page-subtitle">
        Choose a password for your {check.tenant_name} account. You&rsquo;ll sign in with{" "}
        <strong>{check.email}</strong>.
      </p>

      {error && <div className="error-banner">{error}</div>}

      <form onSubmit={submit}>
        <div className="field-row">
          <label htmlFor="pw">Password (at least {MIN_LENGTH} characters)</label>
          <input
            id="pw"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_LENGTH}
            value={password}
            onChange={(e) => setPassword(e.target.value)}
          />
        </div>
        <div className="field-row">
          <label htmlFor="pw2">Repeat password</label>
          <input
            id="pw2"
            type="password"
            autoComplete="new-password"
            required
            minLength={MIN_LENGTH}
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </div>
        <button className="btn btn-primary" type="submit" disabled={submitting} style={{ width: "100%" }}>
          {submitting ? "Saving…" : "Set my password"}
        </button>
      </form>
    </Shell>
  );
}

export default function ActivatePage() {
  // useSearchParams needs a Suspense boundary or the static export fails
  // to prerender this route.
  return (
    <Suspense
      fallback={
        <Shell>
          <p className="page-subtitle" style={{ margin: 0 }}>
            Loading…
          </p>
        </Shell>
      }
    >
      <ActivateForm />
    </Suspense>
  );
}
