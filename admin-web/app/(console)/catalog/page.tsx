"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { ErrorBanner, Loading, PageHeader, describeError, formatPrice, useResource } from "@/lib/ui";
import { ModuleCatalogItem, Plan } from "@/lib/types";

/** Plans & modules — the price list.
 *
 * This screen used to show two tables with nothing said about how they
 * related, because nothing did relate them: `plan_module` existed in the
 * database and no code read it, so a plan was a name with a price and
 * choosing one for a customer granted them nothing. Now a plan holds its
 * modules, and this screen is where you decide what each one sells.
 */

/** The catalog holds two vocabularies (see the Modules panel below), and a
 *  picker that mixes them shows the same display name twice. Splitting them
 *  by the shape of the code is crude but honest — it is the actual
 *  distinction, and it is visible to the reader. */
const MODULE_GROUPS = [
  {
    label: "Platform modules — what a plan is built from",
    match: (code: string) => code === code.toUpperCase(),
  },
  {
    label: "Tablet permission modules — what the app checks",
    match: (code: string) => code !== code.toUpperCase(),
  },
];

export default function CatalogPage() {
  const plans = useResource<Plan[]>("/platform/v1/plans");
  const modules = useResource<ModuleCatalogItem[]>("/platform/v1/modules");
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [editing, setEditing] = useState<string | null>(null);
  const [draft, setDraft] = useState<string[]>([]);
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

  async function run(action: () => Promise<unknown>, message: string) {
    setActionError(null);
    setNotice(null);
    try {
      await action();
      setNotice(message);
      await plans.reload();
    } catch (err) {
      setActionError(describeError(err));
    }
  }

  async function createPlan(e: React.FormEvent) {
    e.preventDefault();
    await run(
      () =>
        apiFetch("/platform/v1/plans", {
          method: "POST",
          body: {
            code: planForm.code,
            name: planForm.name,
            currency: planForm.currency,
            monthly_price_cents: toCents(planForm.monthly),
            annual_price_cents: toCents(planForm.annual),
          },
        }),
      `Added plan ${planForm.code}. Choose which modules it includes below.`
    );
    setPlanForm({ code: "", name: "", currency: "USD", monthly: "", annual: "" });
  }

  async function setPrice(plan: Plan, cycle: "monthly" | "annual") {
    const current = cycle === "monthly" ? plan.monthly_price_cents : plan.annual_price_cents;
    const entered = window.prompt(
      `${cycle === "monthly" ? "Monthly" : "Annual"} price for ${plan.name} in ${plan.currency}:`,
      current === null ? "" : String(current / 100)
    );
    if (entered === null) return;

    await run(
      () =>
        apiFetch(`/platform/v1/plans/${plan.id}`, {
          method: "PATCH",
          body: {
            [cycle === "monthly" ? "monthly_price_cents" : "annual_price_cents"]: toCents(entered),
          },
        }),
      `Updated pricing for ${plan.code}.`
    );
  }

  async function saveModules(plan: Plan) {
    await run(
      () =>
        apiFetch(`/platform/v1/plans/${plan.id}/modules`, {
          method: "PUT",
          body: { module_codes: draft },
        }),
      `${plan.name} now includes ${draft.length} module${draft.length === 1 ? "" : "s"}. ` +
        "Customers already on it keep what they have — this applies from the next subscription."
    );
    setEditing(null);
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

  const moduleName = (code: string) =>
    modules.data?.find((m) => m.module_code === code)?.name_en ?? code;

  return (
    <div>
      <PageHeader
        title="Plans &amp; modules"
        subtitle="Your price list. This is where you decide what Origami sells and for how much — nothing here is about one particular customer."
      />
      <ErrorBanner message={plans.error || modules.error || actionError} />
      {notice && <div className="notice-banner">{notice}</div>}

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          How the two fit together
        </div>
        <div className="pieces" style={{ marginTop: 12 }}>
          <div className="piece">
            <div className="w">A module is a feature area</div>
            <p>
              Animals, milk, crops, sales. The building blocks — each one is a part of the tablet
              app a farm can be allowed to open.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece">
            <div className="w">A plan is a bundle of modules at a price</div>
            <p>
              &ldquo;Starter, $249 a month, includes animals and milk.&rdquo; This is what you
              actually sell.
            </p>
          </div>
          <span className="flow-arrow" aria-hidden="true">
            →
          </span>
          <div className="piece you">
            <div className="w">A customer goes on a plan</div>
            <p>
              On their Subscription tab. That records what they pay and switches on the modules
              the plan includes, in one step.
            </p>
          </div>
        </div>
      </div>

      <div className="panel">
        <div className="chart-title" style={{ marginBottom: 4 }}>
          Plans
        </div>
        <div className="chart-note">
          A plan with no modules sells nothing: a customer put on it is recorded as paying but can
          open nothing. A plan with no price cannot be counted as revenue.
        </div>
        {plans.loading && <Loading what="plans" />}
        {plans.data &&
          (plans.data.length === 0 ? (
            <div className="empty-note">
              No plans yet. Add one below, then choose the modules it includes.
            </div>
          ) : (
            <div className="table-scroll">
              <table className="data-table">
                <thead>
                  <tr>
                    <th>Plan</th>
                    <th>Monthly</th>
                    <th>Annual</th>
                    <th>Includes</th>
                    <th></th>
                  </tr>
                </thead>
                <tbody>
                  {plans.data.map((plan) => (
                    <tr key={plan.id}>
                      <td>
                        <div style={{ fontWeight: 600 }}>{plan.name}</div>
                        <code style={{ fontSize: "0.75rem", color: "var(--farmos-muted)" }}>
                          {plan.code}
                        </code>
                      </td>
                      <td>{formatPrice(plan.monthly_price_cents, plan.currency)}</td>
                      <td>{formatPrice(plan.annual_price_cents, plan.currency)}</td>
                      <td style={{ maxWidth: 280 }}>
                        {editing === plan.id ? (
                          <div>
                            {/* Grouped, because the two vocabularies contain
                                modules with identical display names — an
                                ungrouped list shows "Milk Production" twice
                                with nothing to tell them apart. */}
                            {MODULE_GROUPS.map((group) => {
                              const inGroup =
                                modules.data?.filter((m) => group.match(m.module_code)) ?? [];
                              if (inGroup.length === 0) return null;
                              return (
                                <div key={group.label} style={{ marginBottom: 10 }}>
                                  <div
                                    style={{
                                      fontSize: "0.68rem",
                                      textTransform: "uppercase",
                                      letterSpacing: "0.08em",
                                      color: "var(--farmos-muted)",
                                      marginBottom: 6,
                                    }}
                                  >
                                    {group.label}
                                  </div>
                                  <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                                    {inGroup.map((module) => {
                                      const on = draft.includes(module.module_code);
                                      return (
                                        <button
                                          key={module.module_code}
                                          type="button"
                                          title={module.module_code}
                                          className={`chip ${on ? "chip-active" : "chip-terminated"}`}
                                          style={{ cursor: "pointer", border: "none" }}
                                          onClick={() =>
                                            setDraft(
                                              on
                                                ? draft.filter((c) => c !== module.module_code)
                                                : [...draft, module.module_code]
                                            )
                                          }
                                        >
                                          {on ? "✓ " : "+ "}
                                          {module.module_code}
                                        </button>
                                      );
                                    })}
                                  </div>
                                </div>
                              );
                            })}
                            <div className="inline-actions">
                              <button
                                className="btn btn-primary btn-sm"
                                onClick={() => saveModules(plan)}
                              >
                                Save modules
                              </button>
                              <button
                                className="btn btn-secondary btn-sm"
                                onClick={() => setEditing(null)}
                              >
                                Cancel
                              </button>
                            </div>
                          </div>
                        ) : plan.module_codes.length === 0 ? (
                          <span style={{ color: "var(--farmos-warning)", fontSize: "0.82rem" }}>
                            Nothing yet — this plan sells no features
                          </span>
                        ) : (
                          <span style={{ fontSize: "0.82rem" }}>
                            {plan.module_codes.map(moduleName).join(", ")}
                          </span>
                        )}
                      </td>
                      <td>
                        {editing !== plan.id && (
                          <div className="inline-actions">
                            <button
                              className="btn btn-secondary btn-sm"
                              onClick={() => {
                                setEditing(plan.id);
                                setDraft(plan.module_codes);
                              }}
                            >
                              Choose modules
                            </button>
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
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}

        <form onSubmit={createPlan} style={{ marginTop: 20, maxWidth: 420 }}>
          <div className="chart-title" style={{ marginBottom: 10 }}>
            Add a plan
          </div>
          <div className="field-row">
            <label htmlFor="plan-code">Short code</label>
            <input
              id="plan-code"
              required
              placeholder="STARTER"
              value={planForm.code}
              onChange={(e) => setPlanForm({ ...planForm, code: e.target.value })}
            />
          </div>
          <div className="field-row">
            <label htmlFor="plan-name">Name customers see</label>
            <input
              id="plan-name"
              required
              placeholder="Starter"
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
              placeholder="249"
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
          Every feature area the product has. You rarely add to this list — it changes when the
          tablet app gains a new capability, not when you change what you charge. Two vocabularies
          share it: the platform&apos;s own uppercase codes and the tablet app&apos;s lowercase
          permission modules.
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
                  <th>In plans</th>
                </tr>
              </thead>
              <tbody>
                {modules.data.map((module) => {
                  const inPlans =
                    plans.data?.filter((plan) => plan.module_codes.includes(module.module_code)) ??
                    [];
                  return (
                    <tr key={module.module_code}>
                      <td>
                        <code>{module.module_code}</code>
                      </td>
                      <td>{module.name_en}</td>
                      <td>{module.name_ar}</td>
                      <td style={{ color: "var(--farmos-muted)", fontSize: "0.82rem" }}>
                        {inPlans.length === 0
                          ? "Not sold in any plan"
                          : inPlans.map((plan) => plan.code).join(", ")}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        )}

        <form onSubmit={createModule} style={{ marginTop: 20, maxWidth: 420 }}>
          <div className="chart-title" style={{ marginBottom: 10 }}>
            Add a module
          </div>
          <div className="field-row">
            <label htmlFor="module-code">Module code</label>
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
