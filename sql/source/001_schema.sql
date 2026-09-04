IF DB_ID('opsdb') IS NULL
    CREATE DATABASE opsdb;
GO

USE opsdb;
GO

IF OBJECT_ID('dbo.customer_history', 'U') IS NOT NULL DROP TABLE dbo.customer_history;
IF OBJECT_ID('dbo.tmp_import_2019', 'U') IS NOT NULL DROP TABLE dbo.tmp_import_2019;
IF OBJECT_ID('dbo.transactions', 'U') IS NOT NULL DROP TABLE dbo.transactions;
IF OBJECT_ID('dbo.cards', 'U') IS NOT NULL DROP TABLE dbo.cards;
IF OBJECT_ID('dbo.advances', 'U') IS NOT NULL DROP TABLE dbo.advances;
IF OBJECT_ID('dbo.customers', 'U') IS NOT NULL DROP TABLE dbo.customers;
GO

CREATE TABLE dbo.customers (
    customer_id     INT IDENTITY(1,1) PRIMARY KEY,
    first_name      NVARCHAR(100)  NOT NULL,
    last_name       NVARCHAR(100)  NOT NULL,
    email           NVARCHAR(256)  NULL,
    phone           NVARCHAR(50)   NULL,
    date_of_birth   DATE           NULL,
    -- national id arrives from three upstream apps with no agreed format;
    -- typed as unbounded text so nothing is ever rejected at write time
    national_id     NVARCHAR(MAX)  NULL,
    address_line    NVARCHAR(256)  NULL,
    city            NVARCHAR(100)  NULL,
    created_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_cust_created DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_cust_updated DEFAULT SYSUTCDATETIME(),
    is_deleted      BIT            NOT NULL CONSTRAINT DF_cust_deleted DEFAULT 0
);
GO

CREATE INDEX IX_customers_updated ON dbo.customers (updated_at);
GO

CREATE TABLE dbo.advances (
    advance_id      INT IDENTITY(1,1) PRIMARY KEY,
    customer_id     INT            NOT NULL REFERENCES dbo.customers(customer_id),
    principal       DECIMAL(12,2)  NOT NULL,
    status          VARCHAR(20)    NOT NULL,
    originated_at   DATE           NOT NULL,
    closed_at       DATE           NULL,
    created_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_adv_created DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_adv_updated DEFAULT SYSUTCDATETIME(),
    is_deleted      BIT            NOT NULL CONSTRAINT DF_adv_deleted DEFAULT 0
);
GO

CREATE INDEX IX_advances_updated ON dbo.advances (updated_at);
CREATE INDEX IX_advances_customer ON dbo.advances (customer_id);
GO

CREATE TABLE dbo.cards (
    card_id         INT IDENTITY(1,1) PRIMARY KEY,
    customer_id     INT            NOT NULL REFERENCES dbo.customers(customer_id),
    last_four       CHAR(4)        NOT NULL,
    brand           VARCHAR(20)    NOT NULL,
    expires_on      DATE           NOT NULL,
    status          VARCHAR(20)    NOT NULL,
    created_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_card_created DEFAULT SYSUTCDATETIME(),
    updated_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_card_updated DEFAULT SYSUTCDATETIME(),
    is_deleted      BIT            NOT NULL CONSTRAINT DF_card_deleted DEFAULT 0
);
GO

CREATE INDEX IX_cards_updated ON dbo.cards (updated_at);
CREATE INDEX IX_cards_customer ON dbo.cards (customer_id);
GO

CREATE TABLE dbo.transactions (
    transaction_id  BIGINT IDENTITY(1,1) PRIMARY KEY,
    card_id         INT            NOT NULL REFERENCES dbo.cards(card_id),
    customer_id     INT            NOT NULL REFERENCES dbo.customers(customer_id),
    amount          DECIMAL(12,2)  NOT NULL,
    currency        CHAR(3)        NOT NULL,
    merchant        NVARCHAR(120)  NOT NULL,
    occurred_at     DATETIME2(3)   NOT NULL,
    created_at      DATETIME2(3)   NOT NULL CONSTRAINT DF_txn_created DEFAULT SYSUTCDATETIME()
);
GO

CREATE INDEX IX_transactions_created ON dbo.transactions (created_at);
GO

CREATE TABLE dbo.customer_history (
    history_id      BIGINT IDENTITY(1,1) PRIMARY KEY,
    customer_id     INT            NOT NULL,
    field_name      VARCHAR(64)    NOT NULL,
    old_value       NVARCHAR(400)  NULL,
    new_value       NVARCHAR(400)  NULL,
    changed_at      DATETIME2(3)   NOT NULL,
    changed_by      NVARCHAR(100)  NOT NULL
);
GO

CREATE INDEX IX_history_changed ON dbo.customer_history (changed_at);
GO

CREATE TABLE dbo.tmp_import_2019 (
    row_id          INT IDENTITY(1,1) PRIMARY KEY,
    raw_line        NVARCHAR(MAX)  NULL,
    loaded_at       DATETIME2(3)   NULL
);
GO
