CREATE TABLE user_account (
    user_id UUID PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    display_name VARCHAR(100) NOT NULL,
    password_hash VARCHAR(100) NOT NULL,
    global_role VARCHAR(20) NOT NULL CHECK (global_role IN ('SYSTEM_ADMIN', 'NONE')),
    status VARCHAR(20) NOT NULL CHECK (status IN ('ACTIVE', 'DISABLED')),
    must_change_password BOOLEAN NOT NULL DEFAULT TRUE,
    failed_login_attempts INTEGER NOT NULL DEFAULT 0 CHECK (failed_login_attempts >= 0),
    locked_until TIMESTAMP WITH TIME ZONE,
    credential_version BIGINT NOT NULL DEFAULT 1 CHECK (credential_version > 0),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (username = lower(username)),
    CHECK (username ~ '^[a-z0-9._-]{3,50}$')
);

CREATE TABLE project_membership (
    project_id UUID NOT NULL REFERENCES business_project(project_id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES user_account(user_id) ON DELETE CASCADE,
    project_role VARCHAR(20) NOT NULL CHECK (project_role IN ('MANAGER', 'ANALYST')),
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (project_id, user_id)
);

CREATE INDEX idx_project_membership_user ON project_membership(user_id, project_id);

ALTER TABLE audit_event ADD COLUMN actor_user_id UUID REFERENCES user_account(user_id);
ALTER TABLE audit_event ADD COLUMN project_id UUID;

UPDATE audit_event SET project_id = target_id WHERE target_type = 'PROJECT';
UPDATE audit_event e SET project_id = b.project_id
  FROM import_batch b
 WHERE e.target_type = 'IMPORT_BATCH' AND e.target_id = b.batch_id;
UPDATE audit_event e SET project_id = b.project_id
  FROM analysis_run r JOIN import_batch b ON b.batch_id = r.batch_id
 WHERE e.target_type = 'ANALYSIS_RUN' AND e.target_id = r.run_id;
UPDATE audit_event e SET project_id = b.project_id
  FROM alarm_record a JOIN import_batch b ON b.batch_id = a.batch_id
 WHERE e.target_type = 'ALARM_RECORD' AND e.target_id = a.record_id;

ALTER TABLE audit_event ALTER COLUMN target_id DROP NOT NULL;
ALTER TABLE audit_event DROP CONSTRAINT audit_event_event_type_check;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_event_type_check CHECK (event_type IN (
    'IMPORT_CREATED', 'IMPORT_REJECTED', 'IMPORT_CONFIRMED',
    'ANALYSIS_STARTED', 'ANALYSIS_COMPLETED', 'ANALYSIS_FAILED',
    'RESULT_OVERRIDDEN', 'DISPOSITION_CHANGED', 'REPORT_EXPORTED',
    'PROJECT_CREATED', 'PROJECT_UPDATED', 'PROJECT_ARCHIVED', 'PROJECT_RESTORED', 'PROJECT_DELETED',
    'MANUAL_ALARM_CREATED', 'MANUAL_ALARM_UPDATED', 'MANUAL_ALARM_INVALIDATED',
    'LOGIN_SUCCEEDED', 'LOGIN_FAILED', 'LOGOUT', 'PASSWORD_CHANGED',
    'USER_CREATED', 'USER_UPDATED', 'USER_PASSWORD_RESET',
    'PROJECT_MEMBER_ADDED', 'PROJECT_MEMBER_UPDATED', 'PROJECT_MEMBER_REMOVED', 'DEMO_RESET'
));
ALTER TABLE audit_event DROP CONSTRAINT audit_event_target_type_check;
ALTER TABLE audit_event ADD CONSTRAINT audit_event_target_type_check CHECK (target_type IN (
    'IMPORT_BATCH', 'ANALYSIS_RUN', 'ALARM_RECORD', 'PROJECT', 'USER', 'SYSTEM'
));

CREATE INDEX idx_audit_event_project_occurred
    ON audit_event(project_id, occurred_at DESC, event_id DESC);
