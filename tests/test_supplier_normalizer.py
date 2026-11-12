"""
Tests de supplier_normalizer.py: parseo de linea cruda, agrupacion
(paso A) + sugerencia de parecidos (paso B), y el ida-y-vuelta del
CSV de revision.

TODOS los nombres, contactos y telefonos de aca son INVENTADOS. Lo que
se prueba es el FORMATO de la linea del txt viejo
(`fecha-PROVEEDOR/CONTACTO`), no de quien es: un proveedor real no hace
mejor al test y si publica el dato de un tercero cuando el repo se
abra. Si hace falta reproducir un caso raro del archivo, se copia la
FORMA y se cambian los datos.

    pytest -q
"""

import csv
from datetime import date

from flexstock.importers.supplier_normalizer import (
    CSV_FIELDS,
    group_lines,
    load_reviewed,
    parse_raw_line,
    write_review_csv,
)


# ------------------------------------------------------------
# parse_raw_line
# ------------------------------------------------------------
def test_parse_raw_line_con_fecha_y_separador_barra():
    parsed = parse_raw_line("09/10/24-SURTEC/MARIANA")

    assert parsed.last_purchase == date(2024, 10, 9)
    assert parsed.normalized_key == "SURTEC"
    assert parsed.candidate_contact == "MARIANA"


def test_parse_raw_line_separador_guion_agrupa_igual_que_barra():
    parsed = parse_raw_line("18/06/26-LIMAR-RAMON")

    assert parsed.normalized_key == "LIMAR"
    assert parsed.candidate_contact == "RAMON"


def test_parse_raw_line_separador_coma():
    parsed = parse_raw_line("03/09/25-MOK,RAMON")

    assert parsed.normalized_key == "MOK"
    assert parsed.candidate_contact == "RAMON"


def test_parse_raw_line_saca_punto_final():
    parsed = parse_raw_line("13/04/26-MOK-RAMON.")

    assert parsed.normalized_key == "MOK"


def test_parse_raw_line_saca_punto_aunque_haya_separador_despues():
    """
    Forma real del archivo (con datos inventados, ver la nota de arriba):
    'FERRE SUR./0412-1112233.'. El punto de 'SUR.' queda justo ANTES del
    separador '/' que corta el candidato, asi que un rstrip('.')
    aplicado a toda la cadena (antes de partir) nunca lo alcanza. Sin
    este fix, 'FERRE SUR' (de lineas sin punto) y 'FERRE SUR.' (de esta)
    quedan como dos grupos distintos en vez de uno solo.
    """
    parsed = parse_raw_line("09/04/24-FERRE SUR./0412-1112233.")

    assert parsed.normalized_key == "FERRE SUR"


def test_parse_raw_line_sin_fecha():
    parsed = parse_raw_line("GIANNY PIERG")

    assert parsed.last_purchase is None
    assert parsed.normalized_key == "GIANNY PIERG"
    assert parsed.candidate_contact == ""


def test_parse_raw_line_solo_fecha_sin_texto():
    parsed = parse_raw_line("08/02/24")

    assert parsed.last_purchase == date(2024, 2, 8)
    assert parsed.normalized_key == ""


def test_parse_raw_line_fecha_con_un_solo_digito():
    """'29/7/20': dia y mes de un digito, formato real del txt viejo."""
    parsed = parse_raw_line("29/7/20-GIANNY PIERG.")

    assert parsed.last_purchase == date(2020, 7, 29)
    assert parsed.normalized_key == "GIANNY PIERG"


def test_parse_raw_line_fecha_invalida_no_rompe():
    parsed = parse_raw_line("31/02/24-ALGO")

    assert parsed.last_purchase is None
    assert parsed.normalized_key == "ALGO"


def test_parse_raw_line_sin_separador_reconocible_no_lanza():
    """Ruido real del archivo: no es una linea de proveedor valida."""
    parsed = parse_raw_line("-13/08/26-13.46$ SACO 25KG.IVA IMCLUIDO.")

    assert parsed.normalized_key == ""


# ------------------------------------------------------------
# group_lines: paso A (exacto) + paso B (sugerencia)
# ------------------------------------------------------------
def test_group_lines_junta_variantes_de_separador():
    lineas = [
        "09/10/24-SURTEC/MARIANA",
        "20/09/24-SURTEC-MARIANA SOTO",
        "09/10/24-SURTEC/MARIANA",
    ]

    grupos = group_lines(lineas)

    assert len(grupos) == 1
    assert grupos[0].normalized_key == "SURTEC"
    assert grupos[0].occurrences == 3


def test_group_lines_ordena_por_frecuencia_descendente():
    lineas = ["A", "A", "A", "B", "B", "C"]

    grupos = group_lines(lineas)

    assert [g.normalized_key for g in grupos] == ["A", "B", "C"]


def test_group_lines_suggested_contact_es_el_mas_repetido():
    lineas = [
        "01/01/24-LIMAR/RAMON",
        "02/01/24-LIMAR/RAMON",
        "03/01/24-LIMAR/OTRO",
    ]

    grupos = group_lines(lineas)

    assert grupos[0].suggested_contact == "RAMON"


def test_group_lines_sugiere_parecido_sin_fusionar():
    lineas = ["01/01/24-LIMAR", "02/01/24-LIMR"]  # variante con typo

    grupos = group_lines(lineas)

    claves = {g.normalized_key for g in grupos}
    assert claves == {"LIMAR", "LIMR"}  # el paso A NO los fusiona
    por_clave = {g.normalized_key: g for g in grupos}
    assert por_clave["LIMR"].similar_to == "LIMAR"


def test_group_lines_no_sugiere_similar_a_clave_vacia():
    lineas = ["08/02/24", "01/01/24-LIMAR"]  # una linea sin nombre util

    grupos = group_lines(lineas)

    vacio = next(g for g in grupos if g.normalized_key == "")
    assert vacio.similar_to == ""


# ------------------------------------------------------------
# write_review_csv / load_reviewed: el ida y vuelta
# ------------------------------------------------------------
def test_csv_ida_y_vuelta(tmp_path):
    lineas = [
        "09/10/24-SURTEC/MARIANA",
        "20/09/24-SURTEC-MARIANA SOTO",
        "03/09/25-MOK,RAMON",  # a proposito: coma dentro de un campo
        "08/02/24",  # solo fecha, sin nombre: normalized_key vacio
    ]
    grupos = group_lines(lineas)
    csv_path = tmp_path / "suppliers_to_review.csv"

    write_review_csv(grupos, csv_path)

    # todavia nadie completo canonical_name: nada esta revisado
    assert load_reviewed(csv_path) == {}

    # simulo la revision manual editando con csv, como haria LibreOffice
    with csv_path.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        if fila["normalized_key"] == "SURTEC":
            fila["canonical_name"] = "SURTEC C.A."
            fila["is_supplier"] = "SI"
        elif fila["normalized_key"] == "":
            fila["canonical_name"] = "no aplica"
            fila["is_supplier"] = "NO"
    with csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(filas)

    reviewed = load_reviewed(csv_path)

    assert reviewed["SURTEC"].canonical_name == "SURTEC C.A."
    assert reviewed["SURTEC"].is_supplier is True
    assert reviewed[""].is_supplier is False
    assert "MOK" not in reviewed  # no se reviso: sigue pendiente
