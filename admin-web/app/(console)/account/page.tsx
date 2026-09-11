"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ErrorBanner, PageHeader, describeError } from "@/lib/ui";

const MIN_LENGTH = 12;

export default function AccountPage() {
  const { me, refresh } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function changePassword(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);

    if (next !== confirm) {
      setError("The new passwords do not match.");
      return;
    }

    setSubmitting(true);
    try {
      await apiFetch("/platform/v1/auth/change-password", {
        method: "POST",
        body: { current_password: current, new_password: next },
      });
      setNotice("Password changed. It applies the next time you sign in.");
      // So the "someone else knows this password" notice, here and on the
      // Getting started checklist, stops being shown the moment it stops
      // being true.
      await refresh();
      setCurrent("");
      setNext("");
      setConfirm("");
    } catch (err) {
      setError(describeError(err));
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <PageHeader title="My account" subtitle="Your identity and platform access." />

      {me?.password_set_by_someone_else && !notice && (
        <div className="notice-banner">
          The password on this account is still the one whoever created it typed for you, so they
          know it too. Change it below.
        </div>
      )}

      <div className="panel">
        <div className="meta-grid">
          <div>
            <div className="k">Name</div>
            <div className="v">{me?.display_name}</div>
          </div>
          <div>
            <div className="k">Email</div>
            <div className="v">{me?.email}</div>
          </div>
          <div>
            <div className="k">Platform roles</div>
            <div className="v">
              {me?.platform_roles
                .map((role) => role.replace("PLATFORM_", "").replace(/_/g, " ").toLowerCase())
                .join(", ")}
            </div>
          </div>
        </div>
      </div>

      <div className="panel" style={{ maxWidth: 460 }}>
        <div className="chart-title" style={{ marginBottom: 12 }}>
          Change password
        </div>
        <ErrorBanner message={error} />
        {notice && <div className="notice-banner">{notice}</div>}

        <form onSubmit={changePassword}>
          <div className="field-row">
            <label htmlFor="current">Current password</label>
            <input
              id="current"
              type="password"
              autoComplete="current-password"
              required
              value={current}
              onChange={(e) => setCurrent(e.target.value)}
            />
          </div>
          <div className="field-row">
            <label htmlFor="next">New password (at least {MIN_LENGTH} characters)</label>
            <input
              id="next"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_LENGTH}
              value={next}
              onChange={(e) => setNext(e.target.value)}
            />
          </div>
          <div className="field-row">
            <label htmlFor="confirm">Repeat new password</label>
            <input
              id="confirm"
              type="password"
              autoComplete="new-password"
              required
              minLength={MIN_LENGTH}
              value={confirm}
              onChange={(e) => setConfirm(e.target.value)}
            />
          </div>
          <button className="btn btn-primary" type="submit" disabled={submitting}>
            {submitting ? "Changing…" : "Change password"}
          </button>
        </form>
      </div>
    </div>
  );
}
