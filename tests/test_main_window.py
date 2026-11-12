"""
Tests de MainWindow: que arme las 5 paginas de la barra lateral y que
"Respaldar ahora" llame a BackupService. No es un marco embebible como
el resto de las paginas: es la ventana raiz (ctk.CTk) en si, asi que cada test crea
la suya y la destruye al terminar, en vez de usar un fixture 'root'
compartido.

    pytest -q
"""

import sqlite3
from datetime import datetime, timedelta
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
from flexstock.data.repositories.quotation_repository import QuotationRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.backup_service import BackupService
from flexstock.services.billing_service import BillingService
from flexstock.services.customer_service import CustomerService
from flexstock.services.exchange_rate_service import ExchangeRateService
from flexstock.services.export_service import ExportService
from flexstock.services.product_service import ProductService
from flexstock.services.quotation_service import QuotationService
from flexstock.services.rate_display_service import RateDisplayService
from flexstock.services.settings_service import SettingsService
from flexstock.services.stock_service import StockService
from flexstock.services.supplier_service import SupplierService
from flexstock.ui import main_window as main_window_module
from flexstock.ui.main_window import MainWindow
from flexstock.ui.theme import pair


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: None
    )
    monkeypatch.setattr(main_window_module.messagebox, "showinfo", lambda *a, **k: None)


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture(autouse=True)
def _sin_llamadas_de_red_por_defecto(conn):
    """
    should_refresh() da False si la cache es de hoy: sembrarla evita
    que CADA test que construye una MainWindow dispare un hilo con una
    llamada de red real. Hace falta sembrar dolar, euro Y USDT: son
    cachés independientes (ver should_refresh(tipo)). Los tests que si
    quieren probar el refresh mockean fetch_rate_now() y companeros y
    limpian la fila correspondiente antes.
    """
    from flexstock.data.connection import set_setting

    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    set_setting(conn, "bcv_rate_updated_at", ahora)
    set_setting(conn, "bcv_rate_eur_updated_at", ahora)
    set_setting(conn, "bcv_rate_usdt_updated_at", ahora)


def _quotation_service(conn):
    return QuotationService(
        conn,
        QuotationRepository(conn),
        ProductRepository(conn),
        CustomerRepository(conn),
        PriceTierRepository(conn),
        ExchangeRateService(conn),
    )


