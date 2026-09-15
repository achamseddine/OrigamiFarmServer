"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { apiFetch, ApiError } from "@/lib/api";
import { LicencePack, Plan, Tenant } from "@/lib/types";
import { formatPrice } from "@/lib/ui";

/** Create a customer, end to end.
 *
 * This used to stop halfway: it created the tenant, ticked some modules,
 * made an owner account, and left the admin to work out that the customer
 * still had no subscription, no way to sign in and no paired tablet. Each
 * of those lived on a different tab, so the obvious mistake was to believe
 * the wizard had finished the job.
 *
 * It finishes it now. The last step hands over the three things a customer
 * needs — their email, a password, and a pairing key — so nothing has to
 * be sent and nowhere else has to be visited.
 */
const STEPS = ["Company", "First farm", "Plan", "Owner", "Ready"];

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

  const [plans, setPlans] = useState<Plan[]>([]);
  const [planId, setPlanId] = useState("");
  const [billingCycle, setBillingCycle] = useState("MONTHLY");
  const [subStatus, setSubStatus] = useState("ACTIVE");
  const [planRecorded, setPlanRecorded] = useState(false);

  const [owner, setOwner] = useState({ email: "", display_name: "" });
  // How the owner gets in. Password by default: it needs no mail server
  // and no link anybody has to receive, which is the situation most
  // deployments are actually in.
  const [credential, setCredential] = useState<"password" | "link">("password");
  const [pack, setPack] = useState<LicencePack | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => {
    if (step === 2 && plans.length === 0) {
      apiFetch<Plan[]>("/platform/v1/plans")
        .then(setPlans)
        .catch((err) => setError(err.message));
    }
  }, [step, plans.length]);

  const selectedPlan = plans.find((plan) => plan.id === planId);

  async function run(action: () => Promise<void>, failure: string) {
    setBusy(true);
    setError(null);
    try {
      await action();
    } catch (err) {
      setError(err instanceof ApiError ? `${err.code}: ${err.message}` : failure);
    } finally {
      setBusy(false);
    }
  }

  const createTenant = () =>
    run(async () => {
      setTenant(await apiFetch<Tenant>("/platform/v1/tenants", { method: "POST", body: profile }));
      setStep(1);
    }, "Failed to create tenant");

  const createFarm = () =>
    run(async () => {
      if (!tenant) return;
      await apiFetch(`/platform/v1/tenants/${tenant.id}/farms`, { method: "POST", body: farm });
      setFarmCreated(true);
      setStep(2);
    }, "Failed to create farm");

  const recordPlan = () =>
    run(async () => {
      if (!tenant || !planId) return;
      // One call does both jobs: records what they pay and grants the
      // licences the plan includes, which is what makes the screens
      // appear on their tablets.
      await apiFetch(`/platform/v1/tenants/${tenant.id}/subscription`, {
        method: "PATCH",
        body: {
          plan_id: planId,
          billing_cycle: billingCycle,
          status: subStatus,
          starts_at: new Date().toISOString(),
        },
      });
      setPlanRecorded(true);
      setStep(3);
    }, "Failed to record the plan");

  const finish = () =>
    run(async () => {
      if (!tenant) return;
      const membership = await apiFetch<{ id: string }>(
        `/platform/v1/tenants/${tenant.id}/memberships`,
        { method: "POST", body: { ...owner, tenant_role: "TENANT_OWNER" } }
      );
      if (!membership.id) return;
      // Issuing the licence here rather than on another tab is the whole
      // point: the admin leaves this screen with everything the customer
      // needs, instead of an account nobody can sign in to.
      setPack(
        await apiFetch<LicencePack>(`/platform/v1/tenants/${tenant.id}/licence`, {
          method: "POST",
          body: { send_email: true, credential },
        })
      );
      setStep(4);
    }, "Failed to create the owner");

  function copy(what: string, value: string) {
    navigator.clipboard?.writeText(value);
    setCopied(what);
  }

  return (
    <div>
      <h1 className="page-title">Create a customer</h1>
      <p className="page-subtitle">
        Every step here does something real. By the last one you will have what the customer needs
        to start working.
      </p>

      <div className="wizard-steps">
        {STEPS.map((label, i) => (
          <span key={label} className={`wizard-step ${i === step ? "active" : i < step ? "done" : ""}`}>
            {i + 1}. {label}
          </span>
        ))}
      </div>

      {error && <div className="error-banner">{error}</div>}

      <div className="panel" style={{ maxWidth: step === 4 ? 700 : 520 }}>
        {step === 0 && (
          <>
            <div className="field-row">
              <label>Company ID</label>
              <input
                value={profile.company_code}
                onChange={(e) => setProfile({ ...profile, company_code: e.target.value })}
                placeholder="RIYAK-R"
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
              <label>Country</label>
              <input
                value={profile.country}
                onChange={(e) => setProfile({ ...profile, country: e.target.value })}
              />
            </div>
            <button
              className="btn btn-primary"
              disabled={busy || !profile.company_code || !profile.legal_name}
              onClick={createTenant}
            >
              {busy ? "Creating…" : "Create customer & continue"}
            </button>
          </>
        )}

        {step === 1 && tenant && (
          <>
            <p className="chart-note" style={{ marginTop: 0 }}>
              Their first site. A customer can have several later.
            </p>
            <div className="field-row">
              <label>Farm code</label>
              <input value={farm.farm_code} onChange={(e) => setFarm({ ...farm, farm_code: e.target.value })} />
            </div>
            <div className="field-row">
              <label>Farm name</label>
              <input value={farm.name} onChange={(e) => setFarm({ ...farm, name: e.target.value })} />
            </div>
            <button className="btn btn-primary" disabled={busy || !farm.name} onClick={createFarm}>
              {busy ? "Saving…" : "Create farm & continue"}
            </button>
          </>
        )}

        {step === 2 && tenant && (
          <>
            <p className="chart-note" style={{ marginTop: 0 }}>
              Choosing a plan does two things at once: it records what this customer pays, and it
              opens the screens the plan includes on their tablets.
            </p>
            {plans.length === 0 && <p className="empty-note">Loading plans…</p>}

            <div className="field-row">
              <label htmlFor="wiz-plan">Plan</label>
              <select id="wiz-plan" value={planId} onChange={(e) => setPlanId(e.target.value)}>
                <option value="">Choose a plan…</option>
                {plans.map((plan) => (
                  <option key={plan.id} value={plan.id}>
                    {plan.name} ({plan.code})
                  </option>
                ))}
              </select>
            </div>

            {selectedPlan && (
              <div className="notice-banner" style={{ marginBottom: 16 }}>
                {selectedPlan.module_codes.length === 0 ? (
                  <>
                    <strong>{selectedPlan.name} includes no licences yet.</strong> This customer
                    would be recorded as paying but open nothing. Add licences to the plan under
                    Plans &amp; modules first.
                  </>
                ) : (
                  <>
                    Opens {selectedPlan.module_codes.join(", ")}. Charged at{" "}
                    {formatPrice(
                      billingCycle === "ANNUAL"
                        ? selectedPlan.annual_price_cents
                        : selectedPlan.monthly_price_cents,
                      selectedPlan.currency
                    )}{" "}
                    per {billingCycle === "ANNUAL" ? "year" : "month"}.
                  </>
                )}
              </div>
            )}

            <div className="field-row">
              <label htmlFor="wiz-cycle">Billing cycle</label>
              <select
                id="wiz-cycle"
                value={billingCycle}
                onChange={(e) => setBillingCycle(e.target.value)}
              >
                <option value="MONTHLY">Monthly</option>
                <option value="ANNUAL">Annual</option>
              </select>
            </div>

            <div className="field-row">
              <label htmlFor="wiz-status">Status</label>
              <select id="wiz-status" value={subStatus} onChange={(e) => setSubStatus(e.target.value)}>
                <option value="ACTIVE">Active — paying, counts towards revenue</option>
                <option value="ONBOARDING_TRIAL">Trial — evaluating, not counted yet</option>
              </select>
            </div>

            <div className="inline-actions">
              <button className="btn btn-primary" disabled={busy || !planId} onClick={recordPlan}>
                {busy ? "Saving…" : "Record plan & continue"}
              </button>
              <button className="btn btn-secondary" onClick={() => setStep(3)}>
                Skip for now
              </button>
            </div>
          </>
        )}

        {step === 3 && tenant && (
          <>
            <p className="chart-note" style={{ marginTop: 0 }}>
              The farm&rsquo;s own boss. They manage their staff themselves from the tablet app.
            </p>
            <div className="field-row">
              <label>Owner email</label>
              <input
                type="email"
                value={owner.email}
                onChange={(e) => setOwner({ ...owner, email: e.target.value })}
              />
            </div>
            <div className="field-row">
              <label>Their name</label>
              <input
                value={owner.display_name}
                onChange={(e) => setOwner({ ...owner, display_name: e.target.value })}
              />
            </div>

            <div className="field-row">
              <label htmlFor="wiz-cred">How they get in</label>
              <select
                id="wiz-cred"
                value={credential}
                onChange={(e) => setCredential(e.target.value as "password" | "link")}
              >
                <option value="password">Set a password now — read it to them</option>
                <option value="link">Send a sign-in link — they choose their own</option>
              </select>
            </div>
            <p className="chart-note" style={{ marginTop: -8, marginBottom: 16 }}>
              {credential === "password"
                ? "Nothing needs to be delivered: the next screen shows their email, password and pairing key to read down the phone."
                : "The link is emailed when a mail server is configured, and shown on the next screen either way."}
            </p>

            <button
              className="btn btn-primary"
              disabled={busy || !owner.email || !owner.display_name}
              onClick={finish}
            >
              {busy ? "Finishing…" : "Create owner & issue licence"}
            </button>
          </>
        )}

        {step === 4 && tenant && (
          <>
            <p>
              <strong>{tenant.display_name}</strong> ({tenant.company_code}) is set up.
            </p>
            <ul style={{ color: "var(--farmos-muted)", fontSize: "0.88rem", marginTop: 4 }}>
              <li>Farm: {farmCreated ? "created" : "skipped"}</li>
              <li>
                Plan:{" "}
                {planRecorded
                  ? `${selectedPlan?.name ?? "recorded"} — ${subStatus === "ACTIVE" ? "paying" : "trial"}`
                  : "not recorded — they open nothing and earn nothing until it is"}
              </li>
              <li>Owner: {pack ? pack.owner_email : owner.email}</li>
            </ul>

            {pack && (
              <div className="licence-pack" style={{ marginTop: 18 }}>
                <div className="pack-head">
                  <div>
                    <div className="k">Give the customer these</div>
                    <h3>{pack.display_name}</h3>
                  </div>
                  <span className="chip chip-active">
                    {pack.delivery === "email" ? `Emailed to ${pack.owner_email}` : "Not emailed"}
                  </span>
                </div>
                <p className="chart-note" style={{ marginTop: 0 }}>
                  {pack.delivery_detail} Nothing here can be shown again — issue a new licence from
                  the customer&rsquo;s Licensing tab if it is lost.
                </p>

                <div className="pack-row">
                  <div className="n">1</div>
                  <div>
                    <div className="t">Sign in — for {pack.owner_name}</div>
                    {pack.owner_password ? (
                      <>
                        <div className="d">
                          Read these two to the customer. They should change the password from
                          Settings in the app once they are in.
                        </div>
                        <div className="copyline" style={{ marginBottom: 8 }}>
                          <code className="licence-key">{pack.owner_email}</code>
                        </div>
                        <div className="copyline">
                          <code className="licence-key">{pack.owner_password}</code>
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => copy("password", pack.owner_password ?? "")}
                          >
                            {copied === "password" ? "Copied" : "Copy"}
                          </button>
                        </div>
                      </>
                    ) : (
                      <>
                        <div className="d">
                          {pack.owner_email} opens this and chooses their own password. Works once.
                        </div>
                        <div className="copyline">
                          <input readOnly value={pack.activation_url ?? ""} />
                          <button
                            className="btn btn-secondary btn-sm"
                            onClick={() => copy("link", pack.activation_url ?? "")}
                          >
                            {copied === "link" ? "Copied" : "Copy"}
                          </button>
                        </div>
                      </>
                    )}
                  </div>
                </div>

                <div className="pack-row">
                  <div className="n">2</div>
                  <div>
                    <div className="t">Pair a tablet — licence key</div>
                    <div className="d">
                      Typed into the Origami app, not opened in a browser. Case and dashes do not
                      matter.
                    </div>
                    <div className="copyline">
                      <code className="licence-key">{pack.licence_key}</code>
                      <button
                        className="btn btn-secondary btn-sm"
                        onClick={() => copy("key", pack.licence_key)}
                      >
                        {copied === "key" ? "Copied" : "Copy"}
                      </button>
                    </div>
                  </div>
                </div>

                <div className="pack-foot">
                  <span>
                    <strong>Plan:</strong> {pack.plan_name ?? "not recorded"}
                  </span>
                  <span>
                    <strong>Opens:</strong>{" "}
                    {pack.licences.length > 0 ? pack.licences.join(", ") : "nothing yet"}
                  </span>
                </div>
              </div>
            )}

            <button
              className="btn btn-primary"
              style={{ marginTop: 18 }}
              onClick={() => router.push(`/tenants/detail/?id=${tenant.id}`)}
            >
              Open this customer →
            </button>
          </>
        )}
      </div>
    </div>
  );
}
