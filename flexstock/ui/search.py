"""Orden comun para los buscadores que ya filtran colecciones en memoria.

Una coincidencia al principio del nombre es normalmente la que se quiso
encontrar. Las coincidencias internas siguen disponibles, pero despues: asi
``MANGUERA`` aparece antes que ``BOLA CON MANGUERA`` al escribir ``man``.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")


def split_search_results(
    items: Iterable[T], texto: str, *fields: Callable[[T], str | None]
) -> tuple[list[T], list[T]]:
    """Devuelve ``(empiezan_por, coincidencias_internas)``.

    La lista de entrada ya viene en su orden natural, que se conserva dentro
    de cada grupo. Con el buscador vacio no hay un segundo grupo.
    """
    needle = texto.strip().upper()
    if not needle:
        return list(items), []

    empiezan: list[T] = []
    coincidencias: list[T] = []
    for item in items:
        values = [value.upper() for field in fields if (value := field(item))]
        if any(value.startswith(needle) for value in values):
            empiezan.append(item)
        elif any(needle in value for value in values):
            coincidencias.append(item)
    return empiezan, coincidencias
