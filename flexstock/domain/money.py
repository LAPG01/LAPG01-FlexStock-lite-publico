"""
Conversion entre Decimal (mundo humano) y enteros escalados (mundo SQLite).

SQLite no tiene DECIMAL. Guardar dinero como REAL produce errores de
redondeo acumulativos: 0.1 + 0.2 != 0.3 en punto flotante. Con enteros
no puede pasar.

Dinero    -> centavos   (x100)
Cantidad  -> milesimas  (x1000), para poder vender 2,5 kg de clavos

Estas cuatro funciones son la UNICA frontera. Si aparece un x100 suelto
en cualquier otro archivo, es un bug.
"""

from decimal import ROUND_HALF_UP, Decimal

MONEY_SCALE = Decimal(100)
QTY_SCALE = Decimal(1000)

_CENTS = Decimal("0.01")
_MILLI = Decimal("0.001")


def to_cents(value: Decimal | str | int) -> int:
    """Decimal('1.50') -> 150"""
    return int(
        (Decimal(value) * MONEY_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


def from_cents(cents: int) -> Decimal:
    """150 -> Decimal('1.50')"""
    return (Decimal(cents) / MONEY_SCALE).quantize(_CENTS)


def to_milli(value: Decimal | str | int) -> int:
    """Decimal('2.5') -> 2500"""
    return int(
        (Decimal(value) * QTY_SCALE).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    )


def from_milli(milli: int) -> Decimal:
    """2500 -> Decimal('2.500')"""
    return (Decimal(milli) / QTY_SCALE).quantize(_MILLI)


def convert_cents(cents: int, factor: Decimal) -> int:
    """
    Pasa un monto en centavos a otra moneda multiplicando por 'factor'
    (ej. una tasa Bs/$), redondeando una sola vez al final.
    1000 (10,00$) con factor Decimal('45.50') -> 45500 (455,00 Bs)
    """
    return int((Decimal(cents) * factor).quantize(Decimal(1), rounding=ROUND_HALF_UP))


def format_money(cents: int, symbol: str = "$") -> str:
    """150 -> '1,50$'  (formato local: coma decimal, punto de miles, simbolo al final)"""
    return _format_currency(from_cents(cents), symbol)


def format_money_milli(milli: int, symbol: str = "$") -> str:
    """
    Formatea cost_milli/price_milli (escala x1000 - ver nota en
    ProductService.create_product) para mostrarselos a una persona.

    NUNCA pasar estos campos por format_money(): esa espera centavos
    (x100) y el numero queda 10 veces mas grande. Mezclar las dos
    escalas es justo el bug que prohibe la Regla 1 del CLAUDE.md.

    5000 -> '5,00$'    2 decimales: el caso normal, precio por unidad.
    1125 -> '1,125$'   3 decimales SOLO si el catalogo de verdad los
                        usa (venta fraccionada); nunca se recortan.
    """
    value = from_milli(milli)
    if milli % 10 == 0:  # sin tercer decimal real: no mostrar '5,000'
        value = value.quantize(_CENTS)
    return _format_currency(value, symbol)


def format_rate(rate: Decimal) -> str:
    """Una tasa de cambio para leer: Decimal('1007.96') -> '1.007,96'."""
    return _format_currency(rate.quantize(_CENTS, rounding=ROUND_HALF_UP), "")


def _format_currency(value: Decimal, symbol: str) -> str:
    """Agrupa miles con punto y pone la coma decimal; symbol al final, sin espacio."""
    entera, _, dec = f"{value:f}".partition(".")
    negativo = entera.startswith("-")
    entera = entera.lstrip("-")
    grupos = []
    while len(entera) > 3:
        grupos.insert(0, entera[-3:])
        entera = entera[:-3]
    grupos.insert(0, entera)
    texto = ".".join(grupos) + "," + dec
    return f"{'-' if negativo else ''}{texto}{symbol}"


def format_quantity(milli: int) -> str:
    """
    2500 -> '2,5'   |   3000 -> '3'  (sin decimales inutiles)

    NUNCA con Decimal.normalize(): para un valor redondo (ej. 10.000)
    normalize() no solo saca los ceros de mas, tambien puede pasar a
    notacion cientifica ('1E+1') porque el exponente queda positivo -
    '3' se ve bien, pero cualquier cantidad multiplo de 10 (10, 20,
    100...) se mostraba como '1E+1'. El formato ':f' de abajo fuerza
    punto fijo siempre; los ceros de sobra se sacan a mano con rstrip.
    """
    texto = f"{from_milli(milli):f}"
    if "." in texto:
        texto = texto.rstrip("0").rstrip(".")
    return texto.replace(".", ",")


def parse_local_decimal(text: str) -> Decimal:
    """
    '1.210,00' -> Decimal('1210.00'). Inverso de format_money: texto
    en formato local (coma decimal, punto de miles) a Decimal.

    El punto de miles se saca ANTES de convertir la coma en punto
    decimal: al reves, '1.210,00' quedaria '1,21000' (un desastre).
    Uso real: hay precios de venta por encima de 1.000 en el catalogo
    (ej. herramientas), no solo en el sistema viejo.

    No acepta texto vacio (Decimal(''); levanta InvalidOperation):
    decidir que hacer con un campo vacio es responsabilidad de quien
    llama, no de esta funcion.
    """
    cleaned = text.strip().replace(".", "").replace(",", ".")
    return Decimal(cleaned)
