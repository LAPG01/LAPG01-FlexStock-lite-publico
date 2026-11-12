"""
Parser de PRODR.TXT: ancho fijo, CP850, CRLF.

Solo entiende el formato del sistema viejo: devuelve dicts con valores
tipados (Decimal/int) pero SIN escalar a milli/centavos, y sin saber
nada de sqlite3 ni del dominio nuevo. Esa traduccion es trabajo de
legacy_importer.py.

Rangos de columna medidos contra la propia linea de guiones del
archivo (no adivinados):

    Codigo           [0:6]
    Descripcion      [7:47]   (40 chars, verificado contra el archivo real)
    Referencia       [48:63]
    Costo unitario   [64:80]
    Existencia       [81:91]
    Unidades         [92:102]
    Costo existencia [103:119]
    Precio 1         [120:136]
    Precio 2         [137:153]
    Precio 3         [154:170]

Bajo cada producto hay 0, 1 o 2 lineas indentadas (columna 'Codigo'
en blanco) con el contacto/proveedor. Este modulo las guarda tal
cual, sin parsear: eso es trabajo de supplier_normalizer.py.
"""

from decimal import Decimal
from pathlib import Path

from ..domain.money import parse_local_decimal

_ENCODING = "cp850"

_COL_CODIGO = slice(0, 6)
_COL_DESCRIPCION = slice(7, 47)
_COL_REFERENCIA = slice(48, 63)
_COL_COSTO_UNITARIO = slice(64, 80)
_COL_EXISTENCIA = slice(81, 91)
_COL_UNIDADES = slice(92, 102)
_COL_COSTO_EXISTENCIA = slice(103, 119)
_COL_PRECIO1 = slice(120, 136)
_COL_PRECIO2 = slice(137, 153)
_COL_PRECIO3 = slice(154, 170)


def parse_prodr(path: Path) -> list[dict]:
    """
    Cada dict: codigo, descripcion, referencia, costo_unitario,
    existencia, unidades, costo_existencia, precio1, precio2, precio3
    (Decimal/int) + supplier_lines (list[str], crudas y sin parsear).

    El archivo trae la MISMA linea de guiones para abrir la tabla
    (bajo el encabezado) y para cerrarla (antes del renglon de
    totales). La primera nos dice donde empiezan los datos; la
    segunda, donde terminan, para no confundir el renglon de totales
    con una linea de proveedor sin producto.
    """
    products: list[dict] = []
    current: dict | None = None
    in_data_section = False

    with path.open(encoding=_ENCODING) as f:
        for raw_line in f:
            line = raw_line.rstrip("\n")

            if _is_separator(line):
                if not in_data_section:
                    in_data_section = True
                    continue
                break  # segunda linea de guiones: fin de los datos

            if not in_data_section:
                continue  # titulo, subtitulo, "Tipo:", "Deposito:", encabezado

            if not line.strip():
                continue  # la unica linea en blanco real del archivo

            codigo = line[_COL_CODIGO].strip()
            if codigo:
                if current is not None:
                    products.append(current)
                current = _parse_product_line(line)
            elif current is not None:
                current["supplier_lines"].append(line.strip())

    if current is not None:
        products.append(current)

    if not products:
        raise ValueError(f"No se encontraron productos en {path}")

    return products


def _is_separator(line: str) -> bool:
    return line[:6] == "-" * 6


def _parse_product_line(line: str) -> dict:
    return {
        "codigo": line[_COL_CODIGO].strip(),
        "descripcion": line[_COL_DESCRIPCION].strip(),
        "referencia": line[_COL_REFERENCIA].strip(),
        "costo_unitario": _parse_decimal(line[_COL_COSTO_UNITARIO]),
        "existencia": _parse_int(line[_COL_EXISTENCIA]),
        "unidades": _parse_int(line[_COL_UNIDADES]),
        "costo_existencia": _parse_decimal(line[_COL_COSTO_EXISTENCIA]),
        "precio1": _parse_decimal(line[_COL_PRECIO1]),
        "precio2": _parse_decimal(line[_COL_PRECIO2]),
        "precio3": _parse_decimal(line[_COL_PRECIO3]),
        "supplier_lines": [],
    }


def _parse_decimal(text: str) -> Decimal:
    """
    Campo vacio (columna toda espacios) -> 0. El resto es el mismo
    formato local de siempre (coma decimal, punto de miles): eso lo
    resuelve domain.money.parse_local_decimal, la unica frontera de
    conversion de dinero del proyecto.
    """
    cleaned = text.strip()
    return parse_local_decimal(cleaned) if cleaned else Decimal(0)


def _parse_int(text: str) -> int:
    cleaned = text.strip()
    return int(cleaned) if cleaned else 0
