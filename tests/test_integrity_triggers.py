"""
Tests de flexstock/data/triggers.sql: las reglas 3 y 4 del CLAUDE.md
hechas cumplir por la BASE, no solo por los servicios.

Se escribe SQL directo a proposito: es lo que haria alguien abriendo la
base con DB Browser, y es justo lo que estos triggers tienen que frenar.
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _producto(conn, sku="GEN-0001"):
    categoria = conn.execute("SELECT id FROM categories WHERE code = 'GEN'").fetchone()[
        0
    ]
    return conn.execute(
        "INSERT INTO products (sku, name, category_id) VALUES (?, 'Clavo', ?)",
        (sku, categoria),
    ).lastrowid


def _movimiento(conn, product_id, cantidad=1000):
    return conn.execute(
        "INSERT INTO inventory_movements (product_id, type, quantity_milli)"
        " VALUES (?, 'INITIAL', ?)",
        (product_id, cantidad),
    ).lastrowid


def _cliente(conn):
    return conn.execute(
        "INSERT INTO customers (sku, name) VALUES ('CLI-0001', 'Juan')"
    ).lastrowid


def _factura(conn, *, customer_id=None, numero=1):
    serie = conn.execute(
        "SELECT id FROM document_series WHERE doc_type = 'INVOICE'"
    ).fetchone()[0]
    return conn.execute(
        "INSERT INTO invoices (series_id, number, display_number, customer_id,"
        " customer_name, tax_rate, subtotal_cents, total_cents)"
        " VALUES (?, ?, ?, ?, 'Juan', '16.00', 1000, 1000)",
        (serie, numero, f"FAC-{numero:06d}", customer_id),
    ).lastrowid


def _linea(conn, invoice_id, product_id):
    return conn.execute(
        "INSERT INTO invoice_lines (invoice_id, product_id, product_sku,"
        " product_name, unit, quantity_milli, unit_price_cents, line_total_cents)"
        " VALUES (?, ?, 'GEN-0001', 'Clavo', 'UNI', 1000, 1000, 1000)",
        (invoice_id, product_id),
    ).lastrowid


# ------------------------------------------------------------
# Regla 3: inventory_movements es append-only
# ------------------------------------------------------------
def test_un_movimiento_no_se_edita(conn):
    mov = _movimiento(conn, _producto(conn))

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute(
            "UPDATE inventory_movements SET quantity_milli = 5000 WHERE id = ?", (mov,)
        )


def test_un_movimiento_no_se_borra(conn):
    mov = _movimiento(conn, _producto(conn))

    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        conn.execute("DELETE FROM inventory_movements WHERE id = ?", (mov,))


def test_agregar_movimientos_sigue_permitido(conn):
    pid = _producto(conn)
    _movimiento(conn, pid)
    _movimiento(conn, pid, -500)

    total = conn.execute(
        "SELECT SUM(quantity_milli) FROM inventory_movements WHERE product_id = ?",
        (pid,),
    ).fetchone()[0]
    assert total == 500


# ------------------------------------------------------------
# Regla 4: una factura no se borra ni se edita, solo se anula
# ------------------------------------------------------------
def test_una_factura_no_se_borra(conn):
    fid = _factura(conn)

    with pytest.raises(sqlite3.IntegrityError, match="no se borran"):
        conn.execute("DELETE FROM invoices WHERE id = ?", (fid,))


@pytest.mark.parametrize(
    ("columna", "valor"),
    [
        ("total_cents", 999),
        ("tax_rate", "0.00"),
        ("bs_rate", "50.00"),
        ("customer_name", "Otro"),
        ("issued_at", "2020-01-01 00:00:00"),
        ("display_number", "FAC-999999"),
    ],
)
def test_una_factura_emitida_no_se_edita(conn, columna, valor):
    fid = _factura(conn)

    with pytest.raises(sqlite3.IntegrityError, match="solo se anula"):
        conn.execute(f"UPDATE invoices SET {columna} = ? WHERE id = ?", (valor, fid))


def test_anular_una_factura_si_se_puede(conn):
    fid = _factura(conn)

    conn.execute(
        "UPDATE invoices SET status = 'CANCELLED',"
        " cancelled_at = datetime('now'), cancel_reason = 'Error' WHERE id = ?",
        (fid,),
    )

    status = conn.execute("SELECT status FROM invoices WHERE id = ?", (fid,)).fetchone()
    assert status[0] == "CANCELLED"


def test_una_anulada_no_vuelve_a_emitida(conn):
    fid = _factura(conn)
    conn.execute("UPDATE invoices SET status = 'CANCELLED' WHERE id = ?", (fid,))

    with pytest.raises(sqlite3.IntegrityError, match="solo se anula"):
        conn.execute("UPDATE invoices SET status = 'ISSUED' WHERE id = ?", (fid,))


def test_anulando_no_se_puede_cambiar_el_monto_de_paso(conn):
    fid = _factura(conn)

    with pytest.raises(sqlite3.IntegrityError, match="solo se anula"):
        conn.execute(
            "UPDATE invoices SET status = 'CANCELLED', total_cents = 1 WHERE id = ?",
            (fid,),
        )


def test_borrar_un_cliente_deja_sus_facturas_con_el_nombre_guardado(conn):
    """ON DELETE SET NULL es una escritura que hace SQLite solo: se admite.
    El nombre del cliente queda en la factura (snapshot)."""
    cid = _cliente(conn)
    fid = _factura(conn, customer_id=cid)

    conn.execute("DELETE FROM customers WHERE id = ?", (cid,))

    fila = conn.execute(
        "SELECT customer_id, customer_name FROM invoices WHERE id = ?", (fid,)
    ).fetchone()
    assert (fila["customer_id"], fila["customer_name"]) == (None, "Juan")


def test_no_se_puede_cambiar_el_cliente_de_una_factura(conn):
    otro = _cliente(conn)
    fid = _factura(conn)

    with pytest.raises(sqlite3.IntegrityError, match="solo se anula"):
        conn.execute("UPDATE invoices SET customer_id = ? WHERE id = ?", (otro, fid))


def test_las_lineas_de_una_factura_no_se_editan_ni_se_borran(conn):
    linea = _linea(conn, _factura(conn), _producto(conn))

    with pytest.raises(sqlite3.IntegrityError, match="no se editan"):
        conn.execute(
            "UPDATE invoice_lines SET unit_price_cents = 1 WHERE id = ?", (linea,)
        )
    with pytest.raises(sqlite3.IntegrityError, match="no se borran"):
        conn.execute("DELETE FROM invoice_lines WHERE id = ?", (linea,))


# ------------------------------------------------------------
# Un producto con historia no se borra (se llevaria sus movimientos)
# ------------------------------------------------------------
def test_un_producto_con_movimientos_no_se_borra(conn):
    pid = _producto(conn)
    _movimiento(conn, pid)

    with pytest.raises(sqlite3.IntegrityError, match="se desactiva"):
        conn.execute("DELETE FROM products WHERE id = ?", (pid,))

    assert conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 1


def test_un_producto_vendido_no_se_borra(conn):
    pid = _producto(conn)
    _linea(conn, _factura(conn), pid)

    with pytest.raises(sqlite3.IntegrityError, match="se desactiva"):
        conn.execute("DELETE FROM products WHERE id = ?", (pid,))


def test_un_producto_sin_historia_si_se_borra(conn):
    """Cargado por error recien: no hay nada que conservar."""
    pid = _producto(conn)

    conn.execute("DELETE FROM products WHERE id = ?", (pid,))

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


# ------------------------------------------------------------
# init_schema
# ------------------------------------------------------------
def test_init_schema_dos_veces_no_duplica_ni_falla(conn):
    init_schema(conn)

    triggers = conn.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
    ).fetchone()[0]
    assert triggers == 7


def test_cualquier_conexion_queda_protegida_aunque_no_active_foreign_keys(tmp_path):
    """DB Browser y el CLI abren con foreign_keys=0: los triggers igual valen."""
    ruta = tmp_path / "flexstock.db"
    with sqlite3.connect(ruta, isolation_level=None) as preparar:
        preparar.row_factory = sqlite3.Row
        preparar.execute("PRAGMA foreign_keys = ON")
        init_schema(preparar)
        fid = _factura(preparar)
    preparar.close()

    externa = sqlite3.connect(ruta, isolation_level=None)  # sin PRAGMA, como DB Browser
    try:
        with pytest.raises(sqlite3.IntegrityError):
            externa.execute("DELETE FROM invoices WHERE id = ?", (fid,))
    finally:
        externa.close()
