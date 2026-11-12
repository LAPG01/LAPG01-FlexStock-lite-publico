"""
PDF de un LISTADO completo (todo el catalogo de Productos, Proveedores o
Clientes en una sola tabla) - distinto de invoice_pdf.py/quotation_pdf.py,
que dibujan UN documento individual. Reusa el mismo esqueleto Platypus,
pero apaisado (landscape) y con letra mas chica: es una tabla para
consultar o archivar, no un documento para el cliente.

No sabe nada de sqlite3 ni de flexstock.data ni de que entidad esta
listando: recibe encabezados y filas ya armadas como texto
(ExportService decide que columnas van y como se formatea cada celda).
Table de ReportLab pagina sola cuando el listado no entra en una hoja
(ver repeatRows=1 mas abajo, para que el encabezado se repita en cada
pagina) - no hay que calcularlo a mano ni aunque sean 1.936 filas.
"""

from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

_styles = getSampleStyleSheet()
_titulo = ParagraphStyle("TituloListado", parent=_styles["Heading2"], spaceAfter=2)
_normal = _styles["Normal"]
_celda = ParagraphStyle("CeldaListado", parent=_normal, fontSize=8, leading=10)
_celda_encabezado = ParagraphStyle(
    "EncabezadoListado", parent=_celda, fontName="Helvetica-Bold"
)

# Ancho minimo por columna (una columna angosta, ej. "Activo", nunca
# queda ilegible) y tope de cuanto texto puede sumarle ancho a una
# columna (una columna con textos muy largos, ej. "Nombre", no se come
# toda la pagina) - ver _pesos_de_columna.
_ANCHO_MINIMO = 1.6 * cm
_LARGO_TOPE = 32


def render_listing(
    titulo: str,
    headers: list[str],
    rows: list[list[str]],
    *,
    export_dir: Path,
    filename_prefix: str,
) -> Path:
    """Genera export_dir/{filename_prefix}_{timestamp}.pdf y devuelve la ruta."""
    export_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # noqa: DTZ005
    filepath = export_dir / f"{filename_prefix}_{stamp}.pdf"

    pagesize = landscape(letter)
    doc = SimpleDocTemplate(
        str(filepath),  # reportlab exige un str: no acepta un Path
        pagesize=pagesize,
        leftMargin=1.2 * cm,
        rightMargin=1.2 * cm,
        topMargin=1.2 * cm,
        bottomMargin=1.2 * cm,
    )
    ancho_disponible = pagesize[0] - doc.leftMargin - doc.rightMargin

    ahora = datetime.now().strftime("%d/%m/%Y %H:%M")  # noqa: DTZ005
    doc.build(
        [
            Paragraph(titulo, _titulo),
            Paragraph(f"{len(rows)} fila(s) - Generado: {ahora}", _normal),
            Spacer(1, 0.4 * cm),
            _tabla(headers, rows, ancho_disponible),
        ]
    )
    return filepath


def _tabla(headers: list[str], rows: list[list[str]], ancho_disponible: float) -> Table:
    col_widths = _anchos_de_columna(headers, rows, ancho_disponible)
    filas = [
        [Paragraph(str(texto), _celda_encabezado) for texto in headers],
        *([Paragraph(str(valor), _celda) for valor in fila] for fila in rows),
    ]
    # repeatRows=1: el encabezado se repite en cada pagina nueva - sin
    # esto, la fila 500 de un catalogo de 1.936 productos aparece bajo
    # columnas sin nombre.
    tabla = Table(filas, colWidths=col_widths, repeatRows=1)
    tabla.setStyle(
        TableStyle(
            [
                ("BACKGROUND", (0, 0), (-1, 0), colors.lightgrey),
                ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
                ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.whitesmoke]),
            ]
        )
    )
    return tabla


def _anchos_de_columna(
    headers: list[str], rows: list[list[str]], ancho_disponible: float
) -> list[float]:
    """
    Ancho proporcional al contenido de cada columna (el titulo o el
    valor mas largo que tenga, con un tope): "Nombre" se lleva mas
    espacio que "Activo" sin tener que configurarlo a mano por reporte.
    Si los minimos (_ANCHO_MINIMO) no entran todos juntos en la pagina
    (muchas columnas angostas a la vez), se reescala todo por igual -
    Table no encoge una columna sola.
    """
    pesos = []
    for indice, encabezado in enumerate(headers):
        maximo = len(encabezado)
        for fila in rows:
            maximo = max(maximo, len(str(fila[indice])))
        pesos.append(min(maximo, _LARGO_TOPE))

    total_peso = sum(pesos) or 1
    anchos = [
        max(ancho_disponible * peso / total_peso, _ANCHO_MINIMO) for peso in pesos
    ]
    escala = min(1.0, ancho_disponible / sum(anchos))
    return [ancho * escala for ancho in anchos]
