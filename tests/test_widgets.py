"""
Tests de SearchableTable: ordenar con click en encabezado, doble-click,
tags de fila, navegar la tabla sin perder la busqueda.

    pytest -q
"""

import tkinter as tk
from types import SimpleNamespace

import pytest

from flexstock.ui import kit
from flexstock.ui.widgets import (
    LoadingDialog,
    SearchableTable,
    _clave_de_orden,
    con_pantalla_de_carga,
    fill_product_listbox,
    lines_tree,
    product_listbox,
    repartir_anchos,
)


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _tabla(root, **kwargs):
    return SearchableTable(
        root,
        columns=("nombre", "total"),
        headings={"nombre": "Nombre", "total": "Total"},
        **kwargs,
    )


# ------------------------------------------------------------
# _clave_de_orden: numeros vs texto
# ------------------------------------------------------------
def test_clave_de_orden_reconoce_dinero_local():
    assert _clave_de_orden("$ 2,00") < _clave_de_orden("$ 10,00")


def test_clave_de_orden_reconoce_miles_con_punto():
    assert _clave_de_orden("$ 1.234,56") == (0, 1234.56)


def test_clave_de_orden_cae_a_texto_si_no_es_numero():
    assert _clave_de_orden("ABRAZADERA") == (1, "ABRAZADERA")


# ------------------------------------------------------------
# ordenar con click en el encabezado
# ------------------------------------------------------------
def test_el_titulo_ordenado_lleva_flecha_y_cambia_de_sentido(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("B", "2")), ("2", ("A", "1"))])

    tabla._on_ordenar("nombre")
    assert tabla.tree.heading("nombre", "text") == "Nombre \u25b2"
    assert tabla.tree.heading("total", "text") == "Total"  # solo la ordenada

    tabla._on_ordenar("nombre")
    assert tabla.tree.heading("nombre", "text") == "Nombre \u25bc"

    tabla._on_ordenar("total")
    assert tabla.tree.heading("nombre", "text") == "Nombre"
    assert tabla.tree.heading("total", "text") == "Total \u25b2"


def test_al_recargar_filas_la_flecha_se_va(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("B", "2"))])
    tabla._on_ordenar("nombre")

    tabla.set_rows([("1", ("B", "2"))])

    assert tabla.tree.heading("nombre", "text") == "Nombre"


