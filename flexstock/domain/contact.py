"""
Limpieza de datos de contacto (correo, telefono) y comparacion de nombres.
Python puro: nada de sqlite3 ni tkinter (regla de domain/).
"""

import re
import unicodedata
from difflib import SequenceMatcher

_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def clean_email(value: str | None, *, actual: str | None = None) -> str | None:
    """
    '' -> None. Un correo sin "algo@algo.algo" se rechaza, salvo que sea
    EXACTAMENTE el que ya estaba guardado ('actual'): hay fichas
    migradas con correos raros y editarles otro dato no debe obligar a
    corregirlos.
    """
    texto = (value or "").strip()
    if not texto:
        return None
    if actual is not None and texto == actual.strip():
        return texto
    if not _EMAIL.match(texto):
        raise ValueError("El correo no parece válido (ej. nombre@empresa.com).")
    return texto


def format_phone(value: str | None) -> str | None:
    """
    '' -> None. Un celular o fijo venezolano de 11 digitos (0412 1234567,
    04121234567, 0412-123-4567...) se guarda como 0412-1234567. Cualquier
    otra cosa (internacional, extension...) se deja como se escribio.
    """
    texto = (value or "").strip()
    if not texto:
        return None
    digitos = re.sub(r"\D", "", texto)
    if len(digitos) == 11 and digitos.startswith("0"):
        return f"{digitos[:4]}-{digitos[4:]}"
    return texto


def _normalizar(nombre: str) -> str:
    sin_tildes = unicodedata.normalize("NFD", nombre)
    sin_tildes = "".join(c for c in sin_tildes if unicodedata.category(c) != "Mn")
    # "C.A." y "CA" son lo mismo: los puntos se quitan, el resto de la
    # puntuacion separa palabras.
    return " ".join(re.findall(r"[A-Z0-9]+", sin_tildes.upper().replace(".", "")))


def name_similarity(a: str, b: str) -> float:
    """
    0 a 1: que tan parecidos son dos nombres de proveedor, sin mirar
    mayusculas, tildes ni puntuacion. 1.0 son el mismo; 0.95 uno es el
    comienzo o esta contenido palabra por palabra en el otro ("MOK" y
    "MOK DISTRIBUIDORA"); si no, el parecido de las letras.
    """
    x, y = _normalizar(a), _normalizar(b)
    if not x or not y:
        return 0.0
    if x == y:
        return 1.0
    corto, largo = sorted((x, y), key=len)
    if len(corto) >= 3 and set(corto.split()) <= set(largo.split()):
        return 0.95
    return SequenceMatcher(None, x, y).ratio()
