"""
Tests de ProductsPage y ProductDialog.

Hay display real en esta maquina (ver la conversacion de diseno), asi
que se prueban widgets de Tkinter de verdad -no solo logica extraida-
en una ventana oculta con withdraw(). messagebox se parchea: sus
dialogos son ventanas modales reales que bloquearian la corrida
esperando un click.

    pytest -q
"""

import sqlite3
import tkinter as tk
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.billing_service import BillingService
from flexstock.services.customer_service import CustomerService
from flexstock.services.product_service import ProductService
from flexstock.services.stock_service import StockService
from flexstock.services.supplier_service import SupplierService
from flexstock.ui import kit
from flexstock.ui import products_page as products_page_module
from flexstock.ui.products_page import (
    EntregasProductoDialog,
    ProductDialog,
    ProductsPage,
)
from flexstock.ui.stock_adjustment_page import AjustarExistenciaDialog


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    monkeypatch.setattr(
        products_page_module.messagebox, "showerror", lambda *a, **k: None
    )
    monkeypatch.setattr(
        products_page_module.messagebox, "showinfo", lambda *a, **k: None
    )
    monkeypatch.setattr(
        products_page_module.messagebox, "askyesno", lambda *a, **k: True
    )


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


@pytest.fixture
def customer_service(conn):
    return CustomerService(CustomerRepository(conn))


@pytest.fixture
def billing_service(conn):
    stock = StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )
    return BillingService(
        conn,
        InvoiceRepository(conn),
        ProductRepository(conn),
        CustomerRepository(conn),
        PriceTierRepository(conn),
        stock,
    )


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _botones_de(widget):
    encontrados = []

    def recorrer(w):
        for hijo in w.winfo_children():
            if isinstance(hijo, kit.Button):
                encontrados.append(hijo)
            recorrer(hijo)

    recorrer(widget)
    return encontrados


def _tiene_boton(page, texto):
    """En toda la tabla, no en una fila puntual: la busqueda y los
    botones viven en dos filas distintas (ver SearchableTable)."""

    def recorrer(widget):
        for hijo in widget.winfo_children():
            if isinstance(hijo, kit.Button) and str(hijo.cget("text")) == texto:
                return True
            if recorrer(hijo):
                return True
        return False

    return recorrer(page._table)


def _tiene_checkbox_activo(dialogo):
    for hijo in dialogo.winfo_children():
        for nieto in hijo.winfo_children():
            if isinstance(nieto, kit.Checkbutton) and nieto.cget("text") == "Activo":
                return True
    return False


def _crear_categoria(conn, code, name):
    cur = conn.execute(
        "INSERT INTO categories (code, name) VALUES (?, ?)", (code, name)
    )
    return cur.lastrowid


def _primer_tier_id(service):
    return service.list_price_tiers()[0].id


# ------------------------------------------------------------
# carga inicial
# ------------------------------------------------------------
def test_reload_carga_todos_los_productos(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    service.create_product(
        name="Martillo", category_id=cat, prices={1: Decimal("5.00")}
    )

    page = ProductsPage(root, service)

    assert len(page._tree.get_children()) == 2
    assert page._status.cget("text") == "Mostrando 2 de 2 productos"


def test_usa_el_simbolo_de_moneda_recibido(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    tier_especial = next(
        t for t in PriceTierRepository(conn).list_all() if t.position == 3
    )
    producto = service.create_product(
        name="Tornillo",
        category_id=cat,
        cost=Decimal("0.80"),
        prices={tier_especial.id: Decimal("1.50")},
    )

    page = ProductsPage(root, service, currency_symbol="Bs")

    precio3_mostrado = page._tree.item(str(producto.id), "values")[4]
    assert precio3_mostrado.endswith("Bs")


def test_pagina_vacia_no_falla(root, service):
    page = ProductsPage(root, service)

    assert page._tree.get_children() == ()
    assert page._status.cget("text") == "Mostrando 0 de 0 productos"


# ------------------------------------------------------------
# busqueda: filtra en memoria, no vuelve a consultar
# ------------------------------------------------------------
def test_buscar_por_nombre_filtra_la_tabla(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    service.create_product(
        name="Martillo", category_id=cat, prices={1: Decimal("5.00")}
    )
    page = ProductsPage(root, service)

    page._search_var.set("torni")

    assert len(page._tree.get_children()) == 1
    assert page._status.cget("text") == "Mostrando 1 de 2 productos"


def test_buscar_prioriza_el_inicio_y_separa_las_coincidencias_internas(
    root, service, conn
):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    interna = service.create_product(
        name="Bola con manguera", category_id=cat, prices={1: Decimal("0.25")}
    )
    empieza = service.create_product(
        name="Manguera de jardin", category_id=cat, prices={1: Decimal("5.00")}
    )
    page = ProductsPage(root, service)

    page._search_var.set("man")

    assert page._tree.get_children() == (
        str(empieza.id),
        "__coincidencias__",
        str(interna.id),
    )
    assert page._tree.item("__coincidencias__", "values")[0] == "COINCIDENCIAS"


def test_buscar_por_sku(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    service.create_product(
        name="Martillo", category_id=cat, prices={1: Decimal("5.00")}
    )
    page = ProductsPage(root, service)

    page._search_var.set(producto.sku)

    assert page._tree.get_children() == (str(producto.id),)


def test_buscar_sin_resultados_deja_tabla_vacia(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)

    page._search_var.set("NO EXISTE ESTO")

    assert page._tree.get_children() == ()


def test_borrar_busqueda_muestra_todo_de_nuevo(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    service.create_product(
        name="Martillo", category_id=cat, prices={1: Decimal("5.00")}
    )
    page = ProductsPage(root, service)
    page._search_var.set("torni")

    page._search_var.set("")

    assert len(page._tree.get_children()) == 2


def test_buscar_no_vuelve_a_pedirle_el_catalogo_al_service(
    root, service, conn, monkeypatch
):
    """Filtrar es local: no hay una segunda llamada a list_products()."""
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)

    llamadas = []
    original = service.list_products
    monkeypatch.setattr(
        service, "list_products", lambda: (llamadas.append(1), original())[1]
    )

    page._search_var.set("torni")

    assert llamadas == []


# ------------------------------------------------------------
# Alta
# ------------------------------------------------------------
def test_alta_crea_producto_en_categoria_default(root, service):
    page = ProductsPage(root, service)
    dialogo = ProductDialog(page, service, titulo="Nuevo producto")
    dialogo._name_var.set("Clavo")
    dialogo._cost_var.set("0,10")
    dialogo._price_vars[_primer_tier_id(service)].set("0,25")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    productos = service.list_products()
    assert len(productos) == 1
    assert productos[0].name == "Clavo"
    assert productos[0].category_id == service.default_category_id()


def test_dialogo_tiene_atajos_enter_y_escape(root, service):
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")

    assert dialogo.bind("<Return>") != ""
    assert dialogo.bind("<Escape>") != ""
    dialogo.destroy()


def test_alta_nombre_vacio_no_guarda_y_deja_el_dialogo_abierto(root, service):
    page = ProductsPage(root, service)
    dialogo = ProductDialog(page, service, titulo="Nuevo producto")
    dialogo._name_var.set("   ")
    dialogo._price_vars[_primer_tier_id(service)].set("1,00")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert dialogo.winfo_exists()
    assert service.list_products() == []
    dialogo.destroy()


def test_alta_precio_no_numerico_no_guarda(root, service):
    page = ProductsPage(root, service)
    dialogo = ProductDialog(page, service, titulo="Nuevo producto")
    dialogo._name_var.set("Clavo")
    dialogo._price_vars[_primer_tier_id(service)].set("no es un número")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert service.list_products() == []
    dialogo.destroy()


def test_alta_acepta_precio_con_punto_de_miles(root, service):
    """'1.210,00' es un precio real del catalogo (ver domain/money.py)."""
    page = ProductsPage(root, service)
    dialogo = ProductDialog(page, service, titulo="Nuevo producto")
    dialogo._name_var.set("Taladro")
    dialogo._price_vars[_primer_tier_id(service)].set("1.210,00")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    producto = service.get_product(service.list_products()[0].id)
    assert producto.prices[0].price == Decimal("1210.000")


def test_on_alta_desde_la_pagina_refresca_la_tabla(root, service, monkeypatch):
    page = ProductsPage(root, service)

    def _fake_dialog(master, svc, **kwargs):
        svc.create_product(
            name="Desde el boton",
            category_id=svc.default_category_id(),
            prices={_primer_tier_id(svc): Decimal("1.00")},
        )

        class _Falso:
            guardado = True

        return _Falso()

    monkeypatch.setattr(products_page_module, "ProductDialog", _fake_dialog)
    monkeypatch.setattr(page, "wait_window", lambda *_a: None)

    page._on_alta()

    assert len(page._tree.get_children()) == 1


# ------------------------------------------------------------
# Editar
# ------------------------------------------------------------
def test_editar_prellena_nombre_costo_y_precio(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo",
        category_id=cat,
        cost=Decimal("0.10"),
        prices={1: Decimal("0.25")},
    )
    page = ProductsPage(root, service)

    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )

    assert dialogo._name_var.get() == "Tornillo"
    assert dialogo._cost_var.get() == "0,10"
    assert dialogo._price_vars[1].get() == "0,25"
    dialogo.destroy()


def test_editar_prellena_unidad_y_descripcion(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Cemento",
        category_id=cat,
        unit="KG",
        description="Bolsa de 42,5 kg",
        prices={_primer_tier_id(service): Decimal("5.00")},
    )
    page = ProductsPage(root, service)

    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )

    assert dialogo._unit_var.get() == "KG"
    assert dialogo._description_text.get("1.0", "end-1c") == "Bolsa de 42,5 kg"
    dialogo.destroy()


def test_editar_muestra_los_proveedores_del_producto(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo",
        category_id=cat,
        prices={_primer_tier_id(service): Decimal("0.25")},
    )
    sid = conn.execute(
        "INSERT INTO suppliers (sku, name) VALUES ('PROV-0001', 'LIMAR')"
    ).lastrowid
    conn.execute(
        "INSERT INTO product_suppliers (product_id, supplier_id, last_purchase) "
        "VALUES (?, ?, '2024-01-15')",
        (producto.id, sid),
    )
    # init_schema() ya corrio (ver fixture 'conn'): el backfill de
    # entregas (ver connection.py) no alcanza a esta fila insertada
    # DESPUES. Se simula el estado "ya migrado" insertando la entrega
    # tambien, en vez de confiar en una segunda migracion aca.
    conn.execute(
        "INSERT INTO product_supplier_entries (product_id, supplier_id, entry_date) "
        "VALUES (?, ?, '2024-01-15')",
        (producto.id, sid),
    )
    page = ProductsPage(root, service)

    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )

    assert [s.supplier_name for s in dialogo._proveedores_producto] == ["LIMAR"]
    assert dialogo._tree_proveedores.item(
        dialogo._tree_proveedores.get_children()[0], "values"
    )[0:3] == ("LIMAR", "", "15/01/2024")
    dialogo.destroy()


def test_editar_producto_sin_proveedores_no_muestra_tabla(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo",
        category_id=cat,
        prices={_primer_tier_id(service): Decimal("0.25")},
    )
    page = ProductsPage(root, service)

    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )

    assert dialogo._proveedores_producto == []
    assert not hasattr(dialogo, "_tree_proveedores")
    dialogo.destroy()


