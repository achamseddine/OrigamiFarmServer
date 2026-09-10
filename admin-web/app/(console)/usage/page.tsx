"use client";

import { useRouter } from "next/navigation";
import { StatusChip } from "@/components/StatusChip";
import { ErrorBanner, Loading, PageHeader, RankedBars, formatDateTime, useResource } from "@/lib/ui";
import { UsageList } from "@/lib/types";

export default function UsagePage() {
  const router = useRouter();
  const { data, error, loading } = useResource<UsageList>("/platform/v1/metrics/usage");

  return (
    <div>
      <PageHeader
        title="Usage"
        subtitle="What each farm actually holds — counted from their own records, not estimated."
      />
      <ErrorBanner message={error} />
      {loading && <Loading what="usage across tenants" />}

      {data && (
        <>
          {data.tenants_measured < data.tenants_total && (
            <div className="notice-banner">
              Showing the {data.tenants_measured} most recent tenants of {data.tenants_total}. Each
              one is counted in its own isolated session, so this view is capped rather than slow.
            </div>
          )}

          <div className="panel">
            <RankedBars
              title="Records held, by tenant"
              note="Live rows across every farm-data module, excluding deleted ones."
              unit="records"
              points={data.items.map((tenant) => ({
                label: tenant.display_name,
                value: tenant.total_records,
              }))}
            />
          </div>

          <div className="panel">
            <div className="chart-title" style={{ marginBottom: 12 }}>
              Per-tenant detail
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Tenant</th>
                    <th>Status</th>
                    <th className="num">Records</th>
                    <th className="num">Modules with data</th>
                    <th className="num">Entitlements</th>
                    <th className="num">Users</th>
                    <th className="num">Devices</th>
                    <th>Last activity</th>
                  </tr>
                </thead>
                <tbody>
                  {data.items.map((tenant) => (
                    <tr
                      key={tenant.tenant_id}
                      className="row-link"
                      onClick={() => router.push(`/tenants/detail/?id=${tenant.tenant_id}`)}
                    >
                      <td>
                        {tenant.display_name}
                        <div style={{ color: "var(--farmos-muted)", fontSize: "0.75rem" }}>
                          {tenant.company_code}
                        </div>
                      </td>
                      <td>
                        <StatusChip status={tenant.status} />
                      </td>
                      <td className="num">{tenant.total_records}</td>
                      <td className="num">{tenant.modules_with_data.length}</td>
                      <td className="num">{tenant.modules_entitled.length}</td>
                      <td className="num">{tenant.active_users}</td>
                      <td className="num">{tenant.active_devices}</td>
                      <td>{formatDateTime(tenant.last_activity_at)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <p className="chart-note" style={{ marginTop: 12, marginBottom: 0 }}>
              &ldquo;Modules with data&rdquo; counts farm-data modules holding at least one live
              record. &ldquo;Entitlements&rdquo; counts what the tenant is licensed for. The two are
              recorded in different vocabularies — the platform&apos;s module codes and the FarmOS
              tablet contract&apos;s permission modules — so read them as two separate facts, not as
              a ratio.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
