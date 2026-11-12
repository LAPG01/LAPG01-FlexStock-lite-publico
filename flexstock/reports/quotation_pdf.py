"""
PDF de una nota de entrega (cotizacion). Mismo esqueleto Platypus que
invoice_pdf.py, con todos los montos en UNA moneda elegida al generar:
USD, Bs, o USD a tasa BCV.

Imprime los montos finales en la moneda elegida. En la version "$ BCV"
tambien imprime la tasa BCV usada.
"""

from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table

from ..domain.entities import Quotation
from ..domain.enums import QuoteCurrency
from ..domain.money import format_money, format_quantity
from .invoice_pdf import (
    ANCHO_UTIL,
    NO_FISCAL,
    _aviso_rojo,
    _bloque_pago_y_pie,
    _encabezado_empresa,
    _normal,
    _pequeno,
    cabecera,
    caja_documento,
    documento,
    fila_de_recuadros,
    pie_de_pagina,
    porcentaje,
    tabla_de_lineas,
    tabla_de_totales,
)

_SIMBOLO = {
    QuoteCurrency.USD: "$",
    QuoteCurrency.BS: " Bs",
    QuoteCurrency.USD_BCV: "$",
}
# El nombre de la moneda, en grande en el recuadro "MONEDA": UNA sola por
# papel, dicha arriba, para que nadie dude si un monto son $ o Bs.
_LEYENDA = {
    QuoteCurrency.USD: "Dólares de lista ($)",
    QuoteCurrency.BS: "Bolívares (Bs)",
    QuoteCurrency.USD_BCV: "Dólares a tasa BCV ($)",
}
_SUFIJO_ARCHIVO = {
    QuoteCurrency.USD: "USD",
    QuoteCurrency.BS: "Bs",
    QuoteCurrency.USD_BCV: "BCV",
}


def render_quotation(
    quotation: Quotation,
    currency: QuoteCurrency,
    *,
    company_name: str,
    company_tax_id: str = "",
    company_phone: str = "",
    company_address: str = "",
    company_email: str = "",
    company_legal_name: str = "",
    company_web: str = "",
    company_payment_info: str = "",
    company_footer: str = "",
    company_logo: bytes | None = None,
    company_payment_qr: bytes | None = None,
    export_dir: Path,
) -> Path:
    """Guarda export_dir/{NE-000001}-{USD|Bs|BCV}.pdf y devuelve la ruta."""
    export_dir.mkdir(parents=True, exist_ok=True)
    filepath = (
        export_dir / f"{quotation.display_number}-{_SUFIJO_ARCHIVO[currency]}.pdf"
    )

    doc = documento(filepath)
    fecha = quotation.issued_at.strftime("%d/%m/%Y") if quotation.issued_at else ""
    elementos = [
        cabecera(
            _encabezado_empresa(
                company_name,
                company_tax_id,
                company_phone,
                company_address,
                company_logo,
                company_email,
                company_legal_name,
                company_web,
            )[0],
            caja_documento(
                "NOTA DE ENTREGA",
                quotation.display_number,
                fecha,
                NO_FISCAL,
            ),
        ),
        Spacer(1, 0.45 * cm),
        fila_de_recuadros(
            ("CLIENTE", _datos_cliente(quotation)),
            ("MONEDA", _encabezado_documento(quotation, currency)),
        ),
        Spacer(1, 0.45 * cm),
        *_aviso_anulada(quotation),
        _tabla_lineas(quotation, currency),
        Spacer(1, 0.3 * cm),
        _tabla_totales(quotation, currency),
        *_bloque_pago_y_pie(company_payment_info, company_footer, company_payment_qr),
    ]
    pie = pie_de_pagina(anulada=quotation.is_cancelled, aviso="Documento no fiscal")
    doc.build(elementos, onFirstPage=pie, onLaterPages=pie)
    return filepath


def _encabezado_documento(quotation: Quotation, currency: QuoteCurrency) -> list:
    """El recuadro "MONEDA": en que estan los montos y, si hubo conversion,
    con que tasa (las congeladas al crear la nota)."""
    elementos = [Paragraph(f"<b>{_LEYENDA[currency]}</b>", _normal)]
    if currency is QuoteCurrency.USD_BCV:
        tasa = str(quotation.bcv_rate).replace(".", ",")
        elementos.append(Paragraph(f"Tasa BCV: {tasa} Bs por dólar", _pequeno))
    return elementos


def _aviso_anulada(quotation: Quotation) -> list:
    if not quotation.is_cancelled:
        return []
    motivo = f": {escape(quotation.cancel_reason)}" if quotation.cancel_reason else ""
    return [Paragraph(f"NOTA ANULADA{motivo}", _aviso_rojo), Spacer(1, 0.25 * cm)]


def _datos_cliente(quotation: Quotation) -> list:
    elementos = [Paragraph(f"<b>{escape(quotation.customer_name)}</b>", _normal)]
    if quotation.customer_tax_id:
        elementos.append(
            Paragraph(f"Cédula/RIF: {escape(quotation.customer_tax_id)}", _pequeno)
        )
    if quotation.customer_address:
        elementos.append(
            Paragraph(f"Dirección: {escape(quotation.customer_address)}", _pequeno)
        )
    return elementos


def _tabla_lineas(quotation: Quotation, currency: QuoteCurrency) -> Table:
    simbolo = _SIMBOLO[currency]
    filas = []
    for indice, line in enumerate(quotation.lines):
        filas.append(
            [
                str(indice + 1),
                Paragraph(escape(line.product_name), _normal),
                format_quantity(line.quantity_milli),
                format_money(quotation.unit_price_in(line, currency), simbolo),
                format_money(quotation.line_total_in(line, currency), simbolo),
            ]
        )
    monto = 3.1 * cm if currency is QuoteCurrency.BS else 2.6 * cm
    anchos = [0.8 * cm, ANCHO_UTIL - 0.8 * cm - 1.8 * cm - 2 * monto, 1.8 * cm]
    anchos += [monto, monto]
    return tabla_de_lineas(
        ["#", "Descripción", "Cant.", "Precio unit.", "Importe"], filas, anchos
    )


def _tabla_totales(quotation: Quotation, currency: QuoteCurrency) -> Table:
    simbolo = _SIMBOLO[currency]
    subtotal, iva, total = quotation.totals_in(currency)
    filas = [["Subtotal", format_money(subtotal, simbolo)]]
    if quotation.tax_rate > 0:
        filas.append(
            [f"IVA {porcentaje(quotation.tax_rate)}", format_money(iva, simbolo)]
        )
    filas.append(["TOTAL", format_money(total, simbolo)])
    return tabla_de_totales(filas)
