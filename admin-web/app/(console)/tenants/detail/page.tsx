"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import {
  AuditEventItem,
  DeviceItem,
  Farm,
  IssuedInvitation,
  LicencePack,
  MemberPassword,
  Membership,
  Plan,
  Subscription,
  SubscriptionSaveResult,
  Tenant,
  TenantUsage,
} from "@/lib/types";
import { StatusChip } from "@/components/StatusChip";
import Link from "next/link";
import {
  Loading,
  RankedBars,
  StatTile,
  describeError,
  formatDate,
  formatDateTime,
  formatPrice,
  useResource,
} from "@/lib/ui";

/** "Modules" and "Licensing" are gone.
 *
 *  Modules switched entitlements on and off for one customer; every
 *  customer now has all of them. Licensing listed offline leases and
 *  issued a tablet pairing key, neither of which exists. What survived of
 *  Licensing is the part an operator actually used — handing a new owner
 *  their way in — so it is a tab of its own under the name of the job.
 */
const TABS = [
  "Overview",
  "Usage",
  "Subscription",
  "Access",
  "Handover",
  "Farms",
  "Devices",
  "Audit",
] as const;
type Tab = (typeof TABS)[number];

export default function TenantDetailPage() {
  // useSearchParams has to sit under a Suspense boundary or the static
  // export refuses to prerender this route.
  return (
    <Suspense fallback={<div>Loading…</div>}>
      <TenantDetail />
    </Suspense>
  );
}

function TenantDetail() {
  const tenantId = useSearchParams().get("id") || "";

  const [tab, setTab] = useState<Tab>("Overview");
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadTenant = useCallback(() => {
    if (!tenantId) {
      // Without an id the request would fall through to the list endpoint
      // and render nonsense, so stop here instead.
      setError("No tenant selected — open this page from the tenant list.");
      return;
    }
    apiFetch<Tenant>(`/platform/v1/tenants/${tenantId}`).then(setTenant).catch((e) => setError(e.message));
  }, [tenantId]);

  useEffect(() => {
    loadTenant();
  }, [loadTenant]);

  async function changeStatus(status: string) {
    const reason = window.prompt(`Reason for changing status to ${status}:`);
    if (!reason) return;
    try {
      await apiFetch(`/platform/v1/tenants/${tenantId}/status`, {
        method: "POST",
        body: { status, reason },
      });
      setNotice(`Tenant status changed to ${status}.`);
      loadTenant();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to change status");
    }
  }

  if (!tenant) {
    return <div>{error ? <div className="error-banner">{error}</div> : "Loading…"}</div>;
  }

  return (
    <div>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start" }}>
        <div>
          <h1 className="page-title">
            {tenant.display_name} <span style={{ color: "var(--farmos-muted)", fontWeight: 400 }}>({tenant.company_code})</span>
          </h1>
          <p className="page-subtitle">
            Tenant ID: <code>{tenant.id}</code> · <StatusChip status={tenant.status} />
          </p>
        </div>
        <div style={{ display: "flex", gap: 8 }}>
          {tenant.status !== "SUSPENDED" && tenant.status !== "TERMINATED" && (
            <button className="btn btn-secondary" onClick={() => changeStatus("SUSPENDED")}>
              Suspend
            </button>
          )}
          {tenant.status === "SUSPENDED" && (
            <button className="btn btn-primary" onClick={() => changeStatus("ACTIVE")}>
              Reactivate
            </button>
          )}
          {tenant.status !== "TERMINATED" && (
            <button className="btn btn-danger" onClick={() => changeStatus("TERMINATED")}>
              Terminate
            </button>
          )}
        </div>
      </div>

      {error && <div className="error-banner">{error}</div>}
      {notice && (
        <div className="panel" style={{ background: "var(--farmos-mist)", borderColor: "var(--farmos-olive)" }}>
          {notice}
        </div>
      )}

      <div className="tabs">
        {TABS.map((t) => (
          <div key={t} className={`tab ${tab === t ? "active" : ""}`} onClick={() => setTab(t)}>
            {t}
          </div>
        ))}
      </div>

      {tab === "Overview" && <OverviewTab tenant={tenant} />}
      {tab === "Usage" && <UsageTab tenantId={tenantId} />}
      {tab === "Subscription" && <SubscriptionTab tenantId={tenantId} />}
      {tab === "Access" && <AccessTab tenantId={tenantId} />}
      {tab === "Handover" && <HandoverTab tenantId={tenantId} />}
      {tab === "Farms" && <FarmsTab tenantId={tenantId} />}
      {tab === "Devices" && <DevicesTab tenantId={tenantId} />}
      {tab === "Audit" && <AuditTab tenantId={tenantId} />}
    </div>
  );
}

