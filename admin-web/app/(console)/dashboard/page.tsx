"use client";

import { StatusChip } from "@/components/StatusChip";
import {
  ErrorBanner,
  Loading,
  PageHeader,
  StatTile,
  TimeBars,
  formatDate,
  useResource,
} from "@/lib/ui";
import { MetricsOverview } from "@/lib/types";

export default function DashboardPage() {
  const { data, error, loading } = useResource<MetricsOverview>("/platform/v1/metrics/overview");

  return (
    <div>
      <PageHeader
        title="Platform Overview"
        subtitle="Live state of the control plane — every figure is counted from the database at load."
      />
      <ErrorBanner message={error} />
      {loading && <Loading what="the overview" />}

      {data && (
        <>
          <div className="card-grid">
            <StatTile value={data.tenants_total} label="Tenants" href="/tenants" />
            <StatTile value={data.devices_total} label="Registered devices" />
            <StatTile value={data.leases_active} label="Active license leases" href="/licensing" />
            <StatTile value={data.user_count} label="User accounts" />
            <StatTile value={data.staff_count} label="Platform staff" href="/staff" />
            <StatTile value={data.renewals_due_30d} label="Renewals due (30d)" />
          </div>

          <div className="panel">
            <TimeBars
              title="Audit events per day"
              note="Everything the platform recorded over the last two weeks — a flat stretch means nothing happened, not that nothing was captured."
              unit="events"
              points={data.audit_events_per_day.map((point) => ({
                label: point.day,
                value: point.events,
                detail: formatDate(point.day),
              }))}
            />
          </div>

          <div className="panel">
            <div className="chart-title" style={{ marginBottom: 12 }}>
              Tenants by status
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th className="num">Tenants</th>
                    <th>What it means</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.tenants_by_status)
                    .filter(([, count]) => count > 0)
                    .map(([status, count]) => (
                      <tr key={status}>
                        <td>
                          <StatusChip status={status} />
                        </td>
                        <td className="num">{count}</td>
                        <td style={{ color: "var(--farmos-muted)" }}>{STATUS_MEANING[status]}</td>
                      </tr>
                    ))}
                </tbody>
              </table>
            </div>
          </div>

          <div className="panel">
            <div className="chart-title" style={{ marginBottom: 12 }}>
              Devices by status
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Status</th>
                    <th className="num">Devices</th>
                  </tr>
                </thead>
                <tbody>
                  {Object.entries(data.devices_by_status).filter(([, c]) => c > 0).length === 0 ? (
                    <tr>
                      <td colSpan={2} className="empty-note">
                        No devices have been activated yet.
                      </td>
                    </tr>
                  ) : (
                    Object.entries(data.devices_by_status)
                      .filter(([, count]) => count > 0)
                      .map(([status, count]) => (
                        <tr key={status}>
                          <td>
                            <StatusChip status={status} />
                          </td>
                          <td className="num">{count}</td>
                        </tr>
                      ))
                  )}
                </tbody>
              </table>
            </div>
          </div>

          {data.tenants_created_per_month.length > 0 && (
            <div className="panel">
              <TimeBars
                title="Tenants onboarded per month"
                unit="tenants"
                points={data.tenants_created_per_month.map((point) => ({
                  label: point.month,
                  value: point.tenants,
                }))}
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}

const STATUS_MEANING: Record<string, string> = {
  ONBOARDING: "Being set up — can read and write",
  TRIAL: "Evaluating — full access until the trial ends",
  ACTIVE: "Paying and in good standing",
  GRACE: "Past due — still writable while it is chased",
  SUSPENDED: "Write access withdrawn; data still readable and exportable",
  TERMINATED: "Closed — no access",
};
