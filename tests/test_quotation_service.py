"""
Tests de cotizaciones: calculo en 3 monedas (dominio), repositorio,
QuotationService y PDF.

    pytest -q
"""

import sqlite3
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.quotation_repository import QuotationRepository
from flexstock.domain.entities import Customer, InvoiceLine, Quotation
from flexstock.domain.enums import QuoteCurrency
from flexstock.domain.errors import (
    EmptyDocument,
    InvoiceAlreadyCancelled,
    ReasonRequired,
)
from flexstock.domain.money import convert_cents
from flexstock.services.exchange_rate_service import ExchangeRateService
from flexstock.services.quotation_service import MissingRate, QuotationService


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


def _tier_id(conn, position):
    return next(
        t.id for t in PriceTierRepository(conn).list_all() if t.position == position
    )


def _crear_producto(conn, *, sku="FER-0001", name="Tornillo", precios=None, stock=0):
    precios = precios if precios is not None else {3: 10000, 1: 7000}
    pid = conn.execute(
        "INSERT INTO products (sku, name, current_stock) VALUES (?, ?, ?)",
        (sku, name, stock),
    ).lastrowid
    for position, price_milli in precios.items():
        conn.execute(
            "INSERT INTO product_prices (product_id, tier_id, price_milli) "
            "VALUES (?, ?, ?)",
            (pid, _tier_id(conn, position), price_milli),
        )
    return pid


def _linea(unit_price_cents, quantity_milli=1000):
    return InvoiceLine(
        id=None,
        product_id=None,
        product_sku="X",
        product_name="X",
        unit="UNI",
        quantity_milli=quantity_milli,
        unit_price_cents=unit_price_cents,
    )


# ------------------------------------------------------------
# Dominio: conversion
# ------------------------------------------------------------
def test_convert_cents_redondea_una_sola_vez_half_up():
    assert convert_cents(1000, Decimal("45.50")) == 45500
    assert convert_cents(99, Decimal("1.1375")) == 113  # 112,6125


def test_cotizacion_calcula_las_tres_monedas():
    q = Quotation(
        id=None,
        display_number="",
        customer_name="Mostrador",
        tax_rate=Decimal(16),
        personal_rate=Decimal(50),
        bcv_rate=Decimal(40),
        lines=[_linea(1000, 2000)],
    )
    linea = q.lines[0]

    assert q.totals_in(QuoteCurrency.USD) == (2000, 320, 2320)
    assert q.unit_price_in(linea, QuoteCurrency.BS) == 50000
    assert q.totals_in(QuoteCurrency.BS) == (100000, 16000, 116000)
    assert q.unit_price_in(linea, QuoteCurrency.USD_BCV) == 1250
    assert q.totals_in(QuoteCurrency.USD_BCV) == (2500, 400, 2900)


def test_subtotal_en_otra_moneda_es_la_suma_de_lineas_convertidas():
    q = Quotation(
        id=None,
        display_number="",
        customer_name="X",
        tax_rate=Decimal(0),
        personal_rate=Decimal("45.5"),
        bcv_rate=Decimal(40),
        lines=[_linea(33, 1000), _linea(33, 1000), _linea(33, 1000)],
    )

    lineas = [q.line_total_in(line, QuoteCurrency.USD_BCV) for line in q.lines]

    assert lineas == [38, 38, 38]
    assert q.totals_in(QuoteCurrency.USD_BCV) == (114, 0, 114)


# ------------------------------------------------------------
# Tasas
# ------------------------------------------------------------
def test_tasa_propia_arranca_vacia_y_se_guarda(service):
    assert service.personal_rate() is None

    service.set_personal_rate(Decimal("52.30"))

    assert service.personal_rate() == Decimal("52.30")


def test_tasa_propia_debe_ser_positiva(service):
    with pytest.raises(ValueError):
        service.set_personal_rate(Decimal(0))


def test_borrador_sin_tasa_propia_falla(service, conn):
    set_setting(conn, "bcv_rate", "40.00")

    with pytest.raises(MissingRate, match="tasa propia"):
        service.crear_borrador(customer_name="Mostrador")


def test_borrador_sin_tasa_bcv_falla(service, conn):
    set_setting(conn, "personal_bs_rate", "50")

    with pytest.raises(MissingRate, match="BCV"):
        service.crear_borrador(customer_name="Mostrador")


# ------------------------------------------------------------
# Borrador y lineas
# ------------------------------------------------------------
def test_borrador_toma_tasas_e_iva_vigentes(service, con_tasas):
    q = service.crear_borrador(customer_name="  Mostrador ")

    assert q.customer_name == "Mostrador"
    assert q.personal_rate == Decimal(50)
    assert q.bcv_rate == Decimal("40.00")
    assert q.tax_rate == Decimal("16.00")