def _ventana(conn):
    product_repo = ProductRepository(conn)
    category_repo = CategoryRepository(conn)
    tier_repo = PriceTierRepository(conn)
    customer_repo = CustomerRepository(conn)
    customer_service = CustomerService(customer_repo)
    supplier_repo = SupplierRepository(conn)
    invoice_repo = InvoiceRepository(conn)
    movement_repo = InventoryMovementRepository(conn)

    stock_service = StockService(conn, product_repo, movement_repo)
    product_service = ProductService(product_repo, category_repo, tier_repo)
    supplier_service = SupplierService(supplier_repo)
    billing_service = BillingService(
        conn, invoice_repo, product_repo, customer_repo, tier_repo, stock_service
    )

    ventana = MainWindow(
        product_service=product_service,
        customer_service=customer_service,
        supplier_service=supplier_service,
        billing_service=billing_service,
        stock_service=stock_service,
        backup_service=BackupService(conn),
        export_service=ExportService(
            product_service, supplier_service, billing_service, customer_service
        ),
        exchange_rate_service=ExchangeRateService(conn),
        settings_service=SettingsService(conn),
        quotation_service=_quotation_service(conn),
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    ventana.withdraw()
    return ventana


@pytest.fixture
def window(conn):
    ventana = _ventana(conn)
    yield ventana
    ventana.destroy()


def test_arma_la_barra_lateral_agrupada_con_configuracion_al_final(window):
    etiquetas = window._titulos_visibles()

    assert etiquetas == [
        "Inicio",
        "Facturas",
        "Clientes",
        "Productos",
        "Ajuste de stock",
        "Proveedores",
        "Configuración",
    ]


def test_reparte_el_simbolo_de_moneda_a_productspage(conn):
    from flexstock.data.connection import set_setting

    set_setting(conn, "currency_symbol", "Bs")
    product_repo = ProductRepository(conn)
    category_repo = CategoryRepository(conn)
    tier_repo = PriceTierRepository(conn)
    customer_repo = CustomerRepository(conn)
    customer_service = CustomerService(customer_repo)
    supplier_repo = SupplierRepository(conn)
    invoice_repo = InvoiceRepository(conn)
    movement_repo = InventoryMovementRepository(conn)
    stock_service = StockService(conn, product_repo, movement_repo)
    product_service = ProductService(product_repo, category_repo, tier_repo)
    supplier_service = SupplierService(supplier_repo)
    billing_service = BillingService(
        conn, invoice_repo, product_repo, customer_repo, tier_repo, stock_service
    )

    ventana = MainWindow(
        product_service=product_service,
        customer_service=customer_service,
        supplier_service=supplier_service,
        billing_service=billing_service,
        stock_service=stock_service,
        backup_service=BackupService(conn),
        export_service=ExportService(
            product_service, supplier_service, billing_service, customer_service
        ),
        exchange_rate_service=ExchangeRateService(conn),
        settings_service=SettingsService(conn),
        quotation_service=_quotation_service(conn),
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    ventana.withdraw()

    productos_page = ventana._pagina("Productos")

    assert productos_page._currency_symbol == "Bs"
    ventana.destroy()


def test_respaldar_ahora_llama_a_backup_service(window, monkeypatch, tmp_path):
    from flexstock.data import connection as connection_module

    monkeypatch.setattr(connection_module, "BACKUP_DIR", tmp_path)

    window._on_respaldar()

    assert len(list(tmp_path.glob("flexstock_*.db"))) == 1


def test_respaldar_ahora_con_error_no_falla(window, monkeypatch):
    def _falla(*_a, **_k):
        raise OSError("disco lleno")

    monkeypatch.setattr(window._backup_service, "backup_now", _falla)

    window._on_respaldar()  # no debe propagar la excepcion


def test_configuracion_es_una_pagina_con_los_servicios_y_avisos_de_la_ventana(
    window,
):
    pagina = window._config_page

    assert window._pagina("Configuración") is pagina
    assert pagina._backup_service is window._backup_service
    assert pagina._export_service is window._export_service
    assert pagina._settings_service is window._settings_service
    assert pagina._on_advanced_mode_changed == window._aplicar_modo_avanzado
    assert pagina._on_font_scale_changed == window._aplicar_tamano_letra
    assert pagina._on_theme_changed == window._aplicar_tema


def test_abrir_configuracion_en_una_seccion(window):
    window._abrir_configuracion("Respaldo y exportación")

    assert window._pagina_actual is window._config_page
    titulos = window._config_page.section_titles()
    assert window._config_page._seccion_actual == titulos.index(
        "Respaldo y exportación"
    )


# ------------------------------------------------------------
# Cotizaciones: pestana visible solo con Modo avanzado
# ------------------------------------------------------------
def _etiquetas(ventana):
    return ventana._titulos_visibles()


def test_modo_avanzado_activo_muestra_cotizaciones_al_lado_de_facturas(window):
    window._actualizar_pestana_cotizaciones(True)

    assert _etiquetas(window) == [
        "Inicio",
        "Facturas",
        "Cotizaciones",
        "Clientes",
        "Productos",
        "Ajuste de stock",
        "Proveedores",
        "Configuración",
    ]


def test_mostrar_cotizaciones_dos_veces_no_la_duplica(window):
    window._actualizar_pestana_cotizaciones(True)
    window._actualizar_pestana_cotizaciones(True)

    assert _etiquetas(window).count("Cotizaciones") == 1


def test_apagar_modo_avanzado_oculta_cotizaciones_y_vuelve_a_facturas(window):
    window._actualizar_pestana_cotizaciones(True)
    window._mostrar_pagina(window._quotations_page)

    window._actualizar_pestana_cotizaciones(False)

    assert "Cotizaciones" not in _etiquetas(window)
    assert window._pagina_actual is window._billing_page


def test_arranca_con_cotizaciones_si_modo_avanzado_ya_estaba_activo(conn):
    SettingsService(conn).set_advanced_mode(True)
    product_repo = ProductRepository(conn)
    tier_repo = PriceTierRepository(conn)
    customer_repo = CustomerRepository(conn)
    customer_service = CustomerService(customer_repo)
    movement_repo = InventoryMovementRepository(conn)
    stock_service = StockService(conn, product_repo, movement_repo)
    product_service = ProductService(product_repo, CategoryRepository(conn), tier_repo)
    supplier_service = SupplierService(SupplierRepository(conn))
    billing_service = BillingService(
        conn,
        InvoiceRepository(conn),
        product_repo,
        customer_repo,
        tier_repo,
        stock_service,
    )

    ventana = MainWindow(
        product_service=product_service,
        customer_service=customer_service,
        supplier_service=supplier_service,
        billing_service=billing_service,
        stock_service=stock_service,
        backup_service=BackupService(conn),
        export_service=ExportService(
            product_service, supplier_service, billing_service, customer_service
        ),
        exchange_rate_service=ExchangeRateService(conn),
        settings_service=SettingsService(conn),
        quotation_service=_quotation_service(conn),
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    ventana.withdraw()

    assert "Cotizaciones" in _etiquetas(ventana)
    ventana.destroy()


# ------------------------------------------------------------
# tasas de cambio: Tasa BCV, Tasa Euro (una sola fila, un solo boton
# de refresco - ver _build_encabezado)
# ------------------------------------------------------------
def test_muestra_las_tasas_cacheadas_sin_tocar_la_red(window):
    assert "no disponible" in window._tasa_bcv_var.get()
    assert "no disponible" in window._tasa_bcv_eur_var.get()


def _tarjetas(ventana):
    from flexstock.ui.widgets import RateCard

    def recorrer(widget):
        for hijo in widget.winfo_children():
            if isinstance(hijo, RateCard):
                yield hijo
            yield from recorrer(hijo)

    return list(recorrer(ventana))


def _titulos_visibles(ventana):
    return sorted(
        t.title_label.cget("text")
        for t in _tarjetas(ventana)
        if t.winfo_manager()  # "" = creada pero sin empacar (oculta)
    )


def test_encabezado_tiene_una_tarjeta_por_valor_en_orden(window):
    # Sin Modo avanzado: solo las 4 de siempre, sin "Tasa propia".
    assert _titulos_visibles(window) == ["BCV", "EURO", "IVA", "USDT"]


# ------------------------------------------------------------
# tarjeta "Tasa propia" (Modo avanzado)
# ------------------------------------------------------------
def test_modo_avanzado_muestra_la_tarjeta_de_tasa_propia(window):
    window._aplicar_modo_avanzado(True)

    assert "TASA PROPIA" in _titulos_visibles(window)


def test_apagar_modo_avanzado_oculta_la_tarjeta_de_tasa_propia(window):
    window._aplicar_modo_avanzado(True)
    window._aplicar_modo_avanzado(False)

    assert "TASA PROPIA" not in _titulos_visibles(window)


def test_tarjeta_de_tasa_propia_muestra_la_misma_tasa_que_cotizaciones(conn):
    from flexstock.data.connection import set_setting

    SettingsService(conn).set_advanced_mode(True)
    set_setting(conn, "personal_bs_rate", "1200.00")
    ventana = _ventana(conn)

    assert ventana._tasa_propia_var.get() == "1200,00"
    assert ventana._quotations_page._service.personal_rate() == Decimal("1200.00")
    ventana.destroy()


def test_editar_tasa_propia_desde_la_tarjeta_la_cambia_para_cotizaciones(
    window, monkeypatch
):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "1350,5"
    )

    window._on_editar_tasa_propia()

    assert window._tasa_propia_var.get() == "1350,50"
    assert window._quotations_page._service.personal_rate() == Decimal("1350.50")


def test_editar_tasa_propia_invalida_no_la_cambia(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "abc"
    )

    window._on_editar_tasa_propia()

    assert window._tasa_propia_var.get() == "sin cargar"


def test_modo_avanzado_rearma_las_columnas_de_productos(window, conn):
    from flexstock.ui import columns

    SettingsService(conn).set_visible_columns(columns.PRODUCTS, ("name", "price3_bs"))
    productos = window._paginas_por_modulo[columns.PRODUCTS]

    SettingsService(conn).set_advanced_mode(True)
    window._aplicar_modo_avanzado(True)
    assert "price3_bs" in productos._tree.cget("columns")

    SettingsService(conn).set_advanced_mode(False)
    window._aplicar_modo_avanzado(False)
    assert "price3_bs" not in productos._tree.cget("columns")


def test_tarjeta_separa_valor_y_fecha(conn):
    from flexstock.data.connection import set_setting

    set_setting(conn, "bcv_rate", "848.55")
    set_setting(conn, "bcv_rate_published_at", "2026-09-18 00:00:00")
    ventana = _ventana(conn)

    assert ventana._tasa_bcv_var.get() == "848,55"
    assert ventana._fecha_bcv_var.get() == "18/09"
    ventana.destroy()


def test_click_en_tarjeta_abre_su_edicion(window, monkeypatch):
    llamadas = []
    monkeypatch.setattr(
        main_window_module.simpledialog,
        "askstring",
        lambda titulo, *a, **k: llamadas.append(titulo),
    )
    tarjeta_iva = next(
        t for t in _tarjetas(window) if t.title_label.cget("text") == "IVA"
    )

    tarjeta_iva._click()

    assert llamadas == ["IVA"]


def test_iva_se_muestra_como_porcentaje(window):
    assert window._iva_var.get().endswith("%")


def test_no_dispara_hilo_si_ninguna_cache_vencio(window, monkeypatch):
    """La fixture autouse ya sembro ambas caches (USD y EUR) de hoy."""
    llamado = []
    monkeypatch.setattr(
        main_window_module.threading, "Thread", lambda *a, **k: llamado.append(1)
    )

    window._actualizar_tasas_si_hace_falta()

    assert llamado == []


def test_dispara_hilo_si_solo_la_cache_eur_vencio(window, monkeypatch):
    """Alcanza con que UNA de las dos (usd_oficial o eur_oficial) haya
    vencido para que se dispare el refresco de las 3 tasas juntas."""
    from flexstock.data.connection import set_setting

    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    set_setting(window._exchange_rate_service._conn, "bcv_rate_eur_updated_at", ayer)

    llamado = []

    class _HiloQueNuncaArranca:
        def __init__(self, target, daemon=True):
            llamado.append(1)

        def start(self):
            pass  # a proposito no corre el target: solo interesa que se creo el hilo

    monkeypatch.setattr(main_window_module.threading, "Thread", _HiloQueNuncaArranca)

    window._actualizar_tasas_si_hace_falta()

    assert llamado == [1]


def test_refresca_en_segundo_plano_si_la_cache_vencio(conn, monkeypatch):
    """
    Corre el 'hilo' en el mismo hilo de test (monkeypatch de Thread) y
    procesa manualmente el after(0, ...) con update(): asi el test
    queda determinista, sin condiciones de carrera reales de threading
    ni acceso a la red.
    """
    from flexstock.data.connection import set_setting

    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    set_setting(conn, "bcv_rate_updated_at", ayer)
    set_setting(conn, "bcv_rate_eur_updated_at", ayer)
    monkeypatch.setattr(main_window_module, "fetch_rate_now", lambda: ("47.10", None))
    monkeypatch.setattr(
        main_window_module, "fetch_eur_oficial_now", lambda: ("963.21", None)
    )
    monkeypatch.setattr(main_window_module, "fetch_usdt_now", lambda: ("94.35", None))

    class _HiloSincrono:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main_window_module.threading, "Thread", _HiloSincrono)

    product_repo = ProductRepository(conn)
    category_repo = CategoryRepository(conn)
    tier_repo = PriceTierRepository(conn)
    customer_repo = CustomerRepository(conn)
    customer_service = CustomerService(customer_repo)
    supplier_repo = SupplierRepository(conn)
    invoice_repo = InvoiceRepository(conn)
    movement_repo = InventoryMovementRepository(conn)
    stock_service = StockService(conn, product_repo, movement_repo)
    product_service = ProductService(product_repo, category_repo, tier_repo)
    supplier_service = SupplierService(supplier_repo)
    billing_service = BillingService(
        conn, invoice_repo, product_repo, customer_repo, tier_repo, stock_service
    )

    ventana = MainWindow(
        product_service=product_service,
        customer_service=customer_service,
        supplier_service=supplier_service,
        billing_service=billing_service,
        stock_service=stock_service,
        backup_service=BackupService(conn),
        export_service=ExportService(
            product_service, supplier_service, billing_service, customer_service
        ),
        exchange_rate_service=ExchangeRateService(conn),
        settings_service=SettingsService(conn),
        quotation_service=_quotation_service(conn),
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    ventana.withdraw()
    ventana.update()  # procesa el after(0, ...) encolado por el "hilo" sincrono

    assert "47,10" in ventana._tasa_bcv_var.get()
    assert "963,21" in ventana._tasa_bcv_eur_var.get()
    assert "94,35" in ventana._tasa_usdt_var.get()
    ventana.destroy()


def test_refresh_con_fallo_de_red_deja_los_labels_como_estaban(conn, monkeypatch):
    from flexstock.data.connection import set_setting

    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    set_setting(conn, "bcv_rate", "40.00")
    set_setting(conn, "bcv_rate_updated_at", ayer)
    set_setting(conn, "bcv_rate_eur", "900.00")
    set_setting(conn, "bcv_rate_eur_updated_at", ayer)
    monkeypatch.setattr(main_window_module, "fetch_rate_now", lambda: None)
    monkeypatch.setattr(main_window_module, "fetch_eur_oficial_now", lambda: None)
    monkeypatch.setattr(main_window_module, "fetch_usdt_now", lambda: None)

    class _HiloSincrono:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main_window_module.threading, "Thread", _HiloSincrono)

    product_repo = ProductRepository(conn)
    category_repo = CategoryRepository(conn)
    tier_repo = PriceTierRepository(conn)
    customer_repo = CustomerRepository(conn)
    customer_service = CustomerService(customer_repo)
    supplier_repo = SupplierRepository(conn)
    invoice_repo = InvoiceRepository(conn)
    movement_repo = InventoryMovementRepository(conn)
    stock_service = StockService(conn, product_repo, movement_repo)
    product_service = ProductService(product_repo, category_repo, tier_repo)
    supplier_service = SupplierService(supplier_repo)
    billing_service = BillingService(
        conn, invoice_repo, product_repo, customer_repo, tier_repo, stock_service
    )

    ventana = MainWindow(
        product_service=product_service,
        customer_service=customer_service,
        supplier_service=supplier_service,
        billing_service=billing_service,
        stock_service=stock_service,
        backup_service=BackupService(conn),
        export_service=ExportService(
            product_service, supplier_service, billing_service, customer_service
        ),
        exchange_rate_service=ExchangeRateService(conn),
        settings_service=SettingsService(conn),
        quotation_service=_quotation_service(conn),
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    ventana.withdraw()
    ventana.update()

    assert "40,00" in ventana._tasa_bcv_var.get()  # se quedo con la tasa vieja
    assert "900,00" in ventana._tasa_bcv_eur_var.get()
    ventana.destroy()


# ------------------------------------------------------------
# tasas de cambio: boton de refresco manual (la ruedita, unico para las 3)
# ------------------------------------------------------------
def test_refrescar_tasas_pega_a_la_red_aunque_la_cache_sea_de_hoy(window, monkeypatch):
    """La fixture autouse siembra la cache de hoy: should_refresh() da
    False para las 3, pero el boton de refresco manual consulta igual."""
    monkeypatch.setattr(main_window_module, "fetch_rate_now", lambda: ("47.10", None))
    monkeypatch.setattr(
        main_window_module, "fetch_eur_oficial_now", lambda: ("963.21", None)
    )
    monkeypatch.setattr(main_window_module, "fetch_usdt_now", lambda: ("94.35", None))

    class _HiloSincrono:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main_window_module.threading, "Thread", _HiloSincrono)

    window._on_refrescar_tasas()
    window.update()

    assert "47,10" in window._tasa_bcv_var.get()
    assert "963,21" in window._tasa_bcv_eur_var.get()
    assert "disabled" not in window._refrescar_tasas_button.state()


def test_refrescar_tasas_con_fallo_total_de_red_avisa_y_deja_todo_como_estaba(
    window, monkeypatch
):
    from flexstock.data.connection import set_setting

    set_setting(window._exchange_rate_service._conn, "bcv_rate", "40.00")
    set_setting(window._exchange_rate_service._conn, "bcv_rate_eur", "900.00")
    monkeypatch.setattr(main_window_module, "fetch_rate_now", lambda: None)
    monkeypatch.setattr(main_window_module, "fetch_eur_oficial_now", lambda: None)
    monkeypatch.setattr(main_window_module, "fetch_usdt_now", lambda: None)

    avisos = []
    monkeypatch.setattr(
        main_window_module.messagebox,
        "showwarning",
        lambda *a, **k: avisos.append(a),
    )

    class _HiloSincrono:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main_window_module.threading, "Thread", _HiloSincrono)

    window._on_refrescar_tasas()
    window.update()

    assert "40,00" in window._tasa_bcv_var.get()
    assert "900,00" in window._tasa_bcv_eur_var.get()
    assert len(avisos) == 1
    assert "disabled" not in window._refrescar_tasas_button.state()


