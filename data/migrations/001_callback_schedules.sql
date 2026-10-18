-- Apply once to an existing dedicated database before enabling callback scheduling.
-- This migration only creates the scheduler table; it does not change customer,
-- EMI, or call records.
CREATE TABLE IF NOT EXISTS callback_schedules (
    callback_id INT NOT NULL AUTO_INCREMENT,
    call_id INT NOT NULL,
    scheduled_at DATETIME NOT NULL,
    attempt_number INT NOT NULL DEFAULT 1,
    reason VARCHAR(120) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (callback_id),
    KEY ix_callback_schedules_due (status, scheduled_at),
    CONSTRAINT fk_callback_schedules_call
        FOREIGN KEY (call_id)
        REFERENCES calls(call_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
