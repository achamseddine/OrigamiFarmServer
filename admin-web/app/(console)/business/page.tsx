"use client";

import Link from "next/link";
import {
  ErrorBanner,
  Loading,
  PageHeader,
  RankedBars,
  StatTile,
  TimeBars,
  formatMoney,
  formatPrice,
  useResource,
} from "@/lib/ui";
import { MetricsRevenue } from "@/lib/types";

export default function BusinessPage() {
  const { data, error, loading } = useResource<MetricsRevenue>("/platform/v1/metrics/revenue");

  return (
    <div>
      <PageHeader
        title="Business"
        subtitle="Contracted recurring revenue and the commercial pipeline behind it."
      />
      <ErrorBanner message={error} />
      {loading && <Loading what="revenue" />}

      {data && (
        <>
          <DataQualityNotices data={data} />

          <div className="card-grid">
            <StatTile value={formatMoney(data.mrr_cents, data.currency)} label="MRR (contracted)" />
            <StatTile value={formatMoney(data.arr_cents, data.currency)} label="ARR (MRR × 12)" />
            <StatTile value={formatMoney(data.arpa_cents, data.currency)} label="Average per account" />
            <StatTile value={data.paying_tenants} label="Paying tenants" />
          </div>

          <div className="card-grid">
            <StatTile value={data.trial_tenants} label="In trial / onboarding" />
            <StatTile
              value={formatMoney(data.at_risk_mrr_cents, data.currency)}
              label={`At risk — past due (${data.at_risk_tenants})`}
            />
            <StatTile
              value={formatMoney(data.renewals_due_30d_mrr_cents, data.currency)}
              label={`Up for renewal in 30d (${data.renewals_due_30d})`}
            />
            <StatTile value={data.lost_tenants} label="Suspended or terminated" />
          </div>

          <div className="panel">
            <RankedBars
              title="Recurring revenue by plan"
              note="Monthly, with annual contracts divided by twelve so both cycles sum into one figure."
              unit={data.currency}
              points={data.by_plan.map((plan) => ({
                label: plan.plan_name,
                value: Math.round(plan.mrr_cents / 100),
              }))}
            />
          </div>

          <div className="panel">
            <div className="chart-title" style={{ marginBottom: 12 }}>
              Plans
            </div>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Plan</th>
                    <th>Monthly</th>
                    <th>Annual</th>
                    <th className="num">Subscriptions</th>
                    <th className="num">MRR</th>
                    <th className="num">Unpriced</th>
                  </tr>
                </thead>
                <tbody>
                  {data.by_plan.length === 0 ? (
                    <tr>
                      <td colSpan={6} className="empty-note">
                        No tenant has a subscription yet, so there is nothing to bill for.
                      </td>
                    </tr>
                  ) : (
                    data.by_plan.map((plan) => (
                      <tr key={plan.plan_code}>
                        <td>
                          {plan.plan_name}
                          <div style={{ color: "var(--farmos-muted)", fontSize: "0.75rem" }}>
                            {plan.plan_code}
                          </div>
                        </td>
                        <td>{formatPrice(plan.monthly_price_cents, plan.currency)}</td>
                        <td>{formatPrice(plan.annual_price_cents, plan.currency)}</td>
                        <td className="num">{plan.subscriptions}</td>
                        <td className="num">{formatMoney(plan.mrr_cents, plan.currency)}</td>
                        <td className="num">
                          {plan.unpriced_subscriptions > 0 ? plan.unpriced_subscriptions : "—"}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
            <p className="chart-note" style={{ marginTop: 12, marginBottom: 0 }}>
              Prices are set under <Link href="/catalog">Plans &amp; modules</Link>.
            </p>
          </div>

          {data.tenants_created_per_month.length > 0 && (
            <div className="panel">
              <TimeBars
                title="Customers won per month"
                note="Tenants created, over the last twelve months."
                unit="tenants"
                points={data.tenants_created_per_month.map((point) => ({
                  label: point.month,
                  value: point.tenants,
                }))}
              />
            </div>
          )}

          <Invoicing data={data} />
        </>
      )}
    </div>
  );
}

function DataQualityNotices({ data }: { data: MetricsRevenue }) {
  const notices: string[] = [];

  if (data.unpriced_subscriptions > 0) {
    notices.push(
      `${data.unpriced_subscriptions} active ${
        data.unpriced_subscriptions === 1 ? "subscription is" : "subscriptions are"
      } on a plan with no price for its billing cycle. They are excluded from MRR rather than counted as zero, so the figures above understate the book until those plans are priced.`
    );
  }
  if (data.tenants_without_subscription > 0) {
    notices.push(
      `${data.tenants_without_subscription} ${
        data.tenants_without_subscription === 1 ? "tenant has" : "tenants have"
      } no subscription record at all — they contribute nothing to revenue and will not appear in any plan below.`
    );
  }
  if (data.currencies_present.length > 1) {
    notices.push(
      `Plans are priced in more than one currency (${data.currencies_present.join(
        ", "
      )}). Totals are shown in ${data.currency} and are not converted, so they cannot be read as a single figure.`
    );
  }

  if (notices.length === 0) return null;

  return (
    <div className="notice-banner">
      <strong>Before reading these numbers</strong>
      <ul style={{ margin: "8px 0 0", paddingLeft: 18 }}>
        {notices.map((notice) => (
          <li key={notice} style={{ marginBottom: 4 }}>
            {notice}
          </li>
        ))}
      </ul>
    </div>
  );
}

function Invoicing({ data }: { data: MetricsRevenue }) {
  const { invoicing, currency } = data;

  return (
    <div className="panel">
      <div className="chart-title" style={{ marginBottom: 4 }}>
        Billing and collections
      </div>
      {!invoicing.billing_configured ? (
        <>
          <div className="chart-note">
            Nothing has been invoiced through this system.
          </div>
          <p className="empty-note" style={{ paddingTop: 8 }}>
            The invoice ledger exists in the schema but nothing writes to it yet: there is no
            invoice generation and no payment provider connected. Everything above is{" "}
            <strong>contracted</strong> revenue — what customers have agreed to pay — not cash
            billed or received. Treat collections as unmeasured rather than as zero.
          </p>
        </>
      ) : (
        <div className="meta-grid" style={{ marginTop: 12 }}>
          <div>
            <div className="k">Billed (12m)</div>
            <div className="v">{formatMoney(invoicing.billed_cents, currency)}</div>
          </div>
          <div>
            <div className="k">Collected</div>
            <div className="v">{formatMoney(invoicing.collected_cents, currency)}</div>
          </div>
          <div>
            <div className="k">Outstanding</div>
            <div className="v">{formatMoney(invoicing.outstanding_cents, currency)}</div>
          </div>
          <div>
            <div className="k">Overdue</div>
            <div className="v">{formatMoney(invoicing.overdue_cents, currency)}</div>
          </div>
        </div>
      )}
    </div>
  );
}