def test_alta_no_muestra_seccion_de_proveedores(root, service):
    """Un producto nuevo todavia no tiene filas en product_suppliers:
    la seccion completa no tiene sentido, ni siquiera vacia."""
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")

    assert not hasattr(dialogo, "_tree_proveedores")
    dialogo.destroy()


# ------------------------------------------------------------
# Proveedores del producto: abrir su ficha y registrar quien lo trajo
# ------------------------------------------------------------
@pytest.fixture
def supplier_service(conn):
    return SupplierService(SupplierRepository(conn))


def _producto(conn, service, nombre="Apagador sencillo"):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    return service.create_product(
        name=nombre,
        category_id=cat,
        prices={_primer_tier_id(service): Decimal("2.80")},
        cost=Decimal("1.24"),
    ).id


def _ficha(root, service, supplier_service, pid):
    return ProductDialog(
        root,
        service,
        titulo="Editar producto",
        product_id=pid,
        supplier_service=supplier_service,
    )


def _falso_que_se_cierra(llamadas, *, al_crear=None):
    """
    Doble de un dialogo modal: anota con que lo abrieron y se cierra solo
    (after, no destroy directo: wait_window se quejaria de una ventana que
    ya no existe). Los dialogos reales colgarian la corrida esperando un
    click.
    """

    class Falso(tk.Toplevel):
        def __init__(self, master, *args, **kwargs):
            super().__init__(master)
            llamadas.append((args, kwargs))
            self.guardado = False
            self.resultado = None
            if al_crear is not None:
                al_crear(self)
            self.after(1, self.destroy)

    return Falso


def test_con_supplier_service_la_tabla_existe_aunque_este_vacia(
    root, service, supplier_service, conn
):
    """Si no, no habria donde registrar el PRIMER proveedor."""
    pid = _producto(conn, service)

    dialogo = _ficha(root, service, supplier_service, pid)

    assert dialogo._tree_proveedores.get_children() == ()
    dialogo.destroy()


def test_registrar_proveedor_lo_vincula_y_aparece_en_la_tabla(
    root, service, supplier_service, conn, monkeypatch
):
    from datetime import date

    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id

    def _completar(dialogo):
        dialogo.resultado = {
            "supplier_id": sid,
            "contacto": "Mariana",
            "fecha": date(2026, 9, 20),
            "costo": Decimal("1.24"),
            "nota": "",
        }

    monkeypatch.setattr(
        products_page_module,
        "RegistrarProveedorDialog",
        _falso_que_se_cierra([], al_crear=_completar),
    )
    dialogo = _ficha(root, service, supplier_service, pid)

    dialogo._on_registrar_proveedor()

    (link,) = service.get_product(pid).suppliers
    assert link.supplier_id == sid
    assert link.contact_name == "Mariana"
    (fila_id,) = dialogo._tree_proveedores.get_children()
    fila = dialogo._tree_proveedores.item(fila_id, "values")
    assert fila[0:3] == ("SURTEC", "Mariana", "20/09/2026")
    dialogo.destroy()


def test_cancelar_el_registro_no_vincula_nada(
    root, service, supplier_service, conn, monkeypatch
):
    pid = _producto(conn, service)
    supplier_service.create_supplier(name="SURTEC")
    monkeypatch.setattr(
        products_page_module, "RegistrarProveedorDialog", _falso_que_se_cierra([])
    )
    dialogo = _ficha(root, service, supplier_service, pid)

    dialogo._on_registrar_proveedor()  # resultado None: cancelado

    assert service.get_product(pid).suppliers == []
    dialogo.destroy()


def test_ver_proveedor_abre_su_ficha(
    root, service, supplier_service, conn, monkeypatch
):
    """Lo que pidio el autor: tocar el proveedor abre su menu."""
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid)
    llamadas = []
    monkeypatch.setattr(
        products_page_module, "SupplierDialog", _falso_que_se_cierra(llamadas)
    )
    dialogo = _ficha(root, service, supplier_service, pid)
    dialogo._tree_proveedores.selection_set(str(sid))

    dialogo._on_ver_proveedor()

    ((_args, kwargs),) = llamadas
    assert kwargs["supplier_id"] == sid
    # con product_service, la ficha del proveedor muestra que productos trae
    assert kwargs["product_service"] is service
    dialogo.destroy()


def test_la_tabla_de_proveedores_responde_al_doble_clic_y_al_enter(
    root, service, supplier_service, conn
):
    pid = _producto(conn, service)

    dialogo = _ficha(root, service, supplier_service, pid)

    assert dialogo._tree_proveedores.bind("<Double-1>") != ""
    assert dialogo._tree_proveedores.bind("<Return>") != ""
    dialogo.destroy()


def test_registrar_dos_veces_al_mismo_proveedor_se_ven_las_dos_entregas(
    root, service, supplier_service, conn
):
    """
    Lo que pidio el autor: antes la tabla solo mostraba la ULTIMA
    entrega (el vinculo actual), y eso daba la impresion de que
    registrar de nuevo pisaba la anterior. No es asi (queda en
    product_supplier_entries, ver Regla del CLAUDE.md): la tabla
    "Entregas" tiene que mostrar las dos, directo, sin un paso aparte.
    """
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="MOK").id
    service.link_supplier(
        pid, sid, contact_name="Ramon", last_cost=Decimal("1.10"), note="primera"
    )
    service.link_supplier(
        pid, sid, contact_name="Ramon", last_cost=Decimal("1.24"), note="segunda"
    )

    dialogo = _ficha(root, service, supplier_service, pid)

    filas = dialogo._tree_proveedores.get_children()
    assert len(filas) == 2
    notas = [dialogo._tree_proveedores.item(f, "values")[4] for f in filas]
    assert notas == ["segunda", "primera"]  # la mas reciente primero
    dialogo.destroy()


def test_la_tabla_de_entregas_muestra_la_x_para_eliminar_solo_si_es_editable(
    root, service, supplier_service, conn
):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid, last_cost=Decimal("1.10"))

    editable = _ficha(root, service, supplier_service, pid)
    (fila,) = editable._tree_proveedores.get_children()
    assert editable._tree_proveedores.item(fila, "values")[-1] == "❌"
    editable.destroy()

    solo_lectura = ProductDialog(
        root, service, titulo="Editar producto", product_id=pid
    )
    (fila,) = solo_lectura._tree_proveedores.get_children()
    assert len(solo_lectura._tree_proveedores.item(fila, "values")) == 5  # sin la X
    solo_lectura.destroy()


def test_sin_supplier_service_la_tabla_es_de_solo_lectura(root, service, conn):
    pid = _producto(conn, service)
    sid = conn.execute(
        "INSERT INTO suppliers (sku, name) VALUES ('PROV-1', 'SURTEC')"
    ).lastrowid
    service.link_supplier(pid, sid)

    dialogo = ProductDialog(root, service, titulo="Editar producto", product_id=pid)

    assert dialogo._tree_proveedores.bind("<Double-1>") == ""
    dialogo.destroy()


def test_eliminar_entrega_la_saca_de_la_tabla_y_de_la_base(
    root, service, supplier_service, conn
):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid)
    dialogo = _ficha(root, service, supplier_service, pid)
    (fila,) = dialogo._tree_proveedores.get_children()
    dialogo._tree_proveedores.selection_set(fila)

    dialogo._on_eliminar_entrega()  # askyesno parchado a True

    assert service.get_product(pid).suppliers == []
    assert dialogo._tree_proveedores.get_children() == ()
    dialogo.destroy()


def test_eliminar_entrega_de_una_de_dos_deja_la_otra_y_recalcula_el_vinculo(
    root, service, supplier_service, conn
):
    """La X borra UNA entrega puntual, no todo el vinculo: si quedan
    otras, el vinculo actual (Regla del CLAUDE.md) se recalcula solo."""
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid, last_cost=Decimal("1.10"), note="primera")
    service.link_supplier(pid, sid, last_cost=Decimal("1.24"), note="segunda")
    dialogo = _ficha(root, service, supplier_service, pid)
    mas_reciente, mas_vieja = dialogo._tree_proveedores.get_children()
    dialogo._tree_proveedores.selection_set(mas_reciente)

    dialogo._on_eliminar_entrega()

    (fila,) = dialogo._tree_proveedores.get_children()
    assert fila == mas_vieja
    (vinculo,) = service.get_product(pid).suppliers
    assert vinculo.note == "primera"


def test_eliminar_entrega_sin_elegir_avisa(root, service, supplier_service, conn):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid)
    dialogo = _ficha(root, service, supplier_service, pid)

    dialogo._on_eliminar_entrega()  # messagebox.showinfo parchado, no debe lanzar

    assert len(service.get_product(pid).suppliers) == 1
    dialogo.destroy()


