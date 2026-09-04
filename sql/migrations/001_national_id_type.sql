-- Not run by any make target. Requires coordination with the team that owns
-- writes to dbo.customers, and a window where the write path is paused.
--
-- Rollback: the original column is preserved as national_id_raw until the
-- application has been confirmed to write the constrained column.

USE opsdb;
GO

-- 1. how bad is it, before changing anything
SELECT COUNT(*)                                              AS total_rows,
       SUM(CASE WHEN national_id IS NULL THEN 1 ELSE 0 END)  AS null_rows,
       SUM(CASE WHEN LEN(national_id) <> LEN(LTRIM(RTRIM(national_id)))
                THEN 1 ELSE 0 END)                           AS padded_rows,
       SUM(CASE WHEN national_id LIKE '%' + NCHAR(8203) + '%'
                THEN 1 ELSE 0 END)                           AS invisible_char_rows,
       SUM(CASE WHEN LEN(REPLACE(TRANSLATE(national_id, '0123456789', '##########'),
                                 '#', '')) > 0
                THEN 1 ELSE 0 END)                           AS non_digit_rows
  FROM dbo.customers;
GO

-- 2. keep the raw value before touching it
ALTER TABLE dbo.customers ADD national_id_raw NVARCHAR(MAX) NULL;
GO

UPDATE dbo.customers SET national_id_raw = national_id;
GO

-- 3. add the constrained column and populate it from the normalised value
ALTER TABLE dbo.customers ADD national_id_norm CHAR(9) NULL;
GO

UPDATE dbo.customers
   SET national_id_norm = LEFT(
        REPLACE(TRANSLATE(LTRIM(RTRIM(REPLACE(national_id, NCHAR(8203), ''))),
                          '-. ()', '#####'), '#', ''), 9)
 WHERE national_id IS NOT NULL;
GO

-- 4. rows that could not be normalised stay visible instead of being dropped
SELECT customer_id, national_id_raw
  FROM dbo.customers
 WHERE national_id_raw IS NOT NULL
   AND (national_id_norm IS NULL OR LEN(national_id_norm) <> 9);
GO

-- 5. only once step 4 returns nothing, and only once the application writes
--    national_id_norm, enforce uniqueness
-- CREATE UNIQUE INDEX UX_customers_national_id
--     ON dbo.customers (national_id_norm)
--  WHERE national_id_norm IS NOT NULL;
