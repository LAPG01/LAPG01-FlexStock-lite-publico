"""
Repositorio de productos. Unico archivo que escribe SQL para la tabla
'products' y sus relaciones (precios, proveedores).

Politica de carga (ver CLAUDE.md):

    list_summary(...)        -> filas sueltas, SIN relaciones. Para la
                                 tabla de listado (1.936 productos).
    get(id)                  -> un producto completo: precios y
                                 proveedores incluidos.
    load_suppliers_for(...)  -> carga en lote de proveedores para una
                                 lista de productos ya obtenidos, en UNA
                                 consulta. Nunca llamar esto por producto
                                 dentro de un bucle: eso es el N+1 que
                                 prohibe el CLAUDE.md.
    load_prices_for(...)     -> lo mismo que load_suppliers_for() pero
                                 para product_prices (usado por la
                                 columna 'Precio 3' de la tabla de
                                 listado).
    create(product)          -> inserta producto + precios en una
                                 transaccion. NO acepta stock inicial:
                                 eso es trabajo de StockService.
    update(product)          -> actualiza campos editables y reemplaza
                                 los precios. NO toca current_stock
                                 (cache) ni legacy_code (puente de
                                 auditoria contra el sistema viejo).
    adjust_stock(id, delta)  -> UNICO metodo que toca current_stock.
                                 Ver su docstring: es la excepcion a la
                                 nota de transacciones de abajo.

NOTA sobre transacciones: create() y update() abren su propia
transaccion con 'with transaction(conn):'. Eso los hace atomicos por
si solos, pero por eso mismo NO se pueden llamar desde adentro de otra
transaccion ya abierta (SQLite no anida BEGIN). adjust_stock() es la
excepcion: la introduce la Fase 6 (facturacion), que sí necesita
componer una escritura de producto (el cache de stock) con la insercion
de la factura y su InventoryMovement en una sola transaccion atomica.
"""

import sqlite3
from collections.abc import Iterator
from datetime import date

from ...domain.entities import (
    Product,
    ProductLink,
    ProductPrice,
    StockOverview,
    SupplierEntry,
    SupplierLink,
)
from ...domain.errors import DuplicateCode
from ..connection import transaction

# SQLite limita a 999 parametros por consulta (SQLITE_MAX_VARIABLE_NUMBER).
# Con 900 queda margen y nunca hace falta tocar esto para el catalogo actual.
_IN_CLAUSE_CHUNK_SIZE = 900

_PRODUCT_COLUMNS = (
    "id, sku, legacy_code, name, description, category_id, "
    "unit, cost_milli, current_stock, min_stock, is_active"
)


class ProductRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Listado: sin relaciones
    # ------------------------------------------------------------
    def list_summary(self, only_active: bool = True) -> list[Product]:
        """
        Una consulta, sin joins a precios ni proveedores. Pensada para
        la tabla que muestra el catalogo completo: cargar relaciones
        aqui seria trabajo que la grilla no usa.
        """
        sql = f"SELECT {_PRODUCT_COLUMNS} FROM products"
        if only_active:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"

        rows = self._conn.execute(sql).fetchall()
        return [self._row_to_product(row) for row in rows]

    def count_by_category(self, category_id: int) -> int:
        """
        Cuantos productos tiene ya una categoria (activos e inactivos:
        las filas de products no se borran, asi que contar todas evita
        que un numero de secuencia se repita).
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM products WHERE category_id = ?",
            (category_id,),
        ).fetchone()
        return row["n"]

    # ------------------------------------------------------------
    # Ficha individual: producto completo
    # ------------------------------------------------------------
    def get(self, product_id: int) -> Product | None:
        """
        Tres consultas fijas (producto, precios, proveedores). No es
        N+1: N=1 porque es un solo producto, sin importar cuantos haya
        en el catalogo.
        """
        row = self._conn.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM products WHERE id = ?",
            (product_id,),
        ).fetchone()
        if row is None:
            return None

        product = self._row_to_product(row)
        product.prices = self._load_prices(product_id)
        self.load_suppliers_for([product])  # reusa la carga en lote para N=1
        return product

    # ------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------
    def create(self, product: Product) -> int:
        """
        Inserta el producto y sus precios en una sola transaccion.

        El stock inicial no se acepta aca: 'current_stock' es un cache
        (Regla 2 del CLAUDE.md) que solo StockService puede tocar, y
        siempre junto con su InventoryMovement.
        """
        with transaction(self._conn):
            return self.insert(product)

    def insert(self, product: Product) -> int:
        """
        Lo mismo que create() pero SIN abrir transaccion: para cuando
        quien llama ya abrio una y el alta tiene que quedar adentro junto
        con otra escritura - el producto con su existencia inicial (ver
        ProductService.create_product y StockService.atomic). SQLite no
        anida BEGIN, por eso son dos metodos y no un parametro.
        """
        if product.current_stock != 0:
            raise ValueError(
                "create() no acepta stock inicial: usa StockService con "
                "un movimiento INITIAL despues de crear el producto."
            )

        try:
            cur = self._conn.execute(
                "INSERT INTO products "
                "(sku, legacy_code, name, description, category_id, "
                " unit, cost_milli, min_stock, is_active) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    product.sku,
                    product.legacy_code,
                    product.name,
                    product.description,
                    product.category_id,
                    product.unit,
                    product.cost_milli,
                    product.min_stock,
                    int(product.is_active),
                ),
            )
        except sqlite3.IntegrityError as e:
            duplicate = self._as_duplicate_code(e, product)
            if duplicate is None:
                raise  # no es un UNIQUE conocido: p.ej. un CHECK, no lo disfrazamos
            raise duplicate from e

        product_id = cur.lastrowid
        self._insert_prices(product_id, product.prices)

        product.id = product_id
        return product_id

    def update(self, product: Product) -> None:
        """
        Actualiza los campos editables y reemplaza los precios.

        Fuera del SET a proposito:
          - current_stock: cache, solo lo toca StockService.
          - legacy_code:   puente de auditoria, no se corrige por aca.
        """
        if product.id is None:
            raise ValueError("No se puede actualizar un producto sin id.")

        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "UPDATE products SET "
                    "sku = ?, name = ?, description = ?, category_id = ?, "
                    "unit = ?, cost_milli = ?, min_stock = ?, is_active = ?, "
                    "updated_at = datetime('now', 'localtime') "
                    "WHERE id = ?",
                    (
                        product.sku,
                        product.name,
                        product.description,
                        product.category_id,
                        product.unit,
                        product.cost_milli,
                        product.min_stock,
                        int(product.is_active),
                        product.id,
                    ),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate_code(e, product)
                if duplicate is None:
                    raise  # no es un UNIQUE conocido: p.ej. un CHECK, no lo disfrazamos
                raise duplicate from e

            if cur.rowcount == 0:
                raise ValueError(f"No existe un producto con id={product.id}.")

            self._conn.execute(
                "DELETE FROM product_prices WHERE product_id = ?", (product.id,)
            )
            self._insert_prices(product.id, product.prices)

    def adjust_stock(self, product_id: int, delta_milli: int) -> None:
        """
        Suma (o resta, si delta_milli es negativo) al cache current_stock.

        NO abre su propia transaccion (a diferencia de create()/update()
        de arriba): lo llama StockService dentro de la transaccion que
        ya abrio quien orquesta la operacion completa (por ejemplo
        BillingService.confirmar()), junto con el INSERT del
        InventoryMovement correspondiente. Llamarlo suelto, sin una
        transaccion abierta alrededor, deja el cache desincronizado del
        movimiento si algo falla a mitad de camino - por eso este metodo
        es exclusivo de StockService (regla 2 del CLAUDE.md), nunca de
        ProductService.
        """
        cur = self._conn.execute(
            "UPDATE products SET current_stock = current_stock + ?, "
            "updated_at = datetime('now', 'localtime') WHERE id = ?",
            (delta_milli, product_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No existe un producto con id={product_id}.")

    def link_supplier(
        self,
        product_id: int,
        supplier_id: int,
        *,
        contact_name: str | None = None,
        last_purchase: date | None = None,
        last_cost_milli: int | None = None,
        note: str | None = None,
    ) -> None:
        """
        Registra una entrega: agrega una fila NUEVA a
        product_supplier_entries (el historial, append-only - ver
        schema.sql) y de paso deja product_suppliers (el vinculo
        ACTUAL) apuntando a esta entrega. (product_id, supplier_id) es
        la PK compuesta de esa segunda tabla: volver a vincular el
        mismo par actualiza el vinculo en vez de fallar por UNIQUE, pero
        la entrega anterior sigue en el historial, no se pisa. Pensado
        tambien para el importador de la Fase 3, donde un producto
        puede reprocesarse con datos mas frescos.

        Una sola transaccion: si quedara a medias, el vinculo actual y
        el historial no podrian desincronizarse (mismo criterio que
        StockService con products.current_stock e InventoryMovement).
        """
        entry_date = last_purchase.isoformat() if last_purchase else None
        with transaction(self._conn):
            self._conn.execute(
                "INSERT INTO product_supplier_entries "
                "(product_id, supplier_id, contact_name, entry_date, "
                " cost_milli, note) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    product_id,
                    supplier_id,
                    contact_name,
                    entry_date,
                    last_cost_milli,
                    note,
                ),
            )
            self._conn.execute(
                "INSERT INTO product_suppliers "
                "(product_id, supplier_id, contact_name, last_purchase, "
                " last_cost_milli, note) "
                "VALUES (?, ?, ?, ?, ?, ?) "
                "ON CONFLICT(product_id, supplier_id) DO UPDATE SET "
                "contact_name = excluded.contact_name, "
                "last_purchase = excluded.last_purchase, "
                "last_cost_milli = excluded.last_cost_milli, "
                "note = excluded.note",
                (
                    product_id,
                    supplier_id,
                    contact_name,
                    entry_date,
                    last_cost_milli,
                    note,
                ),
            )

    def list_entries(self, product_id: int, supplier_id: int) -> list[SupplierEntry]:
        """
        Historial completo de entregas de ESTE par producto-proveedor,
        la mas reciente primero. COALESCE empuja las fechas en blanco al
        final (en vez de confundirlas con '0000-00-00' siendo la mas
        vieja): a igual fecha (o ambas en blanco), el id mas alto -el
        ultimo que se registro- va primero.
        """
        rows = self._conn.execute(
            "SELECT pse.id, pse.supplier_id, s.name AS supplier_name, "
            "       pse.contact_name, pse.entry_date, pse.cost_milli, pse.note "
            "FROM product_supplier_entries pse "
            "JOIN suppliers s ON s.id = pse.supplier_id "
            "WHERE pse.product_id = ? AND pse.supplier_id = ? "
            "ORDER BY COALESCE(pse.entry_date, '9999-99-99') DESC, pse.id DESC",
            (product_id, supplier_id),
        ).fetchall()
        return [self._row_to_supplier_entry(row) for row in rows]

    def list_entries_for_product(self, product_id: int) -> list[SupplierEntry]:
        """
        TODAS las entregas de este producto, de CUALQUIER proveedor,
        juntas en una sola lista -la mas reciente primero-. Para la
        ficha del producto, que muestra una unica tabla "Entregas" en
        vez de una fila por proveedor (eso es product_suppliers, el
        vinculo ACTUAL): una sola consulta con JOIN, nunca una por
        proveedor (serian a lo sumo 2 por el catalogo actual, pero el
        N+1 que prohibe el CLAUDE.md no depende de cuantas sean hoy).
        """
        rows = self._conn.execute(
            "SELECT pse.id, pse.supplier_id, s.name AS supplier_name, "
            "       pse.contact_name, pse.entry_date, pse.cost_milli, pse.note "
            "FROM product_supplier_entries pse "
            "JOIN suppliers s ON s.id = pse.supplier_id "
            "WHERE pse.product_id = ? "
            "ORDER BY COALESCE(pse.entry_date, '9999-99-99') DESC, pse.id DESC",
            (product_id,),
        ).fetchall()
        return [self._row_to_supplier_entry(row) for row in rows]

    def list_entries_for_export(self) -> list[SupplierEntry]:
        """
        TODAS las entregas del catalogo COMPLETO, de cualquier producto y
        proveedor -para ExportService, no para una pantalla puntual-.
        Por eso (a diferencia de list_entries/list_entries_for_product)
        cada fila trae tambien el producto: una exportacion de todo el
        historial no tiene, como la ficha, un producto ya elegido de
        contexto. Una sola consulta con JOIN a products y suppliers.
        """
        rows = self._conn.execute(
            "SELECT pse.id, pse.product_id, p.sku AS product_sku, "
            "       p.name AS product_name, pse.supplier_id, "
            "       s.name AS supplier_name, pse.contact_name, pse.entry_date, "
            "       pse.cost_milli, pse.note "
            "FROM product_supplier_entries pse "
            "JOIN products p ON p.id = pse.product_id "
            "JOIN suppliers s ON s.id = pse.supplier_id "
            "ORDER BY p.name, COALESCE(pse.entry_date, '9999-99-99') DESC, pse.id DESC"
        ).fetchall()
        return [self._row_to_supplier_entry(row) for row in rows]

    def delete_entry(self, entry_id: int) -> None:
        """
        Borra UNA entrega cargada por error (confirmado por quien llama:
        ver ProductDialog._on_eliminar_entrega). A diferencia de
        inventory_movements (regla 3), esta tabla no es la fuente de un
        TOTAL acumulado -es "cuando se compro y a cuanto", y una fila
        mal tecleada tiene que poder sacarse sin dejar un rastro falso.

        El vinculo ACTUAL (product_suppliers) se recalcula ahi mismo a
        partir de lo que quede para ese par: si la borrada era la mas
        reciente, pasa a reflejar la que sigue; si no quedaba ninguna
        mas, el vinculo entero desaparece (ya no hay ninguna entrega que
        lo respalde). Una sola transaccion.
        """
        with transaction(self._conn):
            fila = self._conn.execute(
                "SELECT product_id, supplier_id FROM product_supplier_entries "
                "WHERE id = ?",
                (entry_id,),
            ).fetchone()
            if fila is None:
                raise ValueError(f"No existe una entrega con id={entry_id}.")
            product_id, supplier_id = fila["product_id"], fila["supplier_id"]

            self._conn.execute(
                "DELETE FROM product_supplier_entries WHERE id = ?", (entry_id,)
            )

            ultima = self._conn.execute(
                "SELECT contact_name, entry_date, cost_milli, note "
                "FROM product_supplier_entries "
                "WHERE product_id = ? AND supplier_id = ? "
                "ORDER BY COALESCE(entry_date, '9999-99-99') DESC, id DESC LIMIT 1",
                (product_id, supplier_id),
            ).fetchone()
            if ultima is None:
                self._conn.execute(
                    "DELETE FROM product_suppliers "
                    "WHERE product_id = ? AND supplier_id = ?",
                    (product_id, supplier_id),
                )
            else:
                self._conn.execute(
                    "UPDATE product_suppliers SET contact_name = ?, "
                    "last_purchase = ?, last_cost_milli = ?, note = ? "
                    "WHERE product_id = ? AND supplier_id = ?",
                    (
                        ultima["contact_name"],
                        ultima["entry_date"],
                        ultima["cost_milli"],
                        ultima["note"],
                        product_id,
                        supplier_id,
                    ),
                )

    def distinct_contacts_for_supplier(self, supplier_id: int) -> list[str]:
        """
        Nombres de contacto distintos que aparecieron en entregas de
        ESTE proveedor, en cualquier producto: para ofrecerlos en vez de
        tipearlos de nuevo (ver RegistrarProveedorDialog). Se completa
        solo con el uso; un contacto que todavia no trajo nada no sale.
        """
        rows = self._conn.execute(
            "SELECT DISTINCT contact_name FROM product_supplier_entries "
            "WHERE supplier_id = ? AND contact_name IS NOT NULL "
            "ORDER BY contact_name",
            (supplier_id,),
        ).fetchall()
        return [row["contact_name"] for row in rows]

    def unlink_supplier(self, product_id: int, supplier_id: int) -> None:
        """
        Borra el vinculo ACTUAL producto-proveedor (product_suppliers).
        Borrar aca SI esta permitido, a diferencia de
        inventory_movements o de las facturas: este vinculo no es un
        registro historico sino "a quien se le compra esto hoy", y uno
        cargado por error tiene que poder sacarse. El historial de
        entregas (product_supplier_entries) NO se toca: "Quitar" saca el
        vinculo de hoy, no borra que alguna vez se le compro a este
        proveedor. No toca ni al producto ni al proveedor.
        """
        with transaction(self._conn):
            cur = self._conn.execute(
                "DELETE FROM product_suppliers WHERE product_id = ? AND supplier_id = ?",
                (product_id, supplier_id),
            )
        if cur.rowcount == 0:
            raise ValueError("Ese proveedor no estaba vinculado a este producto.")

    # ------------------------------------------------------------
    # Carga en lote: evita el N+1 al mostrar varios productos
    # ------------------------------------------------------------
    def load_suppliers_for(self, products: list[Product]) -> None:
        """
        Llena 'product.suppliers' de TODOS los productos recibidos con
        una sola consulta 'WHERE product_id IN (...)' y agrupacion en
        memoria (O(n + m), nunca O(n * m)).
        """
        if not products:
            return

        by_id = {p.id: p for p in products}
        for p in products:
            p.suppliers = []  # limpio: evita duplicar si se llama dos veces

        for chunk in self._chunks(list(by_id), _IN_CLAUSE_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                "SELECT ps.product_id, ps.supplier_id, s.name AS supplier_name, "
                "       ps.contact_name, ps.last_purchase, "
                "       ps.last_cost_milli, ps.note "
                "FROM product_suppliers ps "
                "JOIN suppliers s ON s.id = ps.supplier_id "
                f"WHERE ps.product_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                by_id[row["product_id"]].suppliers.append(
                    self._row_to_supplier_link(row)
                )

    def stock_of(self, product_ids: list[int]) -> dict[int, int]:
        """
        Solo el stock de varios productos: {product_id: current_stock}.

        Existe para no usar get() en un bucle. get() son TRES consultas
        (producto + precios + proveedores) y devuelve el producto
        entero; quien nada mas quiere saber si alcanza el stock -
        BillingService al confirmar una factura - pagaba 3 consultas por
        linea para leer un solo entero. Con esto son 2 columnas de una
        consulta, sin importar cuantas lineas tenga la factura.

        Un id que no existe simplemente no aparece en el dict: quien
        llama decide si eso es un error (ver _stock_shortfalls) o no.
        El troceado es el mismo de load_suppliers_for: SQLite no acepta
        mas de 999 variables en una consulta.
        """
        if not product_ids:
            return {}

        stocks: dict[int, int] = {}
        for chunk in self._chunks(list(product_ids), _IN_CLAUSE_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                f"SELECT id, current_stock FROM products WHERE id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                stocks[row["id"]] = row["current_stock"]
        return stocks

    def stock_overview(self) -> StockOverview:
        """
        Los tres conteos del tablero en UNA consulta: un recorrido de
        products (1.949 filas hoy, < 1 ms) sumando comparaciones, que en
        SQLite valen 0 o 1. Mucho mas barato que list_summary() +
        load_prices_for() (dos consultas y ~7.800 filas) para despues
        contar en Python lo que la base ya puede contar sola.
        """
        row = self._conn.execute(
            "SELECT COUNT(*) AS active,"
            " COALESCE(SUM(current_stock <= 0), 0) AS out_of_stock,"
            " COALESCE(SUM(current_stock < 0), 0) AS negative,"
            # EXISTS por producto usa idx_movements_product: sigue siendo
            # UNA consulta, sin traer movimientos a Python.
            " COALESCE(SUM(EXISTS (SELECT 1 FROM inventory_movements m"
            "   WHERE m.product_id = products.id"
            "   AND m.type IN ('ADJUSTMENT', 'INITIAL'))), 0) AS counted"
            " FROM products WHERE is_active = 1"
        ).fetchone()
        return StockOverview(
            active=row["active"],
            out_of_stock=row["out_of_stock"],
            negative=row["negative"],
            counted=row["counted"],
        )

    def list_needing_attention(self, limit: int = 50) -> list[Product]:
        """
        Los productos activos que piden atencion: existencia negativa
        (se vendio sin stock cargado) o en/bajo su minimo. Negativos
        primero, despues el que menos tiene. Una consulta con LIMIT: el
        tablero de Inicio no necesita las 1.900 filas.
        """
        rows = self._conn.execute(
            f"SELECT {_PRODUCT_COLUMNS} FROM products"
            " WHERE is_active = 1"
            " AND (current_stock < 0 OR (min_stock > 0 AND current_stock <= min_stock))"
            " ORDER BY (current_stock < 0) DESC, current_stock, name"
            " LIMIT ?",
            (limit,),
        ).fetchall()
        return [self._row_to_product(row) for row in rows]

    def list_summary_for_supplier(self, supplier_id: int) -> list[ProductLink]:
        """
        Reverso de load_suppliers_for(): que productos le compra la
        ferreteria a ESTE proveedor puntual (para la ficha de un
        proveedor). Una sola consulta con JOIN, usa el indice
        idx_prodsup_supplier (ver schema.sql) para el WHERE - nunca un
        list_summary() completo filtrado en Python.
        """
        rows = self._conn.execute(
            "SELECT p.id, p.sku, p.name, p.current_stock, "
            "       ps.contact_name, ps.last_purchase, "
            "       ps.last_cost_milli, ps.note "
            "FROM product_suppliers ps "
            "JOIN products p ON p.id = ps.product_id "
            "WHERE ps.supplier_id = ? "
            "ORDER BY p.name",
            (supplier_id,),
        ).fetchall()
        return [self._row_to_product_link(row) for row in rows]

    def load_prices_for(self, products: list[Product]) -> None:
        """
        Llena 'product.prices' de TODOS los productos recibidos con una
        sola consulta 'WHERE product_id IN (...)' y agrupacion en
        memoria (O(n + m), nunca O(n * m)). Mismo patron que
        load_suppliers_for(): para la tabla de listado, que trae
        list_summary() sin relaciones y despues pide esto en lote
        (nunca product_id por product_id dentro de un bucle).
        """
        if not products:
            return

        by_id = {p.id: p for p in products}
        for p in products:
            p.prices = []  # limpio: evita duplicar si se llama dos veces

        for chunk in self._chunks(list(by_id), _IN_CLAUSE_CHUNK_SIZE):
            placeholders = ",".join("?" * len(chunk))
            rows = self._conn.execute(
                "SELECT product_id, tier_id, price_milli FROM product_prices "
                f"WHERE product_id IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                by_id[row["product_id"]].prices.append(
                    ProductPrice(tier_id=row["tier_id"], price_milli=row["price_milli"])
                )

    # ------------------------------------------------------------
    # Helpers privados: una sola forma de construir cada objeto
    # ------------------------------------------------------------
    @staticmethod
    def _row_to_product(row: sqlite3.Row) -> Product:
        return Product(
            id=row["id"],
            sku=row["sku"],
            legacy_code=row["legacy_code"],
            name=row["name"],
            description=row["description"],
            category_id=row["category_id"],
            unit=row["unit"],
            cost_milli=row["cost_milli"],
            current_stock=row["current_stock"],
            min_stock=row["min_stock"],
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _row_to_supplier_link(row: sqlite3.Row) -> SupplierLink:
        last_purchase = row["last_purchase"]
        return SupplierLink(
            supplier_id=row["supplier_id"],
            supplier_name=row["supplier_name"],
            contact_name=row["contact_name"],
            last_purchase=date.fromisoformat(last_purchase) if last_purchase else None,
            last_cost_milli=row["last_cost_milli"],
            note=row["note"],
        )

    @staticmethod
    def _row_to_supplier_entry(row: sqlite3.Row) -> SupplierEntry:
        entry_date = row["entry_date"]
        # product_id/sku/name solo vienen en list_entries_for_export
        # (el historial del catalogo completo, sin un producto de
        # contexto ya elegido): las demas consultas de esta tabla no
        # los piden, y row.keys() es la forma de no pisarlos con un
        # KeyError cuando faltan.
        columnas = row.keys()
        return SupplierEntry(
            id=row["id"],
            supplier_id=row["supplier_id"],
            supplier_name=row["supplier_name"],
            product_id=row["product_id"] if "product_id" in columnas else 0,
            product_sku=row["product_sku"] if "product_sku" in columnas else "",
            product_name=row["product_name"] if "product_name" in columnas else "",
            contact_name=row["contact_name"],
            entry_date=date.fromisoformat(entry_date) if entry_date else None,
            cost_milli=row["cost_milli"],
            note=row["note"],
        )

    @staticmethod
    def _row_to_product_link(row: sqlite3.Row) -> ProductLink:
        last_purchase = row["last_purchase"]
        return ProductLink(
            product_id=row["id"],
            product_sku=row["sku"],
            product_name=row["name"],
            current_stock=row["current_stock"],
            contact_name=row["contact_name"],
            last_purchase=date.fromisoformat(last_purchase) if last_purchase else None,
            last_cost_milli=row["last_cost_milli"],
            note=row["note"],
        )

    def _load_prices(self, product_id: int) -> list[ProductPrice]:
        rows = self._conn.execute(
            "SELECT tier_id, price_milli FROM product_prices WHERE product_id = ?",
            (product_id,),
        ).fetchall()
        return [
            ProductPrice(tier_id=row["tier_id"], price_milli=row["price_milli"])
            for row in rows
        ]

    def _insert_prices(self, product_id: int, prices: list[ProductPrice]) -> None:
        for price in prices:
            self._conn.execute(
                "INSERT INTO product_prices (product_id, tier_id, price_milli) "
                "VALUES (?, ?, ?)",
                (product_id, price.tier_id, price.price_milli),
            )

    @staticmethod
    def _as_duplicate_code(
        error: sqlite3.IntegrityError, product: Product
    ) -> DuplicateCode | None:
        """
        Traduce un UNIQUE violado (sku o legacy_code) al error de dominio.
        Devuelve None si el IntegrityError es otra cosa (ej. un CHECK):
        eso no es un caso de negocio con mensaje amigable, es un bug de
        quien llama, y el caller debe dejarlo pasar tal cual.
        """
        message = str(error)
        if "products.sku" in message:
            return DuplicateCode(f"Ya existe un producto con el SKU '{product.sku}'.")
        if "products.legacy_code" in message:
            return DuplicateCode(
                f"Ya existe un producto con el codigo legado '{product.legacy_code}'."
            )
        return None

    @staticmethod
    def _chunks(items: list, size: int) -> Iterator[list]:
        """Trocea una lista en pedazos de a lo sumo 'size' elementos."""
        for i in range(0, len(items), size):
            yield items[i : i + size]
