"use client";

import { Suspense, useEffect, useState, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import {
  AuditEventItem,
  DeviceItem,
  Entitlement,
  Farm,
  LicenseLease,
  Membership,
  Plan,
  Subscription,
  ModuleCatalogItem,
  Tenant,
  TenantUsage,
} from "@/lib/types";
import { StatusChip } from "@/components/StatusChip";
import {
  Loading,
  RankedBars,
  StatTile,
  daysUntil,
  describeError,
  formatDateTime,
  formatPrice,
  useResource,
} from "@/lib/ui";

const TABS = [
  "Overview",
  "Usage",
  "Subscription",
  "Access",
  "Farms",
  "Modules",
  "Devices",
  "Licensing",
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
      {tab === "Licensing" && <LicensingTab tenantId={tenantId} />}
      {tab === "Farms" && <FarmsTab tenantId={tenantId} />}
      {tab === "Modules" && <ModulesTab tenantId={tenantId} onChange={() => setNotice("Entitlements updated.")} />}
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

function ModulesTab({ tenantId, onChange }: { tenantId: string; onChange: () => void }) {
  const [entitlements, setEntitlements] = useState<Entitlement[] | null>(null);
  const [catalog, setCatalog] = useState<ModuleCatalogItem[]>([]);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch<Entitlement[]>(`/platform/v1/tenants/${tenantId}/entitlements`).then(setEntitlements);
    apiFetch<ModuleCatalogItem[]>("/platform/v1/modules").then(setCatalog);
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  async function activate(moduleCode: string) {
    const reason = window.prompt(`Reason for activating ${moduleCode}:`, "Customer purchased module");
    if (!reason) return;
    try {
      await apiFetch(`/platform/v1/tenants/${tenantId}/entitlements/${moduleCode}/activate`, {
        method: "POST",
        body: { reason },
      });
      load();
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed");
    }
  }

  async function deactivate(moduleCode: string) {
    const reason = window.prompt(`Reason for deactivating ${moduleCode}:`);
    if (!reason) return;
    try {
      await apiFetch(`/platform/v1/tenants/${tenantId}/entitlements/${moduleCode}/deactivate`, {
        method: "POST",
        body: { reason },
      });
      load();
      onChange();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed");
    }
  }

  const entitledCodes = new Set(entitlements?.map((e) => e.module_code));

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <thead><tr><th>Module</th><th>Status</th><th>Effective from</th><th>Action</th></tr></thead>
          <tbody>
            {entitlements?.map((e) => (
              <tr key={e.module_code}>
                <td>{e.module_code}</td>
                <td><StatusChip status={e.status} /></td>
                <td>{new Date(e.effective_from).toLocaleDateString()}</td>
                <td>
                  {e.status === "ACTIVE" || e.status === "TRIAL" ? (
                    <button className="btn btn-secondary" onClick={() => deactivate(e.module_code)}>Deactivate</button>
                  ) : (
                    <button className="btn btn-primary" onClick={() => activate(e.module_code)}>Activate</button>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <h3 style={{ fontSize: "1rem", color: "var(--farmos-muted)" }}>Available modules not yet entitled</h3>
      <div className="panel" style={{ padding: 0 }}>
        <table>
          <tbody>
            {catalog.filter((m) => !entitledCodes.has(m.module_code)).map((m) => (
              <tr key={m.module_code}>
                <td>{m.name_en} <code style={{ fontSize: "0.75rem" }}>{m.module_code}</code></td>
                <td style={{ textAlign: "right" }}>
                  <button className="btn btn-primary" onClick={() => activate(m.module_code)}>Activate</button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DevicesTab({ tenantId }: { tenantId: string }) {
  const [devices, setDevices] = useState<DeviceItem[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [lastCode, setLastCode] = useState<string | null>(null);

  const load = useCallback(() => {
    apiFetch<DeviceItem[]>(`/platform/v1/tenants/${tenantId}/devices`).then(setDevices);
  }, [tenantId]);

  useEffect(() => { load(); }, [load]);

  async function createActivation() {
    try {
      const result = await apiFetch<{ activation_code: string; expires_at: string }>(
        `/platform/v1/tenants/${tenantId}/device-activations`,
        { method: "POST", body: { ttl_hours: 24 } }
      );
      setLastCode(result.activation_code);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to create activation code");
    }
  }

  async function revoke(deviceId: string) {
    const reason = window.prompt("Reason for revoking this device:");
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
      <button className="btn btn-primary" onClick={createActivation} style={{ marginBottom: 16 }}>
        + Generate device activation code
      </button>
      {lastCode && (
        <div className="panel" style={{ background: "var(--farmos-mist)" }}>
          One-time activation code (shown only now): <code style={{ fontSize: "1.1rem" }}>{lastCode}</code>
        </div>
      )}
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
              <tr><td colSpan={5} style={{ textAlign: "center", padding: 24, color: "var(--farmos-muted)" }}>No devices registered yet.</td></tr>
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
            <div className="k">Entitlements held</div>
            <div className="v">{data.modules_entitled.join(", ") || "None"}</div>
          </div>
          <div>
            <div className="k">Farm-data modules in use</div>
            <div className="v">{data.modules_with_data.join(", ") || "None"}</div>
          </div>
        </div>
        <p className="chart-note" style={{ marginTop: 12, marginBottom: 0 }}>
          These two lists are counted in different vocabularies and are not a like-for-like
          comparison: entitlements are the platform&apos;s own module codes, while the modules in
          use are the FarmOS tablet contract&apos;s permission modules. Nothing in the schema maps
          one to the other, so the console does not guess at an adoption figure.
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
      <div className="panel">
        <div className="chart-note" style={{ marginBottom: 12 }}>
          Everyone who can reach this tenant&apos;s farm data. Suspending keeps the record — their
          name stays attached to everything they entered.
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
                    <td style={{ fontSize: "0.78rem", color: "var(--farmos-muted)" }}>
                      {member.has_password ? "Tablet password set" : "No password"}
                    </td>
                    <td>
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

function LicensingTab({ tenantId }: { tenantId: string }) {
  const { data, error, loading } = useResource<LicenseLease[]>(
    `/platform/v1/tenants/${tenantId}/leases`
  );

  if (loading) return <Loading what="leases" />;

  return (
    <div>
      {error && <div className="error-banner">{error}</div>}
      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          Offline license leases
        </div>
        <div className="chart-note">
          Each one lets a device keep working without a connection until it expires. A lease already
          issued cannot be recalled — revoking the device stops the next one, not this one.
        </div>
        {data && data.length === 0 ? (
          <div className="empty-note">No leases have been issued to this tenant.</div>
        ) : (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Device</th>
                  <th>Issued</th>
                  <th>Expires</th>
                  <th className="num">In</th>
                  <th>Modules</th>
                  <th>State</th>
                </tr>
              </thead>
              <tbody>
                {data?.map((lease) => {
                  const remaining = daysUntil(lease.expires_at);
                  return (
                    <tr key={lease.id}>
                      <td>{lease.device_name ?? "—"}</td>
                      <td>{formatDateTime(lease.issued_at)}</td>
                      <td>{formatDateTime(lease.expires_at)}</td>
                      <td className="num">{remaining >= 0 ? `${remaining}d` : "—"}</td>
                      <td style={{ fontSize: "0.78rem", color: "var(--farmos-muted)" }}>
                        {lease.modules.join(", ") || "—"}
                      </td>
                      <td>
                        <StatusChip
                          status={
                            lease.revoked_at ? "REVOKED" : remaining < 0 ? "TERMINATED" : "ACTIVE"
                          }
                        />
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}
      </div>
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
  const [form, setForm] = useState({
    plan_id: "",
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
      plan_id: current.plan_id,
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
      await apiFetch(`/platform/v1/tenants/${tenantId}/subscription`, {
        method: "PATCH",
        body: {
          plan_id: form.plan_id,
          billing_cycle: form.billing_cycle,
          status: form.status,
          starts_at: new Date(form.starts_at || Date.now()).toISOString(),
          renews_at: form.renews_at ? new Date(form.renews_at).toISOString() : null,
        },
      });
      setNotice("Subscription saved. It is counted on the Business screen from now on.");
      await subscription.reload();
    } catch (err) {
      setError(describeError(err));
    }
  }

  if (subscription.loading || plans.loading) return <Loading what="the subscription" />;

  const selectedPlan = plans.data?.find((plan) => plan.id === form.plan_id);
  const priceForCycle =
    selectedPlan &&
    (form.billing_cycle === "ANNUAL"
      ? selectedPlan.annual_price_cents
      : selectedPlan.monthly_price_cents);

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
          Recording this is what puts them into the revenue figures — it is separate from the
          modules they can use.
        </div>

        <form onSubmit={save} style={{ marginTop: 14 }}>
          <div className="field-row">
            <label htmlFor="sub-plan">Plan</label>
            <select
              id="sub-plan"
              required
              value={form.plan_id}
              onChange={(e) => setForm({ ...form, plan_id: e.target.value })}
            >
              <option value="">Choose a plan…</option>
              {plans.data?.map((plan) => (
                <option key={plan.id} value={plan.id}>
                  {plan.name} ({plan.code})
                </option>
              ))}
            </select>
          </div>

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

          {selectedPlan && (
            <p className="chart-note" style={{ marginTop: -6, marginBottom: 14 }}>
              {priceForCycle === null || priceForCycle === undefined ? (
                <strong>
                  This plan has no {form.billing_cycle.toLowerCase()} price set, so this customer
                  will be reported as unpriced rather than counted in revenue.
                </strong>
              ) : (
                <>Charged at {formatPrice(priceForCycle, selectedPlan.currency)} per{" "}
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
    </div>
  );
}