def test_refrescar_tasas_con_fallo_parcial_no_avisa(window, monkeypatch):
    """Si al menos una de las 3 se pudo consultar, no hace falta avisar:
    las que fallaron simplemente se quedan con el valor anterior."""
    monkeypatch.setattr(main_window_module, "fetch_rate_now", lambda: ("47.10", None))
    monkeypatch.setattr(main_window_module, "fetch_eur_oficial_now", lambda: None)
    monkeypatch.setattr(main_window_module, "fetch_usdt_now", lambda: None)

    avisos = []
    monkeypatch.setattr(
        main_window_module.messagebox,
        "showwarning",
        lambda *a, **k: avisos.append(a),
    )

    class _HiloSincrono:
        def __init__(self, target, daemon=True):
            self._target = target

        def start(self):
            self._target()

    monkeypatch.setattr(main_window_module.threading, "Thread", _HiloSincrono)

    window._on_refrescar_tasas()
    window.update()

    assert "47,10" in window._tasa_bcv_var.get()
    assert avisos == []


# ------------------------------------------------------------
# tasa BCV (dolar): carga manual (fallback cuando la API no responde)
# ------------------------------------------------------------
def test_editar_tasa_bcv_guarda_valor_valido(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "827,74"
    )

    window._on_editar_tasa_bcv()

    assert "827,74" in window._tasa_bcv_var.get()
    assert window._exchange_rate_service.should_refresh() is False


