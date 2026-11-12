"""
Tests de ui/kit.py: los widgets CustomTkinter que hablan "ttk".

Las paginas se escribieron contra ttk y siguen llamando state(),
current(), <<ComboboxSelected>>, style=..., width en caracteres. Cada
test fija una de esas promesas: si kit dejara de cumplirla, la pagina
que la usa se romperia sin que su propio test lo explique.
"""

import tkinter as tk

import pytest

from flexstock.ui import kit
from flexstock.ui.theme import DARK, LIGHT, ThemeManager


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


# ------------------------------------------------------------
# Button
# ------------------------------------------------------------
def test_boton_state_como_ttk(root):
    boton = kit.Button(root, text="Guardar")

    assert "disabled" not in boton.state()
    boton.state(["disabled"])
    assert boton.state() == ("disabled",)
    assert boton.instate(["disabled"])
    boton.state(["!disabled"])
    assert str(boton.cget("state")) == "normal"


def test_boton_invoke_llama_al_comando_y_no_si_esta_deshabilitado(root):
    llamadas = []
    boton = kit.Button(root, text="x", command=lambda: llamadas.append(1))

    boton.invoke()
    boton.configure(state="disabled")
    boton.invoke()

    assert llamadas == [1]


def test_boton_recuerda_su_estilo(root):
    boton = kit.Button(root, text="Crear", style="Success.TButton")

    assert boton.cget("style") == "Success.TButton"
    assert boton.cget("fg_color") == (LIGHT.success_bg, DARK.success_bg)
    boton.configure(style="Danger.TButton")
    assert boton.cget("style") == "Danger.TButton"
    assert boton.cget("fg_color")[0] == LIGHT.danger_bg


def test_boton_de_contorno_tiene_borde_rojo_y_fondo_claro(root):
    """Eliminar no grita: borde y texto rojos, el relleno es el de la tarjeta."""
    boton = kit.Button(root, text="Eliminar", style="Danger.Outline.TButton")

    assert boton.cget("fg_color")[0] == LIGHT.surface
    assert boton.cget("text_color")[0] == LIGHT.danger_bg
    assert boton.cget("border_color")[0] == LIGHT.danger_bg
    assert boton.cget("border_width") > 0
    # y al volver a un estilo solido, el borde se va
    boton.configure(style="Danger.TButton")
    assert boton.cget("border_width") == 0


def test_boton_deshabilitado_se_ve_gris_y_vuelve_a_su_color(root):
    boton = kit.Button(
        root, text="Confirmar", style="Success.TButton", state="disabled"
    )
    assert boton.cget("fg_color")[0] == LIGHT.button_bg

    boton.configure(state="normal")
    assert boton.cget("fg_color")[0] == LIGHT.success_bg


def test_boton_grande_es_mas_grande_que_uno_comun(root):
    comun = kit.Button(root, text="Iniciar factura", style="Success.TButton")
    grande = kit.Button(root, text="Iniciar factura", style="Big.Success.TButton")
    root.update_idletasks()

    assert grande.winfo_reqheight() > comun.winfo_reqheight()
    assert grande.winfo_reqwidth() > comun.winfo_reqwidth()


# ------------------------------------------------------------
# Label
# ------------------------------------------------------------
def test_label_estilo_cambia_color_y_se_puede_leer(root):
    etiqueta = kit.Label(root, text="Listo", style="Ok.TLabel")

    assert etiqueta.cget("style") == "Ok.TLabel"
    assert etiqueta.cget("text_color")[0] == LIGHT.success_bg
    etiqueta.configure(style="Error.TLabel")
    assert etiqueta.cget("style") == "Error.TLabel"
    assert etiqueta.cget("text_color")[0] == LIGHT.danger_bg


def test_label_con_width_tiene_ancho_fijo_como_ttk(root):
    """En CTk el width es un minimo; en ttk, fijo. El resumen de cliente
    de Nueva factura depende de que un nombre largo NO lo ensanche."""
    var = tk.StringVar(value="corto")
    etiqueta = kit.Label(root, textvariable=var, width=10)
    etiqueta.pack()
    root.update_idletasks()
    antes = etiqueta.winfo_reqwidth()

    var.set("un nombre larguisimo que no entra en diez caracteres")
    root.update_idletasks()

    assert etiqueta.winfo_reqwidth() == antes


