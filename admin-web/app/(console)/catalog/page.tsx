"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { ErrorBanner, Loading, PageHeader, describeError, formatPrice, useResource } from "@/lib/ui";
import { ModuleCatalogItem, Plan } from "@/lib/types";

export default function CatalogPage() {
  const plans = useResource<Plan[]>("/platform/v1/plans");
  const modules = useResource<ModuleCatalogItem[]>("/platform/v1/modules");
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [planForm, setPlanForm] = useState({
    code: "",
    name: "",
    currency: "USD",
    monthly: "",
    annual: "",
  });
  const [moduleForm, setModuleForm] = useState({ module_code: "", name_en: "", name_ar: "" });

  // Prices are entered in whole currency units and stored in cents, so no
  // figure is ever held as a float.
  const toCents = (value: string): number | null =>
    value.trim() === "" ? null : Math.round(Number(value) * 100);

  async function createPlan(e: React.FormEvent) {
    e.preventDefault();
    setActionError(null);
    setNotice(null);
    try {
      await apiFetch("/platform/v1/plans", {
        method: "POST",
        body: {
          code: planForm.code,
          name: planForm.name,
          currency: planForm.currency,
          monthly_price_cents: toCents(planForm.monthly),
          annual_price_cents: toCents(planForm.annual),
        },
      });
      setNotice(`Added plan ${planForm.code}.`);
      setPlanForm({ code: "", name: "", currency: "USD", monthly: "", annual: "" });
      await plans.reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  async function setPrice(plan: Plan, cycle: "monthly" | "annual") {
    const current =
      cycle === "monthly" ? plan.monthly_price_cents : plan.annual_price_cents;
    const entered = window.prompt(
      `${cycle === "monthly" ? "Monthly" : "Annual"} price for ${plan.name} in ${plan.currency}:`,
      current === null ? "" : String(current / 100)
    );
    if (entered === null) return;

    setActionError(null);
    setNotice(null);
    try {
      await apiFetch(`/platform/v1/plans/${plan.id}`, {
        method: "PATCH",
        body: {
          [cycle === "monthly" ? "monthly_price_cents" : "annual_price_cents"]: toCents(entered),
        },
      });
      setNotice(`Updated pricing for ${plan.code}.`);
      await plans.reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  async function createModule(e: React.FormEvent) {
    e.preventDefault();
    setActionError(null);
    setNotice(null);
    try {
      await apiFetch("/platform/v1/modules", { method: "POST", body: moduleForm });
      setNotice(`Added module ${moduleForm.module_code}.`);
      setModuleForm({ module_code: "", name_en: "", name_ar: "" });
      await modules.reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  return (
    <div>
      <PageHeader
        title="Plans & modules"
        subtitle="The catalog every tenant's subscription and entitlements are drawn from."
      />
      <ErrorBanner message={plans.error || modules.error || actionError} />
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 12 }}>
          Plans
        </div>
        {plans.loading && <Loading what="plans" />}
        {plans.data &&
          (plans.data.length === 0 ? (
            <div className="empty-note">No plans defined yet.</div>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Code</th>
                    <th>Name</th>
                    <th>Monthly</th>
                    <th>Annual</th>
                    <th>Status</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {plans.data.map((plan) => (
                    <tr key={plan.id}>
                      <td>
                        <code>{plan.code}</code>
                      </td>
                      <td>{plan.name}</td>
                      <td>{formatPrice(plan.monthly_price_cents, plan.currency)}</td>
                      <td>{formatPrice(plan.annual_price_cents, plan.currency)}</td>
                      <td>{plan.status}</td>
                      <td>
                        <div className="inline-actions">
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setPrice(plan, "monthly")}
                          >
                            Set monthly
                          </button>
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => setPrice(plan, "annual")}
                          >
                            Set annual
                          </button>
                        </div>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        <form onSubmit={createPlan} style={{ marginTop: 16 }}>
          <div className="field-row">
            <label htmlFor="plan-code">New plan code</label>
            <input
              id="plan-code"
              required
              placeholder="STANDARD"
              value={planForm.code}
              onChange={(e) => setPlanForm({ ...planForm, code: e.target.value })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="plan-name">Name</label>
            <input
              id="plan-name"
              required
              placeholder="Standard"
              value={planForm.name}
              onChange={(e) => setPlanForm({ ...planForm, name: e.target.value })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="plan-currency">Currency</label>
            <input
              id="plan-currency"
              required
              maxLength={3}
              value={planForm.currency}
              onChange={(e) => setPlanForm({ ...planForm, currency: e.target.value.toUpperCase() })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="plan-monthly">Monthly price (leave blank if not priced yet)</label>
            <input
              id="plan-monthly"
              type="number"
              min="0"
              step="0.01"
              value={planForm.monthly}
              onChange={(e) => setPlanForm({ ...planForm, monthly: e.target.value })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="plan-annual">Annual price (leave blank if not priced yet)</label>
            <input
              id="plan-annual"
              type="number"
              min="0"
              step="0.01"
              value={planForm.annual}
              onChange={(e) => setPlanForm({ ...planForm, annual: e.target.value })}
            />
          </div>
          <button className="btn btn-primary" type="submit">
            Add plan
          </button>
        </form>
      </div>

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          Modules
        </div>
        <div className="chart-note">
          Two vocabularies share this table: the platform&apos;s own uppercase codes and the FarmOS
          tablet contract&apos;s lowercase permission modules.
        </div>
        {modules.loading && <Loading what="modules" />}
        {modules.data && (
          <div className="table-scroll">
            <table className="data-table">
              <thead>
                <tr>
                  <th>Code</th>
                  <th>Name</th>
                  <th>Arabic</th>
                  <th>Active</th>
                </tr>
              </thead>
              <tbody>
                {modules.data.map((module) => (
                  <tr key={module.module_code}>
                    <td>
                      <code>{module.module_code}</code>
                    </td>
                    <td>{module.name_en}</td>
                    <td>{module.name_ar}</td>
                    <td>{module.active ? "Yes" : "No"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}

        <form onSubmit={createModule} style={{ marginTop: 16 }}>
          <div className="field-row">
            <label htmlFor="module-code">New module code</label>
            <input
              id="module-code"
              required
              value={moduleForm.module_code}
              onChange={(e) => setModuleForm({ ...moduleForm, module_code: e.target.value })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="module-name">Name (English)</label>
            <input
              id="module-name"
              required
              value={moduleForm.name_en}
              onChange={(e) => setModuleForm({ ...moduleForm, name_en: e.target.value })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="module-name-ar">Name (Arabic)</label>
            <input
              id="module-name-ar"
              required
              value={moduleForm.name_ar}
              onChange={(e) => setModuleForm({ ...moduleForm, name_ar: e.target.value })}
            />
          </div>
          <button className="btn btn-primary" type="submit">
            Add module
          </button>
        </form>
      </div>
    </div>
  );
}