def test_eliminar_entrega_sin_confirmar_no_borra_nada(
    root, service, supplier_service, conn, monkeypatch
):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid)
    monkeypatch.setattr(
        products_page_module.messagebox, "askyesno", lambda *a, **k: False
    )
    dialogo = _ficha(root, service, supplier_service, pid)
    (fila,) = dialogo._tree_proveedores.get_children()
    dialogo._tree_proveedores.selection_set(fila)

    dialogo._on_eliminar_entrega()

    assert len(service.get_product(pid).suppliers) == 1
    dialogo.destroy()


def test_click_en_la_x_elimina_en_vez_de_abrir_la_ficha(
    root, service, supplier_service, conn, monkeypatch
):
    """
    Mismo criterio que la x de las lineas de factura (ver
    test_billing_page.py::_simular_click_en_columna_quitar): se parchean
    identify_column/identify_row del propio Treeview para simular el
    clic sin depender de coordenadas de pixeles reales -fragil con la
    ventana sin mapear.
    """
    from types import SimpleNamespace

    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid)
    llamadas = []
    monkeypatch.setattr(
        products_page_module, "SupplierDialog", _falso_que_se_cierra(llamadas)
    )
    dialogo = _ficha(root, service, supplier_service, pid)
    tree = dialogo._tree_proveedores
    (fila,) = tree.get_children()
    tree.identify_column = lambda x: f"#{len(tree['columns'])}"  # "quitar" es la ultima
    tree.identify_row = lambda y: fila

    dialogo._on_click_entregas(SimpleNamespace(x=0, y=0))

    assert llamadas == []  # no abrio la ficha del proveedor
    assert service.get_product(pid).suppliers == []  # elimino la entrega
    dialogo.destroy()


def test_click_fuera_de_la_x_abre_la_ficha(
    root, service, supplier_service, conn, monkeypatch
):
    from types import SimpleNamespace

    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid)
    llamadas = []
    monkeypatch.setattr(
        products_page_module, "SupplierDialog", _falso_que_se_cierra(llamadas)
    )
    dialogo = _ficha(root, service, supplier_service, pid)
    tree = dialogo._tree_proveedores
    (fila,) = tree.get_children()
    tree.selection_set(fila)
    tree.identify_column = lambda x: "#1"  # "proveedor", no "quitar"

    dialogo._on_click_entregas(SimpleNamespace(x=0, y=0))

    assert len(llamadas) == 1  # abrio la ficha del proveedor
    assert len(service.get_product(pid).suppliers) == 1  # no se elimino nada
    dialogo.destroy()


# ------------------------------------------------------------
# RegistrarProveedorDialog
# ------------------------------------------------------------
def _registrar(root, supplier_service, **kwargs):
    from flexstock.ui.products_page import RegistrarProveedorDialog

    return RegistrarProveedorDialog(root, supplier_service, **kwargs)


def test_registrar_lista_solo_proveedores_activos_en_orden(root, supplier_service):
    supplier_service.create_supplier(name="ZETA")
    supplier_service.create_supplier(name="alfa")
    inactivo = supplier_service.create_supplier(name="VIEJO").id
    supplier_service.deactivate_supplier(inactivo)

    dialogo = _registrar(root, supplier_service)

    assert list(dialogo._lista.get(0, "end")) == ["alfa", "ZETA"]
    dialogo.destroy()


def test_registrar_el_buscador_filtra_y_un_unico_resultado_queda_elegido(
    root, supplier_service
):
    supplier_service.create_supplier(name="SURTEC")
    supplier_service.create_supplier(name="FERRE SUR")
    dialogo = _registrar(root, supplier_service)

    dialogo._buscar_var.set("surt")

    assert list(dialogo._lista.get(0, "end")) == ["SURTEC"]
    assert dialogo._lista.curselection() == (0,)
    dialogo.destroy()


def test_registrar_enter_con_varias_coincidencias_elige_la_primera_sin_registrar(
    root, supplier_service
):
    """
    "MOK" y "MOK DISTRIBUIDORA" matchean el mismo filtro: antes, sin un
    unico resultado, Enter no elegia nada y quedaba identico a apretarlo
    con la lista vacia (el autor lo describio como "no actuaba"). Ahora
    toma la primera coincidencia (la de "empieza por", ver
    split_search_results) y la deja fija. Registrar es una accion aparte.
    """
    from types import SimpleNamespace

    sid = supplier_service.create_supplier(name="MOK").id
    supplier_service.create_supplier(name="MOK DISTRIBUIDORA")
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("MOK")
    assert dialogo._lista.curselection() == ()  # dos coincidencias: ninguna elegida

    dialogo._picker._on_entry_return(SimpleNamespace())

    assert dialogo._elegido().id == sid
    assert dialogo.resultado is None
    assert not dialogo._picker._abierta  # la lista se cerro
    dialogo.destroy()


def test_registrar_enter_con_buscador_vacio_no_elige_nada(root, supplier_service):
    """
    Sin nada escrito, _filtrados son TODOS los proveedores activos: un
    Enter ahi no puede significar "el primero alfabetico", asi que no
    tiene que elegir ni registrar nada.
    """
    from types import SimpleNamespace

    supplier_service.create_supplier(name="ALFA SUMINISTROS")
    supplier_service.create_supplier(name="BETA DISTRIBUIDORA")
    dialogo = _registrar(root, supplier_service)

    dialogo._picker._on_entry_return(SimpleNamespace())

    assert dialogo._lista.curselection() == ()
    assert dialogo.resultado is None
    dialogo.destroy()


def test_registrar_click_en_un_proveedor_lo_fija_sin_cerrar_el_dialogo(
    root, supplier_service
):
    supplier_service.create_supplier(name="ALFA SUMINISTROS")
    sid = supplier_service.create_supplier(name="BETA DISTRIBUIDORA").id
    dialogo = _registrar(root, supplier_service)

    dialogo._picker._abrir_lista()  # la lista flotante, a la vista como al clickear
    root.update()
    dialogo._lista.selection_set(1)
    dialogo._lista.event_generate("<<ListboxSelect>>")
    root.update_idletasks()

    assert dialogo._elegido().id == sid
    assert dialogo._buscar_var.get() == "BETA DISTRIBUIDORA"
    assert dialogo.resultado is None
    assert dialogo.winfo_exists()
    assert not dialogo._picker._abierta  # la lista se cerro
    dialogo.destroy()


def test_registrar_sin_proveedor_elegido_el_formulario_esta_apagado(
    root, supplier_service
):
    """Lo que pidio el autor: el formulario de abajo no tiene sentido
    hasta elegir a quien corresponde."""
    supplier_service.create_supplier(name="SURTEC")
    supplier_service.create_supplier(name="FERRE SUR")

    dialogo = _registrar(root, supplier_service)

    assert all(str(c.cget("state")) == "disabled" for c in dialogo._campos)
    assert str(dialogo._btn_registrar.cget("state")) == "disabled"
    assert "Elige un proveedor" in dialogo._estado_var.get()
    dialogo.destroy()


def test_registrar_al_elegir_se_llena_con_la_ficha_del_proveedor(
    root, supplier_service
):
    supplier_service.create_supplier(
        name="SURTEC", contact_name="Mariana", phone="0412-1112233"
    )
    dialogo = _registrar(root, supplier_service)

    dialogo._buscar_var.set("SURTEC")  # unico resultado: queda elegido

    assert all(str(c.cget("state")) == "normal" for c in dialogo._campos)
    assert dialogo._contacto_var.get() == "Mariana"
    assert dialogo._telefono_var.get() == "0412-1112233"
    dialogo.destroy()


def test_registrar_primera_vez_propone_hoy_y_el_costo_del_producto(
    root, supplier_service
):
    from datetime import date

    supplier_service.create_supplier(name="SURTEC")
    dialogo = _registrar(root, supplier_service, costo_sugerido="1,24")

    dialogo._buscar_var.set("SURTEC")

    assert dialogo._fecha_var.get() == date.today().strftime("%d/%m/%Y")  # noqa: DTZ011
    assert dialogo._costo_var.get() == "1,24"
    assert "Primera vez" in dialogo._estado_var.get()
    dialogo.destroy()


def _vinculo_viejo(sid, *, costo="1.10"):
    from datetime import date

    from flexstock.domain.entities import SupplierLink
    from flexstock.domain.money import to_milli

    return SupplierLink(
        supplier_id=sid,
        supplier_name="SURTEC",
        last_purchase=date(2026, 3, 1),
        last_cost_milli=to_milli(Decimal(costo)),
        note="caja de 10",
    )


def test_registrar_si_ya_lo_trae_arranca_con_hoy_y_el_costo_de_la_ficha(
    root, supplier_service
):
    """
    Pedido del autor (23/10/2026): lo normal es registrar la entrada el dia
    que llega, al costo recien puesto en la ficha. La entrega anterior
    (01/03, 1,10) se ve en el historial, no se precarga.
    """
    from datetime import date

    sid = supplier_service.create_supplier(name="SURTEC").id
    dialogo = _registrar(
        root, supplier_service, costo_sugerido="1,24", vinculos=[_vinculo_viejo(sid)]
    )

    dialogo._buscar_var.set("SURTEC")

    assert dialogo._fecha_var.get() == date.today().strftime("%d/%m/%Y")  # noqa: DTZ011
    assert dialogo._costo_var.get() == "1,24"
    assert dialogo._nota_var.get() == ""  # la nota era de ESA entrega
    assert "ya trae este producto" in dialogo._estado_var.get()
    dialogo.destroy()


def test_registrar_sin_costo_en_la_ficha_propone_el_de_la_ultima_entrega(
    root, supplier_service
):
    sid = supplier_service.create_supplier(name="SURTEC").id
    dialogo = _registrar(
        root, supplier_service, costo_sugerido="", vinculos=[_vinculo_viejo(sid)]
    )

    dialogo._buscar_var.set("SURTEC")

    assert dialogo._costo_var.get() == "1,10"
    dialogo.destroy()