def test_agregar_linea_usa_el_precio_3(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")

    linea = service.agregar_linea(q, product_id=pid, quantity=Decimal(2))

    assert linea.unit_price_cents == 1000
    assert linea.tier_id == _tier_id(conn, 3)
    assert q.lines == [linea]


def test_agregar_linea_sin_precio_3_falla(service, conn, con_tasas):
    pid = _crear_producto(conn, precios={1: 7000})
    q = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(ValueError, match="Precio 3"):
        service.agregar_linea(q, product_id=pid, quantity=Decimal(1))


def test_agregar_linea_cantidad_cero_falla(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(ValueError):
        service.agregar_linea(q, product_id=pid, quantity=Decimal(0))


def test_toggle_iva_cambia_la_tasa_del_borrador(service, con_tasas):
    q = service.crear_borrador(customer_name="Mostrador")

    service.set_cobrar_iva(q, False)
    assert q.tax_rate == Decimal(0)

    service.set_cobrar_iva(q, True)
    assert q.tax_rate == Decimal("16.00")


def test_quitar_linea(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))

    service.quitar_linea(q, 0)

    assert q.lines == []


# ------------------------------------------------------------
# Confirmar
# ------------------------------------------------------------
def test_confirmar_sin_lineas_falla(service, con_tasas):
    q = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(EmptyDocument):
        service.confirmar(q)


def test_confirmar_guarda_numero_snapshots_y_totales(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(2))

    service.confirmar(q)
    set_setting(conn, "personal_bs_rate", "99")
    set_setting(conn, "bcv_rate", "99")

    guardada = service.get_quotation(q.id)
    assert guardada.display_number == "NE-000001"
    assert guardada.personal_rate == Decimal(50)
    assert guardada.bcv_rate == Decimal("40.00")
    assert guardada.totals_in(QuoteCurrency.BS) == (100000, 16000, 116000)

    resumen = service.list_quotations()[0]
    assert resumen.total_in(QuoteCurrency.USD) == 2320
    assert resumen.total_in(QuoteCurrency.BS) == 116000
    assert resumen.total_in(QuoteCurrency.USD_BCV) == 2900


def test_confirmar_sin_iva(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))

    service.confirmar(q, cobrar_iva=False)

    assert service.get_quotation(q.id).totals_in(QuoteCurrency.USD) == (1000, 0, 1000)


def test_confirmar_no_mueve_stock_ni_crea_facturas(service, conn, con_tasas):
    pid = _crear_producto(conn, stock=5000)
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(3))

    service.confirmar(q)

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()[0]
    assert stock == 5000
    assert conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0


def test_numeracion_correlativa(service, conn, con_tasas):
    pid = _crear_producto(conn)
    numeros = []
    for _ in range(2):
        q = service.crear_borrador(customer_name="Mostrador")
        service.agregar_linea(q, product_id=pid, quantity=Decimal(1))
        numeros.append(service.confirmar(q).display_number)

    assert numeros == ["NE-000001", "NE-000002"]


def test_confirmar_refresca_datos_del_cliente(service, conn, con_tasas):
    customers = CustomerRepository(conn)
    cliente = Customer(id=None, sku="CLI-0001", name="Juan", tax_id="V-1")
    customers.create(cliente)
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_id=cliente.id)
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))
    cliente.name = "Juan Perez"
    customers.update(cliente)

    service.confirmar(q)

    assert service.get_quotation(q.id).customer_name == "Juan Perez"


# ------------------------------------------------------------
# Anular
# ------------------------------------------------------------
def test_anular_requiere_motivo(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))
    service.confirmar(q)

    with pytest.raises(ReasonRequired):
        service.cancelar(q.id, "  ")


def test_anular_dos_veces_falla(service, conn, con_tasas):
    pid = _crear_producto(conn)
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))
    service.confirmar(q)

    anulada = service.cancelar(q.id, "Cliente no vino")

    assert anulada.is_cancelled
    assert anulada.cancel_reason == "Cliente no vino"
    with pytest.raises(InvoiceAlreadyCancelled):
        service.cancelar(q.id, "otra vez")


# ------------------------------------------------------------
# PDF
# ------------------------------------------------------------
@pytest.mark.parametrize(
    ("moneda", "sufijo"),
    [
        (QuoteCurrency.USD, "USD"),
        (QuoteCurrency.BS, "Bs"),
        (QuoteCurrency.USD_BCV, "BCV"),
    ],
)
def test_generate_pdf_en_cada_moneda(
    service, conn, con_tasas, tmp_path, moneda, sufijo
):
    pid = _crear_producto(conn, name='Tornillo 1/2" con nombre bastante largo')
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal("2.5"))
    service.confirmar(q)

    ruta = service.generate_pdf(q.id, moneda, export_dir=tmp_path)

    assert ruta == tmp_path / f"NE-000001-{sufijo}.pdf"
    assert ruta.read_bytes().startswith(b"%PDF")


