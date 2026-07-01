CREATE EXTENSION IF NOT EXISTS timescaledb;

CREATE TABLE raw_logs (
    id BIGSERIAL NOT NULL,
    log_type VARCHAR(50) NOT NULL DEFAULT 'web',
    timestamp TIMESTAMPTZ NOT NULL,
    ip INET,
    method VARCHAR(10),
    endpoint VARCHAR(500),
    status INTEGER,
    response_time_ms FLOAT,
    user_agent TEXT,
    data JSONB NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    PRIMARY KEY (id, timestamp)
);

SELECT create_hypertable('raw_logs', 'timestamp');

CREATE INDEX idx_raw_logs_timestamp ON raw_logs(timestamp DESC);
CREATE INDEX idx_raw_logs_type ON raw_logs(log_type);
CREATE INDEX idx_raw_logs_ip ON raw_logs(ip);
CREATE INDEX idx_raw_logs_status ON raw_logs(status);
CREATE INDEX idx_raw_logs_data_gin ON raw_logs USING GIN(data);

CREATE TABLE alerts (
    id SERIAL PRIMARY KEY,
    alert_type VARCHAR(100) NOT NULL,
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('LOW', 'MEDIUM', 'HIGH', 'CRITICAL')),
    source VARCHAR(50) NOT NULL CHECK (source IN ('rule', 'ml', 'hybrid')),
    confidence FLOAT CHECK (confidence >= 0 AND confidence <= 1),
    description TEXT,
    log_ids INTEGER[],
    ip INET,
    timestamp TIMESTAMPTZ NOT NULL,
    metadata JSONB,
    incident_status VARCHAR(20) NOT NULL DEFAULT 'NEW' CHECK (incident_status IN ('NEW', 'INVESTIGATING', 'RESOLVED')),
    incident_owner VARCHAR(100),
    incident_notes TEXT,
    incident_updated_at TIMESTAMPTZ DEFAULT NOW(),
    incident_updated_by VARCHAR(100),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_alerts_timestamp ON alerts(timestamp DESC);
CREATE INDEX idx_alerts_severity ON alerts(severity);
CREATE INDEX idx_alerts_type ON alerts(alert_type);
CREATE INDEX idx_alerts_ip ON alerts(ip);
CREATE INDEX idx_alerts_source ON alerts(source);
CREATE INDEX idx_alerts_incident_status ON alerts(incident_status);
CREATE INDEX idx_alerts_incident_owner ON alerts(incident_owner);

CREATE TABLE ml_predictions (
    id SERIAL PRIMARY KEY,
    log_id INTEGER,
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50),
    anomaly_score FLOAT NOT NULL,
    is_anomaly BOOLEAN NOT NULL,
    features JSONB,
    shap_values JSONB,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_ml_predictions_log_id ON ml_predictions(log_id);
CREATE INDEX idx_ml_predictions_model ON ml_predictions(model_name, model_version);
CREATE INDEX idx_ml_predictions_is_anomaly ON ml_predictions(is_anomaly);
CREATE INDEX idx_ml_predictions_created_at ON ml_predictions(created_at DESC);

CREATE TABLE model_runs (
    id SERIAL PRIMARY KEY,
    run_id VARCHAR(100) UNIQUE NOT NULL,
    model_name VARCHAR(100) NOT NULL,
    model_version VARCHAR(50),
    hyperparameters JSONB,
    metrics JSONB,
    artifacts_path VARCHAR(500),
    status VARCHAR(50) CHECK (status IN ('RUNNING', 'COMPLETED', 'FAILED')),
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_model_runs_run_id ON model_runs(run_id);
CREATE INDEX idx_model_runs_model ON model_runs(model_name);
CREATE INDEX idx_model_runs_status ON model_runs(status);
CREATE INDEX idx_model_runs_created_at ON model_runs(created_at DESC);

CREATE TABLE feedback (
    id SERIAL PRIMARY KEY,
    alert_id INTEGER REFERENCES alerts(id) ON DELETE CASCADE,
    user_id VARCHAR(100),
    label VARCHAR(50) CHECK (label IN ('true_positive', 'false_positive', 'false_negative')),
    reason TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_feedback_alert_id ON feedback(alert_id);
CREATE INDEX idx_feedback_label ON feedback(label);
CREATE INDEX idx_feedback_created_at ON feedback(created_at DESC);

CREATE TABLE hybrid_scores (
    log_id BIGINT PRIMARY KEY,
    rule_score FLOAT NOT NULL CHECK (rule_score >= 0 AND rule_score <= 1),
    ml_score FLOAT NOT NULL CHECK (ml_score >= 0 AND ml_score <= 1),
    final_score FLOAT NOT NULL CHECK (final_score >= 0 AND final_score <= 1),
    severity VARCHAR(20) NOT NULL CHECK (severity IN ('NORMAL', 'MEDIUM', 'HIGH', 'CRITICAL')),
    triggered_rules TEXT[] DEFAULT ARRAY[]::TEXT[],
    ml_confidence FLOAT CHECK (ml_confidence >= 0 AND ml_confidence <= 1),
    is_anomaly BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX idx_hybrid_scores_final_score ON hybrid_scores(final_score DESC);
CREATE INDEX idx_hybrid_scores_severity ON hybrid_scores(severity);
CREATE INDEX idx_hybrid_scores_is_anomaly ON hybrid_scores(is_anomaly);
CREATE INDEX idx_hybrid_scores_created_at ON hybrid_scores(created_at DESC);