def test_label_item_access(root):
    etiqueta = kit.Label(root, text="hola")

    assert etiqueta["text"] == "hola"
    etiqueta["text"] = "chau"
    assert etiqueta.cget("text") == "chau"


# ------------------------------------------------------------
# Entry / Combobox
# ------------------------------------------------------------
def test_entry_bind_sin_comando_devuelve_lo_atado(root):
    campo = kit.Entry(root)

    assert campo.bind("<Return>") == ""
    campo.bind("<Return>", lambda _e: None)
    assert campo.bind("<Return>") != ""


def test_entry_width_en_caracteres(root):
    angosto = kit.Entry(root, width=4)
    ancho = kit.Entry(root, width=30)

    assert ancho.cget("width") > angosto.cget("width")


def test_combobox_arranca_vacio_sin_variable_como_ttk(root):
    combo = kit.Combobox(root, values=["Efectivo", "Pago movil"], state="readonly")

    assert combo.get() == ""
    assert combo.current() == -1


def test_combobox_current_lee_y_elige(root):
    var = tk.StringVar()
    combo = kit.Combobox(root, values=["a", "b", "c"], textvariable=var)

    combo.current(2)

    assert combo.current() == 2
    assert var.get() == "c"
    assert combo.cget("values") == ("a", "b", "c")


def test_combobox_avisa_combobox_selected_al_elegir_de_la_lista(root):
    combo = kit.Combobox(root, values=["a", "b"])
    elegidos = []
    combo.bind("<<ComboboxSelected>>", lambda e: elegidos.append(e.widget.get()))

    # Lo que hace CTk cuando se elige un valor del menu desplegable.
    combo._dropdown_callback("b")

    assert elegidos == ["b"]


def test_combobox_current_no_dispara_combobox_selected(root):
    """Igual que ttk: elegir por codigo no es una eleccion del usuario."""
    combo = kit.Combobox(root, values=["a", "b"])
    elegidos = []
    combo.bind("<<ComboboxSelected>>", lambda e: elegidos.append(1))

    combo.current(1)

    assert elegidos == []


def test_combobox_values_por_item(root):
    combo = kit.Combobox(root, values=["a"])

    combo["values"] = ["x", "y"]

    assert combo["values"] == ("x", "y")


# ------------------------------------------------------------
# Checkbutton / Radiobutton
# ------------------------------------------------------------
def test_checkbutton_invoke_alterna_la_variable_y_llama_al_comando(root):
    var = tk.BooleanVar(value=False)
    llamadas = []
    casilla = kit.Checkbutton(
        root, text="Cobrar IVA", variable=var, command=lambda: llamadas.append(1)
    )

    casilla.invoke()

    assert var.get() is True
    assert llamadas == [1]


def test_checkbutton_respeta_el_valor_inicial(root):
    var = tk.BooleanVar(value=True)
    casilla = kit.Checkbutton(root, text="Activo", variable=var)

    assert casilla.get() == 1


def test_radiobutton_invoke_elige_su_valor(root):
    var = tk.StringVar(value="bcv")
    opcion = kit.Radiobutton(root, text="Propia", variable=var, value="propia")

    opcion.invoke()

    assert var.get() == "propia"


# ------------------------------------------------------------
# Frame / LabelFrame
# ------------------------------------------------------------
def test_frame_vacio_no_ocupa_lugar(root):
    """ttk.Frame vacio mide 0; CTkFrame trae 200x200 de fabrica."""
    marco = kit.Frame(root)
    marco.pack()
    root.update_idletasks()

    assert marco.winfo_reqwidth() <= 1
    assert marco.winfo_reqheight() <= 1


def test_frame_padding_deja_margen_interior(root):
    marco = kit.Frame(root, padding=20)
    marco.pack()
    hijo = kit.Frame(marco, width=50, height=30)
    hijo.pack()
    root.update_idletasks()

    assert marco.winfo_reqwidth() == 50 + 40
    assert marco.winfo_reqheight() == 30 + 40


