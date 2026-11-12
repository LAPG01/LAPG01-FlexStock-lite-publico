"""
Tests de RateDisplayService y del catalogo de columnas (ui/columns.py).

    pytest -q
"""

import sqlite3
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.services.exchange_rate_service import ExchangeRateService
from flexstock.services.rate_display_service import (
    BCV,
    PERSONAL,
    USDT,
    RateDisplayService,
)
from flexstock.services.settings_service import SettingsService
from flexstock.ui import columns


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def rates(conn):
    return RateDisplayService(conn, ExchangeRateService(conn))


# ------------------------------------------------------------
# Tasas
# ------------------------------------------------------------
def test_sin_tasas_devuelve_none(rates):
    assert rates.personal_rate() is None
    assert rates.rate(BCV) is None
    assert rates.to_bs(1000) is None
    assert rates.usd_at_bcv(1000) is None


def test_tasa_propia_se_guarda_y_se_lee(rates):
    rates.set_personal_rate(Decimal("260.00"))

    assert rates.personal_rate() == Decimal("260.00")
    assert rates.rate(PERSONAL) == Decimal("260.00")


def test_tasa_propia_debe_ser_positiva(rates):
    with pytest.raises(ValueError):
        rates.set_personal_rate(Decimal(0))


def test_convierte_con_cada_tasa(conn, rates):
    set_setting(conn, "bcv_rate", "185.40")
    set_setting(conn, "bcv_rate_usdt", "200.00")
    rates.set_personal_rate(Decimal(260))

    assert rates.to_bs(1000, BCV) == 185400
    assert rates.to_bs(1000, PERSONAL) == 260000
    assert rates.to_bs(1000, USDT) == 200000


def test_usd_at_bcv_usa_la_propia_dividida_por_la_bcv(conn, rates):
    set_setting(conn, "bcv_rate", "185.40")
    rates.set_personal_rate(Decimal(260))

    # 2,50$ x 260 / 185,40 = 3,505...
    assert rates.usd_at_bcv(250) == 351


def test_usd_at_bcv_sin_tasa_propia_es_none(conn, rates):
    set_setting(conn, "bcv_rate", "185.40")

    assert rates.usd_at_bcv(250) is None


def test_tasa_guardada_invalida_se_trata_como_faltante(conn, rates):
    set_setting(conn, "personal_bs_rate", "ninguna")

    assert rates.personal_rate() is None


def _factura(tax_rate, *line_totals):
    from flexstock.domain.entities import Invoice, InvoiceLine

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
        for i, cents in enumerate(line_totals, start=1)
    ]
    return Invoice(
        id=None,
        display_number="",
        customer_name="Mostrador",
        tax_rate=tax_rate,
        lines=lineas,
    )


def test_desglose_en_bs_por_linea_y_totales(conn, rates):
    set_setting(conn, "bcv_rate", "185.40")
    factura = _factura(Decimal("16.00"), 1000, 250)  # 10,00$ y 2,50$

    desglose = rates.snapshot().invoice_in_bs(factura, BCV)

    # 10,00 x 185,40 = 1.854,00 Bs ; IVA 296,64 ; total 2.150,64
    assert desglose.lines[0].sin_iva == 185400
    assert desglose.lines[0].iva == 29664
    assert desglose.lines[0].total == 215064
    # 2,50 x 185,40 = 463,50 Bs ; IVA 74,16
    assert desglose.lines[1].sin_iva == 46350
    assert desglose.lines[1].iva == 7416
    # los totales son la suma de las columnas
    assert desglose.subtotal == 185400 + 46350
    assert desglose.tax == 29664 + 7416
    assert desglose.total == desglose.subtotal + desglose.tax
    assert desglose.rate == Decimal("185.40")


def test_desglose_sin_tasa_es_none(rates):
    assert rates.snapshot().invoice_in_bs(_factura(Decimal(16), 1000), BCV) is None


def test_desglose_sin_iva(conn, rates):
    set_setting(conn, "bcv_rate", "185.40")

    desglose = rates.snapshot().invoice_in_bs(_factura(Decimal(0), 1000), BCV)

    assert desglose.tax == 0
    assert desglose.total == desglose.subtotal == 185400


# ------------------------------------------------------------
# Catalogo de columnas
# ------------------------------------------------------------
def test_todos_los_modulos_tienen_catalogo_y_default():
    for modulo in columns.MODULOS:
        catalogo = columns.catalog(modulo)
        assert catalogo
        assert columns.default_keys(modulo)
        assert modulo in columns.MODULO_LABELS


def test_sin_preferencia_usa_el_default():
    catalogo = columns.catalog(columns.PRODUCTS)

    visibles = columns.visible(catalogo, None)

    assert tuple(c.key for c in visibles) == columns.default_keys(columns.PRODUCTS)


def test_respeta_el_orden_del_catalogo_no_el_de_la_seleccion():
    catalogo = columns.catalog(columns.PRODUCTS)

    visibles = columns.visible(catalogo, ("stock", "name", "cost"))

    assert tuple(c.key for c in visibles) == ("name", "cost", "stock")


def test_seleccion_vacia_cae_al_default():
    catalogo = columns.catalog(columns.PRODUCTS)

    visibles = columns.visible(catalogo, ())

    assert tuple(c.key for c in visibles) == columns.default_keys(columns.PRODUCTS)


def test_tier_names_renombran_las_columnas_de_precio():
    catalogo = columns.catalog(
        columns.PRODUCTS, tier_names={1: "Mayor", 2: "Especial", 3: "Detal"}
    )
    titulos = {c.key: c.heading for c in catalogo}

    assert titulos["price1"] == "Mayor $"
    assert titulos["price3"] == "Detal $"
    assert titulos["price3_bs_bcv"] == "Detal Bs (BCV)"
    assert titulos["price3_bs"] == "Detal Bs (tasa propia)"
    assert titulos["price3_bcv"] == "Detal $ equivalente BCV"


def test_sin_modo_avanzado_el_catalogo_no_trae_columnas_de_tasa_propia():
    productos = {c.key for c in columns.catalog(columns.PRODUCTS, advanced=False)}
    facturas = {c.key for c in columns.catalog(columns.INVOICES, advanced=False)}

    assert "price3_bs_bcv" in productos  # tasa oficial: siempre disponible
    assert not {"price3_bs", "price3_bcv"} & productos
    # Facturas: una sola columna en Bs, con la tasa que guardo la
    # factura, asi que no depende del Modo avanzado
    assert "total_bs" in facturas
    assert not {"total_bs_bcv", "total_bcv"} & facturas


# ------------------------------------------------------------
# Persistencia de la preferencia
# ------------------------------------------------------------
def test_columnas_no_personalizadas_son_none(conn):
    assert SettingsService(conn).visible_columns(columns.PRODUCTS) is None


def test_guardar_y_restaurar_columnas(conn):
    settings = SettingsService(conn)

    settings.set_visible_columns(columns.PRODUCTS, ("name", "cost"))
    assert settings.visible_columns(columns.PRODUCTS) == ("name", "cost")

    settings.reset_visible_columns(columns.PRODUCTS)
    assert settings.visible_columns(columns.PRODUCTS) is None


def test_cada_modulo_guarda_su_propia_seleccion(conn):
    settings = SettingsService(conn)

    settings.set_visible_columns(columns.PRODUCTS, ("name",))
    settings.set_visible_columns(columns.CUSTOMERS, ("sku", "phone"))

    assert settings.visible_columns(columns.PRODUCTS) == ("name",)
    assert settings.visible_columns(columns.CUSTOMERS) == ("sku", "phone")
