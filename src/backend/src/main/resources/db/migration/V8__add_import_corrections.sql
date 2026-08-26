ALTER TABLE import_batch
    ADD COLUMN corrections JSONB NOT NULL DEFAULT '{}'::jsonb;
