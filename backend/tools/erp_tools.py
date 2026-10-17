"""
CareLoop AI: ERP Tools Layer
Provides database abstraction for catalog and inventory operations.
Delegates to database.py to automatically support Supabase Cloud or Local SQLite.
"""

from typing import Any
import database


def get_inventory_status() -> list[dict]:
    """Return all inventory rows as dicts ordered by id."""
    return database.get_inventory_status()


def update_stock(item_name: str, quantity_change: int) -> bool:
    """Atomically adjust stock, reject if missing or would go negative."""
    return database.update_stock(item_name, quantity_change)


def add_new_item(item_name: str, quantity: int, price: float) -> int:
    """Insert a new inventory item after validating inputs, return new row id."""
    return database.add_new_item(item_name, quantity, price)