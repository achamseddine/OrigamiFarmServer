"use client";

import { Suspense, useCallback, useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { apiFetch } from "@/lib/api";
import { BrandLogo } from "@/components/Brand";
import { Icon } from "@/components/Icon";

/** Where an invitation link lands.
 *
 * Public and outside the console shell on purpose: the person opening this
 * is a farm owner or worker who has no account yet, is very likely on a
 * phone, and must not meet a platform admin login screen. The token in the
 * query string is their only credential.
 *
 * Called /welcome rather than /activate because the product already had
 * "device activation codes" for pairing a tablet, and two different
 * credentials both called activation is a trap somebody walks into — an
 * admin pasted a pairing code into this page's URL and reasonably expected
 * it to work. /activate still exists and forwards here, so links already
 * sent keep working.
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
    <div className="entry-solo">
      <div className="panel">
        <BrandLogo size={42} />
        <div style={{ marginTop: 22 }}>{children}</div>
      </div>
    </div>
  );
}

function WelcomeForm() {
  const params = useSearchParams();
  const token = params.get("token") ?? "";

  const [check, setCheck] = useState<CheckResult | null>(null);
  // Pairing keys are gone with device licences, but ones handed out before
  // that are still on scraps of paper, and pasting one here is the mistake
  // this page is most likely to be shown. Naming it beats "invalid link".
  const looksLikePairingKey = /^ORG[-A-Z0-9]*$/i.test(token.trim());
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
        <h1 className="page-title" style={{ fontSize: "1.3rem", display: "flex", alignItems: "center", gap: 10 }}>
          <span className="roundel" style={{ width: 38, height: 38 }}>
            <Icon name="check" size={20} />
          </span>
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
        {looksLikePairingKey ? (
          <>
            <p className="page-subtitle">
              That looks like an old <strong>tablet pairing key</strong>, not a sign-in link.
              Tablets are no longer paired — you just sign in on them — so a key like that does
              nothing now.
            </p>
            <p className="page-subtitle" style={{ fontSize: "0.88rem" }}>
              A sign-in link is a full web address ending in <code>?token=…</code>, and it is the
              one that sets your password.
            </p>
            <p className="page-subtitle" style={{ fontSize: "0.85rem" }}>
              Ask whoever set up your account for the sign-in link.
            </p>
          </>
        ) : (
          <>
            <p className="page-subtitle">{check.message}</p>
            <p className="page-subtitle" style={{ fontSize: "0.85rem" }}>
              Ask whoever set up your account to send you a new invitation.
            </p>
          </>
        )}
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

      {error && (
        <div className="error-banner" role="alert">
          <Icon name="warning" size={18} />
          <span>{error}</span>
        </div>
      )}

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
        <button
          className="btn btn-primary btn-fold"
          type="submit"
          disabled={submitting}
          style={{ width: "100%" }}
        >
          {submitting ? "Saving…" : "Set my password"}
          {!submitting && <Icon name="arrow-right" size={20} />}
        </button>
      </form>
    </Shell>
  );
}

export default function WelcomePage() {
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
      <WelcomeForm />
    </Suspense>
  );
}
