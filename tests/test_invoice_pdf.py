"""
Tests de reports/invoice_pdf.py. Sin inspeccionar el layout interno del
PDF (eso seria fragil y no es lo que importa): solo que el archivo se
genera donde se espera, con nombre y contenido validos.

    pytest -q
"""

import sqlite3
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.domain.entities import Customer, Invoice, InvoiceLine
from flexstock.reports.invoice_pdf import render_invoice


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


def _factura_confirmada(conn, *, direccion="Calle Falsa 123"):
    customers = CustomerRepository(conn)
    cliente = Customer(
        id=None,
        sku="CLI-0001",
        name="Juan Perez",
        tax_id="V-12345678",
        address=direccion,
    )
    customers.create(cliente)

    from flexstock.data.connection import transaction

    invoices = InvoiceRepository(conn)
    factura = Invoice(
        id=None,
        display_number="",
        customer_name=cliente.name,
        tax_rate=Decimal("16.00"),
        customer_id=cliente.id,
        customer_tax_id=cliente.tax_id,
        customer_address=cliente.address,
        lines=[
            InvoiceLine(
                id=None,
                product_id=None,
                product_sku="FER-0001",
                product_name='Tornillo 1/2"',
                unit="UNI",
                quantity_milli=3000,
                unit_price_cents=150,
            )
        ],
    )
    with transaction(conn):
        invoices.create(factura)
    return factura


def test_render_invoice_genera_pdf_valido(conn, tmp_path):
    factura = _factura_confirmada(conn)

    ruta = render_invoice(
        factura,
        company_name="Ferreteria El Tornillo",
        company_tax_id="J-12345678-9",
        company_phone="0212-1234567",
        export_dir=tmp_path,
    )

    assert ruta == tmp_path / f"{factura.display_number}.pdf"
    assert ruta.exists()
    assert ruta.stat().st_size > 0
    assert ruta.read_bytes()[:4] == b"%PDF"


def test_render_invoice_con_simbolo_de_moneda_distinto(conn, tmp_path):
    """
    No inspecciona el contenido comprimido del PDF (mismo criterio que
    el resto de este archivo): solo confirma que un simbolo distinto de
    '$' no rompe el render.
    """
    factura = _factura_confirmada(conn)

    ruta = render_invoice(
        factura, company_name="Ferreteria", currency_symbol="Bs", export_dir=tmp_path
    )

    assert ruta.exists()
    assert ruta.read_bytes()[:4] == b"%PDF"


def test_render_invoice_crea_export_dir_si_no_existe(conn, tmp_path):
    factura = _factura_confirmada(conn)
    destino = tmp_path / "no-existe-todavia"

    ruta = render_invoice(factura, company_name="Ferreteria", export_dir=destino)

    assert ruta.exists()


def test_render_invoice_con_nombre_de_producto_largo_no_falla(conn, tmp_path):
    """
    Regresion: antes el nombre del producto era un string suelto en la
    tabla (sin Paragraph), asi que uno largo se pasaba del ancho fijo de
    la columna en vez de bajar de linea. Esto no lo detecta (no
    inspecciona el layout, ver docstring del archivo), pero confirma que
    un nombre de 60+ caracteres -como hay varios en el catalogo real- no
    rompe el render.
    """
    nombre_largo = "ESMERIL DE 4-1/2 PULGADAS ATOUAN 750W-11.000RPM PROFESIONAL ANGULO"
    factura = _factura_confirmada(conn)
    factura.lines[0].product_name = nombre_largo

    ruta = render_invoice(factura, company_name="Ferreteria", export_dir=tmp_path)

    assert ruta.exists()
    assert ruta.read_bytes()[:4] == b"%PDF"


def test_render_invoice_sin_direccion_no_falla(conn, tmp_path):
    factura = _factura_confirmada(conn, direccion=None)

    ruta = render_invoice(factura, company_name="Ferreteria", export_dir=tmp_path)

    assert ruta.exists()


# ------------------------------------------------------------
# PDF en bolívares (BillingService lo pide siempre): todos los montos en
# Bs, de la misma cuenta que la pantalla, y ningun "$".
# El contenido se lee de las tablas de reportlab (_cellvalues) en vez de
# del binario comprimido del PDF.
# ------------------------------------------------------------
def _celdas(tabla):
    """Los textos de cada celda; un Paragraph aporta su .text."""
    return [
        [getattr(celda, "text", celda) for celda in fila] for fila in tabla._cellvalues
    ]