def test_el_boton_de_la_ficha_se_llama_registrar_entrada(root, conn, service):
    from flexstock.services.supplier_service import SupplierService

    producto = service.create_product(
        name="Clavo",
        category_id=service.default_category_id(),
        prices={_primer_tier_id(service): Decimal("0.25")},
    )
    dialogo = ProductDialog(
        root,
        service,
        titulo="Editar producto",
        product_id=producto.id,
        supplier_service=SupplierService(SupplierRepository(conn)),
    )

    textos = [b.cget("text") for b in _botones_de(dialogo)]

    assert "Registrar entrada..." in textos
    assert "Registrar proveedor..." not in textos
    dialogo.destroy()


def test_registrar_seguir_escribiendo_no_pisa_lo_corregido(root, supplier_service):
    """Si el mismo proveedor sigue elegido mientras se afina la busqueda,
    lo que el usuario ya edito no se vuelve a llenar."""
    supplier_service.create_supplier(name="SURTEC", contact_name="Ana")
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("SURT")
    dialogo._contacto_var.set("Mariana")

    dialogo._buscar_var.set("SURTEC")  # otra tecla, mismo unico resultado

    assert dialogo._contacto_var.get() == "Mariana"
    dialogo.destroy()


def test_registrar_devuelve_los_datos_convertidos(root, supplier_service):
    from datetime import date

    sid = supplier_service.create_supplier(name="SURTEC", contact_name="Mariana").id
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("SURTEC")
    dialogo._fecha_var.set("20/09/2026")
    dialogo._costo_var.set("1.210,50")  # formato local, con punto de miles

    dialogo._on_registrar()

    assert dialogo.resultado == {
        "supplier_id": sid,
        "contacto": "Mariana",
        "fecha": date(2026, 9, 20),
        "costo": Decimal("1210.50"),
        "nota": "",
        "ficha": None,  # no se cambio: no se toca la ficha general
    }


def test_registrar_cambiar_la_ficha_pide_confirmacion(
    root, supplier_service, monkeypatch
):
    avisos = []
    monkeypatch.setattr(
        products_page_module.messagebox,
        "askyesno",
        lambda _t, mensaje, **_k: avisos.append(mensaje) or True,
    )
    supplier_service.create_supplier(name="SURTEC", contact_name="Ana", phone="1")
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("SURTEC")
    dialogo._telefono_var.set("0412-1112233")

    dialogo._on_registrar()

    (aviso,) = avisos
    assert "GENERAL" in aviso and "0412-1112233" in aviso
    assert dialogo.resultado["ficha"] == {
        "contact_name": "Ana",
        "phone": "0412-1112233",
    }


def test_registrar_si_no_se_confirma_el_cambio_no_registra(
    root, supplier_service, monkeypatch
):
    monkeypatch.setattr(
        products_page_module.messagebox, "askyesno", lambda *a, **k: False
    )
    supplier_service.create_supplier(name="SURTEC", contact_name="Ana")
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("SURTEC")
    dialogo._contacto_var.set("Otra persona")

    dialogo._on_registrar()

    assert dialogo.resultado is None
    assert dialogo.winfo_exists()  # sigue abierto para corregir
    dialogo.destroy()


def test_registrar_doble_clic_en_un_proveedor_lo_deja_elegido(root, supplier_service):
    """
    Lo mismo que Enter. El doble clic real no se puede sintetizar (depende
    del reloj) y con la ventana oculta la lista no tiene geometria, asi que
    nearest() -"que renglon hay a esta altura"- se fija en el segundo.
    Lo que se prueba es lo que importa: elige EL RENGLON CLICKEADO (no el
    que estaba elegido antes), lo deja fijo y no registra aun.
    """
    from types import SimpleNamespace

    supplier_service.create_supplier(name="ALFA SUMINISTROS")
    sid = supplier_service.create_supplier(name="BETA DISTRIBUIDORA").id
    dialogo = _registrar(root, supplier_service)
    dialogo._lista.selection_set(0)  # ALFA SUMINISTROS estaba elegido
    dialogo._lista.nearest = lambda _y: 1  # el clic cayo en BETA DISTRIBUIDORA

    dialogo._picker._on_doble_clic(SimpleNamespace(y=40))

    assert dialogo._elegido().id == sid
    assert dialogo.resultado is None
    assert not dialogo._picker._abierta  # la lista se cerro
    dialogo.destroy()


def test_la_lista_esta_atada_al_doble_clic(root, supplier_service):
    dialogo = _registrar(root, supplier_service)

    assert dialogo._lista.bind("<Double-Button-1>") != ""
    dialogo.destroy()


def test_registrar_arranca_con_la_fecha_de_hoy_aunque_no_haya_proveedor(
    root, supplier_service
):
    from datetime import date

    dialogo = _registrar(root, supplier_service)

    assert dialogo._fecha_var.get() == date.today().strftime("%d/%m/%Y")  # noqa: DTZ011
    dialogo.destroy()


def test_registrar_fecha_y_costo_vacios_quedan_en_none(root, supplier_service):
    supplier_service.create_supplier(name="SURTEC")
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("SURTEC")
    dialogo._fecha_var.set("")
    dialogo._costo_var.set("")

    dialogo._on_registrar()

    assert dialogo.resultado["fecha"] is None
    assert dialogo.resultado["costo"] is None


@pytest.mark.parametrize(
    ("campo", "valor"),
    [("_fecha_var", "31/02/2026"), ("_fecha_var", "ayer"), ("_costo_var", "caro")],
)
def test_registrar_con_datos_invalidos_no_devuelve_nada(
    root, supplier_service, campo, valor
):
    supplier_service.create_supplier(name="SURTEC")
    dialogo = _registrar(root, supplier_service)
    dialogo._buscar_var.set("SURTEC")
    getattr(dialogo, campo).set(valor)

    dialogo._on_registrar()

    assert dialogo.resultado is None
    dialogo.destroy()


def test_registrar_sin_elegir_proveedor_no_devuelve_nada(root, supplier_service):
    supplier_service.create_supplier(name="SURTEC")
    supplier_service.create_supplier(name="FERRE SUR")
    dialogo = _registrar(root, supplier_service)  # dos en la lista, ninguno elegido

    dialogo._on_registrar()

    assert dialogo.resultado is None
    dialogo.destroy()


def test_nuevo_proveedor_desde_el_registro_queda_elegido(
    root, supplier_service, monkeypatch
):
    """Si el que trajo el producto todavia no existe, se da de alta ahi
    mismo y queda elegido al volver."""

    def _alta(dialogo):
        dialogo.guardado = True
        dialogo.supplier_id = supplier_service.create_supplier(name="NUEVO C.A.").id

    monkeypatch.setattr(
        products_page_module, "SupplierDialog", _falso_que_se_cierra([], al_crear=_alta)
    )
    supplier_service.create_supplier(name="SURTEC")
    dialogo = _registrar(root, supplier_service)

    dialogo._on_nuevo_proveedor()

    assert dialogo._elegido().name == "NUEVO C.A."
    dialogo.destroy()


def test_supplier_dialog_expone_el_id_del_recien_creado(root, supplier_service):
    """El puente que usa "Nuevo proveedor...": sin esto no habria forma
    de saber a quien elegir despues del alta."""
    from flexstock.ui.suppliers_page import SupplierDialog

    dialogo = SupplierDialog(root, supplier_service, titulo="Nuevo proveedor")
    assert dialogo.supplier_id is None
    dialogo._name_var.set("RECIEN CREADO")

    dialogo._on_guardar()

    (creado,) = supplier_service.list_suppliers()
    assert dialogo.supplier_id == creado.id


def test_editar_guarda_unidad_y_descripcion(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Cemento",
        category_id=cat,
        prices={_primer_tier_id(service): Decimal("5.00")},
    )
    page = ProductsPage(root, service)
    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )

    dialogo._unit_var.set("KG")
    dialogo._description_text.insert("1.0", "Bolsa de 42,5 kg")
    dialogo._on_guardar()

    assert dialogo.guardado is True
    actualizado = service.get_product(producto.id)
    assert actualizado.unit == "KG"
    assert actualizado.description == "Bolsa de 42,5 kg"


def test_alta_guarda_unidad_y_descripcion(root, service):
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")
    dialogo._name_var.set("Cemento")
    dialogo._unit_var.set("KG")
    dialogo._description_text.insert("1.0", "Bolsa de 42,5 kg")
    dialogo._price_vars[_primer_tier_id(service)].set("5,00")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    producto = service.get_product(service.list_products()[0].id)
    assert producto.unit == "KG"
    assert producto.description == "Bolsa de 42,5 kg"


def test_enter_en_descripcion_no_dispara_guardar(root, service):
    """
    El <Return> del Toplevel guarda (ver test_dialogo_tiene_atajos_enter_
    y_escape); dentro de la caja de Descripcion tiene que insertar un
    salto de linea en cambio. Tk no deja sintetizar un evento de teclado
    real de forma confiable (ver test_widgets.py), asi que esto llama al
    handler directo -el binding en si ya se prueba por separado abajo.
    """
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")
    dialogo._description_text.insert("1.0", "linea 1")

    resultado = dialogo._on_return_en_descripcion(None)

    assert resultado == "break"
    assert dialogo._description_text.get("1.0", "end-1c") == "linea 1\n"
    assert dialogo.guardado is False
    dialogo.destroy()


def test_descripcion_tiene_el_binding_de_return(root, service):
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")

    assert dialogo._description_text.bind("<Return>") != ""
    dialogo.destroy()


def test_alta_no_muestra_checkbox_activo(root, service):
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")

    assert not _tiene_checkbox_activo(dialogo)
    dialogo.destroy()


def test_reactivar_producto_desde_el_dialogo(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    service.deactivate_product(producto.id)
    page = ProductsPage(root, service)

    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )
    assert dialogo._active_var.get() is False

    dialogo._active_var.set(True)
    dialogo._on_guardar()

    assert service.get_product(producto.id).is_active is True


def test_editar_guarda_cambios(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)
    dialogo = ProductDialog(
        page, service, titulo="Editar producto", product_id=producto.id
    )
    dialogo._name_var.set("Tornillo Grande")
    dialogo._price_vars[1].set("0,50")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    actualizado = service.get_product(producto.id)
    assert actualizado.name == "Tornillo Grande"
    assert actualizado.prices[0].price == Decimal("0.500")


def test_doble_click_esta_conectado_a_editar(root, service):
    page = ProductsPage(root, service)

    assert page._tree.bind("<Double-1>") != ""


