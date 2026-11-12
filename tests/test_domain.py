"""
Tests del dominio. Corren en milisegundos porque domain/ no toca disco.
Esa es la prueba de que las capas estan bien separadas.

    pytest -q
"""

from decimal import Decimal, InvalidOperation

import pytest

from flexstock.domain import Invoice, InvoiceLine, Product, ProductPrice
from flexstock.domain.money import (
    format_money,
    format_money_milli,
    format_quantity,
    from_cents,
    parse_local_decimal,
    to_cents,
    to_milli,
)


def test_dinero_ida_y_vuelta():
    assert to_cents(Decimal("1.50")) == 150
    assert from_cents(150) == Decimal("1.50")
    assert to_cents(Decimal("0.005")) == 1  # ROUND_HALF_UP, no banquero


def test_formato_local():
    assert format_money(150) == "1,50$"
    assert format_money(1234567) == "12.345,67$"
    assert format_quantity(2500) == "2,5"
    assert format_quantity(3000) == "3"


def test_formato_quantity_multiplos_de_diez_no_usa_notacion_cientifica():
    """
    Regresion: Decimal.normalize() saca los ceros de sobra pero, para un
    valor redondo, tambien puede pasar a notacion cientifica (10 -> '1E+1')
    porque el exponente queda positivo. format_quantity nunca debe mostrar
    eso - tiene que verse igual que cualquier otro numero entero.
    """
    assert format_quantity(10000) == "10"
    assert format_quantity(20000) == "20"
    assert format_quantity(100000) == "100"
    assert format_quantity(-10000) == "-10"


def test_formato_milli_dos_decimales_caso_normal():
    """cost_milli/price_milli sin tercer decimal real: se muestran como plata normal."""
    assert format_money_milli(5000) == "5,00$"
    assert format_money_milli(1234560) == "1.234,56$"


def test_formato_milli_no_es_format_money():
    """
    format_money_milli(5000) != format_money(5000): son escalas distintas
    (x1000 vs x100). Si algun dia coinciden es que se volvio a mezclar
    la Regla 1 - ver la nota en products_page.py sobre la columna Costo.
    """
    assert format_money_milli(5000) == "5,00$"
    assert format_money(5000) == "50,00$"  # a proposito: NO es el mismo valor


def test_formato_milli_conserva_tercer_decimal_real():
    """Venta fraccionada (ver ProductService.create_product): no se recorta."""
    assert format_money_milli(1125) == "1,125$"


def test_margen():
    p = Product(id=1, sku="FER-0001", name="TORNILLO HEX", cost_milli=to_milli("0.56"))
    p.prices = [ProductPrice(tier_id=1, price_milli=to_milli("1.50"))]
    assert p.margin_pct(1) == Decimal("167.86")


def test_margen_sin_costo_no_revienta():
    p = Product(id=1, sku="X", name="X", cost_milli=0)
    p.prices = [ProductPrice(tier_id=1, price_milli=1000)]
    assert p.margin_pct(1) is None


def test_totales_de_factura():
    inv = Invoice(
        id=None,
        display_number="FAC-000001",
        customer_name="Juan Perez",
        tax_rate=Decimal("16.00"),
    )
    inv.lines = [
        InvoiceLine(
            None, 1, "FER-0001", "TORNILLO HEX", "UNI", to_milli(3), to_cents("1.50")
        ),
        InvoiceLine(
            None, 2, "FER-0002", "TORNILLO", "UNI", to_milli(10), to_cents("0.25")
        ),
    ]
    assert inv.subtotal == Decimal("7.00")
    assert inv.tax == Decimal("1.12")
    assert inv.total == Decimal("8.12")


def test_cantidad_fraccionaria():
    """2,5 kg de clavos: por esto la cantidad va en milesimas y no en enteros."""
    line = InvoiceLine(
        None, 1, "FER-9", "CLAVOS", "KG", to_milli("2.5"), to_cents("4.00")
    )
    assert line.line_total == Decimal("10.00")


def test_bajo_stock():
    p = Product(
        id=1, sku="X", name="X", current_stock=to_milli(2), min_stock=to_milli(5)
    )
    assert p.is_low_stock


def test_parse_local_decimal_es_el_inverso_de_format_money():
    assert parse_local_decimal("1,50") == Decimal("1.50")
    assert format_money(to_cents(parse_local_decimal("1,50"))) == "1,50$"


def test_parse_local_decimal_con_punto_de_miles():
    """Hay precios de venta por encima de 1.000 en el catalogo real."""
    assert parse_local_decimal("1.210,00") == Decimal("1210.00")


def test_parse_local_decimal_sin_decimales():
    assert parse_local_decimal("445") == Decimal(445)


def test_parse_local_decimal_vacio_lanza():
    with pytest.raises(InvalidOperation):
        parse_local_decimal("")
