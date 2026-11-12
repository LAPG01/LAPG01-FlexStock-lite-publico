"""
Tests de QuotationsPage / QuotationDialog. Widgets reales en ventana
oculta; messagebox/simpledialog y el selector de moneda se parchean
porque son modales.

    pytest -q
"""

import sqlite3
import tkinter as tk
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.quotation_repository import QuotationRepository
from flexstock.domain.enums import QuoteCurrency
from flexstock.services import quotation_service as quotation_service_module
from flexstock.services.customer_service import CustomerService
from flexstock.services.exchange_rate_service import ExchangeRateService
from flexstock.services.product_service import ProductService
from flexstock.services.quotation_service import QuotationService
from flexstock.ui import kit
from flexstock.ui import quotations_page as page_module
from flexstock.ui.quotations_page import (
    QuotationDetailDialog,
    QuotationDialog,
    QuotationsPage,
)


@pytest.fixture
def errores(monkeypatch):
    mensajes = []
    monkeypatch.setattr(
        page_module.messagebox, "showerror", lambda _t, m, **k: mensajes.append(m)
    )
    monkeypatch.setattr(page_module.messagebox, "showinfo", lambda *a, **k: None)
    monkeypatch.setattr(page_module.messagebox, "askyesno", lambda *a, **k: False)
    return mensajes


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
def service(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(quotation_service_module, "EXPORT_DIR", tmp_path)
    return QuotationService(
        conn,
        QuotationRepository(conn),
        ProductRepository(conn),
        CustomerRepository(conn),
        PriceTierRepository(conn),
        ExchangeRateService(conn),
    )


@pytest.fixture
def con_tasas(conn):
    set_setting(conn, "personal_bs_rate", "50")
    set_setting(conn, "bcv_rate", "40.00")


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _producto(product_service, name="Tornillo", precio3="10.00"):
    tier3 = next(t.id for t in product_service.list_price_tiers() if t.position == 3)
    return product_service.create_product(
        name=name,
        category_id=product_service.default_category_id(),
        prices={tier3: Decimal(precio3)},
    )


def _dialogo(root, service, product_service, customer_service):
    return QuotationDialog(root, service, product_service, customer_service)


def _elegir_primer_producto(dialogo):
    dialogo._lista_productos.selection_set(0)
    dialogo._on_elegir_producto()


# ------------------------------------------------------------
# Pagina
# ------------------------------------------------------------
def test_pagina_vacia_muestra_tasas_sin_cargar(
    root, service, product_service, customer_service, errores
):
    page = QuotationsPage(root, service, product_service, customer_service)

    assert page._tree.get_children() == ()
    assert page._tasas_var.get().count("sin cargar") == 2


def test_editar_tasa_propia(
    root, service, product_service, customer_service, errores, monkeypatch
):
    monkeypatch.setattr(page_module.simpledialog, "askstring", lambda *a, **k: "95,5")
    page = QuotationsPage(root, service, product_service, customer_service)

    page._on_editar_tasa_propia()

    assert service.personal_rate() == Decimal("95.50")
    assert "95,50" in page._tasas_var.get()


def test_editar_tasa_propia_invalida_muestra_error(
    root, service, product_service, customer_service, errores, monkeypatch
):
    monkeypatch.setattr(page_module.simpledialog, "askstring", lambda *a, **k: "abc")
    page = QuotationsPage(root, service, product_service, customer_service)

    page._on_editar_tasa_propia()

    assert service.personal_rate() is None
    assert errores


# ------------------------------------------------------------
# Dialogo
# ------------------------------------------------------------
def test_iniciar_sin_tasas_muestra_error(
    root, service, product_service, customer_service, errores
):
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._nombre_directo_var.set("Mostrador")

    dialogo._on_iniciar()

    assert dialogo._quotation is None
    assert "tasa propia" in errores[0]
    dialogo.destroy()


def test_elegir_producto_muestra_precio_3_en_tres_monedas(
    root, service, product_service, customer_service, con_tasas, errores
):
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)

    _elegir_primer_producto(dialogo)

    assert dialogo._preview_var.get() == (
        "Precio 3:  10,00$   |   500,00 Bs   |   12,50$ BCV"
    )
    dialogo.destroy()