def test_editar_sin_seleccion_no_abre_dialogo(root, service, conn, monkeypatch):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)

    abierto = []
    monkeypatch.setattr(
        products_page_module, "ProductDialog", lambda *a, **k: abierto.append(1)
    )

    page._on_editar()

    assert abierto == []


# ------------------------------------------------------------
# Eliminar (desactivar)
# ------------------------------------------------------------
def test_eliminar_desactiva_y_no_borra_la_fila(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)
    page._tree.selection_set(str(producto.id))

    page._on_eliminar()

    assert service.list_products() == []
    assert service.get_product(producto.id) is not None
    assert service.get_product(producto.id).is_active is False


def test_eliminar_refresca_la_tabla(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    producto = service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)
    page._tree.selection_set(str(producto.id))

    page._on_eliminar()

    assert page._tree.get_children() == ()


def test_eliminar_sin_seleccion_no_falla(root, service, conn):
    cat = _crear_categoria(conn, "FER", "Ferreteria")
    service.create_product(
        name="Tornillo", category_id=cat, prices={1: Decimal("0.25")}
    )
    page = ProductsPage(root, service)

    page._on_eliminar()  # nada seleccionado: no debe lanzar

    assert len(service.list_products()) == 1


# ------------------------------------------------------------
# Crear factura: atajo desde Productos hacia el mismo InvoiceDialog
# que usa BillingPage (ver ProductsPage._on_crear_factura)
# ------------------------------------------------------------
def test_sin_billing_ni_customer_service_no_aparece_el_boton(root, service):
    page = ProductsPage(root, service)

    assert not _tiene_boton(page, "Crear factura")


def test_con_billing_y_customer_service_aparece_el_boton(
    root, service, billing_service, customer_service
):
    page = ProductsPage(
        root,
        service,
        billing_service=billing_service,
        customer_service=customer_service,
    )

    assert _tiene_boton(page, "Crear factura")


def test_crear_factura_abre_invoice_dialog_y_refresca_al_guardar(
    root, service, billing_service, customer_service, monkeypatch
):
    page = ProductsPage(
        root,
        service,
        billing_service=billing_service,
        customer_service=customer_service,
    )
    recibidos = []

    def _fake_dialog(master, billing_svc, product_svc, customer_svc, **kwargs):
        recibidos.append((billing_svc, product_svc, customer_svc))

        class _Falso:
            guardado = True

        return _Falso()

    monkeypatch.setattr(products_page_module, "InvoiceDialog", _fake_dialog)
    monkeypatch.setattr(page, "wait_window", lambda *_a: None)
    monkeypatch.setattr(page, "reload", lambda: recibidos.append("reload"))

    page._on_crear_factura()

    assert recibidos[0] == (billing_service, service, customer_service)
    assert recibidos[-1] == "reload"


def test_crear_factura_le_pasa_las_tasas_al_dialogo(
    root, conn, service, billing_service, customer_service, monkeypatch
):
    rates = _rates(conn)
    page = ProductsPage(
        root,
        service,
        billing_service=billing_service,
        customer_service=customer_service,
        rate_display_service=rates,
    )
    recibidos = {}

    def _fake_dialog(*_a, **kwargs):
        recibidos.update(kwargs)

        class _Falso:
            guardado = False

        return _Falso()

    monkeypatch.setattr(products_page_module, "InvoiceDialog", _fake_dialog)
    monkeypatch.setattr(page, "wait_window", lambda *_a: None)

    page._on_crear_factura()

    # sin tasas, la factura abierta desde Productos no mostraba nada en Bs
    assert recibidos["rate_display_service"] is rates


def test_crear_factura_no_refresca_si_no_se_guardo(
    root, service, billing_service, customer_service, monkeypatch
):
    page = ProductsPage(
        root,
        service,
        billing_service=billing_service,
        customer_service=customer_service,
    )

    def _fake_dialog(*_a, **_k):
        class _Falso:
            guardado = False

        return _Falso()

    monkeypatch.setattr(products_page_module, "InvoiceDialog", _fake_dialog)
    monkeypatch.setattr(page, "wait_window", lambda *_a: None)
    llamado = []
    monkeypatch.setattr(page, "reload", lambda: llamado.append(1))

    page._on_crear_factura()

    assert llamado == []


# ------------------------------------------------------------
# columnas configurables (Configuracion > Personalizacion)
# ------------------------------------------------------------
def _rates(conn):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    return RateDisplayService(conn, ExchangeRateService(conn))


def _settings(conn):
    from flexstock.services.settings_service import SettingsService

    return SettingsService(conn)


def _producto_con_precio3(service, conn, precio="2.50", cost=Decimal("1.00")):
    from flexstock.data.repositories.price_tier_repository import PriceTierRepository

    cat = _crear_categoria(conn, "FER", "Ferreteria")
    tier3 = next(t.id for t in PriceTierRepository(conn).list_all() if t.position == 3)
    return service.create_product(
        name="Tornillo",
        category_id=cat,
        prices={tier3: Decimal(precio)},
        cost=cost,
    )


def test_columnas_por_defecto_son_las_de_siempre(root, service, conn):
    from flexstock.ui import columns

    page = ProductsPage(root, service, settings_service=_settings(conn))

    assert tuple(page._tree.cget("columns")) == columns.default_keys(columns.PRODUCTS)


def test_personalizar_columnas_cambia_la_tabla(root, service, conn):
    from flexstock.data.connection import set_setting
    from flexstock.ui import columns

    producto = _producto_con_precio3(service, conn)
    settings = _settings(conn)
    settings.set_advanced_mode(True)  # price3_bs usa la tasa propia
    settings.set_visible_columns(columns.PRODUCTS, ("name", "cost", "price3_bs"))
    set_setting(conn, "bcv_rate", "185.40")
    rates = _rates(conn)
    rates.set_personal_rate(Decimal(260))

    page = ProductsPage(
        root, service, settings_service=settings, rate_display_service=rates
    )

    assert tuple(page._tree.cget("columns")) == ("name", "cost", "price3_bs")
    assert page._tree.item(str(producto.id), "values") == (
        "Tornillo",
        "1,00$",
        "650,00 Bs",
    )


def test_precio_a_bcv_usa_la_cuenta_de_cotizaciones(root, service, conn):
    from flexstock.data.connection import set_setting
    from flexstock.ui import columns

    producto = _producto_con_precio3(service, conn)
    settings = _settings(conn)
    settings.set_advanced_mode(True)
    settings.set_visible_columns(columns.PRODUCTS, ("price3", "price3_bcv"))
    set_setting(conn, "bcv_rate", "185.40")
    rates = _rates(conn)
    rates.set_personal_rate(Decimal(260))

    page = ProductsPage(
        root, service, settings_service=settings, rate_display_service=rates
    )

    # 2,50 x 260 / 185,40 = 3,505... -> 3,51
    assert page._tree.item(str(producto.id), "values") == ("2,50$", "3,51$")


def test_sin_tasas_las_columnas_convertidas_muestran_guion(root, service, conn):
    from flexstock.ui import columns

    producto = _producto_con_precio3(service, conn)
    settings = _settings(conn)
    settings.set_advanced_mode(True)
    settings.set_visible_columns(columns.PRODUCTS, ("price3_bs", "price3_bcv"))

    page = ProductsPage(
        root, service, settings_service=settings, rate_display_service=_rates(conn)
    )

    assert page._tree.item(str(producto.id), "values") == ("-", "-")


def test_apply_column_settings_rearma_la_tabla_sin_reiniciar(root, service, conn):
    from flexstock.ui import columns

    producto = _producto_con_precio3(service, conn)
    settings = _settings(conn)
    page = ProductsPage(root, service, settings_service=settings)

    settings.set_visible_columns(columns.PRODUCTS, ("sku", "min_stock"))
    page.apply_column_settings()

    assert tuple(page._tree.cget("columns")) == ("sku", "min_stock")
    assert page._tree.item(str(producto.id), "values") == (producto.sku, "0")


def test_convertir_precios_lee_las_tasas_una_vez_por_poblada(root, service, conn):
    """Sin la foto de tasas (RateSnapshot), cada celda en Bs releia
    settings: N filas -> ~3N consultas por tecla del buscador."""
    from flexstock.data.connection import set_setting
    from flexstock.ui import columns

    cat = _crear_categoria(conn, "FER", "Ferreteria")
    tier3 = next(t.id for t in service.list_price_tiers() if t.position == 3)
    for i in range(30):
        service.create_product(
            name=f"Producto {i}", category_id=cat, prices={tier3: Decimal("1.00")}
        )
    settings = _settings(conn)
    settings.set_advanced_mode(True)
    settings.set_visible_columns(columns.PRODUCTS, ("name", "price3_bs", "price3_bcv"))
    set_setting(conn, "bcv_rate", "185.40")
    rates = _rates(conn)
    rates.set_personal_rate(Decimal(260))
    page = ProductsPage(
        root, service, settings_service=settings, rate_display_service=rates
    )

    lecturas = []
    conn.set_trace_callback(
        lambda sql: lecturas.append(sql) if "FROM settings" in sql else None
    )
    page._aplicar_filtro()
    conn.set_trace_callback(None)

    assert len(page._tree.get_children()) == 30
    assert len(lecturas) == 3  # una por tasa (propia, BCV, USDT), no por fila


def test_precio_detal_bs_a_bcv_multiplica_por_la_bcv(root, service, conn):
    """Sin Modo avanzado: es la tasa oficial, no la propia."""
    from flexstock.data.connection import set_setting
    from flexstock.ui import columns

    producto = _producto_con_precio3(service, conn)  # 2,50$
    settings = _settings(conn)
    settings.set_visible_columns(columns.PRODUCTS, ("price3", "price3_bs_bcv"))
    set_setting(conn, "bcv_rate", "848.55")
    rates = _rates(conn)
    rates.set_personal_rate(Decimal(1200))  # no debe influir

    page = ProductsPage(
        root, service, settings_service=settings, rate_display_service=rates
    )

    # 2,50 x 848,55 = 2.121,375 -> 2.121,38 Bs
    assert page._tree.item(str(producto.id), "values") == ("2,50$", "2.121,38 Bs")


