"""Tests del orden de resultados de los buscadores en memoria."""

from dataclasses import dataclass

from flexstock.ui.search import split_search_results
from flexstock.ui.widgets import product_at_listbox_index


@dataclass
class _Producto:
    name: str
    sku: str


def test_las_coincidencias_al_inicio_van_antes_que_las_internas():
    productos = [
        _Producto("Bola con manguera", "B-1"),
        _Producto("Manguera de jardin", "M-1"),
        _Producto("Adaptador para manguera", "A-1"),
        _Producto("Manometro", "M-2"),
    ]

    empiezan, coincidencias = split_search_results(
        productos, "man", lambda p: p.name, lambda p: p.sku
    )

    assert [p.name for p in empiezan] == ["Manguera de jardin", "Manometro"]
    assert [p.name for p in coincidencias] == [
        "Bola con manguera",
        "Adaptador para manguera",
    ]


def test_un_sku_que_empieza_la_busqueda_tambien_es_resultado_principal():
    productos = [_Producto("Bola", "MAN-001"), _Producto("Manilla", "X-1")]

    empiezan, coincidencias = split_search_results(
        productos, "man", lambda p: p.name, lambda p: p.sku
    )

    assert empiezan == productos
    assert coincidencias == []


def test_busqueda_vacia_conserva_todos_los_items_y_sin_segundo_grupo():
    productos = [_Producto("Bola", "B-1"), _Producto("Manguera", "M-1")]

    empiezan, coincidencias = split_search_results(
        productos, "  ", lambda p: p.name, lambda p: p.sku
    )

    assert empiezan == productos
    assert coincidencias == []


def test_el_rotulo_de_coincidencias_no_apunta_a_un_producto():
    productos = [_Producto("Manguera", "M-1"), _Producto("Bola", "B-1")]

    assert product_at_listbox_index(productos, 1, 1) is None
    assert product_at_listbox_index(productos, 2, 1) == productos[1]
