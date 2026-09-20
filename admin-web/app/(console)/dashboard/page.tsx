"use client";

import Link from "next/link";
import { StatusChip } from "@/components/StatusChip";
import { Icon } from "@/components/Icon";
import {
  ErrorBanner,
  Loading,
  PageHeader,
  PanelHead,
  StatTile,
  TimeBars,
  formatDate,
  useResource,
} from "@/lib/ui";
import { MetricsOverview, SetupState } from "@/lib/types";
import { SETUP_STEPS } from "@/lib/setup";

export default function DashboardPage() {
  const { data, error, loading } = useResource<MetricsOverview>("/platform/v1/metrics/overview");
  // Separate request on purpose: the overview is the screen people live on,
  // and an unfinished setup is worth saying here rather than only on a
  // screen they have no reason to open twice.
  const { data: setup } = useResource<SetupState>("/platform/v1/setup");
  const outstanding = (setup?.steps ?? []).filter((step) => !step.done);

  return (
    <div>
      <PageHeader
        icon="chart-line"
        title="Platform Overview"
        subtitle="Live state of the control plane — every figure is counted from the database at load."
        actions={
          <>
            <Link href="/tenants/new" className="btn btn-primary btn-fold">
              <Icon name="plus" size={20} />
              Create tenant
            </Link>
            <Link href="/audit" className="btn btn-secondary">
              <Icon name="report" size={20} />
              Audit log
            </Link>
          </>
        }
      />
      <ErrorBanner message={error} />

      {outstanding.length > 0 && (
        <div className="notice-banner">
          <Icon name="warning" size={18} />
          <div>
            {outstanding.length} setup {outstanding.length === 1 ? "step is" : "steps are"} still
            outstanding — next up, {SETUP_STEPS[outstanding[0].key].title.toLowerCase()}.{" "}
            <Link href="/guide" style={{ fontWeight: 600 }}>
              Open Getting started
            </Link>
          </div>
        </div>
      )}

      {loading && <Loading what="the overview" />}

      {data && (
        <>
          <div className="card-grid">
            <StatTile
              value={data.tenants_total}
              label="Tenants"
              icon="barn"
              href="/tenants"
              foot="Customer companies"
            />
            <StatTile
              value={data.devices_total}
              label="Registered devices"
              icon="qr"
              tone="sky"
              foot="Tablets signed in"
            />
            <StatTile
              value={data.user_count}
              label="User accounts"
              icon="people"
              tone="purple"
              foot="Across every farm"
            />
            <StatTile
              value={data.staff_count}
              label="Platform staff"
              icon="shield"
              href="/staff"
              foot="Can reach this console"
            />
            <StatTile
              value={data.renewals_due_30d}
              label="Renewals due (30d)"
              icon="calendar"
              tone={data.renewals_due_30d > 0 ? "gold" : "neutral"}
              foot="Next thirty days"
            />
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
            <PanelHead
              icon="barn"
              title="Tenants by status"
              note="Where every customer company stands today."
            />
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
            <PanelHead icon="qr" tone="sky" title="Devices by status" />
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