def test_click_en_encabezado_ordena_ascendente(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Zeta", "$ 1,00")), ("2", ("Alfa", "$ 2,00"))])

    tabla._on_ordenar("nombre")

    assert tabla.tree.get_children() == ("2", "1")  # Alfa, Zeta


def test_click_dos_veces_invierte_el_orden(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Zeta", "$ 1,00")), ("2", ("Alfa", "$ 2,00"))])

    tabla._on_ordenar("nombre")
    tabla._on_ordenar("nombre")

    assert tabla.tree.get_children() == ("1", "2")  # Zeta, Alfa


def test_ordenar_columna_numerica_por_valor_no_alfabeticamente(root):
    tabla = _tabla(root)
    tabla.set_rows(
        [("1", ("A", "$ 10,00")), ("2", ("B", "$ 2,00")), ("3", ("C", "$ 100,00"))]
    )

    tabla._on_ordenar("total")

    assert tabla.tree.get_children() == ("2", "1", "3")  # 2, 10, 100


# ------------------------------------------------------------
# doble-click
# ------------------------------------------------------------
def test_doble_click_registra_el_binding(root):
    """
    Tk rechaza sintetizar un '<Double-1>' via event_generate (depende
    de timing real entre clicks, no se puede inyectar), asi que esto
    verifica que el binding quedo registrado - la funcion en si
    (on_double_click) ya es un callable de Python probado aparte por
    quien la pasa (ver test_products_page.py, etc.).
    """
    tabla = _tabla(root, on_double_click=lambda: None)

    assert tabla.tree.bind("<Double-1>") != ""


# Enter hace lo mismo que el doble clic, desde la tabla y desde el
# buscador. Los bindings se verifican registrados y la LOGICA se prueba
# llamando a _abrir_seleccionada: un <Return> sintetizado con
# event_generate no se despacha con la ventana oculta (Tk descarta los
# eventos de teclado a un widget que no esta mapeado), y deiconify() en
# los tests haria aparecer ventanas de verdad en pantalla. Mismo
# criterio que ya usaba el test de <Double-1> de mas arriba.
def test_enter_queda_atado_en_la_tabla_y_en_el_buscador(root):
    tabla = _tabla(root, on_double_click=lambda: None)

    assert tabla.tree.bind("<Return>") != ""
    assert tabla._entry.bind("<Return>") != ""


def test_sin_on_double_click_tampoco_hay_enter(root):
    tabla = _tabla(root)  # sin on_double_click

    assert tabla.tree.bind("<Return>") == ""
    assert tabla._entry.bind("<Return>") == ""


def test_enter_abre_la_fila_que_estaba_elegida(root):
    """
    El flujo natural: escribir, bajar con la flecha (que a proposito no
    le saca el foco al buscador) y abrir sin ir al mouse.
    """
    abiertas = []
    tabla = _tabla(root, on_double_click=lambda: abiertas.append(1))
    tabla.set_rows([("1", ("Alfa", "$ 1,00")), ("2", ("Beta", "$ 2,00"))])
    tabla._mover_seleccion(1)  # como la flecha abajo
    tabla._mover_seleccion(1)

    tabla._abrir_seleccionada(lambda: abiertas.append(1))

    assert abiertas == [1]
    assert tabla.tree.selection() == ("2",)  # la que estaba elegida, no otra


def test_enter_sin_nada_elegido_abre_la_primera(root):
    """Escribir un SKU, ver un unico resultado y apretar Enter: no hay
    que bajar con la flecha antes."""
    abiertas = []
    tabla = _tabla(root, on_double_click=lambda: None)
    tabla.set_rows([("7", ("Único resultado", "$ 1,00"))])

    tabla._abrir_seleccionada(lambda: abiertas.append(1))

    assert abiertas == [1]
    assert tabla.tree.selection() == ("7",)


def test_enter_con_la_tabla_vacia_no_abre_nada(root):
    abiertas = []
    tabla = _tabla(root, on_double_click=lambda: None)

    resultado = tabla._abrir_seleccionada(lambda: abiertas.append(1))

    assert abiertas == []
    assert resultado == "break"  # el Entry no propaga el Enter igual


def test_sin_on_double_click_no_registra_binding(root):
    tabla = _tabla(root)  # sin on_double_click

    assert tabla.tree.bind("<Double-1>") == ""


# ------------------------------------------------------------
# tags de fila
# ------------------------------------------------------------
def test_set_row_tag_y_tag_style(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Zeta", "$ 1,00"))])

    tabla.tag_style("cancelada", foreground="gray")
    tabla.set_row_tag("1", "cancelada")

    assert tabla.tree.item("1", "tags") == ("cancelada",)


# ------------------------------------------------------------
# flechas en la barra de busqueda mueven la seleccion
# ------------------------------------------------------------
def test_flecha_abajo_selecciona_la_primera_fila_sin_seleccion_previa(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Alfa", "$ 1,00")), ("2", ("Beta", "$ 2,00"))])

    tabla._mover_seleccion(1)

    assert tabla.tree.selection() == ("1",)


def test_flecha_abajo_avanza_desde_la_seleccion_actual(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Alfa", "$ 1,00")), ("2", ("Beta", "$ 2,00"))])
    tabla.tree.selection_set("1")

    tabla._mover_seleccion(1)

    assert tabla.tree.selection() == ("2",)


def test_flecha_abajo_no_pasa_de_la_ultima_fila(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Alfa", "$ 1,00")), ("2", ("Beta", "$ 2,00"))])
    tabla.tree.selection_set("2")

    tabla._mover_seleccion(1)

    assert tabla.tree.selection() == ("2",)  # se queda en la ultima, no revienta


def test_flecha_arriba_retrocede_desde_la_seleccion_actual(root):
    tabla = _tabla(root)
    tabla.set_rows([("1", ("Alfa", "$ 1,00")), ("2", ("Beta", "$ 2,00"))])
    tabla.tree.selection_set("2")

    tabla._mover_seleccion(-1)

    assert tabla.tree.selection() == ("1",)


def test_mover_seleccion_con_tabla_vacia_no_falla(root):
    tabla = _tabla(root)

    tabla._mover_seleccion(1)  # no debe lanzar

    assert tabla.tree.selection() == ()


# ------------------------------------------------------------
# seguir escribiendo con el foco en la tabla
# ------------------------------------------------------------
def test_letra_con_foco_en_la_tabla_se_manda_a_la_busqueda(root):
    tabla = _tabla(root)
    tabla.search_var.set("mar")

    tabla._on_tecla_en_tabla(SimpleNamespace(keysym="t", char="t"))

    assert tabla.search_var.get() == "mart"


def test_backspace_con_foco_en_la_tabla_borra_de_la_busqueda(root):
    tabla = _tabla(root)
    tabla.search_var.set("mart")

    tabla._on_tecla_en_tabla(SimpleNamespace(keysym="BackSpace", char="\x08"))

    assert tabla.search_var.get() == "mar"


def test_backspace_con_busqueda_vacia_no_falla(root):
    tabla = _tabla(root)

    tabla._on_tecla_en_tabla(SimpleNamespace(keysym="BackSpace", char="\x08"))

    assert tabla.search_var.get() == ""


def test_flecha_con_foco_en_la_tabla_no_toca_la_busqueda(root):
    """Down/Up no son 'seguir escribiendo': los deja pasar (return None)
    para que el Treeview siga navegando con su comportamiento normal."""
    tabla = _tabla(root)
    tabla.search_var.set("mar")

    resultado = tabla._on_tecla_en_tabla(SimpleNamespace(keysym="Down", char=""))

    assert resultado is None
    assert tabla.search_var.get() == "mar"


# ------------------------------------------------------------
# LoadingDialog / con_pantalla_de_carga
# ------------------------------------------------------------
def test_loading_dialog_muestra_el_texto(root):
    dialogo = LoadingDialog(root, "Exportando...")

    # widget["opt"] devuelve un objeto Tcl, no un str piano - str() de
    # por medio, mismo motivo que en otros lados del proyecto donde se
    # compara una opcion de un widget ttk contra texto.
    assert str(dialogo._barra["mode"]) == "indeterminate"
    dialogo.cerrar()


def test_loading_dialog_ignora_el_cierre_manual(root):
    dialogo = LoadingDialog(root)

    # protocol() devuelve el nombre del comando Tcl registrado, no ""
    # (lo mismo que usan los demas dialogos para probar el atajo Esc,
    # ver test_customers_page.py, que prueba el atajo Esc igual) -
    # confirma que WM_DELETE_WINDOW quedo pisado, sin ejecutarlo (eso
    # cerraria la ventana real y dejaria root sin foco para el resto).
    assert dialogo.protocol("WM_DELETE_WINDOW") != ""
    dialogo.cerrar()


def test_con_pantalla_de_carga_corre_el_trabajo_y_cierra_la_ventanita(root, esperar):
    hecho = []

    con_pantalla_de_carga(root, "Exportando...", lambda: hecho.append(1))

    # 'trabajo' esta diferido con after(30, ...): todavia no corrio.
    assert hecho == []
    ventanitas_antes = [
        w for w in root.winfo_children() if isinstance(w, LoadingDialog)
    ]
    assert len(ventanitas_antes) == 1

    # update() solo procesa eventos YA vencidos - un after(30,...) recien
    # creado no lo esta. Se espera a que el trabajo haya corrido, no una
    # cantidad fija de milisegundos: dormir 200ms y mirar una sola vez
    # es una carrera contra lo cargada que este la maquina (ver conftest).
    esperar(root, lambda: hecho == [1], que="corra el trabajo diferido")

    assert hecho == [1]
    ventanitas_despues = [
        w for w in root.winfo_children() if isinstance(w, LoadingDialog)
    ]
    assert ventanitas_despues == []  # se cerro sola


def test_con_pantalla_de_carga_llama_a_al_terminar_despues_de_cerrar(root, esperar):
    orden = []

    con_pantalla_de_carga(
        root,
        "Exportando...",
        lambda: orden.append("trabajo"),
        al_terminar=lambda: orden.append(
            "al_terminar"
            + (
                "_sin_ventanita"
                if not [
                    w for w in root.winfo_children() if isinstance(w, LoadingDialog)
                ]
                else "_con_ventanita"
            )
        ),
    )
    esperar(root, lambda: len(orden) == 2, que="corran trabajo y al_terminar")

    # al_terminar corre DESPUES de cerrar la ventanita, no adentro del
    # mismo finally que 'trabajo' - asi no queda un messagebox de
    # resultado superpuesto con la pantalla de carga todavia abierta.
    assert orden == ["trabajo", "al_terminar_sin_ventanita"]


def test_con_pantalla_de_carga_sin_al_terminar_no_falla(root, esperar):
    hecho = []
    con_pantalla_de_carga(root, "Exportando...", lambda: hecho.append(1))

    # no debe lanzar por al_terminar=None
    esperar(root, lambda: hecho == [1], que="corra el trabajo sin al_terminar")


def test_con_pantalla_de_carga_cierra_la_ventanita_aunque_trabajo_falle(root, esperar):
    """
    El finally de con_pantalla_de_carga cierra la ventanita pase lo que
    pase. La excepcion en si no se puede capturar aca con pytest.raises:
    Tkinter atrapa lo que lanza un callback de after() y lo manda a
    report_callback_exception (por default, lo imprime a stderr) en vez
    de dejarlo subir por update() - por eso sale en el stderr de este
    test y no como una excepcion de Python normal.
    """

    def _explota():
        raise ValueError("boom")

    con_pantalla_de_carga(root, "Exportando...", _explota)

    esperar(
        root,
        lambda: not [w for w in root.winfo_children() if isinstance(w, LoadingDialog)],
        que="se cierre la ventanita pese a la excepción",
    )

    assert [w for w in root.winfo_children() if isinstance(w, LoadingDialog)] == []


# ------------------------------------------------------------
# La barra: busqueda arriba, botones abajo (antes no entraban en una
# sola fila y el ultimo boton quedaba cortado contra el borde)
# ------------------------------------------------------------
def _botones_de(tabla):
    encontrados = []

    def recorrer(widget):
        for hijo in widget.winfo_children():
            if isinstance(hijo, kit.Button):
                encontrados.append(hijo)
            recorrer(hijo)

    recorrer(tabla)
    return encontrados


def test_los_botones_entran_en_una_ventana_angosta(root):
    """
    Los 5 de Facturas, que son los que no entraban. Se mide el ancho que
    PIDE la fila (winfo_reqwidth) y no el que tiene: con la ventana
    oculta -como en estos tests- Tk no calcula el ancho real.
    """
    tabla = _tabla(
        root,
        search_label="Buscar (número, cliente o cédula):",
        buttons=(
            ("Nueva factura", lambda: None),
            ("Ver detalle", lambda: None),
            ("Generar PDF", lambda: None),
            ("Cierre de caja", lambda: None),
            ("Cancelar factura", lambda: None),
        ),
    )
    tabla.pack(fill="both", expand=True)
    root.update()

    fila_botones = _botones_de(tabla)[0].master
    # 1000 px: el minimo de la ventana principal (ver MainWindow)
    assert fila_botones.winfo_reqwidth() <= 1000


def test_la_busqueda_y_los_botones_van_en_filas_distintas(root):
    tabla = _tabla(root, buttons=(("Nuevo", lambda: None),))
    tabla.pack(fill="both", expand=True)
    root.update()

    fila_busqueda = tabla._entry.master
    fila_botones = _botones_de(tabla)[0].master
    assert fila_busqueda is not fila_botones


def test_si_falta_alto_cede_la_tabla_y_no_el_contador(root, esperar):
    """
    Con la letra al 150 % la ventana principal no daba el alto que
    pedia la tabla, y "Mostrando N de N" -lo ultimo empacado- quedaba
    afuera en todas las pestanas. Ahora se reserva abajo antes que la
    tabla: lo que se encoge es la tabla, que tiene scroll.
    """
    ventana = tk.Toplevel(root)
    tabla = _tabla(ventana, buttons=(("Nuevo", lambda: None),))
    tabla.pack(fill="both", expand=True)
    ventana.geometry("600x170")
    esperar(ventana, lambda: ventana.winfo_height() == 170, que="se aplique 600x170")

    contador = tabla.status
    fondo = ventana.winfo_rooty() + ventana.winfo_height()
    assert contador.winfo_height() >= contador.winfo_reqheight()
    assert contador.winfo_rooty() + contador.winfo_height() <= fondo
    ventana.destroy()


def test_fixed_height_frame_se_vuelve_a_medir_al_cambiar_la_letra(root):
    """
    El marco fija su alto en pixeles al construirse. Sin el aviso de
    ThemeManager, Ctrl+ con un dialogo abierto dejaba el texto recortado
    adentro de un marco medido con la letra vieja.
    """
    import tkinter.font as tkfont

    from flexstock.ui.theme import ThemeManager
    from flexstock.ui.widgets import fixed_height_frame

    tema = ThemeManager(root, font_scale=100)
    marco = fixed_height_frame(root, lines=2)
    antes = int(marco.cget("height"))

    tema.set_font_scale(150)

    renglon = tkfont.nametofont("TkDefaultFont").metrics("linespace")
    assert int(marco.cget("height")) == renglon * 2 + 4
    assert int(marco.cget("height")) > antes


# ------------------------------------------------------------
# repartir_anchos: columnas de la tabla de lineas que se adaptan
# ------------------------------------------------------------
_IDEAL = {"nombre": 200, "total": 100, "quitar": 40}
_MINIMO = {"nombre": 130, "total": 80, "quitar": 40}


def test_repartir_anchos_sobra_espacio_se_reparte_entre_las_flexibles():
    anchos = repartir_anchos(_IDEAL, _MINIMO, 640, fijas=("quitar",))

    # sobran 300: 2/3 para "nombre" y 1/3 para "total"; "quitar" no cambia
    assert anchos == {"nombre": 400, "total": 200, "quitar": 40}


def test_repartir_anchos_falta_espacio_ceden_en_proporcion_a_lo_que_les_sobra():
    anchos = repartir_anchos(_IDEAL, _MINIMO, 310, fijas=("quitar",))

    # faltan 30; pueden ceder 70 y 20 (total 90): 23 y 6, y el pixel que
    # pierde la division entera se le cobra a la que mas le sobra
    assert anchos == {"nombre": 176, "total": 94, "quitar": 40}
    assert sum(anchos.values()) == 310


def test_repartir_anchos_nunca_baja_del_minimo():
    anchos = repartir_anchos(_IDEAL, _MINIMO, 100, fijas=("quitar",))

    assert anchos == {"nombre": 130, "total": 80, "quitar": 40}  # y habra barra


def test_repartir_anchos_la_columna_fija_no_se_toca():
    for disponible in (100, 340, 900):
        anchos = repartir_anchos(_IDEAL, _MINIMO, disponible, fijas=("quitar",))
        assert anchos["quitar"] == 40


def test_repartir_anchos_la_suma_no_pasa_de_lo_disponible_si_alcanzan_los_minimos():
    for disponible in range(250, 340):
        anchos = repartir_anchos(_IDEAL, _MINIMO, disponible, fijas=("quitar",))
        assert sum(anchos.values()) == disponible


_COLUMNAS = (
    ("sku", "SKU", 90),
    ("nombre", "Nombre", 200),
    ("total", "Subtotal $ BCV", 100),
    ("quitar", "", 44),
)


def test_lines_tree_ninguna_columna_baja_del_ancho_de_su_encabezado(root):
    marco = tk.Toplevel(root)
    tree = lines_tree(marco, _COLUMNAS)
    marco.geometry("500x300")
    marco.update()

    for col, _texto, _ancho in _COLUMNAS:
        assert tree.column(col, "width") >= tree.column(col, "minwidth")
    # "Subtotal $ BCV" en negrita necesita bastante mas que los 100 pedidos
    assert tree.column("total", "minwidth") > 100
    marco.destroy()


def test_lines_tree_con_ancho_de_sobra_no_deja_columnas_fuera_de_vista(root):
    marco = tk.Toplevel(root)
    tree = lines_tree(marco, _COLUMNAS)
    marco.geometry("1200x300")
    marco.update()
    marco.update()

    total = sum(tree.column(c, "width") for c, _t, _a in _COLUMNAS)
    assert total <= tree.winfo_width()
    marco.destroy()


def test_fill_product_listbox_pone_el_stock_adelante(root):
    lista = product_listbox(root)

    fill_product_listbox(lista, [("1", 'GEN-0731 - ESMERIL 4-1/2"C/5 DISCOS', True)])

    renglon = lista.get(0)
    assert renglon.strip().startswith("1")
    assert renglon.strip().endswith('ESMERIL 4-1/2"C/5 DISCOS')


def test_fill_product_listbox_colorea_segun_haya_stock(root):
    from flexstock.ui.theme import LIGHT

    lista = product_listbox(root)

    fill_product_listbox(
        lista,
        [("0", "GEN-0001 - SIN EXISTENCIA", False), ("3", "GEN-0002 - HAY", True)],
    )

    assert lista.itemcget(0, "foreground") == LIGHT.muted_fg
    assert lista.itemcget(1, "foreground") == LIGHT.success_bg


def test_fill_product_listbox_reemplaza_lo_que_habia(root):
    lista = product_listbox(root)
    fill_product_listbox(lista, [("1", "VIEJO", True)])

    fill_product_listbox(lista, [("2", "NUEVO", True)])

    assert lista.size() == 1
    assert "NUEVO" in lista.get(0)


def test_product_listbox_es_ancho_y_tiene_las_dos_barras(root):
    lista = product_listbox(root)

    assert int(lista.cget("width")) >= 56
    assert lista.cget("xscrollcommand") and lista.cget("yscrollcommand")


# ------------------------------------------------------------
# Tooltip
# ------------------------------------------------------------
def test_tooltip_muestra_el_texto_y_se_va(root):
    from flexstock.ui.widgets import Tooltip

    boton = kit.Button(root, text="x")
    tooltip = Tooltip(boton, lambda: "Productos")

    tooltip.mostrar()
    assert tooltip._ventana is not None
    etiqueta = tooltip._ventana.winfo_children()[0]
    assert etiqueta.cget("text") == "Productos"

    tooltip.ocultar()
    assert tooltip._ventana is None


def test_tooltip_sin_texto_no_aparece(root):
    from flexstock.ui.widgets import Tooltip

    tooltip = Tooltip(kit.Button(root, text="x"), lambda: None)

    tooltip.mostrar()

    assert tooltip._ventana is None


# ------------------------------------------------------------
# barra de busqueda + botones: una fila si hay ancho, dos si no
# ------------------------------------------------------------
def _tabla_con_botones(root):
    tabla = SearchableTable(
        root,
        columns=("nombre", "total"),
        headings={"nombre": "Nombre", "total": "Total"},
        buttons=(("Nuevo", lambda: None), ("Editar", lambda: None)),
        right_buttons=(("Crear factura", lambda: None),),
    )
    tabla.pack(fill="both", expand=True)
    return tabla


def test_la_barra_va_en_una_fila_si_hay_ancho(root, mostrar_con_tamano):
    ventana = tk.Toplevel(root)
    tabla = _tabla_con_botones(ventana)
    mostrar_con_tamano(root, ventana, "1400x600")

    filas = {int(h.grid_info()["row"]) for h in tabla._barra.grid_slaves()}
    assert filas == {0}  # busqueda y botones, todos en la misma fila
    ventana.destroy()


def test_la_barra_se_parte_en_dos_filas_si_no_entra(root, mostrar_con_tamano):
    ventana = tk.Toplevel(root)
    tabla = _tabla_con_botones(ventana)
    mostrar_con_tamano(root, ventana, "420x600")

    filas = {int(h.grid_info()["row"]) for h in tabla._barra.grid_slaves()}
    assert filas == {0, 1}  # busqueda arriba, botones abajo: ninguno se corta
    for boton in (
        *tabla._botones_izq.winfo_children(),
        *tabla._botones_der.winfo_children(),
    ):
        assert boton.winfo_rootx() + boton.winfo_width() <= (
            ventana.winfo_rootx() + ventana.winfo_width()
        )
    ventana.destroy()


# ------------------------------------------------------------
# fila bajo el cursor
# ------------------------------------------------------------
def test_la_fila_bajo_el_cursor_se_marca_y_se_desmarca(root, mostrar_con_tamano):
    ventana = tk.Toplevel(root)
    tabla = _tabla(ventana)
    tabla.pack(fill="both", expand=True)
    tabla.set_rows([("1", ("A", "1")), ("2", ("B", "2")), ("3", ("C", "3"))])
    mostrar_con_tamano(root, ventana, "800x500")

    x, y, _w, h = tabla.tree.bbox("2")
    tabla.tree.event_generate("<Motion>", x=x + 5, y=y + h // 2)
    ventana.update()
    assert "hover_row" in tabla.tree.item("2", "tags")

    x, y, _w, h = tabla.tree.bbox("3")
    tabla.tree.event_generate("<Motion>", x=x + 5, y=y + h // 2)
    ventana.update()
    assert "hover_row" not in tabla.tree.item("2", "tags")
    assert "hover_row" in tabla.tree.item("3", "tags")
    ventana.destroy()


def test_la_fila_seleccionada_no_se_marca_como_hover(root, mostrar_con_tamano):
    ventana = tk.Toplevel(root)
    tabla = _tabla(ventana)
    tabla.pack(fill="both", expand=True)
    tabla.set_rows([("1", ("A", "1")), ("2", ("B", "2"))])
    mostrar_con_tamano(root, ventana, "800x500")

    tabla.tree.selection_set("2")
    ventana.update()
    x, y, _w, h = tabla.tree.bbox("2")
    tabla.tree.event_generate("<Motion>", x=x + 5, y=y + h // 2)
    ventana.update()
    assert "hover_row" not in tabla.tree.item("2", "tags")  # se vería menos
    ventana.destroy()


# ------------------------------------------------------------
# modo oscuro: el cuerpo desplazable no se queda con el fondo claro
# ------------------------------------------------------------
def test_el_cuerpo_desplazable_pasa_a_oscuro_sin_quedarse_en_claro(root):
    """
    Un marco transparente dentro de un tk.Canvas hacia que sus hijos
    copiaran su color en ese momento ("#f1f4f9" fijo) y no cambiaran al
    pasar a oscuro: los paneles de Configuracion quedaban claros.
    """
    from flexstock.ui.theme import DARK, LIGHT, ThemeManager
    from flexstock.ui.widgets import ScrollableDialogBody

    tema = ThemeManager(root, font_scale=100)
    tema.apply("light")
    cuerpo = ScrollableDialogBody(root)
    cuerpo.pack(fill="both", expand=True)
    etiqueta = kit.Label(cuerpo.content, text="Datos de la tienda")
    etiqueta.pack()

    # El fondo que hereda el hijo es el PAR (claro, oscuro), no un solo color
    assert tuple(etiqueta.cget("bg_color")) == (LIGHT.bg, DARK.bg)
    tema.apply("dark")
    root.update()
    assert etiqueta._apply_appearance_mode(etiqueta.cget("bg_color")) == DARK.bg
    tema.apply("light")


# ------------------------------------------------------------
# letra al 150 %: columnas completas y a la vista
# ------------------------------------------------------------
def test_a_150_las_columnas_entran_y_no_cortan_su_titulo(root, mostrar_con_tamano):
    from flexstock.ui.theme import ThemeManager

    ThemeManager(root, font_scale=150)
    ventana = tk.Toplevel(root)
    tabla = SearchableTable(
        ventana,
        columns=("sku", "nombre", "precio", "stock"),
        headings={
            "sku": "SKU",
            "nombre": "Nombre",
            "precio": "Precio Detal $ efectivo",
            "stock": "Stock",
        },
        column_widths={"sku": 110, "nombre": 320, "precio": 190, "stock": 130},
    )
    tabla.pack(fill="both", expand=True)
    tabla.set_rows([("1", ("GEN-0001", "MARTILLO DE UÑA 16 OZ", "2,00$", "5"))])
    mostrar_con_tamano(root, ventana, "1100x500")
    root.update()

    anchos = {c: int(tabla.tree.column(c, "width")) for c in tabla.tree["columns"]}
    assert sum(anchos.values()) <= tabla.tree.winfo_width()  # ninguna sale de la vista
    for col in tabla.tree["columns"]:
        assert anchos[col] >= tabla._ancho_titulo(col)  # titulo entero
    ventana.destroy()


def test_ctrl_menos_con_la_tabla_enfocada_no_escribe_en_la_busqueda(
    root, mostrar_con_tamano
):
    """
    Ctrl+- (achicar la letra) con el foco en la tabla se tipeaba como un
    "-" en el buscador y le sacaba el foco a la tabla: el atajo global no
    corria y habia que apretarlo dos veces.
    """
    ventana = tk.Toplevel(root)
    tabla = _tabla(ventana)
    tabla.pack(fill="both", expand=True)
    tabla.set_rows([("1", ("A", "1"))])
    mostrar_con_tamano(root, ventana, "800x500")
    tabla.tree.focus_force()
    ventana.update()

    tabla.tree.event_generate("<Control-Key-minus>")
    ventana.update()

    assert tabla.search_var.get() == ""  # no se escribio nada
    assert ventana.focus_get() is tabla.tree  # y el foco sigue en la tabla
    # escribir a secas si sigue yendo al buscador
    tabla.tree.event_generate("<Key-a>")
    ventana.update()
    assert tabla.search_var.get() == "a"
    ventana.destroy()


def test_una_tabla_en_una_ventana_que_mide_su_contenido_no_crece_sin_fin(
    root, monkeypatch
):
    """
    La tabla pide ~2 px mas que la suma de sus columnas (el borde). Si el
    ajuste de columnas repartia ese espacio entero, una ventana que mide lo
    que pide su contenido crecia 2 px por pasada, sin fin (y X11 mataba el
    proceso). El freno corta el bucle si volviera a pasar, para que el test
    falle en vez de colgarse.
    """
    llamadas = []
    original = SearchableTable._ajustar_columnas

    def contando(self, evento=None):
        llamadas.append(1)
        if len(llamadas) > 60:
            raise tk.TclError("se esta reajustando sin parar")
        return original(self, evento)

    monkeypatch.setattr(SearchableTable, "_ajustar_columnas", contando)
    ventana = tk.Toplevel(root)
    tabla = _tabla(ventana)
    tabla.pack(fill="both", expand=True)
    for _ in range(5):
        ventana.update()

    assert 1 <= len(llamadas) <= 60
    ancho = ventana.winfo_reqwidth()
    ventana.update()
    assert ventana.winfo_reqwidth() == ancho  # ya no cambia
    ventana.destroy()


def test_info_hint_es_un_icono_con_cartelito(root):
    from flexstock.ui.widgets import info_hint

    icono = info_hint(root, "Doble clic abre la ficha.")
    icono.pack()
    root.update_idletasks()

    assert isinstance(icono, kit.Label)
    assert icono.winfo_exists()


def _tabla_resaltada(root, mostrar_con_tamano, esperar):
    ventana = tk.Toplevel(root)
    tabla = _tabla(ventana)
    tabla.pack(fill="both", expand=True)
    tabla.set_rows([("1", ("Clavo", "1,00"))])
    tabla.set_highlighted_column("total")
    mostrar_con_tamano(root, ventana, "800x400")
    esperar(root, lambda: tabla._resaltado is not None, que="se dibuje el titulo")
    return ventana, tabla


def test_titulo_resaltado_queda_sobre_su_columna(root, mostrar_con_tamano, esperar):
    ventana, tabla = _tabla_resaltada(root, mostrar_con_tamano, esperar)
    resaltado = tabla._resaltado
    x_total = int(tabla.tree.column("nombre", "width"))

    assert str(resaltado.cget("text")) == "Total"
    assert int(resaltado.place_info()["x"]) == x_total
    assert int(resaltado.place_info()["width"]) == (
        int(tabla.tree.column("total", "width")) - 1
    )
    ventana.destroy()


def test_quitar_el_resaltado_esconde_el_titulo(root, mostrar_con_tamano, esperar):
    ventana, tabla = _tabla_resaltada(root, mostrar_con_tamano, esperar)

    tabla.set_highlighted_column(None)

    assert tabla._resaltado.winfo_manager() == ""
    ventana.destroy()
