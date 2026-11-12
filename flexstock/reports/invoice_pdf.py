"""
PDF de una factura. Unico archivo del proyecto que importa reportlab
(regla de arquitectura: 'reports/ - PDF con ReportLab').

Usa Platypus (SimpleDocTemplate + Table + Paragraph), no pdfgen.canvas
a mano: con canvas hay que calcular uno mismo cuando cortar pagina: con
Platypus el motor pagina solo si una factura tiene muchas lineas, que
es exactamente el caso que no se puede predecir de antemano.

No sabe nada de sqlite3 ni de flexstock.data: recibe un Invoice ya
armado (con sus InvoiceLine) y strings sueltos para el encabezado de
empresa. Quien orquesta la lectura de la base es BillingService.
"""

import io
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from xml.sax.saxutils import escape

from PIL import Image as PILImage  # solo para medir el logo; la trae reportlab
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    Image,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from ..domain.bs_breakdown import BsBreakdown
from ..domain.entities import Invoice
from ..domain.enums import InvoiceStatus, PaymentMethod
from ..domain.money import format_money, format_quantity, format_rate

_ESTADO_DISPLAY = {
    InvoiceStatus.ISSUED: "CONFIRMADA",
    InvoiceStatus.CANCELLED: "ANULADA",
}

_FORMA_PAGO = {
    PaymentMethod.EFECTIVO_BS: "Efectivo en bolívares",
    PaymentMethod.EFECTIVO_USD: "Efectivo en dólares",
    PaymentMethod.TRANSFERENCIA: "Transferencia / Pago móvil",
    PaymentMethod.TARJETA: "Tarjeta (punto de venta)",
    PaymentMethod.ZELLE: "Zelle",
}

# ------------------------------------------------------------
# Estilo: un solo color de acento (azul oscuro) y grises; nada mas. Lo
# comparten la factura, la nota de entrega y la vista previa, para que los
# tres papeles se vean de la misma tienda.
# ------------------------------------------------------------
ACENTO = colors.HexColor("#1F3A5F")
ACENTO_SUAVE = colors.HexColor("#EEF2F7")
BORDE = colors.HexColor("#C9D3E0")
FRANJA = colors.HexColor("#F6F8FB")
GRIS = colors.HexColor("#5B6573")
ROJO = colors.HexColor("#C92A2A")

# Ancho util de una hoja carta con 1,8 cm de margen a cada lado.
MARGEN = 1.8 * cm
ANCHO_UTIL = letter[0] - 2 * MARGEN

_styles = getSampleStyleSheet()
_normal = ParagraphStyle("Cuerpo", parent=_styles["Normal"], fontSize=9.5, leading=12)
_derecha = ParagraphStyle("Derecha", parent=_normal, alignment=2)
_titulo = ParagraphStyle(
    "Empresa", parent=_normal, fontName="Helvetica-Bold", fontSize=15, leading=18
)
_pequeno = ParagraphStyle(
    "Pequeno", parent=_normal, fontSize=8.5, leading=10.5, textColor=GRIS
)
_etiqueta = ParagraphStyle(
    "Etiqueta",
    parent=_normal,
    fontName="Helvetica-Bold",
    fontSize=7.5,
    leading=9,
    textColor=GRIS,
)
_titulo_doc = ParagraphStyle(
    "TituloDocumento",
    parent=_normal,
    fontName="Helvetica-Bold",
    fontSize=17,
    leading=20,
    textColor=ACENTO,
    alignment=2,
)
_numero_doc = ParagraphStyle(
    "NumeroDocumento",
    parent=_normal,
    fontName="Helvetica-Bold",
    fontSize=11,
    leading=14,
    alignment=2,
)
_aviso_rojo = ParagraphStyle(
    "AvisoRojo", parent=_normal, fontName="Helvetica-Bold", textColor=ROJO
)