def test_flujo_completo_crea_la_cotizacion(
    root, conn, service, product_service, customer_service, con_tasas, errores
):
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._nombre_directo_var.set("Mostrador")
    dialogo._on_iniciar()
    _elegir_primer_producto(dialogo)
    dialogo._cantidad_var.set("2")

    dialogo._on_agregar_linea()

    fila = dialogo._lineas_tree.item("0", "values")
    assert fila[3:9] == (
        "10,00$",
        "500,00 Bs",
        "12,50$",
        "20,00$",
        "1.000,00 Bs",
        "25,00$",
    )
    # panel de totales: una columna por moneda, tres filas. El IVA
    # arranca DESMARCADO, asi que de entrada no suma nada.
    assert dialogo._subtotal_vars[QuoteCurrency.BS].get() == "1.000,00 Bs"
    assert dialogo._iva_vars[QuoteCurrency.BS].get() == "0,00 Bs"
    assert dialogo._totales_vars[QuoteCurrency.BS].get() == "1.000,00 Bs"

    dialogo._cobrar_iva_var.set(True)
    dialogo._on_toggle_iva()
    assert dialogo._iva_vars[QuoteCurrency.BS].get() == "160,00 Bs"
    assert dialogo._totales_vars[QuoteCurrency.BS].get() == "1.160,00 Bs"

    dialogo._cobrar_iva_var.set(False)
    dialogo._on_toggle_iva()
    assert dialogo._totales_vars[QuoteCurrency.BS].get() == "1.000,00 Bs"

    dialogo._on_crear()

    assert dialogo.guardado is True
    assert not errores
    guardada = service.list_quotations()[0]
    assert guardada.display_number == "NE-000001"
    assert guardada.total_in(QuoteCurrency.USD) == 2000


def test_crear_y_generar_pdf_pregunta_la_moneda(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(page_module.messagebox, "askyesno", lambda *a, **k: True)
    monkeypatch.setattr(page_module, "pedir_moneda", lambda *_a: QuoteCurrency.BS)
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._nombre_directo_var.set("Mostrador")
    dialogo._on_iniciar()
    _elegir_primer_producto(dialogo)
    dialogo._on_agregar_linea()

    dialogo._on_crear()

    assert (tmp_path / "NE-000001-Bs.pdf").exists()


def test_agregar_sin_producto_elegido_no_agrega(
    root, service, product_service, customer_service, con_tasas, errores
):
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._nombre_directo_var.set("Mostrador")
    dialogo._on_iniciar()

    dialogo._on_agregar_linea()

    assert dialogo._quotation.lines == []
    dialogo.destroy()


# ------------------------------------------------------------
# Pagina: acciones sobre una cotizacion ya creada
# ------------------------------------------------------------
def _crear_cotizacion(service, product_service):
    pid = _producto(product_service).id
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))
    return service.confirmar(q)


def test_listado_muestra_totales_en_tres_monedas(
    root, service, product_service, customer_service, con_tasas, errores
):
    q = _crear_cotizacion(service, product_service)
    page = QuotationsPage(root, service, product_service, customer_service)

    valores = page._tree.item(str(q.id), "values")

    assert valores[4:] == ("11,60$", "580,00 Bs", "14,50$")


