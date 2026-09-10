"use client";

import { useState } from "react";
import { apiFetch } from "@/lib/api";
import { ErrorBanner, Loading, PageHeader, describeError, useResource } from "@/lib/ui";
import { ModuleCatalogItem, Plan } from "@/lib/types";

export default function CatalogPage() {
  const plans = useResource<Plan[]>("/platform/v1/plans");
  const modules = useResource<ModuleCatalogItem[]>("/platform/v1/modules");
  const [notice, setNotice] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [planForm, setPlanForm] = useState({ code: "", name: "" });
  const [moduleForm, setModuleForm] = useState({ module_code: "", name_en: "", name_ar: "" });

  async function createPlan(e: React.FormEvent) {
    e.preventDefault();
    setActionError(null);
    setNotice(null);
    try {
      await apiFetch("/platform/v1/plans", { method: "POST", body: planForm });
      setNotice(`Added plan ${planForm.code}.`);
      setPlanForm({ code: "", name: "" });
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
                    <th>Status</th>
                  </tr>
                </thead>
                <tbody>
                  {plans.data.map((plan) => (
                    <tr key={plan.id}>
                      <td>
                        <code>{plan.code}</code>
                      </td>
                      <td>{plan.name}</td>
                      <td>{plan.status}</td>
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
