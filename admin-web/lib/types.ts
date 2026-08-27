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