def test_frame_estilo_tarjeta(root):
    tarjeta = kit.Frame(root, style="Card.TFrame", padding=8)

    assert tarjeta.cget("style") == "Card.TFrame"
    assert tarjeta.cget("fg_color")[0] == LIGHT.surface
    assert tarjeta.cget("border_width") == 1


def test_labelframe_hijos_van_adentro_y_pack_mueve_la_tarjeta(root):
    seccion = kit.LabelFrame(root, text="Cliente", padding=12)
    seccion.pack(fill="both")
    campo = kit.Entry(seccion)
    campo.pack()

    assert campo.master is seccion
    assert seccion.cget("text") == "Cliente"
    assert seccion._outer.winfo_manager() == "pack"
    seccion.pack_forget()
    assert seccion._outer.winfo_manager() == ""


def test_labelframe_before_apunta_a_la_tarjeta_de_afuera(root):
    """Con la de adentro, Tk empacaba la seccion DENTRO de la otra (bug
    real: "Datos de la factura" quedaba encimada sobre "Cliente")."""
    contenedor = kit.Frame(root)
    contenedor.pack()
    cliente = kit.LabelFrame(contenedor, text="Cliente")
    cliente.pack(side="left")
    datos = kit.LabelFrame(contenedor, text="Datos")
    datos.pack(side="right", before=cliente)

    assert datos._outer.master is contenedor
    assert datos._outer.pack_info()["in"] is contenedor


def test_labelframe_destruir_se_lleva_la_tarjeta(root):
    seccion = kit.LabelFrame(root, text="Logo")
    afuera = seccion._outer

    seccion.destroy()

    assert not afuera.winfo_exists()


def test_destruir_al_padre_destruye_la_seccion_sin_errores(root):
    padre = kit.Frame(root)
    seccion = kit.LabelFrame(padre, text="Precios")
    kit.Label(seccion, text="Costo:").pack()

    padre.destroy()

    assert not seccion.winfo_exists()


# ------------------------------------------------------------
# Spinbox / Scrollbar
# ------------------------------------------------------------
def test_spinbox_suma_y_resta_dentro_de_su_rango(root):
    var = tk.StringVar(value="30")
    numero = kit.Spinbox(root, from_=1, to=31, textvariable=var)

    numero._sumar(1)
    numero._sumar(1)
    assert var.get() == "31"
    var.set("1")
    numero._sumar(-1)
    assert var.get() == "1"


def test_spinbox_deja_un_texto_invalido_como_esta(root):
    var = tk.StringVar(value="abc")
    numero = kit.Spinbox(root, from_=1, to=31, textvariable=var)

    numero._sumar(1)

    assert var.get() == "abc"


def test_scrollbar_no_impone_un_alto_minimo(root):
    """CTk trae 200 px de largo: eso era un alto minimo para cualquier
    tabla con barra, y a 150 % empujaba los botones fuera de la ventana."""
    barra = kit.Scrollbar(root, orient="vertical")

    assert barra.winfo_reqheight() < 100


# ------------------------------------------------------------
# tema
# ------------------------------------------------------------
def test_cambiar_tamano_de_letra_agranda_las_fuentes_ctk(root):
    tema = ThemeManager(root)
    etiqueta = kit.Label(root, text="Hola")
    antes = etiqueta.cget("font").cget("size")

    tema.set_font_scale(150)

    assert etiqueta.cget("font").cget("size") > antes


def test_el_tema_no_pinta_los_lienzos_internos_de_ctk(root):
    """Pintarlos dejaba las esquinas de los botones en el color de fondo."""
    import customtkinter as ctk

    marco = ctk.CTkFrame(root, fg_color="#123456")
    boton = ctk.CTkButton(marco, text="x")
    tema = ThemeManager(root)

    tema.apply("light")

    assert boton._canvas.cget("bg") == "#123456"


