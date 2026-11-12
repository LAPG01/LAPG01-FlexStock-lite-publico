"""
Repositorio de movimientos de inventario. Unico archivo que escribe SQL
para 'inventory_movements'.

Tabla append-only (regla 3 del CLAUDE.md): este repositorio solo INSERTa,
nunca actualiza ni borra una fila.

    record(movement)         -> inserta un movimiento y devuelve su id.
    list_for_product(id)     -> historial de un producto, mas nuevo primero.

DIFERENCIA con product_repository.py / customer_repository.py /
category_repository.py: record() NO abre su propia transaccion ('with
transaction(...)'). Un movimiento nunca viaja solo: siempre acompana a
un ajuste de 'products.current_stock' (regla 2) y casi siempre a un
documento (factura, nota de credito). Que este metodo reciba una
conexion con una transaccion YA ABIERTA por quien orquesta la operacion
completa (StockService, llamado desde BillingService.confirmar()) es lo
que permite escribir factura + lineas + movimientos + stock como una
sola operacion atomica, en vez de varias transacciones sueltas que
podrian dejar el sistema a medio camino si una fallara despues de otra.
"""

import sqlite3
from datetime import datetime

from ...domain.entities import InventoryMovement
from ...domain.enums import MovementType, ReferenceType

_MOVEMENT_COLUMNS = (
    "id, product_id, type, quantity_milli, unit_cost_milli, reason, "
    "reference_type, reference_id, counted_by, note, created_at"
)


class InventoryMovementRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def record(self, movement: InventoryMovement) -> int:
        """
        Inserta el movimiento tal cual llega. No valida reglas de negocio
        (ej. "ADJUSTMENT necesita reason", regla del CLAUDE.md): eso es
        responsabilidad de StockService, que arma el InventoryMovement
        antes de llamar aca. Requiere una transaccion ya abierta por el
        llamador (ver nota del modulo).

        Si 'movement.created_at' viene cargado (StockService.
        apply_movement lo pone en "ahora" antes de llamar aca), se
        inserta tal cual, para que el objeto en memoria y la fila
        guardada tengan EXACTAMENTE el mismo valor sin una segunda
        consulta. Si viene en None (tests sueltos, por ejemplo) se deja
        el DEFAULT de schema.sql, y el objeto en memoria se queda con
        created_at=None -nadie que construye un movimiento sin fecha
        esta mostrando esa fecha despues.
        """
        cur = self._conn.execute(
            "INSERT INTO inventory_movements "
            "(product_id, type, quantity_milli, unit_cost_milli, reason, "
            " reference_type, reference_id, counted_by, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, "
            " COALESCE(?, datetime('now', 'localtime')))",
            (
                movement.product_id,
                movement.type.value,
                movement.quantity_milli,
                movement.unit_cost_milli,
                movement.reason,
                movement.reference_type.value if movement.reference_type else None,
                movement.reference_id,
                movement.counted_by,
                movement.note,
                movement.created_at.strftime("%Y-%m-%d %H:%M:%S")
                if movement.created_at
                else None,
            ),
        )
        movement.id = cur.lastrowid
        return movement.id

    def list_for_product(self, product_id: int) -> list[InventoryMovement]:
        """Historial completo de un producto, mas reciente primero."""
        rows = self._conn.execute(
            f"SELECT {_MOVEMENT_COLUMNS} FROM inventory_movements "
            "WHERE product_id = ? ORDER BY id DESC",
            (product_id,),
        ).fetchall()
        return [self._row_to_movement(row) for row in rows]

    @staticmethod
    def _row_to_movement(row: sqlite3.Row) -> InventoryMovement:
        return InventoryMovement(
            id=row["id"],
            product_id=row["product_id"],
            type=MovementType(row["type"]),
            quantity_milli=row["quantity_milli"],
            unit_cost_milli=row["unit_cost_milli"],
            reason=row["reason"],
            reference_type=(
                ReferenceType(row["reference_type"]) if row["reference_type"] else None
            ),
            reference_id=row["reference_id"],
            counted_by=row["counted_by"],
            note=row["note"],
            created_at=datetime.fromisoformat(row["created_at"]),
        )
