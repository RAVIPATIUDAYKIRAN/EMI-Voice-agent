# Database Setup Files

This folder contains the SQL files for the AI EMI Collection Voice Agent assignment.

## Files

- `schema.sql` — creates the four required tables:
  `customers`, `emis`, `calls`, and `conversation_messages`.
- `sample_data.sql` — inserts fictional demonstration customers and EMI records.

## Safe setup order

1. Start MySQL and confirm that the dedicated assignment database exists.
2. Verify that the application's `.env` setting `MYSQL_DATABASE` points to that database.
3. Back up any existing assignment data before making schema changes.
4. Apply `schema.sql` to the assignment database.
5. Review existing customer and EMI IDs before running `sample_data.sql`.
6. Run `sample_data.sql` only if its IDs and phone numbers will not conflict with existing records.

Do not run these scripts against production or unrelated databases.

## Important notes

- The application does not automatically create tables or insert sample records.
- `CREATE TABLE IF NOT EXISTS` does not update an existing table to match a changed schema.
- The sample records are fictional and intended only for demonstration.
- `sample_data.sql` is not idempotent; running it repeatedly can cause duplicate-key errors.
- These files have been created locally. Creating them does not execute SQL.
