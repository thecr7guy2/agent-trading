-- Phase 1: Initial schema for trading bot

CREATE TABLE IF NOT EXISTS llm_config (
    id SERIAL PRIMARY KEY,
    name VARCHAR(50) UNIQUE NOT NULL,
    api_provider VARCHAR(100) NOT NULL,
    is_active BOOLEAN DEFAULT true
);

CREATE TABLE IF NOT EXISTS daily_picks (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    pick_date DATE NOT NULL,
    is_main_trader BOOLEAN NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    exchange VARCHAR(50),
    allocation_pct DECIMAL(5,2),
    reasoning TEXT,
    confidence DECIMAL(3,2),
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(llm_name, pick_date, ticker)
);

CREATE TABLE IF NOT EXISTS trades (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    trade_date DATE NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    action VARCHAR(10) NOT NULL,
    quantity DECIMAL(12,4),
    price_per_share DECIMAL(12,4),
    total_cost DECIMAL(12,2),
    is_real BOOLEAN NOT NULL,
    broker_order_id VARCHAR(100),
    status VARCHAR(20) DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS positions (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    ticker VARCHAR(20) NOT NULL,
    quantity DECIMAL(12,4) NOT NULL,
    avg_buy_price DECIMAL(12,4) NOT NULL,
    is_real BOOLEAN NOT NULL,
    opened_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(llm_name, ticker, is_real)
);

CREATE TABLE IF NOT EXISTS portfolio_snapshots (
    id SERIAL PRIMARY KEY,
    llm_name VARCHAR(50) NOT NULL,
    snapshot_date DATE NOT NULL,
    total_invested DECIMAL(12,2),
    total_value DECIMAL(12,2),
    realized_pnl DECIMAL(12,2),
    unrealized_pnl DECIMAL(12,2),
    is_real BOOLEAN NOT NULL,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(llm_name, snapshot_date, is_real)
);

CREATE TABLE IF NOT EXISTS reddit_sentiment (
    id SERIAL PRIMARY KEY,
    ticker VARCHAR(20) NOT NULL,
    scrape_date DATE NOT NULL,
    mention_count INTEGER,
    avg_sentiment DECIMAL(5,3),
    top_posts JSONB,
    subreddits JSONB,
    created_at TIMESTAMP DEFAULT NOW(),
    UNIQUE(ticker, scrape_date)
);
