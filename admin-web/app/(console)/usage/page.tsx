"use client";

import { useRouter } from "next/navigation";
import { StatusChip } from "@/components/StatusChip";
import { Icon } from "@/components/Icon";
import { ErrorBanner, Loading, PageHeader, PanelHead, RankedBars, formatDateTime, useResource } from "@/lib/ui";
import { UsageList } from "@/lib/types";

export default function UsagePage() {
  const router = useRouter();
  const { data, error, loading } = useResource<UsageList>("/platform/v1/metrics/usage");

  return (
    <div>
      <PageHeader
        icon="package"
        tone="purple"
        title="Usage"
        subtitle="What each farm actually holds — counted from their own records, not estimated."
      />
      <ErrorBanner message={error} />
      {loading && <Loading what="usage across tenants" />}

      {data && (
        <>
          {data.tenants_measured < data.tenants_total && (
            <div className="notice-banner">
              <Icon name="eye" size={18} />
              <div>
                Showing the {data.tenants_measured} most recent tenants of {data.tenants_total}.
                Each one is counted in its own isolated session, so this view is capped rather
                than slow.
              </div>
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
            <PanelHead
              icon="barn"
              title="Per-tenant detail"
              note="Select a row to open that tenant."
            />
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Tenant</th>
                    <th>Status</th>
                    <th className="num">Records</th>
                    <th className="num">Modules with data</th>
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
              record. It used to sit beside a count of what the tenant was licensed for; every
              customer now has every module, so that column compared a list with itself. A low
              number here means a farm is using a small part of what it pays for — which is worth
              a phone call, not a billing change.
            </p>
          </div>
        </>
      )}
    </div>
  );
}
