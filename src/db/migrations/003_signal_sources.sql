CREATE TABLE IF NOT EXISTS signal_sources (
    id SERIAL PRIMARY KEY,
    scrape_date DATE NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    source VARCHAR(20) NOT NULL,
    reason VARCHAR(50),
    score NUMERIC(5,3),
    evidence_json JSONB,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(scrape_date, ticker, source)
);
CREATE INDEX IF NOT EXISTS idx_signal_sources_date ON signal_sources(scrape_date);
