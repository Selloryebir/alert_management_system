CREATE TABLE analysis_run (
    run_id UUID PRIMARY KEY,
    batch_id UUID NOT NULL REFERENCES import_batch(batch_id),
    attempt INTEGER NOT NULL CHECK (attempt > 0),
    status VARCHAR(20) NOT NULL CHECK (status IN ('ANALYZING', 'COMPLETED', 'FAILED')),
    contract_version VARCHAR(20) NOT NULL,
    algorithm_version VARCHAR(40) NOT NULL,
    rule_version VARCHAR(40),
    parameters JSONB NOT NULL,
    summary JSONB,
    failure_reason VARCHAR(500),
    started_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TIMESTAMP WITH TIME ZONE,
    UNIQUE (batch_id, attempt)
);

CREATE TABLE analysis_result (
    run_id UUID NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    record_id UUID NOT NULL REFERENCES alarm_record(record_id),
    noise_type VARCHAR(20) NOT NULL CHECK (
        noise_type IN ('NORMAL', 'DUPLICATE', 'CHATTER', 'SHORT_LIVED', 'PERSISTENT')
    ),
    alarm_class VARCHAR(100) NOT NULL,
    cause_category VARCHAR(30) NOT NULL CHECK (
        cause_category IN ('PROCESS_DISTURBANCE', 'EQUIPMENT_FAULT', 'INSTRUMENT_ISSUE', 'MAINTENANCE_TEST', 'UNKNOWN')
    ),
    score NUMERIC NOT NULL,
    evidence JSONB NOT NULL,
    PRIMARY KEY (run_id, record_id)
);

CREATE TABLE event_chain (
    run_id UUID NOT NULL REFERENCES analysis_run(run_id) ON DELETE CASCADE,
    chain_id VARCHAR(100) NOT NULL,
    start_record_id UUID NOT NULL REFERENCES alarm_record(record_id),
    start_time TIMESTAMP WITH TIME ZONE NOT NULL,
    end_time TIMESTAMP WITH TIME ZONE NOT NULL,
    association_rule VARCHAR(200) NOT NULL,
    explanation VARCHAR(500) NOT NULL,
    PRIMARY KEY (run_id, chain_id),
    CHECK (end_time >= start_time)
);

CREATE TABLE event_chain_member (
    run_id UUID NOT NULL,
    chain_id VARCHAR(100) NOT NULL,
    member_order INTEGER NOT NULL CHECK (member_order >= 0),
    record_id UUID NOT NULL REFERENCES alarm_record(record_id),
    PRIMARY KEY (run_id, chain_id, member_order),
    UNIQUE (run_id, chain_id, record_id),
    FOREIGN KEY (run_id, chain_id) REFERENCES event_chain(run_id, chain_id) ON DELETE CASCADE
);

CREATE INDEX idx_analysis_run_batch ON analysis_run(batch_id, attempt);
