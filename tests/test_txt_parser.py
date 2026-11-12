"""
Tests de txt_parser.py.

Las lineas de datos de este fixture son copias literales de
source_data/PRODR.TXT (verificadas byte a byte, ver la conversacion de
diseno): encabezado, separador, un producto normal, un producto con
separador de miles en un campo ('1.780,00'), y el cierre con la
segunda linea de guiones + el renglon de totales, igual que el
archivo real. Se dejan como lineas unicas (sin partir) para no
arriesgar el alineado exacto de columnas.

    pytest -q
"""

from decimal import Decimal

import pytest

from flexstock.importers.txt_parser import parse_prodr

_HEADER = "Código Descripción                              Referencia        Costo unitario Existencia   Unidades  Costo existenci         Precio 1         Precio 2         Precio 3"
_SEPARATOR = "------ ---------------------------------------- --------------- ---------------- ---------- ---------- ---------------- ---------------- ---------------- ----------------"
_ROW_TORNILLO = "TOR123 TORNILLO HEX 1/2X3 UNC-A                                             0,56         10          0             5,60             1,50             1,65             1,80"
_SUPPLIER_LINE = "       09/10/24-SURTEC/MARIANA                                                                                                                                           "
_ROW_VALV34 = "VALV34 VALVULA 3/4 BRONCE PASO RECTO C/VOLANTE.                           445,00          4          0         1.780,00             3,00             4,00             5,00"
_TOTALS_ROW = "                                                                                      10290          0        22.070,80                                                   "


def _write_fixture(path, extra_lines: list[str] = ()):
    lines = [
        " " * 60 + "REPORTE DE PRODUCTOS",
        "Tipo    : General",
        _HEADER,
        _SEPARATOR,
        _ROW_TORNILLO,
        _SUPPLIER_LINE,
        _ROW_VALV34,
        *extra_lines,
        _SEPARATOR,
        _TOTALS_ROW,
        "",
    ]
    path.write_bytes("\r\n".join(lines).encode("cp850"))


def test_parsea_dos_productos(tmp_path):
    path = tmp_path / "PRODR.TXT"
    _write_fixture(path)

    productos = parse_prodr(path)

    assert [p["codigo"] for p in productos] == ["TOR123", "VALV34"]


def test_parsea_campos_numericos_con_coma_decimal(tmp_path):
    path = tmp_path / "PRODR.TXT"
    _write_fixture(path)

    tornillo = parse_prodr(path)[0]

    assert tornillo["descripcion"] == "TORNILLO HEX 1/2X3 UNC-A"
    assert tornillo["costo_unitario"] == Decimal("0.56")
    assert tornillo["existencia"] == 10
    assert tornillo["unidades"] == 0
    assert tornillo["costo_existencia"] == Decimal("5.60")
    assert tornillo["precio1"] == Decimal("1.50")
    assert tornillo["precio2"] == Decimal("1.65")
    assert tornillo["precio3"] == Decimal("1.80")


def test_parsea_separador_de_miles(tmp_path):
    """'1.780,00' -> Decimal('1780.00'), no Decimal('1.78000')."""
    path = tmp_path / "PRODR.TXT"
    _write_fixture(path)

    valvula = parse_prodr(path)[1]

    assert valvula["costo_existencia"] == Decimal("1780.00")


def test_agrupa_lineas_de_proveedor_bajo_su_producto(tmp_path):
    path = tmp_path / "PRODR.TXT"
    _write_fixture(path)

    productos = parse_prodr(path)

    assert productos[0]["supplier_lines"] == ["09/10/24-SURTEC/MARIANA"]
    assert productos[1]["supplier_lines"] == []


def test_no_confunde_el_renglon_de_totales_con_proveedor(tmp_path):
    """
    El renglon de totales, igual que una linea de proveedor, tiene la
    columna 'Codigo' en blanco. Sin detectar el segundo separador,
    quedaría pegado como supplier_line del ultimo producto.
    """
    path = tmp_path / "PRODR.TXT"
    _write_fixture(path)

    productos = parse_prodr(path)

    assert productos[-1]["supplier_lines"] == []


def test_producto_con_dos_lineas_de_proveedor(tmp_path):
    path = tmp_path / "PRODR.TXT"
    _write_fixture(path, extra_lines=["       OTRO PROVEEDOR"])
    # la linea extra queda bajo VALV34: es el ultimo producto leido

    productos = parse_prodr(path)

    assert productos[1]["supplier_lines"] == ["OTRO PROVEEDOR"]


def test_archivo_sin_productos_lanza_valueerror(tmp_path):
    path = tmp_path / "PRODR.TXT"
    path.write_bytes("basura\r\nmas basura".encode("cp850"))

    with pytest.raises(ValueError, match="No se encontraron productos"):
        parse_prodr(path)
