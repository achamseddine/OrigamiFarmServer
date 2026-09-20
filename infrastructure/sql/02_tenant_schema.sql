-- Origami Server — farm-data-plane schema.
--
-- GENERATED FILE. Do not edit by hand: regenerate with
-- infrastructure/sql/generate.sh after changing a migration.
--
-- Run as the origami role, against an empty database:
--     psql -U origami -d <db> -f 02_tenant_schema.sql
--
-- Ownership matters: whoever runs this owns the tables. Run it as
-- a superuser and the origami role ends up with no privileges on
-- them. See README.md in this directory.
--
-- Rendered by: alembic -c alembic_tenant.ini upgrade base:head --sql

BEGIN;

CREATE TABLE alembic_version (
    version_num VARCHAR(32) NOT NULL, 
    CONSTRAINT alembic_version_pkc PRIMARY KEY (version_num)
);

-- Running upgrade  -> cebaa357eecc

CREATE TABLE animal (
    tag_code VARCHAR(64) NOT NULL, 
    species VARCHAR(64) NOT NULL, 
    name VARCHAR, 
    birth_date TIMESTAMP WITH TIME ZONE, 
    attributes JSONB NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_animal_farm_id ON animal (farm_id);

CREATE INDEX ix_animal_tenant_id ON animal (tenant_id);

CREATE TABLE field (
    name VARCHAR NOT NULL, 
    crop VARCHAR, 
    area_hectares NUMERIC(10, 3), 
    attributes JSONB NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_field_farm_id ON field (farm_id);

CREATE INDEX ix_field_tenant_id ON field (tenant_id);

CREATE TABLE inventory_item (
    sku VARCHAR(64) NOT NULL, 
    name VARCHAR NOT NULL, 
    unit VARCHAR(16) NOT NULL, 
    quantity_on_hand NUMERIC(14, 3) NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_inventory_item_farm_id ON inventory_item (farm_id);

CREATE INDEX ix_inventory_item_tenant_id ON inventory_item (tenant_id);

CREATE TABLE inventory_movement (
    inventory_item_id UUID NOT NULL, 
    quantity_delta NUMERIC(14, 3) NOT NULL, 
    reason VARCHAR NOT NULL, 
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_inventory_movement_farm_id ON inventory_movement (farm_id);

CREATE INDEX ix_inventory_movement_inventory_item_id ON inventory_movement (inventory_item_id);

CREATE INDEX ix_inventory_movement_tenant_id ON inventory_movement (tenant_id);

CREATE TABLE sync_event (
    tenant_id UUID NOT NULL, 
    device_id UUID, 
    entity_type VARCHAR(64) NOT NULL, 
    entity_id VARCHAR(64) NOT NULL, 
    operation VARCHAR(64) NOT NULL, 
    applied_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    result JSONB NOT NULL, 
    id UUID NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_sync_event_device_id ON sync_event (device_id);

CREATE INDEX ix_sync_event_tenant_id ON sync_event (tenant_id);

CREATE TABLE task (
    title VARCHAR NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    assigned_to UUID, 
    due_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_task_farm_id ON task (farm_id);

CREATE INDEX ix_task_tenant_id ON task (tenant_id);

CREATE OR REPLACE FUNCTION app_current_tenant_id() RETURNS uuid
        LANGUAGE plpgsql STABLE AS $fn$
        DECLARE
            raw text;
        BEGIN
            raw := nullif(current_setting('app.tenant_id', true), '');
            IF raw IS NULL THEN
                RETURN NULL;
            END IF;
            RETURN raw::uuid;
        EXCEPTION WHEN invalid_text_representation THEN
            RETURN NULL;
        END;
        $fn$;

ALTER TABLE animal ENABLE ROW LEVEL SECURITY;

ALTER TABLE animal FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_animal
            ON animal
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE field ENABLE ROW LEVEL SECURITY;

ALTER TABLE field FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_field
            ON field
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE inventory_item ENABLE ROW LEVEL SECURITY;

ALTER TABLE inventory_item FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_inventory_item
            ON inventory_item
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE inventory_movement ENABLE ROW LEVEL SECURITY;

ALTER TABLE inventory_movement FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_inventory_movement
            ON inventory_movement
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE sync_event ENABLE ROW LEVEL SECURITY;

ALTER TABLE sync_event FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_sync_event
            ON sync_event
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE task ENABLE ROW LEVEL SECURITY;

ALTER TABLE task FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_task
            ON task
            USING (tenant_id = app_current_tenant_id())
            WITH CHECK (tenant_id = app_current_tenant_id());

INSERT INTO alembic_version (version_num) VALUES ('cebaa357eecc') RETURNING alembic_version.version_num;

-- Running upgrade cebaa357eecc -> f6f362c79150

CREATE TABLE notification (
    module_code VARCHAR(64) NOT NULL, 
    notification_type VARCHAR(64) NOT NULL, 
    title VARCHAR NOT NULL, 
    description VARCHAR, 
    priority VARCHAR(16) NOT NULL, 
    entity_type VARCHAR, 
    entity_id VARCHAR, 
    source_type VARCHAR, 
    source_id VARCHAR, 
    read_at TIMESTAMP WITH TIME ZONE, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_notification_farm_id ON notification (farm_id);

CREATE INDEX ix_notification_tenant_id ON notification (tenant_id);

ALTER TABLE notification ENABLE ROW LEVEL SECURITY;

ALTER TABLE notification FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_notification
        ON notification
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE animal ADD COLUMN tag VARCHAR(64) DEFAULT '' NOT NULL;

ALTER TABLE animal ADD COLUMN breed VARCHAR;

ALTER TABLE animal ADD COLUMN sex VARCHAR(1);

ALTER TABLE animal ADD COLUMN status VARCHAR(32) DEFAULT 'healthy' NOT NULL;

ALTER TABLE animal ADD COLUMN location_label VARCHAR;

ALTER TABLE animal ADD COLUMN health_score INTEGER DEFAULT '100' NOT NULL;

ALTER TABLE animal ADD COLUMN pregnant BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE animal ADD COLUMN pregnancy_days INTEGER;

ALTER TABLE animal ADD COLUMN lactating BOOLEAN DEFAULT false NOT NULL;

ALTER TABLE animal ADD COLUMN lactation_cycle INTEGER;

ALTER TABLE animal ADD COLUMN withdrawal_until TIMESTAMP WITH TIME ZONE;

ALTER TABLE animal ADD COLUMN withdrawal_reason VARCHAR;

ALTER TABLE animal ADD COLUMN weight_kg NUMERIC(10, 2);

ALTER TABLE animal ADD COLUMN group_name VARCHAR;

ALTER TABLE animal ADD COLUMN photo_path VARCHAR;

ALTER TABLE animal ADD COLUMN acquisition_date TIMESTAMP WITH TIME ZONE;

ALTER TABLE animal ADD COLUMN acquisition_source VARCHAR;

ALTER TABLE animal ADD COLUMN sire_tag VARCHAR;

ALTER TABLE animal ADD COLUMN dam_tag VARCHAR;

ALTER TABLE animal ADD COLUMN color_markings VARCHAR;

ALTER TABLE animal ADD COLUMN purchase_cost NUMERIC(12, 2);

ALTER TABLE animal ADD COLUMN current_value NUMERIC(12, 2);

ALTER TABLE animal ADD COLUMN notes VARCHAR;

ALTER TABLE animal ADD COLUMN active BOOLEAN DEFAULT true NOT NULL;

ALTER TABLE animal ALTER COLUMN name SET NOT NULL;

ALTER TABLE animal DROP COLUMN tag_code;

ALTER TABLE animal DROP COLUMN attributes;

ALTER TABLE task ADD COLUMN description VARCHAR;

ALTER TABLE task ADD COLUMN priority VARCHAR(16) DEFAULT 'medium' NOT NULL;

ALTER TABLE task ADD COLUMN source_type VARCHAR;

ALTER TABLE task ADD COLUMN source_id VARCHAR;

UPDATE alembic_version SET version_num='f6f362c79150' WHERE alembic_version.version_num = 'cebaa357eecc';

-- Running upgrade f6f362c79150 -> de1b1a212f04

CREATE TABLE crop (
    name VARCHAR NOT NULL, 
    category VARCHAR, 
    default_cycle_days INTEGER, 
    active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_crop_farm_id ON crop (farm_id);

CREATE INDEX ix_crop_tenant_id ON crop (tenant_id);

CREATE TABLE crop_planting (
    field_id UUID NOT NULL, 
    crop_id UUID NOT NULL, 
    variety VARCHAR, 
    planted_area NUMERIC(10, 3), 
    area_unit VARCHAR(16), 
    planted_date TIMESTAMP WITH TIME ZONE, 
    expected_harvest_date TIMESTAMP WITH TIME ZONE, 
    expected_yield_kg NUMERIC(10, 2), 
    stage VARCHAR(32) NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    notes VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_crop_planting_crop_id ON crop_planting (crop_id);

CREATE INDEX ix_crop_planting_farm_id ON crop_planting (farm_id);

CREATE INDEX ix_crop_planting_field_id ON crop_planting (field_id);

CREATE INDEX ix_crop_planting_tenant_id ON crop_planting (tenant_id);

CREATE TABLE daily_harvest (
    field_id UUID NOT NULL, 
    product_name VARCHAR NOT NULL, 
    total_quantity NUMERIC(12, 2) NOT NULL, 
    sellable_quantity NUMERIC(12, 2) NOT NULL, 
    waste_quantity NUMERIC(12, 2) NOT NULL, 
    unit VARCHAR(16) NOT NULL, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    inventory_item_id UUID, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_daily_harvest_farm_id ON daily_harvest (farm_id);

CREATE INDEX ix_daily_harvest_field_id ON daily_harvest (field_id);

CREATE INDEX ix_daily_harvest_tenant_id ON daily_harvest (tenant_id);

CREATE TABLE egg_record (
    flock_id VARCHAR(64) NOT NULL, 
    total_eggs INTEGER NOT NULL, 
    sellable_eggs INTEGER NOT NULL, 
    broken_eggs INTEGER NOT NULL, 
    consumed INTEGER NOT NULL, 
    hatched INTEGER NOT NULL, 
    wasted INTEGER NOT NULL, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_egg_record_farm_id ON egg_record (farm_id);

CREATE INDEX ix_egg_record_tenant_id ON egg_record (tenant_id);

CREATE TABLE harvest_record (
    field_id UUID NOT NULL, 
    product_name VARCHAR NOT NULL, 
    quantity NUMERIC(12, 2) NOT NULL, 
    unit VARCHAR(16) NOT NULL, 
    waste_qty NUMERIC(12, 2) NOT NULL, 
    destination VARCHAR, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_harvest_record_farm_id ON harvest_record (farm_id);

CREATE INDEX ix_harvest_record_field_id ON harvest_record (field_id);

CREATE INDEX ix_harvest_record_tenant_id ON harvest_record (tenant_id);

CREATE TABLE milk_record (
    animal_id UUID NOT NULL, 
    session VARCHAR(16) NOT NULL, 
    liters NUMERIC(8, 2) NOT NULL, 
    quality_status VARCHAR(32) NOT NULL, 
    destination VARCHAR(32) NOT NULL, 
    recorded_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    recorded_by UUID, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_milk_record_animal_id ON milk_record (animal_id);

CREATE INDEX ix_milk_record_farm_id ON milk_record (farm_id);

CREATE INDEX ix_milk_record_tenant_id ON milk_record (tenant_id);

CREATE TABLE observation (
    entity_type VARCHAR(32) NOT NULL, 
    entity_id VARCHAR(64) NOT NULL, 
    observation_type VARCHAR(64) NOT NULL, 
    quality VARCHAR(32) NOT NULL, 
    confidence NUMERIC(4, 3) NOT NULL, 
    value_numeric NUMERIC(12, 3), 
    value_text VARCHAR, 
    unit VARCHAR, 
    severity VARCHAR, 
    observed_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    observer_id UUID NOT NULL, 
    notes VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_observation_farm_id ON observation (farm_id);

CREATE INDEX ix_observation_tenant_id ON observation (tenant_id);

CREATE TABLE treatment (
    entity_type VARCHAR(32) NOT NULL, 
    entity_id VARCHAR(64) NOT NULL, 
    diagnosis VARCHAR, 
    medication VARCHAR NOT NULL, 
    dose VARCHAR NOT NULL, 
    route VARCHAR NOT NULL, 
    start_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    end_at TIMESTAMP WITH TIME ZONE, 
    withdrawal_until TIMESTAMP WITH TIME ZONE, 
    vet_id UUID, 
    responsible_user_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    cost NUMERIC(12, 2), 
    notes VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_treatment_farm_id ON treatment (farm_id);

CREATE INDEX ix_treatment_tenant_id ON treatment (tenant_id);

ALTER TABLE crop ENABLE ROW LEVEL SECURITY;

ALTER TABLE crop FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_crop
        ON crop
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE crop_planting ENABLE ROW LEVEL SECURITY;

ALTER TABLE crop_planting FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_crop_planting
        ON crop_planting
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE daily_harvest ENABLE ROW LEVEL SECURITY;

ALTER TABLE daily_harvest FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_daily_harvest
        ON daily_harvest
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE egg_record ENABLE ROW LEVEL SECURITY;

ALTER TABLE egg_record FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_egg_record
        ON egg_record
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE harvest_record ENABLE ROW LEVEL SECURITY;

ALTER TABLE harvest_record FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_harvest_record
        ON harvest_record
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE milk_record ENABLE ROW LEVEL SECURITY;

ALTER TABLE milk_record FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_milk_record
        ON milk_record
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE observation ENABLE ROW LEVEL SECURITY;

ALTER TABLE observation FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_observation
        ON observation
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE treatment ENABLE ROW LEVEL SECURITY;

ALTER TABLE treatment FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_treatment
        ON treatment
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE field ADD COLUMN crop_type VARCHAR;

ALTER TABLE field ADD COLUMN area_value NUMERIC(10, 3);

ALTER TABLE field ADD COLUMN area_unit VARCHAR(16);

ALTER TABLE field ADD COLUMN stage VARCHAR;

ALTER TABLE field ADD COLUMN expected_harvest_date TIMESTAMP WITH TIME ZONE;

ALTER TABLE field ADD COLUMN est_yield_kg NUMERIC(10, 2);

ALTER TABLE field ADD COLUMN field_code VARCHAR(32);

ALTER TABLE field ADD COLUMN location_label VARCHAR;

ALTER TABLE field ADD COLUMN soil_type VARCHAR;

ALTER TABLE field ADD COLUMN irrigation_method VARCHAR;

ALTER TABLE field ADD COLUMN status VARCHAR(32) DEFAULT 'active' NOT NULL;

ALTER TABLE field ADD COLUMN notes VARCHAR;

ALTER TABLE field DROP COLUMN area_hectares;

ALTER TABLE field DROP COLUMN crop;

ALTER TABLE field DROP COLUMN attributes;

ALTER TABLE inventory_item ADD COLUMN category VARCHAR;

ALTER TABLE inventory_item ADD COLUMN current_qty NUMERIC(14, 3) DEFAULT '0' NOT NULL;

ALTER TABLE inventory_item ADD COLUMN reorder_level NUMERIC(14, 3) DEFAULT '0' NOT NULL;

ALTER TABLE inventory_item ADD COLUMN supplier_label VARCHAR;

ALTER TABLE inventory_item ADD COLUMN unit_cost NUMERIC(12, 2);

ALTER TABLE inventory_item ADD COLUMN last_purchase TIMESTAMP WITH TIME ZONE;

ALTER TABLE inventory_item DROP COLUMN quantity_on_hand;

ALTER TABLE inventory_item DROP COLUMN sku;

ALTER TABLE inventory_movement ADD COLUMN linked_entity_type VARCHAR;

ALTER TABLE inventory_movement ADD COLUMN linked_entity_id VARCHAR;

UPDATE alembic_version SET version_num='de1b1a212f04' WHERE alembic_version.version_num = 'f6f362c79150';

-- Running upgrade de1b1a212f04 -> 9bcd61fb309c

CREATE TABLE expense (
    supplier_id UUID, 
    category VARCHAR(64) NOT NULL, 
    amount NUMERIC(12, 2) NOT NULL, 
    currency VARCHAR(3) NOT NULL, 
    linked_entity_type VARCHAR, 
    linked_entity_id VARCHAR, 
    incurred_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_expense_farm_id ON expense (farm_id);

CREATE INDEX ix_expense_tenant_id ON expense (tenant_id);

CREATE TABLE recommendation (
    category VARCHAR(32) NOT NULL, 
    priority VARCHAR(16) NOT NULL, 
    title VARCHAR NOT NULL, 
    entity_type VARCHAR, 
    entity_id VARCHAR, 
    entity_label VARCHAR, 
    confidence NUMERIC(4, 3) NOT NULL, 
    rationale VARCHAR NOT NULL, 
    suggested_action VARCHAR NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    rule_id VARCHAR, 
    generated_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    evidence JSONB NOT NULL, 
    decided_by UUID, 
    decided_at TIMESTAMP WITH TIME ZONE, 
    decision_note VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_recommendation_farm_id ON recommendation (farm_id);

CREATE INDEX ix_recommendation_tenant_id ON recommendation (tenant_id);

CREATE TABLE sale (
    customer_id UUID, 
    product_type VARCHAR(64) NOT NULL, 
    product_label VARCHAR, 
    quantity NUMERIC(12, 3), 
    unit VARCHAR(16), 
    amount NUMERIC(12, 2) NOT NULL, 
    currency VARCHAR(3) NOT NULL, 
    payment_status VARCHAR(32) NOT NULL, 
    sold_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_sale_farm_id ON sale (farm_id);

CREATE INDEX ix_sale_tenant_id ON sale (tenant_id);

ALTER TABLE expense ENABLE ROW LEVEL SECURITY;

ALTER TABLE expense FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_expense
        ON expense
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE recommendation ENABLE ROW LEVEL SECURITY;

ALTER TABLE recommendation FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_recommendation
        ON recommendation
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE sale ENABLE ROW LEVEL SECURITY;

ALTER TABLE sale FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_sale
        ON sale
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

UPDATE alembic_version SET version_num='9bcd61fb309c' WHERE alembic_version.version_num = 'de1b1a212f04';

-- Running upgrade 9bcd61fb309c -> 90a7c8cee8bc

CREATE TABLE mouneh_batch_input_consumption (
    batch_id UUID NOT NULL, 
    material_id VARCHAR(64) NOT NULL, 
    planned_qty NUMERIC(12, 3) NOT NULL, 
    actual_qty NUMERIC(12, 3), 
    unit_cost NUMERIC(12, 4) NOT NULL, 
    total_cost NUMERIC(12, 4), 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_batch_input_consumption_batch_id ON mouneh_batch_input_consumption (batch_id);

CREATE INDEX ix_mouneh_batch_input_consumption_farm_id ON mouneh_batch_input_consumption (farm_id);

CREATE INDEX ix_mouneh_batch_input_consumption_tenant_id ON mouneh_batch_input_consumption (tenant_id);

CREATE TABLE mouneh_cost_component (
    recipe_id UUID, 
    product_id UUID, 
    batch_id UUID, 
    label VARCHAR NOT NULL, 
    cost_type VARCHAR(32) NOT NULL, 
    calculation_method VARCHAR(32) NOT NULL, 
    quantity NUMERIC(12, 3), 
    unit_cost NUMERIC(12, 4), 
    amount NUMERIC(12, 2), 
    allocation_basis VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_cost_component_batch_id ON mouneh_cost_component (batch_id);

CREATE INDEX ix_mouneh_cost_component_farm_id ON mouneh_cost_component (farm_id);

CREATE INDEX ix_mouneh_cost_component_recipe_id ON mouneh_cost_component (recipe_id);

CREATE INDEX ix_mouneh_cost_component_tenant_id ON mouneh_cost_component (tenant_id);

CREATE TABLE mouneh_finished_goods_stock (
    batch_id UUID NOT NULL, 
    product_id UUID NOT NULL, 
    quantity_produced NUMERIC(12, 3) NOT NULL, 
    quantity_available NUMERIC(12, 3) NOT NULL, 
    quantity_reserved NUMERIC(12, 3) NOT NULL, 
    quantity_sold NUMERIC(12, 3) NOT NULL, 
    quantity_damaged NUMERIC(12, 3) NOT NULL, 
    quantity_expired NUMERIC(12, 3) NOT NULL, 
    unit_cost NUMERIC(12, 4) NOT NULL, 
    expiry_date TIMESTAMP WITH TIME ZONE, 
    warehouse_location VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_finished_goods_stock_batch_id ON mouneh_finished_goods_stock (batch_id);

CREATE INDEX ix_mouneh_finished_goods_stock_farm_id ON mouneh_finished_goods_stock (farm_id);

CREATE INDEX ix_mouneh_finished_goods_stock_product_id ON mouneh_finished_goods_stock (product_id);

CREATE INDEX ix_mouneh_finished_goods_stock_tenant_id ON mouneh_finished_goods_stock (tenant_id);

CREATE TABLE mouneh_product (
    name VARCHAR NOT NULL, 
    category VARCHAR(64) NOT NULL, 
    photo_path VARCHAR, 
    output_unit VARCHAR(32) NOT NULL, 
    custom_output_unit_label VARCHAR, 
    default_batch_size NUMERIC(12, 3) NOT NULL, 
    shelf_life_days INTEGER, 
    warehouse_rules VARCHAR, 
    low_stock_threshold NUMERIC(12, 3), 
    target_price NUMERIC(12, 2), 
    wholesale_price NUMERIC(12, 2), 
    target_margin_pct NUMERIC(5, 2), 
    status VARCHAR(32) NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_product_farm_id ON mouneh_product (farm_id);

CREATE INDEX ix_mouneh_product_tenant_id ON mouneh_product (tenant_id);

CREATE TABLE mouneh_production_batch (
    product_id UUID NOT NULL, 
    recipe_version_id UUID NOT NULL, 
    batch_code VARCHAR(64) NOT NULL, 
    planned_qty NUMERIC(12, 3) NOT NULL, 
    actual_output_qty NUMERIC(12, 3), 
    waste_qty NUMERIC(12, 3) NOT NULL, 
    damaged_qty NUMERIC(12, 3) NOT NULL, 
    quality_status VARCHAR(32) NOT NULL, 
    expiry_date TIMESTAMP WITH TIME ZONE, 
    warehouse_location VARCHAR, 
    status VARCHAR(32) NOT NULL, 
    planned_unit_cost NUMERIC(12, 4), 
    planned_total_cost NUMERIC(12, 4), 
    actual_unit_cost NUMERIC(12, 4), 
    actual_total_cost NUMERIC(12, 4), 
    labor_hours NUMERIC(8, 2), 
    started_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    notes VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_production_batch_farm_id ON mouneh_production_batch (farm_id);

CREATE INDEX ix_mouneh_production_batch_product_id ON mouneh_production_batch (product_id);

CREATE INDEX ix_mouneh_production_batch_tenant_id ON mouneh_production_batch (tenant_id);

CREATE TABLE mouneh_recipe (
    product_id UUID NOT NULL, 
    version INTEGER NOT NULL, 
    effective_from TIMESTAMP WITH TIME ZONE NOT NULL, 
    basis_quantity NUMERIC(12, 3) NOT NULL, 
    basis_unit VARCHAR(32) NOT NULL, 
    active BOOLEAN NOT NULL, 
    notes VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_recipe_farm_id ON mouneh_recipe (farm_id);

CREATE INDEX ix_mouneh_recipe_product_id ON mouneh_recipe (product_id);

CREATE INDEX ix_mouneh_recipe_tenant_id ON mouneh_recipe (tenant_id);

CREATE TABLE mouneh_recipe_item (
    recipe_id UUID NOT NULL, 
    material_id VARCHAR(64) NOT NULL, 
    material_type VARCHAR(32) NOT NULL, 
    quantity NUMERIC(12, 3) NOT NULL, 
    unit VARCHAR(16) NOT NULL, 
    loss_percent NUMERIC(5, 2) NOT NULL, 
    is_optional BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_recipe_item_farm_id ON mouneh_recipe_item (farm_id);

CREATE INDEX ix_mouneh_recipe_item_recipe_id ON mouneh_recipe_item (recipe_id);

CREATE INDEX ix_mouneh_recipe_item_tenant_id ON mouneh_recipe_item (tenant_id);

CREATE TABLE mouneh_sale (
    product_id UUID NOT NULL, 
    batch_id UUID NOT NULL, 
    finished_goods_stock_id UUID NOT NULL, 
    quantity NUMERIC(12, 3) NOT NULL, 
    unit_price NUMERIC(12, 2) NOT NULL, 
    discount NUMERIC(12, 2) NOT NULL, 
    customer_id UUID, 
    channel VARCHAR(32) NOT NULL, 
    cost_per_unit NUMERIC(12, 4) NOT NULL, 
    revenue NUMERIC(12, 2) NOT NULL, 
    margin NUMERIC(12, 2) NOT NULL, 
    sold_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_mouneh_sale_farm_id ON mouneh_sale (farm_id);

CREATE INDEX ix_mouneh_sale_product_id ON mouneh_sale (product_id);

CREATE INDEX ix_mouneh_sale_tenant_id ON mouneh_sale (tenant_id);

CREATE TABLE raw_material (
    name VARCHAR NOT NULL, 
    category VARCHAR(64) NOT NULL, 
    source_type VARCHAR(32) NOT NULL, 
    inventory_item_id UUID, 
    unit VARCHAR(16) NOT NULL, 
    default_unit_cost NUMERIC(12, 4) NOT NULL, 
    stock_tracking_enabled BOOLEAN NOT NULL, 
    current_stock NUMERIC(14, 3) NOT NULL, 
    loss_percent_default NUMERIC(5, 2) NOT NULL, 
    active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_raw_material_farm_id ON raw_material (farm_id);

CREATE INDEX ix_raw_material_tenant_id ON raw_material (tenant_id);

ALTER TABLE raw_material ENABLE ROW LEVEL SECURITY;

ALTER TABLE raw_material FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_raw_material
        ON raw_material
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_product ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_product FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_product
        ON mouneh_product
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_recipe ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_recipe FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_recipe
        ON mouneh_recipe
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_recipe_item ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_recipe_item FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_recipe_item
        ON mouneh_recipe_item
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_cost_component ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_cost_component FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_cost_component
        ON mouneh_cost_component
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_production_batch ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_production_batch FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_production_batch
        ON mouneh_production_batch
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_batch_input_consumption ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_batch_input_consumption FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_batch_input_consumption
        ON mouneh_batch_input_consumption
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_finished_goods_stock ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_finished_goods_stock FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_finished_goods_stock
        ON mouneh_finished_goods_stock
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE mouneh_sale ENABLE ROW LEVEL SECURITY;

ALTER TABLE mouneh_sale FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_mouneh_sale
        ON mouneh_sale
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

UPDATE alembic_version SET version_num='90a7c8cee8bc' WHERE alembic_version.version_num = '9bcd61fb309c';

-- Running upgrade 90a7c8cee8bc -> cbd6ac9cefaf

CREATE TABLE visit_activity (
    name VARCHAR NOT NULL, 
    activity_type VARCHAR(32) NOT NULL, 
    price NUMERIC(10, 2) NOT NULL, 
    capacity_per_slot INTEGER NOT NULL, 
    duration_minutes INTEGER, 
    requires_staff_role VARCHAR, 
    requires_animal_id VARCHAR, 
    welfare_limit_json JSONB, 
    active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_activity_farm_id ON visit_activity (farm_id);

CREATE INDEX ix_visit_activity_tenant_id ON visit_activity (tenant_id);

CREATE TABLE visit_booking (
    visitor_id UUID NOT NULL, 
    session_id UUID NOT NULL, 
    package_id UUID NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    adults INTEGER NOT NULL, 
    children INTEGER NOT NULL, 
    total_amount NUMERIC(10, 2) NOT NULL, 
    deposit_amount NUMERIC(10, 2) NOT NULL, 
    balance_due NUMERIC(10, 2) NOT NULL, 
    source VARCHAR(32) NOT NULL, 
    payment_method VARCHAR, 
    notes VARCHAR, 
    confirmed_at TIMESTAMP WITH TIME ZONE, 
    checked_in_at TIMESTAMP WITH TIME ZONE, 
    completed_at TIMESTAMP WITH TIME ZONE, 
    cancelled_at TIMESTAMP WITH TIME ZONE, 
    idempotency_key VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_booking_farm_id ON visit_booking (farm_id);

CREATE INDEX ix_visit_booking_idempotency_key ON visit_booking (idempotency_key);

CREATE INDEX ix_visit_booking_package_id ON visit_booking (package_id);

CREATE INDEX ix_visit_booking_session_id ON visit_booking (session_id);

CREATE INDEX ix_visit_booking_tenant_id ON visit_booking (tenant_id);

CREATE INDEX ix_visit_booking_visitor_id ON visit_booking (visitor_id);

CREATE TABLE visit_booking_activity (
    booking_id UUID NOT NULL, 
    activity_id UUID NOT NULL, 
    quantity INTEGER NOT NULL, 
    unit_price NUMERIC(10, 2) NOT NULL, 
    total_price NUMERIC(10, 2) NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_booking_activity_booking_id ON visit_booking_activity (booking_id);

CREATE INDEX ix_visit_booking_activity_farm_id ON visit_booking_activity (farm_id);

CREATE INDEX ix_visit_booking_activity_tenant_id ON visit_booking_activity (tenant_id);

CREATE TABLE visit_cost (
    session_id UUID NOT NULL, 
    category VARCHAR(64) NOT NULL, 
    description VARCHAR, 
    amount NUMERIC(10, 2) NOT NULL, 
    allocation_method VARCHAR(32) NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_cost_farm_id ON visit_cost (farm_id);

CREATE INDEX ix_visit_cost_session_id ON visit_cost (session_id);

CREATE INDEX ix_visit_cost_tenant_id ON visit_cost (tenant_id);

CREATE TABLE visit_incident (
    session_id UUID NOT NULL, 
    booking_id UUID, 
    incident_type VARCHAR(64) NOT NULL, 
    severity VARCHAR(16) NOT NULL, 
    description VARCHAR NOT NULL, 
    action_taken VARCHAR, 
    created_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_incident_farm_id ON visit_incident (farm_id);

CREATE INDEX ix_visit_incident_session_id ON visit_incident (session_id);

CREATE INDEX ix_visit_incident_tenant_id ON visit_incident (tenant_id);

CREATE TABLE visit_opening_calendar_day (
    weekday INTEGER NOT NULL, 
    is_open BOOLEAN NOT NULL, 
    open_time VARCHAR, 
    close_time VARCHAR, 
    default_capacity INTEGER NOT NULL, 
    notes VARCHAR, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_opening_calendar_day_farm_id ON visit_opening_calendar_day (farm_id);

CREATE INDEX ix_visit_opening_calendar_day_tenant_id ON visit_opening_calendar_day (tenant_id);

CREATE TABLE visit_package (
    name VARCHAR NOT NULL, 
    description VARCHAR, 
    base_price NUMERIC(10, 2) NOT NULL, 
    currency VARCHAR(3) NOT NULL, 
    duration_minutes INTEGER, 
    included_items_json JSONB NOT NULL, 
    active BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_package_farm_id ON visit_package (farm_id);

CREATE INDEX ix_visit_package_tenant_id ON visit_package (tenant_id);

CREATE TABLE visit_retail_sale (
    booking_id UUID, 
    visitor_id UUID, 
    sale_id UUID NOT NULL, 
    channel VARCHAR(32) NOT NULL, 
    total_amount NUMERIC(10, 2) NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_retail_sale_farm_id ON visit_retail_sale (farm_id);

CREATE INDEX ix_visit_retail_sale_tenant_id ON visit_retail_sale (tenant_id);

CREATE TABLE visit_session (
    date DATE NOT NULL, 
    start_time VARCHAR(8) NOT NULL, 
    end_time VARCHAR(8) NOT NULL, 
    capacity INTEGER NOT NULL, 
    status VARCHAR(32) NOT NULL, 
    weather_note VARCHAR, 
    expected_staff_cost NUMERIC(10, 2), 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_session_farm_id ON visit_session (farm_id);

CREATE INDEX ix_visit_session_tenant_id ON visit_session (tenant_id);

CREATE TABLE visit_staff_roster (
    session_id UUID NOT NULL, 
    worker_id UUID NOT NULL, 
    role VARCHAR(64) NOT NULL, 
    start_time VARCHAR(8) NOT NULL, 
    end_time VARCHAR(8) NOT NULL, 
    hourly_rate NUMERIC(10, 2) NOT NULL, 
    total_cost NUMERIC(10, 2), 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visit_staff_roster_farm_id ON visit_staff_roster (farm_id);

CREATE INDEX ix_visit_staff_roster_session_id ON visit_staff_roster (session_id);

CREATE INDEX ix_visit_staff_roster_tenant_id ON visit_staff_roster (tenant_id);

CREATE TABLE visitor_feedback (
    booking_id UUID NOT NULL, 
    rating INTEGER NOT NULL, 
    comments VARCHAR, 
    would_return BOOLEAN, 
    submitted_at TIMESTAMP WITH TIME ZONE NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visitor_feedback_booking_id ON visitor_feedback (booking_id);

CREATE INDEX ix_visitor_feedback_farm_id ON visitor_feedback (farm_id);

CREATE INDEX ix_visitor_feedback_tenant_id ON visitor_feedback (tenant_id);

CREATE TABLE visitor_profile (
    full_name VARCHAR NOT NULL, 
    phone VARCHAR, 
    email VARCHAR, 
    preferred_language VARCHAR(8) NOT NULL, 
    notes VARCHAR, 
    consent_marketing BOOLEAN NOT NULL, 
    id UUID NOT NULL, 
    tenant_id UUID NOT NULL, 
    farm_id UUID, 
    version INTEGER NOT NULL, 
    deleted_at TIMESTAMP WITH TIME ZONE, 
    origin_device_id UUID, 
    last_modified_by UUID, 
    created_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT now() NOT NULL, 
    PRIMARY KEY (id)
);

CREATE INDEX ix_visitor_profile_farm_id ON visitor_profile (farm_id);

CREATE INDEX ix_visitor_profile_tenant_id ON visitor_profile (tenant_id);

ALTER TABLE visit_activity ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_activity FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_activity
        ON visit_activity
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_package ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_package FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_package
        ON visit_package
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visitor_profile ENABLE ROW LEVEL SECURITY;

ALTER TABLE visitor_profile FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visitor_profile
        ON visitor_profile
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_session ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_session FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_session
        ON visit_session
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_booking ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_booking FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_booking
        ON visit_booking
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_booking_activity ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_booking_activity FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_booking_activity
        ON visit_booking_activity
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_cost ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_cost FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_cost
        ON visit_cost
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_incident ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_incident FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_incident
        ON visit_incident
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_staff_roster ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_staff_roster FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_staff_roster
        ON visit_staff_roster
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visitor_feedback ENABLE ROW LEVEL SECURITY;

ALTER TABLE visitor_feedback FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visitor_feedback
        ON visitor_feedback
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_retail_sale ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_retail_sale FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_retail_sale
        ON visit_retail_sale
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

ALTER TABLE visit_opening_calendar_day ENABLE ROW LEVEL SECURITY;

ALTER TABLE visit_opening_calendar_day FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation_visit_opening_calendar_day
        ON visit_opening_calendar_day
        USING (tenant_id = app_current_tenant_id())
        WITH CHECK (tenant_id = app_current_tenant_id());

UPDATE alembic_version SET version_num='cbd6ac9cefaf' WHERE alembic_version.version_num = '90a7c8cee8bc';

COMMIT;

