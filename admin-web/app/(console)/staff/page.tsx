"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { useAuth } from "@/lib/auth";
import { ErrorBanner, Loading, PageHeader, describeError, useResource } from "@/lib/ui";
import { Staff } from "@/lib/types";

const ROLES = [
  "PLATFORM_SUPER_ADMIN",
  "PLATFORM_COMMERCIAL_ADMIN",
  "PLATFORM_SUPPORT_ADMIN",
  "PLATFORM_AUDITOR",
] as const;

const ROLE_MEANING: Record<string, string> = {
  PLATFORM_SUPER_ADMIN: "Everything, including managing staff",
  PLATFORM_COMMERCIAL_ADMIN: "Tenants, plans and entitlements; cannot terminate or manage staff",
  PLATFORM_SUPPORT_ADMIN: "Support sessions and read access for troubleshooting",
  PLATFORM_AUDITOR: "Read-only across the platform",
};

function roleLabel(role: string): string {
  return role.replace("PLATFORM_", "").replace(/_/g, " ").toLowerCase();
}

export default function StaffPage() {
  const { me } = useAuth();
  const { data, error, loading, reload } = useResource<Staff[]>("/platform/v1/staff");
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({
    email: "",
    display_name: "",
    platform_role: "PLATFORM_SUPPORT_ADMIN",
    password: "",
  });

  const isSuperAdmin = me?.platform_roles.includes("PLATFORM_SUPER_ADMIN") ?? false;

  async function run(work: () => Promise<void>, success: string) {
    setActionError(null);
    setNotice(null);
    try {
      await work();
      setNotice(success);
      await reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  async function createStaff(e: React.FormEvent) {
    e.preventDefault();
    await run(async () => {
      await apiFetch("/platform/v1/staff", { method: "POST", body: form });
      setForm({ email: "", display_name: "", platform_role: "PLATFORM_SUPPORT_ADMIN", password: "" });
      setCreating(false);
    }, `Created ${form.email}. Pass the password on directly — it is not emailed.`);
  }

  return (
    <div>
      <PageHeader
        title="Staff & access"
        subtitle="Who can reach this console, and what each of them is allowed to do."
      />

      <ErrorBanner message={error || actionError} />
      {notice && <div className="notice-banner">{notice}</div>}

      {isSuperAdmin && (
        <div className="panel">
          {!creating ? (
            <button className="btn btn-primary" onClick={() => setCreating(true)}>
              Add a staff account
            </button>
          ) : (
            <form onSubmit={createStaff}>
              <div className="chart-title" style={{ marginBottom: 12 }}>
                New staff account
              </div>
              <div className="field-row">
                <label htmlFor="staff-email">Email</label>
                <input
                  id="staff-email"
                  type="email"
                  required
                  value={form.email}
                  onChange={(e) => setForm({ ...form, email: e.target.value })}
                />
              </div>
              <div className="field-row">
                <label htmlFor="staff-name">Display name</label>
                <input
                  id="staff-name"
                  required
                  value={form.display_name}
                  onChange={(e) => setForm({ ...form, display_name: e.target.value })}
                />
              </div>
              <div className="field-row">
                <label htmlFor="staff-role">Role</label>
                <select
                  id="staff-role"
                  value={form.platform_role}
                  onChange={(e) => setForm({ ...form, platform_role: e.target.value })}
                >
                  {ROLES.map((role) => (
                    <option key={role} value={role}>
                      {roleLabel(role)} — {ROLE_MEANING[role]}
                    </option>
                  ))}
                </select>
              </div>
              <div className="field-row">
                <label htmlFor="staff-password">Initial password (at least 12 characters)</label>
                <input
                  id="staff-password"
                  type="text"
                  required
                  minLength={12}
                  value={form.password}
                  onChange={(e) => setForm({ ...form, password: e.target.value })}
                />
              </div>
              <div className="inline-actions">
                <button className="btn btn-primary" type="submit">
                  Create
                </button>
                <button className="btn btn-secondary" type="button" onClick={() => setCreating(false)}>
                  Cancel
                </button>
              </div>
            </form>
          )}
        </div>
      )}

      {loading && <Loading what="staff" />}

      {data && (
        <div className="panel">
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Roles</th>
                  <th>Sign-in</th>
                  {isSuperAdmin && <th>Change access</th>}
                </tr>
              </thead>
              <tbody>
                {data.map((person) => (
                  <tr key={person.user_id}>
                    <td>
                      {person.display_name}
                      <div style={{ color: "var(--farmos-muted)", fontSize: "0.75rem" }}>
                        {person.email}
                        {person.user_id === me?.user_id && " · you"}
                      </div>
                    </td>
                    <td>
                      {person.platform_roles.map((role) => (
                        <span key={role} className="chip chip-active" style={{ marginRight: 4 }}>
                          {roleLabel(role)}
                        </span>
                      ))}
                    </td>
                    <td style={{ color: "var(--farmos-muted)", fontSize: "0.8rem" }}>
                      {person.has_password ? "Password" : "No password (OIDC only)"}
                    </td>
                    {isSuperAdmin && (
                      <td>
                        <div className="inline-actions">
                          {ROLES.filter((role) => !person.platform_roles.includes(role)).map((role) => (
                            <button
                              key={role}
                              className="btn btn-secondary btn-sm"
                              onClick={() =>
                                run(
                                  () =>
                                    apiFetch(`/platform/v1/staff/${person.user_id}/roles`, {
                                      method: "POST",
                                      body: { platform_role: role },
                                    }),
                                  `Granted ${roleLabel(role)} to ${person.email}.`
                                )
                              }
                            >
                              + {roleLabel(role)}
                            </button>
                          ))}
                          {person.platform_roles
                            // Revoking your own super admin is refused by the
                            // API, so don't offer a button that cannot work.
                            .filter(
                              (role) =>
                                !(
                                  person.user_id === me?.user_id &&
                                  role === "PLATFORM_SUPER_ADMIN"
                                )
                            )
                            .map((role) => (
                            <button
                              key={role}
                              className="btn btn-danger btn-sm"
                              onClick={() =>
                                run(
                                  () =>
                                    apiFetch(`/platform/v1/staff/${person.user_id}/roles/${role}`, {
                                      method: "DELETE",
                                    }),
                                  `Revoked ${roleLabel(role)} from ${person.email}.`
                                )
                              }
                            >
                              − {roleLabel(role)}
                            </button>
                          ))}
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => {
                              const next = window.prompt(
                                `New password for ${person.email} (at least 12 characters):`
                              );
                              if (!next) return;
                              run(
                                () =>
                                  apiFetch(`/platform/v1/staff/${person.user_id}/password`, {
                                    method: "POST",
                                    body: { new_password: next },
                                  }),
                                `Password reset for ${person.email}.`
                              );
                            }}
                          >
                            Reset password
                          </button>
                        </div>
                      </td>
                    )}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {!isSuperAdmin && (
            <p className="chart-note" style={{ marginTop: 12, marginBottom: 0 }}>
              Only a super admin can change who has access.
            </p>
          )}
        </div>
      )}
    </div>
  );
}