def test_generar_pdf_desde_la_pagina(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(page_module, "pedir_moneda", lambda *_a: QuoteCurrency.USD_BCV)
    q = _crear_cotizacion(service, product_service)
    page = QuotationsPage(root, service, product_service, customer_service)
    page._tree.selection_set(str(q.id))

    page._on_generar_pdf()

    assert (tmp_path / "NE-000001-BCV.pdf").exists()


def test_generar_pdf_cancelando_la_moneda_no_genera(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(page_module, "pedir_moneda", lambda *_a: None)
    q = _crear_cotizacion(service, product_service)
    page = QuotationsPage(root, service, product_service, customer_service)
    page._tree.selection_set(str(q.id))

    page._on_generar_pdf()

    assert list(tmp_path.iterdir()) == []


def test_anular_desde_la_pagina(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    monkeypatch,
):
    monkeypatch.setattr(
        page_module.simpledialog, "askstring", lambda *a, **k: "No vino"
    )
    q = _crear_cotizacion(service, product_service)
    page = QuotationsPage(root, service, product_service, customer_service)
    page._tree.selection_set(str(q.id))

    page._on_anular()

    assert service.get_quotation(q.id).is_cancelled
    assert page._tree.item(str(q.id), "values")[3] == "Anulada"


def test_detalle_no_falla(root, service, product_service, customer_service, con_tasas):
    q = _crear_cotizacion(service, product_service)

    dialogo = QuotationDetailDialog(root, service.get_quotation(q.id))

    assert dialogo.title() == "Cotización NE-000001"
    dialogo.destroy()


def test_detalle_sin_servicio_no_tiene_boton_de_pdf(
    root, service, product_service, customer_service, con_tasas, errores
):
    q = _crear_cotizacion(service, product_service)

    dialogo = QuotationDetailDialog(root, service.get_quotation(q.id))

    assert not hasattr(dialogo, "_on_generar_pdf") or all(
        b.cget("text") != "Generar PDF" for b in _botones(dialogo)
    )
    dialogo.destroy()


def test_detalle_con_servicio_tiene_boton_de_pdf(
    root, service, product_service, customer_service, con_tasas, errores
):
    q = _crear_cotizacion(service, product_service)

    dialogo = QuotationDetailDialog(root, service.get_quotation(q.id), service)

    assert any(b.cget("text") == "Generar PDF" for b in _botones(dialogo))
    dialogo.destroy()


def test_generar_pdf_desde_el_detalle(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setattr(page_module, "pedir_moneda", lambda *_a: QuoteCurrency.USD)
    q = _crear_cotizacion(service, product_service)
    dialogo = QuotationDetailDialog(root, service.get_quotation(q.id), service)

    dialogo._on_generar_pdf()

    assert (tmp_path / "NE-000001-USD.pdf").exists()
    dialogo.destroy()


def _botones(widget):
    for hijo in widget.winfo_children():
        if isinstance(hijo, kit.Button):
            yield hijo
        yield from _botones(hijo)


def _panel_de_totales(widget):
    for hijo in widget.winfo_children():
        if isinstance(hijo, kit.Frame) and str(hijo.cget("style")) == "Card.TFrame":
            return hijo
        encontrado = _panel_de_totales(hijo)
        if encontrado is not None:
            return encontrado
    return None


def test_totales_y_botones_no_se_recortan_si_la_ventana_es_mas_baja(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    mostrar_con_tamano,
):
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._nombre_directo_var.set("Mostrador")
    dialogo._on_iniciar()
    _elegir_primer_producto(dialogo)
    dialogo._on_agregar_linea()

    # bastante mas baja y angosta que lo que pide el contenido
    mostrar_con_tamano(root, dialogo, "1300x700")

    panel = _panel_de_totales(dialogo)
    fondo = dialogo.winfo_rooty() + dialogo.winfo_height()
    # el panel tiene TODO el alto que necesita y queda dentro de la ventana
    assert panel.winfo_height() >= panel.winfo_reqheight()
    assert panel.winfo_rooty() + panel.winfo_height() <= fondo
    assert dialogo._btn_crear.winfo_height() >= dialogo._btn_crear.winfo_reqheight()
    dialogo.destroy()


def test_a_150_por_ciento_la_cotizacion_no_esconde_ningun_control(
    root,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    mostrar_con_tamano,
):
    """Mismo criterio que la factura: con el tema real al 150 %,
    "Agregar linea" quedaba cortado por el borde de su panel."""
    from flexstock.ui.theme import ThemeManager

    ThemeManager(root, font_scale=150)
    _producto(product_service)
    dialogo = QuotationDialog(
        root, service, product_service, customer_service, preview_service=object()
    )
    dialogo._nombre_directo_var.set("Mostrador")
    dialogo._on_iniciar()
    _elegir_primer_producto(dialogo)
    dialogo._on_agregar_linea()
    mostrar_con_tamano(root, dialogo, "1800x950")

    for control in (
        dialogo._btn_agregar,
        dialogo._btn_crear,
        dialogo._btn_vista_previa,
        _panel_de_totales(dialogo),
    ):
        x1 = control.winfo_rootx() + control.winfo_width()
        y1 = control.winfo_rooty() + control.winfo_height()
        contenedor = control.master
        while contenedor is not dialogo.master:
            assert y1 <= contenedor.winfo_rooty() + contenedor.winfo_height() + 1, (
                control
            )
            assert x1 <= contenedor.winfo_rootx() + contenedor.winfo_width() + 1, (
                control
            )
            contenedor = contenedor.master
    dialogo.destroy()


def test_la_lista_de_productos_es_ancha(
    root, service, product_service, customer_service
):
    dialogo = _dialogo(root, service, product_service, customer_service)

    assert int(dialogo._lista_productos.cget("width")) >= 56
    dialogo.destroy()


# ------------------------------------------------------------
# Las notas de entrega son LIBRES: sin cedula/RIF ni direccion (a
# diferencia de una factura fiscal), para poder vender rapido
# ------------------------------------------------------------
def test_nota_de_entrega_solo_con_nombre_se_crea(
    root, conn, service, product_service, customer_service, con_tasas, errores
):
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._nombre_directo_var.set("Mostrador")  # sin cedula ni direccion

    dialogo._on_iniciar()
    _elegir_primer_producto(dialogo)
    dialogo._on_agregar_linea()
    dialogo._on_crear()

    assert not errores
    assert dialogo.guardado is True
    creada = service.list_quotations()[0]
    assert creada.customer_name == "Mostrador"
    assert creada.customer_tax_id is None
    assert creada.customer_address is None


def test_nota_de_entrega_con_cliente_registrado_sin_datos_se_crea(
    root, service, product_service, customer_service, con_tasas, errores
):
    customer_service.create_customer(name="Juan Perez")  # sin cedula ni direccion
    _producto(product_service)
    dialogo = _dialogo(root, service, product_service, customer_service)
    dialogo._modo_cliente.set("registrado")
    dialogo._cliente_combo.current(0)

    dialogo._on_iniciar()

    assert not errores
    assert dialogo._quotation is not None
    assert dialogo._quotation.customer_name == "Juan Perez"
    dialogo.destroy()


# ------------------------------------------------------------
# Vista previa: el borrador como NOTA DE ENTREGA en PDF, sin crearla
# ------------------------------------------------------------
def _dialogo_con_vista_previa(
    root, conn, service, product_service, customer_service, monkeypatch
):
    from flexstock.services.preview_service import PreviewService

    abiertos = []
    monkeypatch.setattr(page_module, "open_in_viewer", abiertos.append)
    dialogo = QuotationDialog(
        root,
        service,
        product_service,
        customer_service,
        preview_service=PreviewService(conn),
    )
    return dialogo, abiertos


def test_sin_servicio_de_vista_previa_no_hay_boton_en_cotizaciones(
    root, service, product_service, customer_service
):
    dialogo = _dialogo(root, service, product_service, customer_service)

    assert not hasattr(dialogo, "_btn_vista_previa")
    dialogo.destroy()


def test_vista_previa_de_cotizacion_abre_el_pdf_y_no_crea_nada(
    root,
    conn,
    service,
    product_service,
    customer_service,
    con_tasas,
    errores,
    monkeypatch,
    tmp_path,
):
    _producto(product_service)
    dialogo, abiertos = _dialogo_con_vista_previa(
        root, conn, service, product_service, customer_service, monkeypatch
    )
    monkeypatch.setattr(
        dialogo._preview, "render", lambda doc, export_dir=None: tmp_path / "n.pdf"
    )
    dialogo._nombre_directo_var.set("Mostrador")
    dialogo._on_iniciar()
    _elegir_primer_producto(dialogo)
    dialogo._on_agregar_linea()

    dialogo._on_vista_previa()

    assert abiertos == [tmp_path / "n.pdf"]
    assert conn.execute("SELECT count(*) FROM quotations").fetchone()[0] == 0
    dialogo.destroy()


def test_vista_previa_de_cotizacion_sin_lineas_avisa(
    root, conn, service, product_service, customer_service, monkeypatch
):
    dialogo, abiertos = _dialogo_con_vista_previa(
        root, conn, service, product_service, customer_service, monkeypatch
    )
    avisos = []
    monkeypatch.setattr(
        page_module.messagebox, "showinfo", lambda _t, m, **k: avisos.append(m)
    )

    dialogo._on_vista_previa()

    assert abiertos == []
    assert "al menos una línea" in avisos[0]
    dialogo.destroy()


def test_la_ventana_de_moneda_muestra_el_total_y_de_donde_sale(
    root, service, product_service, con_tasas
):
    from flexstock.ui import kit
    from flexstock.ui.quotations_page import CurrencyChoiceDialog

    quotation = service.crear_borrador(customer_name="Ana")
    ventana = CurrencyChoiceDialog(root, quotation)

    textos = []

    def recorrer(w):
        for h in w.winfo_children():
            if isinstance(h, kit.Label):
                textos.append(str(h.cget("text")))
            recorrer(h)

    recorrer(ventana)
    assert any("tasa propia (50,00)" in t for t in textos)
    assert any("BCV (40,00)" in t for t in textos)
    # La primera vez viene marcada "Dolares a tasa BCV".
    assert ventana._moneda_var.get() == QuoteCurrency.USD_BCV.value
    ventana._moneda_var.set(QuoteCurrency.BS.value)
    ventana._on_generar()
    assert ventana.resultado is QuoteCurrency.BS
    # La proxima vez arranca con la que se eligio.
    otra = CurrencyChoiceDialog(root, quotation)
    assert otra._moneda_var.get() == QuoteCurrency.BS.value
    otra.destroy()
