CREATE TABLE business_project (
    project_id UUID PRIMARY KEY,
    code VARCHAR(80) NOT NULL UNIQUE,
    name VARCHAR(160) NOT NULL UNIQUE,
    client_name VARCHAR(160) NOT NULL,
    site VARCHAR(100) NOT NULL,
    unit_name VARCHAR(100) NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('ACTIVE', 'ARCHIVED')),
    report_title VARCHAR(200) NOT NULL,
    report_fields JSONB NOT NULL,
    validation_rules JSONB NOT NULL DEFAULT '{"required_fields":[]}'::jsonb,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO business_project (
    project_id, code, name, client_name, site, unit_name, status, report_title, report_fields
) VALUES (
    '00000000-0000-0000-0000-000000000001',
    'DEFAULT-DEMO',
    '默认演示项目',
    '演示客户',
    '合成厂区',
    '演示装置',
    'ACTIVE',
    '报警分析报告',
    '["summary","priority","area","unit","noise","cause","disposition","chains"]'::jsonb
);

ALTER TABLE import_batch ADD COLUMN project_id UUID;
UPDATE import_batch SET project_id = '00000000-0000-0000-0000-000000000001';
ALTER TABLE import_batch ALTER COLUMN project_id SET NOT NULL;
ALTER TABLE import_batch
    ADD CONSTRAINT fk_import_batch_project
    FOREIGN KEY (project_id) REFERENCES business_project(project_id);
ALTER TABLE import_batch ADD COLUMN source_type VARCHAR(20) NOT NULL DEFAULT 'FILE'
    CHECK (source_type IN ('FILE', 'MANUAL_ENTRY'));
CREATE INDEX idx_import_batch_project_created
    ON import_batch(project_id, created_at DESC, batch_id DESC);

ALTER TABLE alarm_record ADD COLUMN invalidated_at TIMESTAMP WITH TIME ZONE;
ALTER TABLE alarm_record ADD COLUMN invalidated_by VARCHAR(100);
ALTER TABLE alarm_record ADD COLUMN invalidation_reason VARCHAR(500);
ALTER TABLE alarm_record ADD CONSTRAINT chk_alarm_record_invalidation CHECK (
    (invalidated_at IS NULL AND invalidated_by IS NULL AND invalidation_reason IS NULL)
    OR (invalidated_at IS NOT NULL AND invalidated_by IS NOT NULL AND invalidation_reason IS NOT NULL)
);

ALTER TABLE alarm_disposition ADD COLUMN assignee VARCHAR(100);
ALTER TABLE disposition_history ADD COLUMN assignee VARCHAR(100);

ALTER TABLE audit_event DROP CONSTRAINT audit_event_event_type_check;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_event_type_check CHECK (event_type IN (
    'IMPORT_CREATED', 'IMPORT_REJECTED', 'IMPORT_CONFIRMED',
    'ANALYSIS_STARTED', 'ANALYSIS_COMPLETED', 'ANALYSIS_FAILED',
    'RESULT_OVERRIDDEN', 'DISPOSITION_CHANGED', 'REPORT_EXPORTED',
    'PROJECT_CREATED', 'PROJECT_UPDATED', 'PROJECT_ARCHIVED', 'PROJECT_RESTORED', 'PROJECT_DELETED',
    'MANUAL_ALARM_CREATED', 'MANUAL_ALARM_UPDATED', 'MANUAL_ALARM_INVALIDATED'
));

ALTER TABLE audit_event ADD CONSTRAINT audit_event_target_type_check CHECK (target_type IN (
    'IMPORT_BATCH', 'ANALYSIS_RUN', 'ALARM_RECORD', 'PROJECT'
));