def _textos(elementos):
    """Todos los textos de una lista de flowables, entrando en las tablas."""
    textos = []
    for elemento in elementos:
        if hasattr(elemento, "_cellvalues"):
            for fila in elemento._cellvalues:
                for celda in fila:
                    textos += _textos(celda if isinstance(celda, list) else [celda])
        elif hasattr(elemento, "text"):
            textos.append(elemento.text)
        elif isinstance(elemento, str):
            textos.append(elemento)
    return textos


def _factura_a_100_bs(conn):
    from flexstock.domain.bs_breakdown import breakdown

    factura = _factura_confirmada(conn)  # 3 x 1,50$ = 4,50$ + 16% de IVA
    return factura, breakdown(factura, Decimal(100))


def test_las_lineas_salen_en_bolívares(conn):
    from flexstock.reports.invoice_pdf import _tabla_lineas

    factura, bs = _factura_a_100_bs(conn)

    filas = _celdas(_tabla_lineas(factura, "$", bs))

    # '#', descripcion, cantidad y despues los montos:
    # 1,50$ x 100 = 150,00 Bs por unidad; 4,50$ x 100 = 450,00 Bs; IVA 72,00
    assert filas[0] == ["#", "Descripción", "Cant.", "Precio unit.", "IVA", "Importe"]
    assert filas[1][3:] == ["150,00 Bs", "72,00 Bs", "450,00 Bs"]


def test_los_totales_salen_en_bolívares_y_cuadran(conn):
    from flexstock.reports.invoice_pdf import _tabla_totales

    factura, bs = _factura_a_100_bs(conn)

    filas = _celdas(_tabla_totales(factura, "$", bs))

    assert filas == [
        ["Subtotal", "450,00 Bs"],
        ["IVA 16%", "72,00 Bs"],
        ["TOTAL", "522,00 Bs"],
    ]


def test_en_bolívares_no_aparece_ningun_dolar(conn):
    from flexstock.reports.invoice_pdf import _tabla_lineas, _tabla_totales

    factura, bs = _factura_a_100_bs(conn)

    celdas = _celdas(_tabla_lineas(factura, "$", bs)) + _celdas(
        _tabla_totales(factura, "$", bs)
    )

    textos = [str(celda) for fila in celdas for celda in fila]
    assert not [t for t in textos if "$" in t]


def test_el_encabezado_avisa_que_los_montos_estan_en_bolívares(conn):
    from flexstock.reports.invoice_pdf import _encabezado_factura

    factura, bs = _factura_a_100_bs(conn)

    con_bs = [getattr(e, "text", "") for e in _encabezado_factura(factura, bs)]
    sin_bs = [getattr(e, "text", "") for e in _encabezado_factura(factura)]

    assert con_bs[:2] == [
        "<b>Bolívares (Bs)</b>",
        "Tasa BCV: 100,00 Bs por dólar",
    ]
    assert sin_bs[0] == "<b>Dólares ($)</b>"
    assert not [t for t in sin_bs if "Bolívares" in str(t)]


def test_sin_desglose_en_bs_sigue_imprimiendo_en_dolares(conn):
    from flexstock.reports.invoice_pdf import _tabla_lineas, _tabla_totales

    factura = _factura_confirmada(conn)

    lineas = _celdas(_tabla_lineas(factura, "$"))
    totales = _celdas(_tabla_totales(factura, "$"))

    assert lineas[1][3:] == ["1,50$", "0,72$", "4,50$"]
    assert totales[-1] == ["TOTAL", "5,22$"]


def test_render_invoice_en_bolívares_genera_un_pdf_valido(conn, tmp_path):
    factura, bs = _factura_a_100_bs(conn)

    ruta = render_invoice(
        factura, company_name="Ferreteria", bs=bs, export_dir=tmp_path
    )

    assert ruta.exists()
    assert ruta.read_bytes()[:4] == b"%PDF"


def test_render_invoice_en_bolívares_con_montos_grandes_no_falla(conn, tmp_path):
    """Con una tasa alta los numeros son largos ('1.234.567,89 Bs'): el
    render no debe romperse ni cortar la tabla."""
    from flexstock.domain.bs_breakdown import breakdown

    factura = _factura_confirmada(conn)
    factura.lines[0].unit_price_cents = 99_999_99

    ruta = render_invoice(
        factura,
        company_name="Ferreteria",
        bs=breakdown(factura, Decimal("1100.50")),
        export_dir=tmp_path,
    )

    assert ruta.read_bytes()[:4] == b"%PDF"


