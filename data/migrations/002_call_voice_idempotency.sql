-- Prevent duplicate outbound voice call creation when a client retries.
-- Review the target schema and apply only through the approved migration process.
ALTER TABLE calls
    ADD COLUMN idempotency_key VARCHAR(128) NULL,
    ADD COLUMN provider_call_id VARCHAR(64) NULL,
    ADD UNIQUE KEY uq_calls_idempotency_key (idempotency_key);
