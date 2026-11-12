"""
Tests de ProductService: SKU automatico y traduccion Decimal <-> milli.

Usan SQLite en memoria, igual que test_product_repository.py.

    pytest -q
"""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.domain.money import to_milli
from flexstock.services.product_service import ProductService


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def service(conn):
    return ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )


def _crear_categoria(conn, code, name):
    cur = conn.execute(
        "INSERT INTO categories (code, name) VALUES (?, ?)", (code, name)
    )
    return cur.lastrowid


def test_create_product_genera_sku_con_prefijo_de_categoria(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")

    producto = service.create_product(
        name="Abrazadera", category_id=cat_id, prices={1: Decimal("1.50")}
    )

    assert producto.sku == "FER-0001"


def test_create_product_incrementa_secuencia_dentro_de_la_categoria(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(name="Uno", category_id=cat_id, prices={1: Decimal("1.00")})
    service.create_product(name="Dos", category_id=cat_id, prices={1: Decimal("1.00")})

    tres = service.create_product(
        name="Tres", category_id=cat_id, prices={1: Decimal("1.00")}
    )

    assert tres.sku == "FER-0003"


def test_create_product_secuencia_es_independiente_por_categoria(conn, service):
    fer = _crear_categoria(conn, "FER", "Ferreteria")
    ele = _crear_categoria(conn, "ELE", "Electricidad")
    service.create_product(name="Uno", category_id=fer, prices={1: Decimal("1.00")})

    primero_ele = service.create_product(
        name="Cable", category_id=ele, prices={1: Decimal("1.00")}
    )

    assert primero_ele.sku == "ELE-0001"


def test_create_product_convierte_decimal_a_milli_no_a_centavos(conn, service):
    """
    cost_milli y price_milli escalan x1000 (to_milli), no x100 como los
    centavos de una factura. Decimal('1.50') -> 1500, no 150.
    """
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")

    producto = service.create_product(
        name="Abrazadera",
        category_id=cat_id,
        cost=Decimal("0.56"),
        prices={1: Decimal("1.50")},
    )

    assert producto.cost_milli == 560
    assert producto.prices[0].price_milli == 1500


def test_create_product_categoria_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="categoría"):
        service.create_product(name="X", category_id=999, prices={1: Decimal("1.00")})


def test_create_product_sin_precios_lanza_valueerror(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    with pytest.raises(ValueError, match="precio"):
        service.create_product(name="X", category_id=cat_id, prices={})


def test_create_product_nombre_vacio_lanza_valueerror(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    with pytest.raises(ValueError, match="nombre"):
        service.create_product(
            name="   ", category_id=cat_id, prices={1: Decimal("1.00")}
        )


def test_create_product_queda_guardado_con_stock_en_cero(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")

    producto = service.create_product(
        name="X", category_id=cat_id, prices={1: Decimal("1.00")}
    )

    guardado = ProductRepository(conn).get(producto.id)
    assert guardado.current_stock == 0


# ------------------------------------------------------------
# list_products
# ------------------------------------------------------------
def test_list_products_devuelve_los_activos(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(name="Uno", category_id=cat_id, prices={1: Decimal("1.00")})
    service.create_product(name="Dos", category_id=cat_id, prices={1: Decimal("1.00")})

    productos = service.list_products()

    assert {p.name for p in productos} == {"Uno", "Dos"}


def test_list_products_no_trae_inactivos(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    activo = service.create_product(
        name="Activo", category_id=cat_id, prices={1: Decimal("1.00")}
    )
    inactivo = service.create_product(
        name="Inactivo", category_id=cat_id, prices={1: Decimal("1.00")}
    )
    inactivo.is_active = False
    ProductRepository(conn).update(inactivo)

    productos = service.list_products()

    assert [p.id for p in productos] == [activo.id]


def test_list_products_sin_productos_devuelve_lista_vacia(service):
    assert service.list_products() == []


# ------------------------------------------------------------
# get_product
# ------------------------------------------------------------
def test_get_product_trae_precios(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    creado = service.create_product(
        name="X", category_id=cat_id, prices={1: Decimal("1.50")}
    )

    producto = service.get_product(creado.id)

    assert producto.name == "X"
    assert producto.prices[0].price_milli == to_milli(Decimal("1.50"))


def test_get_product_inexistente_devuelve_none(service):
    assert service.get_product(999) is None


# ------------------------------------------------------------
# list_price_tiers
# ------------------------------------------------------------
def test_list_price_tiers_trae_la_semilla(service):
    tiers = service.list_price_tiers()

    assert [t.name for t in tiers] == ["Mayor", "Especial", "Detal"]


# ------------------------------------------------------------
# default_category_id
# ------------------------------------------------------------
def test_default_category_id_devuelve_la_categoria_gen(conn, service):
    gen_id = CategoryRepository(conn).get_by_code("GEN").id

    assert service.default_category_id() == gen_id


# ------------------------------------------------------------
# update_product
# ------------------------------------------------------------
def test_update_product_modifica_nombre_costo_y_precios(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Viejo",
        category_id=cat_id,
        cost=Decimal("1.00"),
        prices={1: Decimal("2.00")},
    )

    actualizado = service.update_product(
        producto.id,
        name="Nuevo",
        cost=Decimal("1.50"),
        prices={1: Decimal("3.00"), 2: Decimal("3.50")},
    )

    assert actualizado.name == "Nuevo"
    assert actualizado.cost_milli == to_milli(Decimal("1.50"))
    guardado = ProductRepository(conn).get(producto.id)
    assert {p.tier_id for p in guardado.prices} == {1, 2}


def test_update_product_no_toca_categoria_ni_unidad(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X", category_id=cat_id, unit="KG", prices={1: Decimal("1.00")}
    )

    service.update_product(producto.id, name="X", prices={1: Decimal("2.00")})

    guardado = ProductRepository(conn).get(producto.id)
    assert guardado.category_id == cat_id
    assert guardado.unit == "KG"


def test_update_product_cambia_unidad_y_descripcion_si_se_pasan(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X",
        category_id=cat_id,
        unit="UNI",
        description="vieja",
        prices={1: Decimal("1.00")},
    )

    service.update_product(
        producto.id,
        name="X",
        prices={1: Decimal("1.00")},
        unit="KG",
        description="nueva",
    )

    guardado = ProductRepository(conn).get(producto.id)
    assert guardado.unit == "KG"
    assert guardado.description == "nueva"


def test_update_product_description_vacia_explicita_la_borra(conn, service):
    """'' es distinto de no pasar el argumento: None conserva, '' borra."""
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X", category_id=cat_id, description="algo", prices={1: Decimal("1.00")}
    )

    service.update_product(
        producto.id, name="X", prices={1: Decimal("1.00")}, description=""
    )

    guardado = ProductRepository(conn).get(producto.id)
    assert guardado.description == ""


def test_update_product_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.update_product(999, name="X", prices={1: Decimal("1.00")})


def test_update_product_nombre_vacio_lanza_valueerror(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X", category_id=cat_id, prices={1: Decimal("1.00")}
    )

    with pytest.raises(ValueError, match="nombre"):
        service.update_product(producto.id, name="  ", prices={1: Decimal("1.00")})


# ------------------------------------------------------------
# deactivate_product
# ------------------------------------------------------------
def test_deactivate_product_lo_saca_de_list_products(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X", category_id=cat_id, prices={1: Decimal("1.00")}
    )

    service.deactivate_product(producto.id)

    assert service.list_products() == []
    assert ProductRepository(conn).get(producto.id).is_active is False


def test_update_product_reactiva_con_is_active_true(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X", category_id=cat_id, prices={1: Decimal("1.00")}
    )
    service.deactivate_product(producto.id)

    service.update_product(
        producto.id, name="X", prices={1: Decimal("1.00")}, is_active=True
    )

    assert ProductRepository(conn).get(producto.id).is_active is True
    assert [p.id for p in service.list_products()] == [producto.id]


def test_deactivate_product_no_borra_la_fila(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="X", category_id=cat_id, prices={1: Decimal("1.00")}
    )

    service.deactivate_product(producto.id)

    assert ProductRepository(conn).get(producto.id) is not None


def test_deactivate_product_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.deactivate_product(999)


# ------------------------------------------------------------
# list_products_for_supplier
# ------------------------------------------------------------
def test_list_products_for_supplier_delega_al_repositorio(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Abrazadera", category_id=cat_id, prices={1: Decimal("1.00")}
    )
    sid = conn.execute(
        "INSERT INTO suppliers (sku, name) VALUES ('PROV-0001', 'LIMAR')"
    ).lastrowid
    conn.execute(
        "INSERT INTO product_suppliers (product_id, supplier_id) VALUES (?, ?)",
        (producto.id, sid),
    )

    productos = service.list_products_for_supplier(sid)

    assert [p.product_sku for p in productos] == ["FER-0001"]


# ------------------------------------------------------------
# link_supplier / unlink_supplier: registrar quien trajo un producto
# ------------------------------------------------------------
def _producto_y_proveedor(conn, service, *, proveedor="SURTEC"):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Apagador sencillo", category_id=cat_id, prices={1: Decimal("2.80")}
    )
    return producto.id, _crear_proveedor(conn, proveedor)


def _crear_proveedor(conn, nombre):
    return conn.execute(
        "INSERT INTO suppliers (sku, name) VALUES (?, ?)", (f"PROV-{nombre}", nombre)
    ).lastrowid


def test_link_supplier_registra_quien_trajo_el_producto(conn, service):
    pid, sid = _producto_y_proveedor(conn, service)

    service.link_supplier(
        pid,
        sid,
        contact_name="Mariana",
        last_purchase=date(2026, 9, 20),
        last_cost=Decimal("1.24"),
        note="viene por caja de 10",
    )

    (link,) = service.get_product(pid).suppliers
    assert link.supplier_id == sid
    assert link.contact_name == "Mariana"
    assert link.last_purchase == date(2026, 9, 20)
    assert link.last_cost_milli == to_milli(Decimal("1.24"))  # regla 1
    assert link.note == "viene por caja de 10"


def test_link_supplier_dos_veces_actualiza_en_vez_de_duplicar(conn, service):
    """Una entrega nueva del mismo proveedor pone al dia su ultima compra."""
    pid, sid = _producto_y_proveedor(conn, service)
    service.link_supplier(
        pid, sid, last_purchase=date(2026, 1, 5), last_cost=Decimal(1)
    )

    service.link_supplier(
        pid, sid, last_purchase=date(2026, 9, 20), last_cost=Decimal("1.24")
    )

    (link,) = service.get_product(pid).suppliers
    assert link.last_purchase == date(2026, 9, 20)
    assert link.last_cost_milli == to_milli(Decimal("1.24"))


def test_link_supplier_dos_veces_guarda_las_dos_entregas_en_el_historial(conn, service):
    """
    La Regla 4-bis que pidio el autor: "Registrar proveedor" dos veces
    para el mismo par no puede pisar la fecha/costo de la vez anterior.
    El vinculo ACTUAL (test de arriba) sigue mostrando solo la ultima,
    pero el historial completo tiene que conservar las dos.
    """
    pid, sid = _producto_y_proveedor(conn, service)
    service.link_supplier(
        pid, sid, last_purchase=date(2026, 1, 5), last_cost=Decimal(1), note="primera"
    )

    service.link_supplier(
        pid,
        sid,
        last_purchase=date(2026, 9, 20),
        last_cost=Decimal("1.24"),
        note="segunda",
    )

    entradas = service.list_supplier_entries(pid, sid)
    assert [e.note for e in entradas] == ["segunda", "primera"]  # mas reciente primero
    assert entradas[0].entry_date == date(2026, 9, 20)
    assert entradas[1].cost_milli == to_milli(Decimal(1))


def test_unlink_supplier_no_borra_el_historial(conn, service):
    """
    'Quitar' saca el vinculo de HOY, no borra que alguna vez se le
    compro a este proveedor (ver ProductRepository.unlink_supplier).
    """
    pid, sid = _producto_y_proveedor(conn, service)
    service.link_supplier(pid, sid, last_cost=Decimal(1))

    service.unlink_supplier(pid, sid)

    assert service.get_product(pid).suppliers == []
    assert len(service.list_supplier_entries(pid, sid)) == 1


def test_distinct_contacts_for_supplier_junta_los_de_todos_los_productos(conn, service):
    cat_id = _crear_categoria(conn, "FER", "Ferreteria")
    pid1 = service.create_product(
        name="Producto uno", category_id=cat_id, prices={1: Decimal("1.00")}
    ).id
    pid2 = service.create_product(
        name="Producto dos", category_id=cat_id, prices={1: Decimal("1.00")}
    ).id
    sid = _crear_proveedor(conn, "SURTEC")
    service.link_supplier(pid1, sid, contact_name="Ramon")
    service.link_supplier(pid2, sid, contact_name="Mariana")
    service.link_supplier(pid1, sid, contact_name="Ramon")  # repetido: no duplica

    assert service.distinct_contacts_for_supplier(sid) == ["Mariana", "Ramon"]


def test_distinct_contacts_for_supplier_sin_entregas_es_vacio(conn, service):
    _, sid = _producto_y_proveedor(conn, service)

    assert service.distinct_contacts_for_supplier(sid) == []


def test_list_all_supplier_entries_junta_los_de_todos_los_proveedores(conn, service):
    """
    Para la tabla 'Entregas' de la ficha del producto: todas las
    entregas de CUALQUIER proveedor de este producto, juntas, la mas
    reciente primero -a diferencia de list_supplier_entries, que es por
    UN proveedor puntual.
    """
    pid, limar = _producto_y_proveedor(conn, service, proveedor="LIMAR")
    mok = _crear_proveedor(conn, "MOK")
    service.link_supplier(pid, limar, last_cost=Decimal("1.00"), note="de limar")
    service.link_supplier(pid, mok, last_cost=Decimal("2.00"), note="de mok")

    entradas = service.list_all_supplier_entries(pid)

    assert [e.note for e in entradas] == ["de mok", "de limar"]
    assert {e.supplier_name for e in entradas} == {"LIMAR", "MOK"}


def test_list_all_supplier_entries_no_trae_las_de_otro_producto(conn, service):
    pid1, sid = _producto_y_proveedor(conn, service)
    pid2 = service.create_product(
        name="Otro producto",
        category_id=service.default_category_id(),
        prices={1: Decimal("1.00")},
    ).id
    service.link_supplier(pid1, sid)
    service.link_supplier(pid2, sid)

    assert len(service.list_all_supplier_entries(pid1)) == 1


def test_delete_supplier_entry_borra_solo_esa_entrega(conn, service):
    pid, sid = _producto_y_proveedor(conn, service)
    service.link_supplier(pid, sid, last_cost=Decimal("1.00"), note="primera")
    service.link_supplier(pid, sid, last_cost=Decimal("2.00"), note="segunda")
    (mas_reciente, mas_vieja) = service.list_all_supplier_entries(pid)

    service.delete_supplier_entry(mas_vieja.id)

    (quedo,) = service.list_all_supplier_entries(pid)
    assert quedo.id == mas_reciente.id
    # la mas vieja no era la que reflejaba el vinculo actual: no cambia
    (vinculo,) = service.get_product(pid).suppliers
    assert vinculo.note == "segunda"


def test_delete_supplier_entry_de_la_mas_reciente_recalcula_el_vinculo_actual(
    conn, service
):
    pid, sid = _producto_y_proveedor(conn, service)
    service.link_supplier(pid, sid, last_cost=Decimal("1.00"), note="primera")
    service.link_supplier(pid, sid, last_cost=Decimal("2.00"), note="segunda")
    (mas_reciente, _mas_vieja) = service.list_all_supplier_entries(pid)

    service.delete_supplier_entry(mas_reciente.id)

    (vinculo,) = service.get_product(pid).suppliers
    assert vinculo.note == "primera"
    assert vinculo.last_cost_milli == to_milli(Decimal("1.00"))


def test_delete_supplier_entry_de_la_unica_borra_tambien_el_vinculo_actual(
    conn, service
):
    pid, sid = _producto_y_proveedor(conn, service)
    service.link_supplier(pid, sid, last_cost=Decimal("1.00"))
    (unica,) = service.list_all_supplier_entries(pid)

    service.delete_supplier_entry(unica.id)

    assert service.get_product(pid).suppliers == []
    assert service.list_all_supplier_entries(pid) == []


def test_delete_supplier_entry_inexistente_lanza_valueerror(conn, service):
    with pytest.raises(ValueError, match="No existe"):
        service.delete_supplier_entry(999)


def test_un_producto_puede_tener_varios_proveedores(conn, service):
    """Regla 6: muchos-a-muchos. Vincular uno no toca al otro."""
    pid, uno = _producto_y_proveedor(conn, service)
    otro = _crear_proveedor(conn, "FERRE SUR")

    service.link_supplier(pid, uno)
    service.link_supplier(pid, otro)

    assert {s.supplier_id for s in service.get_product(pid).suppliers} == {uno, otro}


def test_link_supplier_campos_vacios_quedan_en_null(conn, service):
    pid, sid = _producto_y_proveedor(conn, service)

    service.link_supplier(pid, sid, contact_name="   ", note="")

    (link,) = service.get_product(pid).suppliers
    assert link.contact_name is None
    assert link.note is None
    assert link.last_cost_milli is None


def test_link_supplier_costo_negativo_lanza_valueerror(conn, service):
    pid, sid = _producto_y_proveedor(conn, service)

    with pytest.raises(ValueError, match="negativo"):
        service.link_supplier(pid, sid, last_cost=Decimal(-1))

    assert service.get_product(pid).suppliers == []


def test_link_supplier_proveedor_inexistente_da_un_error_en_espanol(conn, service):
    pid, _ = _producto_y_proveedor(conn, service)

    with pytest.raises(ValueError, match="proveedor"):
        service.link_supplier(pid, 9999)


def test_link_supplier_producto_inexistente_lanza_valueerror(conn, service):
    _, sid = _producto_y_proveedor(conn, service)

    with pytest.raises(ValueError, match="producto"):
        service.link_supplier(9999, sid)


def test_unlink_supplier_saca_solo_ese_vinculo(conn, service):
    pid, uno = _producto_y_proveedor(conn, service)
    otro = _crear_proveedor(conn, "FERRE SUR")
    service.link_supplier(pid, uno)
    service.link_supplier(pid, otro)

    service.unlink_supplier(pid, uno)

    assert [s.supplier_id for s in service.get_product(pid).suppliers] == [otro]
    # el proveedor sigue existiendo: solo se saco el vinculo
    fila = conn.execute("SELECT COUNT(*) AS n FROM suppliers WHERE id = ?", (uno,))
    assert fila.fetchone()["n"] == 1


def test_unlink_supplier_que_no_estaba_vinculado_avisa(conn, service):
    pid, sid = _producto_y_proveedor(conn, service)

    with pytest.raises(ValueError, match="no estaba vinculado"):
        service.unlink_supplier(pid, sid)


# ------------------------------------------------------------
# stock_overview: los conteos del tablero de Inicio
# ------------------------------------------------------------
def test_stock_overview_cuenta_sin_existencia_y_negativos(conn, service):
    ids = [
        service.create_product(
            name=f"Producto {n}",
            category_id=service.default_category_id(),
            prices={1: Decimal("1.00")},
        ).id
        for n in range(4)
    ]
    # Ajuste directo del cache solo para armar el escenario: en la app
    # esto lo hace StockService con su movimiento (regla 2).
    for product_id, stock in zip(ids, (5000, 0, -2000, 1000), strict=True):
        conn.execute(
            "UPDATE products SET current_stock = ? WHERE id = ?", (stock, product_id)
        )

    resumen = service.stock_overview()

    assert resumen.active == 4
    assert resumen.out_of_stock == 2  # el 0 y el negativo
    assert resumen.negative == 1


def test_stock_overview_sin_productos_da_ceros(service):
    resumen = service.stock_overview()

    assert (resumen.active, resumen.out_of_stock, resumen.negative) == (0, 0, 0)


def test_stock_overview_es_una_sola_consulta(conn, service):
    """El tablero se abre seguido: cuenta en SQL, no trae 1.949 productos."""
    consultas = []
    conn.set_trace_callback(consultas.append)

    service.stock_overview()

    conn.set_trace_callback(None)
    assert len(consultas) == 1


# ------------------------------------------------------------
# create_product(initial_stock=...): alta con existencia, todo o nada
# ------------------------------------------------------------
def _con_stock(conn):
    from flexstock.data.repositories.inventory_movement_repository import (
        InventoryMovementRepository,
    )
    from flexstock.services.stock_service import StockService

    stock = StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )
    servicio = ProductService(
        ProductRepository(conn),
        CategoryRepository(conn),
        PriceTierRepository(conn),
        stock_service=stock,
    )
    return servicio, stock


def _movimientos(conn, product_id):
    return conn.execute(
        "SELECT type, quantity_milli, counted_by, reason"
        " FROM inventory_movements WHERE product_id = ?",
        (product_id,),
    ).fetchall()


def test_alta_con_existencia_inicial_deja_producto_y_movimiento_initial(conn):
    servicio, _stock = _con_stock(conn)

    producto = servicio.create_product(
        name="Tornillo 1/4",
        category_id=servicio.default_category_id(),
        prices={1: Decimal("0.50")},
        initial_stock=Decimal("12.5"),
        counted_by="  Leo  ",
    )

    assert servicio.get_product(producto.id).current_stock == 12_500
    (movimiento,) = _movimientos(conn, producto.id)
    assert movimiento["type"] == "INITIAL"
    assert movimiento["quantity_milli"] == 12_500
    assert movimiento["counted_by"] == "Leo"
    assert movimiento["reason"]  # queda dicho de donde salio


def test_alta_con_existencia_cero_no_crea_movimiento(conn):
    servicio, _stock = _con_stock(conn)

    producto = servicio.create_product(
        name="Clavo",
        category_id=servicio.default_category_id(),
        prices={1: Decimal("0.10")},
        initial_stock=Decimal(0),
    )

    assert _movimientos(conn, producto.id) == []


def test_existencia_inicial_negativa_se_rechaza_y_no_crea_nada(conn):
    servicio, _stock = _con_stock(conn)

    with pytest.raises(ValueError, match="negativa"):
        servicio.create_product(
            name="Clavo",
            category_id=servicio.default_category_id(),
            prices={1: Decimal("0.10")},
            initial_stock=Decimal(-1),
        )

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0


def test_si_falla_el_movimiento_tampoco_queda_el_producto(conn, monkeypatch):
    """Todo o nada: nunca un producto creado con la existencia a medias."""
    servicio, stock = _con_stock(conn)

    def _falla(**_k):
        raise RuntimeError("se corto la luz")

    monkeypatch.setattr(stock, "apply_movement", _falla)

    with pytest.raises(RuntimeError):
        servicio.create_product(
            name="Clavo",
            category_id=servicio.default_category_id(),
            prices={1: Decimal("0.10")},
            initial_stock=Decimal(5),
        )

    assert conn.execute("SELECT COUNT(*) FROM products").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM product_prices").fetchone()[0] == 0


def test_existencia_inicial_sin_stock_service_avisa(service):
    with pytest.raises(RuntimeError, match="stock_service"):
        service.create_product(
            name="Clavo",
            category_id=service.default_category_id(),
            prices={1: Decimal("0.10")},
            initial_stock=Decimal(5),
        )


def test_el_cache_coincide_con_la_suma_de_movimientos(conn):
    """Regla 2: current_stock es la suma de inventory_movements."""
    servicio, _stock = _con_stock(conn)

    producto = servicio.create_product(
        name="Clavo",
        category_id=servicio.default_category_id(),
        prices={1: Decimal("0.10")},
        initial_stock=Decimal(7),
    )

    suma = conn.execute(
        "SELECT SUM(quantity_milli) FROM inventory_movements WHERE product_id = ?",
        (producto.id,),
    ).fetchone()[0]
    assert servicio.get_product(producto.id).current_stock == suma
