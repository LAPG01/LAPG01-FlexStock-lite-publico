"""
Tests de StockService: unico punto que debe tocar current_stock, y
siempre junto con su InventoryMovement.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.domain.enums import MovementType, ReferenceType
from flexstock.domain.errors import ReasonRequired
from flexstock.services.stock_service import StockService


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def products(conn):
    return ProductRepository(conn)


@pytest.fixture
def service(conn):
    return StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )


def _crear_producto(conn, sku="FER-0001", name="Tornillo"):
    cur = conn.execute("INSERT INTO products (sku, name) VALUES (?, ?)", (sku, name))
    return cur.lastrowid


def test_apply_movement_registra_y_ajusta_stock(conn, service):
    pid = _crear_producto(conn)

    service.apply_movement(
        product_id=pid,
        type=MovementType.OUT,
        quantity_milli=-3000,
        reference_type=ReferenceType.INVOICE,
        reference_id=1,
    )

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == -3000

    movimientos = conn.execute("SELECT * FROM inventory_movements").fetchall()
    assert len(movimientos) == 1
    assert movimientos[0]["quantity_milli"] == -3000


def test_apply_movement_positivo_suma_stock(conn, service):
    pid = _crear_producto(conn)

    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=10000
    )

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 10000


def test_apply_movement_cantidad_cero_lanza_valueerror(conn, service):
    pid = _crear_producto(conn)

    with pytest.raises(ValueError, match="cero"):
        service.apply_movement(
            product_id=pid, type=MovementType.ADJUSTMENT, quantity_milli=0
        )


def test_apply_movement_ajuste_sin_motivo_lanza_reason_required(conn, service):
    pid = _crear_producto(conn)

    with pytest.raises(ReasonRequired):
        service.apply_movement(
            product_id=pid, type=MovementType.ADJUSTMENT, quantity_milli=-1000
        )


def test_apply_movement_ajuste_con_motivo_no_falla(conn, service):
    pid = _crear_producto(conn)

    service.apply_movement(
        product_id=pid,
        type=MovementType.ADJUSTMENT,
        quantity_milli=-1000,
        reason="Conteo físico",
    )

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == -1000


def test_apply_movement_producto_inexistente_falla(conn, service):
    """
    Falla en el INSERT del movimiento, no en adjust_stock: la FK
    'inventory_movements.product_id -> products.id' (con PRAGMA
    foreign_keys=ON) rechaza el movimiento antes de que el servicio
    llegue a tocar el cache de stock.
    """
    with pytest.raises(sqlite3.IntegrityError):
        service.apply_movement(
            product_id=999, type=MovementType.OUT, quantity_milli=-1000
        )


# ------------------------------------------------------------
# ajustar: entrada del ajuste manual (Fase 8), abre su propia transaccion
# ------------------------------------------------------------
def test_ajustar_calcula_el_delta_desde_lo_contado(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=50000
    )

    service.ajustar(product_id=pid, counted_milli=47000, reason="Conteo físico")

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 47000

    movimiento = conn.execute(
        "SELECT type, quantity_milli, reason FROM inventory_movements "
        "WHERE product_id = ? ORDER BY id DESC LIMIT 1",
        (pid,),
    ).fetchone()
    assert movimiento["type"] == "ADJUSTMENT"
    assert movimiento["quantity_milli"] == -3000
    assert movimiento["reason"] == "Conteo físico"


def test_ajustar_guarda_quien_conto_y_una_nota_libre(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=50000
    )

    service.ajustar(
        product_id=pid,
        counted_milli=47000,
        reason="Conteo físico",
        counted_by="Juan",
        note="Llegaron 4 pero pronto vendran 6",
    )

    movimiento = conn.execute(
        "SELECT counted_by, note FROM inventory_movements "
        "WHERE product_id = ? ORDER BY id DESC LIMIT 1",
        (pid,),
    ).fetchone()
    assert movimiento["counted_by"] == "Juan"
    assert movimiento["note"] == "Llegaron 4 pero pronto vendran 6"


def test_ajustar_sin_quien_conto_ni_nota_quedan_en_null(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=50000
    )

    service.ajustar(product_id=pid, counted_milli=47000, reason="Conteo físico")

    movimiento = conn.execute(
        "SELECT counted_by, note FROM inventory_movements "
        "WHERE product_id = ? ORDER BY id DESC LIMIT 1",
        (pid,),
    ).fetchone()
    assert movimiento["counted_by"] is None
    assert movimiento["note"] is None


def test_apply_movement_devuelve_la_fecha_real_sin_consultar_de_nuevo(conn, service):
    """
    created_at se fija en Python antes de insertar (ver docstring de
    apply_movement): el objeto devuelto ya trae la fecha real, sin
    depender de una segunda consulta para mostrarla.
    """
    pid = _crear_producto(conn)

    movimiento = service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=10000
    )

    assert movimiento.created_at is not None
    fila = conn.execute(
        "SELECT created_at FROM inventory_movements WHERE id = ?", (movimiento.id,)
    ).fetchone()
    assert fila["created_at"] == movimiento.created_at.strftime("%Y-%m-%d %H:%M:%S")


def test_ajustar_permite_delta_positivo(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=10000
    )

    service.ajustar(product_id=pid, counted_milli=15000, reason="Se encontro mercancia")

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 15000


def test_ajustar_sin_motivo_lanza_reason_required(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=10000
    )

    with pytest.raises(ReasonRequired):
        service.ajustar(product_id=pid, counted_milli=5000, reason="")


def test_ajustar_cantidad_igual_a_la_actual_lanza_valueerror(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=10000
    )

    with pytest.raises(ValueError, match="igual"):
        service.ajustar(product_id=pid, counted_milli=10000, reason="Conteo físico")


def test_ajustar_producto_inexistente_lanza_valueerror(conn, service):
    with pytest.raises(ValueError, match="No existe"):
        service.ajustar(product_id=999, counted_milli=5000, reason="Conteo físico")


# ------------------------------------------------------------
# history_for
# ------------------------------------------------------------
def test_history_for_trae_todo_el_historial_mas_reciente_primero(conn, service):
    pid = _crear_producto(conn)
    service.apply_movement(
        product_id=pid, type=MovementType.INITIAL, quantity_milli=50000
    )
    service.ajustar(
        product_id=pid,
        counted_milli=47000,
        reason="Conteo físico",
        counted_by="Juan",
    )

    historial = service.history_for(pid)

    assert [m.type for m in historial] == [
        MovementType.ADJUSTMENT,
        MovementType.INITIAL,
    ]
    assert historial[0].counted_by == "Juan"


def test_history_for_producto_sin_movimientos_es_vacio(conn, service):
    pid = _crear_producto(conn)

    assert service.history_for(pid) == []