function OverviewTab({ tenant }: { tenant: Tenant }) {
  return (
    <div className="panel">
      <table>
        <tbody>
          <tr><th>Legal name</th><td>{tenant.legal_name}</td></tr>
          <tr><th>Country</th><td>{tenant.country}</td></tr>
          <tr><th>Timezone</th><td>{tenant.timezone}</td></tr>
          <tr><th>Currency</th><td>{tenant.default_currency}</td></tr>
          <tr><th>Onboarding</th><td>{tenant.onboarding_status}</td></tr>
          <tr><th>Created</th><td>{new Date(tenant.created_at).toLocaleString()}</td></tr>
        </tbody>
      </table>
    </div>
  );
}

function FarmsTab({ tenantId }: { tenantId: string }) {
  const [farms, setFarms] = useState<Farm[] | null>(null);
  useEffect(() => {
    apiFetch<Farm[]>(`/platform/v1/tenants/${tenantId}/farms`).then(setFarms);
  }, [tenantId]);

  return (
    <div className="panel" style={{ padding: 0 }}>
      <table>
        <thead><tr><th>Farm code</th><th>Name</th><th>Active</th></tr></thead>
        <tbody>
          {farms?.map((f) => (
            <tr key={f.id}><td>{f.farm_code}</td><td>{f.name}</td><td>{f.active ? "Yes" : "No"}</td></tr>
          ))}
          {farms && farms.length === 0 && (
            <tr><td colSpan={3} style={{ textAlign: "center", padding: 24, color: "var(--farmos-muted)" }}>No farms yet.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function DevicesTab({ tenantId }: { tenantId: string }) {
  const [devices, setDevices] = useState<DeviceItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch<DeviceItem[]>(`/platform/v1/tenants/${tenantId}/devices`).then(setDevices);
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  async function revoke(deviceId: string) {
    const reason = window.prompt("Reason for revoking this tablet:");
    if (!reason) return;
    try {
      await apiFetch(`/platform/v1/devices/${deviceId}/revoke`, { method: "POST", body: { reason } });
      load();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to revoke device");
    }
  }

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          Tablets this customer uses
        </div>
        <div className="chart-note">
          A tablet appears here by itself, the first time somebody signs in on it — there is no
          pairing key to generate and nothing to type into the app. So this answers{" "}
          <strong>which tablets is this customer using</strong>, not which ones they are permitted.
          Revoke one that has been lost or stolen; it stops being counted and stays revoked however
          often somebody signs in on it again.
        </div>
      </div>

      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Installation ID</th><th>Name</th><th>Status</th><th>Last seen</th><th></th></tr></thead>
          <tbody>
            {devices?.map((d) => (
              <tr key={d.id}>
                <td>{d.installation_id}</td>
                <td>{d.display_name}</td>
                <td><StatusChip status={d.status} /></td>
                <td>{d.last_seen_at ? new Date(d.last_seen_at).toLocaleString() : "—"}</td>
                <td>
                  {d.status === "ACTIVE" && (
                    <button className="btn btn-secondary" onClick={() => revoke(d.id)}>Revoke</button>
                  )}
                </td>
              </tr>
            ))}
            {devices && devices.length === 0 && (
              <tr><td colSpan={5} style={{ textAlign: "center", padding: 24, color: "var(--farmos-muted)" }}>No tablet has signed in for this customer yet.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function AuditTab({ tenantId }: { tenantId: string }) {
  const [events, setEvents] = useState<AuditEventItem[] | null>(null);
  useEffect(() => {
    apiFetch<AuditEventItem[]>(`/platform/v1/audit-events?tenant_id=${tenantId}`).then(setEvents);
  }, [tenantId]);

  return (
    <div className="panel" style={{ padding: 0 }}>
      <table>
        <thead><tr><th>When</th><th>Action</th><th>Entity</th><th>Reason</th></tr></thead>
        <tbody>
          {events?.map((e) => (
            <tr key={e.id}>
              <td>{new Date(e.created_at).toLocaleString()}</td>
              <td>{e.action}</td>
              <td>{e.entity_type}{e.entity_id ? ` (${e.entity_id.slice(0, 8)}…)` : ""}</td>
              <td>{e.reason || "—"}</td>
            </tr>
          ))}
          {events && events.length === 0 && (
            <tr><td colSpan={4} style={{ textAlign: "center", padding: 24, color: "var(--farmos-muted)" }}>No audit events yet.</td></tr>
          )}
        </tbody>
      </table>
    </div>
  );
}

function UsageTab({ tenantId }: { tenantId: string }) {
  const { data, error, loading } = useResource<TenantUsage>(`/platform/v1/metrics/usage/${tenantId}`);

  if (loading) return <Loading what="usage" />;
  if (error) return <div className="error-banner">{error}</div>;
  if (!data) return null;

  return (
    <div>
      <div className="card-grid">
        <StatTile value={data.total_records} label="Live records" />
        <StatTile value={data.modules_with_data.length} label="Modules in use" />
        <StatTile value={data.active_users} label="Active users" />
        <StatTile value={data.active_devices} label="Active devices" />
      </div>

      <div className="panel">
        <RankedBars
          title="Records by module"
          note="Counted inside this tenant's own isolated session, excluding deleted rows."
          unit="records"
          points={Object.entries(data.records_by_module).map(([module, value]) => ({
            label: module.replace(/_/g, " "),
            value,
          }))}
        />
      </div>

      <div className="panel">
        <div className="meta-grid">
          <div>
            <div className="k">Last activity</div>
            <div className="v">{formatDateTime(data.last_activity_at)}</div>
          </div>
          <div>
            <div className="k">Farm-data modules in use</div>
            <div className="v">{data.modules_with_data.join(", ") || "None"}</div>
          </div>
        </div>
        <p className="chart-note" style={{ marginTop: 12, marginBottom: 0 }}>
          This used to sit beside a list of what the customer was entitled to. They are entitled to
          all of it, so what is worth reading here is the gap between what they pay for and what
          they have actually started using.
        </p>
      </div>
    </div>
  );
}

function AccessTab({ tenantId }: { tenantId: string }) {
  const { data, error, loading, reload } = useResource<Membership[]>(
    `/platform/v1/tenants/${tenantId}/memberships`
  );
  const [actionError, setActionError] = useState<string | null>(null);
  // The invitation link, held only until the admin has copied it: the API
  // returns it once and cannot be asked for it again.
  const [invitation, setInvitation] = useState<IssuedInvitation | null>(null);
  const [copied, setCopied] = useState(false);
  // Shown once, same as an invitation: the API cannot read it back.
  const [credentials, setCredentials] = useState<MemberPassword | null>(null);

  async function setPassword(membershipId: string) {
    setActionError(null);
    setInvitation(null);
    setCredentials(null);
    try {
      setCredentials(
        await apiFetch<MemberPassword>(
          `/platform/v1/tenants/${tenantId}/memberships/${membershipId}/password`,
          { method: "POST", body: {} }
        )
      );
      reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  async function invite(membershipId: string) {
    setActionError(null);
    setInvitation(null);
    setCopied(false);
    try {
      setInvitation(
        await apiFetch<IssuedInvitation>(
          `/platform/v1/tenants/${tenantId}/memberships/${membershipId}/invitation`,
          { method: "POST", body: { send_email: true } }
        )
      );
      reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  async function setActive(membershipId: string, active: boolean) {
    const reason = active ? null : window.prompt("Reason for suspending this person's access:");
    if (!active && !reason) return;
    try {
      await apiFetch(`/platform/v1/tenants/${tenantId}/memberships/${membershipId}/status`, {
        method: "POST",
        body: { active, reason },
      });
      reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  if (loading) return <Loading what="access" />;

  return (
    <div>
      {(error || actionError) && <div className="error-banner">{error || actionError}</div>}
      {credentials && (
        <div className="licence-pack">
          <div className="pack-head">
            <div>
              <div className="k">Password set</div>
              <h3>Read these to {credentials.display_name}</h3>
            </div>
          </div>
          <p className="chart-note" style={{ marginTop: 0 }}>
            They sign in on the tablet with these. Shown only now — set another if it is lost. Ask
            them to change it from Settings once they are in; until they do, this screen keeps
            showing that somebody else chose it.
          </p>
          <div className="copyline" style={{ marginBottom: 8 }}>
            <code className="licence-key">{credentials.email}</code>
          </div>
          <div className="copyline">
            <code className="licence-key">{credentials.password}</code>
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => navigator.clipboard?.writeText(credentials.password)}
            >
              Copy
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setCredentials(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      {invitation && (
        <div className="panel" style={{ borderColor: "var(--farmos-olive)" }}>
          <div className="chart-title" style={{ marginBottom: 4 }}>
            {invitation.delivery === "email"
              ? `Invitation emailed to ${invitation.email}`
              : `Send this link to ${invitation.email}`}
          </div>
          <div className="chart-note">
            {invitation.delivery_detail}{" "}
            {invitation.delivery !== "email" &&
              "Copy the link and send it to them yourself — by WhatsApp, SMS, or your own email."}{" "}
            It works once, expires on {formatDate(invitation.expires_at)}, and cannot be shown
            again — issue a new one if it is lost.
          </div>
          <div style={{ display: "flex", gap: 8, marginTop: 12, alignItems: "center" }}>
            <input readOnly value={invitation.url} style={{ flex: 1, fontSize: "0.8rem" }} />
            <button
              className="btn btn-secondary btn-sm"
              onClick={() => {
                navigator.clipboard?.writeText(invitation.url);
                setCopied(true);
              }}
            >
              {copied ? "Copied" : "Copy link"}
            </button>
            <button className="btn btn-secondary btn-sm" onClick={() => setInvitation(null)}>
              Done
            </button>
          </div>
        </div>
      )}

      <div className="panel">
        <div className="chart-note" style={{ marginBottom: 12 }}>
          Everyone who can reach this tenant&apos;s farm data. A person with no password cannot sign
          in anywhere yet — either set one for them and read it out, or send a link and let them
          choose their own. Suspending keeps the record: their name stays attached to everything
          they entered.
        </div>
        {data && data.length === 0 ? (
          <div className="empty-note">Nobody has been given access yet.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Person</th>
                  <th>Platform role</th>
                  <th>Job role</th>
                  <th>Status</th>
                  <th>Sign-in</th>
                  <th></th>
                </tr>
              </thead>
              <tbody>
                {data?.map((member) => (
                  <tr key={member.id}>
                    <td>
                      {member.display_name}
                      <div style={{ color: "var(--farmos-muted)", fontSize: "0.75rem" }}>
                        {member.email}
                      </div>
                    </td>
                    <td>{member.tenant_role.replace(/_/g, " ").toLowerCase()}</td>
                    <td>{member.role}</td>
                    <td>
                      <StatusChip status={member.status} />
                    </td>
                    <td style={{ fontSize: "0.78rem" }}>
                      {member.has_password ? (
                        <span style={{ color: "var(--farmos-muted)" }}>Can sign in</span>
                      ) : (
                        <span style={{ color: "var(--farmos-warning)" }}>
                          Cannot sign in yet
                        </span>
                      )}
                    </td>
                    <td>
                      <div className="inline-actions">
                      {member.status === "ACTIVE" && (
                        <>
                          <button
                            className={`btn btn-sm ${
                              member.has_password ? "btn-secondary" : "btn-primary"
                            }`}
                            onClick={() => setPassword(member.id)}
                          >
                            Set a password
                          </button>
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => invite(member.id)}
                          >
                            {member.has_password ? "Resend link" : "Send link"}
                          </button>
                        </>
                      )}
                      {member.status === "ACTIVE" ? (
                        <button
                          className="btn btn-danger btn-sm"
                          onClick={() => setActive(member.id, false)}
                        >
                          Suspend
                        </button>
                      ) : (
                        <button
                          className="btn btn-secondary btn-sm"
                          onClick={() => setActive(member.id, true)}
                        >
                          Restore
                        </button>
                      )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}

function HandoverTab({ tenantId }: { tenantId: string }) {
  // The pack is returned once and cannot be read back, so it is held here
  // until the admin has copied it or sent it on.
  const [pack, setPack] = useState<LicencePack | null>(null);
  const [issuing, setIssuing] = useState(false);
  const [issueError, setIssueError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  async function issueHandover(credential: "link" | "password") {
    setIssuing(true);
    setIssueError(null);
    setPack(null);
    setCopied(null);
    try {
      setPack(
        await apiFetch<LicencePack>(`/platform/v1/tenants/${tenantId}/licence`, {
          method: "POST",
          body: { send_email: true, credential },
        })
      );
    } catch (err) {
      setIssueError(describeError(err));
    } finally {
      setIssuing(false);
    }
  }

  function copy(what: string, value: string) {
    navigator.clipboard?.writeText(value);
    setCopied(what);
  }

  return (
    <div>
      {issueError && <div className="error-banner">{issueError}</div>}

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          Hand this customer their way in
        </div>
        <div className="chart-note">
          One thing to pass on, not two: this gives the owner a way to sign in on any tablet, and
          the app opens every module from their first sign-in. There is no pairing key any more.
          Emailed when a mail server is configured, and shown here either way. Issuing again
          replaces whatever is outstanding.
        </div>
        <div className="inline-actions" style={{ marginTop: 14 }}>
          <button
            className="btn btn-primary"
            onClick={() => issueHandover("password")}
            disabled={issuing}
          >
            {issuing ? "Issuing…" : "Hand over a password"}
          </button>
          <button
            className="btn btn-secondary"
            onClick={() => issueHandover("link")}
            disabled={issuing}
          >
            Hand over a sign-in link
          </button>
        </div>
        <div className="chart-note" style={{ marginTop: 10, marginBottom: 0 }}>
          <strong>A password</strong> is the one to use when you have no mail server: you read the
          email address and password to the customer and they are in.{" "}
          <strong>A link</strong> lets them choose their own password, but somebody has to receive
          it.
        </div>
      </div>

      {pack && (
        <div className="licence-pack">
          <div className="pack-head">
            <div>
              <div className="k">Handover issued</div>
              <h3>{pack.display_name}</h3>
            </div>
            <span className="chip chip-active">
              {pack.delivery === "email" ? `Emailed to ${pack.owner_email}` : "Not emailed"}
            </span>
          </div>

          <p className="chart-note" style={{ marginTop: 0 }}>
            {pack.delivery_detail}{" "}
            {pack.delivery !== "email" && "Send the following to the customer yourself."}{" "}
            It cannot be shown again — issue another if it is lost.
          </p>

          <div className="pack-row">
            <div className="n">1</div>
            <div>
              <div className="t">Sign in — for {pack.owner_name}</div>
              {pack.owner_password ? (
                <>
                  <div className="d">
                    Read these two to the customer. They sign in with them on the tablet, and
                    should change the password from Settings once they are in.
                  </div>
                  <div className="copyline" style={{ marginBottom: 8 }}>
                    <code className="licence-key">{pack.owner_email}</code>
                  </div>
                  <div className="copyline">
                    <code className="licence-key">{pack.owner_password}</code>
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => copy("password", pack.owner_password ?? "")}
                    >
                      {copied === "password" ? "Copied" : "Copy"}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="d">
                    {pack.owner_email} opens this and chooses their own password. Works once,
                    expires {formatDate(pack.activation_expires_at)}.
                  </div>
                  <div className="copyline">
                    <input readOnly value={pack.activation_url ?? ""} />
                    <button
                      className="btn btn-secondary btn-sm"
                      onClick={() => copy("link", pack.activation_url ?? "")}
                    >
                      {copied === "link" ? "Copied" : "Copy"}
                    </button>
                  </div>
                </>
              )}
            </div>
          </div>

          <div className="pack-row">
            <div className="n">2</div>
            <div>
              <div className="t">Open the app on their tablet</div>
              <div className="d">
                Nothing to type in beyond the sign-in above. The tablet puts itself on this
                customer&rsquo;s Devices tab the first time it is used.
              </div>
            </div>
          </div>

          <div className="pack-foot">
            <span>
              <strong>Subscription:</strong> {pack.plan_name ?? "not recorded yet"}
            </span>
            <span>
              <strong>Opens:</strong> every module
            </span>
          </div>
        </div>
      )}
    </div>
  );
}

const SUBSCRIPTION_STATES = [
  { value: "ONBOARDING_TRIAL", label: "Trial — not paying yet" },
  { value: "ACTIVE", label: "Active — paying" },
  { value: "GRACE", label: "Past due — chasing payment" },
  { value: "SUSPENDED", label: "Suspended" },
  { value: "TERMINATED", label: "Terminated" },
];

function SubscriptionTab({ tenantId }: { tenantId: string }) {
  const subscription = useResource<Subscription | null>(
    `/platform/v1/tenants/${tenantId}/subscription`
  );
  const plans = useResource<Plan[]>("/platform/v1/plans");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  // What the last save actually did to their modules. Kept rather than
  // folded into the notice text because "these five modules are now on"
  // is the answer to the question the operator is really asking.
  const [result, setResult] = useState<SubscriptionSaveResult | null>(null);
  const [form, setForm] = useState({
    billing_cycle: "MONTHLY",
    status: "ONBOARDING_TRIAL",
    starts_at: "",
    renews_at: "",
  });

  // Populate the form from whatever is already recorded, once it arrives.
  useEffect(() => {
    const current = subscription.data;
    if (!current) return;
    setForm({
      billing_cycle: current.billing_cycle,
      status: current.status,
      starts_at: current.starts_at.slice(0, 10),
      renews_at: current.renews_at ? current.renews_at.slice(0, 10) : "",
    });
  }, [subscription.data]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    setError(null);
    setNotice(null);
    try {
      const result = await apiFetch<SubscriptionSaveResult>(
        `/platform/v1/tenants/${tenantId}/subscription`,
        {
          method: "PATCH",
          body: {
            billing_cycle: form.billing_cycle,
            status: form.status,
            starts_at: new Date(form.starts_at || Date.now()).toISOString(),
            renews_at: form.renews_at ? new Date(form.renews_at).toISOString() : null,
          },
        }
      );
      setResult(result);
      setNotice("Subscription saved. It is counted on the Business screen from now on.");
      await subscription.reload();
    } catch (err) {
      setError(describeError(err));
    }
  }

  if (subscription.loading || plans.loading) return <Loading what="the subscription" />;

  // There is one subscription and the server picks it; the console shows
  // what it costs rather than asking which one this customer is on.
  const plan = plans.data?.[0];
  const priceForCycle =
    plan && (form.billing_cycle === "ANNUAL" ? plan.annual_price_cents : plan.monthly_price_cents);

  return (
    <div>
      {(error || subscription.error) && (
        <div className="error-banner">{error || subscription.error}</div>
      )}
      {notice && <div className="notice-banner">{notice}</div>}

      {!subscription.data && (
        <div className="notice-banner">
          This customer has no subscription recorded, so they contribute nothing to revenue and do
          not appear on the Business screen. Set one below.
        </div>
      )}

      <div className="panel" style={{ maxWidth: 560 }}>
        <div className="chart-title" style={{ marginBottom: 4 }}>
          What this customer pays
        </div>
        <div className="chart-note">
          Recording this is what puts the customer into the revenue figures. It does not decide
          what they may open: every customer has every module from their first sign-in.
        </div>

        <form onSubmit={save} style={{ marginTop: 14 }}>
          <div className="field-row">
            <label htmlFor="sub-cycle">Billing cycle</label>
            <select
              id="sub-cycle"
              value={form.billing_cycle}
              onChange={(e) => setForm({ ...form, billing_cycle: e.target.value })}
            >
              <option value="MONTHLY">Monthly</option>
              <option value="ANNUAL">Annual</option>
            </select>
          </div>

          {plan && (
            <p className="chart-note" style={{ marginTop: -6, marginBottom: 14 }}>
              {priceForCycle === null || priceForCycle === undefined ? (
                <>
                  <strong>
                    Origami has no {form.billing_cycle.toLowerCase()} price set, so this customer
                    will be reported as unpriced rather than counted in revenue.
                  </strong>{" "}
                  <Link href="/catalog" style={{ fontWeight: 600 }}>
                    Set it under Subscription
                  </Link>
                  .
                </>
              ) : (
                <>Charged at {formatPrice(priceForCycle, plan.currency)} per{" "}
                {form.billing_cycle === "ANNUAL" ? "year" : "month"}.</>
              )}
            </p>
          )}

          <div className="field-row">
            <label htmlFor="sub-status">Status</label>
            <select
              id="sub-status"
              value={form.status}
              onChange={(e) => setForm({ ...form, status: e.target.value })}
            >
              {SUBSCRIPTION_STATES.map((state) => (
                <option key={state.value} value={state.value}>
                  {state.label}
                </option>
              ))}
            </select>
          </div>

          <div className="field-row">
            <label htmlFor="sub-starts">Starts on</label>
            <input
              id="sub-starts"
              type="date"
              required
              value={form.starts_at}
              onChange={(e) => setForm({ ...form, starts_at: e.target.value })}
            />
          </div>

          <div className="field-row">
            <label htmlFor="sub-renews">Renews on (optional)</label>
            <input
              id="sub-renews"
              type="date"
              value={form.renews_at}
              onChange={(e) => setForm({ ...form, renews_at: e.target.value })}
            />
          </div>

          <button className="btn btn-primary" type="submit">
            {subscription.data ? "Update subscription" : "Record subscription"}
          </button>
        </form>
      </div>

      {result && (
        <div className="panel" style={{ maxWidth: 560 }}>
          <div className="chart-title" style={{ marginBottom: 10 }}>
            What saving that changed
          </div>
          <p style={{ margin: 0, fontSize: "0.88rem", lineHeight: 1.7 }}>
            Recorded on {result.plan_code}. Nothing was switched on or off — this customer could
            already open every module, which is what the one subscription buys.
          </p>
        </div>
      )}
    </div>
  );
}
