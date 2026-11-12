"""
Tests del repositorio de productos.

Usan SQLite en memoria (':memory:'): no tocan disco, corren en
milisegundos. Verifican tanto el contrato (que cada metodo cargue
exactamente lo que promete) como el conteo de consultas donde importa.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.domain.entities import Product, ProductPrice
from flexstock.domain.errors import DuplicateCode


@pytest.fixture
def conn():
    # isolation_level=None: mismo modo autocommit-manual que connection.py
    # de produccion. Sin esto, sqlite3 abre un BEGIN implicito antes de
    # cada INSERT/UPDATE/DELETE y choca con el 'with transaction(...)'
    # del repositorio ("cannot start a transaction within a transaction").
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _crear_producto(conn, sku, name, is_active=1):
    cur = conn.execute(
        "INSERT INTO products (sku, name, is_active) VALUES (?, ?, ?)",
        (sku, name, is_active),
    )
    return cur.lastrowid


def _crear_proveedor(conn, name):
    cur = conn.execute(
        "INSERT INTO suppliers (sku, name) VALUES (?, ?)", (f"PROV-{name}", name)
    )
    return cur.lastrowid


def _vincular_proveedor(conn, product_id, supplier_id, last_purchase=None):
    conn.execute(
        "INSERT INTO product_suppliers (product_id, supplier_id, last_purchase) "
        "VALUES (?, ?, ?)",
        (product_id, supplier_id, last_purchase),
    )


# ------------------------------------------------------------
# list_summary: sin relaciones
# ------------------------------------------------------------
def test_list_summary_no_trae_precios_ni_proveedores(conn):
    pid = _crear_producto(conn, "FER-0001", "TORNILLO HEX")
    sid = _crear_proveedor(conn, "LIMAR")
    _vincular_proveedor(conn, pid, sid)
    conn.execute(
        "INSERT INTO product_prices (product_id, tier_id, price_milli) VALUES (?, 1, 1500)",
        (pid,),
    )
    repo = ProductRepository(conn)

    productos = repo.list_summary(only_active=False)

    assert len(productos) == 1
    assert productos[0].prices == []
    assert productos[0].suppliers == []


def test_list_summary_filtra_inactivos_por_defecto(conn):
    _crear_producto(conn, "FER-0001", "ACTIVO", is_active=1)
    _crear_producto(conn, "FER-0002", "INACTIVO", is_active=0)
    repo = ProductRepository(conn)

    productos = repo.list_summary()

    assert [p.sku for p in productos] == ["FER-0001"]


def test_list_summary_only_active_false_trae_todo(conn):
    _crear_producto(conn, "FER-0001", "ACTIVO", is_active=1)
    _crear_producto(conn, "FER-0002", "INACTIVO", is_active=0)
    repo = ProductRepository(conn)

    productos = repo.list_summary(only_active=False)

    assert {p.sku for p in productos} == {"FER-0001", "FER-0002"}


# ------------------------------------------------------------
# get: producto completo
# ------------------------------------------------------------
def test_get_trae_precios_y_proveedores(conn):
    pid = _crear_producto(conn, "FER-0001", "TORNILLO HEX")
    conn.execute(
        "INSERT INTO product_prices (product_id, tier_id, price_milli) VALUES (?, 1, 1500)",
        (pid,),
    )
    sid = _crear_proveedor(conn, "LIMAR")
    _vincular_proveedor(conn, pid, sid, last_purchase="2024-01-15")

    producto = ProductRepository(conn).get(pid)

    assert producto.sku == "FER-0001"
    assert producto.prices[0].price_milli == 1500
    assert producto.suppliers[0].supplier_name == "LIMAR"
    assert producto.suppliers[0].last_purchase.isoformat() == "2024-01-15"


def test_get_producto_inexistente_devuelve_none(conn):
    assert ProductRepository(conn).get(999) is None


def test_get_sin_relaciones_devuelve_listas_vacias(conn):
    pid = _crear_producto(conn, "FER-0001", "SOLO")

    producto = ProductRepository(conn).get(pid)

    assert producto.prices == []
    assert producto.suppliers == []


# ------------------------------------------------------------
# load_suppliers_for: carga en lote, evita el N+1
# ------------------------------------------------------------
def test_load_suppliers_for_agrupa_cada_proveedor_con_su_producto(conn):
    p1 = _crear_producto(conn, "FER-0001", "UNO")
    p2 = _crear_producto(conn, "FER-0002", "DOS")
    s1 = _crear_proveedor(conn, "LIMAR")
    s2 = _crear_proveedor(conn, "MEDINAS")
    _vincular_proveedor(conn, p1, s1)
    _vincular_proveedor(conn, p1, s2)
    _vincular_proveedor(conn, p2, s1)
    repo = ProductRepository(conn)
    productos = repo.list_summary(only_active=False)

    repo.load_suppliers_for(productos)

    by_sku = {p.sku: p for p in productos}
    assert {s.supplier_name for s in by_sku["FER-0001"].suppliers} == {
        "LIMAR",
        "MEDINAS",
    }
    assert [s.supplier_name for s in by_sku["FER-0002"].suppliers] == ["LIMAR"]


def test_load_suppliers_for_usa_una_sola_consulta(conn):
    """
    Cuenta las consultas reales via el trace callback de sqlite3 (no se
    puede parchar conn.execute: es un metodo de la extension en C, de
    solo lectura). Si esto crece, alguien metio un bucle.
    """
    p1 = _crear_producto(conn, "FER-0001", "UNO")
    p2 = _crear_producto(conn, "FER-0002", "DOS")
    sid = _crear_proveedor(conn, "LIMAR")
    _vincular_proveedor(conn, p1, sid)
    _vincular_proveedor(conn, p2, sid)
    repo = ProductRepository(conn)
    productos = repo.list_summary(only_active=False)

    consultas = []
    conn.set_trace_callback(consultas.append)

    repo.load_suppliers_for(productos)
    conn.set_trace_callback(None)

    ejecutadas = [sql for sql in consultas if "product_suppliers" in sql]
    assert len(ejecutadas) == 1


def test_load_suppliers_for_lista_vacia_no_falla(conn):
    ProductRepository(conn).load_suppliers_for([])  # no debe lanzar


def test_load_suppliers_for_producto_sin_proveedores_queda_con_lista_vacia(conn):
    _crear_producto(conn, "FER-0001", "SOLO")
    repo = ProductRepository(conn)
    productos = repo.list_summary(only_active=False)

    repo.load_suppliers_for(productos)

    assert productos[0].suppliers == []


# ------------------------------------------------------------
# list_summary_for_supplier: reverso, para la ficha de un proveedor
# ------------------------------------------------------------
def test_list_summary_for_supplier_trae_los_productos_del_proveedor(conn):
    p1 = _crear_producto(conn, "FER-0001", "UNO")
    p2 = _crear_producto(conn, "FER-0002", "DOS")
    otro = _crear_producto(conn, "FER-0003", "OTRO")
    s1 = _crear_proveedor(conn, "LIMAR")
    s2 = _crear_proveedor(conn, "MEDINAS")
    _vincular_proveedor(conn, p1, s1, last_purchase="2024-01-15")
    _vincular_proveedor(conn, p2, s1)
    _vincular_proveedor(conn, otro, s2)  # de otro proveedor: no debe aparecer
    repo = ProductRepository(conn)

    productos = repo.list_summary_for_supplier(s1)

    assert {p.product_sku for p in productos} == {"FER-0001", "FER-0002"}
    con_fecha = next(p for p in productos if p.product_sku == "FER-0001")
    assert con_fecha.last_purchase.isoformat() == "2024-01-15"


def test_list_summary_for_supplier_sin_productos_devuelve_lista_vacia(conn):
    s1 = _crear_proveedor(conn, "LIMAR")

    assert ProductRepository(conn).list_summary_for_supplier(s1) == []


# ------------------------------------------------------------
# create: alta con precios, en una transaccion
# ------------------------------------------------------------
def test_create_inserta_producto_y_precios(conn):
    producto = Product(id=None, sku="FER-0001", name="TORNILLO HEX")
    producto.prices = [ProductPrice(tier_id=1, price_milli=1500)]
    repo = ProductRepository(conn)

    nuevo_id = repo.create(producto)

    assert producto.id == nuevo_id
    guardado = repo.get(nuevo_id)
    assert guardado.sku == "FER-0001"
    assert guardado.prices[0].price_milli == 1500
    assert guardado.current_stock == 0  # el default del schema, no lo puso create()


def test_create_rechaza_stock_inicial_distinto_de_cero(conn):
    producto = Product(id=None, sku="FER-0001", name="X", current_stock=5000)
    repo = ProductRepository(conn)

    with pytest.raises(ValueError, match="stock inicial"):
        repo.create(producto)


def test_create_sku_duplicado_lanza_duplicate_code(conn):
    repo = ProductRepository(conn)
    repo.create(Product(id=None, sku="FER-0001", name="UNO"))

    with pytest.raises(DuplicateCode, match="FER-0001"):
        repo.create(Product(id=None, sku="FER-0001", name="OTRO"))


def test_create_es_atomica_si_una_insercion_de_precio_falla(conn):
    """
    Si el precio no cumple el CHECK (price_milli >= 0), la fila de
    producto tampoco debe quedar guardada: todo o nada, misma
    transaccion.
    """
    producto = Product(id=None, sku="FER-0001", name="X")
    producto.prices = [ProductPrice(tier_id=1, price_milli=-100)]
    repo = ProductRepository(conn)

    with pytest.raises(sqlite3.IntegrityError):
        repo.create(producto)

    fila = conn.execute(
        "SELECT * FROM products WHERE sku = ?", ("FER-0001",)
    ).fetchone()
    assert fila is None


# ------------------------------------------------------------
# update: reemplaza campos editables y precios
# ------------------------------------------------------------
def test_update_modifica_campos_y_reemplaza_precios(conn):
    repo = ProductRepository(conn)
    producto = Product(id=None, sku="FER-0001", name="VIEJO", cost_milli=1000)
    producto.prices = [ProductPrice(tier_id=1, price_milli=1500)]
    repo.create(producto)

    producto.name = "NUEVO"
    producto.cost_milli = 2000
    producto.prices = [
        ProductPrice(tier_id=1, price_milli=1800),
        ProductPrice(tier_id=2, price_milli=2200),
    ]
    repo.update(producto)

    actualizado = repo.get(producto.id)
    assert actualizado.name == "NUEVO"
    assert actualizado.cost_milli == 2000
    assert {p.tier_id for p in actualizado.prices} == {1, 2}


def test_update_no_toca_current_stock(conn):
    repo = ProductRepository(conn)
    producto = Product(id=None, sku="FER-0001", name="X")
    repo.create(producto)
    conn.execute(
        "UPDATE products SET current_stock = 7000 WHERE id = ?", (producto.id,)
    )

    producto.name = "CAMBIADO"
    repo.update(producto)  # producto.current_stock sigue en 0 en el objeto Python

    assert (
        conn.execute(
            "SELECT current_stock FROM products WHERE id = ?", (producto.id,)
        ).fetchone()["current_stock"]
        == 7000
    )


def test_update_producto_inexistente_lanza_valueerror(conn):
    repo = ProductRepository(conn)
    producto = Product(id=999, sku="FER-0001", name="X")

    with pytest.raises(ValueError, match="No existe"):
        repo.update(producto)


def test_update_sku_duplicado_lanza_duplicate_code(conn):
    repo = ProductRepository(conn)
    repo.create(Product(id=None, sku="FER-0001", name="UNO"))
    dos = Product(id=None, sku="FER-0002", name="DOS")
    repo.create(dos)

    dos.sku = "FER-0001"
    with pytest.raises(DuplicateCode, match="FER-0001"):
        repo.update(dos)


# ------------------------------------------------------------
# adjust_stock (Fase 6: usado solo por StockService)
# ------------------------------------------------------------
def test_adjust_stock_suma_al_cache(conn):
    repo = ProductRepository(conn)
    producto = Product(id=None, sku="FER-0001", name="Tornillo")
    repo.create(producto)

    repo.adjust_stock(producto.id, 5000)
    repo.adjust_stock(producto.id, -2000)

    assert repo.get(producto.id).current_stock == 3000


def test_adjust_stock_permite_negativo(conn):
    """No bloquea por si solo: la decision de permitir stock negativo
    es de settings.allow_negative_stock, que valida quien orquesta
    (BillingService), no este metodo."""
    repo = ProductRepository(conn)
    producto = Product(id=None, sku="FER-0001", name="Tornillo")
    repo.create(producto)

    repo.adjust_stock(producto.id, -1000)

    assert repo.get(producto.id).current_stock == -1000


def test_adjust_stock_producto_inexistente_lanza_valueerror(conn):
    repo = ProductRepository(conn)

    with pytest.raises(ValueError, match="No existe"):
        repo.adjust_stock(999, 1000)


def test_stock_overview_cuenta_los_productos_ya_contados(conn):
    from decimal import Decimal

    from flexstock.data.repositories.category_repository import CategoryRepository
    from flexstock.data.repositories.inventory_movement_repository import (
        InventoryMovementRepository,
    )
    from flexstock.data.repositories.price_tier_repository import PriceTierRepository
    from flexstock.services.product_service import ProductService
    from flexstock.services.stock_service import StockService

    repo = ProductRepository(conn)
    servicio = ProductService(repo, CategoryRepository(conn), PriceTierRepository(conn))
    stock = StockService(conn, repo, InventoryMovementRepository(conn))
    tier = PriceTierRepository(conn).list_all()[0].id
    cat = servicio.default_category_id()
    contado = servicio.create_product(
        name="A", category_id=cat, prices={tier: Decimal(1)}
    )
    servicio.create_product(name="B", category_id=cat, prices={tier: Decimal(1)})
    stock.ajustar(product_id=contado.id, counted_milli=4000, reason="Conteo")

    resumen = repo.stock_overview()

    assert resumen.counted == 1


def test_list_needing_attention_negativos_primero_y_bajo_minimo(conn):
    from decimal import Decimal

    from flexstock.data.repositories.category_repository import CategoryRepository
    from flexstock.data.repositories.price_tier_repository import PriceTierRepository
    from flexstock.services.product_service import ProductService

    repo = ProductRepository(conn)
    servicio = ProductService(repo, CategoryRepository(conn), PriceTierRepository(conn))
    tier = PriceTierRepository(conn).list_all()[0].id
    cat = servicio.default_category_id()
    ids = {}
    for nombre in ("Normal", "Bajo", "Negativo"):
        ids[nombre] = servicio.create_product(
            name=nombre, category_id=cat, prices={tier: Decimal(1)}
        ).id
    conn.execute(
        "UPDATE products SET current_stock = 9000, min_stock = 1000 WHERE id = ?",
        (ids["Normal"],),
    )
    conn.execute(
        "UPDATE products SET current_stock = 1000, min_stock = 5000 WHERE id = ?",
        (ids["Bajo"],),
    )
    conn.execute(
        "UPDATE products SET current_stock = -2000 WHERE id = ?", (ids["Negativo"],)
    )

    nombres = [p.name for p in repo.list_needing_attention()]

    assert nombres == ["Negativo", "Bajo"]
