-- Fictional demonstration data for the AI EMI Collection Voice Agent.
-- Run only after applying schema.sql to a dedicated assignment database.
-- Do not run against a production or unrelated database.

INSERT INTO customers
    (customer_id, full_name, phone_number, preferred_language)
VALUES
    (1, 'Rahul Sharma', '9000000001', 'English'),
    (2, 'Priya Reddy', '9000000002', 'English'),
    (3, 'Arjun Kumar', '9000000003', 'Hindi'),
    (4, 'Sneha Rao', '9000000004', 'English');

INSERT INTO emis
    (emi_id, customer_id, emi_amount, due_date, status)
VALUES
    (1, 1, 8500.00, '2026-10-10', 'OVERDUE'),
    (2, 2, 6200.00, '2026-10-25', 'PENDING'),
    (3, 3, 4500.00, '2026-10-20', 'PENDING'),
    (4, 4, 7100.00, '2026-10-12', 'OVERDUE');