def test_render_invoice_reimprimir_sobrescribe_el_mismo_archivo(conn, tmp_path):
    factura = _factura_confirmada(conn)

    primera = render_invoice(factura, company_name="Ferreteria", export_dir=tmp_path)
    tamanio_primera = primera.stat().st_size
    segunda = render_invoice(factura, company_name="Ferreteria", export_dir=tmp_path)

    assert primera == segunda
    assert len(list(tmp_path.glob("*.pdf"))) == 1
    assert segunda.stat().st_size == tamanio_primera


# ------------------------------------------------------------
# Encabezado de la tienda (Configuracion > Tienda)
# ------------------------------------------------------------
def _textos_del_encabezado(*args, **kwargs):
    from flexstock.reports.invoice_pdf import _encabezado_empresa

    (tabla,) = _encabezado_empresa(*args, **kwargs)
    textos = []
    for fila in tabla._cellvalues:
        for celda in fila:
            if isinstance(celda, list):
                textos += [getattr(p, "text", str(p)) for p in celda]
            else:
                textos.append(getattr(celda, "text", celda))
    return textos


def _png(tamano=(800, 400)):
    import io

    from PIL import Image

    buffer = io.BytesIO()
    Image.new("RGB", tamano, "white").save(buffer, "PNG")
    return buffer.getvalue()


def test_el_encabezado_imprime_los_datos_de_la_tienda():
    textos = _textos_del_encabezado(
        "Ferreteria El Tornillo",
        "J-12345678-9",
        "0212-5551234",
        "Av. Principal, Local 3",
    )

    assert "Ferreteria El Tornillo" in textos
    assert "Av. Principal, Local 3" in textos
    assert "RIF: J-12345678-9 | Tel: 0212-5551234" in textos


def test_el_rif_va_antes_que_el_telefono():
    """El RIF es lo que hace valida la factura; el telefono es contacto."""
    (pie,) = [
        t for t in _textos_del_encabezado("X", "J-1", "0212-1", "") if "RIF" in str(t)
    ]

    assert pie.index("RIF") < pie.index("Tel")


def test_un_dato_vacio_no_deja_una_etiqueta_pelada():
    textos = _textos_del_encabezado("Solo el nombre", "", "", "")

    assert not [t for t in textos if "RIF:" in str(t) or "Tel:" in str(t)]


def test_sin_logo_no_imprime_el_recuadro_de_relleno():
    """Antes salia un recuadro gris con la palabra '[LOGO]' impresa en la
    factura que recibe el cliente: era un recordatorio para el programador."""
    textos = _textos_del_encabezado("Mi Tienda", "", "", "", None)

    assert "[LOGO]" not in [str(t) for t in textos]


def test_con_logo_entra_una_imagen_en_el_encabezado():
    from reportlab.platypus import Image

    from flexstock.reports.invoice_pdf import _encabezado_empresa

    (tabla,) = _encabezado_empresa("Mi Tienda", "", "", "", _png())

    assert isinstance(tabla._cellvalues[0][0], Image)


def test_el_logo_entra_en_su_recuadro_sin_deformarse():
    from reportlab.lib.units import cm

    from flexstock.reports.invoice_pdf import _encabezado_empresa

    (tabla,) = _encabezado_empresa("Mi Tienda", "", "", "", _png((800, 400)))
    logo = tabla._cellvalues[0][0]

    assert logo.drawWidth <= 2.5 * cm + 0.01
    assert logo.drawHeight <= 1.5 * cm + 0.01
    # 2:1 de origen -> 2:1 en el papel, no estirado para llenar la caja
    assert abs(logo.drawWidth / logo.drawHeight - 2) < 0.01


def test_un_logo_ilegible_no_impide_imprimir_la_factura():
    """Imprimir es lo importante; el logo es decoracion."""
    from flexstock.reports.invoice_pdf import _encabezado_empresa

    (tabla,) = _encabezado_empresa("Mi Tienda", "", "", "", b"esto no es una imagen")

    assert tabla._cellvalues[0][0] == ""


def test_render_invoice_completo_con_logo_y_datos(conn, tmp_path):
    """El puente entero: los datos de Configuracion salen en el archivo."""
    factura = _factura_confirmada(conn)

    ruta = render_invoice(
        factura,
        company_name="Ferreteria El Tornillo",
        company_tax_id="J-12345678-9",
        company_phone="0212-5551234",
        company_address="Av. Principal, Local 3",
        company_logo=_png(),
        export_dir=tmp_path,
    )

    assert ruta.exists()
    assert ruta.stat().st_size > 0


def test_el_encabezado_imprime_el_correo_si_esta_cargado():
    textos = _textos_del_encabezado(
        "Ferreteria El Tornillo",
        "J-12345678-9",
        "0212-5551234",
        "Av. Principal",
        None,
        "ventas@tornillo.com",
    )

    assert "ventas@tornillo.com" in textos