def test_editar_tasa_bcv_cancelado_no_cambia_nada(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: None
    )

    window._on_editar_tasa_bcv()

    assert "no disponible" in window._tasa_bcv_var.get()


def test_editar_tasa_bcv_texto_invalido_muestra_error_y_no_guarda(window, monkeypatch):
    errores = []
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "no-es-numero"
    )
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    window._on_editar_tasa_bcv()

    assert errores
    assert "no disponible" in window._tasa_bcv_var.get()


def test_editar_tasa_bcv_valor_cero_o_negativo_se_rechaza(window, monkeypatch):
    errores = []
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "0"
    )
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    window._on_editar_tasa_bcv()

    assert errores
    assert "no disponible" in window._tasa_bcv_var.get()


# ------------------------------------------------------------
# tasa euro: carga manual (mismo esquema que el dolar arriba)
# ------------------------------------------------------------
def test_editar_tasa_eur_guarda_valor_valido(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "963,21"
    )

    window._on_editar_tasa_eur()

    assert "963,21" in window._tasa_bcv_eur_var.get()
    assert window._exchange_rate_service.should_refresh("eur_oficial") is False


def test_editar_tasa_eur_cancelado_no_cambia_nada(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: None
    )

    window._on_editar_tasa_eur()

    assert "no disponible" in window._tasa_bcv_eur_var.get()


