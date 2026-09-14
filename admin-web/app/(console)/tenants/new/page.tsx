"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { IssuedInvitation, ModuleCatalogItem, Tenant } from "@/lib/types";

const STEPS = ["Company profile", "First farm", "Modules", "Tenant Owner", "Review"];

export default function CreateTenantWizard() {
  const router = useRouter();
  const [step, setStep] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [tenant, setTenant] = useState<Tenant | null>(null);
  const [profile, setProfile] = useState({
    company_code: "",
    legal_name: "",
    display_name: "",
    country: "US",
    timezone: "UTC",
    default_currency: "USD",
  });
  const [farm, setFarm] = useState({ farm_code: "MAIN", name: "" });
  const [farmCreated, setFarmCreated] = useState(false);

  const [modules, setModules] = useState<ModuleCatalogItem[]>([]);
  const [selectedModules, setSelectedModules] = useState<Record<string, boolean>>({});
  const [modulesActivated, setModulesActivated] = useState(false);

  const [owner, setOwner] = useState({ email: "", display_name: "" });
  const [ownerInvited, setOwnerInvited] = useState(false);
  const [invitation, setInvitation] = useState<IssuedInvitation | null>(null);
  const [copied, setCopied] = useState(false);

  useEffect(() => {
    if (step === 2 && modules.length === 0) {
      apiFetch<ModuleCatalogItem[]>("/platform/v1/modules")
        .then(setModules)
        .catch((err) => setError(err.message));
    }
  }, [step, modules.length]);

  async function handleCreateTenant() {
    setBusy(true);
    setError(null);
    try {
      const created = await apiFetch<Tenant>("/platform/v1/tenants", {
        method: "POST",
        body: profile,
      });
      setTenant(created);
      setStep(1);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to create tenant");
    } finally {
      setBusy(false);
    }
  }

  async function handleCreateFarm() {
    if (!tenant) return;
    setBusy(true);
    setError(null);
    try {
      await apiFetch(`/platform/v1/tenants/${tenant.id}/farms`, { method: "POST", body: farm });
      setFarmCreated(true);
      setStep(2);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to create farm");
    } finally {
      setBusy(false);
    }
  }

  async function handleActivateModules() {
    if (!tenant) return;
    setBusy(true);
    setError(null);
    try {
      const codes = Object.entries(selectedModules)
        .filter(([, checked]) => checked)
        .map(([code]) => code);
      for (const code of codes) {
        await apiFetch(`/platform/v1/tenants/${tenant.id}/entitlements/${code}/activate`, {
          method: "POST",
          body: { reason: "Selected during onboarding wizard" },
        });
      }
      setModulesActivated(true);
      setStep(3);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to activate modules");
    } finally {
      setBusy(false);
    }
  }

  async function handleInviteOwner() {
    if (!tenant) return;
    setBusy(true);
    setError(null);
    try {
      const membership = await apiFetch<{ id: string }>(
        `/platform/v1/tenants/${tenant.id}/memberships`,
        { method: "POST", body: { ...owner, tenant_role: "TENANT_OWNER" } }
      );
      // Creating the membership alone leaves them with an account and no
      // password, which is what made a brand-new owner unable to open
      // anything. The invitation is the half that lets them in, so it is
      // issued here rather than left as a step somebody has to know about.
      setInvitation(
        await apiFetch<IssuedInvitation>(
          `/platform/v1/tenants/${tenant.id}/memberships/${membership.id}/invitation`,
          { method: "POST", body: { send_email: true } }
        )
      );
      setOwnerInvited(true);
      setStep(4);
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : "Failed to invite owner");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <h1 className="page-title">Create Tenant</h1>
      <p className="page-subtitle">Each step below performs a real action against the running tenant.</p>

      <div className="wizard-steps">
        {STEPS.map((label, i) => (
          <span key={label} className={`wizard-step ${i === step ? "active" : i < step ? "done" : ""}`}>
            {i + 1}. {label}
          </span>
        ))}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="panel" style={{ maxWidth: 520 }}>
        {step === 0 && (
          <>
            <div className="field-row">
              <label>Company ID (company_code)</label>
              <input
                value={profile.company_code}
                onChange={(e) => setProfile({ ...profile, company_code: e.target.value })}
                placeholder="FARM-C"
              />
            </div>
            <div className="field-row">
              <label>Legal name</label>
              <input
                value={profile.legal_name}
                onChange={(e) => setProfile({ ...profile, legal_name: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>Display name</label>
              <input
                value={profile.display_name}
                onChange={(e) => setProfile({ ...profile, display_name: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>Country (ISO-2)</label>
              <input
                value={profile.country}
                maxLength={2}
                onChange={(e) => setProfile({ ...profile, country: e.target.value.toUpperCase() })}
              />
            </div>
            <button
              className="btn btn-primary"
              disabled={busy || !profile.company_code || !profile.legal_name}
              onClick={handleCreateTenant}
            >
              {busy ? "Creating…" : "Create tenant & continue"}
            </button>
          </>
        )}

        {step === 1 && tenant && (
          <>
            <div className="field-row">
              <label>Farm code</label>
              <input value={farm.farm_code} onChange={(e) => setFarm({ ...farm, farm_code: e.target.value })} />
            </div>
            <div className="field-row">
              <label>Farm name</label>
              <input value={farm.name} onChange={(e) => setFarm({ ...farm, name: e.target.value })} />
            </div>
            <button className="btn btn-primary" disabled={busy || !farm.name} onClick={handleCreateFarm}>
              {busy ? "Saving…" : "Create farm & continue"}
            </button>
          </>
        )}

        {step === 2 && tenant && (
          <>
            {modules.length === 0 && <p>Loading module catalog…</p>}
            {modules.map((m) => (
              <label key={m.module_code} style={{ display: "flex", gap: 8, marginBottom: 8, alignItems: "center" }}>
                <input
                  type="checkbox"
                  checked={!!selectedModules[m.module_code]}
                  onChange={(e) =>
                    setSelectedModules({ ...selectedModules, [m.module_code]: e.target.checked })
                  }
                />
                {m.name_en} <code style={{ fontSize: "0.75rem", color: "var(--farmos-muted)" }}>{m.module_code}</code>
              </label>
            ))}
            <div style={{ marginTop: 12 }}>
              <button className="btn btn-primary" disabled={busy} onClick={handleActivateModules}>
                {busy ? "Activating…" : "Activate selected modules & continue"}
              </button>
              <button className="btn btn-secondary" style={{ marginLeft: 8 }} onClick={() => setStep(3)}>
                Skip
              </button>
            </div>
          </>
        )}

        {step === 3 && tenant && (
          <>
            <div className="field-row">
              <label>Tenant Owner email</label>
              <input
                type="email"
                value={owner.email}
                onChange={(e) => setOwner({ ...owner, email: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>Display name</label>
              <input
                value={owner.display_name}
                onChange={(e) => setOwner({ ...owner, display_name: e.target.value })}
              />
            </div>
            <p className="chart-note" style={{ marginTop: -6, marginBottom: 14 }}>
              This creates their account and issues an invitation link so they can choose their own
              password. If this deployment has no mail server the link is shown for you to send on.
            </p>
            <button className="btn btn-primary" disabled={busy || !owner.email} onClick={handleInviteOwner}>
              {busy ? "Inviting…" : "Invite owner & continue"}
            </button>
            <button className="btn btn-secondary" style={{ marginLeft: 8 }} onClick={() => setStep(4)}>
              Skip
            </button>
          </>
        )}

        {step === 4 && tenant && (
          <>
            <p>
              <strong>{tenant.display_name}</strong> ({tenant.company_code}) has been created.
            </p>
            <ul style={{ color: "var(--farmos-muted)", fontSize: "0.9rem" }}>
              <li>Farm: {farmCreated ? "created" : "skipped"}</li>
              <li>Modules: {modulesActivated ? "activated" : "none activated yet"}</li>
              <li>Tenant Owner: {ownerInvited ? owner.email : "not invited yet"}</li>
            </ul>

            {invitation && (
              <div className="notice-banner" style={{ marginBottom: 16 }}>
                <strong>
                  {invitation.delivery === "email"
                    ? `Invitation emailed to ${invitation.email}.`
                    : `Send this link to ${invitation.email} so they can set a password:`}
                </strong>
                <div style={{ display: "flex", gap: 8, marginTop: 10, alignItems: "center" }}>
                  <input readOnly value={invitation.url} style={{ flex: 1, fontSize: "0.78rem" }} />
                  <button
                    className="btn btn-secondary btn-sm"
                    onClick={() => {
                      navigator.clipboard?.writeText(invitation.url);
                      setCopied(true);
                    }}
                  >
                    {copied ? "Copied" : "Copy"}
                  </button>
                </div>
                <div style={{ marginTop: 8, fontSize: "0.78rem" }}>
                  It works once and cannot be shown again — issue a new one from the customer&apos;s
                  Access tab if it is lost.
                </div>
              </div>
            )}

            <p style={{ fontSize: "0.88rem" }}>
              <strong>Next:</strong> record what they pay on the Subscription tab. That is what
              switches on the modules in their plan and puts them into the revenue figures.
            </p>
            <button className="btn btn-primary" onClick={() => router.push(`/tenants/detail/?id=${tenant.id}`)}>
              Go to Tenant 360 →
            </button>
          </>
        )}
      </div>
    </div>
  );
}
