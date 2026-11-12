"""
Tests de BillingPage / InvoiceDialog / InvoiceDetailDialog.

Mismo enfoque que test_customers_page.py: widgets de Tkinter reales en
una ventana oculta con withdraw(); messagebox/simpledialog se parchean
porque sus dialogos son modales que bloquearian la corrida esperando
un click.

    pytest -q
"""

import sqlite3
import tkinter as tk
from decimal import Decimal
from tkinter import ttk
from types import SimpleNamespace

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.domain.enums import PaymentMethod
from flexstock.services import billing_service as billing_service_module
from flexstock.services.billing_service import BillingService
from flexstock.services.customer_service import CustomerService
from flexstock.services.product_service import ProductService
from flexstock.services.stock_service import StockService
from flexstock.ui import billing_page as billing_page_module
from flexstock.ui import kit
from flexstock.ui.billing_page import BillingPage, InvoiceDetailDialog, InvoiceDialog


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    from flexstock.ui import customers_page as customers_page_module

    def _dialogo_de_cliente_que_se_cierra_solo(master, *_a, **_k):
        # wait_window(dialogo) espera a que se cierre: uno de verdad
        # colgaria la corrida. Este se destruye solo, sin guardar.
        ventana = tk.Toplevel(master)
        ventana.guardado = False
        ventana.after(0, ventana.destroy)
        return ventana

    monkeypatch.setattr(
        customers_page_module, "CustomerDialog", _dialogo_de_cliente_que_se_cierra_solo
    )
    monkeypatch.setattr(
        billing_page_module.messagebox, "showerror", lambda *a, **k: None
    )
    monkeypatch.setattr(
        billing_page_module.messagebox, "showinfo", lambda *a, **k: None
    )
    monkeypatch.setattr(
        billing_page_module.messagebox, "askyesno", lambda *a, **k: True
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
def product_service(conn):
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
def settings_service(conn):
    from flexstock.services.settings_service import SettingsService

    return SettingsService(conn)


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _dar_stock(conn, product_id, milli):
    conn.execute(
        "UPDATE products SET current_stock = ? WHERE id = ?", (milli, product_id)
    )


def _elegir_cliente(dialogo, customer_service, name="Mostrador", **campos):
    """
    Una factura siempre lleva un cliente de la ficha (ya no hay "venta
    sin cliente"): lo crea y lo deja elegido en la lista del dialogo.
    """
    datos = {"tax_id": "V-12345678", "address": "Calle 1", **campos}
    cliente = customer_service.create_customer(name=name, **datos)
    dialogo._recargar_clientes(cliente.id)
    return cliente


def _elegir_producto(dialogo, product_service, product_id):
    """
    Deja elegido un producto en el campo (releyendo el catalogo: se creo
    despues de abrir el dialogo), como si se hubiera escrito y pulsado Enter.
    """
    productos = product_service.list_products()
    dialogo._producto_picker.set_items(productos)
    dialogo._producto_picker.elegir(next(p for p in productos if p.id == product_id))


def _tier_id_default(product_service):
    """
    El tier por defecto (is_default=1, hoy 'Detal') no es necesariamente
    el primero por posicion: no asumirlo con list_price_tiers()[0].
    """
    return next(t.id for t in product_service.list_price_tiers() if t.is_default)


def _resumen(dialogo):
    """Los datos del cliente que muestra el panel, por campo."""
    return {clave: var.get() for clave, var in dialogo._datos_cliente.items()}


# ------------------------------------------------------------
# BillingPage: listado
# ------------------------------------------------------------
def test_pagina_vacia_no_falla(
    root, billing_service, product_service, customer_service
):
    page = BillingPage(root, billing_service, product_service, customer_service)

    assert page._tree.get_children() == ()
    assert page._status.cget("text") == "Mostrando 0 de 0 facturas"


def test_reload_carga_facturas_confirmadas(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(2))
    billing_service.confirmar(factura)

    page = BillingPage(root, billing_service, product_service, customer_service)

    assert len(page._tree.get_children()) == 1
    assert page._status.cget("text") == "Mostrando 1 de 1 facturas"


def test_usa_el_simbolo_de_moneda_de_settings(
    root, conn, billing_service, product_service, customer_service
):
    set_setting(conn, "currency_symbol", "Bs")
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura)

    page = BillingPage(root, billing_service, product_service, customer_service)

    total_mostrado = page._tree.item(str(factura.id), "values")[-1]
    assert total_mostrado.endswith("Bs")