def test_editar_tasa_eur_texto_invalido_muestra_error_y_no_guarda(window, monkeypatch):
    errores = []
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "no-es-numero"
    )
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    window._on_editar_tasa_eur()

    assert errores
    assert "no disponible" in window._tasa_bcv_eur_var.get()


def test_editar_tasa_eur_valor_cero_o_negativo_se_rechaza(window, monkeypatch):
    errores = []
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "0"
    )
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    window._on_editar_tasa_eur()

    assert errores
    assert "no disponible" in window._tasa_bcv_eur_var.get()


# ------------------------------------------------------------
# IVA: editable desde el encabezado (mismo esquema que las tasas arriba)
# ------------------------------------------------------------
def test_iva_muestra_el_valor_por_defecto(window):
    assert "16" in window._iva_var.get()


def test_editar_iva_guarda_valor_valido(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "18,00"
    )

    window._on_editar_iva()

    assert "18,00" in window._iva_var.get()
    assert window._billing_service.default_tax_rate() == Decimal("18.00")


def test_editar_iva_cancelado_no_cambia_nada(window, monkeypatch):
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: None
    )

    window._on_editar_iva()

    assert "16" in window._iva_var.get()


def test_editar_iva_texto_invalido_muestra_error_y_no_guarda(window, monkeypatch):
    errores = []
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "no-es-numero"
    )
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    window._on_editar_iva()

    assert errores
    assert "16" in window._iva_var.get()


