"""
Una factura expresada en OTRA moneda (bolivares), linea por linea.

Vive en domain/ porque la usan tres lugares que tienen que dar
EXACTAMENTE los mismos numeros: la pantalla de Nueva factura, el detalle
de una factura guardada y el PDF. Tres cuentas copiadas serian tres
lugares donde un centavo puede diferir.

La regla, la misma de las cotizaciones (ver Quotation.totals_in):

    cada linea se convierte SOLA (una sola vez, redondeada), su IVA se
    calcula sobre esa linea ya convertida, y los totales son la SUMA de
    las lineas. Asi el papel cuadra a simple vista: convertir solo el
    total en $ podia diferir por un centavo de la suma de las lineas.

No cambia la factura: la factura se guarda en $. Esto es una vista.
"""

from dataclasses import dataclass
from decimal import Decimal

from .entities import Invoice
from .money import convert_cents


@dataclass(frozen=True, slots=True)
class BsLine:
    """
    Centavos de UNA linea en la moneda convertida: el monto sin IVA, su
    IVA, el total y el precio de una unidad.
    """

    sin_iva: int
    iva: int
    total: int
    unitario: int = 0


@dataclass(frozen=True, slots=True)
class BsBreakdown:
    lines: tuple[BsLine, ...]
    subtotal: int
    tax: int
    total: int
    rate: Decimal


def breakdown(invoice: Invoice, factor: Decimal) -> BsBreakdown:
    """Convierte la factura multiplicando cada monto en $ por 'factor'
    (Bs por $, o cualquier otro factor: ver RateSnapshot.invoice_at_usd_bcv)."""
    lineas = []
    for line in invoice.lines:
        sin_iva = convert_cents(line.line_total_cents, factor)
        iva = round(sin_iva * invoice.tax_rate / 100)
        lineas.append(
            BsLine(
                sin_iva,
                iva,
                sin_iva + iva,
                convert_cents(line.unit_price_cents, factor),
            )
        )
    subtotal = sum(linea.sin_iva for linea in lineas)
    iva = sum(linea.iva for linea in lineas)
    return BsBreakdown(tuple(lineas), subtotal, iva, subtotal + iva, factor)


def catalog_total(invoice: Invoice) -> int:
    """
    El total de la factura al precio del CATALOGO, en centavos.

    Los montos en $ de una factura estan expresados a tasa BCV: con Modo
    avanzado el precio entro multiplicado por ref_rate / bs_rate (ver
    Regla 7), asi que deshacerlo es el camino de vuelta:

        catalogo = total x bs_rate / ref_rate

    Sin tasa de referencia (o sin la BCV) el total YA es el del catalogo.
    """
    if invoice.ref_rate is None or invoice.bs_rate is None:
        return invoice.total_cents
    return convert_cents(invoice.total_cents, invoice.bs_rate / invoice.ref_rate)
