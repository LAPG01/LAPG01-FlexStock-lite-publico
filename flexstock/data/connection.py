"""
Conexion a SQLite. Unico archivo del proyecto que abre la base de datos.

Los PRAGMA de aqui no son opcionales:
  foreign_keys=ON  -> SQLite IGNORA las FK por defecto. Sin esto puedes
                      insertar una factura con un cliente inexistente.
                      No es persistente: va en CADA conexion.
  journal_mode=WAL -> lectores y escritor dejan de bloquearse entre si,
                      y el archivo aguanta mejor un corte de luz.
                      Si es persistente: se graba en el archivo .db.
  busy_timeout     -> si la base esta ocupada, espera en vez de reventar.
  synchronous=NORMAL -> con WAL es seguro y bastante mas rapido que FULL.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import closing, contextmanager
from datetime import datetime
from pathlib import Path

from ..config import BACKUP_DIR, DB_PATH, SCHEMA_PATH, TRIGGERS_PATH, ensure_dirs


def connect(db_path: Path | None = None) -> sqlite3.Connection:
    ensure_dirs()
    path = db_path or DB_PATH
    conn = sqlite3.connect(
        path, isolation_level=None
    )  # control manual de transacciones
    conn.row_factory = sqlite3.Row  # filas accesibles por nombre
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA synchronous = NORMAL")
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def init_schema(conn: sqlite3.Connection) -> None:
    """Crea las tablas si no existen. Idempotente: se puede llamar siempre."""
    conn.executescript(SCHEMA_PATH.read_text(encoding="utf-8"))
    _upgrade_customers_table(conn)
    _upgrade_invoices_table(conn)
    _upgrade_suppliers_table(conn)
    _upgrade_price_tier_names(conn)
    _upgrade_inventory_movements_table(conn)
    _backfill_product_supplier_entries(conn)
    # Al final: los triggers nombran columnas que las migraciones de
    # arriba recien agregan en una base vieja (ver triggers.sql).
    create_integrity_triggers(conn)


def create_integrity_triggers(conn: sqlite3.Connection) -> None:
    """Las reglas 3 y 4 hechas cumplir por la base (ver triggers.sql)."""
    conn.executescript(TRIGGERS_PATH.read_text(encoding="utf-8"))


# Columnas agregadas a 'customers' en la Fase 5, despues de que algunas
# bases locales ya tenian la tabla creada (vacia: la Fase 6 -facturacion-
# todavia no existe). 'CREATE TABLE IF NOT EXISTS' de arriba no las agrega
# a una tabla que ya existe, asi que hace falta este empujon con ALTER.
_CUSTOMER_COLUMN_UPGRADES = {
    "sku": "ALTER TABLE customers ADD COLUMN sku TEXT",
    "is_wholesale": (
        "ALTER TABLE customers ADD COLUMN is_wholesale INTEGER "
        "NOT NULL DEFAULT 0 CHECK (is_wholesale IN (0, 1))"
    ),
}


def _upgrade_customers_table(conn: sqlite3.Connection) -> None:
    """
    SQLite no permite UNIQUE ni NOT NULL-sin-default en un ALTER TABLE
    ADD COLUMN, asi que 'sku' entra aca sin esas restricciones (no importa:
    la tabla nunca tuvo filas todavia) y el UNIQUE se cubre aparte con un
    indice. En una base nueva esto es un no-op: las columnas ya vinieron
    en el CREATE TABLE.
    """
    columnas = {row["name"] for row in conn.execute("PRAGMA table_info(customers)")}
    for columna, ddl in _CUSTOMER_COLUMN_UPGRADES.items():
        if columna not in columnas:
            conn.execute(ddl)
    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_customers_sku ON customers(sku)"
    )


# 'customer_address' se agrego en la Fase 7 (PDF de facturas), despues de
# que algunas bases locales ya tenian 'invoices' creada sin esa columna.
# 'payment_method' se agrego despues (cierre de caja): a diferencia de
# 'status' (que es NOT NULL con DEFAULT 'ISSUED'), esta columna es
# NULLABLE - no lleva NOT NULL ni DEFAULT, asi que el ALTER no necesita
# resolver un valor para las filas que ya existian (quedan en NULL sin
# mas, que es exactamente lo que se quiere: "sin especificar"). El CHECK
# si se puede agregar en el mismo ALTER (a diferencia de UNIQUE, que
# _upgrade_customers_table() de arriba tiene que resolver aparte con un
# indice): SQLite acepta CHECK en un ALTER TABLE ADD COLUMN siempre que
# las filas existentes lo puedan cumplir, y NULL cumple cualquier CHECK
# de la forma 'columna IN (...)' porque la comparacion da NULL (ni
# verdadero ni falso) en vez de fallar.
def _upgrade_invoices_table(conn: sqlite3.Connection) -> None:
    columnas = {row["name"] for row in conn.execute("PRAGMA table_info(invoices)")}
    if "customer_address" not in columnas:
        conn.execute("ALTER TABLE invoices ADD COLUMN customer_address TEXT")
    if "payment_method" not in columnas:
        conn.execute(
            "ALTER TABLE invoices ADD COLUMN payment_method TEXT "
            "CHECK (payment_method IN "
            "('EFECTIVO_BS', 'EFECTIVO_USD', 'TRANSFERENCIA', 'TARJETA', 'ZELLE'))"
        )
    # Tasa en Bs con la que se paso la factura a bolivares al confirmarla
    # (ver schema.sql). Nullable: las facturas viejas no la tienen.
    if "bs_rate" not in columnas:
        conn.execute("ALTER TABLE invoices ADD COLUMN bs_rate TEXT")
    # Tasa de referencia con que se alteraron los precios (ver schema.sql).
    # Nullable: NULL = se cobro el precio del catalogo tal cual.
    if "ref_rate" not in columnas:
        conn.execute("ALTER TABLE invoices ADD COLUMN ref_rate TEXT")


# 'sku' y 'contact_name' se agregaron en la Fase 8 (proveedores en la UI).
_SUPPLIER_COLUMN_UPGRADES = {
    "sku": "ALTER TABLE suppliers ADD COLUMN sku TEXT",
    "contact_name": "ALTER TABLE suppliers ADD COLUMN contact_name TEXT",
}


def _upgrade_suppliers_table(conn: sqlite3.Connection) -> None:
    """
    A diferencia de customers (que nunca tenia filas cuando se le agrego
    el sku, Fase 5), suppliers si puede tener filas ya cargadas por el
    importador de la Fase 3 antes de que existiera esta columna: no
    alcanza con agregar la columna vacia, hay que rellenarles un sku a
    las que ya existian. Se usa el id de cada fila como numero de
    secuencia (unico y ya asignado, no hace falta contar nada mas).
    """
    columnas = {row["name"] for row in conn.execute("PRAGMA table_info(suppliers)")}
    for columna, ddl in _SUPPLIER_COLUMN_UPGRADES.items():
        if columna not in columnas:
            conn.execute(ddl)

    sin_sku = conn.execute(
        "SELECT id FROM suppliers WHERE sku IS NULL ORDER BY id"
    ).fetchall()
    for row in sin_sku:
        conn.execute(
            "UPDATE suppliers SET sku = ? WHERE id = ?",
            (f"PROV-{row['id']:04d}", row["id"]),
        )

    conn.execute(
        "CREATE UNIQUE INDEX IF NOT EXISTS idx_suppliers_sku ON suppliers(sku)"
    )


# Los nombres de price_tiers se intercambiaron de lugar (misma 'position',
# 1/2/3, que usa legacy_importer.py para mapear Precio1/2/3 del txt viejo -
# eso NO cambia): 'Detal' (venta de mostrador, el precio mas alto) pasa a
# position 3, 'Mayor' (venta al por mayor, el mas bajo) pasa a position 1,
# 'Especial' se queda en el medio. 'is_default' se mueve con 'Detal': el
# default de una venta de mostrador es el precio de detal, no el de mayor.
_PRICE_TIER_NAME_BY_POSITION = {1: "Mayor", 2: "Especial", 3: "Detal"}


def _upgrade_price_tier_names(conn: sqlite3.Connection) -> None:
    """
    Bases que ya tenian price_tiers sembrada con los nombres viejos
    (Detal=1/Mayor=2/Especial=3, INSERT OR IGNORE de schema.sql no las
    toca) quedan con los nombres correctos. No toca product_prices: los
    precios siguen atados al mismo tier_id/position, solo se reetiqueta
    lo que ve la persona.

    Nombres temporales primero: 'name' es UNIQUE, y renombrar directo
    ('Detal'->'Mayor' mientras otra fila todavia se llama 'Mayor')
    choca contra esa restriccion a mitad de la rotacion.
    """
    filas = conn.execute("SELECT id, position, name FROM price_tiers").fetchall()
    si_falta_actualizar = any(
        row["name"] != _PRICE_TIER_NAME_BY_POSITION.get(row["position"], row["name"])
        for row in filas
    )
    if not si_falta_actualizar:
        return

    for row in filas:
        conn.execute(
            "UPDATE price_tiers SET name = ? WHERE id = ?",
            (f"_tmp_{row['id']}", row["id"]),
        )
    for row in filas:
        nuevo_nombre = _PRICE_TIER_NAME_BY_POSITION.get(row["position"])
        if nuevo_nombre is not None:
            conn.execute(
                "UPDATE price_tiers SET name = ?, is_default = ? WHERE id = ?",
                (nuevo_nombre, int(nuevo_nombre == "Detal"), row["id"]),
            )


# 'counted_by' y 'note' se agregaron para que un ajuste de stock (ver
# StockAdjustmentPage) pueda anotar quien conto y una nota libre, ademas
# del motivo. Ambas NULLABLE: los movimientos viejos (ventas, ajustes
# anteriores) quedan en NULL, que es exactamente "sin ese dato".
_INVENTORY_MOVEMENT_COLUMN_UPGRADES = {
    "counted_by": "ALTER TABLE inventory_movements ADD COLUMN counted_by TEXT",
    "note": "ALTER TABLE inventory_movements ADD COLUMN note TEXT",
}


def _upgrade_inventory_movements_table(conn: sqlite3.Connection) -> None:
    columnas = {
        row["name"] for row in conn.execute("PRAGMA table_info(inventory_movements)")
    }
    for columna, ddl in _INVENTORY_MOVEMENT_COLUMN_UPGRADES.items():
        if columna not in columnas:
            conn.execute(ddl)


def _backfill_product_supplier_entries(conn: sqlite3.Connection) -> None:
    """
    product_supplier_entries (el historial de entregas, Fase 8-bis) nacio
    DESPUES de que el importador de la Fase 3 ya habia llenado
    product_suppliers directo, sin pasar por entregas - y de que
    "Registrar proveedor..." lo hiciera asi durante un tiempo. Sin esto,
    la tabla "Entregas" de la ficha (que lee solo product_supplier_
    entries, ver ProductService.list_all_supplier_entries) mostraria
    "sin entregas" para los 1.936 productos migrados del sistema viejo,
    aunque product_suppliers diga bien claro quien se los trae.

    Rellena UNA entrada por cada vinculo que todavia no tenga ninguna,
    con los mismos datos que ya tenia en product_suppliers (como si se
    hubiera registrado una sola vez). Idempotente: el NOT EXISTS hace
    que una segunda corrida no duplique nada, y un vinculo que ya tiene
    aunque sea una entrega (la normal, desde que se registra con la
    app) no se toca.
    """
    conn.execute(
        "INSERT INTO product_supplier_entries "
        "(product_id, supplier_id, contact_name, entry_date, cost_milli, note) "
        "SELECT ps.product_id, ps.supplier_id, ps.contact_name, ps.last_purchase, "
        "       ps.last_cost_milli, ps.note "
        "FROM product_suppliers ps "
        "WHERE NOT EXISTS ("
        "    SELECT 1 FROM product_supplier_entries pse "
        "    WHERE pse.product_id = ps.product_id "
        "      AND pse.supplier_id = ps.supplier_id"
        ")"
    )


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """
    Todo o nada.

        with transaction(conn):
            crear_factura()
            registrar_movimientos()
            descontar_stock()

    Si algo lanza una excepcion, ROLLBACK y no paso nada. Nunca vas a
    tener una factura emitida sin su descuento de stock.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK")
        raise
    else:
        conn.execute("COMMIT")