def test_editar_iva_fuera_de_rango_se_rechaza(window, monkeypatch):
    errores = []
    monkeypatch.setattr(
        main_window_module.simpledialog, "askstring", lambda *a, **k: "150"
    )
    monkeypatch.setattr(
        main_window_module.messagebox, "showerror", lambda *a, **k: errores.append(a)
    )

    window._on_editar_iva()

    assert errores
    assert "16" in window._iva_var.get()


# ------------------------------------------------------------
# barra lateral (CustomTkinter)
# ------------------------------------------------------------
def test_arranca_en_inicio_con_su_boton_resaltado(window):
    assert window._pagina_actual is window._home_page
    assert window._titulo_pagina_var.get() == "Inicio"
    assert window._nav_buttons["Inicio"].cget("fg_color") == pair("sidebar_active_bg")
    assert window._nav_buttons["Clientes"].cget("fg_color") == pair("sidebar_bg")


def test_click_en_la_barra_lateral_muestra_esa_pagina_y_oculta_la_anterior(window):
    productos = window._pagina("Productos")

    window._nav_buttons["Facturas"].invoke()

    assert window._pagina_actual is window._billing_page
    assert window._billing_page.winfo_manager() == "pack"
    assert productos.winfo_manager() == ""
    assert window._titulo_pagina_var.get() == "Facturas"


def test_cambiar_de_pagina_la_recarga(window, monkeypatch):
    recargas = []
    clientes = window._pagina("Clientes")
    monkeypatch.setattr(clientes, "reload", lambda: recargas.append(1))

    window._nav_buttons["Clientes"].invoke()

    assert recargas == [1]


def test_sin_modo_avanzado_no_hay_boton_de_cotizaciones(window):
    assert window._nav_buttons["Cotizaciones"].winfo_manager() == ""

    window._actualizar_pestana_cotizaciones(True)

    assert window._nav_buttons["Cotizaciones"].winfo_manager() == "pack"


def test_el_nombre_de_la_tienda_se_ve_en_la_barra_lateral(window, conn):
    from flexstock.services.settings_service import StoreInfo

    window._settings_service.set_store_info(
        StoreInfo(name="Ferreteria El Tornillo", tax_id="", address="", phone="")
    )
    window._on_datos_de_tienda_cambiados()

    assert window._tienda_var.get() == "Ferreteria El Tornillo"


def test_cambiar_el_tema_desde_configuracion_lo_aplica_y_lo_recuerda(window):
    window._aplicar_tema("dark")

    assert window._theme.mode == "dark"
    assert window._settings_service.ui_theme() == "dark"


# ------------------------------------------------------------
# estado del respaldo, abajo de la barra lateral
# ------------------------------------------------------------
def test_sin_respaldos_el_pie_lo_avisa_en_rojo(window, monkeypatch):
    monkeypatch.setattr(window._backup_service, "last_backup_at", lambda: None)

    window._refrescar_estado_respaldo()

    assert "Sin respaldos" in window._estado_respaldo.cget("text")
    assert window._estado_respaldo.cget("text_color") == pair("danger_bg")


def test_con_un_respaldo_de_hoy_el_pie_no_alarma(window, monkeypatch):
    monkeypatch.setattr(
        window._backup_service,
        "last_backup_at",
        lambda: datetime.now(),  # noqa: DTZ005
    )

    window._refrescar_estado_respaldo()

    assert "hoy" in window._estado_respaldo.cget("text")
    assert window._estado_respaldo.cget("text_color") == pair("sidebar_muted_fg")


def test_respaldo_viejo_vuelve_a_alarmar(window, monkeypatch):
    from flexstock.ui.home_page import DIAS_SIN_RESPALDO_ALERTA

    viejo = datetime.now() - timedelta(days=DIAS_SIN_RESPALDO_ALERTA)  # noqa: DTZ005
    monkeypatch.setattr(window._backup_service, "last_backup_at", lambda: viejo)

    window._refrescar_estado_respaldo()

    assert window._estado_respaldo.cget("text_color") == pair("danger_bg")


def test_click_en_el_estado_del_respaldo_abre_esa_seccion(window):
    window._estado_respaldo.invoke()

    assert window._pagina_actual is window._config_page


def test_productos_recibe_el_servicio_de_stock_para_ajustar_desde_la_ficha(window):
    assert window._pagina("Productos")._stock_service is not None


# ------------------------------------------------------------
# barra lateral plegable (tocando el cuadrado de la marca)
# ------------------------------------------------------------
def _esperar_barra(esperar, ventana, *, plegada: bool) -> None:
    """Corre el mainloop hasta que la animacion de la barra termina."""
    objetivo = 78 if plegada else ventana._ancho_abierto
    esperar(
        ventana,
        lambda: (
            ventana._sidebar_animacion is None
            and int(ventana._sidebar.cget("width")) == objetivo
        ),
        que="termine de plegarse" if plegada else "termine de desplegarse",
    )