def test_buscar_por_cliente_filtra_la_tabla(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    f1 = billing_service.crear_borrador(customer_name="Juan Perez")
    billing_service.agregar_linea(f1, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(f1)
    f2 = billing_service.crear_borrador(customer_name="Maria Lopez")
    billing_service.agregar_linea(f2, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(f2)

    page = BillingPage(root, billing_service, product_service, customer_service)
    page._search_var.set("juan")

    assert page._tree.get_children() == (str(f1.id),)


# ------------------------------------------------------------
# BillingPage: detalle
# ------------------------------------------------------------
def test_doble_click_esta_conectado_a_ver_detalle(
    root, billing_service, product_service, customer_service
):
    page = BillingPage(root, billing_service, product_service, customer_service)

    assert page._tree.bind("<Double-1>") != ""


def test_detalle_sin_seleccion_no_abre_dialogo(
    root, billing_service, product_service, customer_service, monkeypatch
):
    page = BillingPage(root, billing_service, product_service, customer_service)
    abierto = []
    monkeypatch.setattr(
        billing_page_module, "InvoiceDetailDialog", lambda *a, **k: abierto.append(1)
    )

    page._on_detalle()

    assert abierto == []


def test_detalle_con_seleccion_abre_dialogo(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura)
    page = BillingPage(root, billing_service, product_service, customer_service)
    page._tree.selection_set(str(factura.id))

    vistas = []
    monkeypatch.setattr(
        billing_page_module,
        "InvoiceDetailDialog",
        lambda master, invoice, **_kwargs: vistas.append(invoice.id),
    )

    page._on_detalle()

    assert vistas == [factura.id]


def _factura_confirmada(conn, billing_service, product_service):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    return billing_service.confirmar(factura)


def _botones(widget):
    for hijo in widget.winfo_children():
        if isinstance(hijo, kit.Button):
            yield hijo
        yield from _botones(hijo)


def test_detalle_sin_billing_service_no_tiene_boton_de_pdf(
    root, conn, billing_service, product_service, customer_service
):
    factura = _factura_confirmada(conn, billing_service, product_service)

    dialogo = InvoiceDetailDialog(root, factura)

    assert all(b.cget("text") != "Generar PDF" for b in _botones(dialogo))
    dialogo.destroy()


def test_detalle_con_billing_service_tiene_boton_de_pdf(
    root, conn, billing_service, product_service, customer_service
):
    factura = _factura_confirmada(conn, billing_service, product_service)

    dialogo = InvoiceDetailDialog(root, factura, billing_service=billing_service)

    assert any(b.cget("text") == "Generar PDF" for b in _botones(dialogo))
    dialogo.destroy()


def test_generar_pdf_desde_el_detalle(
    root,
    conn,
    billing_service,
    product_service,
    customer_service,
    tmp_path,
    monkeypatch,
):
    from flexstock.services import billing_service as billing_service_module

    monkeypatch.setattr(billing_service_module, "EXPORT_DIR", tmp_path)
    set_setting(conn, "bcv_rate", "185.40")  # el PDF sale en Bs
    factura = _factura_confirmada(conn, billing_service, product_service)
    dialogo = InvoiceDetailDialog(root, factura, billing_service=billing_service)

    dialogo._on_generar_pdf()

    assert (tmp_path / f"{factura.display_number}.pdf").exists()
    dialogo.destroy()


def test_pagina_reutiliza_el_mismo_helper_de_pdf(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    """BillingPage._on_generar_pdf y InvoiceDetailDialog._on_generar_pdf llaman
    al mismo generar_pdf() de modulo - un solo lugar que arma el messagebox."""
    factura = _factura_confirmada(conn, billing_service, product_service)
    page = BillingPage(root, billing_service, product_service, customer_service)
    page._tree.selection_set(str(factura.id))

    llamadas = []
    monkeypatch.setattr(
        billing_page_module,
        "generar_pdf",
        lambda parent, service, invoice_id: llamadas.append(invoice_id),
    )

    page._on_generar_pdf()

    assert llamadas == [factura.id]


# ------------------------------------------------------------
# BillingPage: generar PDF
# ------------------------------------------------------------
def test_generar_pdf_sin_seleccion_no_falla(
    root, billing_service, product_service, customer_service
):
    page = BillingPage(root, billing_service, product_service, customer_service)

    page._on_generar_pdf()  # nada seleccionado: no debe lanzar


def test_generar_pdf_con_seleccion_crea_el_archivo(
    root,
    conn,
    billing_service,
    product_service,
    customer_service,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(billing_service_module, "EXPORT_DIR", tmp_path)
    set_setting(conn, "bcv_rate", "185.40")  # el PDF sale en Bs
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura)
    page = BillingPage(root, billing_service, product_service, customer_service)
    page._tree.selection_set(str(factura.id))

    page._on_generar_pdf()

    assert (tmp_path / f"{factura.display_number}.pdf").exists()


# ------------------------------------------------------------
# BillingPage: cierre de caja
# ------------------------------------------------------------
def test_cierre_caja_abre_el_dialogo(
    root, billing_service, product_service, customer_service, monkeypatch
):
    page = BillingPage(root, billing_service, product_service, customer_service)
    abiertos = []
    monkeypatch.setattr(
        billing_page_module,
        "CashClosingDialog",
        lambda master, service, **kwargs: abiertos.append((service, kwargs)),
    )

    page._on_cierre_caja()

    assert len(abiertos) == 1
    servicio_pasado, kwargs = abiertos[0]
    assert servicio_pasado is billing_service
    assert kwargs["currency_symbol"] == page._currency_symbol


# ------------------------------------------------------------
# BillingPage: cancelar
# ------------------------------------------------------------
def test_cancelar_sin_seleccion_no_falla(
    root, billing_service, product_service, customer_service
):
    page = BillingPage(root, billing_service, product_service, customer_service)

    page._on_cancelar()  # nada seleccionado: no debe lanzar


def test_cancelar_revierte_stock_y_refresca_tabla(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(3))
    billing_service.confirmar(factura)
    page = BillingPage(root, billing_service, product_service, customer_service)
    page._tree.selection_set(str(factura.id))
    monkeypatch.setattr(
        billing_page_module.simpledialog, "askstring", lambda *a, **k: "Se equivoco"
    )

    page._on_cancelar()

    actualizada = billing_service.get_invoice(factura.id)
    assert actualizada.is_cancelled
    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 10000

    page.reload()
    assert page._tree.item(str(factura.id), "tags") == ("cancelada",)


def test_factura_confirmada_no_tiene_tag_cancelada(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura)

    page = BillingPage(root, billing_service, product_service, customer_service)

    assert page._tree.item(str(factura.id), "tags") == ""


def test_cancelar_prompt_cancelado_no_cancela_factura(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura)
    page = BillingPage(root, billing_service, product_service, customer_service)
    page._tree.selection_set(str(factura.id))
    monkeypatch.setattr(
        billing_page_module.simpledialog, "askstring", lambda *a, **k: None
    )

    page._on_cancelar()

    assert billing_service.get_invoice(factura.id).is_cancelled is False


# ------------------------------------------------------------
# InvoiceDialog: iniciar
# ------------------------------------------------------------
def test_invoice_dialog_tiene_atajo_escape_no_enter(
    root, billing_service, product_service, customer_service
):
    """
    Solo Esc: Enter queda libre porque el formulario tiene dos acciones
    (agregar linea, confirmar factura) y atarlo a una sola arriesgaria
    confirmar por error mientras se tipea una cantidad.
    """
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert dialogo.bind("<Escape>") != ""
    assert dialogo.bind("<Return>") == ""
    dialogo.destroy()


def test_elegir_al_cliente_inicia_la_factura(
    root, billing_service, product_service, customer_service
):
    """Ya no hay boton "Iniciar factura": elegir al cliente arranca todo."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    assert str(dialogo._producto_picker.entry.cget("state")) == "disabled"

    cliente = _elegir_cliente(dialogo, customer_service, name="Juan Perez")

    assert dialogo._invoice is not None
    assert dialogo._invoice.customer_id == cliente.id
    assert str(dialogo._producto_picker.entry.cget("state")) == "normal"
    assert str(dialogo._cantidad_entry.cget("state")) == "normal"
    assert str(dialogo._btn_confirmar.cget("state")) == "normal"
    dialogo.destroy()


def test_sin_cliente_elegido_no_inicia(
    root, billing_service, product_service, customer_service, monkeypatch
):
    avisos = []
    monkeypatch.setattr(
        billing_page_module.messagebox, "showinfo", lambda _t, m, **k: avisos.append(m)
    )
    customer_service.create_customer(name="Juan Perez", tax_id="V-12345678")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._on_iniciar()  # nadie elegido en la lista

    assert dialogo._invoice is None
    assert "Elige un cliente" in avisos[0]
    dialogo.destroy()


def test_la_factura_ya_no_ofrece_venta_sin_cliente(
    root, billing_service, product_service, customer_service
):
    """Una factura siempre lleva ficha; el nombre suelto quedo solo en
    Cotizaciones/notas de entrega."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert not hasattr(dialogo, "_modo_cliente")
    assert not hasattr(dialogo, "_nombre_directo_var")
    dialogo.destroy()


# ------------------------------------------------------------
# Buscador de clientes: filtra por nombre o cedula y muestra sus datos
# ------------------------------------------------------------
def _nombres_de_clientes(dialogo):
    return [c.name for c in dialogo._cliente_picker.filtrados]


def test_la_lista_muestra_los_datos_de_cada_cliente(
    root, billing_service, product_service, customer_service
):
    cliente = customer_service.create_customer(
        name="Juan Perez",
        tax_id="V-12345678",
        phone="0412-1234567",
        address="Av. Principal",
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    texto, _estilo = dialogo._fila_cliente(cliente)

    assert _nombres_de_clientes(dialogo) == ["Juan Perez"]
    assert "Juan Perez" in texto
    assert "V-12345678" in texto
    assert "0412-1234567" in texto
    dialogo.destroy()


def test_buscar_cliente_filtra_por_nombre(
    root, billing_service, product_service, customer_service
):
    customer_service.create_customer(name="Juan Perez", tax_id="V-11111111")
    customer_service.create_customer(name="Maria Lopez", tax_id="V-22222222")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._cliente_picker.var.set("maria")

    assert _nombres_de_clientes(dialogo) == ["Maria Lopez"]
    dialogo.destroy()


def test_buscar_cliente_filtra_por_cedula(
    root, billing_service, product_service, customer_service
):
    customer_service.create_customer(name="Juan Perez", tax_id="V-11111111")
    customer_service.create_customer(name="Maria Lopez", tax_id="V-22222222")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._cliente_picker.var.set("2222")

    assert _nombres_de_clientes(dialogo) == ["Maria Lopez"]
    dialogo.destroy()


def test_enter_en_el_campo_de_cliente_elige_el_primero_e_inicia(
    root, billing_service, product_service, customer_service
):
    customer_service.create_customer(name="Maria Lopez", tax_id="V-22222222")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    dialogo._cliente_picker.var.set("mar")

    dialogo._cliente_picker._on_return()

    assert dialogo._cliente_id is not None
    assert dialogo._invoice is not None
    assert "V-22222222" in dialogo._datos_cliente_var.get()
    dialogo.destroy()


def test_elegir_un_cliente_muestra_sus_datos(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(
        dialogo,
        customer_service,
        name="Juan Perez",
        phone="0412-1234567",
        address="Av. Principal",
    )

    datos = dialogo._datos_cliente_var.get()
    assert "Cédula/RIF: V-12345678" in datos
    assert "Tel: 0412-1234567" in datos
    assert "Dir: Av. Principal" in datos
    assert "V-12345678" in datos
    assert "0412-1234567" in datos
    assert "Av. Principal" in datos
    dialogo.destroy()


def test_los_datos_del_cliente_no_cambian_el_tamano_de_la_ventana(
    root, billing_service, product_service, customer_service
):
    """
    Un texto que cambia de largo con cada cliente le cambiaba el tamano
    al panel (y elegir uno con direccion larga estiraba la ventana). El
    renglon de datos tiene alto fijo.
    """
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    dialogo.update_idletasks()
    antes = (dialogo.winfo_reqwidth(), dialogo.winfo_reqheight())

    _elegir_cliente(
        dialogo,
        customer_service,
        name="Distribuidora de Materiales y Ferreteria El Gran Constructor",
        address="Avenida Principal, Sector Los Robles, Galpon 4, Valencia " * 3,
    )
    dialogo.update_idletasks()

    assert (dialogo.winfo_reqwidth(), dialogo.winfo_reqheight()) == antes
    dialogo.destroy()


def test_escribir_sobre_el_cliente_elegido_lo_suelta(
    root, billing_service, product_service, customer_service
):
    """Sin factura iniciada (la ficha quedo incompleta), volver a escribir
    en el campo deja al dialogo sin cliente."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    cliente = customer_service.create_customer(name="Juan Perez", tax_id="V-12345678")
    dialogo._cliente_picker.elegir(cliente, avisar=False)
    dialogo._cliente_id = cliente.id

    dialogo._cliente_picker.var.set("maria")

    assert dialogo._cliente_id is None
    dialogo.destroy()


# ------------------------------------------------------------
# Factura: cedula/RIF obligatoria (las notas de entrega quedan libres:
# ver test_quotations_page.py)
# ------------------------------------------------------------
def _errores_de(monkeypatch):
    mensajes = []
    monkeypatch.setattr(
        billing_page_module.messagebox,
        "showerror",
        lambda _t, m, **k: mensajes.append(m),
    )
    return mensajes


def test_cliente_registrado_sin_datos_ofrece_completar_su_ficha(
    root, billing_service, product_service, customer_service, monkeypatch
):
    from flexstock.ui import customers_page as customers_page_module

    cliente = customer_service.create_customer(name="Juan Perez")  # sin RIF ni dir.
    abiertos = []

    def _completa_la_ficha(master, service, **kwargs):
        abiertos.append(kwargs)
        service.update_customer(
            kwargs["customer_id"],
            name="Juan Perez",
            tax_id="V-12345678",
            address="Av. Principal",
            is_active=True,
        )
        ventana = tk.Toplevel(master)
        ventana.guardado = True
        ventana.after(0, ventana.destroy)
        return ventana

    monkeypatch.setattr(customers_page_module, "CustomerDialog", _completa_la_ficha)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._cliente_picker.elegir(cliente)  # al elegirlo se intenta iniciar

    # se abrio la ficha DE ESE cliente (el formulario ya exige la cedula
    # por su cuenta, ver CustomerDialog)...
    assert abiertos[0]["customer_id"] == cliente.id
    # ...y con la ficha completa la factura arranca sola
    assert dialogo._invoice is not None
    assert dialogo._invoice.customer_tax_id == "V-12345678"
    assert dialogo._invoice.customer_address == "Av. Principal"


def test_cliente_registrado_sin_datos_y_sin_completarlo_no_inicia(
    root, billing_service, product_service, customer_service
):
    cliente = customer_service.create_customer(name="Juan Perez")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    # el dialogo de cliente del fixture se cierra sin guardar
    dialogo._cliente_picker.elegir(cliente)

    assert dialogo._invoice is None
    # el campo queda vacio para elegir a otro
    assert dialogo._cliente_picker.elegido() is None
    assert dialogo._cliente_id is None
    dialogo.destroy()


def test_cliente_registrado_sin_datos_y_diciendo_que_no_no_abre_la_ficha(
    root, billing_service, product_service, customer_service, monkeypatch
):
    from flexstock.ui import customers_page as customers_page_module

    cliente = customer_service.create_customer(name="Juan Perez")
    abiertos = []
    monkeypatch.setattr(
        customers_page_module, "CustomerDialog", lambda *a, **k: abiertos.append(1)
    )
    monkeypatch.setattr(
        billing_page_module.messagebox, "askyesno", lambda *a, **k: False
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._cliente_picker.elegir(cliente)

    assert abiertos == []
    assert dialogo._invoice is None


def test_confirmar_pide_al_servicio_los_datos_fiscales(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    dialogo._forma_pago_combo.current(0)
    recibidos = {}
    original = billing_service.confirmar

    def _espia(invoice, **kwargs):
        recibidos.update(kwargs)
        return original(invoice, **kwargs)

    monkeypatch.setattr(billing_service, "confirmar", _espia)

    dialogo._on_confirmar()

    assert recibidos["require_fiscal_customer"] is True


def test_iniciar_sin_seleccionar_cliente_no_crea_borrador(
    root, billing_service, product_service, customer_service
):
    customer_service.create_customer(name="Juan Perez")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._on_iniciar()

    assert dialogo._invoice is None


# ------------------------------------------------------------
# InvoiceDialog: lineas
# ------------------------------------------------------------
def test_agregar_linea_antes_de_iniciar_no_falla(
    root, conn, billing_service, product_service, customer_service
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")

    dialogo._on_agregar_linea()  # sin invoice todavia: no debe lanzar

    assert dialogo._invoice is None


def test_agregar_linea_actualiza_treeview_y_totales(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()

    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("3")
    dialogo._on_agregar_linea()

    assert len(dialogo._lineas_tree.get_children()) == 1
    assert len(dialogo._invoice.lines) == 1
    assert "4,50" in dialogo._subtotal_var.get()  # 1,50 x 3


def _simular_click_en_columna_quitar(dialogo, fila_iid: str) -> None:
    """
    Evita depender de coordenadas de pixeles reales (fragil en CI): se
    parchean identify_region/column/row del propio Treeview -lo unico
    que _on_click_lineas les pide- para simular "se hizo click en la x
    de la fila `fila_iid`", sin necesitar que la ventana este mapeada.
    """
    tree = dialogo._lineas_tree
    tree.identify_region = lambda x, y: "cell"
    tree.identify_column = lambda x: f"#{len(tree['columns'])}"  # la x es la ultima
    tree.identify_row = lambda y: fila_iid
    dialogo._on_click_lineas(SimpleNamespace(x=0, y=0))


def test_click_en_x_quita_la_linea(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    assert len(dialogo._invoice.lines) == 1

    _simular_click_en_columna_quitar(dialogo, "0")

    assert dialogo._invoice.lines == []
    assert dialogo._lineas_tree.get_children() == ()


def test_click_en_x_quita_solo_la_linea_clickeada(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    otro = product_service.create_product(
        name="Tuerca",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("0.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    _dar_stock(conn, otro, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    _elegir_producto(dialogo, product_service, pid)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    _elegir_producto(dialogo, product_service, otro)
    dialogo._cantidad_var.set("2")
    dialogo._on_agregar_linea()
    assert len(dialogo._invoice.lines) == 2

    _simular_click_en_columna_quitar(dialogo, "0")  # saca la primera

    assert len(dialogo._invoice.lines) == 1
    assert dialogo._invoice.lines[0].quantity_milli == 2000


def test_click_fuera_de_la_columna_quitar_no_hace_nada(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()

    tree = dialogo._lineas_tree
    tree.identify_region = lambda x, y: "cell"
    tree.identify_column = lambda x: "#2"  # "nombre", no "quitar"
    tree.identify_row = lambda y: "0"
    dialogo._on_click_lineas(SimpleNamespace(x=0, y=0))

    assert len(dialogo._invoice.lines) == 1


def test_filtrar_productos_reduce_la_lista(
    root, product_service, customer_service, billing_service
):
    tier_id = _tier_id_default(product_service)
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={tier_id: Decimal("1.00")},
    )
    product_service.create_product(
        name="Martillo",
        category_id=product_service.default_category_id(),
        prices={tier_id: Decimal("5.00")},
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._producto_picker.var.set("martillo")

    assert [p.name for p in dialogo._producto_picker.filtrados] == ["Martillo"]
    dialogo.destroy()


# ------------------------------------------------------------
# InvoiceDialog: cerrar
# ------------------------------------------------------------
def test_cerrar_sin_lineas_no_pregunta_nada(
    root, billing_service, product_service, customer_service, monkeypatch
):
    preguntas = []
    monkeypatch.setattr(
        billing_page_module.messagebox,
        "askyesno",
        lambda *a, **k: preguntas.append(1) or True,
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    dialogo._on_cerrar()

    assert preguntas == []
    assert not dialogo.winfo_exists()


def test_cerrar_con_lineas_pregunta_y_respeta_el_no(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    monkeypatch.setattr(
        billing_page_module.messagebox, "askyesno", lambda *a, **k: False
    )

    dialogo._on_cerrar()

    assert dialogo.winfo_exists()  # el "No" del aviso dejo el dialogo abierto
    dialogo.destroy()


def test_cerrar_con_lineas_y_confirmacion_positiva_cierra(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    monkeypatch.setattr(
        billing_page_module.messagebox, "askyesno", lambda *a, **k: True
    )

    dialogo._on_cerrar()

    assert not dialogo.winfo_exists()


# ------------------------------------------------------------
# InvoiceDialog: confirmar
# ------------------------------------------------------------
def test_confirmar_sin_lineas_no_confirma(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()

    dialogo._on_confirmar()

    assert dialogo.guardado is False


def test_confirmar_con_lineas_guarda_y_cierra(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("2")
    dialogo._on_agregar_linea()
    dialogo._forma_pago_combo.current(0)

    dialogo._on_confirmar()

    assert dialogo.guardado is True
    assert billing_service.list_invoices()[0].display_number == "FAC-000001"


def test_confirmar_con_stock_insuficiente_pregunta_y_permite_seguir(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    """stock=0 y allow_negative_stock='1' (default): pide confirmacion via
    askyesno (parchado a True por el fixture autouse) y sigue igual."""
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("2")
    dialogo._on_agregar_linea()
    dialogo._forma_pago_combo.current(0)

    dialogo._on_confirmar()

    assert dialogo.guardado is True


# ------------------------------------------------------------
# InvoiceDialog: forma de pago obligatoria
# ------------------------------------------------------------
def test_confirmar_sin_forma_de_pago_no_confirma_y_avisa(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("2")
    dialogo._on_agregar_linea()
    # a proposito NO se elige forma de pago (combobox queda en current() == -1)
    errores = []
    monkeypatch.setattr(
        billing_page_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    dialogo._on_confirmar()

    assert dialogo.guardado is False
    assert errores  # avisó con un mensaje claro, mismo estilo que los demás showerror
    assert billing_service.list_invoices() == []


def test_confirmar_guarda_la_forma_de_pago_elegida(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    # Zelle es el ultimo de _FORMA_PAGO_OPCIONES: prueba que el indice
    # elegido en el combobox mapea a la forma de pago correcta, no
    # siempre a la primera.
    dialogo._forma_pago_combo.current(4)

    dialogo._on_confirmar()

    factura_guardada = billing_service.list_invoices()[0]
    assert factura_guardada.payment_method is PaymentMethod.ZELLE


# ------------------------------------------------------------
# InvoiceDialog: fecha/hora de elaboracion
# ------------------------------------------------------------
def test_dialogo_arranca_con_fecha_y_hora_de_ahora(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert dialogo._fecha_var.get() != ""
    assert dialogo._hora_var.get() != ""
    dialogo.destroy()


def test_confirmar_guarda_la_fecha_editada(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    dialogo._forma_pago_combo.current(0)
    dialogo._fecha_var.set("15/01/2024")
    dialogo._hora_var.set("09:30")

    dialogo._on_confirmar()

    factura_guardada = billing_service.list_invoices()[0]
    assert factura_guardada.issued_at.strftime("%d/%m/%Y %H:%M") == "15/01/2024 09:30"


def test_confirmar_con_fecha_invalida_no_confirma_y_avisa(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    dialogo._forma_pago_combo.current(0)
    dialogo._fecha_var.set("31 de febrero")
    errores = []
    monkeypatch.setattr(
        billing_page_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    dialogo._on_confirmar()

    assert dialogo.guardado is False
    assert errores
    assert billing_service.list_invoices() == []
    dialogo.destroy()


# ------------------------------------------------------------
# InvoiceDialog: IVA por documento (Modo avanzado)
# ------------------------------------------------------------
def _tiene_checkbox_iva(dialogo) -> bool:
    def _buscar(widget):
        for hijo in widget.winfo_children():
            if (
                isinstance(hijo, kit.Checkbutton)
                and hijo.cget("text") == "Cobrar IVA en esta factura"
            ):
                return True
            if _buscar(hijo):
                return True
        return False

    return _buscar(dialogo)


def test_el_checkbox_de_iva_se_ve_siempre_y_arranca_desmarcado(
    root, billing_service, product_service, customer_service
):
    """Sin Modo avanzado tambien: si no, no habria como cobrar IVA."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert _tiene_checkbox_iva(dialogo)
    assert dialogo._cobrar_iva_var.get() is False
    dialogo.destroy()


def test_el_checkbox_de_iva_tambien_esta_con_modo_avanzado(
    root, billing_service, product_service, customer_service, settings_service
):
    settings_service.set_advanced_mode(True)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings_service,
    )

    assert _tiene_checkbox_iva(dialogo)
    assert dialogo._cobrar_iva_var.get() is False
    dialogo.destroy()


def test_destildar_iva_recalcula_los_totales_en_vivo(
    root, conn, billing_service, product_service, customer_service, settings_service
):
    set_setting(conn, "default_tax_rate", "16.00")
    settings_service.set_advanced_mode(True)
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings_service,
    )
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    # arranca desmarcado: en pantalla se ve desde el principio que no
    # se esta cobrando IVA
    assert "0%" in dialogo._iva_titulo_var.get()
    assert "0,00" in dialogo._iva_var.get()

    dialogo._cobrar_iva_var.set(True)
    dialogo._on_toggle_iva()

    assert "16.00%" in dialogo._iva_titulo_var.get()
    assert dialogo._iva_var.get() == "1,60$"
    dialogo.destroy()


def test_confirmar_sin_iva_guarda_la_factura_con_tasa_cero(
    root, conn, billing_service, product_service, customer_service, settings_service
):
    set_setting(conn, "default_tax_rate", "16.00")
    settings_service.set_advanced_mode(True)
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings_service,
    )
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    dialogo._cobrar_iva_var.set(False)
    dialogo._on_toggle_iva()
    dialogo._forma_pago_combo.current(0)

    dialogo._on_confirmar()

    factura_guardada = billing_service.list_invoices()[0]
    assert factura_guardada.tax_rate == Decimal(0)
    assert factura_guardada.tax_cents == 0
    assert factura_guardada.total_cents == 1000  # 10,00$ sin IVA


def test_confirmar_con_iva_tildado_usa_la_tasa_de_settings(
    root, conn, billing_service, product_service, customer_service, settings_service
):
    """Tildando el checkbox (arranca desmarcado), confirmar() relee la
    tasa de settings, igual que siempre."""
    set_setting(conn, "default_tax_rate", "16.00")
    settings_service.set_advanced_mode(True)
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings_service,
    )
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    dialogo._producto_picker.elegir_indice(0)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()
    dialogo._cobrar_iva_var.set(True)  # arranca desmarcado
    dialogo._on_toggle_iva()
    dialogo._forma_pago_combo.current(0)

    dialogo._on_confirmar()

    factura_guardada = billing_service.list_invoices()[0]
    assert factura_guardada.tax_rate == Decimal("16.00")


# ------------------------------------------------------------
# InvoiceDetailDialog
# ------------------------------------------------------------
def test_detail_dialog_muestra_lineas(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(2))
    billing_service.confirmar(factura)

    dialogo = InvoiceDetailDialog(root, billing_service.get_invoice(factura.id))

    assert dialogo.title() == "Factura FAC-000001"
    dialogo.destroy()


# ------------------------------------------------------------
# selector de tasa: muestra el total convertido, la factura no cambia
# ------------------------------------------------------------
def _rates_con_tasas(conn):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    set_setting(conn, "bcv_rate", "185.40")
    rates = RateDisplayService(conn, ExchangeRateService(conn))
    rates.set_personal_rate(Decimal(260))
    return rates


def _factura_con_una_linea(dialogo, product_service, conn, customer_service):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    # por la pantalla y no por el servicio: es ahi donde se aplica el
    # factor de precio de Modo avanzado (ver _on_agregar_linea). El
    # producto se creo despues de abrir el dialogo, asi que hay que
    # releer su catalogo.
    _elegir_producto(dialogo, product_service, pid)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()


def test_sin_servicio_de_tasas_no_hay_montos_en_bs(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert dialogo._total_convertido_var.get() == ""
    dialogo.destroy()


def test_los_montos_en_bs_salen_a_tasa_bcv(
    root, conn, billing_service, product_service, customer_service
):
    """Sin Modo avanzado el precio es el del catalogo y los Bs salen de
    la tasa BCV; ya no hay selector que elegir."""
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),  # BCV 185,40
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    dialogo._cobrar_iva_var.set(True)
    dialogo._on_toggle_iva()

    # 10,00$ + 16% = 11,60$ ; 11,60 x 185,40 = 2.150,64 Bs
    assert dialogo._total_convertido_var.get() == "2.150,64 Bs"
    assert "185,40 Bs/$" in dialogo._tasa_info_var.get()
    dialogo.destroy()


def test_con_modo_avanzado_se_elige_a_que_tasa_cobrar(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )

    radios = _textos_de_radios(dialogo)

    assert "Tasa BCV" in radios
    assert "Tasa de referencia" in radios
    # arranca en la de referencia: es para lo que se prende el modo
    assert dialogo._tasa_var.get() == "propia"
    # ningun texto dice de donde sale esa tasa (se le muestra al cliente)
    assert not [r for r in radios if "propia" in r.lower()]
    dialogo.destroy()


def test_sin_modo_avanzado_no_hay_a_que_tasa_elegir(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),
    )

    assert not [r for r in _textos_de_radios(dialogo) if "Tasa" in r]
    assert dialogo._tasa_var.get() == "bcv"  # siempre la oficial
    dialogo.destroy()


def test_cobrando_a_la_bcv_el_precio_no_se_altera(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    dialogo._tasa_var.set("bcv")

    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    assert dialogo._invoice.lines[0].unit_price_cents == 1000  # el del catalogo
    dialogo.destroy()


def test_cambiar_de_tasa_recalcula_las_lineas_ya_cargadas(
    root, conn, billing_service, product_service, customer_service
):
    """Toda la factura va a UNA tasa, no una mitad a cada una."""
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    assert dialogo._invoice.lines[0].unit_price_cents == 1402  # 10$ x 260/185,40

    dialogo._tasa_var.set("bcv")
    dialogo._on_cambiar_tasa()

    assert dialogo._invoice.lines[0].unit_price_cents == 1000
    assert dialogo._lineas_tree.item("0", "values")[3] == "10,00$"

    dialogo._tasa_var.set("propia")
    dialogo._on_cambiar_tasa()

    assert dialogo._invoice.lines[0].unit_price_cents == 1402
    dialogo.destroy()


def test_cambiar_de_tasa_conserva_cantidad_y_nivel_de_precio(
    root, conn, billing_service, product_service, customer_service
):
    tiers = product_service.list_price_tiers()
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={tiers[0].id: Decimal("8.00"), tiers[2].id: Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    _elegir_cliente(dialogo, customer_service)
    dialogo._on_iniciar()
    _elegir_producto(dialogo, product_service, pid)
    dialogo._tier_combo.current(0)  # Precio 1
    dialogo._cantidad_var.set("3")
    dialogo._on_agregar_linea()

    dialogo._tasa_var.set("bcv")
    dialogo._on_cambiar_tasa()

    linea = dialogo._invoice.lines[0]
    assert linea.quantity_milli == 3000
    assert linea.tier_id == tiers[0].id
    assert linea.unit_price_cents == 800  # el Precio 1, sin alterar
    dialogo.destroy()


def test_sin_tasa_bcv_cargada_lo_avisa(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    assert dialogo._total_convertido_var.get() == "-"
    assert "sin cargar" in dialogo._tasa_info_var.get()
    dialogo.destroy()


# ------------------------------------------------------------
# La tasa elegida se guarda EN la factura al confirmar (snapshot): su
# PDF sale en Bs con esa tasa y "Ver detalle" muestra lo mismo
# ------------------------------------------------------------
def _confirmar(dialogo, product_service, conn, customer_service):
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    dialogo._forma_pago_combo.current(0)
    dialogo._on_confirmar()


def _dialogo_con_tasas(root, conn, billing_service, product_service, customer_service):
    return InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),  # BCV 185,40 y propia 260
    )


def test_confirmar_guarda_la_tasa_bcv_en_la_factura(
    root, conn, billing_service, product_service, customer_service
):
    """Siempre la BCV: los montos en $ ya estan expresados a esa tasa."""
    dialogo = _dialogo_con_tasas(
        root, conn, billing_service, product_service, customer_service
    )

    _confirmar(dialogo, product_service, conn, customer_service)

    factura = billing_service.get_invoice(billing_service.list_invoices()[0].id)
    assert factura.bs_rate == Decimal("185.40")
    assert factura.total_cents == 1000  # 10,00$ sin IVA (arranca desmarcado)


def test_confirmar_sin_servicio_de_tasas_no_guarda_tasa(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    dialogo._forma_pago_combo.current(0)

    dialogo._on_confirmar()

    assert billing_service.list_invoices()[0].bs_rate is None


def test_confirmar_con_la_tasa_sin_cargar_pregunta_y_si_se_cancela_no_guarda(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    preguntas = []
    monkeypatch.setattr(
        billing_page_module.messagebox,
        "askyesno",
        lambda _t, m, **k: preguntas.append(m) or False,
    )
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )

    _confirmar(dialogo, product_service, conn, customer_service)

    assert "tasa BCV" in preguntas[-1]
    assert billing_service.list_invoices() == []  # nada se guardo


def test_confirmar_con_la_tasa_sin_cargar_y_aceptando_guarda_sin_tasa(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )

    _confirmar(dialogo, product_service, conn, customer_service)

    assert billing_service.list_invoices()[0].bs_rate is None  # askyesno -> True


def _detalle(root, conn, factura_id, billing_service, *, tasas=True):
    return InvoiceDetailDialog(
        root,
        billing_service.get_invoice(factura_id),
        billing_service=billing_service,
        rate_display_service=_rates_con_tasas(conn) if tasas else None,
    )


def _confirmada_a(conn, billing_service, product_service, bs_rate):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    return billing_service.confirmar(factura, bs_rate=bs_rate)


def test_el_detalle_usa_la_tasa_guardada_y_no_la_de_hoy(
    root, conn, billing_service, product_service
):
    """La de hoy es 185,40 (ver _rates_con_tasas); la factura se emitio a 100."""
    factura = _confirmada_a(conn, billing_service, product_service, Decimal(100))

    dialogo = _detalle(root, conn, factura.id, billing_service)

    valores = dialogo._tree.item(dialogo._tree.get_children()[0], "values")
    assert "1.000,00 Bs" in valores  # 10,00$ x 100, no x 185,40
    assert "a la tasa de la factura, 100 Bs/$" in dialogo._totales_bs_var.get()
    assert "1.160,00 Bs" in dialogo._totales_bs_var.get()  # total con IVA
    dialogo.destroy()


def test_el_detalle_de_una_factura_con_tasa_no_ofrece_elegir_otra(
    root, conn, billing_service, product_service
):
    from flexstock.services.settings_service import SettingsService

    SettingsService(conn).set_advanced_mode(True)
    factura = _confirmada_a(conn, billing_service, product_service, Decimal(100))
    dialogo = InvoiceDetailDialog(
        root,
        billing_service.get_invoice(factura.id),
        billing_service=billing_service,
        settings_service=SettingsService(conn),
        rate_display_service=_rates_con_tasas(conn),
    )

    assert not [r for r in _textos_de_radios(dialogo) if "Tasa" in r]
    dialogo.destroy()


def test_el_detalle_funciona_con_tasa_guardada_aunque_no_haya_servicio_de_tasas(
    root, conn, billing_service, product_service
):
    factura = _confirmada_a(conn, billing_service, product_service, Decimal(100))

    dialogo = _detalle(root, conn, factura.id, billing_service, tasas=False)

    assert "1.160,00 Bs" in dialogo._totales_bs_var.get()
    dialogo.destroy()


def test_el_detalle_de_una_factura_sin_tasa_usa_la_de_hoy(
    root, conn, billing_service, product_service
):
    factura = _confirmada_a(conn, billing_service, product_service, None)

    dialogo = _detalle(root, conn, factura.id, billing_service)

    assert "a la tasa de hoy, 185,40 Bs/$" in dialogo._totales_bs_var.get()
    dialogo.destroy()


def test_las_lineas_muestran_el_total_en_dolares_y_en_bolívares(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),  # BCV 185,40
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    valores = dialogo._lineas_tree.item("0", "values")

    # 10,00$ del catalogo (sin Modo avanzado no se altera) x 185,40
    assert valores[1:] == ("Tornillo", "1", "10,00$", "10,00$", "1.854,00 Bs", "❌")
    assert dialogo._subtotal_bs_var.get() == "1.854,00 Bs"
    assert dialogo._total_var.get() == "10,00$"  # IVA desmarcado por defecto
    dialogo.destroy()


def test_encabezados_de_las_lineas_de_la_factura(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    tree = dialogo._lineas_tree
    assert [tree.heading(col, "text") for col in tree["columns"]] == [
        "SKU",
        "Nombre",
        "Cant.",
        "Precio unit. $",
        "Total $ BCV",
        "Total Bs",
        "",
    ]
    dialogo.destroy()


def test_sin_tasa_cargada_la_columna_bs_muestra_guion(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    assert dialogo._lineas_tree.item("0", "values")[5] == "-"
    assert "sin cargar" in dialogo._tasa_info_var.get()
    dialogo.destroy()


# ------------------------------------------------------------
# Modo avanzado: el precio entra alterado (tasa de referencia / BCV), de
# forma que la factura queda como una venta normal a tasa BCV
# ------------------------------------------------------------
def _dialogo_avanzado(root, conn, billing_service, product_service, customer_service):
    from flexstock.services.settings_service import SettingsService

    settings = SettingsService(conn)
    settings.set_advanced_mode(True)
    return InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=_rates_con_tasas(conn),  # BCV 185,40 y referencia 260
    )


def test_con_modo_avanzado_el_precio_de_la_linea_se_altera(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    # 10,00$ x 260 / 185,40 = 14,02$ ; en Bs: 14,02 x 185,40 = 2.599,31
    valores = dialogo._lineas_tree.item("0", "values")
    assert valores[3] == "14,02$"  # precio unitario ya alterado
    assert valores[4] == "14,02$"  # total de la linea
    assert valores[5] == "2.599,31 Bs"
    assert dialogo._invoice.lines[0].unit_price_cents == 1402
    dialogo.destroy()


def test_sin_modo_avanzado_el_precio_es_el_del_catalogo(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    assert dialogo._invoice.lines[0].unit_price_cents == 1000
    dialogo.destroy()


def test_con_modo_avanzado_pero_sin_tasas_el_precio_no_se_altera(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService
    from flexstock.services.settings_service import SettingsService

    settings = SettingsService(conn)
    settings.set_advanced_mode(True)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    assert dialogo._invoice.lines[0].unit_price_cents == 1000
    dialogo.destroy()


def test_la_factura_alterada_se_guarda_a_tasa_bcv(
    root, conn, billing_service, product_service, customer_service
):
    """Queda como una venta normal a BCV: el $ ya viene alterado y la
    tasa guardada es la oficial."""
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )

    _confirmar(dialogo, product_service, conn, customer_service)

    factura = billing_service.get_invoice(billing_service.list_invoices()[0].id)
    assert factura.bs_rate == Decimal("185.40")
    assert factura.total_cents == 1402


# ------------------------------------------------------------
# Guia de precios: lo que costaria el producto antes de agregarlo
# ------------------------------------------------------------
def test_la_guia_muestra_el_precio_en_las_tres_monedas(
    root, conn, billing_service, product_service, customer_service
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    )
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )

    dialogo._producto_picker.elegir_indice(0)

    # 10,00$ | 10 x 260 = 2.600,00 Bs | 2.600 / 185,40 = 14,02$ BCV
    guia = dialogo._guia_precio_var.get()
    assert "10,00$" in guia
    assert "2.600,00 Bs" in guia
    assert "14,02$ BCV" in guia
    dialogo.destroy()


def test_la_guia_sigue_al_nivel_de_precio_elegido(
    root, conn, billing_service, product_service, customer_service
):
    tiers = product_service.list_price_tiers()
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={tiers[0].id: Decimal("8.00"), tiers[2].id: Decimal("10.00")},
    )
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    _elegir_cliente(dialogo, customer_service)  # sin cliente, Nivel esta apagado
    dialogo._producto_picker.elegir_indice(0)

    dialogo._tier_combo.current(0)  # Precio 1
    dialogo._on_cambiar_nivel_de_precio()

    assert "8,00$" in dialogo._guia_precio_var.get()
    dialogo.destroy()


def test_sin_producto_elegido_la_guia_lo_dice(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )

    assert dialogo._guia_precio_var.get() == billing_page_module._SIN_PRODUCTO
    dialogo.destroy()


def test_sin_modo_avanzado_no_hay_guia_de_precios(
    root, conn, billing_service, product_service, customer_service
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    )
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),
    )

    etiquetas = []

    def recorrer(widget):
        for hijo in widget.winfo_children():
            if isinstance(hijo, kit.Label):
                etiquetas.append(str(hijo.cget("text")))
            recorrer(hijo)

    recorrer(dialogo)
    assert billing_page_module._SIN_PRODUCTO not in etiquetas
    dialogo.destroy()


def _textos_de_radios(widget):
    textos = []
    for hijo in widget.winfo_children():
        if isinstance(hijo, kit.Radiobutton):
            textos.append(hijo.cget("text"))
        textos.extend(_textos_de_radios(hijo))
    return textos


def _pagina_con_una_factura(
    root, conn, billing_service, product_service, customer_service, *, bs_rate
):
    from flexstock.services.settings_service import SettingsService
    from flexstock.ui import columns

    settings = SettingsService(conn)
    settings.set_visible_columns(columns.INVOICES, ("numero", "total", "total_bs"))
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura, bs_rate=bs_rate)
    page = BillingPage(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=_rates_con_tasas(conn),  # BCV de hoy: 185,40
    )
    return page, page._tree.item(page._tree.get_children()[0], "values")


def test_la_columna_en_bs_usa_la_tasa_que_guardo_la_factura(
    root, conn, billing_service, product_service, customer_service
):
    """Y no las de hoy: es el monto que de verdad se cobro."""
    page, valores = _pagina_con_una_factura(
        root,
        conn,
        billing_service,
        product_service,
        customer_service,
        bs_rate=Decimal(100),
    )

    # 10,00$ + 16% = 11,60$ ; x 100 = 1.160,00 Bs (no x 185,40)
    assert valores[1:] == ("11,60$", "1.160,00 Bs")
    page.destroy()


def test_una_factura_sin_tasa_guardada_cae_a_la_bcv_de_hoy(
    root, conn, billing_service, product_service, customer_service
):
    page, valores = _pagina_con_una_factura(
        root, conn, billing_service, product_service, customer_service, bs_rate=None
    )

    assert valores[2] == "2.150,64 Bs"  # 11,60$ x 185,40, la BCV de hoy
    page.destroy()


def test_sin_tasas_la_columna_en_bs_muestra_un_guion(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.services.settings_service import SettingsService
    from flexstock.ui import columns

    settings = SettingsService(conn)
    settings.set_visible_columns(columns.INVOICES, ("numero", "total_bs"))
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(factura)  # sin tasa

    page = BillingPage(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
    )

    assert page._tree.item(page._tree.get_children()[0], "values")[1] == "-"
    page.destroy()


def test_la_lista_muestra_los_tres_montos_de_la_factura(
    root, conn, billing_service, product_service, customer_service
):
    """El $ cobrado (a tasa BCV), ese mismo monto en Bs, y lo que
    habrian costado los productos a precio de catalogo."""
    from flexstock.services.settings_service import SettingsService
    from flexstock.ui import columns

    settings = SettingsService(conn)
    settings.set_advanced_mode(True)
    settings.set_visible_columns(
        columns.INVOICES, ("numero", "total", "total_bs", "total_catalogo")
    )
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("20.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(
        factura,
        product_id=pid,
        quantity=Decimal(1),
        price_factor=Decimal(1000) / Decimal("848.55"),
    )
    billing_service.confirmar(
        factura,
        tax_rate_override=Decimal(0),
        bs_rate=Decimal("848.55"),
        ref_rate=Decimal(1000),
    )

    page = BillingPage(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=_rates_con_tasas(conn),
    )

    # el orden lo fija el catalogo: ref, BCV, Bs
    valores = page._tree.item(page._tree.get_children()[0], "values")
    assert valores[1:] == ("20,00$", "23,57$", "20.000,32 Bs")
    page.destroy()


def test_una_factura_a_tasa_bcv_muestra_el_mismo_total_en_las_dos_columnas(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.services.settings_service import SettingsService
    from flexstock.ui import columns

    settings = SettingsService(conn)
    settings.set_advanced_mode(True)
    settings.set_visible_columns(columns.INVOICES, ("total", "total_catalogo"))
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("20.00")},
    ).id
    _dar_stock(conn, pid, 10000)
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    billing_service.confirmar(
        factura, tax_rate_override=Decimal(0), bs_rate=Decimal("848.55")
    )

    page = BillingPage(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=_rates_con_tasas(conn),
    )

    valores = page._tree.item(page._tree.get_children()[0], "values")
    assert valores == ("20,00$", "20,00$")  # no se altero nada
    page.destroy()


def test_confirmar_a_tasa_de_referencia_guarda_esa_tasa_en_la_factura(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )

    _confirmar(dialogo, product_service, conn, customer_service)

    factura = billing_service.get_invoice(billing_service.list_invoices()[0].id)
    assert factura.ref_rate == Decimal(260)  # la de _rates_con_tasas
    assert factura.bs_rate == Decimal("185.40")


def test_confirmar_a_tasa_bcv_no_guarda_tasa_de_referencia(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    dialogo._tasa_var.set("bcv")

    _confirmar(dialogo, product_service, conn, customer_service)

    assert billing_service.list_invoices()[0].ref_rate is None


def _panel_de_totales(widget):
    for hijo in widget.winfo_children():
        if isinstance(hijo, kit.Frame) and str(hijo.cget("style")) == "Card.TFrame":
            return hijo
        encontrado = _panel_de_totales(hijo)
        if encontrado is not None:
            return encontrado
    return None


def test_totales_y_botones_no_se_recortan_si_la_ventana_es_mas_baja(
    root, conn, billing_service, product_service, customer_service, mostrar_con_tamano
):
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=_rates_con_tasas(conn),
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    # Bastante mas baja y angosta que lo que pide el contenido.
    mostrar_con_tamano(root, dialogo, "1300x700")

    panel = _panel_de_totales(dialogo)
    fondo = dialogo.winfo_rooty() + dialogo.winfo_height()
    # el panel tiene TODO el alto que necesita y queda dentro de la ventana
    assert panel.winfo_height() >= panel.winfo_reqheight()
    assert panel.winfo_rooty() + panel.winfo_height() <= fondo
    boton = dialogo._btn_confirmar
    assert boton.winfo_height() >= boton.winfo_reqheight()
    dialogo.destroy()


def test_al_elegir_al_cliente_el_cursor_pasa_al_producto(
    root, conn, billing_service, product_service, customer_service, mostrar_con_tamano
):
    """Sin clic ni boton: elegir al cliente deja el cursor en Producto."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    mostrar_con_tamano(root, dialogo, "1200x700")

    _elegir_cliente(dialogo, customer_service, name="Ferreteria Lopez")
    dialogo.update()

    assert dialogo._cliente_picker.var.get() == "Ferreteria Lopez"
    assert "V-12345678" in dialogo._datos_cliente_var.get()
    # el foco real lo tiene el tk.Entry de adentro del campo
    assert str(dialogo.focus_get()).startswith(str(dialogo._producto_picker.entry))
    dialogo.destroy()


def test_cambiar_cliente_con_la_factura_iniciada_conserva_las_lineas(
    root, conn, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    otro = customer_service.create_customer(
        name="Ferreteria Lopez", tax_id="J-11111111", phone="0412-7654321"
    )
    dialogo._recargar_clientes()  # se creo despues de abrir el dialogo

    dialogo._cliente_picker.elegir(otro)  # elegir otro cambia el de la factura

    assert dialogo._invoice.customer_id == otro.id
    assert dialogo._invoice.customer_name == "Ferreteria Lopez"
    assert dialogo._invoice.customer_tax_id == "J-11111111"
    assert len(dialogo._invoice.lines) == 1
    assert "J-11111111" in dialogo._datos_cliente_var.get()
    dialogo.destroy()


def test_dejar_el_campo_de_cliente_sin_elegir_vuelve_al_de_la_factura(
    root, conn, billing_service, product_service, customer_service
):
    """Con la factura iniciada, empezar a buscar a otro y no elegir a nadie
    no deja la factura sin cliente: el campo vuelve al que tenia."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    antes = dialogo._invoice.customer_id

    dialogo._cliente_picker.var.set("zzz")  # busca y no elige
    assert dialogo._cliente_picker.elegido() is None
    dialogo._restaurar_cliente()  # lo que corre al salir del campo

    assert dialogo._invoice.customer_id == antes
    assert dialogo._cliente_picker.elegido().id == antes
    assert dialogo._cliente_picker.var.get() == "Mostrador"
    dialogo.destroy()


def test_cambiar_a_un_cliente_sin_cedula_que_no_se_completa_no_lo_cambia(
    root, conn, billing_service, product_service, customer_service
):
    """La fixture deja que el dialogo de completar la ficha se cierre sin
    guardar: el borrador sigue con el cliente que tenia."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    antes = dialogo._invoice.customer_id
    sin_cedula = customer_service.create_customer(name="Sin Cédula")
    dialogo._recargar_clientes()

    dialogo._cliente_picker.elegir(sin_cedula)

    assert dialogo._invoice.customer_id == antes
    assert dialogo._invoice.customer_name == "Mostrador"
    assert dialogo._cliente_picker.var.get() == "Mostrador"
    dialogo.destroy()


def _dentro_de_sus_paneles(widget, tope):
    """Cada borde del widget queda adentro de cada contenedor hasta 'tope'."""
    x1 = widget.winfo_rootx() + widget.winfo_width()
    y1 = widget.winfo_rooty() + widget.winfo_height()
    contenedor = widget.master
    while contenedor is not None:
        if y1 > contenedor.winfo_rooty() + contenedor.winfo_height() + 1:
            return False
        if x1 > contenedor.winfo_rootx() + contenedor.winfo_width() + 1:
            return False
        if contenedor is tope:
            return True
        contenedor = contenedor.master
    return True


def test_a_150_por_ciento_la_factura_no_esconde_ningun_control(
    root, conn, billing_service, product_service, customer_service, mostrar_con_tamano
):
    """
    Con la letra al 150 % el formulario pedia 1456 px de alto en una
    pantalla de 1080: Cantidad, Nivel y "Agregar linea" quedaban fuera
    de la ventana y la tabla de lineas en 1 px. Se mide con el tema real
    (fuentes y alto de fila) y el tamano real de la ventana.
    """
    from flexstock.services.settings_service import SettingsService
    from flexstock.ui.theme import ThemeManager

    ThemeManager(root, font_scale=150)
    settings = SettingsService(conn)
    settings.set_advanced_mode(True)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=_rates_con_tasas(conn),
        preview_service=object(),  # solo para que exista "Vista previa"
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    mostrar_con_tamano(root, dialogo, "1800x990")

    cantidad_fila = dialogo._tier_combo.master
    for control in (
        *cantidad_fila.winfo_children(),
        dialogo._btn_confirmar,
        dialogo._btn_vista_previa,
        _panel_de_totales(dialogo),
    ):
        assert _dentro_de_sus_paneles(control, dialogo), control
        assert control.winfo_height() >= control.winfo_reqheight() - 2, control
    fila = int(ttk.Style().lookup("Treeview", "rowheight"))
    assert dialogo._lineas_tree.winfo_height() >= 3 * fila  # encabezado + 2 lineas
    dialogo.destroy()


def test_el_nivel_de_precio_lleva_el_numero_adelante(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    opciones = list(dialogo._tier_combo.cget("values"))

    assert opciones == [
        f"Precio {t.position} - {t.name}" for t in product_service.list_price_tiers()
    ]
    assert opciones[0].startswith("Precio 1 - ")
    dialogo.destroy()


def test_la_lista_de_productos_muestra_el_stock_adelante_y_en_color(
    root, conn, billing_service, product_service, customer_service
):
    from flexstock.ui.theme import LIGHT

    precios = {_tier_id_default(product_service): Decimal("1.00")}
    product_service.create_product(
        name="SIN EXISTENCIA",
        category_id=product_service.default_category_id(),
        prices=precios,
    )
    con_stock = product_service.create_product(
        name="HAY EN DEPOSITO",
        category_id=product_service.default_category_id(),
        prices=precios,
    )
    _dar_stock(conn, con_stock.id, 3000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    lista = dialogo._producto_picker.lista
    renglones = [lista.get(i) for i in range(lista.size())]
    hay = next(i for i, r in enumerate(renglones) if "HAY EN DEPOSITO" in r)
    sin = next(i for i, r in enumerate(renglones) if "SIN EXISTENCIA" in r)

    assert renglones[hay].strip().startswith("3")
    assert lista.itemcget(hay, "foreground") == LIGHT.success_bg
    assert lista.itemcget(sin, "foreground") == LIGHT.muted_fg
    dialogo.destroy()


def test_nuevo_cliente_desde_la_factura_lo_guarda_y_lo_deja_elegido(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    from flexstock.ui import customers_page as customers_page_module

    customer_service.create_customer(name="Cliente viejo")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    def _dialogo_falso(master, service, **kwargs):
        service.create_customer(
            name="Cliente nuevo", tax_id="V-12345678", address="Calle 1"
        )
        ventana = tk.Toplevel(master)
        ventana.guardado = True
        return ventana

    monkeypatch.setattr(customers_page_module, "CustomerDialog", _dialogo_falso)
    monkeypatch.setattr(dialogo, "wait_window", lambda *_a: None)

    dialogo._on_nuevo_cliente()

    assert sorted(c.name for c in customer_service.list_customers()) == [
        "Cliente nuevo",
        "Cliente viejo",
    ]
    # queda elegido y a la vista, sin buscarlo a mano
    nuevo = next(
        c for c in customer_service.list_customers() if c.name == "Cliente nuevo"
    )
    assert dialogo._cliente_id == nuevo.id
    assert dialogo._cliente_picker.var.get() == "Cliente nuevo"
    assert dialogo._invoice is not None  # elegirlo inicio la factura
    dialogo.destroy()


def test_la_lista_de_productos_es_ancha(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert dialogo._producto_picker._ancho_lista >= 600
    dialogo.destroy()


# ------------------------------------------------------------
# Vista previa: el borrador como COTIZACION en PDF, sin guardar nada
# ------------------------------------------------------------
def _dialogo_con_vista_previa(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    from flexstock.services.preview_service import PreviewService
    from flexstock.services.settings_service import SettingsService

    settings = SettingsService(conn)
    settings.set_advanced_mode(True)
    rates = _rates_con_tasas(conn)
    abiertos = []
    monkeypatch.setattr(billing_page_module, "open_in_viewer", abiertos.append)
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        settings_service=settings,
        rate_display_service=rates,
        preview_service=PreviewService(conn, rates),
    )
    return dialogo, abiertos


def test_sin_servicio_de_vista_previa_no_hay_boton(
    root, billing_service, product_service, customer_service
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert not hasattr(dialogo, "_btn_vista_previa")
    dialogo.destroy()


def test_con_servicio_de_vista_previa_aparece_el_boton(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    dialogo, _abiertos = _dialogo_con_vista_previa(
        root, conn, billing_service, product_service, customer_service, monkeypatch
    )

    assert dialogo._btn_vista_previa.cget("text") == "Vista previa (PDF)"
    # Sin lineas no hay nada que mostrar: apagado hasta agregar la primera.
    assert str(dialogo._btn_vista_previa.cget("state")) == "disabled"
    dialogo.destroy()


def test_vista_previa_abre_el_pdf_y_no_guarda_nada(
    root,
    conn,
    billing_service,
    product_service,
    customer_service,
    monkeypatch,
    tmp_path,
):
    from flexstock.config import EXPORT_DIR  # noqa: F401 - solo para mostrar el default

    dialogo, abiertos = _dialogo_con_vista_previa(
        root, conn, billing_service, product_service, customer_service, monkeypatch
    )
    monkeypatch.setattr(
        dialogo._preview,
        "render",
        lambda doc, export_dir=None: tmp_path / "x.pdf",
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    dialogo._on_vista_previa()

    assert abiertos == [tmp_path / "x.pdf"]
    # nada se guardo: ni factura ni movimiento de stock
    assert conn.execute("SELECT count(*) FROM invoices").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM inventory_movements").fetchone()[0] == 0
    dialogo.destroy()


def test_vista_previa_siempre_usa_la_tasa_bcv(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    """La factura ya esta expresada en $ a tasa BCV (con Modo avanzado
    el precio entro alterado), asi que no hay otra tasa que elegir."""
    dialogo, _abiertos = _dialogo_con_vista_previa(
        root, conn, billing_service, product_service, customer_service, monkeypatch
    )
    recibidos = []

    def _falso(invoice, tasa, **kwargs):
        recibidos.append(tasa)
        return "ruta"

    monkeypatch.setattr(dialogo._preview, "invoice_pdf", _falso)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    dialogo._on_vista_previa()

    assert recibidos == ["bcv"]
    dialogo.destroy()


def test_vista_previa_sin_lineas_avisa_y_no_abre_nada(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    dialogo, abiertos = _dialogo_con_vista_previa(
        root, conn, billing_service, product_service, customer_service, monkeypatch
    )
    avisos = []
    monkeypatch.setattr(
        billing_page_module.messagebox, "showinfo", lambda _t, m, **k: avisos.append(m)
    )

    dialogo._on_vista_previa()

    assert abiertos == []
    assert "al menos una línea" in avisos[0]
    dialogo.destroy()


def test_vista_previa_con_tasa_sin_cargar_muestra_el_error(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.preview_service import PreviewService
    from flexstock.services.rate_display_service import RateDisplayService

    rates = RateDisplayService(conn, ExchangeRateService(conn))  # sin tasas
    abiertos, errores = [], []
    monkeypatch.setattr(billing_page_module, "open_in_viewer", abiertos.append)
    monkeypatch.setattr(
        billing_page_module.messagebox,
        "showerror",
        lambda _t, m, **k: errores.append(m),
    )
    dialogo = InvoiceDialog(
        root,
        billing_service,
        product_service,
        customer_service,
        rate_display_service=rates,
        preview_service=PreviewService(conn, rates),
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    dialogo._on_vista_previa()

    assert abiertos == []
    assert "Falta cargar" in errores[0]
    dialogo.destroy()


def test_las_tablas_vacias_dicen_que_hacer_y_el_mensaje_se_va_con_la_primera_linea(
    root, conn, billing_service, product_service, customer_service
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    _dar_stock(conn, pid, 10000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    assert dialogo._vacio_lineas._label.winfo_manager() == "place"  # sin lineas

    _elegir_cliente(dialogo, customer_service)
    _elegir_producto(dialogo, product_service, pid)
    dialogo._cantidad_var.set("1")
    dialogo._on_agregar_linea()

    assert dialogo._vacio_lineas._label.winfo_manager() == ""
    dialogo.destroy()


def test_el_total_va_en_grande_y_confirmar_junto_a_el(
    root, billing_service, product_service, customer_service
):
    from flexstock.ui import kit

    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    def estilos_de(texto):
        etiquetas = []

        def recorrer(w):
            for h in w.winfo_children():
                if isinstance(h, kit.Label) and str(h.cget("text")) == texto:
                    etiquetas.append(h.cget("style"))
                recorrer(h)

        recorrer(dialogo)
        return etiquetas

    assert estilos_de("TOTAL") == ["Total.TLabel"]
    # Confirmar justo debajo del TOTAL, en el mismo panel.
    total = next(
        h
        for h in dialogo._btn_confirmar.master.winfo_children()
        if isinstance(h, kit.Label) and str(h.cget("text")) == "TOTAL"
    )
    assert int(dialogo._btn_confirmar.grid_info()["row"]) > int(
        total.grid_info()["row"]
    )
    dialogo.destroy()


# ------------------------------------------------------------
# La "columna de venta": todo con el teclado, sin ratón
# ------------------------------------------------------------
def _producto_con_stock(product_service, conn, nombre="Tornillo", precio="1.50"):
    pid = product_service.create_product(
        name=nombre,
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal(precio)},
    ).id
    _dar_stock(conn, pid, 100000)
    return pid


def test_producto_enter_cantidad_enter_agrega_y_vuelve_al_producto(
    root, conn, billing_service, product_service, customer_service, mostrar_con_tamano
):
    """El ciclo de una linea son tres teclas: escribir, Enter (elige y salta a
    Cant.), Enter (agrega). El cursor vuelve al producto, vacio."""
    _producto_con_stock(product_service, conn)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    mostrar_con_tamano(root, dialogo, "1200x700")
    _elegir_cliente(dialogo, customer_service)
    dialogo.update()
    picker = dialogo._producto_picker

    picker.var.set("tornil")
    picker._on_return()  # Enter 1: elige y pasa a Cant.

    assert picker.elegido().name == "Tornillo"
    assert str(dialogo.focus_get()).startswith(str(dialogo._cantidad_entry))
    assert dialogo._cantidad_var.get() == "1"  # ya en 1: no hay que escribir

    dialogo._cantidad_entry.event_generate("<Return>")  # Enter 2: agrega
    dialogo.update()

    assert len(dialogo._invoice.lines) == 1
    assert picker.var.get() == ""
    assert picker.elegido() is None
    assert str(dialogo.focus_get()).startswith(str(picker.entry))
    dialogo.destroy()


def test_el_mismo_producto_al_mismo_precio_suma_a_su_linea(
    root, conn, billing_service, product_service, customer_service
):
    pid = _producto_con_stock(product_service, conn)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)

    for cantidad in ("2", "3"):
        _elegir_producto(dialogo, product_service, pid)
        dialogo._cantidad_var.set(cantidad)
        dialogo._on_agregar_linea()

    assert len(dialogo._invoice.lines) == 1
    assert dialogo._invoice.lines[0].quantity_milli == 5000
    assert len(dialogo._lineas_tree.get_children()) == 1
    assert "7,50" in dialogo._subtotal_var.get()  # 1,50 x 5
    dialogo.destroy()


def test_el_mismo_producto_a_otro_nivel_de_precio_va_en_otra_linea(
    root, conn, billing_service, product_service, customer_service
):
    tiers = product_service.list_price_tiers()
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={tiers[0].id: Decimal("1.00"), tiers[2].id: Decimal("2.00")},
    ).id
    _dar_stock(conn, pid, 100000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)

    for nivel in (0, 2):
        _elegir_producto(dialogo, product_service, pid)
        dialogo._tier_combo.current(nivel)
        dialogo._on_agregar_linea()

    assert len(dialogo._invoice.lines) == 2
    assert {l.unit_price_cents for l in dialogo._invoice.lines} == {100, 200}
    dialogo.destroy()


def test_supr_quita_la_linea_elegida(
    root, conn, billing_service, product_service, customer_service
):
    uno = _producto_con_stock(product_service, conn, "Tornillo")
    dos = _producto_con_stock(product_service, conn, "Tuerca")
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    for pid in (uno, dos):
        _elegir_producto(dialogo, product_service, pid)
        dialogo._on_agregar_linea()
    dialogo._lineas_tree.selection_set("0")

    assert dialogo._on_supr_en_lineas() == "break"

    assert [l.product_name for l in dialogo._invoice.lines] == ["Tuerca"]
    assert len(dialogo._lineas_tree.get_children()) == 1
    dialogo.destroy()


def test_supr_sin_linea_elegida_no_hace_nada(
    root, conn, billing_service, product_service, customer_service
):
    pid = _producto_con_stock(product_service, conn)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    _elegir_producto(dialogo, product_service, pid)
    dialogo._on_agregar_linea()

    dialogo._on_supr_en_lineas()

    assert len(dialogo._invoice.lines) == 1
    dialogo.destroy()


def test_mas_y_menos_cambian_la_cantidad_de_la_linea_elegida(
    root, conn, billing_service, product_service, customer_service
):
    pid = _producto_con_stock(product_service, conn)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    _elegir_producto(dialogo, product_service, pid)
    dialogo._on_agregar_linea()
    dialogo._lineas_tree.selection_set("0")

    dialogo._on_cantidad_en_lineas(+1)
    dialogo._on_cantidad_en_lineas(+1)
    assert dialogo._invoice.lines[0].quantity_milli == 3000
    assert dialogo._lineas_tree.selection() == ("0",)  # sigue elegida: se repite

    dialogo._on_cantidad_en_lineas(-1)
    assert dialogo._invoice.lines[0].quantity_milli == 2000
    assert "3,00" in dialogo._subtotal_var.get()  # 1,50 x 2
    dialogo.destroy()


def test_menos_no_baja_de_una_unidad(
    root, conn, billing_service, product_service, customer_service
):
    pid = _producto_con_stock(product_service, conn)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    _elegir_producto(dialogo, product_service, pid)
    dialogo._on_agregar_linea()
    dialogo._lineas_tree.selection_set("0")

    dialogo._on_cantidad_en_lineas(-1)

    assert dialogo._invoice.lines[0].quantity_milli == 1000  # para quitarla es Supr
    dialogo.destroy()


# ------------------------------------------------------------
# Confirmar: F12 (nunca Enter), aviso que se va solo, fecha plegada
# ------------------------------------------------------------
def test_f12_confirma_la_factura(
    root,
    conn,
    billing_service,
    product_service,
    customer_service,
    monkeypatch,
    mostrar_con_tamano,
):
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    mostrar_con_tamano(root, dialogo, "1200x700")  # event_generate pide ventana visible
    llamadas = []
    monkeypatch.setattr(dialogo, "_on_confirmar", lambda: llamadas.append(1))

    assert dialogo.bind("<F12>") != ""
    dialogo.event_generate("<F12>")
    dialogo.update()

    assert llamadas == [1]
    dialogo.destroy()


def test_enter_no_confirma_la_factura(
    root,
    conn,
    billing_service,
    product_service,
    customer_service,
    monkeypatch,
    mostrar_con_tamano,
):
    """Ni en la ventana ni en ningun campo: confirmar es F12 o el boton."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    mostrar_con_tamano(root, dialogo, "1200x700")  # event_generate pide ventana visible
    llamadas = []
    monkeypatch.setattr(dialogo, "_on_confirmar", lambda: llamadas.append(1))
    _elegir_cliente(dialogo, customer_service)

    dialogo._cantidad_entry.event_generate("<Return>")
    dialogo._producto_picker.entry.event_generate("<Return>")
    dialogo.update()

    assert llamadas == []
    assert dialogo.bind("<Return>") == ""
    dialogo.destroy()


def test_confirmar_avisa_con_un_toast_y_no_con_un_cuadro_modal(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    avisos = []
    monkeypatch.setattr(
        billing_page_module.messagebox, "showinfo", lambda _t, m, **k: avisos.append(m)
    )
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    _confirmar(dialogo, product_service, conn, customer_service)

    factura = billing_service.list_invoices()[0]
    assert dialogo.guardado is True
    assert avisos == []  # nada que cerrar con un clic
    aviso = root._flex_toast
    assert aviso.winfo_exists()
    textos = [str(h.cget("text")) for h in aviso.winfo_children()]
    assert any(factura.display_number in t and "confirmada" in t for t in textos)


def test_confirmar_sin_forma_de_pago_avisa_y_no_guarda(
    root, conn, billing_service, product_service, customer_service, monkeypatch
):
    avisos = []
    monkeypatch.setattr(
        billing_page_module.messagebox, "showinfo", lambda _t, m, **k: avisos.append(m)
    )
    errores = _errores_de(monkeypatch)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)

    dialogo._on_confirmar()  # sin elegir la forma de pago

    assert billing_service.list_invoices() == []
    assert any("pago" in m.lower() for m in avisos + errores)
    dialogo.destroy()


def test_la_fecha_y_la_hora_estan_a_la_vista_y_arrancan_en_hoy(
    root, billing_service, product_service, customer_service
):
    """Sin boton que abrir: los dos campos se ven siempre, ya con la fecha de hoy."""
    from datetime import datetime

    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)

    assert dialogo._fecha_var.get() == datetime.now().strftime("%d/%m/%Y")  # noqa: DTZ005
    assert len(dialogo._hora_var.get()) == 5
    assert not hasattr(dialogo, "_on_cambiar_fecha")
    entradas = []

    def recorrer(w):
        if isinstance(w, kit.Entry):
            entradas.append(str(w.cget("textvariable")))
        for h in w.winfo_children():
            recorrer(h)

    recorrer(dialogo)
    assert str(dialogo._fecha_var) in entradas
    assert str(dialogo._hora_var) in entradas
    dialogo.destroy()


def test_confirmar_queda_despues_de_la_forma_de_pago_en_el_recorrido_de_tab(
    root, billing_service, product_service, customer_service
):
    """El orden de creacion es el orden de Tab: se llega a Confirmar habiendo
    pasado por IVA y por la forma de pago, no antes."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    recorrido = []

    def recorrer(w):
        recorrido.append(w)
        for h in w.winfo_children():
            recorrer(h)

    recorrer(dialogo)
    posicion = {id(w): i for i, w in enumerate(recorrido)}

    assert (
        posicion[id(dialogo._forma_pago_combo)] < posicion[id(dialogo._btn_confirmar)]
    )
    assert (
        posicion[id(dialogo._producto_picker)] < posicion[id(dialogo._forma_pago_combo)]
    )
    dialogo.destroy()


def test_tab_de_cantidad_sigue_por_nivel_iva_pago_fecha_hora_y_confirmar(
    root, billing_service, product_service, customer_service
):
    """Todo se cambia de a poco con Tab, flechas y Enter: de Cantidad a Nivel,
    de ahi a IVA, la tasa, la forma de pago, la fecha, la hora y Confirmar.
    La tabla de lineas va al final y "Cerrar" queda fuera (Esc ya cierra)."""
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    dialogo.update()

    recorrido = []
    actual = dialogo._cantidad_entry._entry
    for _ in range(14):
        actual = actual.tk_focusNext()
        recorrido.append(actual)

    def indice(widget):
        return next(
            i for i, w in enumerate(recorrido) if str(w).startswith(str(widget))
        )

    assert indice(dialogo._tier_combo) == 0  # lo primero despues de Cantidad
    assert (
        indice(dialogo._tier_combo)
        < indice(dialogo._forma_pago_combo)
        < indice(dialogo._btn_confirmar)
        < indice(dialogo._lineas_tree)
    )
    assert not any("button" in str(w) and "Cerrar" in str(w) for w in recorrido)
    dialogo.destroy()


def test_flechas_en_nivel_cambian_el_precio_y_enter_pasa_al_siguiente(
    root, conn, billing_service, product_service, customer_service
):
    tiers = product_service.list_price_tiers()
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={tiers[0].id: Decimal("8.00"), tiers[2].id: Decimal("10.00")},
    ).id
    _dar_stock(conn, pid, 100000)
    dialogo = InvoiceDialog(root, billing_service, product_service, customer_service)
    _elegir_cliente(dialogo, customer_service)
    _elegir_producto(dialogo, product_service, pid)
    dialogo._tier_combo.current(0)

    dialogo._tier_combo._flecha(+1)

    assert dialogo._tier_combo.current() == 1
    assert dialogo._tier_combo._entry.bind("<Return>") != ""
    assert dialogo._forma_pago_combo._entry.bind("<Return>") != ""
    dialogo.destroy()


def test_enter_marca_y_desmarca_iva_y_elige_la_tasa(
    root,
    conn,
    billing_service,
    product_service,
    customer_service,
    mostrar_con_tamano,
):
    dialogo = _dialogo_avanzado(
        root, conn, billing_service, product_service, customer_service
    )
    _factura_con_una_linea(dialogo, product_service, conn, customer_service)
    mostrar_con_tamano(root, dialogo, "1400x900")  # event_generate pide ventana visible

    lienzo = dialogo._check_iva._canvas
    lienzo.focus_force()
    lienzo.event_generate("<Return>")
    dialogo.update()
    assert dialogo._cobrar_iva_var.get() is True
    assert dialogo._invoice.tax_rate > 0
    lienzo.event_generate("<Return>")
    dialogo.update()
    assert dialogo._cobrar_iva_var.get() is False

    bcv = next(r for r in dialogo._radios_tasa if r.cget("value") == "bcv")
    bcv._canvas.focus_force()
    bcv._canvas.event_generate("<Return>")
    dialogo.update()
    assert dialogo._tasa_var.get() == "bcv"
    dialogo.destroy()