# ------------------------------------------------------------
# Dialog: trampas de CTkToplevel (sobre todo en Windows)
# ------------------------------------------------------------
def test_grab_set_de_una_ventana_que_no_se_ve_no_revienta(root, monkeypatch):
    """
    En Windows, CTkToplevel se esconde unos ms al nacer y los dialogos
    llaman a grab_set() en su __init__: Tk respondia "window not
    viewable" y el dialogo no abria. Ahora se reintenta.
    """
    dialogo = kit.Dialog(root)
    reintentos = []
    monkeypatch.setattr(dialogo, "after", lambda ms, fn: reintentos.append(ms))

    def _no_se_ve():
        raise tk.TclError("grab failed: window not viewable")

    monkeypatch.setattr(kit.ctk.CTkToplevel, "grab_set", lambda self: _no_se_ve())

    dialogo.grab_set()  # no lanza

    assert reintentos == [20]
    dialogo.destroy()


def test_grab_set_deja_de_reintentar_despues_de_un_rato(root, monkeypatch):
    dialogo = kit.Dialog(root)
    reintentos = []
    monkeypatch.setattr(dialogo, "after", lambda ms, fn: reintentos.append(ms))
    monkeypatch.setattr(
        kit.ctk.CTkToplevel,
        "grab_set",
        lambda self: (_ for _ in ()).throw(tk.TclError("not viewable")),
    )

    for _ in range(kit.Dialog._GRAB_REINTENTOS + 10):
        dialogo.grab_set()

    assert len(reintentos) == kit.Dialog._GRAB_REINTENTOS
    dialogo.destroy()


def test_ctk_no_pisa_el_tope_de_tamano_de_un_dialogo(root):
    """CTk re-aplica SU minimo/maximo al segundo de abrir: no debe borrar
    el tope que pone size_dialog_to_screen."""
    dialogo = kit.Dialog(root)
    dialogo.maxsize(900, 700)

    dialogo._set_scaled_min_max()  # lo que CTk corre con after(1000)

    assert dialogo.maxsize() == (900, 700)
    dialogo.destroy()


def test_un_dialogo_nuevo_no_se_muestra_hasta_estar_armado(root, esperar):
    """
    Los dialogos miden su contenido con update_idletasks(), que MUESTRA una
    ventana nueva (chica, arriba a la izquierda); despues se centraban y
    agrandaban: se veia el salto. Ahora nace escondido y aparece recien con
    su tamano y lugar finales.
    """

    class Demo(kit.Dialog):
        def __init__(self, master):
            super().__init__(master)
            kit.Label(self, text="Hola").pack(padx=40, pady=40)
            self.update_idletasks()  # lo que hacen todos los dialogos reales
            tk.Wm.wm_geometry(self, "420x310+90+70")
            self.grab_set()

    dialogo = Demo(root)

    assert not dialogo.winfo_ismapped()  # nadie lo vio chico
    esperar(dialogo, lambda: dialogo.winfo_ismapped(), que="aparezca el dialogo")
    assert (dialogo.winfo_width(), dialogo.winfo_height()) == (420, 310)
    dialogo.destroy()


def test_destruir_un_dialogo_antes_de_que_aparezca_no_falla(root):
    dialogo = kit.Dialog(root)
    dialogo.destroy()
    root.update()  # el after pendiente se cancelo: nada que reclamar


def test_un_dialogo_se_muestra_en_la_posicion_que_se_le_pidio(root, esperar):
    """
    Una ficha (la de Cliente) llegaba al momento de mostrarse con su posicion
    vuelta a +0+0 y se abria pegada a la esquina de la pantalla. Los
    ayudantes que posicionan anotan _posicion_inicial y Dialog la vuelve a
    pedir justo antes de mostrarse.
    """

    class Demo(kit.Dialog):
        def __init__(self, master):
            super().__init__(master)
            kit.Label(self, text="Hola").pack(padx=40, pady=40)
            tk.Wm.wm_geometry(self, "300x200+120+90")
            self._posicion_inicial = (120, 90)
            # lo que le pasaba a la ficha de Cliente
            tk.Wm.wm_geometry(self, "+0+0")

    dialogo = Demo(root)
    esperar(dialogo, lambda: dialogo.winfo_ismapped(), que="aparezca el dialogo")

    assert (dialogo.winfo_x(), dialogo.winfo_y()) == (120, 90)
    dialogo.destroy()