def _barra_en_modo_iconos(ventana) -> bool:
    return (
        all(
            b.winfo_manager() == "pack"
            for b in ventana._nav_iconos.values()
            if b is not ventana._nav_iconos["Cotizaciones"]
        )
        and all(b.winfo_manager() == "" for b in ventana._nav_buttons.values())
        and ventana._marca_textos.winfo_manager() == ""
        and all(t.winfo_manager() == "" for t in ventana._titulos_de_grupo.values())
        and all(t.winfo_manager() == "pack" for t in ventana._separadores.values())
        and ventana._estado_respaldo.winfo_manager() == ""
        and ventana._estado_respaldo_icono.winfo_manager() == "pack"
    )


def _barra_en_modo_texto(ventana) -> bool:
    return (
        all(
            b.winfo_manager() == "pack"
            for b in ventana._nav_buttons.values()
            if b is not ventana._nav_buttons["Cotizaciones"]
        )
        and all(b.winfo_manager() == "" for b in ventana._nav_iconos.values())
        and ventana._marca_textos.winfo_manager() == "pack"
        and all(t.winfo_manager() == "pack" for t in ventana._titulos_de_grupo.values())
        and all(t.winfo_manager() == "" for t in ventana._separadores.values())
        and ventana._estado_respaldo.winfo_manager() == "pack"
        and ventana._estado_respaldo_icono.winfo_manager() == ""
    )


def test_al_arrancar_desplegada_se_ve_el_nombre_de_la_tienda(window):
    """
    _poner_modo_sidebar es idempotente: al arrancar con la barra desplegada
    nadie empacaba el texto de la marca y quedaba solo la "F".
    """
    assert window._marca_textos.winfo_manager() == "pack"


def test_tocar_la_marca_pliega_la_barra_a_solo_iconos(window, esperar):
    window._marca_boton.invoke()

    # La preferencia se guarda al tocar, no al terminar la animacion.
    assert window._sidebar_plegada is True
    assert window._settings_service.sidebar_collapsed() is True
    _esperar_barra(esperar, window, plegada=True)
    assert int(window._sidebar.cget("width")) == 78
    assert _barra_en_modo_iconos(window)
    assert all(b.cget("text") == "" for b in window._nav_iconos.values())


def test_plegar_y_desplegar_devuelve_la_barra_a_como_estaba(window, esperar):
    ancho = int(window._sidebar.cget("width"))

    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=True)
    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=False)

    assert window._sidebar_plegada is False
    assert window._settings_service.sidebar_collapsed() is False
    assert int(window._sidebar.cget("width")) == ancho
    assert window._nav_buttons["Facturas"].cget("text").strip() == "Facturas"
    assert "Último respaldo" in window._estado_respaldo.cget("text")
    assert _barra_en_modo_texto(window)


def test_la_animacion_pasa_por_anchos_intermedios_y_siempre_decrece(window, esperar):
    anchos = []
    window._marca_boton.invoke()
    esperar(
        window,
        lambda: (
            anchos.append(int(window._sidebar.cget("width")))
            or window._sidebar_animacion is None
        ),
        que="termine de plegarse",
    )

    assert anchos[-1] == 78
    assert any(78 < a < window._ancho_abierto for a in anchos)
    assert anchos == sorted(anchos, reverse=True)


def test_tocar_a_mitad_de_la_animacion_cambia_de_rumbo_sin_dejarla_a_medias(
    window, esperar
):
    window._marca_boton.invoke()  # pliega...
    esperar(
        window,
        lambda: int(window._sidebar.cget("width")) < window._ancho_abierto - 20,
        que="la barra empiece a achicarse",
    )
    en_el_aire = int(window._sidebar.cget("width"))
    assert en_el_aire > 78  # sigue a mitad de camino

    window._marca_boton.invoke()  # ...y se arrepiente

    _esperar_barra(esperar, window, plegada=False)
    assert window._sidebar_plegada is False
    assert window._settings_service.sidebar_collapsed() is False
    assert _barra_en_modo_texto(window)


def test_desplegar_y_volver_a_plegar_a_mitad_de_camino_termina_plegada(window, esperar):
    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=True)
    window._marca_boton.invoke()  # despliega...
    esperar(
        window,
        lambda: int(window._sidebar.cget("width")) > 78 + 20,
        que="la barra empiece a ensancharse",
    )

    window._marca_boton.invoke()  # ...y vuelve a plegar

    _esperar_barra(esperar, window, plegada=True)
    assert _barra_en_modo_iconos(window)


def test_cerrar_la_ventana_a_mitad_de_la_animacion_cancela_el_paso_pendiente(conn):
    ventana = _ventana(conn)
    ventana._marca_boton.invoke()
    assert ventana._sidebar_animacion is not None  # hay un paso en cola

    ventana.destroy()

    assert ventana._sidebar_animacion is None