def test_el_encabezado_sin_correo_no_deja_una_linea_vacia():
    textos = _textos_del_encabezado("X", "J-1", "0212-1", "")

    assert not [t for t in textos if "@" in str(t)]


# ------------------------------------------------------------
# razon social, web/redes, datos de pago y texto de pie
# ------------------------------------------------------------
def test_la_razon_social_sale_solo_si_es_distinta_del_nombre_comercial():
    distinta = _textos_del_encabezado(
        "La Esquina", "J-1", "0212-1", "", None, "", "Inversiones La Esquina C.A."
    )
    igual = _textos_del_encabezado(
        "La Esquina", "J-1", "0212-1", "", None, "", "LA ESQUINA"
    )

    assert "Razón social: Inversiones La Esquina C.A." in distinta
    assert not [t for t in igual if "Razón social" in str(t)]


def test_correo_y_web_van_en_una_sola_linea():
    textos = _textos_del_encabezado(
        "X", "J-1", "0212-1", "", None, "ventas@x.com", "", "@laesquina"
    )

    assert "ventas@x.com | @laesquina" in textos


def test_el_texto_del_usuario_no_rompe_el_pdf_con_simbolos():
    from flexstock.reports.invoice_pdf import _bloque_pago_y_pie

    elementos = _bloque_pago_y_pie("Pago <movil> & Zelle", "")
    textos = _textos(elementos)

    assert "Pago &lt;movil&gt; &amp; Zelle" in textos


def test_sin_datos_de_pago_ni_pie_el_bloque_esta_vacio():
    from flexstock.reports.invoice_pdf import _bloque_pago_y_pie

    assert _bloque_pago_y_pie("", "") == []
    assert _bloque_pago_y_pie("   ", "\n") == []


def test_los_saltos_de_linea_del_pie_se_respetan():
    from flexstock.reports.invoice_pdf import _bloque_pago_y_pie

    elementos = _bloque_pago_y_pie("Banco X\nCuenta 0102", "Garantia 30 días")
    textos = _textos(elementos)

    assert "DATOS DE PAGO" in textos
    assert "CONDICIONES" in textos
    assert "Banco X<br/>Cuenta 0102" in textos
    assert "Garantia 30 días" in textos


def test_render_invoice_con_todos_los_datos_de_la_tienda_genera_el_pdf(conn, tmp_path):
    factura = _factura_confirmada(conn)

    ruta = render_invoice(
        factura,
        company_name="La Esquina",
        company_tax_id="J-12345678-9",
        company_phone="0241-1234567",
        company_address="Av. Bolivar & Calle 5",
        company_email="ventas@laesquina.com",
        company_legal_name="Inversiones <La Esquina> C.A.",
        company_web="@laesquina",
        company_payment_info="Pago movil 0414-1234567\nZelle: pagos@laesquina.com",
        company_footer="Garantia 30 días.\nNo se aceptan devoluciones sin factura.",
        export_dir=tmp_path,
    )

    assert ruta.read_bytes()[:4] == b"%PDF"


def test_sin_iva_la_tabla_no_lleva_columna_de_iva(conn):
    from flexstock.reports.invoice_pdf import _tabla_lineas

    factura = _factura_confirmada(conn)
    factura.tax_rate = Decimal(0)

    encabezado = _celdas(_tabla_lineas(factura, "$"))[0]

    assert "IVA" not in encabezado


def test_la_tabla_de_lineas_repite_su_encabezado_en_cada_hoja(conn):
    from flexstock.reports.invoice_pdf import _tabla_lineas

    tabla = _tabla_lineas(_factura_confirmada(conn), "$")

    assert tabla.repeatRows == 1


def test_el_recuadro_de_moneda_dice_la_forma_de_pago(conn):
    from flexstock.domain.enums import PaymentMethod
    from flexstock.reports.invoice_pdf import _encabezado_factura

    factura = _factura_confirmada(conn)
    factura.payment_method = PaymentMethod.ZELLE

    textos = [getattr(e, "text", "") for e in _encabezado_factura(factura)]

    assert "Forma de pago: Zelle" in textos


def test_una_factura_anulada_lo_dice_arriba_y_genera_el_pdf(conn, tmp_path):
    from flexstock.domain.enums import InvoiceStatus
    from flexstock.reports.invoice_pdf import _aviso_anulada

    factura = _factura_confirmada(conn)
    factura.status = InvoiceStatus.CANCELLED
    factura.cancel_reason = "Error de precio"

    textos = [getattr(e, "text", "") for e in _aviso_anulada(factura)]
    ruta = render_invoice(factura, company_name="Ferreteria", export_dir=tmp_path)

    assert "FACTURA ANULADA: Error de precio" in textos
    assert ruta.read_bytes()[:4] == b"%PDF"


