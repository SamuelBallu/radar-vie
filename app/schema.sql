-- Radar VIE schema. Idempotent: run on every production deploy (see vercel.json).

-- PDFs (cover letters, CVs). Kept in Postgres: a few dozen files of 50-500 KB,
-- served only to the signed-in owner.
CREATE TABLE IF NOT EXISTS files (
    id          text PRIMARY KEY,
    name        text        NOT NULL,
    type        text        NOT NULL DEFAULT 'application/pdf',
    size        integer     NOT NULL,
    content     bytea       NOT NULL,
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- One row per V.I.E. offer. `data` holds what the morning agent found (title, company,
-- city, score, skills...). The owner's decision lives in its own columns, so an agent
-- rewriting an offer can never undo what the owner decided.
CREATE TABLE IF NOT EXISTS offers (
    id            text PRIMARY KEY,
    data          jsonb       NOT NULL DEFAULT '{}'::jsonb,
    status        text        NOT NULL DEFAULT 'a_trier'
                  CHECK (status IN ('a_trier', 'validee', 'postulee', 'passee')),
    validated_at  timestamptz,
    applied_at    date,
    updated_at    timestamptz NOT NULL DEFAULT now()
);

-- Small documents: the last morning run, the current CV files.
CREATE TABLE IF NOT EXISTS meta (
    key         text PRIMARY KEY,
    data        jsonb       NOT NULL,
    updated_at  timestamptz NOT NULL DEFAULT now()
);

-- Failed sign-ins, to slow down password guessing.
CREATE TABLE IF NOT EXISTS failed_logins (
    ip  text        NOT NULL,
    at  timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS failed_logins_at ON failed_logins (at);

-- Every offer the morning run has read, kept whole: it can be rescored when the profile changes
-- without reading the site again, and the letters can be written from it.
CREATE TABLE IF NOT EXISTS postings (
    id          text PRIMARY KEY,
    text        text        NOT NULL,
    data        jsonb       NOT NULL DEFAULT '{}'::jsonb,
    fetched_at  timestamptz NOT NULL DEFAULT now()
);
