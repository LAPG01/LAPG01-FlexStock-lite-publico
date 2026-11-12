"""
Tests de PreviewService / reports/preview_pdf.py / ui/files.py: la
vista previa de un borrador como COTIZACION (desde Facturas) o NOTA DE
ENTREGA (desde Cotizaciones).

    pytest -q
"""

import re
import sqlite3
import subprocess
import sys
from datetime import datetime
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.quotation_repository import QuotationRepository
from flexstock.domain.entities import Invoice, InvoiceLine
from flexstock.reports.preview_pdf import _apto_para_nombre
from flexstock.services.exchange_rate_service import ExchangeRateService
from flexstock.services.preview_service import PreviewService
from flexstock.services.quotation_service import QuotationService
from flexstock.services.rate_display_service import BCV, PERSONAL, RateDisplayService
from flexstock.ui import files


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def con_tasas(conn):
    set_setting(conn, "bcv_rate", "185.40")
    set_setting(conn, "personal_bs_rate", "260.00")


@pytest.fixture
def service(conn):
    return PreviewService(conn, RateDisplayService(conn, ExchangeRateService(conn)))


def _factura(*precios_cents, tax_rate="16.00", nombre="Juan Perez"):
    lineas = [
        InvoiceLine(
            id=None,
            product_id=i,
            product_sku=f"P{i}",
            product_name=f"Producto {i}",
            unit="UND",
            quantity_milli=1000,
            unit_price_cents=cents,
        )
        for i, cents in enumerate(precios_cents, start=1)
    ]
    return Invoice(
        id=None,
        display_number="",
        customer_name=nombre,
        tax_rate=Decimal(tax_rate),
        customer_tax_id="V-12345678",
        customer_address="Av. Principal",
        lines=lineas,
    )


# ------------------------------------------------------------
# factura -> COTIZACION
# ------------------------------------------------------------
def test_con_la_tasa_bcv_muestra_el_dolar_del_catalogo_y_los_bs(service, con_tasas):
    doc = service.invoice_preview(_factura(1000), BCV)

    linea = doc.lines[0]
    assert (linea.unit_usd, linea.subtotal_usd) == (1000, 1000)  # 10,00$
    # 10,00$ x 185,40 = 1.854,00 Bs
    assert (linea.unit_bs, linea.subtotal_bs) == (185400, 185400)
    assert doc.totals_usd == (1000, 160, 1160)
    assert doc.totals_bs == (185400, 29664, 215064)
    assert doc.title == "COTIZACIÓN"


def test_con_la_tasa_de_referencia_el_dolar_es_el_de_la_cuenta_bcv(service, con_tasas):
    doc = service.invoice_preview(_factura(1000), PERSONAL)

    linea = doc.lines[0]
    # 10,00$ x 260 = 2.600,00 Bs ; 2.600,00 / 185,40 = 14,02$
    assert (linea.unit_bs, linea.subtotal_bs) == (260000, 260000)
    assert (linea.unit_usd, linea.subtotal_usd) == (1402, 1402)
    assert doc.totals_bs == (260000, 41600, 301600)
    assert doc.totals_usd == (1402, 224, 1626)


def test_con_la_tasa_de_referencia_no_sale_el_dolar_del_catalogo(service, con_tasas):
    """El cliente podria dividir los Bs entre ese $ y ver la tasa usada."""
    doc = service.invoice_preview(_factura(1000, 250), PERSONAL)

    catalogo = {1000, 250}  # 10,00$ y 2,50$
    dolares = {v for line in doc.lines for v in (line.unit_usd, line.subtotal_usd)}
    assert not catalogo & dolares
    assert not catalogo & set(doc.totals_usd)


def test_los_totales_cuadran_con_la_suma_de_las_lineas(service, con_tasas):
    doc = service.invoice_preview(_factura(1000, 250, 85), PERSONAL)

    assert doc.totals_bs[0] == sum(line.subtotal_bs for line in doc.lines)
    assert doc.totals_usd[0] == sum(line.subtotal_usd for line in doc.lines)
    assert doc.totals_bs[2] == doc.totals_bs[0] + doc.totals_bs[1]


def test_sin_iva_el_iva_es_cero(service, con_tasas):
    doc = service.invoice_preview(_factura(1000, tax_rate="0"), BCV)

    assert doc.tax_rate == 0
    assert doc.totals_bs[1] == 0


