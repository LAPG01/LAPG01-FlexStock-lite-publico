"""
Tests de CustomersPage y CustomerDialog.

Mismo enfoque que test_products_page.py: widgets de Tkinter reales en una
ventana oculta con withdraw(); messagebox se parchea porque sus dialogos
son modales que bloquearian la corrida esperando un click.

    pytest -q
"""

import sqlite3
import tkinter as tk
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.services.billing_service import BillingService
from flexstock.services.customer_service import CustomerService
from flexstock.services.stock_service import StockService
from flexstock.ui import customers_page as customers_page_module
from flexstock.ui import kit
from flexstock.ui.customers_page import CustomerDialog, CustomersPage


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    monkeypatch.setattr(
        customers_page_module.messagebox, "showerror", lambda *a, **k: None
    )
    monkeypatch.setattr(
        customers_page_module.messagebox, "showinfo", lambda *a, **k: None
    )
    monkeypatch.setattr(
        customers_page_module.messagebox, "askyesno", lambda *a, **k: True
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


def _crear_producto_con_stock(conn, price_milli=1500):
    tier_id = next(t.id for t in PriceTierRepository(conn).list_all() if t.is_default)
    cur = conn.execute(
        "INSERT INTO products (sku, name, current_stock) "
        "VALUES ('FER-0001', 'Tornillo', 10000)"
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO product_prices (product_id, tier_id, price_milli) VALUES (?, ?, ?)",
        (pid, tier_id, price_milli),
    )
    return pid


def _tiene_checkbox_activo(dialogo):
    for hijo in dialogo.winfo_children():
        for nieto in hijo.winfo_children():
            if isinstance(nieto, kit.Checkbutton) and nieto.cget("text") == "Activo":
                return True
    return False


# ------------------------------------------------------------
# carga inicial
# ------------------------------------------------------------
def test_reload_carga_todos_los_clientes(root, service):
    service.create_customer(name="Juan Perez")
    service.create_customer(name="Maria Lopez")

    page = CustomersPage(root, service)

    assert len(page._tree.get_children()) == 2
    assert page._status.cget("text") == "Mostrando 2 de 2 clientes"


def test_pagina_vacia_no_falla(root, service):
    page = CustomersPage(root, service)

    assert page._tree.get_children() == ()
    assert page._status.cget("text") == "Mostrando 0 de 0 clientes"


# ------------------------------------------------------------
# busqueda: filtra en memoria por nombre o cedula
# ------------------------------------------------------------
def test_buscar_por_nombre_filtra_la_tabla(root, service):
    service.create_customer(name="Juan Perez")
    service.create_customer(name="Maria Lopez")
    page = CustomersPage(root, service)

    page._search_var.set("juan")

    assert len(page._tree.get_children()) == 1
    assert page._status.cget("text") == "Mostrando 1 de 2 clientes"


def test_buscar_por_cedula(root, service):
    cliente = service.create_customer(name="Juan Perez", tax_id="V-12345678")
    service.create_customer(name="Maria Lopez")
    page = CustomersPage(root, service)

    page._search_var.set("12345678")

    assert page._tree.get_children() == (str(cliente.id),)


def test_buscar_sin_resultados_deja_tabla_vacia(root, service):
    service.create_customer(name="Juan Perez")
    page = CustomersPage(root, service)

    page._search_var.set("NO EXISTE ESTO")

    assert page._tree.get_children() == ()


def test_borrar_busqueda_muestra_todo_de_nuevo(root, service):
    service.create_customer(name="Juan Perez")
    service.create_customer(name="Maria Lopez")
    page = CustomersPage(root, service)
    page._search_var.set("juan")

    page._search_var.set("")

    assert len(page._tree.get_children()) == 2


def test_cliente_sin_cedula_no_rompe_la_busqueda(root, service):
    """tax_id es None: el filtro no debe reventar comparando con .upper()."""
    service.create_customer(name="Juan Perez")
    page = CustomersPage(root, service)

    page._search_var.set("juan")

    assert len(page._tree.get_children()) == 1


# ------------------------------------------------------------
# Alta
# ------------------------------------------------------------
def test_alta_con_nombre_y_cedula(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("Juan Perez")
    dialogo._set_tax_id("V-12345678")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    clientes = service.list_customers()
    assert len(clientes) == 1
    assert clientes[0].name == "Juan Perez"
    assert clientes[0].sku == "CLI-0001"


def test_alta_solo_con_nombre_ya_no_guarda(root, service, monkeypatch):
    """La cedula es obligatoria en toda ficha: sin ella no se puede
    facturar despues (para vender sin datos estan las notas de entrega)."""
    errores = []
    monkeypatch.setattr(
        customers_page_module.messagebox,
        "showerror",
        lambda _t, m, **k: errores.append(m),
    )
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("Juan Perez")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert service.list_customers() == []
    assert "la cédula/RIF" in errores[0]
    dialogo.destroy()


def test_dialogo_tiene_atajos_enter_y_escape(root, service):
    dialogo = CustomerDialog(root, service, titulo="Nuevo cliente")

    assert dialogo.bind("<Return>") != ""
    assert dialogo.bind("<Escape>") != ""
    dialogo.destroy()


def test_alta_con_todos_los_campos(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("Juan Perez")
    dialogo._set_tax_id("V-12345678")
    dialogo._phone_var.set("0412-1234567")
    dialogo._email_var.set("juan@correo.com")
    dialogo._address_var.set("Calle Falsa 123")
    dialogo._wholesale_var.set(True)

    dialogo._on_guardar()

    cliente = service.list_customers()[0]
    assert cliente.tax_id == "V-12345678"
    assert cliente.is_wholesale is True


def _dialogo_para_facturar(root, service):
    """El mismo formulario de siempre: la cedula es obligatoria en toda
    ficha, se abra desde donde se abra."""
    page = CustomersPage(root, service)
    return CustomerDialog(page, service, titulo="Nuevo cliente")


def test_desde_una_factura_sin_cedula_no_guarda(root, service, monkeypatch):
    errores = []
    monkeypatch.setattr(
        customers_page_module.messagebox,
        "showerror",
        lambda _t, m, **k: errores.append(m),
    )
    dialogo = _dialogo_para_facturar(root, service)
    dialogo._name_var.set("Juan Perez")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert service.list_customers() == []
    assert "la cédula/RIF" in errores[0]
    dialogo.destroy()


def test_desde_una_factura_la_direccion_no_es_obligatoria(root, service):
    """Solo nombre y cedula frenan una venta; la direccion sale en el
    PDF si el cliente la tiene."""
    dialogo = _dialogo_para_facturar(root, service)
    dialogo._name_var.set("Juan Perez")
    dialogo._set_tax_id("V-12345678")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    cliente = service.list_customers()[0]
    assert (cliente.tax_id, cliente.address) == ("V-12345678", None)


def test_desde_una_factura_con_todo_guarda(root, service):
    dialogo = _dialogo_para_facturar(root, service)
    dialogo._name_var.set("Juan Perez")
    dialogo._set_tax_id("V-12345678")
    dialogo._address_var.set("Av. Principal")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    cliente = service.list_customers()[0]
    assert (cliente.tax_id, cliente.address) == ("V-12345678", "Av. Principal")


def test_las_etiquetas_no_llevan_asterisco_ni_cartel_de_obligatorios(root, service):
    def etiquetas(dialogo):
        textos = []

        def recorrer(widget):
            for hijo in widget.winfo_children():
                if isinstance(hijo, kit.Label):
                    textos.append(str(hijo.cget("text")))
                recorrer(hijo)

        recorrer(dialogo)
        return textos

    dialogo = CustomerDialog(CustomersPage(root, service), service, titulo="Nuevo")

    textos = etiquetas(dialogo)
    # La restriccion la avisa el guardado, no la etiqueta.
    assert "Nombre:" in textos
    assert "Cédula/RIF:" in textos
    assert "Dirección:" in textos
    assert "Teléfono:" in textos
    assert not any("*" in texto or "Obligatorios" in texto for texto in textos)
    dialogo.destroy()


def test_alta_nombre_vacio_no_guarda_y_deja_el_dialogo_abierto(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("   ")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert dialogo.winfo_exists()
    assert service.list_customers() == []
    dialogo.destroy()


def test_alta_cedula_invalida_no_guarda(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("Juan Perez")
    dialogo._tax_number_var.set("no-es-una-cedula")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert service.list_customers() == []
    dialogo.destroy()


def test_on_alta_desde_la_pagina_refresca_la_tabla(root, service, monkeypatch):
    page = CustomersPage(root, service)

    def _fake_dialog(master, svc, **kwargs):
        svc.create_customer(name="Desde el boton")

        class _Falso:
            guardado = True

        return _Falso()

    monkeypatch.setattr(customers_page_module, "CustomerDialog", _fake_dialog)
    monkeypatch.setattr(page, "wait_window", lambda *_a: None)

    page._on_alta()

    assert len(page._tree.get_children()) == 1


# ------------------------------------------------------------
# Editar
# ------------------------------------------------------------
def test_editar_prellena_los_campos(root, service):
    cliente = service.create_customer(name="Juan Perez", tax_id="V-12345678")
    page = CustomersPage(root, service)

    dialogo = CustomerDialog(
        page, service, titulo="Editar cliente", customer_id=cliente.id
    )

    assert dialogo._name_var.get() == "Juan Perez"
    assert dialogo._tax_id() == "V-12345678"
    dialogo.destroy()


def test_alta_no_muestra_checkbox_activo(root, service):
    dialogo = CustomerDialog(root, service, titulo="Nuevo cliente")

    assert not _tiene_checkbox_activo(dialogo)
    dialogo.destroy()


# ------------------------------------------------------------
# historial de compras
# ------------------------------------------------------------
def test_editar_muestra_el_historial_de_compras(root, service, billing_service, conn):
    cliente = service.create_customer(name="Juan Perez")
    pid = _crear_producto_con_stock(conn)
    factura = billing_service.crear_borrador(customer_id=cliente.id)
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(2))
    billing_service.confirmar(factura)
    page = CustomersPage(root, service, billing_service=billing_service)

    dialogo = CustomerDialog(
        page,
        service,
        titulo="Editar cliente",
        customer_id=cliente.id,
        billing_service=billing_service,
    )

    assert [f.id for f in dialogo._facturas_cliente] == [factura.id]
    fila = dialogo._tree_facturas.get_children()[0]
    valores = dialogo._tree_facturas.item(fila, "values")
    assert valores[0] == factura.display_number
    assert valores[2] == "Confirmada"
    dialogo.destroy()


def test_editar_cliente_sin_compras_no_muestra_tabla(root, service, billing_service):
    cliente = service.create_customer(name="Juan Perez")

    dialogo = CustomerDialog(
        root,
        service,
        titulo="Editar cliente",
        customer_id=cliente.id,
        billing_service=billing_service,
    )

    assert dialogo._facturas_cliente == []
    assert not hasattr(dialogo, "_tree_facturas")
    dialogo.destroy()


def test_editar_sin_billing_service_no_falla_ni_muestra_seccion(root, service):
    """billing_service es opcional (ver __init__): sin el, el dialogo
    sigue funcionando, solo que sin la seccion de historial."""
    cliente = service.create_customer(name="Juan Perez")

    dialogo = CustomerDialog(
        root, service, titulo="Editar cliente", customer_id=cliente.id
    )

    assert dialogo._facturas_cliente == []
    assert not hasattr(dialogo, "_tree_facturas")
    dialogo.destroy()


def test_alta_no_muestra_seccion_de_historial(root, service, billing_service):
    """Un cliente nuevo todavia no compro nada."""
    dialogo = CustomerDialog(
        root, service, titulo="Nuevo cliente", billing_service=billing_service
    )

    assert not hasattr(dialogo, "_tree_facturas")
    dialogo.destroy()


def _factura_de(billing_service, conn, cliente_id):
    pid = _crear_producto_con_stock(conn)
    factura = billing_service.crear_borrador(customer_id=cliente_id)
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(2))
    billing_service.confirmar(factura)
    return factura


def _espiar_detalle(monkeypatch):
    """
    Reemplaza InvoiceDetailDialog por un doble que anota con que lo
    llamaron y se destruye solo: el de verdad es modal y el
    wait_window() de _on_ver_factura colgaria la corrida esperando un
    click. Se parchea en billing_page porque el import de
    _on_ver_factura es local (se resuelve recien al llamarla).
    """
    from flexstock.ui import billing_page as billing_page_module

    llamadas = []

    class DetalleFalso(tk.Toplevel):
        def __init__(self, master, invoice, **kwargs):
            super().__init__(master)
            llamadas.append((invoice, kwargs))
            # No destroy() directo: wait_window() se quejaria de que la
            # ventana ya no existe. Con after() se cierra sola apenas
            # el wait_window de _on_ver_factura empieza a correr.
            self.after(1, self.destroy)

    monkeypatch.setattr(billing_page_module, "InvoiceDetailDialog", DetalleFalso)
    return llamadas


def test_doble_clic_en_el_historial_abre_el_detalle(
    root, service, billing_service, conn, monkeypatch
):
    """Lo que pidio el autor: tocar una compra abre la misma ventana que
    "Ver detalle" de Facturas, la que tiene el boton de PDF."""
    llamadas = _espiar_detalle(monkeypatch)
    cliente = service.create_customer(name="Juan Perez")
    factura = _factura_de(billing_service, conn, cliente.id)
    dialogo = CustomerDialog(
        root,
        service,
        titulo="Editar cliente",
        customer_id=cliente.id,
        billing_service=billing_service,
    )

    # El iid de cada fila es el id de la factura (ver _build_facturas_section).
    dialogo._tree_facturas.selection_set(str(factura.id))
    dialogo._on_ver_factura()

    assert len(llamadas) == 1
    abierta, kwargs = llamadas[0]
    assert abierta.id == factura.id
    # Completa (con lineas): la lista del historial viene sin ellas.
    assert len(abierta.lines) == 1
    # Sin el service no aparece el boton "Generar PDF".
    assert kwargs["billing_service"] is billing_service
    dialogo.destroy()


def test_el_historial_esta_atado_al_doble_clic(root, service, billing_service, conn):
    cliente = service.create_customer(name="Juan Perez")
    _factura_de(billing_service, conn, cliente.id)
    dialogo = CustomerDialog(
        root,
        service,
        titulo="Editar cliente",
        customer_id=cliente.id,
        billing_service=billing_service,
    )

    assert dialogo._tree_facturas.bind("<Double-1>")
    dialogo.destroy()


def test_ver_detalle_sin_seleccion_no_abre_nada(
    root, service, billing_service, conn, monkeypatch
):
    llamadas = _espiar_detalle(monkeypatch)
    cliente = service.create_customer(name="Juan Perez")
    _factura_de(billing_service, conn, cliente.id)
    dialogo = CustomerDialog(
        root,
        service,
        titulo="Editar cliente",
        customer_id=cliente.id,
        billing_service=billing_service,
    )

    dialogo._on_ver_factura()  # nadie eligio fila

    assert llamadas == []
    dialogo.destroy()


def test_reactivar_cliente_desde_el_dialogo(root, service):
    cliente = service.create_customer(name="Juan Perez", tax_id="V-12345678")
    service.deactivate_customer(cliente.id)
    page = CustomersPage(root, service)

    dialogo = CustomerDialog(
        page, service, titulo="Editar cliente", customer_id=cliente.id
    )
    assert dialogo._active_var.get() is False

    dialogo._active_var.set(True)
    dialogo._on_guardar()

    assert service.get_customer(cliente.id).is_active is True


def test_editar_guarda_cambios_y_no_toca_el_sku(root, service):
    cliente = service.create_customer(name="Juan Perez", tax_id="V-12345678")
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(
        page, service, titulo="Editar cliente", customer_id=cliente.id
    )
    dialogo._name_var.set("Juan Carlos Perez")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    actualizado = service.get_customer(cliente.id)
    assert actualizado.name == "Juan Carlos Perez"
    assert actualizado.sku == cliente.sku


def test_doble_click_esta_conectado_a_editar(root, service):
    page = CustomersPage(root, service)

    assert page._tree.bind("<Double-1>") != ""


def test_editar_sin_seleccion_no_abre_dialogo(root, service, monkeypatch):
    service.create_customer(name="Juan Perez")
    page = CustomersPage(root, service)

    abierto = []
    monkeypatch.setattr(
        customers_page_module, "CustomerDialog", lambda *a, **k: abierto.append(1)
    )

    page._on_editar()

    assert abierto == []


# ------------------------------------------------------------
# Eliminar (desactivar)
# ------------------------------------------------------------
def test_eliminar_desactiva_y_no_borra_la_fila(root, service):
    cliente = service.create_customer(name="Juan Perez")
    page = CustomersPage(root, service)
    page._tree.selection_set(str(cliente.id))

    page._on_eliminar()

    assert service.list_customers() == []
    assert service.get_customer(cliente.id) is not None
    assert service.get_customer(cliente.id).is_active is False


def test_eliminar_refresca_la_tabla(root, service):
    cliente = service.create_customer(name="Juan Perez")
    page = CustomersPage(root, service)
    page._tree.selection_set(str(cliente.id))

    page._on_eliminar()

    assert page._tree.get_children() == ()


def test_eliminar_sin_seleccion_no_falla(root, service):
    service.create_customer(name="Juan Perez")
    page = CustomersPage(root, service)

    page._on_eliminar()  # nada seleccionado: no debe lanzar

    assert len(service.list_customers()) == 1


def test_a_150_por_ciento_los_campos_de_ambas_secciones_arrancan_en_la_misma_x(
    root, service, mostrar_con_tamano
):
    """
    "Datos personales" y "Contacto" son dos LabelFrame con su propia
    columna de etiquetas: sin igualarlas, "Nombre:" y "Telefono:" dejaban
    los campos en x distintas. Se mide con el tema real a 150 %, donde las
    etiquetas son mas anchas, y con la ventana mostrada de verdad.
    """
    from flexstock.ui.theme import ThemeManager

    ThemeManager(root, font_scale=150)
    dialogo = CustomerDialog(CustomersPage(root, service), service, titulo="Nuevo")
    mostrar_con_tamano(root, dialogo, "900x640")

    assert (
        abs(dialogo._name_entry.winfo_rootx() - dialogo._phone_entry.winfo_rootx()) <= 1
    )
    dialogo.destroy()


# ------------------------------------------------------------
# notas (opcional)
# ------------------------------------------------------------
def test_alta_guarda_las_notas_escritas(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("Juan Perez")
    dialogo._set_tax_id("V-12345678")
    dialogo._notes_text.insert("1.0", "Paga a fin de mes\nLlamar antes")

    dialogo._on_guardar()

    (cliente,) = service.list_customers()
    assert cliente.notes == "Paga a fin de mes\nLlamar antes"


def test_alta_sin_notas_las_deja_vacias(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._name_var.set("Juan Perez")
    dialogo._set_tax_id("V-12345678")

    dialogo._on_guardar()

    (cliente,) = service.list_customers()
    assert cliente.notes is None


def test_editar_muestra_las_notas_y_se_pueden_borrar(root, service):
    cliente = service.create_customer(
        name="Juan", tax_id="V-12345678", notes="Llamar antes"
    )
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(
        page, service, titulo="Editar cliente", customer_id=cliente.id
    )
    assert dialogo._notes_text.get("1.0", "end").strip() == "Llamar antes"

    dialogo._notes_text.delete("1.0", "end")
    dialogo._on_guardar()

    assert service.get_customer(cliente.id).notes is None


def test_enter_en_las_notas_escribe_un_salto_y_no_guarda(root, service):
    page = CustomersPage(root, service)
    dialogo = CustomerDialog(page, service, titulo="Nuevo cliente")
    dialogo._notes_text.insert("1.0", "Linea 1")

    resultado = dialogo._on_return_en_notas(None)

    assert resultado == "break"  # corta el <Return> que guarda
    assert "\n" in dialogo._notes_text.get("1.0", "end-1c")
    assert dialogo.guardado is False
    assert service.list_customers() == []


def test_la_columna_notas_va_en_una_sola_linea(root, service):
    from flexstock.ui.columns import ColumnDef

    service.create_customer(name="Juan", notes="Linea 1\nLinea 2")
    page = CustomersPage(root, service)
    page._columnas = [ColumnDef("notes", "Notas")]

    (cliente,) = service.list_customers()

    assert page._valores_de(cliente) == ("Linea 1 Linea 2",)