def test_titulo_detal_en_dolares(root, service, conn):
    page = ProductsPage(root, service, settings_service=_settings(conn))

    titulos = [page._tree.heading(c, "text") for c in page._tree.cget("columns")]

    assert "Detal $" in titulos


def test_sin_modo_avanzado_no_aparecen_las_columnas_de_tasa_propia(root, service, conn):
    from flexstock.ui import columns

    settings = _settings(conn)
    settings.set_visible_columns(
        columns.PRODUCTS, ("name", "price3_bs_bcv", "price3_bs", "price3_bcv")
    )

    page = ProductsPage(root, service, settings_service=settings)

    assert tuple(page._tree.cget("columns")) == ("name", "price3_bs_bcv")


def test_prender_modo_avanzado_devuelve_las_columnas_de_tasa_propia(
    root, service, conn
):
    from flexstock.ui import columns

    settings = _settings(conn)
    settings.set_visible_columns(columns.PRODUCTS, ("name", "price3_bs"))
    page = ProductsPage(root, service, settings_service=settings)
    assert tuple(page._tree.cget("columns")) == ("name",)

    settings.set_advanced_mode(True)
    page.apply_column_settings()

    assert tuple(page._tree.cget("columns")) == ("name", "price3_bs")


# ------------------------------------------------------------
# De punta a punta: lo confirmado en "Registrar" llega a la base
# ------------------------------------------------------------
def test_confirmar_el_cambio_de_ficha_actualiza_al_proveedor_general(
    root, service, supplier_service, conn, monkeypatch
):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(
        name="SURTEC", contact_name="Ana", tax_id="J-123"
    ).id

    def _completar(dialogo):
        dialogo.resultado = {
            "supplier_id": sid,
            "contacto": "Mariana",
            "fecha": None,
            "costo": None,
            "nota": "",
            "ficha": {"contact_name": "Mariana", "phone": "0412-1112233"},
        }

    monkeypatch.setattr(
        products_page_module,
        "RegistrarProveedorDialog",
        _falso_que_se_cierra([], al_crear=_completar),
    )
    dialogo = _ficha(root, service, supplier_service, pid)

    dialogo._on_registrar_proveedor()

    general = supplier_service.get_supplier(sid)
    assert general.contact_name == "Mariana"
    assert general.phone == "0412-1112233"
    assert general.tax_id == "J-123"  # lo demas de la ficha, intacto
    (link,) = service.get_product(pid).suppliers
    assert link.contact_name == "Mariana"
    dialogo.destroy()


def test_sin_cambios_en_la_ficha_el_proveedor_general_no_se_toca(
    root, service, supplier_service, conn, monkeypatch
):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC", phone="0212-000").id

    def _completar(dialogo):
        dialogo.resultado = {
            "supplier_id": sid,
            "contacto": "",
            "fecha": None,
            "costo": None,
            "nota": "",
            "ficha": None,
        }

    monkeypatch.setattr(
        products_page_module,
        "RegistrarProveedorDialog",
        _falso_que_se_cierra([], al_crear=_completar),
    )
    dialogo = _ficha(root, service, supplier_service, pid)

    dialogo._on_registrar_proveedor()

    assert supplier_service.get_supplier(sid).phone == "0212-000"
    dialogo.destroy()


def test_registrar_le_pasa_al_dialogo_lo_que_el_producto_ya_tiene(
    root, service, supplier_service, conn, monkeypatch
):
    """Para que pueda precargar la ultima entrega de ese proveedor."""
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="SURTEC").id
    service.link_supplier(pid, sid, last_cost=Decimal("1.10"))
    llamadas = []
    monkeypatch.setattr(
        products_page_module, "RegistrarProveedorDialog", _falso_que_se_cierra(llamadas)
    )
    dialogo = _ficha(root, service, supplier_service, pid)

    dialogo._on_registrar_proveedor()

    ((_args, kwargs),) = llamadas
    assert [v.supplier_id for v in kwargs["vinculos"]] == [sid]
    dialogo.destroy()


# ------------------------------------------------------------
# Nuevo producto: elegir quien lo trajo ya en el alta
# ------------------------------------------------------------
def _alta(root, service, supplier_service):
    return ProductDialog(
        root, service, titulo="Nuevo producto", supplier_service=supplier_service
    )


def _llenar_alta(dialogo, service, nombre="Apagador sencillo"):
    dialogo._name_var.set(nombre)
    dialogo._cost_var.set("1,24")
    dialogo._price_vars[_primer_tier_id(service)].set("2,80")


def test_alta_con_supplier_service_muestra_el_selector(root, service, supplier_service):
    supplier_service.create_supplier(name="SURTEC")

    dialogo = _alta(root, service, supplier_service)

    assert list(dialogo._picker_alta.lista.get(0, "end")) == ["SURTEC"]
    dialogo.destroy()


def test_alta_sin_supplier_service_no_muestra_el_selector(root, service):
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")

    assert not hasattr(dialogo, "_picker_alta")
    dialogo.destroy()


def test_alta_con_proveedor_elegido_lo_vincula_al_guardar(
    root, service, supplier_service, conn
):
    from datetime import date

    from flexstock.domain.money import to_milli

    sid = supplier_service.create_supplier(name="SURTEC").id
    supplier_service.create_supplier(name="FERRE SUR")
    dialogo = _alta(root, service, supplier_service)
    _llenar_alta(dialogo, service)
    dialogo._picker_alta.buscar_var.set("SURT")  # un solo resultado: elegido
    assert dialogo._proveedor_alta_var.get() == "Se registra: SURTEC"

    dialogo._on_guardar()

    (producto,) = service.list_products()
    (link,) = service.get_product(producto.id).suppliers
    assert link.supplier_id == sid
    assert link.last_purchase == date.today()  # noqa: DTZ011 - hora local
    assert link.last_cost_milli == to_milli(Decimal("1.24"))  # el costo cargado


def test_alta_con_proveedor_permite_asignar_su_contacto(
    root, service, supplier_service, monkeypatch
):
    sid = supplier_service.create_supplier(name="SURTEC").id
    dialogo = _alta(root, service, supplier_service)
    _llenar_alta(dialogo, service)
    dialogo._picker_alta.buscar_var.set("SURT")
    dialogo._contacto_alta_var.set("Mariana")
    monkeypatch.setattr(
        products_page_module.messagebox, "askyesno", lambda *_a, **_k: True
    )

    dialogo._on_guardar()

    assert supplier_service.get_supplier(sid).contact_name == "Mariana"
    producto = service.list_products()[0]
    assert service.get_product(producto.id).suppliers[0].contact_name == "Mariana"


def test_alta_sin_proveedor_elegido_crea_el_producto_sin_vinculo(
    root, service, supplier_service, conn
):
    supplier_service.create_supplier(name="SURTEC")
    supplier_service.create_supplier(name="FERRE SUR")
    dialogo = _alta(root, service, supplier_service)
    _llenar_alta(dialogo, service)
    assert dialogo._proveedor_alta_var.get() == "Sin proveedor"

    dialogo._on_guardar()

    (producto,) = service.list_products()
    assert service.get_product(producto.id).suppliers == []


def test_alta_enter_en_el_buscador_de_proveedor_no_guarda_el_producto(
    root, service, supplier_service
):
    """
    El selector de "Nuevo producto" no tiene on_activate (registrar un
    proveedor ahi no tiene sentido: todavia no existe el producto). Antes
    del fix, Enter en ese buscador seguia de largo hasta el <Return> del
    dialogo entero y GUARDABA EL PRODUCTO con el primer proveedor que
    matcheara el filtro, sin que nadie lo haya elegido a proposito.
    """
    from types import SimpleNamespace

    supplier_service.create_supplier(name="MOK")
    supplier_service.create_supplier(name="MOK DISTRIBUIDORA")
    dialogo = _alta(root, service, supplier_service)
    dialogo._picker_alta.buscar_var.set("MOK")  # dos coincidencias: ninguna elegida

    dialogo._picker_alta._on_entry_return(SimpleNamespace())

    assert dialogo.guardado is False
    assert dialogo._picker_alta.elegido() is not None  # si eligio, pero no guardo
    dialogo.destroy()


def test_alta_ninguno_deshace_la_eleccion(root, service, supplier_service):
    supplier_service.create_supplier(name="SURTEC")
    dialogo = _alta(root, service, supplier_service)  # unico: queda elegido
    assert dialogo._picker_alta.elegido() is not None

    dialogo._picker_alta.limpiar()

    assert dialogo._picker_alta.elegido() is None
    assert dialogo._proveedor_alta_var.get() == "Sin proveedor"
    dialogo.destroy()


# ------------------------------------------------------------
# Existencia: inicial en el alta (Configuracion > Inventario) y solo
# lectura + "Ajustar..." al editar
# ------------------------------------------------------------
def _con_stock(conn):
    stock = StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )
    return ProductService(
        ProductRepository(conn),
        CategoryRepository(conn),
        PriceTierRepository(conn),
        stock_service=stock,
    )


def _settings(conn, *, permitir=True):
    from flexstock.services.settings_service import SettingsService

    settings = SettingsService(conn)
    settings.set_allow_initial_stock(permitir)
    return settings


def test_alta_ofrece_existencia_inicial_si_esta_permitida(root, conn):
    servicio = _con_stock(conn)
    dialogo = ProductDialog(
        root, servicio, titulo="Nuevo producto", settings_service=_settings(conn)
    )

    assert hasattr(dialogo, "_existencia_entry")
    dialogo.destroy()


def test_alta_no_ofrece_existencia_si_esta_apagada(root, conn):
    servicio = _con_stock(conn)
    dialogo = ProductDialog(
        root,
        servicio,
        titulo="Nuevo producto",
        settings_service=_settings(conn, permitir=False),
    )

    assert not hasattr(dialogo, "_existencia_entry")
    dialogo.destroy()


