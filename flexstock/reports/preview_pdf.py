"""
PDF de "Vista previa": el borrador de una factura o de una cotizacion
todavia sin guardar, presentado como una COTIZACION para poder verlo en
pantalla (o mandarselo a un cliente que solo pregunta precios).

A diferencia de invoice_pdf.py / quotation_pdf.py, este documento:
- no tiene numero ni nada que lo ligue a un registro: el documento no
  existe en la base, y generarlo no numera, no mueve stock ni toca nada;
- recibe los montos YA convertidos (PreviewDocument): no sabe de tasas ni
  de facturas. Quien decide que se muestra en cada columna es
  PreviewService; aca solo se dibuja.

No sabe nada de sqlite3: es solo dibujo, igual que los otros PDF.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from reportlab.lib import colors
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, Spacer, Table, TableStyle

from ..domain.money import format_money, format_quantity
from .invoice_pdf import (
    ACENTO,
    ANCHO_UTIL,
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
)

# (base, iva, total) en centavos de una moneda
Totales = tuple[int, int, int]


@dataclass(frozen=True, slots=True)
class PreviewLine:
    """Una linea ya convertida. None = esa moneda no se muestra."""

    product_name: str
    quantity_milli: int
    unit_usd: int | None
    unit_bs: int | None
    subtotal_usd: int | None
    subtotal_bs: int | None


@dataclass(frozen=True, slots=True)
class PreviewDocument:
    customer_name: str
    tax_rate: Decimal
    lines: tuple[PreviewLine, ...]
    issued_at: datetime
    totals_usd: Totales | None
    totals_bs: Totales | None
    customer_tax_id: str | None = None
    customer_address: str | None = None
    currency_symbol: str = "$"
    # Desde Facturas se presenta como COTIZACION (un documento sin pagar);
    # desde Cotizaciones como NOTA DE ENTREGA, que es lo que ese modulo
    # es en realidad (ver quotation_pdf.py).
    title: str = "COTIZACIÓN"
    legend: str = "Documento no fiscal - pendiente de pago"
    # "Tasa BCV: 899,27 Bs por dólar", en el recuadro MONEDA. None = no se
    # imprime (una nota de entrega a tasa propia no la muestra: ver
    # PreviewService).
    rate_text: str | None = None


def render_preview(
    document: PreviewDocument,
    *,
    company_name: str,
    company_tax_id: str = "",
    company_phone: str = "",
    company_payment_info: str = "",
    company_footer: str = "",
    company_logo: bytes | None = None,
    company_payment_qr: bytes | None = None,
    export_dir: Path,
    **_otros_datos_de_la_tienda: str,
) -> Path:
    """
    Guarda export_dir/{Titulo}-{cliente}.pdf (ej. Cotizacion-Juan.pdf) y
    devuelve la ruta. El nombre del archivo es lo primero que se ve en el
    visor de PDF, asi que no dice "factura" ni "vista previa".
    Regenerarla para el mismo cliente sobrescribe la anterior.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    # Sin tildes en el nombre del archivo ("Cotizacion-..."): algunos
    # programas de correo y pendrives viejos se llevan mal con ellas.
    prefijo = _apto_para_nombre(_sin_tildes(document.title.capitalize()))
    filepath = export_dir / f"{prefijo}-{_apto_para_nombre(document.customer_name)}.pdf"

    doc = documento(filepath)
    doc.title = document.title.capitalize()
    tienda = _otros_datos_de_la_tienda
    elementos = [
        cabecera(
            _encabezado_empresa(
                company_name,
                company_tax_id,
                company_phone,
                tienda.get("company_address", ""),
                company_logo,
                tienda.get("company_email", ""),
                tienda.get("company_legal_name", ""),
                tienda.get("company_web", ""),
            )[0],
            caja_documento(
                document.title,
                "",
                f"{document.issued_at:%d/%m/%Y}",
                f'<font color="#C92A2A"><b>{escape(document.legend.upper())}</b></font>',
            ),
        ),
        Spacer(1, 0.45 * cm),
        fila_de_recuadros(
            ("CLIENTE", _datos_cliente(document)),
            ("MONEDA", _recuadro_moneda(document)),
        ),
        Spacer(1, 0.45 * cm),
        _tabla_lineas(document),
        Spacer(1, 0.3 * cm),
        _tabla_totales(document),
        *_bloque_pago_y_pie(company_payment_info, company_footer, company_payment_qr),
    ]
    pie = pie_de_pagina(aviso=document.legend)
    doc.build(elementos, onFirstPage=pie, onLaterPages=pie)
    return filepath


