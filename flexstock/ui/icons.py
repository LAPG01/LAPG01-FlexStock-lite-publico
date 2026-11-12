"""
Iconos de la interfaz (barra lateral, botones, avisos).

Los PNG de assets/icons/ son de Lucide (lucide.dev, licencia ISC, ver
assets/icons/LICENSE-lucide.txt), pasados a 64x64 en BLANCO sobre fondo
transparente. Uno solo por icono: el color se le pone aca, en memoria,
asi el mismo archivo sirve para el texto oscuro del modo claro, el claro
del modo oscuro y el blanco de la barra lateral, sin guardar tres copias.

Pillow no es una dependencia nueva: ya la exige ReportLab.

Costo: teñir un icono de 64x64 son ~4.000 pixeles, unos pocos ms. Cada
combinacion (icono, color, tamano) se tiñe UNA vez por ventana raiz y
se reusa: la barra lateral entera son ~10 iconos, una sola vez al abrir.
"""

from __future__ import annotations

import tkinter as tk
from functools import cache
from pathlib import Path

import customtkinter as ctk
from PIL import Image

from .theme import find_app_root

ICONS_DIR = Path(__file__).parent / "assets" / "icons"


@cache
def _base(nombre: str) -> Image.Image:
    with Image.open(ICONS_DIR / f"{nombre}.png") as imagen:
        return imagen.convert("RGBA")


def _hex_a_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    return int(color[0:2], 16), int(color[2:4], 16), int(color[4:6], 16)


def _tenir(base: Image.Image, color: str) -> Image.Image:
    """El icono en 'color', conservando su transparencia (bordes suaves)."""
    pintada = Image.new("RGBA", base.size, (*_hex_a_rgb(color), 255))
    pintada.putalpha(base.getchannel("A"))
    return pintada


def icon(
    widget: tk.Misc,
    nombre: str,
    color: str | tuple[str, str],
    *,
    size: int = 20,
) -> ctk.CTkImage:
    """
    CTkImage del icono 'nombre' en 'color' (uno solo, o (claro, oscuro)
    como los colores de theme.pair). 'size' en pixeles al 100 %: CTk lo
    escala solo en pantallas de alta densidad.
    """
    claro, oscuro = (color, color) if isinstance(color, str) else color
    raiz = find_app_root(widget)
    cache_raiz = getattr(raiz, "_flex_icons", None)
    if cache_raiz is None:
        cache_raiz = raiz._flex_icons = {}
    clave = (nombre, claro, oscuro, size)
    imagen = cache_raiz.get(clave)
    if imagen is None:
        base = _base(nombre)
        imagen = ctk.CTkImage(
            light_image=_tenir(base, claro),
            dark_image=_tenir(base, oscuro),
            size=(size, size),
        )
        cache_raiz[clave] = imagen
    return imagen


def available() -> list[str]:
    return sorted(p.stem for p in ICONS_DIR.glob("*.png"))
