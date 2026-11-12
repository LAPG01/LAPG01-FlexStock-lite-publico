"""
Repositorio de clientes. Unico archivo que escribe SQL para 'customers'.

    list_all(only_active=True)  -> listado completo, sin relaciones
                                    (customers no tiene ninguna todavia).
    get(id)                     -> un cliente.
    count()                     -> cuantas filas tiene la tabla (activas e
                                    inactivas: las filas no se borran), para
                                    que CustomerService arme el proximo SKU.
    create(customer)            -> valida sku/cedula unicos, inserta.
    update(customer)            -> mismo cuidado con sku/cedula.

Igual que en product_repository.py y supplier_repository.py, create()/
update() abren su propia transaccion.
"""

import sqlite3
from datetime import datetime

from ...domain.entities import Customer
from ...domain.errors import DuplicateCode
from ..connection import transaction

_CUSTOMER_COLUMNS = (
    "id, sku, name, tax_id, phone, email, address, notes, "
    "is_wholesale, is_active, created_at"
)


class CustomerRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_all(self, only_active: bool = True) -> list[Customer]:
        sql = f"SELECT {_CUSTOMER_COLUMNS} FROM customers"
        if only_active:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"

        rows = self._conn.execute(sql).fetchall()
        return [self._row_to_customer(row) for row in rows]

    def get(self, customer_id: int) -> Customer | None:
        row = self._conn.execute(
            f"SELECT {_CUSTOMER_COLUMNS} FROM customers WHERE id = ?",
            (customer_id,),
        ).fetchone()
        return self._row_to_customer(row) if row else None

    def count(self) -> int:
        """Activos e inactivos: la secuencia del SKU nunca se repite."""
        row = self._conn.execute("SELECT COUNT(*) AS n FROM customers").fetchone()
        return row["n"]

    # ------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------
    def create(self, customer: Customer) -> int:
        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "INSERT INTO customers "
                    "(sku, name, tax_id, phone, email, address, notes, "
                    " is_wholesale, is_active) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        customer.sku,
                        customer.name,
                        customer.tax_id,
                        customer.phone,
                        customer.email,
                        customer.address,
                        customer.notes,
                        int(customer.is_wholesale),
                        int(customer.is_active),
                    ),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate(e, customer)
                if duplicate is None:
                    raise
                raise duplicate from e

            customer_id = cur.lastrowid

        customer.id = customer_id
        return customer_id

    def update(self, customer: Customer) -> None:
        if customer.id is None:
            raise ValueError("No se puede actualizar un cliente sin id.")

        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "UPDATE customers SET sku = ?, name = ?, tax_id = ?, "
                    "phone = ?, email = ?, address = ?, notes = ?, "
                    "is_wholesale = ?, is_active = ? WHERE id = ?",
                    (
                        customer.sku,
                        customer.name,
                        customer.tax_id,
                        customer.phone,
                        customer.email,
                        customer.address,
                        customer.notes,
                        int(customer.is_wholesale),
                        int(customer.is_active),
                        customer.id,
                    ),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate(e, customer)
                if duplicate is None:
                    raise
                raise duplicate from e

            if cur.rowcount == 0:
                raise ValueError(f"No existe un cliente con id={customer.id}.")

    # ------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------
    @staticmethod
    def _row_to_customer(row: sqlite3.Row) -> Customer:
        return Customer(
            id=row["id"],
            sku=row["sku"],
            name=row["name"],
            tax_id=row["tax_id"],
            phone=row["phone"],
            email=row["email"],
            address=row["address"],
            notes=row["notes"],
            is_wholesale=bool(row["is_wholesale"]),
            is_active=bool(row["is_active"]),
            created_at=datetime.fromisoformat(row["created_at"]),
        )

    @staticmethod
    def _as_duplicate(
        error: sqlite3.IntegrityError, customer: Customer
    ) -> DuplicateCode | None:
        message = str(error)
        if "customers.sku" in message:
            return DuplicateCode(f"Ya existe un cliente con el SKU '{customer.sku}'.")
        if "customers.tax_id" in message:
            return DuplicateCode(
                f"Ya existe un cliente con la cedula/RIF '{customer.tax_id}'."
            )
        return None
