CREATE TABLE IF NOT EXISTS meter_files (
    id          bigserial PRIMARY KEY,
    sha256      text UNIQUE NOT NULL,
    nmi         text,
    body        text NOT NULL,
    uploaded_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS jobs (
    id         bigserial PRIMARY KEY,
    file_id    bigint UNIQUE NOT NULL REFERENCES meter_files,
    state      text NOT NULL DEFAULT 'queued'
               CHECK (state IN ('queued', 'parsing', 'validated', 'priced', 'done', 'failed', 'quarantined')),
    attempts   int NOT NULL DEFAULT 0,
    error      text,
    locked_at  timestamptz,
    updated_at timestamptz NOT NULL DEFAULT now()
);

-- ts is the interval start in NEM time (AEST, no daylight saving)
CREATE TABLE IF NOT EXISTS intervals (
    file_id   bigint NOT NULL REFERENCES meter_files,
    nmi       text NOT NULL,
    ts        timestamp NOT NULL,
    kwh       numeric(10, 3) NOT NULL,
    direction text NOT NULL CHECK (direction IN ('import', 'export')),
    quality   text NOT NULL,
    PRIMARY KEY (file_id, nmi, ts, direction)
);

CREATE TABLE IF NOT EXISTS dq_issues (
    file_id bigint NOT NULL REFERENCES meter_files,
    rule    text NOT NULL,
    row_ref text NOT NULL,
    detail  text
);

CREATE TABLE IF NOT EXISTS plans (
    id          bigserial PRIMARY KEY,
    plan_id     text UNIQUE NOT NULL,
    retailer    text NOT NULL,
    name        text NOT NULL,
    tariff_type text,
    raw         jsonb NOT NULL,
    priceable   boolean NOT NULL,
    reason      text,
    source      text NOT NULL,
    fetched_at  timestamptz NOT NULL
);

CREATE TABLE IF NOT EXISTS quotes (
    file_id           bigint NOT NULL REFERENCES meter_files,
    plan_id           bigint NOT NULL REFERENCES plans,
    annual_cost_cents int NOT NULL,
    breakdown         jsonb NOT NULL,
    PRIMARY KEY (file_id, plan_id)
);
