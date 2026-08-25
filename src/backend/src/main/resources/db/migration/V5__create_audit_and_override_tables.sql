CREATE TABLE analysis_result_override (
    run_id UUID NOT NULL,
    record_id UUID NOT NULL,
    noise_type VARCHAR(20) NOT NULL CHECK (
        noise_type IN ('NORMAL', 'DUPLICATE', 'CHATTER', 'SHORT_LIVED', 'PERSISTENT')
    ),
    alarm_class VARCHAR(100) NOT NULL CHECK (alarm_class IN ('STANDARD', 'NUISANCE')),
    cause_category VARCHAR(30) NOT NULL CHECK (
        cause_category IN ('PROCESS_DISTURBANCE', 'EQUIPMENT_FAULT', 'INSTRUMENT_ISSUE', 'MAINTENANCE_TEST', 'UNKNOWN')
    ),
    operator_name VARCHAR(100) NOT NULL,
    reason VARCHAR(500) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (run_id, record_id),
    FOREIGN KEY (run_id, record_id) REFERENCES analysis_result(run_id, record_id) ON DELETE CASCADE
);

CREATE TABLE audit_event (
    event_id UUID PRIMARY KEY,
    event_type VARCHAR(40) NOT NULL CHECK (event_type IN (
        'IMPORT_CREATED', 'IMPORT_REJECTED', 'IMPORT_CONFIRMED',
        'ANALYSIS_STARTED', 'ANALYSIS_COMPLETED', 'ANALYSIS_FAILED',
        'RESULT_OVERRIDDEN', 'DISPOSITION_CHANGED', 'REPORT_EXPORTED'
    )),
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    operator_name VARCHAR(100) NOT NULL,
    target_type VARCHAR(30) NOT NULL,
    target_id UUID NOT NULL,
    result VARCHAR(10) NOT NULL CHECK (result IN ('SUCCESS', 'FAILURE')),
    trace_id UUID NOT NULL,
    details JSONB NOT NULL
);

CREATE INDEX idx_audit_event_occurred ON audit_event(occurred_at DESC, event_id DESC);
CREATE INDEX idx_audit_event_target ON audit_event(target_type, target_id, occurred_at DESC);