def test_una_factura_larga_pasa_a_otra_hoja_sin_romperse(conn, tmp_path):
    import re

    factura = _factura_confirmada(conn)
    factura.lines = factura.lines * 60  # mas de una hoja carta

    ruta = render_invoice(factura, company_name="Ferreteria", export_dir=tmp_path)

    paginas = re.search(rb"/Count (\d+)", ruta.read_bytes())
    assert paginas is not None and int(paginas.group(1)) >= 2


def test_porcentaje_sin_ceros_de_mas():
    from flexstock.reports.invoice_pdf import porcentaje

    assert porcentaje(Decimal("16.00")) == "16%"
    assert porcentaje(Decimal("12.50")) == "12,5%"
    assert porcentaje(Decimal(0)) == "0%"


def test_una_factura_de_antes_del_cambio_con_otra_tasa_dice_tasa_de_cambio(conn):
    """Antes del 21/09/2026 02:34, cobrar a la tasa de referencia la guardaba
    en bs_rate: esa no se puede llamar BCV."""
    from datetime import datetime

    from flexstock.reports.invoice_pdf import _encabezado_factura

    factura, bs = _factura_a_100_bs(conn)
    factura.bs_rate = Decimal(1000)
    factura.ref_rate = None
    factura.issued_at = datetime(2026, 9, 21, 2, 11)  # noqa: DTZ001

    textos = [getattr(e, "text", "") for e in _encabezado_factura(factura, bs)]

    assert textos[1] == "Tasa de cambio: 100,00 Bs por dólar"


def test_la_tasa_lleva_punto_de_miles():
    from flexstock.domain.money import format_rate

    assert format_rate(Decimal("1007.956")) == "1.007,96"
    assert format_rate(Decimal("899.27")) == "899,27"


# ------------------------------------------------------------
# QR de pago
# ------------------------------------------------------------
def _imagenes(elementos):
    from reportlab.platypus import Image

    encontradas = []
    for elemento in elementos:
        if isinstance(elemento, Image):
            encontradas.append(elemento)
        elif hasattr(elemento, "_cellvalues"):
            for fila in elemento._cellvalues:
                for celda in fila:
                    encontradas += _imagenes(
                        celda if isinstance(celda, list) else [celda]
                    )
    return encontradas


def test_el_qr_sale_junto_a_los_datos_de_pago():
    from flexstock.reports.invoice_pdf import _QR_LADO, _bloque_pago_y_pie

    elementos = _bloque_pago_y_pie("Pago móvil 0412", "", _png((600, 600)))

    assert "DATOS DE PAGO" in _textos(elementos)
    assert "Pago móvil 0412" in _textos(elementos)
    (qr,) = _imagenes(elementos)
    assert (qr.drawWidth, qr.drawHeight) == (_QR_LADO, _QR_LADO)
    assert "Escanea para pagar" in _textos(elementos)


def test_solo_con_qr_y_sin_texto_igual_sale_el_recuadro_de_pago():
    from flexstock.reports.invoice_pdf import _bloque_pago_y_pie

    elementos = _bloque_pago_y_pie("", "", _png((600, 600)))

    assert "DATOS DE PAGO" in _textos(elementos)
    assert len(_imagenes(elementos)) == 1


def test_una_foto_apaisada_del_qr_no_se_deforma():
    from flexstock.reports.invoice_pdf import _QR_LADO, _bloque_pago_y_pie

    (qr,) = _imagenes(_bloque_pago_y_pie("Pago", "", _png((800, 400))))

    assert qr.drawWidth == _QR_LADO
    assert abs(qr.drawHeight - _QR_LADO / 2) < 0.01


def test_un_qr_ilegible_no_impide_imprimir():
    from flexstock.reports.invoice_pdf import _bloque_pago_y_pie

    elementos = _bloque_pago_y_pie("Pago", "", b"esto no es una imagen")

    assert "Pago" in _textos(elementos)
    assert _imagenes(elementos) == []


def test_render_invoice_con_qr_genera_el_pdf(conn, tmp_path):
    ruta = render_invoice(
        _factura_confirmada(conn),
        company_name="Ferreteria",
        company_payment_info="Pago móvil",
        company_payment_qr=_png((600, 600)),
        export_dir=tmp_path,
    )

    assert ruta.stat().st_size > 0
