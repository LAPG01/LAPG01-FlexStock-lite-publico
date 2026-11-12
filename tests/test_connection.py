"""
Tests de connection.py: sobre todo la migracion de nombres de price_tiers
(_upgrade_price_tier_names), que corre sobre bases ya sembradas con los
nombres viejos.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema


def _conn() -> sqlite3.Connection:
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def _tiers(connection: sqlite3.Connection) -> list[tuple[int, str, int]]:
    rows = connection.execute(
        "SELECT position, name, is_default FROM price_tiers ORDER BY position"
    ).fetchall()
    return [(row["position"], row["name"], row["is_default"]) for row in rows]


def _columnas_de_invoices(connection: sqlite3.Connection) -> set[str]:
    return {row["name"] for row in connection.execute("PRAGMA table_info(invoices)")}


def _sin_triggers(connection: sqlite3.Connection) -> None:
    """
    Una base de antes de triggers.sql (23/10/2026) no los tiene. Hay que
    sacarlos para simular una base vieja: si no, SQLite no deja borrar
    una columna que un trigger nombra (bs_rate, ref_rate...).
    """
    nombres = [
        fila["name"]
        for fila in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'trigger'"
        )
    ]
    for nombre in nombres:
        connection.execute(f"DROP TRIGGER {nombre}")


def _cantidad_de_triggers(connection: sqlite3.Connection) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
    ).fetchone()[0]


def _columnas_de_movimientos(connection: sqlite3.Connection) -> set[str]:
    return {
        row["name"]
        for row in connection.execute("PRAGMA table_info(inventory_movements)")
    }


def test_base_nueva_de_facturas_ya_trae_las_dos_tasas():
    conn = _conn()
    init_schema(conn)

    assert {"bs_rate", "ref_rate"} <= _columnas_de_invoices(conn)


def test_migra_una_base_de_facturas_sin_ref_rate():
    conn = _conn()
    init_schema(conn)
    _sin_triggers(conn)
    conn.execute("ALTER TABLE invoices DROP COLUMN ref_rate")
    serie = conn.execute("SELECT id FROM document_series LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO invoices (series_id, number, display_number, customer_name, "
        "tax_rate) VALUES (?, 1, 'FAC-000001', 'Cliente viejo', '16.00')",
        (serie,),
    )

    init_schema(conn)  # aca migra

    assert "ref_rate" in _columnas_de_invoices(conn)
    assert conn.execute("SELECT ref_rate FROM invoices").fetchone()["ref_rate"] is None
    # Y recibe los triggers DESPUES de la columna que nombran.
    assert _cantidad_de_triggers(conn) == 7


def test_migra_una_base_de_facturas_sin_bs_rate():
    """Una base que ya tiene facturas, creada ANTES de que la factura
    guardara su tasa en Bs: la columna se agrega y las facturas viejas
    quedan con NULL (su PDF usara la tasa BCV vigente)."""
    conn = _conn()
    init_schema(conn)
    # Simula la base vieja: sin la columna, con una factura ya guardada.
    _sin_triggers(conn)
    conn.execute("ALTER TABLE invoices DROP COLUMN bs_rate")
    serie = conn.execute("SELECT id FROM document_series LIMIT 1").fetchone()["id"]
    conn.execute(
        "INSERT INTO invoices (series_id, number, display_number, customer_name, "
        "tax_rate) VALUES (?, 1, 'FAC-000001', 'Cliente viejo', '16.00')",
        (serie,),
    )
    assert "bs_rate" not in _columnas_de_invoices(conn)

    init_schema(conn)  # se puede llamar siempre: aca es donde migra

    assert "bs_rate" in _columnas_de_invoices(conn)
    fila = conn.execute("SELECT bs_rate FROM invoices").fetchone()
    assert fila["bs_rate"] is None  # la factura vieja sigue ahi, sin tasa


def test_migrar_dos_veces_no_falla():
    conn = _conn()
    init_schema(conn)

    init_schema(conn)
    init_schema(conn)

    assert "bs_rate" in _columnas_de_invoices(conn)


def test_migra_una_base_de_movimientos_sin_counted_by_ni_note():
    """
    'counted_by' y 'note' se agregaron para el ajuste de stock (quien
    conto, bitacora opcional). Una base con movimientos ya guardados
    desde antes tiene que poder sumar las columnas sin perder esas
    filas.
    """
    conn = _conn()
    init_schema(conn)
    conn.execute("ALTER TABLE inventory_movements DROP COLUMN counted_by")
    conn.execute("ALTER TABLE inventory_movements DROP COLUMN note")
    conn.execute("INSERT INTO products (sku, name) VALUES ('FER-0001', 'Tornillo')")
    conn.execute(
        "INSERT INTO inventory_movements (product_id, type, quantity_milli, reason) "
        "VALUES (1, 'ADJUSTMENT', -3000, 'Conteo físico')"
    )
    assert not {"counted_by", "note"} <= _columnas_de_movimientos(conn)

    init_schema(conn)  # aca migra

    assert {"counted_by", "note"} <= _columnas_de_movimientos(conn)
    fila = conn.execute("SELECT counted_by, note FROM inventory_movements").fetchone()
    assert fila["counted_by"] is None
    assert fila["note"] is None


def test_base_nueva_ya_sale_con_los_nombres_correctos():
    conn = _conn()
    init_schema(conn)

    assert _tiers(conn) == [
        (1, "Mayor", 0),
        (2, "Especial", 0),
        (3, "Detal", 1),
    ]


def test_migra_una_base_con_los_nombres_viejos():
    conn = _conn()
    init_schema(conn)
    # Simula una base ya sembrada ANTES de este cambio (los nombres viejos
    # de la Fase 8, is_default en 'Detal' de position 1).
    conn.execute(
        "UPDATE price_tiers SET name = '_tmp1', is_default = 0 WHERE position = 1"
    )
    conn.execute(
        "UPDATE price_tiers SET name = '_tmp2', is_default = 0 WHERE position = 2"
    )
    conn.execute(
        "UPDATE price_tiers SET name = '_tmp3', is_default = 0 WHERE position = 3"
    )
    conn.execute("UPDATE price_tiers SET name = 'Detal' WHERE position = 1")
    conn.execute("UPDATE price_tiers SET name = 'Mayor' WHERE position = 2")
    conn.execute("UPDATE price_tiers SET name = 'Especial' WHERE position = 3")
    conn.execute("UPDATE price_tiers SET is_default = 1 WHERE position = 1")

    init_schema(conn)  # se puede llamar siempre: aca es donde migra

    assert _tiers(conn) == [
        (1, "Mayor", 0),
        (2, "Especial", 0),
        (3, "Detal", 1),
    ]


def test_migracion_no_desata_los_precios_del_producto():
    conn = _conn()
    init_schema(conn)
    # Simula una base vieja: los nombres de la Fase 8 (name es UNIQUE,
    # asi que hay que pasar por nombres temporales para rotarlos).
    for pos in (1, 2, 3):
        conn.execute(
            "UPDATE price_tiers SET name = ? WHERE position = ?", (f"_tmp{pos}", pos)
        )
    conn.execute("UPDATE price_tiers SET name = 'Detal' WHERE position = 1")
    conn.execute("UPDATE price_tiers SET name = 'Mayor' WHERE position = 2")
    conn.execute("UPDATE price_tiers SET name = 'Especial' WHERE position = 3")
    tier_id = conn.execute("SELECT id FROM price_tiers WHERE position = 1").fetchone()[
        "id"
    ]
    cat_id = conn.execute(
        "INSERT INTO categories (code, name) VALUES ('GEN2', 'Gen2')"
    ).lastrowid
    product_id = conn.execute(
        "INSERT INTO products (sku, name, category_id) VALUES ('X-0001', 'X', ?)",
        (cat_id,),
    ).lastrowid
    conn.execute(
        "INSERT INTO product_prices (product_id, tier_id, price_milli) "
        "VALUES (?, ?, 1000)",
        (product_id, tier_id),
    )

    init_schema(conn)  # migra los nombres de nuevo (no-op si ya migro)

    precio = conn.execute(
        "SELECT price_milli FROM product_prices WHERE product_id = ? AND tier_id = ?",
        (product_id, tier_id),
    ).fetchone()
    assert precio["price_milli"] == 1000  # el vinculo tier_id no se movio


def test_backfill_product_supplier_entries_rellena_los_vinculos_sin_historial():
    """
    product_supplier_entries nacio despues de que el importador de la
    Fase 3 (y "Registrar proveedor..." durante un tiempo) ya habian
    llenado product_suppliers directo. Sin esto, la tabla "Entregas" de
    la ficha mostraria "sin entregas" para los 1.936 productos migrados
    del sistema viejo.
    """
    conn = _conn()
    init_schema(conn)
    conn.execute("INSERT INTO products (sku, name) VALUES ('FER-0001', 'Tornillo')")
    conn.execute("INSERT INTO suppliers (sku, name) VALUES ('PROV-0001', 'LIMAR')")
    # Simula el importador viejo: escribe product_suppliers directo, sin
    # pasar por product_supplier_entries.
    conn.execute(
        "INSERT INTO product_suppliers "
        "(product_id, supplier_id, contact_name, last_purchase, last_cost_milli) "
        "VALUES (1, 1, 'RAMON', '2024-01-15', 470)"
    )
    assert (
        conn.execute("SELECT COUNT(*) AS n FROM product_supplier_entries").fetchone()[
            "n"
        ]
        == 0
    )

    init_schema(conn)  # aca rellena

    (entrada,) = conn.execute(
        "SELECT product_id, supplier_id, contact_name, entry_date, cost_milli "
        "FROM product_supplier_entries"
    ).fetchall()
    assert entrada["product_id"] == 1
    assert entrada["supplier_id"] == 1
    assert entrada["contact_name"] == "RAMON"
    assert entrada["entry_date"] == "2024-01-15"
    assert entrada["cost_milli"] == 470


def test_backfill_product_supplier_entries_no_duplica_si_ya_tenia_historial():
    """Un vinculo que ya se registro con la app (tiene su propia entrada)
    no recibe una segunda, aunque product_suppliers tambien tenga datos."""
    conn = _conn()
    init_schema(conn)
    conn.execute("INSERT INTO products (sku, name) VALUES ('FER-0001', 'Tornillo')")
    conn.execute("INSERT INTO suppliers (sku, name) VALUES ('PROV-0001', 'LIMAR')")
    conn.execute(
        "INSERT INTO product_suppliers (product_id, supplier_id, last_cost_milli) "
        "VALUES (1, 1, 470)"
    )
    conn.execute(
        "INSERT INTO product_supplier_entries (product_id, supplier_id, cost_milli) "
        "VALUES (1, 1, 999)"
    )

    init_schema(conn)

    assert (
        conn.execute("SELECT COUNT(*) AS n FROM product_supplier_entries").fetchone()[
            "n"
        ]
        == 1
    )


def test_backfill_product_supplier_entries_corre_dos_veces_sin_duplicar():
    conn = _conn()
    init_schema(conn)
    conn.execute("INSERT INTO products (sku, name) VALUES ('FER-0001', 'Tornillo')")
    conn.execute("INSERT INTO suppliers (sku, name) VALUES ('PROV-0001', 'LIMAR')")
    conn.execute(
        "INSERT INTO product_suppliers (product_id, supplier_id, last_cost_milli) "
        "VALUES (1, 1, 470)"
    )

    init_schema(conn)
    init_schema(conn)

    assert (
        conn.execute("SELECT COUNT(*) AS n FROM product_supplier_entries").fetchone()[
            "n"
        ]
        == 1
    )


# ------------------------------------------------------------
# backup(): cierra la copia y la verifica
# ------------------------------------------------------------
def test_backup_cierra_la_conexion_de_la_copia(tmp_path, monkeypatch):
    """
    El "with sqlite3.connect()" de antes solo hacia commit, no cerraba: en
    Windows el archivo quedaba abierto y el pendrive no se podia expulsar.
    """
    import sqlite3 as sqlite3_real

    from flexstock.data import connection as connection_module

    abiertas = []
    conectar = sqlite3_real.connect

    def _espiar(*args, **kwargs):
        conexion = conectar(*args, **kwargs)
        abiertas.append(conexion)
        return conexion

    origen = conectar(":memory:")
    origen.row_factory = sqlite3_real.Row
    connection_module.init_schema(origen)
    monkeypatch.setattr(connection_module.sqlite3, "connect", _espiar)

    connection_module.backup(origen, tmp_path)

    (copia,) = abiertas
    with pytest.raises(sqlite3_real.ProgrammingError):
        copia.execute("SELECT 1")  # cerrada


def test_backup_verifica_que_la_copia_este_sana(tmp_path):
    import sqlite3 as sqlite3_real

    from flexstock.data import connection as connection_module

    origen = sqlite3_real.connect(":memory:")
    origen.row_factory = sqlite3_real.Row
    connection_module.init_schema(origen)

    ruta = connection_module.backup(origen, tmp_path)

    with sqlite3_real.connect(ruta) as copia:
        assert copia.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        triggers = copia.execute(
            "SELECT COUNT(*) FROM sqlite_master WHERE type = 'trigger'"
        ).fetchone()[0]
    assert triggers == 7  # el respaldo se lleva tambien las reglas
