-- Origami Server — control-plane schema.
--
-- GENERATED FILE. Do not edit by hand: regenerate with
-- infrastructure/sql/generate.sh after changing a migration.
--
-- Run as the origami role, against an empty database:
--     psql -U origami -d <db> -f 01_control_schema.sql
--
-- Ownership matters: whoever runs this owns the tables. Run it as
-- a superuser and the origami role ends up with no privileges on
-- them. See README.md in this directory.
--
-- Rendered by: alembic -c alembic_control.ini upgrade base:head --sql

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> 17183600cffa

CREATE TABLE feature_flag (
    flag_code VARCHAR(64) NOT NULL, 
    description VARCHAR NOT NULL, 
    enabled_globally BOOLEAN NOT NULL, 
    enabled_tenant_ids JSONB NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_feature_flag_flag_code ON feature_flag (flag_code);

CREATE TABLE module_catalog (
    module_code VARCHAR(64) NOT NULL, 
    name_en VARCHAR NOT NULL, 
    name_ar VARCHAR NOT NULL, 
    description VARCHAR NOT NULL, 
    version VARCHAR NOT NULL, 
    minimum_app_version VARCHAR NOT NULL, 
    dependencies JSONB NOT NULL, 
    default_features JSONB NOT NULL, 
    commercial_status VARCHAR(16) NOT NULL, 
    trial_allowed BOOLEAN NOT NULL, 
    active BOOLEAN NOT NULL, 
    PRIMARY KEY (module_code)
);

CREATE TABLE plan (
    code VARCHAR(32) NOT NULL, 
    name VARCHAR NOT NULL, 
    status VARCHAR(16) NOT NULL, 
    limits JSONB NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_plan_code ON plan (code);

CREATE TABLE tenant (
    company_code VARCHAR(32) NOT NULL, 
    legal_name VARCHAR NOT NULL, 
    display_name VARCHAR NOT NULL, 
    country VARCHAR(2) NOT NULL, 
    timezone VARCHAR NOT NULL, 
    default_currency VARCHAR(3) NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    onboarding_status VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_tenant_company_code ON tenant (company_code);

CREATE TABLE user_identity (
    idp_subject VARCHAR NOT NULL, 
    email VARCHAR NOT NULL, 
    display_name VARCHAR NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE UNIQUE INDEX ix_user_identity_email ON user_identity (email);

CREATE UNIQUE INDEX ix_user_identity_idp_subject ON user_identity (idp_subject);

CREATE TABLE audit_event (
    actor_id UUID, 
    actor_type VARCHAR(64) NOT NULL, 
    actor_role VARCHAR, 
    tenant_id UUID, 
    action VARCHAR(128) NOT NULL, 
    entity_type VARCHAR(64) NOT NULL, 
    entity_id VARCHAR, 
    before_summary JSONB, 
    after_summary JSONB, 
    reason VARCHAR, 
    correlation_id VARCHAR, 
    ip_address VARCHAR, 
    session_id VARCHAR, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    id UUID NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE SET NULL
);

CREATE INDEX ix_audit_event_action ON audit_event (action);

CREATE INDEX ix_audit_event_correlation_id ON audit_event (correlation_id);

CREATE INDEX ix_audit_event_created_at ON audit_event (created_at);

CREATE INDEX ix_audit_event_tenant_id ON audit_event (tenant_id);

CREATE TABLE backup_job (
    tenant_id UUID, 
    job_type VARCHAR(64) NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    started_at TIMESTAMP WITH TIME ZONE, 
    finished_at TIMESTAMP WITH TIME ZONE, 
    error_message VARCHAR, 
    storage_key VARCHAR, 
    size_bytes BIGINT, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE INDEX ix_backup_job_tenant_id ON backup_job (tenant_id);

CREATE TABLE billing_account (
    tenant_id UUID NOT NULL, 
    currency VARCHAR(3) NOT NULL, 
    billing_email VARCHAR, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_billing_account_tenant_id ON billing_account (tenant_id);

CREATE TABLE farm (
    tenant_id UUID NOT NULL, 
    farm_code VARCHAR(32) NOT NULL, 
    name VARCHAR NOT NULL, 
    location_metadata JSONB NOT NULL, 
    timezone_override VARCHAR, 
    active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    CONSTRAINT uq_farm_tenant_code UNIQUE (tenant_id, farm_code)
);

CREATE INDEX ix_farm_tenant_active ON farm (tenant_id, active);

CREATE INDEX ix_farm_tenant_id ON farm (tenant_id);

CREATE TABLE file_object (
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    entity_type VARCHAR(64) NOT NULL, 
    entity_id VARCHAR(64) NOT NULL, 
    storage_key VARCHAR NOT NULL, 
    mime_type VARCHAR(128) NOT NULL, 
    size_bytes BIGINT NOT NULL, 
    checksum VARCHAR, 
    uploaded_by UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    FOREIGN KEY(uploaded_by) REFERENCES user_identity (id), 
    UNIQUE (storage_key)
);

CREATE INDEX ix_file_object_tenant_id ON file_object (tenant_id);

CREATE TABLE plan_module (
    plan_id UUID NOT NULL, 
    module_code VARCHAR(64) NOT NULL, 
    included BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(module_code) REFERENCES module_catalog (module_code) ON DELETE CASCADE, 
    FOREIGN KEY(plan_id) REFERENCES plan (id) ON DELETE CASCADE, 
    CONSTRAINT uq_plan_module UNIQUE (plan_id, module_code)
);

CREATE INDEX ix_plan_module_plan_id ON plan_module (plan_id);

CREATE TABLE platform_role_assignment (
    user_id UUID NOT NULL, 
    platform_role VARCHAR(64) NOT NULL, 
    granted_by UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(granted_by) REFERENCES user_identity (id), 
    FOREIGN KEY(user_id) REFERENCES user_identity (id) ON DELETE CASCADE, 
    CONSTRAINT uq_platform_role_user UNIQUE (user_id, platform_role)
);

CREATE INDEX ix_platform_role_assignment_user_id ON platform_role_assignment (user_id);

CREATE TABLE subscription (
    tenant_id UUID NOT NULL, 
    plan_id UUID NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    billing_cycle VARCHAR(64) NOT NULL, 
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    renews_at TIMESTAMP WITH TIME ZONE, 
    ends_at TIMESTAMP WITH TIME ZONE, 
    grace_until TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(plan_id) REFERENCES plan (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_subscription_tenant_id ON subscription (tenant_id);

CREATE TABLE support_case (
    tenant_id UUID NOT NULL, 
    opened_by UUID NOT NULL, 
    subject VARCHAR NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(opened_by) REFERENCES user_identity (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE INDEX ix_support_case_tenant_id ON support_case (tenant_id);

CREATE TABLE tenant_data_locator (
    tenant_id UUID NOT NULL, 
    mode VARCHAR(64) NOT NULL, 
    connection_secret_ref VARCHAR, 
    schema_version VARCHAR, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (tenant_id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE TABLE tenant_entitlement (
    tenant_id UUID NOT NULL, 
    module_code VARCHAR(64) NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    source VARCHAR(64) NOT NULL, 
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
    effective_until TIMESTAMP WITH TIME ZONE, 
    configuration JSONB NOT NULL, 
    changed_by UUID, 
    reason VARCHAR, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(changed_by) REFERENCES user_identity (id), 
    FOREIGN KEY(module_code) REFERENCES module_catalog (module_code), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    CONSTRAINT uq_tenant_entitlement UNIQUE (tenant_id, module_code)
);

CREATE INDEX ix_tenant_entitlement_tenant_id ON tenant_entitlement (tenant_id);

CREATE TABLE tenant_export (
    tenant_id UUID NOT NULL, 
    requested_by UUID NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    storage_key VARCHAR, 
    download_url_expires_at TIMESTAMP WITH TIME ZONE, 
    reason VARCHAR, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(requested_by) REFERENCES user_identity (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE INDEX ix_tenant_export_tenant_id ON tenant_export (tenant_id);

CREATE TABLE usage_meter (
    tenant_id UUID NOT NULL, 
    metric_code VARCHAR(64) NOT NULL, 
    period_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    period_end TIMESTAMP WITH TIME ZONE NOT NULL, 
    value NUMERIC(20, 4) NOT NULL, 
    id UUID NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    CONSTRAINT uq_usage_meter_period UNIQUE (tenant_id, metric_code, period_start)
);

CREATE INDEX ix_usage_meter_tenant_id ON usage_meter (tenant_id);

CREATE TABLE device (
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    installation_id VARCHAR(128) NOT NULL, 
    display_name VARCHAR NOT NULL, 
    platform VARCHAR(64) NOT NULL, 
    app_version VARCHAR NOT NULL, 
    fingerprint_hash VARCHAR, 
    status VARCHAR(64) NOT NULL, 
    activated_at TIMESTAMP WITH TIME ZONE, 
    last_seen_at TIMESTAMP WITH TIME ZONE, 
    last_sync_at TIMESTAMP WITH TIME ZONE, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    revoked_by UUID, 
    revoked_reason VARCHAR, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(farm_id) REFERENCES farm (id) ON DELETE SET NULL, 
    FOREIGN KEY(revoked_by) REFERENCES user_identity (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX ix_device_installation_id ON device (installation_id);

CREATE INDEX ix_device_tenant_id ON device (tenant_id);

CREATE TABLE invoice (
    billing_account_id UUID NOT NULL, 
    amount_cents INTEGER NOT NULL, 
    currency VARCHAR(3) NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    period_start TIMESTAMP WITH TIME ZONE NOT NULL, 
    period_end TIMESTAMP WITH TIME ZONE NOT NULL, 
    due_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    paid_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(billing_account_id) REFERENCES billing_account (id) ON DELETE CASCADE
);

CREATE INDEX ix_invoice_billing_account_id ON invoice (billing_account_id);

CREATE TABLE subscription_item (
    subscription_id UUID NOT NULL, 
    item_code VARCHAR(64) NOT NULL, 
    quantity INTEGER NOT NULL, 
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
    effective_until TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(subscription_id) REFERENCES subscription (id) ON DELETE CASCADE
);

CREATE INDEX ix_subscription_item_subscription_id ON subscription_item (subscription_id);

CREATE TABLE support_session (
    tenant_id UUID NOT NULL, 
    support_user_id UUID NOT NULL, 
    case_id UUID, 
    reason VARCHAR NOT NULL, 
    scope JSONB NOT NULL, 
    approved_by UUID, 
    starts_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    ended_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(approved_by) REFERENCES user_identity (id), 
    FOREIGN KEY(case_id) REFERENCES support_case (id), 
    FOREIGN KEY(support_user_id) REFERENCES user_identity (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE INDEX ix_support_session_tenant_id ON support_session (tenant_id);

CREATE TABLE tenant_membership (
    tenant_id UUID NOT NULL, 
    user_id UUID NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    tenant_role VARCHAR(64) NOT NULL, 
    default_farm_id UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(default_farm_id) REFERENCES farm (id) ON DELETE SET NULL, 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    FOREIGN KEY(user_id) REFERENCES user_identity (id) ON DELETE CASCADE, 
    CONSTRAINT uq_membership_tenant_user UNIQUE (tenant_id, user_id)
);

CREATE INDEX ix_tenant_membership_tenant_id ON tenant_membership (tenant_id);

CREATE INDEX ix_tenant_membership_user_id ON tenant_membership (user_id);

CREATE TABLE device_activation (
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    code_hash VARCHAR NOT NULL, 
    status VARCHAR(64) NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    used_at TIMESTAMP WITH TIME ZONE, 
    used_by_device_id UUID, 
    created_by UUID, 
    id UUID NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(created_by) REFERENCES user_identity (id), 
    FOREIGN KEY(farm_id) REFERENCES farm (id) ON DELETE SET NULL, 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    FOREIGN KEY(used_by_device_id) REFERENCES device (id)
);

CREATE UNIQUE INDEX ix_device_activation_code_hash ON device_activation (code_hash);

CREATE INDEX ix_device_activation_tenant_id ON device_activation (tenant_id);

CREATE TABLE license_lease (
    tenant_id UUID NOT NULL, 
    device_id UUID NOT NULL, 
    issued_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    policy_version INTEGER NOT NULL, 
    modules JSONB NOT NULL, 
    farm_ids JSONB NOT NULL, 
    permission_profile_hash VARCHAR NOT NULL, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(device_id) REFERENCES device (id) ON DELETE CASCADE, 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE
);

CREATE INDEX ix_license_lease_device_id ON license_lease (device_id);

CREATE INDEX ix_license_lease_tenant_id ON license_lease (tenant_id);

CREATE TABLE membership_farm_access (
    membership_id UUID NOT NULL, 
    farm_id UUID NOT NULL, 
    id UUID NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(farm_id) REFERENCES farm (id) ON DELETE CASCADE, 
    FOREIGN KEY(membership_id) REFERENCES tenant_membership (id) ON DELETE CASCADE, 
    CONSTRAINT uq_membership_farm UNIQUE (membership_id, farm_id)
);

CREATE INDEX ix_membership_farm_access_farm_id ON membership_farm_access (farm_id);

CREATE INDEX ix_membership_farm_access_membership_id ON membership_farm_access (membership_id);

CREATE TABLE membership_module_permission (
    membership_id UUID NOT NULL, 
    module_code VARCHAR(64) NOT NULL, 
    permission_code VARCHAR(64) NOT NULL, 
    id UUID NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(membership_id) REFERENCES tenant_membership (id) ON DELETE CASCADE, 
    CONSTRAINT uq_membership_permission UNIQUE (membership_id, module_code, permission_code)
);

CREATE INDEX ix_membership_module_permission_membership_id ON membership_module_permission (membership_id);

CREATE INDEX ix_membership_module_permission_module_code ON membership_module_permission (module_code);

INSERT INTO alembic_version (version_num) VALUES ('17183600cffa') RETURNING alembic_version.version_num;

-- Running upgrade 17183600cffa -> 67cdc086039d

CREATE TABLE farmos_idempotency_record (
    id UUID NOT NULL, 
    key VARCHAR NOT NULL, 
    user_id UUID NOT NULL, 
    method VARCHAR NOT NULL, 
    path VARCHAR NOT NULL, 
    status_code INTEGER NOT NULL, 
    response_body JSONB, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(user_id) REFERENCES user_identity (id) ON DELETE CASCADE, 
    CONSTRAINT uq_idempotency_key_user UNIQUE (key, user_id)
);

CREATE INDEX ix_farmos_idempotency_record_key ON farmos_idempotency_record (key);

CREATE INDEX ix_farmos_idempotency_record_user_id ON farmos_idempotency_record (user_id);

ALTER TABLE module_catalog ADD COLUMN "group" VARCHAR DEFAULT '' NOT NULL;

ALTER TABLE module_catalog ADD COLUMN license_code VARCHAR;

ALTER TABLE tenant ADD COLUMN region VARCHAR;

ALTER TABLE tenant ALTER COLUMN country TYPE VARCHAR;

ALTER TABLE tenant_entitlement ADD COLUMN plan VARCHAR;

ALTER TABLE tenant_entitlement ADD COLUMN max_users INTEGER;

ALTER TABLE tenant_entitlement ADD COLUMN max_products INTEGER;

ALTER TABLE tenant_membership ADD COLUMN role VARCHAR DEFAULT 'worker' NOT NULL;

ALTER TABLE tenant_membership ADD COLUMN phone VARCHAR;

ALTER TABLE tenant_membership ADD COLUMN department VARCHAR;

ALTER TABLE tenant_membership ADD COLUMN language VARCHAR DEFAULT 'en' NOT NULL;

ALTER TABLE tenant_membership ADD COLUMN job_title VARCHAR;

ALTER TABLE tenant_membership ADD COLUMN employment_status VARCHAR DEFAULT 'active' NOT NULL;

ALTER TABLE tenant_membership ADD COLUMN start_date TIMESTAMP WITH TIME ZONE;

ALTER TABLE tenant_membership ADD COLUMN photo_path VARCHAR;

ALTER TABLE tenant_membership ADD COLUMN working_days JSONB;

ALTER TABLE tenant_membership ADD COLUMN working_hours VARCHAR;

ALTER TABLE tenant_membership ADD COLUMN notes VARCHAR;

ALTER TABLE user_identity ADD COLUMN password_hash VARCHAR;

UPDATE alembic_version SET version_num='67cdc086039d' WHERE alembic_version.version_num = '17183600cffa';

-- Running upgrade 67cdc086039d -> 9cff7b1c5dc1

ALTER TABLE audit_event ADD COLUMN module_code VARCHAR;

ALTER TABLE audit_event ADD COLUMN summary VARCHAR;

ALTER TABLE audit_event ADD COLUMN changes_json JSONB;

ALTER TABLE audit_event ADD COLUMN metadata_json JSONB DEFAULT '{}'::jsonb NOT NULL;

ALTER TABLE audit_event ADD COLUMN device VARCHAR;

UPDATE alembic_version SET version_num='9cff7b1c5dc1' WHERE alembic_version.version_num = '67cdc086039d';

-- Running upgrade 9cff7b1c5dc1 -> a1c4e7f92b30

ALTER TABLE plan ADD COLUMN currency VARCHAR(3) DEFAULT 'USD' NOT NULL;

ALTER TABLE plan ADD COLUMN monthly_price_cents INTEGER;

ALTER TABLE plan ADD COLUMN annual_price_cents INTEGER;

UPDATE alembic_version SET version_num='a1c4e7f92b30' WHERE alembic_version.version_num = '9cff7b1c5dc1';

-- Running upgrade a1c4e7f92b30 -> b7d21f508c44

ALTER TABLE user_identity ADD COLUMN password_changed_at TIMESTAMP WITH TIME ZONE;

UPDATE alembic_version SET version_num='b7d21f508c44' WHERE alembic_version.version_num = 'a1c4e7f92b30';

-- Running upgrade b7d21f508c44 -> c3f81a2e7d95

CREATE TABLE membership_invitation (
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    membership_id UUID NOT NULL, 
    token_hash VARCHAR NOT NULL, 
    expires_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    accepted_at TIMESTAMP WITH TIME ZONE, 
    revoked_at TIMESTAMP WITH TIME ZONE, 
    invited_by UUID, 
    delivery VARCHAR(16) DEFAULT 'manual' NOT NULL, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id), 
    FOREIGN KEY(tenant_id) REFERENCES tenant (id) ON DELETE CASCADE, 
    FOREIGN KEY(membership_id) REFERENCES tenant_membership (id) ON DELETE CASCADE, 
    FOREIGN KEY(invited_by) REFERENCES user_identity (id) ON DELETE SET NULL, 
    UNIQUE (token_hash)
);

CREATE INDEX ix_membership_invitation_tenant_id ON membership_invitation (tenant_id);

CREATE INDEX ix_membership_invitation_membership_id ON membership_invitation (membership_id);

CREATE UNIQUE INDEX ix_membership_invitation_token_hash ON membership_invitation (token_hash);

UPDATE alembic_version SET version_num='c3f81a2e7d95' WHERE alembic_version.version_num = 'b7d21f508c44';

-- Running upgrade c3f81a2e7d95 -> d4a9c61b83e7

UPDATE module_catalog SET license_code = 'CORE' WHERE module_code = 'morning_operations' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'CORE' WHERE module_code = 'tasks' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'CORE' WHERE module_code = 'employees' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'CORE' WHERE module_code = 'reports' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'CORE' WHERE module_code = 'settings' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'ANIMALS' WHERE module_code = 'animals' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'ANIMAL_HEALTH' WHERE module_code = 'animal_health' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'FEED' WHERE module_code = 'feed_nutrition' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'MILK' WHERE module_code = 'milk_production' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'EGGS' WHERE module_code = 'egg_production' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'AGRICULTURE' WHERE module_code = 'agriculture' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'PRODUCE' WHERE module_code = 'produce_harvest' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'INVENTORY' WHERE module_code = 'inventory' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'SALES' WHERE module_code = 'sales' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'SALES' WHERE module_code = 'expenses' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'SALES' WHERE module_code = 'finance' AND license_code IS NULL;

UPDATE module_catalog SET license_code = 'AI_INTELLIGENCE' WHERE module_code = 'ai_intelligence' AND license_code IS NULL;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('AGRICULTURE', 'AGRICULTURE', 'AGRICULTURE', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('AI_INTELLIGENCE', 'AI_INTELLIGENCE', 'AI_INTELLIGENCE', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('ANIMALS', 'ANIMALS', 'ANIMALS', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('ANIMAL_HEALTH', 'ANIMAL_HEALTH', 'ANIMAL_HEALTH', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('CORE', 'CORE', 'CORE', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('EGGS', 'EGGS', 'EGGS', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('FEED', 'FEED', 'FEED', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('INVENTORY', 'INVENTORY', 'INVENTORY', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('MILK', 'MILK', 'MILK', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('PRODUCE', 'PRODUCE', 'PRODUCE', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO module_catalog (module_code, name_en, name_ar, description, version, minimum_app_version, dependencies, default_features, commercial_status, trial_allowed, active, "group") VALUES ('SALES', 'SALES', 'SALES', '', '1.0.0', '0.0.0', '[]'::jsonb, '{}'::jsonb, 'AVAILABLE', true, true, '') ON CONFLICT (module_code) DO NOTHING;

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('AGRICULTURE' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('AGRICULTURE' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('AI_INTELLIGENCE' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('AI_INTELLIGENCE' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('ANIMALS' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('ANIMALS' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('ANIMAL_HEALTH' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('ANIMAL_HEALTH' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('CORE' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('CORE' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('EGGS' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('EGGS' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('FEED' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('FEED' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('INVENTORY' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('INVENTORY' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('MILK' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('MILK' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('PRODUCE' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('PRODUCE' AS varchar));

INSERT INTO tenant_entitlement (id, tenant_id, module_code, status, source, effective_from,  configuration, created_at, updated_at) SELECT gen_random_uuid(), t.id, CAST('SALES' AS varchar), 'ACTIVE', 'PLAN', now(),        '{}'::jsonb, now(), now() FROM tenant t WHERE NOT EXISTS (    SELECT 1 FROM tenant_entitlement e     WHERE e.tenant_id = t.id AND e.module_code = CAST('SALES' AS varchar));

UPDATE alembic_version SET version_num='d4a9c61b83e7' WHERE alembic_version.version_num = 'c3f81a2e7d95';

-- Running upgrade d4a9c61b83e7 -> e5b2d84f1c06

UPDATE tenant_membership
           SET role = 'owner'
         WHERE tenant_role = 'TENANT_OWNER'
           AND role = 'worker';

UPDATE tenant_membership
           SET role = 'manager'
         WHERE tenant_role = 'FARM_MANAGER'
           AND role = 'worker';

UPDATE alembic_version SET version_num='e5b2d84f1c06' WHERE alembic_version.version_num = 'd4a9c61b83e7';

-- Running upgrade e5b2d84f1c06 -> f9a3c17e64b2

INSERT INTO plan (id, code, name, status, currency,
                          monthly_price_cents, annual_price_cents, limits,
                          created_at, updated_at)
        SELECT gen_random_uuid(), 'ORIGAMI', 'Origami', 'ACTIVE',
               COALESCE((SELECT currency FROM plan ORDER BY created_at LIMIT 1), 'USD'),
               NULL, NULL, '{}'::jsonb, now(), now()
        WHERE NOT EXISTS (SELECT 1 FROM plan WHERE code = 'ORIGAMI');

UPDATE subscription
        SET plan_id = (SELECT id FROM plan WHERE code = 'ORIGAMI')
        WHERE plan_id <> (SELECT id FROM plan WHERE code = 'ORIGAMI');

UPDATE plan SET status = 'ARCHIVED' WHERE code <> 'ORIGAMI' AND status <> 'ARCHIVED';

UPDATE alembic_version SET version_num='f9a3c17e64b2' WHERE alembic_version.version_num = 'e5b2d84f1c06';

COMMIT;

