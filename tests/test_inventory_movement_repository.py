"""
Tests de InventoryMovementRepository. SQLite en memoria, igual que los demas.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.domain.entities import InventoryMovement
from flexstock.domain.enums import MovementType, ReferenceType


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn):
    return InventoryMovementRepository(conn)


def _crear_producto(conn, sku="FER-0001", name="Tornillo"):
    cur = conn.execute("INSERT INTO products (sku, name) VALUES (?, ?)", (sku, name))
    return cur.lastrowid


def test_record_inserta_y_devuelve_id(conn, repo):
    pid = _crear_producto(conn)

    movement_id = repo.record(
        InventoryMovement(
            id=None,
            product_id=pid,
            type=MovementType.OUT,
            quantity_milli=-3000,
            reference_type=ReferenceType.INVOICE,
            reference_id=1,
        )
    )

    assert movement_id is not None
    fila = conn.execute(
        "SELECT * FROM inventory_movements WHERE id = ?", (movement_id,)
    ).fetchone()
    assert fila["product_id"] == pid
    assert fila["type"] == "OUT"
    assert fila["quantity_milli"] == -3000
    assert fila["reference_type"] == "INVOICE"
    assert fila["reference_id"] == 1


def test_record_con_fecha_propia_la_inserta_tal_cual(conn, repo):
    """
    StockService.apply_movement fija created_at en Python antes de
    llamar aca (ver su docstring): record() tiene que respetarla en vez
    de dejarla pisar por el DEFAULT de schema.sql.
    """
    from datetime import datetime

    pid = _crear_producto(conn)
    fecha = datetime(2026, 3, 1, 14, 30, 0)  # noqa: DTZ001 - hora local, igual que issued_at

    repo.record(
        InventoryMovement(
            id=None,
            product_id=pid,
            type=MovementType.ADJUSTMENT,
            quantity_milli=-1000,
            reason="Conteo físico",
            counted_by="Juan",
            note="Llegaron 4 pero pronto vendran 6",
            created_at=fecha,
        )
    )

    fila = conn.execute("SELECT * FROM inventory_movements").fetchone()
    assert fila["created_at"] == "2026-03-01 14:30:00"
    assert fila["counted_by"] == "Juan"
    assert fila["note"] == "Llegaron 4 pero pronto vendran 6"


def test_record_sin_referencia_guarda_null(conn, repo):
    pid = _crear_producto(conn)

    repo.record(
        InventoryMovement(
            id=None,
            product_id=pid,
            type=MovementType.INITIAL,
            quantity_milli=5000,
        )
    )

    fila = conn.execute("SELECT * FROM inventory_movements").fetchone()
    assert fila["reference_type"] is None
    assert fila["reference_id"] is None


def test_record_no_permite_cantidad_cero(conn, repo):
    """quantity_milli <> 0 es un CHECK del esquema (regla: el delta nunca es cero)."""
    pid = _crear_producto(conn)

    with pytest.raises(sqlite3.IntegrityError):
        repo.record(
            InventoryMovement(
                id=None, product_id=pid, type=MovementType.ADJUSTMENT, quantity_milli=0
            )
        )


def test_list_for_product_trae_quien_conto_y_la_nota(conn, repo):
    pid = _crear_producto(conn)
    repo.record(
        InventoryMovement(
            id=None,
            product_id=pid,
            type=MovementType.ADJUSTMENT,
            quantity_milli=-1000,
            reason="Conteo físico",
            counted_by="Juan",
            note="Llegaron 4 pero pronto vendran 6",
        )
    )

    (movimiento,) = repo.list_for_product(pid)

    assert movimiento.counted_by == "Juan"
    assert movimiento.note == "Llegaron 4 pero pronto vendran 6"


def test_list_for_product_mas_reciente_primero(conn, repo):
    pid = _crear_producto(conn)
    repo.record(
        InventoryMovement(
            id=None, product_id=pid, type=MovementType.INITIAL, quantity_milli=10000
        )
    )
    repo.record(
        InventoryMovement(
            id=None, product_id=pid, type=MovementType.OUT, quantity_milli=-2000
        )
    )

    movimientos = repo.list_for_product(pid)

    assert [m.type for m in movimientos] == [MovementType.OUT, MovementType.INITIAL]


def test_list_for_product_no_trae_movimientos_de_otro_producto(conn, repo):
    pid1 = _crear_producto(conn, "FER-0001", "Uno")
    pid2 = _crear_producto(conn, "FER-0002", "Dos")
    repo.record(
        InventoryMovement(
            id=None, product_id=pid1, type=MovementType.INITIAL, quantity_milli=1000
        )
    )
    repo.record(
        InventoryMovement(
            id=None, product_id=pid2, type=MovementType.INITIAL, quantity_milli=2000
        )
    )

    assert len(repo.list_for_product(pid1)) == 1