def render_invoice(
    invoice: Invoice,
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
    currency_symbol: str = "$",
    bs: BsBreakdown | None = None,
    export_dir: Path,
) -> Path:
    """
    Genera el PDF de 'invoice' y lo guarda en export_dir/{display_number}.pdf.
    Devuelve la ruta del archivo. Reimprimir la misma factura sobrescribe
    el archivo anterior con el mismo contenido (regla 5: reimprimir no
    cambia lo que dice el documento, asi que sobrescribir es seguro).

    'bs': la factura ya convertida a bolívares (ver BillingService.
    generate_pdf, que la arma con la tasa guardada en la factura). Con
    ella TODOS los montos del papel salen en Bs y no aparece ningun $.
    Sin ella (None) se imprime en 'currency_symbol', como siempre.

    De arriba abajo: la tienda y la caja del documento (tipo, numero,
    fecha); el cliente y la moneda, cada uno en su recuadro; las lineas;
    los totales con el TOTAL resaltado; datos de pago y condiciones; y al
    pie de cada hoja la fecha de impresion y el numero de pagina.
    """
    export_dir.mkdir(parents=True, exist_ok=True)
    filepath = export_dir / f"{invoice.display_number}.pdf"

    doc = documento(filepath)
    fecha = invoice.issued_at.strftime("%d/%m/%Y") if invoice.issued_at else ""
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
            caja_documento("FACTURA", invoice.display_number, fecha),
        ),
        Spacer(1, 0.45 * cm),
        fila_de_recuadros(
            ("CLIENTE", _datos_cliente(invoice)),
            ("MONEDA Y PAGO", _encabezado_factura(invoice, bs)),
        ),
        Spacer(1, 0.45 * cm),
        *_aviso_anulada(invoice),
        _tabla_lineas(invoice, currency_symbol, bs),
        Spacer(1, 0.3 * cm),
        _tabla_totales(invoice, currency_symbol, bs),
        *_bloque_pago_y_pie(company_payment_info, company_footer, company_payment_qr),
    ]
    pie = pie_de_pagina(anulada=invoice.is_cancelled)
    doc.build(elementos, onFirstPage=pie, onLaterPages=pie)
    return filepath


# ------------------------------------------------------------
# Piezas comunes (las usan tambien quotation_pdf y preview_pdf)
# ------------------------------------------------------------
def documento(filepath: Path) -> SimpleDocTemplate:
    return SimpleDocTemplate(
        str(filepath),  # reportlab exige un str: no acepta un Path
        pagesize=letter,
        leftMargin=MARGEN,
        rightMargin=MARGEN,
        topMargin=1.5 * cm,
        bottomMargin=1.8 * cm,  # lugar para el pie de pagina
    )


def cabecera(empresa, caja) -> Table:
    """La tienda a la izquierda, la caja del documento a la derecha y una
    linea de acento debajo."""
    tabla = Table([[empresa, caja]], colWidths=[ANCHO_UTIL - 6.2 * cm, 6.2 * cm])
    tabla.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
                ("LINEBELOW", (0, 0), (-1, 0), 1.5, ACENTO),
            ]
        )
    )
    return tabla


def caja_documento(tipo: str, numero: str, fecha: str, *extra: str) -> list:
    """FACTURA / NOTA DE ENTREGA en grande, el numero y la fecha."""
    caja = [Paragraph(tipo, _titulo_doc)]
    if numero:  # la vista previa no tiene numero: no existe en la base
        caja.append(Paragraph(f"N° {escape(numero)}", _numero_doc))
    if fecha:
        caja.append(Paragraph(f"Fecha: {fecha}", _derecha))
    caja += [Paragraph(texto, _derecha) for texto in extra]
    return caja


