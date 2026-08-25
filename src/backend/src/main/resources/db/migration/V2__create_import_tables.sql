CREATE TABLE import_batch (
    batch_id UUID PRIMARY KEY,
    file_name VARCHAR(255) NOT NULL,
    file_format VARCHAR(10) NOT NULL CHECK (file_format IN ('CSV', 'TXT', 'XLSX')),
    status VARCHAR(20) NOT NULL CHECK (
        status IN ('UPLOADED', 'VALIDATING', 'READY', 'REJECTED', 'IMPORTED', 'ANALYZING', 'COMPLETED', 'FAILED')
    ),
    total_rows INTEGER NOT NULL CHECK (total_rows >= 0),
    valid_rows INTEGER NOT NULL CHECK (valid_rows >= 0),
    error_count INTEGER NOT NULL CHECK (error_count >= 0),
    headers JSONB NOT NULL,
    field_mapping JSONB NOT NULL,
    errors JSONB NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    imported_at TIMESTAMP WITH TIME ZONE
);

CREATE TABLE import_staging (
    record_id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batch(batch_id) ON DELETE CASCADE,
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    event_time TIMESTAMP WITH TIME ZONE NOT NULL,
    return_time TIMESTAMP WITH TIME ZONE,
    ack_time TIMESTAMP WITH TIME ZONE,
    site VARCHAR(100) NOT NULL,
    area VARCHAR(100) NOT NULL,
    unit_name VARCHAR(100),
    tag VARCHAR(120) NOT NULL,
    description VARCHAR(500) NOT NULL,
    priority VARCHAR(2) NOT NULL CHECK (priority IN ('P1', 'P2', 'P3', 'P4')),
    alarm_state VARCHAR(20) NOT NULL CHECK (alarm_state IN ('ACTIVE', 'RETURNED', 'ACKNOWLEDGED')),
    alarm_value NUMERIC,
    threshold NUMERIC,
    engineering_unit VARCHAR(40),
    source_system VARCHAR(100) NOT NULL,
    operator_name VARCHAR(100),
    raw_payload JSONB NOT NULL,
    UNIQUE (batch_id, source_row)
);

CREATE TABLE alarm_record (
    record_id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batch(batch_id),
    source_row INTEGER NOT NULL CHECK (source_row > 0),
    event_time TIMESTAMP WITH TIME ZONE NOT NULL,
    return_time TIMESTAMP WITH TIME ZONE,
    ack_time TIMESTAMP WITH TIME ZONE,
    site VARCHAR(100) NOT NULL,
    area VARCHAR(100) NOT NULL,
    unit_name VARCHAR(100),
    tag VARCHAR(120) NOT NULL,
    description VARCHAR(500) NOT NULL,
    priority VARCHAR(2) NOT NULL CHECK (priority IN ('P1', 'P2', 'P3', 'P4')),
    alarm_state VARCHAR(20) NOT NULL CHECK (alarm_state IN ('ACTIVE', 'RETURNED', 'ACKNOWLEDGED')),
    alarm_value NUMERIC,
    threshold NUMERIC,
    engineering_unit VARCHAR(40),
    source_system VARCHAR(100) NOT NULL,
    operator_name VARCHAR(100),
    raw_payload JSONB NOT NULL,
    imported_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (batch_id, source_row)
);

CREATE INDEX idx_alarm_record_batch_source_row ON alarm_record(batch_id, source_row);
