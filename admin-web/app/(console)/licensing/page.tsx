"use client";

import Link from "next/link";
import {
  ErrorBanner,
  Loading,
  PageHeader,
  RankedBars,
  StatTile,
  daysUntil,
  formatDateTime,
  useResource,
} from "@/lib/ui";
import { MetricsLicensing, MetricsOverview } from "@/lib/types";

export default function LicensingPage() {
  const licensing = useResource<MetricsLicensing>("/platform/v1/metrics/licensing");
  const overview = useResource<MetricsOverview>("/platform/v1/metrics/overview");

  const data = licensing.data;

  return (
    <div>
      <PageHeader
        title="Licensing"
        subtitle="Which modules are licensed to whom, and which devices can still work offline."
      />
      <ErrorBanner message={licensing.error || overview.error} />
      {licensing.loading && <Loading what="licensing" />}

      {overview.data && (
        <div className="card-grid">
          <StatTile value={overview.data.leases_active} label="Active leases" />
          <StatTile value={overview.data.leases_expiring_7d} label="Leases expiring (7d)" />
          <StatTile value={overview.data.devices_total} label="Registered devices" />
          <StatTile value={overview.data.renewals_due_30d} label="Renewals due (30d)" />
        </div>
      )}

      {data && (
        <>
          <div className="panel">
            <RankedBars
              title="Tenants licensed per module"
              note={`Active or trial entitlements, out of ${data.tenants_total} tenants.`}
              unit="tenants"
              limit={15}
              points={data.modules.map((module) => ({
                label: module.name,
                value: module.active + module.trial,
              }))}
            />
          </div>

          <div className="panel">
            <div className="chart-title" style={{ marginBottom: 12 }}>
              Module catalog
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Module</th>
                    <th>Code</th>
                    <th className="num">Active</th>
                    <th className="num">Trial</th>
                    <th className="num">Inactive</th>
                    <th>Paid add-on</th>
                  </tr>
                </thead>
                <tbody>
                  {data.modules.map((module) => (
                    <tr key={module.module_code}>
                      <td>{module.name}</td>
                      <td>
                        <code>{module.module_code}</code>
                      </td>
                      <td className="num">{module.active}</td>
                      <td className="num">{module.trial}</td>
                      <td className="num">{module.other}</td>
                      <td>{module.license_code ? <code>{module.license_code}</code> : "—"}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="chart-title" style={{ marginBottom: 4 }}>
              Leases expiring within 7 days
            </div>
            <div className="chart-note">
              A lease already issued cannot be recalled — a device keeps working offline until its
              own expiry, then has to reach the server for a new one.
            </div>
            {data.leases_expiring_soon.length === 0 ? (
              <div className="empty-note">Nothing expiring in the next week.</div>
            ) : (
              <div className="table-scroll">
                <table className="data-table">
                  <thead>
                    <tr>
                      <th>Tenant</th>
                      <th>Device</th>
                      <th>Expires</th>
                      <th className="num">In</th>
                      <th>Modules</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.leases_expiring_soon.map((lease) => (
                      <tr key={lease.lease_id}>
                        <td>
                          <Link href={`/tenants/detail/?id=${lease.tenant_id}`}>
                            {lease.tenant_name}
                          </Link>
                        </td>
                        <td>{lease.device_name ?? "—"}</td>
                        <td>{formatDateTime(lease.expires_at)}</td>
                        <td className="num">{daysUntil(lease.expires_at)}d</td>
                        <td style={{ color: "var(--farmos-muted)", fontSize: "0.78rem" }}>
                          {lease.modules.join(", ") || "—"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
