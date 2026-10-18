# Database Setup Files

This folder contains the SQL files for the AI EMI Collection Voice Agent assignment.

## Files

- `schema.sql` — creates the core tables and callback_schedules:
  `customers`, `emis`, `calls`, `conversation_messages`, and `callback_schedules`.
- `migrations/001_callback_schedules.sql` — adds callback scheduling to an existing database without changing call, customer, or EMI rows.
- `migrations/002_call_voice_idempotency.sql` — adds the unique outbound voice idempotency key and provider call ID fields to existing calls.
- `sample_data.sql` — inserts fictional demonstration customers and EMI records.

## Safe setup order

1. Start MySQL and confirm that the dedicated assignment database exists.
2. Verify that the application's `.env` setting `MYSQL_DATABASE` points to that database.
3. Back up any existing assignment data before making schema changes.
4. Apply `schema.sql` to the assignment database.
5. For an existing installation, apply `migrations/001_callback_schedules.sql` before enabling the callback worker.
6. For an existing installation, apply `migrations/002_call_voice_idempotency.sql` before using outbound voice calls. It is not applied automatically.

## Migration 002 preflight

Do not run the migration against the configured application database as a test. First connect explicitly to the intended development database and inspect its identity and schema. The migration is one-shot: it will fail if either new column or the named unique index already exists. It is additive, nullable, and does not rewrite existing rows; MySQL unique indexes permit multiple `NULL` values while enforcing uniqueness for non-null keys.

Run these read-only checks on the intended target before applying the migration:

```sql
SELECT DATABASE() AS database_name, CURRENT_USER() AS account_name;
SHOW COLUMNS FROM calls LIKE 'idempotency_key';
SHOW COLUMNS FROM calls LIKE 'provider_call_id';
SHOW INDEX FROM calls WHERE Key_name = 'uq_calls_idempotency_key';
SELECT idempotency_key, COUNT(*) AS duplicate_count
FROM calls
WHERE idempotency_key IS NOT NULL
GROUP BY idempotency_key
HAVING COUNT(*) > 1;
```

The three `SHOW` statements and duplicate query should return no rows before a first application. Record the verified database name and account outside the repository, then apply migration 002 once through the approved migration procedure. This project has not run these checks or applied the migration to a live database.
7. Review existing customer and EMI IDs before running `sample_data.sql`.
8. Run `sample_data.sql` only if its IDs and phone numbers will not conflict with existing records.

Do not run these scripts against production or unrelated databases.

## Important notes

- The application does not automatically create tables or insert sample records.
- `CREATE TABLE IF NOT EXISTS` does not update an existing table to match a changed schema.
- The sample records are fictional and intended only for demonstration.
- `sample_data.sql` is not idempotent; running it repeatedly can cause duplicate-key errors.
- These files have been created locally. Creating them does not execute SQL.