def test_alta_con_existencia_la_guarda_como_carga_inicial(root, conn):
    servicio = _con_stock(conn)
    dialogo = ProductDialog(
        root, servicio, titulo="Nuevo producto", settings_service=_settings(conn)
    )
    dialogo._name_var.set("Clavo")
    dialogo._price_vars[_primer_tier_id(servicio)].set("0,25")
    dialogo._existencia_var.set("24")
    dialogo._contado_por_var.set("Leo")

    dialogo._on_guardar()

    (producto,) = servicio.list_products()
    assert producto.current_stock == 24_000
    tipo, quien = conn.execute(
        "SELECT type, counted_by FROM inventory_movements WHERE product_id = ?",
        (producto.id,),
    ).fetchone()
    assert (tipo, quien) == ("INITIAL", "Leo")


def test_alta_con_existencia_que_no_es_numero_no_guarda(root, conn, monkeypatch):
    errores = []
    monkeypatch.setattr(
        products_page_module.messagebox,
        "showerror",
        lambda _t, m, **k: errores.append(m),
    )
    servicio = _con_stock(conn)
    dialogo = ProductDialog(
        root, servicio, titulo="Nuevo producto", settings_service=_settings(conn)
    )
    dialogo._name_var.set("Clavo")
    dialogo._price_vars[_primer_tier_id(servicio)].set("0,25")
    dialogo._existencia_var.set("muchos")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert servicio.list_products() == []
    assert "número" in errores[0]
    dialogo.destroy()


def _producto_guardado(servicio):
    return servicio.create_product(
        name="Clavo",
        category_id=servicio.default_category_id(),
        prices={_primer_tier_id(servicio): Decimal("0.25")},
        initial_stock=Decimal(3),
    )


def test_editar_muestra_la_existencia_sin_campo_para_cambiarla(root, conn):
    servicio = _con_stock(conn)
    producto = _producto_guardado(servicio)

    dialogo = ProductDialog(
        root,
        servicio,
        titulo="Editar producto",
        product_id=producto.id,
        settings_service=_settings(conn),
    )

    assert dialogo._existencia_actual == "3 UNI"
    assert not hasattr(dialogo, "_existencia_entry")
    assert not hasattr(dialogo, "_btn_ajustar")  # sin a donde ir, sin boton
    dialogo.destroy()


def _stock_service(conn):
    return StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )


def _ficha_con_stock(root, conn, **extra):
    servicio = _con_stock(conn)
    producto = _producto_guardado(servicio)
    dialogo = ProductDialog(
        root,
        servicio,
        titulo="Editar producto",
        product_id=producto.id,
        stock_service=_stock_service(conn),
        **extra,
    )
    return servicio, producto, dialogo


def _abrir_ajuste(dialogo, monkeypatch, *, cuenta, motivo=None, por=None):
    """Aprieta "Ajustar...", teclea el conteo en la ventana y registra."""
    abiertas = []
    original = products_page_module.AjustarExistenciaDialog

    def abrir(*args, **kwargs):
        ventana = original(*args, **kwargs)
        abiertas.append(ventana)
        ventana._contado_var.set(cuenta)
        if motivo is not None:
            ventana._motivo_var.set(motivo)
        if por is not None:
            ventana._contado_por_var.set(por)
        ventana.after(0, ventana._on_registrar)
        return ventana

    monkeypatch.setattr(products_page_module, "AjustarExistenciaDialog", abrir)
    dialogo._btn_ajustar.invoke()
    return abiertas[0]


def test_ajustar_abre_la_ventana_sin_cerrar_la_ficha_y_actualiza_el_numero(
    root, conn, monkeypatch
):
    servicio, producto, dialogo = _ficha_con_stock(root, conn)

    _abrir_ajuste(dialogo, monkeypatch, cuenta="10", motivo="Conteo físico")

    assert dialogo.winfo_exists()  # la ficha sigue ahi
    assert dialogo._existencia_texto_var.get() == "10 UNI"
    assert dialogo.stock_cambiado is True
    assert servicio.get_product(producto.id).current_stock == 10_000
    dialogo.destroy()


def test_ajustar_recuerda_motivo_y_quien_conto_para_la_siguiente(
    root, conn, monkeypatch
):
    recuerdo: dict[str, str] = {}
    _s, _p, dialogo = _ficha_con_stock(root, conn, recuerdo_ajuste=recuerdo)

    _abrir_ajuste(dialogo, monkeypatch, cuenta="8", motivo="Merma", por="Ana")

    assert recuerdo == {"motivo": "Merma", "contado_por": "Ana"}
    dialogo.destroy()


def test_ajustar_cancelando_no_toca_nada(root, conn, monkeypatch):
    servicio, producto, dialogo = _ficha_con_stock(root, conn)
    original = products_page_module.AjustarExistenciaDialog

    def abrir(*args, **kwargs):
        ventana = original(*args, **kwargs)
        ventana.after(0, ventana.destroy)
        return ventana

    monkeypatch.setattr(products_page_module, "AjustarExistenciaDialog", abrir)
    dialogo._btn_ajustar.invoke()

    assert dialogo.stock_cambiado is False
    assert dialogo._existencia_texto_var.get() == "3 UNI"
    assert servicio.get_product(producto.id).current_stock == 3_000
    dialogo.destroy()


def test_sin_servicio_de_stock_no_hay_boton_ajustar(root, conn):
    servicio = _con_stock(conn)
    producto = _producto_guardado(servicio)
    dialogo = ProductDialog(
        root, servicio, titulo="Editar producto", product_id=producto.id
    )

    assert not hasattr(dialogo, "_btn_ajustar")
    dialogo.destroy()


def _ventana_de_ajuste(root, conn, existencia_milli=3_000):
    servicio = _con_stock(conn)
    producto = _producto_guardado(servicio)
    return producto, AjustarExistenciaDialog(
        root,
        _stock_service(conn),
        product_id=producto.id,
        sku=producto.sku,
        nombre=producto.name,
        unidad="UNI",
        existencia_milli=existencia_milli,
    )


def test_ajuste_muestra_en_vivo_cuanto_queda_y_que_va_a_pasar(root, conn):
    _p, ventana = _ventana_de_ajuste(root, conn)

    ventana._contado_var.set("10")
    assert ventana._queda_var.get() == "10 UNI"
    assert ventana._mensaje_var.get() == "Va a sumar +7"

    ventana._contado_var.set("1")
    assert ventana._mensaje_var.get() == "Va a restar 2"

    ventana._contado_var.set("abc")
    assert ventana._queda_var.get() == "-"
    assert "No es un número" in ventana._mensaje_var.get()
    ventana.destroy()


def test_ajuste_sin_cantidad_o_igual_no_registra_y_avisa(root, conn):
    _producto, ventana = _ventana_de_ajuste(root, conn)

    ventana._on_registrar()
    assert "Escribe la cantidad" in ventana._mensaje_var.get()

    ventana._contado_var.set("3")  # igual a lo que hay
    ventana._on_registrar()
    assert ventana.winfo_exists() and ventana.resultado is None
    ventana.destroy()


def test_ajuste_restar_calcula_el_total_y_deja_un_ajuste_negativo(root, conn):
    producto, ventana = _ventana_de_ajuste(root, conn)  # hay 3
    ventana._modo_var.set("Restar")
    ventana._on_modo("Restar")
    ventana._contado_var.set("2")

    assert ventana._queda_var.get() == "1 UNI"
    assert ventana._mensaje_var.get() == "Va a restar 2"
    assert ventana._motivo_var.get() == "Merma"  # sugerido, no el de conteo

    ventana._on_registrar()

    assert ventana.resultado == 1_000
    movimiento = _stock_service(conn).history_for(producto.id)[0]
    assert movimiento.quantity_milli == -2_000
    assert movimiento.reason == "Merma"


def test_ajuste_sumar_calcula_el_total(root, conn):
    _p, ventana = _ventana_de_ajuste(root, conn)
    ventana._modo_var.set("Sumar")
    ventana._on_modo("Sumar")
    ventana._contado_var.set("4")

    assert ventana._queda_var.get() == "7 UNI"
    assert ventana._mensaje_var.get() == "Va a sumar +4"
    assert ventana._motivo_var.get() == "Entrada de mercancia"
    ventana.destroy()


def test_ajuste_restar_de_mas_avisa_que_queda_en_negativo(root, conn):
    _p, ventana = _ventana_de_ajuste(root, conn)
    ventana._modo_var.set("Restar")
    ventana._on_modo("Restar")
    ventana._contado_var.set("5")

    assert ventana._queda_var.get() == "-2 UNI"
    assert "negativo" in ventana._mensaje_var.get()
    ventana.destroy()


def test_ajuste_cambiar_de_modo_no_pisa_un_motivo_escrito_a_mano(root, conn):
    _p, ventana = _ventana_de_ajuste(root, conn)
    ventana._motivo_var.set("Se cayo del estante")

    ventana._modo_var.set("Restar")
    ventana._on_modo("Restar")

    assert ventana._motivo_var.get() == "Se cayo del estante"
    ventana.destroy()


def test_ajuste_sumar_y_restar_no_aceptan_negativos(root, conn):
    _p, ventana = _ventana_de_ajuste(root, conn)
    ventana._modo_var.set("Sumar")
    ventana._contado_var.set("-2")

    ventana._on_registrar()

    assert "negativa" in ventana._mensaje_var.get()
    assert ventana.resultado is None
    ventana.destroy()


def test_ajuste_avisa_si_la_existencia_esta_en_negativo(root, conn):
    _p, ventana = _ventana_de_ajuste(root, conn, existencia_milli=-1_000)

    avisos = [w for w in _todos(ventana) if isinstance(w, kit.Notice)]

    assert len(avisos) == 1
    ventana.destroy()


def _todos(widget):
    for hijo in widget.winfo_children():
        yield hijo
        yield from _todos(hijo)


def _con_entregas(root, service, supplier_service, conn, cantidad):
    pid = _producto(conn, service)
    sid = supplier_service.create_supplier(name="MOK").id
    for n in range(cantidad):
        service.link_supplier(pid, sid, note=f"entrega {n + 1}")
    return pid, _ficha(root, service, supplier_service, pid)


