CREATE TABLE alarm_disposition (
    run_id UUID NOT NULL,
    record_id UUID NOT NULL,
    status VARCHAR(20) NOT NULL CHECK (status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')),
    operator_name VARCHAR(100) NOT NULL,
    note VARCHAR(500) NOT NULL,
    updated_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    closed_at TIMESTAMP WITH TIME ZONE,
    PRIMARY KEY (run_id, record_id),
    FOREIGN KEY (run_id, record_id) REFERENCES analysis_result(run_id, record_id) ON DELETE CASCADE,
    CHECK ((status = 'CLOSED' AND closed_at IS NOT NULL) OR (status <> 'CLOSED' AND closed_at IS NULL))
);

CREATE TABLE disposition_history (
    history_id BIGSERIAL PRIMARY KEY,
    run_id UUID NOT NULL,
    record_id UUID NOT NULL,
    from_status VARCHAR(20) NOT NULL CHECK (from_status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')),
    to_status VARCHAR(20) NOT NULL CHECK (to_status IN ('OPEN', 'IN_PROGRESS', 'CLOSED')),
    operator_name VARCHAR(100) NOT NULL,
    note VARCHAR(500) NOT NULL,
    occurred_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (run_id, record_id) REFERENCES analysis_result(run_id, record_id) ON DELETE CASCADE
);

CREATE INDEX idx_disposition_history_record
    ON disposition_history(run_id, record_id, occurred_at, history_id);