def test_el_boton_activo_sigue_a_la_pagina_aunque_se_cambie_en_el_otro_modo(
    window, esperar
):
    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=True)

    window._nav_iconos["Clientes"].invoke()  # navegar con la barra plegada
    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=False)

    assert window._nav_buttons["Clientes"].cget("fg_color") == pair("sidebar_active_bg")
    assert window._nav_buttons["Inicio"].cget("fg_color") == pair("sidebar_bg")
    # y al reves: navegar abierta, plegar, y el icono correcto queda marcado
    window._nav_buttons["Productos"].invoke()
    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=True)
    assert window._nav_iconos["Productos"].cget("fg_color") == pair("sidebar_active_bg")
    assert window._nav_iconos["Clientes"].cget("fg_color") == pair("sidebar_bg")


def test_cotizaciones_aparece_en_el_modo_en_que_este_la_barra(window, esperar):
    window._marca_boton.invoke()
    _esperar_barra(esperar, window, plegada=True)

    window._actualizar_pestana_cotizaciones(True)

    assert window._nav_iconos["Cotizaciones"].winfo_manager() == "pack"
    assert window._nav_buttons["Cotizaciones"].winfo_manager() == ""


def test_plegada_cada_icono_dice_su_nombre_al_pasar_el_mouse(window, esperar):
    tooltip = window._nav_iconos["Productos"]._flex_tooltip

    assert tooltip._texto() is None  # abierta: el nombre ya se ve

    window._alternar_sidebar()

    assert tooltip._texto() == "Productos"
    _esperar_barra(esperar, window, plegada=True)


def test_plegada_el_estado_del_respaldo_dice_su_texto_al_pasar_el_mouse(
    window, esperar
):
    tooltip = window._estado_respaldo_icono._flex_tooltip

    assert tooltip._texto() is None  # abierta: el texto ya se ve

    window._alternar_sidebar()

    assert tooltip._texto() == window._texto_respaldo
    _esperar_barra(esperar, window, plegada=True)


def test_arranca_plegada_si_asi_se_dejo(conn):
    SettingsService(conn).set_sidebar_collapsed(True)

    ventana = _ventana(conn)

    assert ventana._sidebar_plegada is True
    assert ventana._sidebar_animacion is None  # arranca ya plegada, sin animar
    assert int(ventana._sidebar.cget("width")) == 78
    assert _barra_en_modo_iconos(ventana)
    ventana.destroy()


def test_el_estado_del_respaldo_se_refresca_en_los_dos_botones(window):
    window._refrescar_estado_respaldo()

    assert window._estado_respaldo.cget("text_color") == (
        window._estado_respaldo_icono.cget("text_color")
    )
    assert window._estado_respaldo.cget("border_color") == (
        window._estado_respaldo_icono.cget("border_color")
    )


@pytest.mark.parametrize("letra", [100, 150])
def test_plegar_no_mueve_los_iconos_ni_corta_nada_a_cualquier_tamano_de_letra(
    conn, esperar, mostrar_con_tamano, letra
):
    """
    Los botones de las dos versiones miden lo mismo y se empacan en el mismo
    lugar: lo unico que cambia al plegar es el ancho y que desaparece el
    texto. Medido con geometria real, a 100 % y a 150 % de letra.
    """
    SettingsService(conn).set_font_scale(letra)
    ventana = _ventana(conn)
    mostrar_con_tamano(ventana, ventana, "1500x900")
    esperar(ventana, lambda: ventana._nav_buttons["Inicio"].winfo_height() > 1)
    abiertos = {
        t: (b.winfo_y(), b.winfo_height())
        for t, b in ventana._nav_buttons.items()
        if b.winfo_manager()
    }
    alto_abierto = ventana._sidebar.winfo_height()
    assert ventana._nav_buttons["Proveedores"].winfo_width() <= int(
        ventana._sidebar.cget("width")
    )

    ventana._marca_boton.invoke()
    _esperar_barra(esperar, ventana, plegada=True)
    esperar(ventana, lambda: ventana._nav_iconos["Inicio"].winfo_height() > 1)

    for titulo, (y, alto) in abiertos.items():
        icono = ventana._nav_iconos[titulo]
        assert (icono.winfo_y(), icono.winfo_height()) == (y, alto), titulo
        assert icono.winfo_width() == 44
    assert ventana._sidebar.winfo_height() == alto_abierto
    # Nada se sale de la barra ni se tapa con el respaldo.
    pie = ventana._pie.winfo_y()
    ultimo = ventana._nav_iconos["Proveedores"]
    assert ultimo.winfo_rooty() + ultimo.winfo_height() <= ventana._pie.winfo_rooty()
    assert pie + ventana._pie.winfo_height() <= ventana._sidebar.winfo_height()
    assert ventana._estado_respaldo_icono.winfo_height() >= 48
    ventana.destroy()


def test_con_logo_de_la_tienda_la_marca_lo_muestra_en_vez_de_la_f(window, tmp_path):
    from PIL import Image

    ruta = tmp_path / "logo.png"
    Image.new("RGB", (300, 120), "red").save(ruta)
    window._settings_service.set_logo(ruta)

    window._on_datos_de_tienda_cambiados()

    assert window._marca_boton.cget("text") == ""
    assert window._marca_boton.cget("image") is not None


def test_sin_logo_la_marca_es_la_f(window):
    assert window._marca_boton.cget("text") == "F"