def test_lleva_nombre_documento_y_direccion_del_cliente(service, con_tasas):
    doc = service.invoice_preview(_factura(1000), BCV)

    assert doc.customer_name == "Juan Perez"
    assert doc.customer_tax_id == "V-12345678"
    assert doc.customer_address == "Av. Principal"


def test_sin_lineas_avisa(service, con_tasas):
    with pytest.raises(ValueError, match="al menos una línea"):
        service.invoice_preview(_factura(), BCV)


def test_tasa_sin_cargar_avisa_cual_falta(conn, service):
    set_setting(conn, "bcv_rate", "185.40")  # falta la de referencia

    with pytest.raises(ValueError, match="referencia"):
        service.invoice_preview(_factura(1000), PERSONAL)


def test_tasa_bcv_sin_cargar_avisa(service):
    with pytest.raises(ValueError, match="BCV"):
        service.invoice_preview(_factura(1000), BCV)


def test_sin_servicio_de_tasas_solo_sale_el_dolar(conn):
    doc = PreviewService(conn).invoice_preview(_factura(1000))

    assert doc.totals_bs is None
    assert doc.totals_usd == (1000, 160, 1160)
    assert doc.lines[0].unit_bs is None


def test_usa_la_fecha_indicada(service, con_tasas):
    fecha = datetime(2026, 9, 1, 10, 0)  # noqa: DTZ001 - hora local, igual que el esquema

    assert service.invoice_preview(_factura(1000), BCV, issued_at=fecha).issued_at == (
        fecha
    )


# ------------------------------------------------------------
# cotizacion -> NOTA DE ENTREGA
# ------------------------------------------------------------
@pytest.fixture
def quotation_service(conn):
    return QuotationService(
        conn,
        QuotationRepository(conn),
        ProductRepository(conn),
        CustomerRepository(conn),
        PriceTierRepository(conn),
        ExchangeRateService(conn),
    )


def _borrador_de_cotizacion(conn, quotation_service, product_service_fixture):
    tier3 = next(
        t.id for t in product_service_fixture.list_price_tiers() if t.position == 3
    )
    producto = product_service_fixture.create_product(
        name="Tornillo",
        category_id=product_service_fixture.default_category_id(),
        prices={tier3: Decimal("10.00")},
    )
    q = quotation_service.crear_borrador(customer_name="Maria")
    quotation_service.agregar_linea(q, product_id=producto.id, quantity=Decimal(1))
    return q


@pytest.fixture
def product_service_fixture(conn):
    from flexstock.data.repositories.category_repository import CategoryRepository
    from flexstock.services.product_service import ProductService

    return ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )


def test_la_cotizacion_muestra_bs_y_dolar_bcv_con_titulo_de_nota_de_entrega(
    conn, service, con_tasas, quotation_service, product_service_fixture
):
    q = _borrador_de_cotizacion(conn, quotation_service, product_service_fixture)

    doc = service.quotation_preview(q)

    linea = doc.lines[0]
    assert (linea.unit_bs, linea.unit_usd) == (260000, 1402)  # 2.600,00 Bs / 14,02$
    assert doc.title == "NOTA DE ENTREGA"
    assert doc.legend == "Documento no fiscal"
    assert doc.customer_name == "Maria"
    # el $ del catalogo (10,00$) no sale
    assert 1000 not in (linea.unit_usd, linea.subtotal_usd, *doc.totals_usd)


def test_generar_la_vista_previa_no_toca_la_base(
    conn, service, con_tasas, quotation_service, product_service_fixture, tmp_path
):
    q = _borrador_de_cotizacion(conn, quotation_service, product_service_fixture)
    antes = conn.execute("SELECT count(*) FROM quotations").fetchone()[0]

    service.quotation_pdf(q, export_dir=tmp_path)
    service.invoice_pdf(_factura(1000), BCV, export_dir=tmp_path)

    assert conn.execute("SELECT count(*) FROM quotations").fetchone()[0] == antes
    assert conn.execute("SELECT count(*) FROM invoices").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM inventory_movements").fetchone()[0] == 0


# ------------------------------------------------------------
# el PDF
# ------------------------------------------------------------
def test_genera_un_pdf_con_titulo_y_cliente_en_el_nombre(service, con_tasas, tmp_path):
    ruta = service.invoice_pdf(_factura(1000, 250), PERSONAL, export_dir=tmp_path)

    assert ruta.parent == tmp_path
    assert ruta.name == "Cotizacion-Juan-Perez.pdf"
    assert ruta.read_bytes().startswith(b"%PDF")


