"""
Repositorio de proveedores. Unico archivo que escribe SQL para
'suppliers'.

    list_all(only_active=True)  -> listado completo.
    get(id)                     -> un proveedor.
    count()                     -> cuantas filas tiene la tabla (activas e
                                    inactivas: las filas no se borran), para
                                    que SupplierService arme el proximo SKU.
    create(supplier)            -> valida sku/nombre unicos, inserta.
    update(supplier)            -> mismo cuidado con sku/nombre.
    find_by_name_fuzzy(pattern) -> LIKE sobre 'name'. Pensada para la
                                    Fase 3: agrupar a mano las ~700
                                    variantes de nombre que corresponden
                                    a 80-150 proveedores reales.

Igual que en product_repository.py y category_repository.py,
create()/update() abren su propia transaccion.
"""

import sqlite3
from datetime import datetime

from ...domain.entities import Supplier
from ...domain.errors import DuplicateCode
from ..connection import transaction

_SUPPLIER_COLUMNS = (
    "id, sku, name, tax_id, phone, email, address, contact_name, notes, "
    "is_active, created_at"
)


class SupplierRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_all(self, only_active: bool = True) -> list[Supplier]:
        sql = f"SELECT {_SUPPLIER_COLUMNS} FROM suppliers"
        if only_active:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"

        rows = self._conn.execute(sql).fetchall()
        return [self._row_to_supplier(row) for row in rows]

    def get(self, supplier_id: int) -> Supplier | None:
        row = self._conn.execute(
            f"SELECT {_SUPPLIER_COLUMNS} FROM suppliers WHERE id = ?",
            (supplier_id,),
        ).fetchone()
        return self._row_to_supplier(row) if row else None

    def count(self) -> int:
        """Activos e inactivos: la secuencia del SKU nunca se repite."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM suppliers").fetchone()
        return row["n"]

    def find_by_name_fuzzy(self, name_pattern: str) -> list[Supplier]:
        """
        LIKE case-insensitive (SQLite ya lo es para ASCII, no hace
        falta LOWER()) sobre 'name'. No es busqueda de texto completo
        ni distancia de edicion: alcanza para que una persona revise a
        ojo, en un CSV, cuales de los 700 nombres del txt viejo son la
        misma proveedora escrita distinto.
        """
        rows = self._conn.execute(
            f"SELECT {_SUPPLIER_COLUMNS} FROM suppliers "
            "WHERE name LIKE ? ORDER BY name",
            (f"%{name_pattern}%",),
        ).fetchall()
        return [self._row_to_supplier(row) for row in rows]

    # ------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------
    def create(self, supplier: Supplier) -> int:
        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "INSERT INTO suppliers "
                    "(sku, name, tax_id, phone, email, address, contact_name, "
                    " notes, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        supplier.sku,
                        supplier.name,
                        supplier.tax_id,
                        supplier.phone,
                        supplier.email,
                        supplier.address,
                        supplier.contact_name,
                        supplier.notes,
                        int(supplier.is_active),
                    ),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate(e, supplier)
                if duplicate is None:
                    raise
                raise duplicate from e

            supplier_id = cur.lastrowid

        supplier.id = supplier_id
        return supplier_id

    def update(self, supplier: Supplier) -> None:
        if supplier.id is None:
            raise ValueError("No se puede actualizar un proveedor sin id.")

        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "UPDATE suppliers SET sku = ?, name = ?, tax_id = ?, "
                    "phone = ?, email = ?, address = ?, contact_name = ?, "
                    "notes = ?, is_active = ? WHERE id = ?",
                    (
                        supplier.sku,
                        supplier.name,
                        supplier.tax_id,
                        supplier.phone,
                        supplier.email,
                        supplier.address,
                        supplier.contact_name,
                        supplier.notes,
                        int(supplier.is_active),
                        supplier.id,
                    ),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate(e, supplier)
                if duplicate is None:
                    raise
                raise duplicate from e

            if cur.rowcount == 0:
                raise ValueError(f"No existe un proveedor con id={supplier.id}.")

    # ------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------
    @staticmethod
    def _row_to_supplier(row: sqlite3.Row) -> Supplier:
        return Supplier(
            id=row["id"],
            sku=row["sku"],
            name=row["name"],
            tax_id=row["tax_id"],
            phone=row["phone"],
            email=row["email"],
            address=row["address"],
            contact_name=row["contact_name"],
            notes=row["notes"],
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _as_duplicate(
        error: sqlite3.IntegrityError, supplier: Supplier
    ) -> DuplicateCode | None:
        message = str(error)
        if "suppliers.sku" in message:
            return DuplicateCode(f"Ya existe un proveedor con el SKU '{supplier.sku}'.")
        if "suppliers.name" in message:
            return DuplicateCode(f"Ya existe un proveedor llamado '{supplier.name}'.")
        return None
