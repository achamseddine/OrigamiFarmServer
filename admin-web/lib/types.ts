export interface Tenant {
  id: string;
  company_code: string;
  legal_name: string;
  display_name: string;
  country: string;
  timezone: string;
  default_currency: string;
  status: "ONBOARDING" | "TRIAL" | "ACTIVE" | "GRACE" | "SUSPENDED" | "TERMINATED";
  onboarding_status: "NOT_STARTED" | "IN_PROGRESS" | "COMPLETE";
  created_at: string;
}

export interface TenantListResponse {
  items: Tenant[];
  total: number;
  limit: number;
  offset: number;
}

export interface Farm {
  id: string;
  tenant_id: string;
  farm_code: string;
  name: string;
  active: boolean;
}

export interface ModuleCatalogItem {
  module_code: string;
  name_en: string;
  name_ar: string;
  description: string;
  dependencies: string[];
  active: boolean;
}

export interface DeviceItem {
  id: string;
  tenant_id: string;
  farm_id: string | null;
  installation_id: string;
  display_name: string;
  status: string;
  last_seen_at: string | null;
  last_sync_at: string | null;
}

export interface AuditEventItem {
  id: string;
  actor_id: string | null;
  actor_type: string;
  tenant_id: string | null;
  action: string;
  entity_type: string;
  entity_id: string | null;
  reason: string | null;
  created_at: string;
}

export interface DashboardSummary {
  active_tenants: number;
  trial_tenants: number;
  suspended_tenants: number;
  active_devices: number;
  renewals_due_30d: number;
  backup_failures: number;
}

export interface MetricsOverview {
  generated_at: string;
  tenants_by_status: Record<string, number>;
  tenants_total: number;
  devices_by_status: Record<string, number>;
  devices_total: number;
  staff_count: number;
  user_count: number;
  renewals_due_30d: number;
  audit_events_per_day: { day: string; events: number }[];
  tenants_created_per_month: { month: string; tenants: number }[];
}

export interface TenantUsage {
  tenant_id: string;
  company_code: string;
  display_name: string;
  status: string;
  total_records: number;
  records_by_module: Record<string, number>;
  modules_with_data: string[];
  last_activity_at: string | null;
  active_devices: number;
  active_users: number;
}

export interface UsageList {
  items: TenantUsage[];
  tenants_total: number;
  tenants_measured: number;
}

export interface Staff {
  user_id: string;
  email: string;
  display_name: string;
  platform_roles: string[];
  has_password: boolean;
  password_set_by_someone_else: boolean;
}

export interface Membership {
  id: string;
  tenant_id: string;
  user_id: string;
  tenant_role: string;
  status: string;
  email: string;
  display_name: string;
  role: string;
  default_farm_id: string | null;
  has_password: boolean;
}

/** The one subscription. There is no second plan to choose between: the
 *  list endpoint returns exactly this, and the console edits its price. */
export interface Plan {
  id: string;
  code: string;
  name: string;
  status: string;
  limits: Record<string, unknown>;
  currency: string;
  monthly_price_cents: number | null;
  annual_price_cents: number | null;
}

export interface PlanRevenue {
  plan_code: string;
  plan_name: string;
  currency: string;
  monthly_price_cents: number | null;
  annual_price_cents: number | null;
  subscriptions: number;
  mrr_cents: number;
  unpriced_subscriptions: number;
}

export interface MetricsRevenue {
  generated_at: string;
  currency: string;
  currencies_present: string[];
  mrr_cents: number;
  arr_cents: number;
  arpa_cents: number;
  paying_tenants: number;
  trial_tenants: number;
  at_risk_tenants: number;
  at_risk_mrr_cents: number;
  lost_tenants: number;
  renewals_due_30d: number;
  renewals_due_30d_mrr_cents: number;
  unpriced_subscriptions: number;
  tenants_without_subscription: number;
  by_plan: PlanRevenue[];
  tenants_created_per_month: { month: string; tenants: number }[];
  invoicing: {
    invoices_recorded: number;
    billed_cents: number;
    collected_cents: number;
    outstanding_cents: number;
    overdue_cents: number;
    billing_configured: boolean;
  };
}

export interface Subscription {
  id: string;
  tenant_id: string;
  plan_id: string;
  status: string;
  billing_cycle: string;
  starts_at: string;
  renews_at: string | null;
  ends_at: string | null;
  grace_until: string | null;
}

/** One step of the first-run checklist (GET /platform/v1/setup).
 *
 * The key set is closed on purpose: the guide screen maps each one to the
 * screen that does it, so a step the API adds without the console knowing
 * would be a compile error rather than a box with nowhere to go.
 */
export interface SetupStep {
  key: "password" | "staff" | "pricing" | "tenant" | "subscription" | "device";
  done: boolean;
  detail: string;
}

export interface SetupState {
  generated_at: string;
  steps: SetupStep[];
  steps_done: number;
  steps_total: number;
  complete: boolean;
}

/** What PATCH /tenants/{id}/subscription did.
 *
 *  The module lists are always empty and kept only so an older console
 *  build reading them does not break: subscribing records what a customer
 *  pays and grants nothing, because they already had everything. */
export interface SubscriptionSaveResult {
  subscription: Subscription;
  plan_code: string;
  modules_granted: string[];
  modules_already_active: string[];
  modules_not_in_plan: string[];
}

/** Where an invited tenant user has got to, without exposing their link. */
export interface InvitationStatus {
  membership_id: string;
  email: string;
  display_name: string;
  has_password: boolean;
  invitation_sent_at: string | null;
  invitation_expires_at: string | null;
  invitation_accepted_at: string | null;
  state: "no_invitation" | "pending" | "expired" | "accepted";
}

/** The invitation link itself — returned once, never readable again. */
export interface IssuedInvitation {
  invitation_id: string;
  email: string;
  url: string;
  expires_at: string;
  delivery: "email" | "manual" | "failed";
  delivery_detail: string;
}

/** One area of the product, and the screens that belong to it.
 *
 * These used to be things a customer could buy separately, and the plan
 * picker was built from them. Nothing is bought separately now, so this
 * only groups the module list into readable sections.
 */
export interface Licence {
  license_code: string;
  name: string;
  unlocks: string[];
  unlocks_labels: string[];
}

/** The customer handover, returned exactly once.
 *
 * One credential, not two: the tablet pairing key is gone with device
 * licences, so this is only how the owner first signs in. Whatever it
 * carries the API cannot read back, so it is copied now or reissued
 * later — never looked up.
 */
export interface LicencePack {
  tenant_id: string;
  company_code: string;
  display_name: string;
  plan_code: string | null;
  plan_name: string | null;
  owner_email: string;
  owner_name: string;
  /** Exactly one of these, per the credential asked for: a one-time
   *  sign-in link, or a password set for the owner here. */
  activation_url: string | null;
  activation_expires_at: string | null;
  owner_password: string | null;
  delivery: "email" | "manual" | "failed";
  delivery_detail: string;
}

/** A password set for a farm user by an admin — shown once. */
export interface MemberPassword {
  email: string;
  display_name: string;
  password: string;
  must_change: boolean;
}