def backup(conn: sqlite3.Connection, dest_dir: Path | None = None) -> Path:
    """
    Respaldo consistente. Usa la API backup() de SQLite en vez de copiar
    el archivo: con WAL activo, el .db por si solo puede no tener las
    ultimas operaciones.

    dest_dir: carpeta destino. None (el caso normal) usa BACKUP_DIR; con
    una carpeta propia (ej. un pendrive, elegida via filedialog en
    BackupDialog) el respaldo sale de la carpeta fija de la app sin
    tocar ninguna otra logica de esta funcion - sigue siendo el mismo
    archivo .db consistente, solo cambia donde se escribe.
    """
    directorio = dest_dir or BACKUP_DIR
    directorio.mkdir(parents=True, exist_ok=True)
    # Hora local a proposito (no UTC): el resto del esquema tambien usa
    # datetime('now', 'localtime'), y un nombre de archivo en UTC
    # desentonaria con eso para quien mire la carpeta de respaldos.
    # Microsegundos incluidos (Fase 8): dos respaldos en el mismo
    # segundo (dos clicks rapidos de "Respaldar ahora", o un bucle de
    # tests) pisarian el archivo del primero si solo tuvieran segundos.
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")  # noqa: DTZ005
    dest = directorio / f"flexstock_{stamp}.db"
    # closing() y no un "with" pelado: el "with" de sqlite3 solo hace
    # commit, NO cierra la conexion. En Windows el archivo quedaba abierto
    # y un pendrive no se podia expulsar hasta cerrar la aplicacion.
    with closing(sqlite3.connect(dest)) as target:
        conn.backup(target)
        # Un respaldo que no se puede abrir es peor que ninguno: da una
        # tranquilidad falsa. Se verifica la copia antes de darla por buena.
        resultado = target.execute("PRAGMA integrity_check").fetchone()[0]
    if resultado != "ok":
        dest.unlink(missing_ok=True)
        raise OSError(f"El respaldo quedo danado ({resultado}); no se guardo.")
    return dest


def get_setting(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO settings (key, value, updated_at) "
        "VALUES (?, ?, datetime('now','localtime')) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
        "updated_at = excluded.updated_at",
        (key, value),
    )