def fila_de_recuadros(*recuadros: tuple[str, list]) -> Table:
    """Recuadros lado a lado (CLIENTE | MONEDA...), cada uno con su etiqueta
    chica arriba. Mismo alto: el mas largo manda."""
    separacion = 0.4 * cm
    ancho = (ANCHO_UTIL - separacion * (len(recuadros) - 1)) / len(recuadros)
    celdas, anchos = [], []
    for indice, (titulo, contenido) in enumerate(recuadros):
        if indice:
            celdas.append("")
            anchos.append(separacion)
        celdas.append([Paragraph(titulo, _etiqueta), Spacer(1, 3), *contenido])
        anchos.append(ancho)
    tabla = Table([celdas], colWidths=anchos)
    estilo = [
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]
    for columna in range(0, len(celdas), 2):
        estilo += [
            ("BOX", (columna, 0), (columna, 0), 0.75, BORDE),
            ("BACKGROUND", (columna, 0), (columna, 0), colors.white),
        ]
    tabla.setStyle(TableStyle(estilo))
    return tabla


def tabla_de_lineas(
    encabezado: list[str], filas: list[list], anchos: list[float]
) -> Table:
    """
    La tabla de productos: encabezado en el color de acento, filas con una
    franja suave alterna y una linea fina debajo de cada una (sin cuadricula
    completa, que se ve recargada). repeatRows=1: si las lineas pasan a otra
    hoja, el encabezado se repite arriba.
    """
    tabla = Table([encabezado, *filas], colWidths=anchos, repeatRows=1)
    estilo = [
        ("BACKGROUND", (0, 0), (-1, 0), ACENTO),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("ALIGN", (2, 0), (-1, -1), "RIGHT"),
        ("ALIGN", (0, 0), (0, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
        ("LINEBELOW", (0, 1), (-1, -1), 0.5, BORDE),
    ]
    for fila in range(2, len(filas) + 1, 2):
        estilo.append(("BACKGROUND", (0, fila), (-1, fila), FRANJA))
    tabla.setStyle(TableStyle(estilo))
    return tabla


def tabla_de_totales(filas: list[list[str]]) -> Table:
    """Subtotal, IVA... y la ultima fila (TOTAL) en el color de acento."""
    tabla = Table(filas, colWidths=[3.6 * cm, 4.2 * cm], hAlign="RIGHT")
    tabla.setStyle(
        TableStyle(
            [
                ("ALIGN", (0, 0), (-1, -1), "RIGHT"),
                ("FONTSIZE", (0, 0), (-1, -1), 9.5),
                ("TOPPADDING", (0, 0), (-1, -1), 4),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
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


def porcentaje(tasa: Decimal) -> str:
    """Decimal('16.00') -> '16%', Decimal('12.5') -> '12,5%'."""
    texto = format(tasa.normalize(), "f").replace(".", ",")
    return f"{texto}%"


def pie_de_pagina(*, anulada: bool = False, aviso: str = ""):
    """
    Lo que se dibuja en CADA hoja, fuera del flujo: la fecha de impresion,
    el numero de pagina y, si el documento esta anulado, un sello "ANULADA"
    cruzado que no deja usarlo como si valiera.
    """
    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")  # noqa: DTZ005 - hora local, igual que el resto del esquema

    def _dibujar(lienzo, doc) -> None:
        lienzo.saveState()
        lienzo.setStrokeColor(BORDE)
        lienzo.setLineWidth(0.5)
        lienzo.line(MARGEN, 1.3 * cm, letter[0] - MARGEN, 1.3 * cm)
        lienzo.setFont("Helvetica", 7.5)
        lienzo.setFillColor(GRIS)
        lienzo.drawString(MARGEN, 0.9 * cm, f"Impreso el {ahora}")
        if aviso:
            lienzo.drawCentredString(letter[0] / 2, 0.9 * cm, aviso)
        lienzo.drawRightString(letter[0] - MARGEN, 0.9 * cm, f"Página {doc.page}")
        if anulada:
            lienzo.setFillColor(ROJO)
            lienzo.setFillAlpha(0.12)
            lienzo.setFont("Helvetica-Bold", 90)
            lienzo.translate(letter[0] / 2, letter[1] / 2)
            lienzo.rotate(35)
            lienzo.drawCentredString(0, 0, "ANULADA")
        lienzo.restoreState()

    return _dibujar


# El sello de los papeles que NO son una factura (nota de entrega, vista
# previa): en rojo, debajo del numero, para que nadie los tome por una.
NO_FISCAL = '<font color="#C92A2A"><b>DOCUMENTO NO FISCAL</b></font>'

_LOGO_ANCHO = 2.5 * cm
_LOGO_ALTO = 1.5 * cm


def _logo_del_encabezado(imagen: bytes | None):
    """
    El logo de la tienda escalado para entrar en su recuadro SIN
    deformarse: se toma la escala mas chica de las dos (ancho y alto),
    asi un logo apaisado toca los bordes laterales y uno vertical los
    de arriba y abajo, pero nunca se estira.

    Sin logo cargado devuelve una celda vacia, no un recuadro con la
    palabra "[LOGO]" como antes: ese cartel era un recordatorio para el
    programador, y quedaba impreso en la factura que recibe el cliente.

    Una imagen ilegible tampoco frena nada: la factura sale sin logo.
    Imprimir es lo importante; el logo es decoracion.
    """
    if not imagen:
        return ""

    try:
        with PILImage.open(io.BytesIO(imagen)) as abierta:
            ancho_px, alto_px = abierta.size
        if not ancho_px or not alto_px:
            return ""
        escala = min(_LOGO_ANCHO / ancho_px, _LOGO_ALTO / alto_px)
        return Image(
            io.BytesIO(imagen), width=ancho_px * escala, height=alto_px * escala
        )
    except Exception:  # noqa: BLE001 - ver docstring: nunca frenar la impresion
        return ""


def _encabezado_empresa(
    name: str,
    tax_id: str,
    phone: str,
    address: str = "",
    logo_bytes: bytes | None = None,
    email: str = "",
    legal_name: str = "",
    web: str = "",
) -> list:
    logo = _logo_del_encabezado(logo_bytes)

    datos = [Paragraph(escape(name), _titulo)]
    # La razon social es el nombre LEGAL: solo se imprime si existe y es
    # distinta del nombre comercial de arriba (no repetir lo mismo dos veces).
    if legal_name and legal_name.strip().lower() != name.strip().lower():
        datos.append(Paragraph(f"Razón social: {escape(legal_name)}", _pequeno))
    if address:
        datos.append(Paragraph(escape(address), _pequeno))
    # RIF primero: es el dato que hace valida la factura, el telefono
    # es de contacto. Cada uno sale solo si esta cargado, para no dejar
    # un "RIF:" pelado en el papel.
    pie = " | ".join(
        p
        for p in (f"RIF: {tax_id}" if tax_id else "", f"Tel: {phone}" if phone else "")
        if p
    )
    if pie:
        datos.append(Paragraph(pie, _pequeno))
    contacto_web = " | ".join(p for p in (email, web) if p)
    if contacto_web:
        datos.append(Paragraph(escape(contacto_web), _pequeno))

    # Sin logo, el texto arranca en el borde: no queda un hueco vacio.
    if logo:
        tabla = Table([[logo, datos]], colWidths=[2.9 * cm, None])
    else:
        tabla = Table([["", datos]], colWidths=[0.01 * cm, None])
    tabla.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [tabla]


def _encabezado_factura(invoice: Invoice, bs: BsBreakdown | None = None) -> list:
    """
    El recuadro "MONEDA Y PAGO": en que moneda estan TODOS los montos de
    la hoja (y con que tasa se pasaron a bolívares), y como se pago. Una
    sola moneda por papel, dicha arriba y en grande, para que nadie tenga
    que adivinar si un "1.500" son dolares o bolívares.
    """
    if bs is not None:
        # La tasa a la vista y en letra normal: es la que quedo guardada en
        # la factura (invoices.bs_rate), la BCV del dia en que se confirmo.
        # Solo una factura de antes del 21/09/2026 pudo guardarse con la
        # tasa de referencia: esa dice "de cambio" (Invoice.bs_rate_is_bcv).
        nombre = "Tasa BCV" if invoice.bs_rate_is_bcv else "Tasa de cambio"
        elementos = [
            Paragraph("<b>Bolívares (Bs)</b>", _normal),
            Paragraph(f"{nombre}: {format_rate(bs.rate)} Bs por dólar", _normal),
        ]
    else:
        elementos = [Paragraph("<b>Dólares ($)</b>", _normal)]
    forma = _FORMA_PAGO.get(invoice.payment_method) if invoice.payment_method else None
    if forma:
        elementos.append(Paragraph(f"Forma de pago: {forma}", _pequeno))
    return elementos


def _aviso_anulada(invoice: Invoice) -> list:
    """Una factura anulada lo dice arriba de las lineas, con su motivo."""
    if not invoice.is_cancelled:
        return []
    motivo = f": {escape(invoice.cancel_reason)}" if invoice.cancel_reason else ""
    return [
        Paragraph(f"FACTURA ANULADA{motivo}", _aviso_rojo),
        Spacer(1, 0.25 * cm),
    ]


def _datos_cliente(invoice: Invoice) -> list:
    elementos = [Paragraph(f"<b>{escape(invoice.customer_name)}</b>", _normal)]
    if invoice.customer_tax_id:
        elementos.append(
            Paragraph(f"Cédula/RIF: {escape(invoice.customer_tax_id)}", _pequeno)
        )
    if invoice.customer_address:
        elementos.append(
            Paragraph(f"Dirección: {escape(invoice.customer_address)}", _pequeno)
        )
    return elementos


def _tabla_lineas(
    invoice: Invoice, currency_symbol: str, bs: BsBreakdown | None = None
) -> Table:
    """
    '#', producto, cantidad, precio unitario, IVA (solo si la factura lo
    cobra: una columna de ceros solo confunde) e importe.
    """
    con_iva = invoice.tax_rate > 0
    encabezado = ["#", "Descripción", "Cant.", "Precio unit."]
    encabezado += ["IVA"] if con_iva else []
    encabezado += ["Importe"]
    filas = []
    for indice, line in enumerate(invoice.lines):
        if bs is not None:
            # Todo en Bs, de la misma cuenta que la pantalla (ver
            # domain/bs_breakdown.py): cada linea convertida UNA vez.
            linea_bs = bs.lines[indice]
            unitario = format_money(linea_bs.unitario, " Bs")
            iva_linea = format_money(linea_bs.iva, " Bs")
            total_linea = format_money(linea_bs.sin_iva, " Bs")
        else:
            # IVA de linea: solo para desglosar en el papel. La cifra que
            # cuadra con la contabilidad es invoice.tax_cents (un solo
            # redondeo sobre el subtotal completo, no la suma de estas).
            iva = round(line.line_total_cents * invoice.tax_rate / 100)
            unitario = format_money(line.unit_price_cents, currency_symbol)
            iva_linea = format_money(iva, currency_symbol)
            total_linea = format_money(line.line_total_cents, currency_symbol)
        cantidad = format_quantity(line.quantity_milli)
        if line.unit and line.unit != "UNI":
            cantidad = f"{cantidad} {line.unit}"
        filas.append(
            [
                str(indice + 1),
                # Paragraph, no el string suelto: un nombre largo (el
                # catalogo tiene varios de 40+ caracteres) baja de linea en
                # vez de pasarse encima de la columna de al lado.
                Paragraph(escape(line.product_name), _normal),
                cantidad,
                unitario,
                *([iva_linea] if con_iva else []),
                total_linea,
            ]
        )

    # Montos mas anchos en Bs ("107.184,00 Bs"): se le saca a Descripcion,
    # que baja de linea.
    monto = 3.1 * cm if bs is not None else 2.6 * cm
    fijas = 0.8 * cm + 1.8 * cm + monto * (3 if con_iva else 2)
    anchos = [0.8 * cm, ANCHO_UTIL - fijas, 1.8 * cm, monto]
    anchos += [monto] if con_iva else []
    anchos += [monto]
    return tabla_de_lineas(encabezado, filas, anchos)


def _tabla_totales(
    invoice: Invoice, currency_symbol: str, bs: BsBreakdown | None = None
) -> Table:
    if bs is not None:
        subtotal, iva, total, simbolo = bs.subtotal, bs.tax, bs.total, " Bs"
    else:
        subtotal = invoice.subtotal_cents
        iva = invoice.tax_cents
        total = invoice.total_cents
        simbolo = currency_symbol
    return tabla_de_totales(
        [
            ["Subtotal", format_money(subtotal, simbolo)],
            [f"IVA {porcentaje(invoice.tax_rate)}", format_money(iva, simbolo)],
            ["TOTAL", format_money(total, simbolo)],
        ]
    )


def _texto_en_renglones(texto: str) -> str:
    """Texto libre del usuario -> markup de reportlab: sin que un '&' o un
    '<' lo rompan, y respetando los saltos de linea que escribio."""
    return "<br/>".join(escape(linea) for linea in texto.strip().splitlines())


_QR_LADO = 3.2 * cm  # impreso: se escanea con el telefono sin acercarse
_qr_pie = ParagraphStyle("QrPie", parent=_pequeno, alignment=1)  # centrado


def _qr_de_pago(imagen: bytes | None):
    """
    El QR de pago escalado a _QR_LADO sin deformarse (una foto apaisada
    queda mas baja, nunca estirada). Ilegible o ausente: None, y el
    documento sale igual sin QR - mismo criterio que el logo.
    """
    if not imagen:
        return None
    try:
        with PILImage.open(io.BytesIO(imagen)) as abierta:
            ancho_px, alto_px = abierta.size
        if not ancho_px or not alto_px:
            return None
        escala = min(_QR_LADO / ancho_px, _QR_LADO / alto_px)
        return Image(
            io.BytesIO(imagen), width=ancho_px * escala, height=alto_px * escala
        )
    except Exception:  # noqa: BLE001 - nunca frenar la impresion por el QR
        return None


def _contenido_pago(payment_info: str, qr) -> list:
    """El texto de pago y, a su derecha, el QR. Solo uno de los dos: ese."""
    texto = (
        Paragraph(_texto_en_renglones(payment_info), _normal)
        if payment_info.strip()
        else None
    )
    if qr is None:
        return [texto]
    # Debajo del QR, que se entienda para que esta (no todos lo deducen).
    qr = [qr, Paragraph("Escanea para pagar", _qr_pie)]
    if texto is None:
        return qr
    tabla = Table([[texto, qr]], colWidths=[None, _QR_LADO + 0.2 * cm])
    tabla.setStyle(
        TableStyle(
            [
                ("VALIGN", (0, 0), (-1, -1), "TOP"),
                ("ALIGN", (1, 0), (1, 0), "RIGHT"),
                ("LEFTPADDING", (0, 0), (-1, -1), 0),
                ("RIGHTPADDING", (0, 0), (-1, -1), 0),
                ("TOPPADDING", (0, 0), (-1, -1), 0),
                ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
            ]
        )
    )
    return [tabla]


def _bloque_pago_y_pie(
    payment_info: str, footer_text: str, payment_qr: bytes | None = None
) -> list:
    """
    Datos de pago y condiciones (Configuracion > Tienda), al final del
    documento, cada uno en su recuadro y lado a lado. Con QR de pago, va
    dentro de "DATOS DE PAGO", a la derecha del texto: el cliente lo
    escanea con el telefono y paga sin tipear numeros. Vacio si no hay
    nada cargado: el papel termina en los totales.
    """
    recuadros = []
    qr = _qr_de_pago(payment_qr)
    if payment_info.strip() or qr is not None:
        recuadros.append(("DATOS DE PAGO", _contenido_pago(payment_info, qr)))
    if footer_text.strip():
        recuadros.append(
            ("CONDICIONES", [Paragraph(_texto_en_renglones(footer_text), _normal)])
        )
    if not recuadros:
        return []
    return [Spacer(1, 0.6 * cm), fila_de_recuadros(*recuadros)]
