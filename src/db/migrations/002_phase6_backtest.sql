-- Phase 6: Backtest tables

CREATE TABLE IF NOT EXISTS backtest_runs (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    start_date DATE NOT NULL,
    end_date DATE NOT NULL,
    status VARCHAR(50) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW(),
    completed_at TIMESTAMP,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS backtest_daily_results (
    id SERIAL PRIMARY KEY,
    run_id INTEGER REFERENCES backtest_runs(id) ON DELETE CASCADE,
    trade_date DATE NOT NULL,
    llm_name VARCHAR(50) NOT NULL,
    is_real BOOLEAN NOT NULL,
    invested NUMERIC(12,2) DEFAULT 0,
    value NUMERIC(12,2) DEFAULT 0,
    realized_pnl NUMERIC(12,2) DEFAULT 0,
    unrealized_pnl NUMERIC(12,2) DEFAULT 0,
    trades_json JSONB,
    created_at TIMESTAMP DEFAULT NOW()
);