def test_entregas_sin_ninguna_muestra_estado_vacio_y_sin_ayuda(
    root, service, supplier_service, conn
):
    _pid, dialogo = _con_entregas(root, service, supplier_service, conn, 0)

    assert dialogo._entregas_vacio.winfo_manager() == "grid"
    assert dialogo._tabla_entregas.winfo_manager() == ""
    assert dialogo._info_entregas.winfo_manager() == "pack"  # la ayuda siempre esta
    assert dialogo._pie_entregas.winfo_manager() == ""
    assert dialogo._boton_ver_proveedor.state() == ("disabled",)
    dialogo.destroy()


def test_entregas_con_pocas_muestra_la_tabla_y_la_ayuda_sin_ver_todas(
    root, service, supplier_service, conn
):
    _pid, dialogo = _con_entregas(root, service, supplier_service, conn, 3)

    assert dialogo._entregas_vacio.winfo_manager() == ""
    assert dialogo._info_entregas.winfo_manager() == "pack"
    assert len(dialogo._tree_proveedores.get_children()) == 3
    assert dialogo._pie_entregas.winfo_manager() == ""
    assert dialogo._boton_ver_proveedor.state() == ()
    dialogo.destroy()


def test_entregas_con_mas_de_tres_muestra_las_ultimas_y_ver_todas(
    root, service, supplier_service, conn
):
    _pid, dialogo = _con_entregas(root, service, supplier_service, conn, 5)

    filas = dialogo._tree_proveedores.get_children()
    notas = [dialogo._tree_proveedores.item(f, "values")[4] for f in filas]
    assert notas == ["entrega 5", "entrega 4", "entrega 3"]
    assert dialogo._pie_entregas.winfo_manager() == "grid"
    assert "(5)" in str(dialogo._boton_ver_todas.cget("text"))
    dialogo.destroy()


def test_ver_todas_muestra_el_historial_completo_y_borrar_desde_ahi_refresca(
    root, service, supplier_service, conn
):
    _pid, dialogo = _con_entregas(root, service, supplier_service, conn, 5)
    todas = EntregasProductoDialog(dialogo)
    dialogo._dialogo_entregas = todas
    assert len(todas._tree.get_children()) == 5

    masreciente = todas._tree.get_children()[0]
    todas._tree.selection_set(masreciente)
    dialogo._on_eliminar_entrega(todas._tree)  # askyesno parchado a True

    assert len(todas._tree.get_children()) == 4
    assert len(dialogo._tree_proveedores.get_children()) == 3
    assert "(4)" in str(dialogo._boton_ver_todas.cget("text"))
    todas.destroy()
    dialogo.destroy()


def test_ver_todas_se_cierra_sola_si_ya_no_quedan_entregas(
    root, service, supplier_service, conn
):
    _pid, dialogo = _con_entregas(root, service, supplier_service, conn, 1)
    todas = EntregasProductoDialog(dialogo)
    dialogo._dialogo_entregas = todas
    todas._tree.selection_set(todas._tree.get_children()[0])

    dialogo._on_eliminar_entrega(todas._tree)

    assert not todas.winfo_exists()
    dialogo.destroy()


def test_el_precio_detal_resalta_entre_los_tres_niveles(root, service, conn):
    pid = _producto(conn, service)
    dialogo = ProductDialog(root, service, titulo="Editar producto", product_id=pid)

    def estilo_de(texto):
        (etiqueta,) = [
            w
            for w in _todos(dialogo)
            if isinstance(w, kit.Label) and w.cget("text") == texto
        ]
        return etiqueta.cget("style")

    assert estilo_de("Precio Detal:") == "Bold.TLabel"
    assert estilo_de("Precio Mayor:") != "Bold.TLabel"
    assert estilo_de("Precio Especial:") != "Bold.TLabel"
    dialogo.destroy()


def test_el_alta_arranca_con_el_cursor_en_nombre(root, service, esperar):
    dialogo = ProductDialog(root, service, titulo="Nuevo producto")

    esperar(
        root,
        lambda: dialogo.focus_get() is not None and dialogo.focus_get() is not dialogo,
        que="el cursor llegue a un campo",
    )

    campo = dialogo.focus_get()
    assert str(campo).startswith(str(dialogo._name_entry))
    dialogo.destroy()


def test_alta_pliega_existencia_y_proveedor_y_se_abren_con_el_boton(
    root, conn, supplier_service
):
    servicio = _con_stock(conn)
    dialogo = ProductDialog(
        root,
        servicio,
        titulo="Nuevo producto",
        supplier_service=supplier_service,
        settings_service=_settings(conn),
    )
    assert dialogo._opcionales.winfo_manager() == ""  # plegado de entrada

    dialogo._btn_opcionales.invoke()
    assert dialogo._opcionales.winfo_manager() == "grid"
    assert dialogo._opcionales_abiertos is True

    dialogo._btn_opcionales.invoke()
    assert dialogo._opcionales.winfo_manager() == ""
    dialogo.destroy()


def test_editar_no_tiene_seccion_de_opcionales(root, conn):
    servicio = _con_stock(conn)
    producto = _producto_guardado(servicio)
    dialogo = ProductDialog(
        root, servicio, titulo="Editar producto", product_id=producto.id
    )

    assert not hasattr(dialogo, "_btn_opcionales")
    dialogo.destroy()


def test_existencia_negativa_pinta_la_fila_de_rojo_y_gana_sobre_stock_bajo(
    root, service, conn
):
    from flexstock.domain.entities import Product

    page = ProductsPage(root, service)
    negativo = Product(
        id=1, sku="A", name="A", unit="UNI", category_id=1, current_stock=-1000
    )
    bajo = Product(
        id=2, sku="B", name="B", unit="UNI", category_id=1,
        current_stock=1000, min_stock=5000,
    )  # fmt: skip
    normal = Product(
        id=3, sku="C", name="C", unit="UNI", category_id=1, current_stock=9000
    )

    assert page._tags_de_stock(negativo) == ("stock_negativo",)
    assert page._tags_de_stock(bajo) == ("bajo_stock",)
    assert page._tags_de_stock(normal) == ()


def test_selector_de_proveedor_abre_la_lista_flotante_al_escribir_y_la_cierra_al_elegir(
    root, supplier_service
):
    supplier_service.create_supplier(name="SURTEC")
    supplier_service.create_supplier(name="SURTIDORA")
    dialogo = _registrar(root, supplier_service)
    picker = dialogo._picker
    assert picker._abierta is False

    dialogo._buscar_var.set("SURT")
    picker._on_entry_tecla(type("E", (), {"keysym": "t"})())
    assert picker._abierta is True

    picker.lista.selection_set(0)
    picker._fijar_elegido()
    assert picker._abierta is False
    assert dialogo._elegido() is not None
    dialogo.destroy()


def test_selector_de_proveedor_esc_cierra_la_lista_sin_cerrar_la_ventana(
    root, supplier_service
):
    supplier_service.create_supplier(name="SURTEC")
    dialogo = _registrar(root, supplier_service)
    picker = dialogo._picker
    picker._abrir_lista()

    assert picker._on_entry_escape() == "break"
    assert picker._abierta is False
    assert picker._on_entry_escape() is None  # sin lista, Esc sigue a la ventana
    dialogo.destroy()


def test_alta_contacto_ofrece_los_contactos_ya_vistos_del_proveedor(
    root, conn, service, supplier_service
):
    sid = supplier_service.create_supplier(name="SURTEC", contact_name="Ana").id
    pid = _producto(conn, service)
    service.link_supplier(pid, sid, contact_name="Beto")
    dialogo = ProductDialog(
        root,
        service,
        titulo="Nuevo producto",
        supplier_service=supplier_service,
    )
    dialogo._picker_alta.buscar_var.set("SURTEC")  # unico resultado: elegido

    valores = list(dialogo._contacto_alta_entry.cget("values"))

    assert valores == ["Ana", "Beto"]
    assert dialogo._contacto_alta_var.get() == "Ana"
    dialogo.destroy()


def test_nuevo_proveedor_desde_el_selector_pasa_lo_escrito_como_nombre(
    root, supplier_service, monkeypatch
):
    recibido = {}

    class _Falso:
        guardado = False
        supplier_id = None

        def __init__(self, master, service, **kwargs):
            recibido.update(kwargs)
            self._ventana = master

    monkeypatch.setattr(products_page_module, "SupplierDialog", _Falso)
    dialogo = _registrar(root, supplier_service)
    monkeypatch.setattr(dialogo._picker, "wait_window", lambda *_a: None)
    dialogo._buscar_var.set("MOKITO")

    dialogo._picker.on_nuevo_proveedor()

    assert recibido["nombre_inicial"] == "MOKITO"
    dialogo.destroy()


def test_tab_en_la_descripcion_pasa_al_campo_siguiente_y_shift_tab_al_anterior(
    root, conn, esperar
):
    dialogo = ProductDialog(root, _con_stock(conn), titulo="Nuevo producto")
    texto = dialogo._description_text
    esperar(root, lambda: dialogo.winfo_viewable(), que="se muestre la ficha")
    texto.focus_force()
    esperar(
        root,
        lambda: dialogo.focus_get() is texto,
        que="el foco llegue a la descripcion",
    )

    texto.event_generate("<Tab>")
    esperar(root, lambda: dialogo.focus_get() is not texto, que="Tab salga de la caja")
    assert "\t" not in texto.get("1.0", "end")  # no inserto una sangria
    siguiente = dialogo.focus_get()

    siguiente.event_generate("<Shift-Tab>")
    esperar(root, lambda: dialogo.focus_get() is texto, que="Shift+Tab vuelva")
    dialogo.destroy()


def test_tab_desde_el_ultimo_precio_despliega_lo_opcional_y_entra_en_el_primer_campo(
    root, conn, supplier_service
):
    dialogo = ProductDialog(
        root,
        _con_stock(conn),
        titulo="Nuevo producto",
        supplier_service=supplier_service,
        settings_service=_settings(conn),
    )
    assert dialogo._opcionales_abiertos is False

    resultado = dialogo._on_tab_ultimo_precio(None)

    assert resultado == "break"  # el Tab no sigue por su camino normal
    assert dialogo._opcionales_abiertos is True
    # Ya desplegado, el siguiente Tab sigue normal.
    assert dialogo._on_tab_ultimo_precio(None) is None
    dialogo.destroy()
