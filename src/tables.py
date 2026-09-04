TABLES = {
    "customers": {
        "strategy": "merge",
        "key": ["customer_id"],
        "watermark_column": "updated_at",
        "soft_delete_column": "is_deleted",
        "reconcile_keys": True,
        "columns": [
            "customer_id", "first_name", "last_name", "email", "phone",
            "date_of_birth", "national_id", "address_line", "city",
            "created_at", "updated_at", "is_deleted",
        ],
    },
    "advances": {
        "strategy": "merge",
        "key": ["advance_id"],
        "watermark_column": "updated_at",
        "soft_delete_column": "is_deleted",
        "reconcile_keys": True,
        "columns": [
            "advance_id", "customer_id", "principal", "status",
            "originated_at", "closed_at", "created_at", "updated_at", "is_deleted",
        ],
    },
    "cards": {
        "strategy": "merge",
        "key": ["card_id"],
        "watermark_column": "updated_at",
        "soft_delete_column": "is_deleted",
        "reconcile_keys": True,
        "columns": [
            "card_id", "customer_id", "last_four", "brand", "expires_on",
            "status", "created_at", "updated_at", "is_deleted",
        ],
    },
    "transactions": {
        "strategy": "append",
        "key": ["transaction_id"],
        "watermark_column": "transaction_id",
        "soft_delete_column": None,
        "reconcile_keys": False,
        "columns": [
            "transaction_id", "card_id", "customer_id", "amount", "currency",
            "merchant", "occurred_at", "created_at",
        ],
    },
    "customer_history": {
        "strategy": "append",
        "key": ["history_id"],
        "watermark_column": "history_id",
        "soft_delete_column": None,
        "reconcile_keys": False,
        "columns": [
            "history_id", "customer_id", "field_name", "old_value",
            "new_value", "changed_at", "changed_by",
        ],
    },
}

EXCLUDED = {
    "tmp_import_2019": "unowned scratch table, no downstream consumer",
}