def _sin_tildes(texto: str) -> str:
    return "".join(
        c
        for c in unicodedata.normalize("NFD", texto)
        if unicodedata.category(c) != "Mn"
    )


def _apto_para_nombre(texto: str) -> str:
    """'Juan Perez / C.A.' -> 'Juan-Perez-C.A.': un nombre de archivo que
    sirve igual en Windows y en Linux (sin barras, comillas, dos puntos...)."""
    limpio = re.sub(r"[^\w.\-]+", "-", texto.strip(), flags=re.UNICODE).strip("-.")
    return limpio[:60] or "cliente"


def _recuadro_moneda(document: PreviewDocument) -> list:
    usd, bs = document.totals_usd is not None, document.totals_bs is not None
    if usd and bs:
        texto = "Dólares ($) y bolívares (Bs)"
    elif bs:
        texto = "Bolívares (Bs)"
    else:
        texto = "Dólares ($)"
    elementos = [Paragraph(f"<b>{texto}</b>", _normal)]
    if document.rate_text:
        elementos.append(Paragraph(escape(document.rate_text), _normal))
    return elementos


def _datos_cliente(document: PreviewDocument) -> list:
    elementos = [Paragraph(f"<b>{escape(document.customer_name)}</b>", _normal)]
    if document.customer_tax_id:
        elementos.append(
            Paragraph(f"Cédula/RIF: {escape(document.customer_tax_id)}", _pequeno)
        )
    if document.customer_address:
        elementos.append(
            Paragraph(f"Dirección: {escape(document.customer_address)}", _pequeno)
        )
    return elementos


def _tabla_lineas(document: PreviewDocument) -> Table:
    usd, bs = document.totals_usd is not None, document.totals_bs is not None
    simbolo = document.currency_symbol

    encabezado = ["#", "Descripción", "Cant."]
    anchos = [0.8 * cm, None, 1.4 * cm]
    if usd:
        encabezado.append("Precio unit. $")
        anchos.append(2.5 * cm)
    if bs:
        encabezado.append("Precio unit. Bs")
        anchos.append(3.1 * cm)
    if usd:
        encabezado.append("Importe $")
        anchos.append(2.4 * cm)
    if bs:
        encabezado.append("Importe Bs")
        anchos.append(3.1 * cm)
    anchos[1] = ANCHO_UTIL - sum(a for a in anchos if a is not None)

    filas = []
    for indice, line in enumerate(document.lines):
        fila = [
            str(indice + 1),
            Paragraph(escape(line.product_name), _normal),
            format_quantity(line.quantity_milli),
        ]
        if usd:
            fila.append(format_money(line.unit_usd or 0, simbolo))
        if bs:
            fila.append(format_money(line.unit_bs or 0, " Bs"))
        if usd:
            fila.append(format_money(line.subtotal_usd or 0, simbolo))
        if bs:
            fila.append(format_money(line.subtotal_bs or 0, " Bs"))
        filas.append(fila)
    return tabla_de_lineas(encabezado, filas, anchos)


def _tabla_totales(document: PreviewDocument) -> Table:
    """Una columna por moneda que se muestre; filas base, IVA y total."""
    simbolo = document.currency_symbol
    columnas = []  # (encabezado, totales, sufijo)
    if document.totals_usd is not None:
        columnas.append(("$", document.totals_usd, simbolo))
    if document.totals_bs is not None:
        columnas.append(("Bs", document.totals_bs, " Bs"))

    filas = [[""] + [c[0] for c in columnas]]
    filas.append(["Subtotal"] + [format_money(t[0], suf) for _e, t, suf in columnas])
    if document.tax_rate > 0:
        filas.append(
            [f"IVA {porcentaje(document.tax_rate)}"]
            + [format_money(t[1], suf) for _e, t, suf in columnas]
        )
    filas.append(["TOTAL"] + [format_money(t[2], suf) for _e, t, suf in columnas])

    tabla = Table(
        filas, colWidths=[3.2 * cm] + [3.8 * cm] * len(columnas), hAlign="RIGHT"
    )
    tabla.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
                ("TEXTCOLOR", (0, 0), (-1, 0), colors.HexColor("#5B6573")),
                ("BACKGROUND", (0, -1), (-1, -1), ACENTO),
                ("TEXTCOLOR", (0, -1), (-1, -1), colors.white),
                ("FONTNAME", (0, -1), (-1, -1), "Helvetica-Bold"),
                ("FONTSIZE", (0, -1), (-1, -1), 11),
                ("TOPPADDING", (0, -1), (-1, -1), 7),
                ("BOTTOMPADDING", (0, -1), (-1, -1), 7),
            ]
        )
    )
    return tabla
