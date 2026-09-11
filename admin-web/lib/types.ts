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

export interface Entitlement {
  module_code: string;
  status: string;
  effective_from: string;
  effective_until: string | null;
  reason: string | null;
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
  leases_active: number;
  leases_expiring_7d: number;
  staff_count: number;
  user_count: number;
  renewals_due_30d: number;
  audit_events_per_day: { day: string; events: number }[];
  tenants_created_per_month: { month: string; tenants: number }[];
}

export interface LicensingModule {
  module_code: string;
  name: string;
  license_code: string | null;
  is_permission_module: boolean;
  active: number;
  trial: number;
  other: number;
}

export interface MetricsLicensing {
  generated_at: string;
  tenants_total: number;
  modules: LicensingModule[];
  leases_expiring_soon: {
    lease_id: string;
    tenant_id: string;
    tenant_name: string;
    device_name: string | null;
    expires_at: string;
    modules: string[];
  }[];
}

export interface TenantUsage {
  tenant_id: string;
  company_code: string;
  display_name: string;
  status: string;
  total_records: number;
  records_by_module: Record<string, number>;
  modules_with_data: string[];
  modules_entitled: string[];
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

export interface LicenseLease {
  id: string;
  tenant_id: string;
  device_id: string;
  device_name: string | null;
  issued_at: string;
  expires_at: string;
  policy_version: number;
  modules: string[];
  revoked_at: string | null;
}

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
