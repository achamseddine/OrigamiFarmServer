"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { ErrorBanner, Loading, PageHeader, describeError, formatPrice, useResource } from "@/lib/ui";
import { Licence, ModuleCatalogItem, Plan } from "@/lib/types";

/** Subscription & modules — what Origami costs, and what it contains.
 *
 * This screen has been two different things. It began as two tables with
 * nothing said about how they related, because nothing did. Then it became
 * a plan builder: tick the licences each tier includes, and putting a
 * customer on one granted exactly those.
 *
 * Neither is true now. Origami is one subscription at one price covering
 * every module, so the only decision left on this screen is the price —
 * and the module list below it is a description of the product rather
 * than a set of switches. Keeping the tick boxes for a choice nobody can
 * make any more would have been the worst of the three.
 */

export default function CatalogPage() {
  const plans = useResource<Plan[]>("/platform/v1/plans");
  const modules = useResource<ModuleCatalogItem[]>("/platform/v1/modules");
  // The product's own sections, grouped server-side from each module's
  // license_code. They no longer gate anything — they are how "Milk
  // Production" is known to belong with MILK.
  const licences = useResource<Licence[]>("/platform/v1/licences");
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [form, setForm] = useState<{ monthly: string; annual: string; currency: string } | null>(
    null
  );

  const plan = plans.data?.[0] ?? null;

  // Prices are entered in whole currency units and stored in cents, so no
  // figure is ever held as a float.
  const toCents = (value: string): number | null =>
    value.trim() === "" ? null : Math.round(Number(value) * 100);
  const toUnits = (cents: number | null): string => (cents === null ? "" : String(cents / 100));

  function startEditing(current: Plan) {
    setNotice(null);
    setActionError(null);
    setForm({
      monthly: toUnits(current.monthly_price_cents),
      annual: toUnits(current.annual_price_cents),
      currency: current.currency,
    });
  }

  async function savePrice(e: React.FormEvent) {
    e.preventDefault();
    if (!plan || !form) return;
    setSaving(true);
    setActionError(null);
    setNotice(null);
    try {
      await apiFetch(`/platform/v1/plans/${plan.id}`, {
        method: "PATCH",
        body: {
          currency: form.currency,
          monthly_price_cents: toCents(form.monthly),
          annual_price_cents: toCents(form.annual),
        },
      });
      setNotice(
        "Saved. This is what every customer pays from now on — what they were already " +
          "charged does not change until you update their subscription."
      );
      setForm(null);
      await plans.reload();
    } catch (err) {
      setActionError(describeError(err));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div>
      <PageHeader
        icon="cart"
        title="Subscription &amp; modules"
        subtitle="One price for the whole product. This is what Origami costs — nothing here is about one particular customer."
      />
      <ErrorBanner message={plans.error || modules.error || actionError} />
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          How it fits together
        </div>
        <div className="pieces" style={{ marginTop: 12 }}>
          <div className="piece">
            <div className="w">One subscription covers everything</div>
            <p>
              Every module in the tablet app is included. There is nothing to choose between and
              no add-on to sell separately.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece you">
            <div className="w">You set the monthly price</div>
            <p>Below. An unpriced subscription is excluded from revenue rather than counted as free.</p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece">
            <div className="w">A customer is subscribed</div>
            <p>
              On their Subscription tab. That records what they pay and when it renews; their
              tablets already open every screen.
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          Price
        </div>
        <div className="chart-note">
          A subscription with no price cannot be counted as revenue: the business dashboard reports
          those customers separately rather than treating them as free.
        </div>

        {plans.loading && <Loading what="the subscription" />}
        {plan && (
          <>
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Subscription</th>
                    <th>Monthly</th>
                    <th>Annual</th>
                    <th>Includes</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  <tr>
                    <td>
                      <div style={{ fontWeight: 600 }}>{plan.name}</div>
                      <code style={{ fontSize: "0.75rem", color: "var(--farmos-muted)" }}>
                        {plan.code}
                      </code>
                    </td>
                    <td>{formatPrice(plan.monthly_price_cents, plan.currency)}</td>
                    <td>{formatPrice(plan.annual_price_cents, plan.currency)}</td>
                    <td style={{ fontSize: "0.82rem" }}>
                      Every module — {modules.data?.length ?? 0} screens in the app
                    </td>
                    <td>
                      <button
                        className={`btn btn-sm ${
                          plan.monthly_price_cents === null ? "btn-primary" : "btn-secondary"
                        }`}
                        onClick={() => startEditing(plan)}
                      >
                        {plan.monthly_price_cents === null ? "Set the price" : "Change the price"}
                      </button>
                    </td>
                  </tr>
                </tbody>
              </table>
            </div>

            {form && (
              <form onSubmit={savePrice} style={{ marginTop: 20, maxWidth: 420 }}>
                <div className="chart-title" style={{ marginBottom: 10 }}>
                  What {plan.name} costs
                </div>
                <div className="field-row">
                  <label htmlFor="plan-currency">Currency</label>
                  <input
                    id="plan-currency"
                    required
                    maxLength={3}
                    value={form.currency}
                    onChange={(e) => setForm({ ...form, currency: e.target.value.toUpperCase() })}
                  />
                </div>
                <div className="field-row">
                  <label htmlFor="plan-monthly">Monthly price (leave blank if not priced yet)</label>
                  <input
                    id="plan-monthly"
                    type="number"
                    min="0"
                    step="0.01"
                    placeholder="249"
                    value={form.monthly}
                    onChange={(e) => setForm({ ...form, monthly: e.target.value })}
                  />
                </div>
                <div className="field-row">
                  <label htmlFor="plan-annual">Annual price (leave blank if not offered)</label>
                  <input
                    id="plan-annual"
                    type="number"
                    min="0"
                    step="0.01"
                    value={form.annual}
                    onChange={(e) => setForm({ ...form, annual: e.target.value })}
                  />
                </div>
                <div className="inline-actions">
                  <button className="btn btn-primary" type="submit" disabled={saving}>
                    {saving ? "Saving…" : "Save price"}
                  </button>
                  <button
                    className="btn btn-secondary"
                    type="button"
                    onClick={() => setForm(null)}
                    disabled={saving}
                  >
                    Cancel
                  </button>
                </div>
              </form>
            )}
          </>
        )}
      </div>

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          What is included
        </div>
        <div className="chart-note">
          Every screen the tablet app has, grouped by the part of the product it belongs to. All of
          it is included for every customer — this list moves when the app gains a capability, not
          when you change what you charge.
        </div>
        {modules.loading && <Loading what="modules" />}
        {licences.data && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Module</th>
                  <th>Name</th>
                  <th>Part of</th>
                </tr>
              </thead>
              <tbody>
                {licences.data.flatMap((licence) =>
                  licence.unlocks.map((moduleCode, index) => (
                    <tr key={moduleCode}>
                      <td>
                        <code>{moduleCode}</code>
                      </td>
                      <td>{licence.unlocks_labels[index]}</td>
                      <td>
                        <code style={{ fontSize: "0.78rem" }}>{licence.license_code}</code>
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </div>
  );
}