def test_aviso_con_x_se_cierra_y_se_recuerda_con_su_clave(root):
    primero = kit.Notice(root, text="Una pista.", closable=True, key="pista")
    primero.grid(row=0, column=0)
    assert primero.winfo_manager() == "grid"

    primero._boton_cerrar.invoke()

    assert primero.cerrado is True
    assert primero.winfo_manager() == ""
    # Otra ventana con la misma pista nace cerrada; con otra clave, no.
    segundo = kit.Notice(root, text="Una pista.", closable=True, key="pista")
    otro = kit.Notice(root, text="Otra.", closable=True, key="otra")
    assert segundo.cerrado is True
    assert otro.cerrado is False


def test_aviso_cerrado_no_reaparece_con_mostrar(root):
    aviso = kit.Notice(root, text="Pista.", closable=True, key="pista")
    aviso.grid(row=0, column=0)
    aviso.cerrar()

    aviso.mostrar(True)

    assert aviso.winfo_manager() == ""


def test_aviso_sin_closable_no_lleva_x(root):
    aviso = kit.Notice(root, text="Importante.")

    assert not hasattr(aviso, "_boton_cerrar")


# ------------------------------------------------------------
# Teclado en combos y radios
# ------------------------------------------------------------
def test_combobox_readonly_las_flechas_cambian_la_opcion_y_avisan(root):
    elegidos = []
    combo = kit.Combobox(root, values=["a", "b", "c"], state="readonly")
    combo.bind("<<ComboboxSelected>>", lambda e: elegidos.append(e.widget.get()))

    assert combo._flecha(+1) == "break"  # sin nada elegido: la primera
    assert combo.get() == "a"
    combo._flecha(+1)
    combo._flecha(+1)
    combo._flecha(+1)  # no pasa del final
    assert combo.get() == "c"
    combo._flecha(-1)
    assert combo.get() == "b"
    assert elegidos == ["a", "b", "c", "b"]


def test_combobox_editable_no_se_traga_las_flechas(root):
    combo = kit.Combobox(root, values=["a", "b"], state="normal")

    assert combo._flecha(+1) is None
    assert combo.get() == ""


def test_combobox_enter_pasa_al_siguiente_solo_si_se_pide(root):
    root.deiconify()
    combo = kit.Combobox(root, values=["a"], state="readonly")
    otro = kit.Entry(root)
    combo.pack()
    otro.pack()
    root.update()
    assert combo._entry.bind("<Return>") == ""  # por defecto no toca Enter

    combo.enter_pasa_al_siguiente()

    assert combo._entry.bind("<Return>") != ""
    assert combo._entry.bind("<KP_Enter>") != ""


def test_radiobutton_las_flechas_eligen_el_vecino_del_grupo(root):
    var = tk.StringVar(value="a")
    uno = kit.Radiobutton(root, text="A", value="a", variable=var)
    dos = kit.Radiobutton(root, text="B", value="b", variable=var)
    otro_grupo = kit.Radiobutton(root, text="X", value="x", variable=tk.StringVar())
    for r in (uno, dos, otro_grupo):
        r.pack()

    assert uno._mover_en_grupo(+1) == "break"
    assert var.get() == "b"
    dos._mover_en_grupo(+1)  # ya es el ultimo: se queda
    assert var.get() == "b"
    dos._mover_en_grupo(-1)
    assert var.get() == "a"


def test_enter_en_una_casilla_solo_la_alterna_si_se_pide(root):
    var = tk.BooleanVar(value=False)
    casilla = kit.Checkbutton(root, text="IVA", variable=var)
    assert casilla._canvas.bind("<Return>") == ""  # por defecto Enter es del form

    casilla.enter_tambien_activa()
    casilla._activar_con_teclado()

    assert casilla._canvas.bind("<Return>") != ""
    assert var.get() is True