def test_generate_pdf_inexistente_falla(service, tmp_path):
    with pytest.raises(ValueError):
        service.generate_pdf(999, QuoteCurrency.USD, export_dir=tmp_path)


# ------------------------------------------------------------
# Una nota de entrega NO es una venta
# ------------------------------------------------------------
# Regla del negocio, dicha por el autor: una nota de entrega es un
# documento informal y no entra al cierre de caja ni mueve inventario.
# El codigo ya lo cumplia -el cierre lee solo 'invoices' y este servicio
# nunca llama a StockService- pero eso era una propiedad de como estaba
# escrito, no una garantia. Estos tests la convierten en garantia: si
# alguien manana suma las notas al cierre "para que cuadre", se entera
# aca y no en el mostrador.
def _billing(conn):
    from flexstock.data.repositories.inventory_movement_repository import (
        InventoryMovementRepository,
    )
    from flexstock.data.repositories.invoice_repository import InvoiceRepository
    from flexstock.services.billing_service import BillingService
    from flexstock.services.stock_service import StockService

    productos = ProductRepository(conn)
    return BillingService(
        conn,
        InvoiceRepository(conn),
        productos,
        CustomerRepository(conn),
        PriceTierRepository(conn),
        StockService(conn, productos, InventoryMovementRepository(conn)),
    )


def _nota_de_entrega(conn, service):
    pid = _crear_producto(conn)
    nota = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(nota, product_id=pid, quantity=Decimal(2))
    return service.confirmar(nota)


def test_una_nota_de_entrega_no_entra_al_cierre_de_caja(conn, service, con_tasas):
    from datetime import date

    nota = _nota_de_entrega(conn, service)
    assert nota.id is not None  # se creo de verdad

    hoy = date.today()  # noqa: DTZ011 - hora local, igual que issued_at
    assert _billing(conn).cierre_diario(hoy) == []
    assert _billing(conn).cierre_mensual(hoy.year, hoy.month) == []


def test_una_nota_de_entrega_no_mueve_el_inventario(conn, service, con_tasas):
    pid = _crear_producto(conn)
    conn.execute("UPDATE products SET current_stock = 10000 WHERE id = ?", (pid,))
    nota = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(nota, product_id=pid, quantity=Decimal(2))

    service.confirmar(nota)

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 10000  # intacto
    movimientos = conn.execute(
        "SELECT COUNT(*) AS n FROM inventory_movements"
    ).fetchone()["n"]
    assert movimientos == 0


def test_una_nota_de_entrega_no_escribe_en_invoices(conn, service, con_tasas):
    """Tablas separadas a proposito: una nota nunca es una factura."""
    _nota_de_entrega(conn, service)

    assert conn.execute("SELECT COUNT(*) AS n FROM invoices").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM invoice_lines").fetchone()["n"] == 0
    assert conn.execute("SELECT COUNT(*) AS n FROM quotations").fetchone()["n"] == 1


def test_generate_pdf_con_los_datos_de_la_tienda_cargados(
    service, conn, con_tasas, tmp_path
):
    """La nota de entrega imprime razon social, web, datos de pago y pie
    (Configuracion > Tienda); con simbolos raros en el texto sigue generando."""
    from flexstock.services.settings_service import SettingsService, StoreInfo

    SettingsService(conn).set_store_info(
        StoreInfo(
            name="La Esquina",
            legal_name="Inversiones <La Esquina> & Hijos C.A.",
            web="@laesquina",
            payment_info="Pago movil 0414-1234567\nZelle: pagos@laesquina.com",
            footer_text="Garantia 30 días.",
        )
    )
    pid = _crear_producto(conn, name="Tornillo")
    q = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(q, product_id=pid, quantity=Decimal(1))
    service.confirmar(q)

    ruta = service.generate_pdf(q.id, QuoteCurrency.USD, export_dir=tmp_path)

    assert ruta.read_bytes().startswith(b"%PDF")


def test_la_nota_de_entrega_en_pdf_dice_documento_no_fiscal(tmp_path):
    from flexstock.reports.invoice_pdf import NO_FISCAL, caja_documento

    textos = [
        getattr(p, "text", "")
        for p in caja_documento("NOTA DE ENTREGA", "NE-1", "01/01/2026", NO_FISCAL)
    ]

    assert NO_FISCAL in textos
    assert "DOCUMENTO NO FISCAL" in NO_FISCAL
    assert "NOTA DE ENTREGA" in textos
