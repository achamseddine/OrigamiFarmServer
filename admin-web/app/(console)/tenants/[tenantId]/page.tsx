"use client";

import { useEffect, useState, useCallback } from "react";
import { useParams } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import {
  AuditEventItem,
  DeviceItem,
  Entitlement,
  Farm,
  ModuleCatalogItem,
  Tenant,
} from "@/lib/types";
import { StatusChip } from "@/components/StatusChip";

const TABS = ["Overview", "Farms", "Modules", "Devices", "Audit"] as const;
type Tab = (typeof TABS)[number];

export default function TenantDetailPage() {
  const params = useParams<{ tenantId: string }>();
  const tenantId = params.tenantId;

  const [tab, setTab] = useState<Tab>("Overview");
  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  const loadTenant = useCallback(() => {
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
