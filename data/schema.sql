CREATE TABLE IF NOT EXISTS customers (
    customer_id INT NOT NULL AUTO_INCREMENT,
    full_name VARCHAR(100) NOT NULL,
    phone_number VARCHAR(20) NOT NULL,
    preferred_language VARCHAR(30) NOT NULL DEFAULT 'English',
    PRIMARY KEY (customer_id),
    UNIQUE KEY uq_customers_phone_number (phone_number)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS emis (
    emi_id INT NOT NULL AUTO_INCREMENT,
    customer_id INT NOT NULL,
    emi_amount DECIMAL(12,2) NOT NULL,
    due_date DATE NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'PENDING',
    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (emi_id),
    CONSTRAINT fk_emis_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS calls (
    call_id INT NOT NULL AUTO_INCREMENT,
    customer_id INT NOT NULL,
    emi_id INT NOT NULL,
    call_status VARCHAR(20) NOT NULL DEFAULT 'INITIATED',
    reason VARCHAR(255) NULL,
    expected_salary_date DATE NULL,
    pending_payment_date DATE NULL,
    promised_payment_date DATE NULL,
    payment_commitment_confirmed BOOLEAN NOT NULL DEFAULT FALSE,
    call_summary TEXT NULL,
    started_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    ended_at DATETIME NULL,
    PRIMARY KEY (call_id),
    CONSTRAINT fk_calls_customer
        FOREIGN KEY (customer_id)
        REFERENCES customers(customer_id),
    CONSTRAINT fk_calls_emi
        FOREIGN KEY (emi_id)
        REFERENCES emis(emi_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS conversation_messages (
    message_id BIGINT NOT NULL AUTO_INCREMENT,
    call_id INT NOT NULL,
    speaker VARCHAR(10) NOT NULL,
    message_text TEXT NOT NULL,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (message_id),
    CONSTRAINT fk_conversation_messages_call
        FOREIGN KEY (call_id)
        REFERENCES calls(call_id)
        ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
