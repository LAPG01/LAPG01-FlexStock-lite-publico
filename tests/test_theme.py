"""Helpers puros del tema, sin requerir una ventana Tk."""

import pytest

from flexstock.ui.theme import font_shortcut_direction


@pytest.mark.parametrize("keysym", ["plus", "equal", "KP_Add"])
def test_atajos_de_aumentar_cubren_teclado_normal_y_numerico(keysym):
    assert font_shortcut_direction(keysym) == 1


@pytest.mark.parametrize("keysym", ["minus", "underscore", "KP_Subtract"])
def test_atajos_de_reducir_cubren_todas_las_variantes_de_menos(keysym):
    assert font_shortcut_direction(keysym) == -1


def test_otras_teclas_control_no_cambian_el_tamano():
    assert font_shortcut_direction("a") is None