def test_el_nombre_del_archivo_de_una_nota_de_entrega(
    conn, service, con_tasas, quotation_service, product_service_fixture, tmp_path
):
    q = _borrador_de_cotizacion(conn, quotation_service, product_service_fixture)

    ruta = service.quotation_pdf(q, export_dir=tmp_path)

    assert ruta.name == "Nota-de-entrega-Maria.pdf"


def test_regenerar_para_el_mismo_cliente_sobrescribe(service, con_tasas, tmp_path):
    service.invoice_pdf(_factura(1000), BCV, export_dir=tmp_path)
    service.invoice_pdf(_factura(1000, 250), BCV, export_dir=tmp_path)

    assert len(list(tmp_path.iterdir())) == 1


def test_un_pdf_solo_en_dolares_tambien_se_genera(conn, tmp_path):
    ruta = PreviewService(conn).invoice_pdf(_factura(1000), export_dir=tmp_path)

    assert ruta.read_bytes().startswith(b"%PDF")


def test_muchas_lineas_paginan_sin_fallar(service, con_tasas, tmp_path):
    corta = service.invoice_pdf(_factura(500, 500, 500), PERSONAL, export_dir=tmp_path)
    larga = service.invoice_pdf(
        _factura(*([500] * 120), nombre="Otro"), PERSONAL, export_dir=tmp_path
    )

    def paginas(ruta):
        return len(re.findall(rb"/Type\s*/Page(?![s\w])", ruta.read_bytes()))

    assert paginas(corta) == 1
    assert paginas(larga) > 1  # el motor pagina solo, no se corta la tabla


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("Juan Perez", "Juan-Perez"),
        ('Ferre/"Sur": C.A.', "Ferre-Sur-C.A"),
        ("   ", "cliente"),
        ("Jose Nunez", "Jose-Nunez"),
        ("x" * 200, "x" * 60),
    ],
)
def test_nombre_de_archivo_apto_para_windows_y_linux(texto, esperado):
    assert _apto_para_nombre(texto) == esperado


# ------------------------------------------------------------
# abrir en el visor de PDF
# ------------------------------------------------------------
def test_abre_con_xdg_open_en_linux(monkeypatch, tmp_path):
    lanzados = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: lanzados.append(args))

    files.open_in_viewer(tmp_path / "a.pdf")

    assert lanzados == [["xdg-open", str(tmp_path / "a.pdf")]]


def test_abre_con_open_en_mac(monkeypatch, tmp_path):
    lanzados = []
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(subprocess, "Popen", lambda args, **k: lanzados.append(args))

    files.open_in_viewer(tmp_path / "a.pdf")

    assert lanzados == [["open", str(tmp_path / "a.pdf")]]


def test_abre_con_startfile_en_windows(monkeypatch, tmp_path):
    abiertos = []
    monkeypatch.setattr(sys, "platform", "win32")
    # os.startfile solo existe en Windows: raising=False para poder
    # probarlo desde Linux
    monkeypatch.setattr(files.os, "startfile", abiertos.append, raising=False)

    files.open_in_viewer(tmp_path / "a.pdf")

    assert abiertos == [tmp_path / "a.pdf"]


def test_a_tasa_bcv_el_recuadro_moneda_dice_la_tasa_del_dia(service, con_tasas):
    from flexstock.reports.preview_pdf import _recuadro_moneda

    doc = service.invoice_preview(_factura(1000), BCV)

    assert doc.rate_text == "Tasa BCV: 185,40 Bs por dólar"
    textos = [getattr(e, "text", "") for e in _recuadro_moneda(doc)]
    assert textos == ["<b>Dólares ($) y bolívares (Bs)</b>", doc.rate_text]


def test_a_tasa_de_referencia_no_imprime_ninguna_tasa(service, con_tasas):
    """Junto a los Bs, la tasa delataria la cuenta (igual que el $ del catalogo)."""
    assert service.invoice_preview(_factura(1000), PERSONAL).rate_text is None


def test_sin_servicio_de_tasas_no_hay_tasa_que_mostrar(conn):
    sin_tasas = PreviewService(conn)
    assert sin_tasas.invoice_preview(_factura(1000)).rate_text is None
