"""FloatingPicker: campo + lista flotante, 'escribe y Enter'."""

import tkinter as tk
from types import SimpleNamespace

import pytest

from flexstock.ui.picker import FloatingPicker


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


COSAS = [("ABANICO", "A1"), ("ABRAZADERA", "B2"), ("ACEITE", "C3")]


def _picker(root, **kw):
    activados, cambios = [], []
    p = FloatingPicker(
        root,
        claves=lambda c: (c[0], c[1]),
        fila=lambda c: (f"{c[1]} - {c[0]}", "ok" if c[1] == "A1" else "normal"),
        texto_elegido=lambda c: c[0],
        items=COSAS,
        on_activate=activados.append,
        on_change=cambios.append,
        **kw,
    )
    return p, activados, cambios


def test_escribir_filtra_por_cualquiera_de_las_claves(root):
    p, _a, _c = _picker(root)

    p.var.set("abr")
    assert p.filtrados == [("ABRAZADERA", "B2")]
    p.var.set("c3")  # por codigo
    assert p.filtrados == [("ACEITE", "C3")]
    p.var.set("")
    assert len(p.filtrados) == 3


def test_enter_elige_la_resaltada_y_avisa(root):
    p, activados, cambios = _picker(root)
    p.var.set("a")
    p._mover(1)  # baja a la segunda coincidencia

    assert p._on_return() == "break"

    assert p.elegido() == ("ABRAZADERA", "B2")
    assert activados == [("ABRAZADERA", "B2")]
    assert cambios == [("ABRAZADERA", "B2")]
    assert p.var.get() == "ABRAZADERA"


def test_enter_sin_nada_escrito_ni_flechas_no_elige_nada(root):
    p, activados, _c = _picker(root)
    p._abrir()  # al enfocar, la lista muestra todo

    assert p._on_return() is None
    assert p.elegido() is None and activados == []

    p._mover(1)  # con una flecha, SI es una eleccion a proposito
    p._on_return()
    assert p.elegido() is not None


def test_tab_elige_la_primera_coincidencia_sin_activar(root):
    p, activados, cambios = _picker(root)
    p.var.set("ab")

    p._on_tab()

    assert p.elegido() == ("ABANICO", "A1")
    assert cambios == [("ABANICO", "A1")]
    assert activados == []  # pasar de campo no es "confirmar"


def test_escribir_sobre_un_elegido_lo_suelta_y_avisa(root):
    p, _a, cambios = _picker(root)
    p.elegir(COSAS[0])

    p.var.set("zz")

    assert p.elegido() is None
    assert cambios[-1] is None


def test_la_fila_de_nuevo_llama_a_su_accion(root):
    llamadas = []
    p, activados, _c = _picker(
        root, nuevo=("Nuevo cliente...", lambda: llamadas.append(1))
    )
    p.var.set("zzz")  # nada coincide: solo queda "+ Nuevo"

    assert p.lista.get(0, "end") == ("+ Nuevo cliente...",)
    p._on_return()

    assert llamadas == [1] and activados == []


def test_esc_cierra_la_lista_y_sin_lista_sigue_a_la_ventana(root):
    p, _a, _c = _picker(root)
    p._abrir()

    assert p._on_escape() == "break"
    assert p.abierta is False
    assert p._on_escape() is None


def test_limpiar_vacia_el_campo_y_avisa(root):
    p, _a, cambios = _picker(root)
    p.elegir(COSAS[1])

    p.limpiar()

    assert p.var.get() == "" and p.elegido() is None
    assert cambios[-1] is None


def test_set_items_conserva_el_elegido_si_sigue_y_lo_suelta_si_no(root):
    p, _a, _c = _picker(root)
    p.elegir(COSAS[0])

    p.set_items(COSAS[:2])
    assert p.elegido() == COSAS[0]
    p.set_items(COSAS[1:])
    assert p.elegido() is None


def test_las_filas_con_stock_van_en_color_y_el_clic_elige(root):
    p, activados, _c = _picker(root)
    p.var.set("")

    assert p.lista.itemcget(0, "foreground") != p.lista.itemcget(1, "foreground")
    p._on_clic_en_lista(SimpleNamespace(y=0))  # nearest(0) = primera fila
    assert activados == [COSAS[0]]


def test_la_barra_solo_aparece_si_hay_mas_filas_que_las_que_caben(root):
    p, _a, _c = _picker(root, altura=2)
    p.grid()

    p.var.set("")  # 3 coincidencias, caben 2
    p._abrir()
    assert p._barra.winfo_manager() == "pack"

    p.var.set("ace")  # 1 coincidencia
    p._abrir()
    assert p._barra.winfo_manager() == ""


def test_un_focusout_pasajero_no_cierra_la_lista_si_el_foco_sigue_en_el_campo(
    root, esperar
):
    """Cerrarla ahi y reabrirla con el FocusIn siguiente se veia como parpadeo."""
    root.deiconify()
    p, _a, _c = _picker(root)
    p.pack()
    root.geometry("400x200+0+0")
    root.update()
    p.entry.focus_force()
    esperar(root, lambda: str(root.focus_get()).startswith(str(p.entry)))
    p._abrir()
    assert p.abierta

    p._cerrar_si_perdio_foco()  # el foco esta en el campo: sigue abierta
    assert p.abierta

    boton = tk.Entry(root)
    boton.pack()
    boton.focus_force()
    esperar(root, lambda: root.focus_get() is boton)
    p._cerrar_si_perdio_foco()  # el foco se fue a otro lado: ahora si cierra
    assert not p.abierta


def test_si_el_campo_se_mueve_la_lista_se_cierra(root, esperar):
    """Con el formulario scrolleado, la lista quedaria flotando lejos de su
    campo: se cierra."""
    root.deiconify()
    root.geometry("500x300+0+0")
    arriba = tk.Frame(root, height=10)
    arriba.pack()
    p, _a, _c = _picker(root)
    p.pack()
    esperar(root, lambda: p.entry.winfo_ismapped())
    p._abrir()
    assert p.abierta
    assert p._capa.winfo_manager() == "place"  # dentro de la ventana, no aparte

    arriba.configure(height=80)  # el campo baja, como al scrollear
    esperar(root, lambda: not p.abierta, que="que la lista se cierre")

    assert p._capa.winfo_manager() == ""
