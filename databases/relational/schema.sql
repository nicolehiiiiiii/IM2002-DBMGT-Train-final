CREATE TABLE IF NOT EXISTS metro_stations (
    station_id  VARCHAR(10) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    zone        INT NOT NULL
);


-- ============================================================
--  TransitFlow PostgreSQL Schema
--  Seed data is loaded separately by: python skeleton/seed_postgres.py
--
--  TWO ROLES:
--    1. Relational  → dual-network transit data you design below
--    2. Vector      → policy documents for RAG (provided — do not modify)
-- ============================================================

-- ============================================================
--  STUDENT TASK — Design and create your relational tables here
--
--  Start from the mock data in train-mock-data/:
--    metro_stations.json, national_rail_stations.json
--    metro_schedules.json, national_rail_schedules.json
--    national_rail_seat_layouts.json
--    registered_users.json
--    bookings.json, metro_travel_history.json
--    payments.json, feedback.json
--
--  Think about:
--    - What tables do you need?
--    - What columns and data types?
--    - Which fields are primary keys? Which are foreign keys?
--    - What constraints make sense?
--
--  Apply your schema with:
--    docker-compose down -v && docker-compose up -d
-- ============================================================




-- ============================================================
--  VECTOR SCHEMA  (RAG / Help Desk) — do not modify
-- ============================================================

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS policy_documents (
    id          SERIAL       PRIMARY KEY,
    title       VARCHAR(200) NOT NULL,
    category    VARCHAR(50)  NOT NULL,  -- 'refund', 'booking', 'conduct'
    content     TEXT         NOT NULL,
    -- 768-dim  → Ollama nomic-embed-text (default)
    -- 3072-dim → Gemini gemini-embedding-001
    -- If you switch LLM_PROVIDER to gemini, change to vector(3072) and reset the database.
    embedding   vector(768),
    source_file VARCHAR(200),
    created_at  TIMESTAMPTZ  DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS national_rail_stations (
    station_id  VARCHAR(10) PRIMARY KEY,
    name        VARCHAR(100) NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    train_id        VARCHAR(20) PRIMARY KEY,
    route_id        VARCHAR(20) NOT NULL,
    departure_time  VARCHAR(10) NOT NULL,
    arrival_time    VARCHAR(10) NOT NULL
);

CREATE TABLE IF NOT EXISTS schedule_stops (
    id              SERIAL PRIMARY KEY,
    train_id        VARCHAR(20) REFERENCES schedules(train_id) ON DELETE CASCADE,
    station_id      VARCHAR(10) NOT NULL,
    arrival_time    VARCHAR(10),
    departure_time  VARCHAR(10)
);

CREATE TABLE IF NOT EXISTS bookings (
    booking_id    VARCHAR(20) PRIMARY KEY,
    user_id       VARCHAR(20) NOT NULL,
    train_id      VARCHAR(20) REFERENCES schedules(train_id) ON DELETE CASCADE,
    seat_number   VARCHAR(10) NOT NULL,
    booking_time  VARCHAR(30) NOT NULL,
    status        VARCHAR(20) NOT NULL
);

-- 1. 座位配置表
CREATE TABLE IF NOT EXISTS seat_layouts (
    id              SERIAL PRIMARY KEY,
    train_id        VARCHAR(20) NOT NULL,
    coach           VARCHAR(5) NOT NULL,
    seat_id         VARCHAR(10) NOT NULL,
    status          VARCHAR(20) DEFAULT 'available'
);

-- 2. 註冊使用者表
CREATE TABLE IF NOT EXISTS users (
    user_id         VARCHAR(20) PRIMARY KEY,
    name            VARCHAR(100) NOT NULL,
    email           VARCHAR(100),
    phone           VARCHAR(20)
);

-- 3. 捷運搭乘歷史紀錄表
CREATE TABLE IF NOT EXISTS metro_travels (
    travel_id       VARCHAR(20) PRIMARY KEY,
    user_id         VARCHAR(20) NOT NULL,
    station_id      VARCHAR(10) NOT NULL,
    travel_time     VARCHAR(30) NOT NULL,
    fare            NUMERIC(6, 2)
);

-- 4. 付款金流紀錄表
CREATE TABLE IF NOT EXISTS payments (
    payment_id      VARCHAR(20) PRIMARY KEY,
    booking_id      VARCHAR(20) NOT NULL,
    amount          NUMERIC(8, 2) NOT NULL,
    payment_method  VARCHAR(30),
    status          VARCHAR(20)
);

-- 5. 乘客意見回饋表
CREATE TABLE IF NOT EXISTS feedback (
    feedback_id     VARCHAR(20) PRIMARY KEY,
    user_id         VARCHAR(20) NOT NULL,
    rating          INT NOT NULL,
    comment         TEXT,
    created_at      VARCHAR(30)
);

-- Index for fast cosine similarity search
CREATE INDEX IF NOT EXISTS policy_docs_hnsw_idx ON policy_documents USING hnsw (embedding vector_cosine_ops);
