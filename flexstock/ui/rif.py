"""
Cedula/RIF partido en tipo (V, E, J, G, P) y numero, para los formularios
que lo piden (Tienda, Proveedor): el tipo se elige de una lista en vez de
escribirlo, que es de donde salian la mitad de los errores de formato.

ui/ puede importar de services/: los tipos son los mismos que valida
CustomerService (TAX_ID_TYPES).
"""

from ..services.customer_service import TAX_ID_TYPES

TIPOS_RIF = [tipo for tipo, _etiqueta in TAX_ID_TYPES]


def partir_rif(rif: str, *, tipo_por_defecto: str = "J") -> tuple[str, str]:
    """'J-12345678-9' -> ('J', '12345678-9'). Lo que no empieza con un tipo
    conocido va entero al campo del numero, para no perder lo ya guardado
    (y 'tipo_por_defecto' queda elegido)."""
    texto = rif.strip().upper()
    tipo, _, resto = texto.partition("-")
    if resto and tipo in TIPOS_RIF:
        return tipo, resto
    return tipo_por_defecto, texto


def armar_rif(tipo: str, numero: str) -> str:
    """('J', '12345678 9') -> 'J-12345678-9'. Vacio si no hay numero. El
    noveno digito es el verificador del RIF y va separado por un guion."""
    crudo = numero.replace("-", "").replace(" ", "")
    if not crudo:
        return ""
    if not crudo.isdigit():
        raise ValueError("El RIF solo lleva numeros (ej. 12345678-9).")
    if not 6 <= len(crudo) <= 9:
        raise ValueError("El RIF lleva entre 6 y 9 numeros, con el verificador.")
    cuerpo = f"{crudo[:8]}-{crudo[8:]}" if len(crudo) == 9 else crudo
    return f"{tipo}-{cuerpo}"
