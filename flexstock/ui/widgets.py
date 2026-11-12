"""
Widgets Tkinter reusables entre paginas de listado (productos, clientes,
proveedores, facturas).

SearchableTable es solo el cascaron visual: campo de busqueda + Treeview +
scrollbar + label de estado. NO sabe filtrar -cada entidad busca por
campos distintos (productos: nombre/SKU: clientes: nombre/cedula)- ni
sabe de que servicio viene la data. Quien la use conecta 'search_var' con
su propio filtro en memoria y le pasa filas ya armadas via set_rows().

Agrega dos cosas de ergonomia que antes no tenia ninguna de las 4 tablas
que la usan: ordenar con click en el encabezado, y doble-click en una
fila para disparar la accion por default de esa pagina (editar, ver
detalle, etc. - lo decide quien la usa, via on_double_click).
"""

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable, Sequence
from tkinter import ttk

from . import icons, kit
from .theme import find_app_root, get_palette, paint_canvas, paint_listbox, pair


def focus_first_entry(widget: tk.Misc) -> bool:
    """
    Pone el cursor en el primer Entry/Combobox del formulario (DFS en
    orden de creacion, ver winfo_children()) para no obligar a un click
    extra antes de poder escribir - pensado para gente mayor, en
    dialogos con muchos campos donde ese primer click no es obvio.
    ttk.Combobox hereda de ttk.Entry, asi que tambien cuenta como
    "primer campo" cuando un formulario arranca con uno (ej. el combo
    de cliente en InvoiceDialog).

    Devuelve True apenas encuentra y enfoca uno, para cortar la
    busqueda ahi: sin el corte, seguiria recorriendo hermanos/hijos
    restantes y el foco terminaria en el ULTIMO Entry del arbol, no el
    primero.
    """
    for hijo in widget.winfo_children():
        if isinstance(hijo, (kit.Entry, kit.Combobox, ttk.Entry)):
            hijo.focus_set()
            # Los dialogos nacen escondidos (ver kit.Dialog) y el foco
            # pedido mientras estan asi se pierde al mostrarse: se anota
            # aca para que Dialog._mostrar lo vuelva a poner.
            hijo.winfo_toplevel()._foco_inicial = hijo
            return True
        if focus_first_entry(hijo):
            return True
    return False


def product_listbox(
    parent: tk.Misc, *, height: int = 10, width: int = 56, plano: bool = False
) -> tk.Listbox:
    """
    Lista de productos de los formularios de alta (Nueva factura y
    Nueva cotizacion), con barra vertical y horizontal.

    Ancha a proposito: cada renglon es "SKU - nombre (stock: N)" y los
    nombres del catalogo viejo llegan a 40 caracteres, asi que el
    renglon completo pasa de 60. 'width' esta en caracteres; la lista
    se encoge sola si la ventana es mas chica (las barras se empacan
    ANTES que ella, asi que nunca desaparecen) y la barra horizontal es
    la red de seguridad para los pocos nombres que igual no entran.
    """
    marco = (
        kit.Frame(parent)
        if plano
        else kit.Frame(parent, style="Table.TFrame", padding=6)
    )
    marco.pack(side="top", fill="both", expand=True)
    lista = tk.Listbox(
        marco,
        height=height,
        width=width,
        exportselection=False,
        borderwidth=0,
        highlightthickness=0,
        activestyle="none",
    )
    paint_listbox(lista)
    vertical = kit.Scrollbar(marco, orient="vertical", command=lista.yview)
    horizontal = kit.Scrollbar(marco, orient="horizontal", command=lista.xview)
    lista.configure(yscrollcommand=vertical.set, xscrollcommand=horizontal.set)
    horizontal.pack(side="bottom", fill="x")
    vertical.pack(side="right", fill="y")
    lista.pack(side="left", fill="both", expand=True)
    return lista


def product_list_min_height(lines: int = 4) -> int:
    """Alto para 'lines' productos completos y su barra horizontal.

    La fuente cambia con la preferencia de tamano, por eso este minimo
    se mide al crear el formulario en vez de usar un numero fijo que
    serviria solo a 100 %.
    """
    fuente = tkfont.nametofont("TkDefaultFont")
    return fuente.metrics("linespace") * lines + 28


def fill_product_listbox(
    lista: tk.Listbox,
    filas: "Sequence[tuple[str, str, bool]]",
    *,
    coincidences_at: int | None = None,
) -> None:
    """
    Llena la lista de productos de Nueva factura / Nueva cotizacion.
    'filas' es (stock, descripcion, hay_stock) por producto.

    El stock va ADELANTE, en un campo de ancho fijo: al final del
    renglon quedaba fuera de vista apenas el nombre era largo (el
    catalogo viejo llega a 40 caracteres). Ademas va en color, que es lo
    unico que un tk.Listbox permite por renglon (no hay negrita por
    item): verde el que tiene existencia, gris el que esta en cero, para
    que el que SI hay salte a la vista en una lista donde casi todo esta
    en cero. Mismo criterio que el naranja de "bajo stock" en la tabla
    de Productos.
    """
    paleta = get_palette(lista)
    lista.delete(0, "end")
    for indice, (stock, descripcion, hay_stock) in enumerate(filas):
        if coincidences_at == indice:
            lista.insert("end", "─ COINCIDENCIAS ─")
            lista.itemconfig(
                "end",
                foreground=paleta.muted_fg,
                selectforeground=paleta.muted_fg,
            )
        lista.insert("end", f"{stock:>5}   {descripcion}")
        lista.itemconfig(
            "end",
            foreground=paleta.success_bg if hay_stock else paleta.muted_fg,
            selectforeground=paleta.select_fg,
        )


def product_at_listbox_index(
    products: Sequence, index: int, coincidences_at: int | None
):
    """Traduce un indice visual; el separador no representa un producto."""
    if coincidences_at is not None:
        if index == coincidences_at:
            return None
        if index > coincidences_at:
            index -= 1
    return products[index] if 0 <= index < len(products) else None


def lines_tree(
    parent: tk.Misc,
    columnas: tuple[tuple[str, str, int], ...],
    *,
    height: int = 6,
    plano: bool = False,
) -> ttk.Treeview:
    """
    Tabla de lineas de Nueva factura / Nueva cotizacion. 'columnas' es
    (clave, encabezado, ancho): "sku" y "nombre" alineadas a la
    izquierda, "quitar" (la x) centrada y fija, el resto a la derecha.

    Se adapta al ancho que le toque en vez de recortarse. El Treeview de
    Tk solo AGRANDA columnas cuando el widget crece; cuando se achica no
    las encoge y la ultima (la x de quitar) queda fuera de vista. Por eso
    el reparto lo hace repartir_anchos() en cada cambio de tamano:
    - ninguna columna baja del ancho de su propio encabezado (medido con
      la fuente real), asi ningun titulo aparece cortado;
    - todas menos "quitar" se encogen o estiran juntas;
    - si ni con los minimos entran (ventana muy angosta) aparece una
      barra horizontal, y se oculta cuando no hace falta.
    """
    # plano: sin su propio recuadro redondeado, para cuando ya esta dentro de
    # una tarjeta (una caja dentro de otra caja cansa la vista).
    marco = (
        kit.Frame(parent)
        if plano
        else kit.Frame(parent, style="Table.TFrame", padding=6)
    )
    marco.pack(fill="both", expand=True)
    tree = ttk.Treeview(
        marco, columns=[c[0] for c in columnas], show="headings", height=height
    )
    vertical = kit.Scrollbar(marco, orient="vertical", command=tree.yview)
    horizontal = kit.Scrollbar(marco, orient="horizontal", command=tree.xview)

    def _barra_horizontal(primero: str, ultimo: str) -> None:
        if float(primero) <= 0.0 and float(ultimo) >= 1.0:
            horizontal.grid_remove()
        else:
            horizontal.grid()
        horizontal.set(primero, ultimo)

    tree.configure(yscrollcommand=vertical.set, xscrollcommand=_barra_horizontal)
    tree.grid(row=0, column=0, sticky="nsew")
    vertical.grid(row=0, column=1, sticky="ns")
    horizontal.grid(row=1, column=0, sticky="ew")
    marco.rowconfigure(0, weight=1)
    marco.columnconfigure(0, weight=1)

    fuente = tkfont.nametofont("TkHeadingFont")
    ideal: dict[str, int] = {}
    minimo: dict[str, int] = {}
    for col, texto, ancho in columnas:
        # +16: relleno de la celda del encabezado.
        piso = fuente.measure(texto) + 16 if texto else ancho
        minimo[col] = max(piso, _MINIMO_NOMBRE) if col == "nombre" else piso
        ideal[col] = max(ancho, minimo[col])
        tree.heading(col, text=texto)
        tree.column(
            col,
            width=ideal[col],
            minwidth=minimo[col],
            anchor="w" if col in ("sku", "nombre") else "e",
            stretch=False,  # el reparto lo hace repartir_anchos()
        )
    if "quitar" in tree["columns"]:
        tree.column("quitar", anchor="center")
    alinear_encabezados(tree)

    # El ancho que PIDE el marco no depende de las columnas (que cambian
    # con cada reparto): si dependiera, cada reparto cambiaria lo pedido
    # y el reparto siguiente cambiaria el tamano, en bucle.
    fila = int(ttk.Style().lookup("Treeview", "rowheight") or 24)
    marco.grid_propagate(False)
    marco.configure(width=sum(ideal.values()) + 24, height=(height + 1) * fila + 24)

    def _repartir(evento: tk.Event) -> None:
        anchos = repartir_anchos(ideal, minimo, evento.width - 4, fijas=("quitar",))
        for col, ancho in anchos.items():
            tree.column(col, width=ancho)

    tree.bind("<Configure>", _repartir)
    return tree


_MINIMO_NOMBRE = 130  # un nombre mas angosto ya no dice nada

# Tag de las filas pares de una tabla: un gris apenas distinto del fondo
# para seguir una fila larga con la vista. ThemeManager lo repinta al
# cambiar de modo (ver theme._pintar_widget_clasico).
STRIPE_TAG = "franja"
HOVER_TAG = "hover_row"


def repartir_anchos(
    ideal: dict[str, int],
    minimo: dict[str, int],
    disponible: int,
    *,
    fijas: tuple[str, ...] = (),
) -> dict[str, int]:
    """
    Ancho de cada columna para 'disponible' pixeles.

    - Sobra espacio: se reparte entre las columnas no fijas en
      proporcion a su ancho ideal.
    - Falta espacio: cada columna no fija cede en proporcion a cuanto
      le sobra sobre su minimo, sin bajar nunca de el.
    - Ni con todos los minimos alcanza: quedan en su minimo (la tabla
      muestra entonces una barra horizontal).
    Las 'fijas' (la x de quitar) mantienen su ancho ideal siempre.
    """
    flexibles = [c for c in ideal if c not in fijas]
    total_ideal = sum(ideal.values())
    if disponible >= total_ideal:
        extra = disponible - total_ideal
        base = sum(ideal[c] for c in flexibles) or 1
        return {
            c: ideal[c] + (0 if c in fijas else extra * ideal[c] // base) for c in ideal
        }

    deficit = total_ideal - disponible
    cedible = {c: ideal[c] - minimo[c] for c in flexibles}
    total_cedible = sum(cedible.values())
    if deficit >= total_cedible:
        return {c: ideal[c] if c in fijas else minimo[c] for c in ideal}

    # La division entera pierde algun pixel: se le cobra, de a uno, a las
    # columnas a las que todavia les sobra, para que la suma nunca pase
    # de 'disponible' (un pixel de mas ya esconde el borde de la ultima).
    reduccion = {c: deficit * cedible[c] // total_cedible for c in flexibles}
    resto = deficit - sum(reduccion.values())
    for c in sorted(flexibles, key=lambda c: cedible[c] - reduccion[c], reverse=True):
        if resto == 0:
            break
        if cedible[c] - reduccion[c] > 0:
            reduccion[c] += 1
            resto -= 1
    return {c: ideal[c] - reduccion.get(c, 0) for c in ideal}


def size_dialog_to_screen(dialog: tk.Toplevel) -> None:
    """
    Tamano inicial de un formulario grande: casi toda la pantalla, pero
    nunca menos de lo que pide su contenido ni mas de lo que entra
    (se deja margen para la barra del escritorio y el marco de la
    ventana). El minimo es chico a proposito: si el escritorio no da
    tanto espacio, la parte del medio se encoge y lo de abajo (totales y
    botones) sigue completo.

    "nunca mas de lo que entra" tambien se le pide al gestor de
    ventanas con maxsize(), no solo al calcular el tamano inicial: sin
    esto, arrastrar el borde o maximizar (el "se van para afuera del
    margen y se esconden" que vimos en una laptop) puede dejar la
    ventana mas ancha o alta que la pantalla, con media factura
    escondida del otro lado. Mismo limite que 'ancho'/'alto' de arriba,
    asi el techo no contradice lo que ya se calculo.

    Llamar DESPUES de construir todos los widgets.
    """
    dialog.update_idletasks()
    ancho_pantalla = dialog.winfo_screenwidth()
    alto_pantalla = dialog.winfo_screenheight()
    ancho_maximo = ancho_pantalla - 24
    alto_maximo = alto_pantalla - 90
    ancho = min(max(dialog.winfo_reqwidth(), int(ancho_pantalla * 0.9)), ancho_maximo)
    alto = min(max(dialog.winfo_reqheight(), int(alto_pantalla * 0.88)), alto_maximo)
    # tk.Wm directo y no dialog.geometry(): CTkToplevel multiplica lo que
    # recibe por su escalado de pantalla, y estos ya son pixeles reales
    # medidos con winfo_screen*() (en Windows a 150 % quedaba enorme).
    tk.Wm.wm_geometry(dialog, f"{ancho}x{alto}+{(ancho_pantalla - ancho) // 2}+0")
    dialog._posicion_inicial = ((ancho_pantalla - ancho) // 2, 0)
    tk.Wm.wm_minsize(
        dialog,
        min(dialog.winfo_reqwidth(), 1000, ancho),
        min(dialog.winfo_reqheight(), 600, alto),
    )
    tk.Wm.wm_maxsize(dialog, ancho_maximo, alto_maximo)


class ScrollableDialogBody(kit.Frame):
    """Cuerpo desplazable para formularios altos, con acciones afuera.

    Los botones de Guardar/Cancelar van como hermano de este widget, no
    dentro: aunque el texto al 150 % no entre entero, la accion principal
    nunca queda fuera de la pantalla. El scroll aparece solo cuando hace
    falta; en zoom normal el formulario se ve igual que un ttk.Frame.
    """

    def __init__(self, master: tk.Misc, *, padding: int | tuple[int, ...] = 0) -> None:
        super().__init__(master)
        # _lienzo y no _canvas: CTkFrame ya usa self._canvas para su fondo.
        self._lienzo = tk.Canvas(self, borderwidth=0)
        paint_canvas(self._lienzo)
        self._scrollbar = kit.Scrollbar(
            self, orient="vertical", command=self._lienzo.yview
        )
        # bg_color explicito (claro, oscuro): CTk sigue solo el color de un
        # padre Frame/Toplevel, no el de un tk.Canvas, y al pasar a modo
        # oscuro el contenido se quedaba con el fondo claro.
        self.content = kit.Frame(self._lienzo, padding=padding, bg_color=pair("bg"))
        # Y fg_color como PAR, no transparente: un CTkFrame transparente cuyo
        # padre es un tk.Canvas hace que sus hijos copien el color del
        # canvas en ese instante (un "#f1f4f9" fijo) y nunca lo cambien al
        # pasar a oscuro; con un par (claro, oscuro) lo heredan como par.
        self.content.configure(fg_color=pair("bg"))
        self._content_window = self._lienzo.create_window(
            (0, 0), anchor="nw", window=self.content
        )
        self._wheel_bound_widgets: set[tk.Misc] = set()
        self._lienzo.configure(yscrollcommand=self._on_scroll)
        self._lienzo.grid(row=0, column=0, sticky="nsew")
        self._scrollbar.grid(row=0, column=1, sticky="ns")
        self._scrollbar.grid_remove()
        self.rowconfigure(0, weight=1)
        self.columnconfigure(0, weight=1)
        self.content.bind("<Configure>", self._on_content_configure)
        self._lienzo.bind("<Configure>", self._on_canvas_configure)
        self._bind_mousewheel(self._lienzo)
        self._bind_mousewheel(self.content)

    def _on_content_configure(self, _event: tk.Event) -> None:
        self._lienzo.configure(scrollregion=self._lienzo.bbox("all"))
        self._bind_mousewheel_children(self.content)

    def _on_canvas_configure(self, event: tk.Event) -> None:
        self._lienzo.itemconfigure(self._content_window, width=event.width)

    def _on_scroll(self, first: str, last: str) -> None:
        if float(first) <= 0.0 and float(last) >= 1.0:
            self._scrollbar.grid_remove()
        else:
            self._scrollbar.grid()
        self._scrollbar.set(first, last)

    def _bind_mousewheel_children(self, parent: tk.Misc) -> None:
        """Activa la rueda en etiquetas, marcos y botones del formulario.

        Las listas, arboles, cuadros de texto y combos conservan su propio
        desplazamiento: si el puntero esta sobre uno de esos controles, no
        se debe mover todo el dialogo en su lugar.
        """
        for child in parent.winfo_children():
            if not isinstance(
                child, (tk.Listbox, tk.Text, kit.Combobox, ttk.Combobox, ttk.Treeview)
            ):
                self._bind_mousewheel(child)
            self._bind_mousewheel_children(child)

    def _bind_mousewheel(self, widget: tk.Misc) -> None:
        if widget in self._wheel_bound_widgets:
            return
        self._wheel_bound_widgets.add(widget)
        widget.bind("<MouseWheel>", self._on_mousewheel, add="+")
        # Linux/X11 entrega la rueda como botones 4 y 5, no como delta.
        widget.bind("<Button-4>", lambda _event: self._scroll_by(-1), add="+")
        widget.bind("<Button-5>", lambda _event: self._scroll_by(1), add="+")

    def _on_mousewheel(self, event: tk.Event) -> str:
        return self._scroll_by(-1 if event.delta > 0 else 1)

    def _scroll_by(self, amount: int) -> str:
        self._lienzo.yview_scroll(amount, "units")
        return "break"


def fit_scrollable_dialog_to_screen(
    dialog: tk.Toplevel,
    master: tk.Misc,
    body: ScrollableDialogBody,
    *fixed_widgets: tk.Misc,
    minimum_width: int = 0,
) -> None:
    """Ajusta un dialogo con cuerpo desplazable al escritorio disponible.

    ``fixed_widgets`` son pies de accion (Guardar/Cancelar, por ejemplo).
    Se descuentan antes de asignar espacio al cuerpo, para que nunca sean
    desplazados fuera de la pantalla al aumentar el tamano del texto.
    """
    dialog.update_idletasks()
    screen_width = dialog.winfo_screenwidth()
    screen_height = dialog.winfo_screenheight()
    requested_width = max(
        dialog.winfo_reqwidth(), body.content.winfo_reqwidth() + 24, minimum_width
    )
    requested_height = (
        body.content.winfo_reqheight()
        + sum(widget.winfo_reqheight() for widget in fixed_widgets)
        + 32
    )  # pady de los pies fijos y margen antes del borde de la ventana
    width = min(requested_width, screen_width - 24)
    height = min(requested_height, screen_height - 90)
    x = master.winfo_rootx() + (master.winfo_width() - width) // 2
    # Un poco ARRIBA del centro exacto (a un tercio): es donde el ojo espera
    # una ventana, y no tapa el medio de la pantalla de donde se vino.
    y = master.winfo_rooty() + (master.winfo_height() - height) // 3
    x = max(0, min(x, screen_width - width))
    y = max(0, min(y, screen_height - height - 60))
    tk.Wm.wm_geometry(dialog, f"{width}x{height}+{x}+{y}")
    dialog._posicion_inicial = (x, y)
    tk.Wm.wm_minsize(dialog, min(width, 900), min(height, 420))


def wrap_to_width(label: kit.Label, *, max_width: int | None = None) -> None:
    """
    Hace que el texto de 'label' se parta en renglones segun el ancho
    que tenga en cada momento, en vez de un wraplength fijo que se
    queda corto o largo cuando cambia el tamano de la ventana. Con
    ``max_width`` se puede impedir que un resumen largo ensanche todo
    el dialogo. Empacar el label con fill="x".
    """

    def _ajustar(evento: tk.Event) -> None:
        ancho = max(evento.width, 120)
        label.configure(wraplength=min(ancho, max_width) if max_width else ancho)

    label.bind("<Configure>", _ajustar)


def fixed_height_frame(parent: tk.Misc, *, lines: int, padding: int = 4) -> kit.Frame:
    """
    Frame de alto fijo (en renglones de la fuente por defecto) que no
    crece ni se achica pase lo que pase adentro.

    Pensado para contener un Label con wrap_to_width() cuyo texto cambia
    de largo en vivo (el resumen de un cliente elegido, el precio guia de
    un producto): ttk.Label no tiene opcion 'height' -a diferencia del
    tk.Label clasico-, asi que sin este marco, un texto que pasa a mas
    renglones le pide mas alto a toda la fila y, sin nada que frene al
    Toplevel que lo contiene, termina estirando la VENTANA entera -justo
    al elegir un cliente o un producto, que es cuando mas hace falta que
    el formulario se quede quieto. Un texto que no entre en 'lines'
    renglones se corta: preferible a que la ventana cambie de tamano
    sola.

    Agregar el Label adentro con pack(fill="both", expand=True). El alto
    se vuelve a medir cuando cambia el tamano de letra (ThemeManager
    llama a on_font_scale_changed): Ctrl+ con el dialogo abierto no
    deja el texto recortado en un marco medido con la letra vieja.
    """

    def _alto() -> int:
        return tkfont.nametofont("TkDefaultFont").metrics("linespace") * lines + padding

    marco = kit.Frame(parent, height=_alto())
    marco.pack_propagate(False)
    marco.on_font_scale_changed = lambda: marco.configure(height=_alto())
    return marco


class RateCard(kit.Frame):
    """
    Una tasa (o el IVA) en un bloque compacto de tres renglones: nombre
    chico, valor grande y fecha chica. Un click en cualquier parte de la
    tarjeta llama a on_click (editarla) - reemplaza a los botones
    "Editar X" que ocupaban una fila entera del encabezado. Al pasar el
    mouse se le marca el borde, para que se note que se puede tocar.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        value_var: tk.StringVar,
        date_var: tk.StringVar | None = None,
        on_click: object | None = None,
        highlighted: bool = False,
    ) -> None:
        # highlighted: variante "CardAccent*" de theme.py, para la tasa
        # propia (Modo avanzado) al lado de las oficiales.
        base = "CardAccent" if highlighted else "Card"
        super().__init__(master, style=f"{base}.TFrame", padding=(14, 6))
        self._on_click = on_click
        self.title_label = kit.Label(
            self, text=title.upper(), style=f"{base}Title.TLabel"
        )
        self.title_label.pack(anchor="w")
        self.value_label = kit.Label(
            self, textvariable=value_var, style=f"{base}Value.TLabel"
        )
        self.value_label.pack(anchor="w")
        self.date_label = kit.Label(
            self,
            textvariable=date_var or tk.StringVar(value=""),
            style=f"{base}Date.TLabel",
        )
        self.date_label.pack(anchor="w")

        if on_click is not None:
            normal = f"{base}.TFrame"
            hover = f"{base}Hover.TFrame"
            for widget in (self, self.title_label, self.value_label, self.date_label):
                widget.configure(cursor="hand2")
                widget.bind("<Button-1>", self._click)
                widget.bind("<Enter>", lambda _e: self.configure(style=hover))
                widget.bind("<Leave>", lambda _e: self.configure(style=normal))

    def _click(self, _evento: object = None) -> None:
        if self._on_click is not None:
            self._on_click()


class LoadingDialog(kit.Dialog):
    """
    Aviso de "trabajando..." para una operacion que bloquea el hilo
    principal un rato (ej. armar el PDF de un catalogo de 1.936 filas
    con ReportLab). Sin boton ni X para cerrarla a mano: se cierra sola
    cuando el trabajo termina (ver con_pantalla_de_carga).

    Por que no un hilo aparte: SQLite en este proyecto exige que una
    conexion se use SOLO desde el hilo que la abrio (ver la nota de
    diseno en exchange_rate_service.py) - los unicos hilos que ya usa
    la app son para las tasas de cambio, que son puro pedido de red y
    nunca tocan la conexion. ExportService si lee la base, asi que
    correrlo en otro hilo romperia esa regla.
    """

    def __init__(self, master: tk.Misc, text: str = "Trabajando...") -> None:
        super().__init__(master)
        self.title("")
        self.transient(master)
        self.resizable(False, False)
        self.protocol("WM_DELETE_WINDOW", lambda: None)  # nada de cerrarla a mano

        frame = kit.Frame(self, padding=28)
        frame.pack(fill="both", expand=True)
        kit.Label(frame, text=text, style="Heading.TLabel").pack(pady=(0, 14))
        self._barra = kit.Progressbar(frame, mode="indeterminate", length=240)
        self._barra.pack()
        self._barra.start(12)

        self.update_idletasks()
        # reqwidth/reqheight: el dialogo todavia esta escondido (ver Dialog)
        # y winfo_width() de una ventana sin mostrar vale 1.
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 2
        tk.Wm.wm_geometry(self, f"+{max(x, 0)}+{max(y, 0)}")
        self._posicion_inicial = (max(x, 0), max(y, 0))
        self.lift()
        self.grab_set()

    def cerrar(self) -> None:
        self._barra.stop()
        self.destroy()


def con_pantalla_de_carga(
    master: tk.Misc,
    texto: str,
    trabajo: Callable[[], None],
    *,
    al_terminar: Callable[[], None] | None = None,
) -> None:
    """
    Muestra LoadingDialog y, en el PROXIMO ciclo del mainloop (via
    after(), no en la misma linea), corre 'trabajo' -sincrono, puede
    bloquear un rato-, cierra la ventanita, y recien ahi llama a
    'al_terminar' (ej. el messagebox con el resultado).

    con_pantalla_de_carga() en si vuelve DE UNA, antes de que 'trabajo'
    haya corrido - es asincronica, no un llamado bloqueante disfrazado.
    Por eso el resultado no se puede leer en la linea de despues (ver el
    bug real que esto evito en backup_dialog.py): todo lo que dependa
    de 'trabajo' va adentro de 'trabajo' mismo o de 'al_terminar'.

    'al_terminar' corre DESPUES de cerrar la ventanita (no adentro del
    'finally' junto con 'trabajo'), para no superponer un messagebox de
    resultado con la pantalla de carga todavia abierta atras.

    El after() de por medio es lo que importa para que se pinte: mostrar
    la ventanita y llamar a 'trabajo' de una la deja sin pintar (o
    pintada a medias) hasta que 'trabajo' termina, exactamente lo que se
    quiere evitar. Con after(), Tk tiene una vuelta del mainloop para
    dibujarla antes de que arranque el trabajo bloqueante.
    """
    carga = LoadingDialog(master, texto)

    def _ejecutar() -> None:
        try:
            trabajo()
        finally:
            carga.cerrar()
        if al_terminar is not None:
            al_terminar()

    master.after(30, _ejecutar)


class SearchableTable(kit.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        columns: tuple[str, ...],
        headings: dict[str, str],
        column_widths: dict[str, int] | None = None,
        column_anchors: dict[str, str] | None = None,
        search_label: str = "Buscar:",
        buttons: tuple[tuple[str, object] | tuple[str, object, str], ...] = (),
        right_buttons: tuple[tuple[str, object] | tuple[str, object, str], ...] = (),
        on_double_click: object | None = None,
    ) -> None:
        super().__init__(master)
        self.search_var = tk.StringVar()
        self._sort_column: str | None = None
        self._sort_reverso = False
        # Titulos sin la flecha de orden (▲/▼): se agrega y se saca sobre
        # estos (ver _marcar_orden).
        self._titulos_base: dict[str, str] = dict(headings)

        # Busqueda y botones en UNA fila cuando hay ancho (como un flex
        # con justify-content: space-between): la busqueda se estira y los
        # botones quedan a su derecha. Cuando no entran se parte en dos
        # filas (busqueda arriba, botones abajo) - ver _acomodar_barra:
        # Facturas tiene 5 botones (723 px) y en una sola fila el ultimo
        # -"Cancelar factura"- quedaba cortado contra el borde en ventanas
        # angostas. Los cuatro hijos se crean aca y solo cambia su grid.
        barra = kit.Frame(self)
        barra.pack(side="top", fill="x", padx=8, pady=(8, 10))
        barra.columnconfigure(1, weight=1)
        self._barra = barra
        self._barra_en_una_linea: bool | None = None
        self._etiqueta_busqueda = kit.Label(
            barra, text=f"🔍  {search_label}", style="Muted.TLabel"
        )
        # El cuadro de busqueda es lo unico de la barra que puede ceder:
        # se estira con la ventana y se encoge si falta lugar, en vez de
        # medir 30 caracteres fijos.
        self._entry = kit.Entry(barra, textvariable=self.search_var, width=14)
        self._botones_izq = kit.Frame(barra)
        self._botones_der = kit.Frame(barra)
        for entrada in buttons:
            _empacar_boton(self._botones_izq, entrada, side="left")
        # right_buttons: para una accion que no es "del mismo grupo" que
        # el resto (ej. "Crear factura" en ProductsPage, que no edita
        # productos) - separada al otro extremo de la barra en vez de
        # amontonada junto a Nuevo/Editar/Eliminar.
        for entrada in reversed(right_buttons):
            _empacar_boton(self._botones_der, entrada, side="right")
        self._poner_barra_en_dos_filas()
        self._barra_en_una_linea = False
        barra.bind("<Configure>", self._acomodar_barra)
        # Ctrl+/Ctrl-: los anchos de los botones cambian con la letra.
        barra.on_font_scale_changed = lambda: self.after_idle(self._acomodar_barra)
        # Flecha arriba/abajo en la barra de busqueda mueve la seleccion
        # de la tabla SIN sacarle el foco al Entry (ver _mover_seleccion):
        # asi se puede escribir, bajar con las flechas, y seguir
        # escribiendo para afinar la busqueda sin tener que hacer click
        # de nuevo en el cuadro de texto.
        self._entry.bind("<Down>", lambda _evento: self._mover_seleccion(1))
        self._entry.bind("<Up>", lambda _evento: self._mover_seleccion(-1))

        # La tabla va dentro de una tarjeta redondeada (ttk.Treeview no
        # tiene esquinas): theme.py le saca su propio marco 3D.
        tabla_frame = kit.Frame(self, style="Table.TFrame", padding=6)
        tabla_frame.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 6))

        self.tree = ttk.Treeview(
            tabla_frame, columns=columns, show="headings", selectmode="browse"
        )
        # Los anchos vienen en pixeles a letra 100 %: se guardan asi y se
        # escalan con la letra (ver _px), si no a 150 % los titulos
        # ("Precio Detal $ efectivo") quedaban cortados.
        self._anchos_base = dict(column_widths or {})
        self._anchos_de_datos: dict[str, int] | None = None
        for col in columns:
            self.tree.heading(
                col, text=headings[col], command=lambda c=col: self._on_ordenar(c)
            )
            self.tree.column(col, width=self._px(self._anchos_base.get(col, 100)))
        for col, anchor in (column_anchors or {}).items():
            self.tree.column(col, anchor=anchor)
        alinear_encabezados(self.tree)
        self.on_font_scale_changed = self._reescalar_anchos
        self.tree.bind("<Configure>", self._ajustar_columnas, add="+")
        # Titulo resaltado (ver set_highlighted_column). El tema lo repinta
        # desde ThemeManager al cambiar de modo, via este gancho del tree.
        self._col_resaltada: str | None = None
        self._resaltado: tk.Label | None = None
        self.tree._flex_repintar_resaltado = self._actualizar_resaltado

        if on_double_click is not None:
            self.tree.bind("<Double-1>", lambda _evento: on_double_click())
            # Enter hace lo mismo que el doble clic, desde la tabla Y
            # desde el buscador. Desde el buscador importa tanto o mas:
            # el flujo natural es escribir, bajar con las flechas (que a
            # proposito NO le sacan el foco al Entry, ver
            # _mover_seleccion) y abrir - sin esto habia que soltar el
            # teclado e ir al mouse justo en el ultimo paso.
            self.tree.bind("<Return>", lambda _evento: on_double_click())
            self._entry.bind(
                "<Return>", lambda _evento: self._abrir_seleccionada(on_double_click)
            )
        # Si el foco quedo en la tabla (ej. despues de un click en una
        # fila) y se sigue escribiendo, redirige esas teclas al cuadro
        # de busqueda en vez de dejar que el Treeview use su propio
        # "buscar feo" interno (que salta a filas por una letra sin
        # tocar search_var, un comportamiento distinto e inconsistente
        # con el de la barra de busqueda real).
        self.tree.bind("<Key>", self._on_tecla_en_tabla)
        # Fila bajo el cursor: un gris apenas mas oscuro, para seguir los
        # precios en horizontal sin perder la fila.
        self._hover_iid: str | None = None
        self.tree.bind("<Motion>", self._on_hover, add="+")
        self.tree.bind("<Leave>", self._quitar_hover, add="+")
        self.tree.bind("<<TreeviewSelect>>", self._quitar_hover, add="+")

        scrollbar = kit.Scrollbar(
            tabla_frame, orient="vertical", command=self.tree.yview
        )
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        self.status = kit.Label(self, text="", style="Muted.TLabel")
        # Abajo y ANTES que la tabla en el orden de pack: cuando falta
        # alto (letra al 150 %), pack recorta lo ultimo que se empaco, y
        # asi lo que cede es la tabla -que tiene scroll- y no el contador.
        self.status.pack(
            side="bottom", fill="x", padx=8, pady=(0, 8), before=tabla_frame
        )

    # ------------------------------------------------------------
    # barra de busqueda y botones
    # ------------------------------------------------------------
    # Lo que se le exige al cuadro de busqueda para que valga la pena
    # dejar todo en una fila: menos que eso es un campo en el que no se
    # puede escribir comodo.
    _BUSQUEDA_MINIMA_PX = 240

    def _poner_barra_en_una_linea(self) -> None:
        for hijo in self._barra.grid_slaves():
            hijo.grid_forget()
        self._etiqueta_busqueda.grid(row=0, column=0)
        self._entry.grid(row=0, column=1, sticky="ew", padx=(8, 12))
        self._botones_izq.grid(row=0, column=2)
        self._botones_der.grid(row=0, column=3, padx=(12, 0))

    def _poner_barra_en_dos_filas(self) -> None:
        for hijo in self._barra.grid_slaves():
            hijo.grid_forget()
        self._etiqueta_busqueda.grid(row=0, column=0)
        self._entry.grid(row=0, column=1, columnspan=3, sticky="ew", padx=(8, 0))
        self._botones_izq.grid(row=1, column=0, columnspan=2, sticky="w", pady=(6, 0))
        self._botones_der.grid(row=1, column=2, columnspan=2, sticky="e", pady=(6, 0))

    def _acomodar_barra(self, _evento: object = None) -> None:
        """
        Una fila si entra todo (etiqueta, una busqueda de al menos
        _BUSQUEDA_MINIMA_PX y los botones), dos si no. Solo cambia el alto
        de la barra, nunca su ancho, asi que no se dispara a si misma.
        """
        ancho = self._barra.winfo_width()
        if ancho <= 1:  # todavia no se dibujo (o la ventana esta oculta)
            return
        necesario = (
            self._etiqueta_busqueda.winfo_reqwidth()
            + max(self._entry.winfo_reqwidth(), self._BUSQUEDA_MINIMA_PX)
            + self._botones_izq.winfo_reqwidth()
            + self._botones_der.winfo_reqwidth()
            + 8
            + 12
            + 12
        )
        una_linea = ancho >= necesario
        if una_linea == self._barra_en_una_linea:
            return
        self._barra_en_una_linea = una_linea
        if una_linea:
            self._poner_barra_en_una_linea()
        else:
            self._poner_barra_en_dos_filas()

    def _px(self, ancho_base: int) -> int:
        """Ancho de columna a la letra de ahora (100 % = el que se pidio)."""
        escala = getattr(find_app_root(self), "_flex_font_scale", 100)
        return round(ancho_base * escala / 100)

    def _ancho_titulo(self, col: str) -> int:
        """Lo que mide el titulo de la columna (con su margen): menos que
        eso lo corta ("Precio Detal $ efe...")."""
        fuente = tkfont.Font(
            root=self, font=ttk.Style(self).lookup("Treeview.Heading", "font")
        )
        return fuente.measure(str(self.tree.heading(col, "text"))) + 32

    def _ancho_de_datos(self, columnas: Sequence[str]) -> dict[str, int]:
        """Lo que mide el dato mas ancho de cada columna entre las primeras
        filas (con su margen). Una muestra, no las 1.900: se corre al
        redimensionar."""
        fuente = tkfont.nametofont("TkDefaultFont")
        anchos = dict.fromkeys(columnas, 0)
        for iid in self.tree.get_children("")[:60]:
            for col in columnas:
                ancho = fuente.measure(str(self.tree.set(iid, col))) + 20
                anchos[col] = max(anchos[col], ancho)
        return anchos

    def _ajustar_columnas(self, _evento: object = None) -> None:
        """
        Anchos a la letra de ahora, sin que ninguna columna salga de la
        vista. Cada una pide su ancho escalado; si el total no entra, todas
        se achican con la MISMA proporcion, pero ninguna baja del ancho de
        su titulo (cortarlo es lo que se veia a 150 %). La proporcion se
        busca por biseccion (20 vueltas sobre ~10 columnas: nada). Siempre
        se calcula desde el ancho base, asi que repetirlo (al redimensionar,
        con Ctrl+/Ctrl-) no se acumula.
        """
        columnas = self.tree["columns"]
        if self.tree.winfo_width() <= 1 or not columnas:
            return
        # La tabla pide un par de pixeles mas que la suma de sus columnas
        # (el borde). Si se repartieran enteros, en una ventana que mide
        # lo que pide su contenido cada pasada la agrandaria 2 px, sin fin
        # (y X11 termina el proceso). Se descuentan del espacio disponible.
        borde = max(
            0,
            self.tree.winfo_reqwidth()
            - sum(int(self.tree.column(c, "width")) for c in columnas),
        )
        disponible = self.tree.winfo_width() - borde
        deseado = {c: self._px(self._anchos_base.get(c, 100)) for c in columnas}
        piso = {c: self._ancho_titulo(c) for c in columnas}
        # Primero se intenta que tampoco se corte el dato mas ancho de las
        # primeras filas (un SKU sin su ultimo digito es peor que un
        # titulo cortado); si con eso no entran todas, solo el titulo.
        if self._anchos_de_datos is None and self.tree.get_children(""):
            # Una sola vez por juego de columnas/letra: si se midiera en
            # cada filtrado, las columnas se moverian mientras se escribe.
            self._anchos_de_datos = self._ancho_de_datos(columnas)
        if self._anchos_de_datos:
            con_datos = {
                c: max(piso[c], min(deseado[c], self._anchos_de_datos.get(c, 0)))
                for c in columnas
            }
            suma_titulos, suma_datos = sum(piso.values()), sum(con_datos.values())
            if suma_datos <= disponible:
                piso = con_datos
            elif suma_titulos < disponible:
                # No entra todo el dato: cada columna conserva su titulo y
                # recorta lo que falta en la misma proporcion.
                parte = (disponible - suma_titulos) / (suma_datos - suma_titulos)
                piso = {
                    c: round(piso[c] + (con_datos[c] - piso[c]) * parte)
                    for c in columnas
                }

        def total(proporcion: float) -> int:
            return sum(max(piso[c], round(deseado[c] * proporcion)) for c in columnas)

        proporcion = 1.0
        if total(1.0) > disponible:
            bajo, alto = 0.0, 1.0
            for _ in range(20):
                medio = (bajo + alto) / 2
                if total(medio) > disponible:
                    alto = medio
                else:
                    bajo = medio
            proporcion = bajo
        anchos = {c: max(piso[c], round(deseado[c] * proporcion)) for c in columnas}
        # Si sobra ancho, va a la columna mas ancha (el nombre del producto):
        # repartido entre todas, lo ganaban los montos y el nombre se cortaba.
        sobra = disponible - sum(anchos.values())
        if sobra > 0:
            ancha = max(columnas, key=lambda c: self._anchos_base.get(c, 100))
            anchos[ancha] += sobra
        for col in columnas:
            self.tree.column(col, width=anchos[col], stretch=False)
        self._actualizar_resaltado()

    def set_highlighted_column(self, columna: str | None) -> None:
        """
        Resalta el TITULO de una columna (la que mas se usa), con el color
        de seleccion y sin tocar su ancho. ttk no deja darle estilo a un
        solo encabezado, asi que se dibuja encima uno igual: se recoloca
        con cada reparto de anchos. None lo quita.
        """
        self._col_resaltada = columna
        self._actualizar_resaltado()

    def _actualizar_resaltado(self) -> None:
        columna = self._col_resaltada
        columnas = list(self.tree["columns"])
        if columna is None or columna not in columnas or self.tree.winfo_width() <= 1:
            if self._resaltado is not None:
                self._resaltado.place_forget()
            return
        x = sum(
            int(self.tree.column(c, "width"))
            for c in columnas[: columnas.index(columna)]
        )
        ancho = int(self.tree.column(columna, "width"))
        # Alto del encabezado: ttk no lo informa; se mide recorriendo hacia
        # abajo, en el borde izquierdo de la tabla (el centro de la
        # columna puede quedar fuera de lo que la tabla ya acomodo tras
        # cambiar los anchos), hasta que deja de ser "heading".
        # (el primer pixel de arriba es borde, no encabezado).
        alto = 0
        for y in range(80):
            if self.tree.identify_region(2, y) == "heading":
                alto = y + 1
            elif alto:
                break
        if alto == 0:
            return
        paleta = get_palette(self)
        if self._resaltado is None:
            self._resaltado = tk.Label(self.tree, borderwidth=0, padx=8)
            self._resaltado.bind(
                "<Button-1>",
                lambda _e: (
                    self._col_resaltada and self._on_ordenar(self._col_resaltada)
                ),
            )
        self._resaltado.configure(
            text=str(self.tree.heading(columna, "text")),
            anchor={"w": "w", "e": "e", "center": "center"}.get(
                str(self.tree.column(columna, "anchor")), "w"
            ),
            background=paleta.select_bg,
            foreground=paleta.select_fg,
            font=ttk.Style(self).lookup("Treeview.Heading", "font"),
        )
        # -1 de ancho y de alto: se deja visible la linea fina de borde.
        self._resaltado.place(
            x=x, y=0, width=max(ancho - 1, 1), height=max(alto - 1, 1)
        )

    def _reescalar_anchos(self) -> None:
        """Ctrl+/Ctrl- con la tabla a la vista: los anchos siguen a la letra."""
        self._anchos_de_datos = None  # el dato mide distinto con otra letra
        self.after_idle(self._ajustar_columnas)

    def set_columns(
        self,
        columns: tuple[str, ...],
        headings: dict[str, str],
        column_widths: dict[str, int] | None = None,
        column_anchors: dict[str, str] | None = None,
    ) -> None:
        """
        Cambia las columnas de la tabla ya construida (Configuracion >
        Personalizacion). Vacia las filas: las que estaban tienen la
        cantidad de valores de la configuracion anterior, asi que quien
        llame vuelve a poblar con set_rows().
        """
        self.tree.delete(*self.tree.get_children())
        self._sort_column = None
        self._titulos_base = dict(headings)
        self._anchos_base = dict(column_widths or {})
        self._anchos_de_datos = None
        self.tree.configure(columns=columns)
        for col in columns:
            self.tree.heading(
                col, text=headings[col], command=lambda c=col: self._on_ordenar(c)
            )
            self.tree.column(
                col,
                width=self._px(self._anchos_base.get(col, 100)),
                anchor=(column_anchors or {}).get(col, "w"),
            )
        alinear_encabezados(self.tree)
        self._ajustar_columnas()

    def set_rows(self, rows: list[tuple[str, tuple]]) -> None:
        """rows: lista de (iid, values) ya armada por quien la use."""
        self.tree.delete(*self.tree.get_children())
        self._quitar_orden()  # las filas vuelven en el orden en que se pasaron
        for indice, (iid, values) in enumerate(rows):
            self.tree.insert(
                "",
                "end",
                iid=iid,
                values=values,
                tags=(STRIPE_TAG,) if indice % 2 else (),
            )
        self._pintar_franjas()
        self._ajustar_columnas()

    def _pintar_franjas(self) -> None:
        self.tree.tag_configure(STRIPE_TAG, background=get_palette(self).stripe_bg)
        self.tree.tag_configure(HOVER_TAG, background=get_palette(self).hover_bg)

    def _on_hover(self, evento: tk.Event) -> None:
        iid = self.tree.identify_row(evento.y)
        if iid == self._hover_iid:
            return
        self._quitar_hover()
        # Ni la seleccionada (se vería menos) ni el separador de
        # "COINCIDENCIAS".
        if not iid or iid in self.tree.selection():
            return
        tags = self.tree.item(iid, "tags")
        if "separator" in tags:
            return
        self._hover_iid = iid
        self.tree.item(iid, tags=(HOVER_TAG, *tags))

    def _quitar_hover(self, _evento: object = None) -> None:
        iid, self._hover_iid = self._hover_iid, None
        if iid is not None and self.tree.exists(iid):
            tags = tuple(t for t in self.tree.item(iid, "tags") if t != HOVER_TAG)
            self.tree.item(iid, tags=tags)

    def _rehacer_franjas(self) -> None:
        """Despues de reordenar: una fila si, otra no, en el orden nuevo."""
        for indice, iid in enumerate(self.tree.get_children("")):
            tags = [t for t in self.tree.item(iid, "tags") if t != STRIPE_TAG]
            if indice % 2:
                tags.insert(0, STRIPE_TAG)
            self.tree.item(iid, tags=tags)

    def set_grouped_rows(
        self,
        first: list[tuple[str, tuple]],
        matches: list[tuple[str, tuple]],
        *,
        separator_label: str = "COINCIDENCIAS",
    ) -> None:
        """Muestra coincidencias internas despues de un separador visual."""
        self.tree.delete(*self.tree.get_children())
        self._quitar_orden()
        for iid, values in first:
            self.tree.insert("", "end", iid=iid, values=values)
        if matches:
            values = (separator_label,) + ("",) * (len(self.tree["columns"]) - 1)
            self.tree.insert(
                "", "end", iid="__coincidencias__", values=values, tags=("separator",)
            )
        for iid, values in matches:
            self.tree.insert("", "end", iid=iid, values=values)
        self.tree.tag_configure("separator", foreground=get_palette(self).muted_fg)

    def set_status(self, text: str) -> None:
        self.status.configure(text=text)

    def selected_iid(self) -> str | None:
        seleccion = self.tree.selection()
        return seleccion[0] if seleccion else None

    def set_row_tag(self, iid: str, tag: str) -> None:
        """Para que quien use la tabla marque una fila (ej. factura cancelada)."""
        franja = (STRIPE_TAG,) if STRIPE_TAG in self.tree.item(iid, "tags") else ()
        self.tree.item(iid, tags=(*franja, tag))

    def tag_style(self, tag: str, **opciones) -> None:
        """Pasa directo a Treeview.tag_configure (ej. foreground='gray')."""
        self.tree.tag_configure(tag, **opciones)

    # ------------------------------------------------------------
    # navegar la tabla sin perder la busqueda
    # ------------------------------------------------------------
    def _abrir_seleccionada(self, on_double_click) -> str:
        """
        Enter en el buscador. Si ya hay una fila elegida la abre; si no,
        elige la PRIMERA y la abre, que es lo que espera quien escribio
        un SKU y vio un unico resultado: escribir y apretar Enter, sin
        bajar con la flecha primero.

        Devuelve "break" para que el Entry no propague el Enter (Tk lo
        usaría para su propio manejo del evento).
        """
        if not self.tree.selection():
            hijos = self._filas_elegibles()
            if not hijos:
                return "break"
            self.tree.selection_set(hijos[0])
        on_double_click()
        return "break"

    def _mover_seleccion(self, delta: int) -> str:
        """delta=+1/-1: mueve la fila seleccionada, sin sacarle el foco
        a la barra de busqueda (ver bind de <Down>/<Up> en __init__)."""
        hijos = self._filas_elegibles()
        if not hijos:
            return "break"

        actual = self.tree.selection()
        if actual and actual[0] in hijos:
            indice = hijos.index(actual[0]) + delta
        else:
            indice = 0 if delta > 0 else len(hijos) - 1
        indice = max(0, min(len(hijos) - 1, indice))

        nuevo_iid = hijos[indice]
        self.tree.selection_set(nuevo_iid)
        self.tree.see(nuevo_iid)
        return "break"

    def _filas_elegibles(self) -> tuple[str, ...]:
        """El rotulo de coincidencias informa; nunca es una fila de datos."""
        return tuple(
            iid for iid in self.tree.get_children("") if iid != "__coincidencias__"
        )

    def _on_tecla_en_tabla(self, evento: tk.Event) -> str | None:
        """Letra/numero mientras la tabla tiene el foco -> se lo manda a
        search_var y devuelve el foco al Entry. Todo lo demas (flechas,
        Tab, Enter, etc.) sigue el comportamiento normal del Treeview -
        solo se intercepta lo que de verdad es "seguir escribiendo"."""
        # Ctrl+algo (Ctrl+- / Ctrl++ de tamano de letra, Ctrl+C...) no es
        # escribir: sin esto el "-" de Ctrl+- se tipeaba en la busqueda,
        # le robaba el foco a la tabla y el atajo global no llegaba a
        # correr (funcionaba recien al segundo intento).
        if getattr(evento, "state", 0) & 0x4:  # mascara de Control
            return None
        if evento.keysym == "BackSpace":
            texto = self.search_var.get()
            if texto:
                self.search_var.set(texto[:-1])
            self._entry.focus_set()
            self._entry.icursor("end")
            return "break"
        if evento.char and evento.char.isprintable():
            self.search_var.set(self.search_var.get() + evento.char)
            self._entry.focus_set()
            self._entry.icursor("end")
            return "break"
        return None

    # ------------------------------------------------------------
    # ordenar con click en el encabezado
    # ------------------------------------------------------------
    def _marcar_orden(self) -> None:
        """Flecha en el titulo de la columna ordenada (▲ ascendente, ▼ descendente)."""
        for col, base in self._titulos_base.items():
            if col not in self.tree["columns"]:
                continue
            flecha = ""
            if col == self._sort_column:
                flecha = " \u25bc" if self._sort_reverso else " \u25b2"
            self.tree.heading(col, text=base + flecha)
        self._ajustar_columnas()  # el titulo con flecha mide mas

    def _quitar_orden(self) -> None:
        if self._sort_column is not None:
            self._sort_column = None
            self._sort_reverso = False
            self._marcar_orden()

    def _on_ordenar(self, columna: str) -> None:
        if self._sort_column == columna:
            self._sort_reverso = not self._sort_reverso
        else:
            self._sort_column = columna
            self._sort_reverso = False
        self._marcar_orden()

        filas = [
            (self.tree.set(iid, columna), iid) for iid in self.tree.get_children("")
        ]
        filas.sort(key=lambda par: _clave_de_orden(par[0]), reverse=self._sort_reverso)
        for indice, (_valor, iid) in enumerate(filas):
            self.tree.move(iid, "", indice)
        self._rehacer_franjas()


def alinear_encabezados(tree: ttk.Treeview) -> None:
    """Cada titulo alineado como sus datos: un monto a la derecha bajo un
    titulo centrado se leia como si no le perteneciera."""
    for col in tree["columns"]:
        tree.heading(col, anchor=tree.column(col, "anchor"))


# Icono de cada accion de las barras de las tablas, por como empieza su
# texto: asi las paginas no cambian (siguen pasando solo texto y
# comando) y "Nuevo producto" / "Nueva factura" comparten el +.
_ICONOS_POR_TEXTO = (
    ("Nuev", "plus"),
    ("Crear", "receipt"),
    ("Editar", "pencil"),
    ("Ver", "eye"),
    ("Eliminar", "trash-2"),
    ("Generar PDF", "file-down"),
    ("Cierre de caja", "calculator"),
    ("Cancelar factura", "ban"),
    ("Anular", "ban"),
)


def _icono_para(texto: str) -> str | None:
    return next(
        (icono for prefijo, icono in _ICONOS_POR_TEXTO if texto.startswith(prefijo)),
        None,
    )


def _empacar_boton(
    toolbar: kit.Frame,
    entrada: tuple[str, object] | tuple[str, object, str],
    *,
    side: str,
) -> kit.Button:
    # Tupla de 2: boton comun. De 3: el tercer valor es un ttk.Style
    # propio (ej. "Success.TButton" en theme.py) para destacar una
    # accion puntual sin tocar el estilo de todos los botones de la
    # barra.
    texto, comando, *resto = entrada
    boton = kit.Button(toolbar, text=texto, command=comando, icon=_icono_para(texto))
    if resto:
        boton.configure(style=resto[0])
    boton.pack(side=side, padx=3)
    return boton


def _clave_de_orden(texto: str):
    """
    Intenta leer el texto como numero (formato local: '$ 1.234,56' o
    '2,5') para que las columnas de plata/cantidad ordenen por valor y
    no alfabeticamente (donde '$ 10,00' quedaría antes que '$ 2,00').
    Si no parece un numero, ordena como texto. La tupla (0, ...) / (1, ...)
    al frente evita comparar float con str directamente: Python nunca
    llega a comparar el segundo elemento entre grupos distintos.
    """
    limpio = texto.strip().replace("$", "").replace(".", "").replace(",", ".").strip()
    try:
        return (0, float(limpio))
    except ValueError:
        return (1, texto.upper())


# ------------------------------------------------------------
# avisos que se van solos ("toast")
# ------------------------------------------------------------
# tipo -> (icono, color de fondo de la paleta)
_TOAST_TIPOS = {
    "ok": ("circle-check", "success_bg"),
    "info": ("info", "accent"),
    "error": ("triangle-alert", "danger_bg"),
}


def toast(
    widget: tk.Misc, texto: str, *, tipo: str = "ok", duracion_ms: int = 3500
) -> kit.Frame:
    """
    Aviso chico abajo a la derecha de la ventana de 'widget', que se va
    solo a los pocos segundos. Para un resultado que no pide decision
    ("Respaldo creado", "PDF generado"): un messagebox para eso es un
    click de mas cada vez y frena a quien esta trabajando.

    Los errores que SI hay que leer (no se pudo guardar, falta un dato)
    siguen siendo messagebox: un aviso que se va solo se puede perder.

    Uno solo a la vez por ventana: uno nuevo reemplaza al anterior en vez
    de apilarse. Se ubica con place() encima de todo lo demas, asi no
    empuja ni re-acomoda nada de la pantalla.
    """
    from . import icons
    from .theme import ctk_font, pair

    ventana = widget.winfo_toplevel()
    anterior = getattr(ventana, "_flex_toast", None)
    if anterior is not None and anterior.winfo_exists():
        anterior.destroy()

    nombre_icono, fondo = _TOAST_TIPOS.get(tipo, _TOAST_TIPOS["info"])
    aviso = kit.Frame(ventana, style="Card.TFrame", padding=(14, 10))
    aviso.configure(fg_color=pair(fondo), border_color=pair(fondo))
    # Texto blanco ("CardAccent*" = texto sobre color) en negrita normal.
    etiqueta = kit.Label(
        aviso,
        text=f"  {texto}",
        image=icons.icon(ventana, nombre_icono, "#ffffff", size=20),
        compound="left",
        style="CardAccentDate.TLabel",
        wraplength=420,
    )
    etiqueta.configure(font=ctk_font(ventana, "bold"))
    etiqueta.pack()
    aviso.place(relx=1.0, rely=1.0, x=-24, y=-24, anchor="se")
    aviso.lift()
    ventana._flex_toast = aviso

    def _cerrar() -> None:
        if aviso.winfo_exists():
            aviso.destroy()

    aviso.after(duracion_ms, _cerrar)
    return aviso


# ------------------------------------------------------------
# etiqueta flotante al pasar el mouse ("tooltip")
# ------------------------------------------------------------
class TextoVacio:
    """
    Un mensaje centrado SOBRE una tabla o lista cuando no tiene filas
    ("Aun no hay lineas. Busca un producto..."): sin el, un panel vacio es
    un bloque gris que no dice que hacer. Es un tk.Label comun (no uno de
    CTk, que copia un fondo que aca no existe); ThemeManager lo repinta
    solo al cambiar de modo (atributo _flex_repintar).
    """

    def __init__(self, sobre: tk.Misc, texto: str) -> None:
        self._sobre = sobre
        self._label = tk.Label(sobre, text=texto, justify="center", wraplength=320)
        self._label._flex_repintar = self._pintar
        self._pintar()

    def _pintar(self) -> None:
        paleta = get_palette(self._sobre)
        fondo = ttk.Style(self._sobre).lookup("Treeview", "fieldbackground") or (
            paleta.entry_bg
        )
        if isinstance(self._sobre, tk.Listbox):
            fondo = self._sobre.cget("background")
        self._label.configure(
            background=fondo,
            foreground=paleta.muted_fg,
            font=tkfont.nametofont("TkDefaultFont"),
        )

    def mostrar(self, vacio: bool) -> None:
        if vacio:
            self._pintar()
            self._label.place(relx=0.5, rely=0.42, anchor="center")
            self._label.lift()
        else:
            self._label.place_forget()


def tab_pasa_al_siguiente(texto: tk.Text) -> None:
    """
    Tab (y Shift+Tab) en una caja de texto de varios renglones pasa al
    campo siguiente (o anterior) en vez de insertar una sangria: es lo
    que se espera en un formulario que se llena de corrido con el
    teclado. Lo de dentro de la caja no necesita sangrias.
    """

    def _siguiente(_evento: object) -> str:
        texto.tk_focusNext().focus_set()
        return "break"

    def _anterior(_evento: object) -> str:
        texto.tk_focusPrev().focus_set()
        return "break"

    texto.bind("<Tab>", _siguiente)
    # Shift+Tab llega como ISO_Left_Tab en X11 y como Shift-Tab en Windows.
    texto.bind("<Shift-Tab>", _anterior)
    texto.bind("<ISO_Left_Tab>", _anterior)


def info_hint(parent: tk.Misc, texto: str) -> kit.Label:
    """
    Un icono "i" chico que muestra 'texto' al pasar el mouse. Para las
    pistas de uso ("doble clic abre...") que antes eran un recuadro: no
    ocupa lugar, no se mueve nada cuando uno deja de necesitarla y la
    ayuda sigue a un paso. 'texto' puede llevar saltos de linea.
    """
    icono = kit.Label(
        parent,
        text="",
        image=icons.icon(parent, "info", pair("muted_fg"), size=18),
    )
    Tooltip(icono, lambda: texto)
    return icono


class Tooltip:
    """
    Un cartelito con 'texto()' al lado de 'widget' cuando el mouse se
    queda encima un momento. 'texto' es una funcion y no un str: la barra
    lateral solo quiere mostrar el nombre de la pagina cuando esta
    PLEGADA (con el nombre a la vista seria ruido), asi que devuelve None
    cuando no hace falta, y el cartel no aparece.

    Es un Toplevel sin marco (overrideredirect) que se crea al mostrarse
    y se destruye al salir: nada vivo mientras no se usa.
    """

    _DEMORA_MS = 350

    def __init__(self, widget: tk.Misc, texto: Callable[[], str | None]) -> None:
        self._widget = widget
        self._texto = texto
        self._ventana: tk.Toplevel | None = None
        self._pendiente: str | None = None
        widget.bind("<Enter>", self._al_entrar, add="+")
        widget.bind("<Leave>", self._al_salir, add="+")
        widget.bind("<Button-1>", self._al_salir, add="+")

    def _al_entrar(self, _evento: object = None) -> None:
        self._cancelar()
        self._pendiente = self._widget.after(self._DEMORA_MS, self.mostrar)

    def _al_salir(self, _evento: object = None) -> None:
        self._cancelar()
        self.ocultar()

    def _cancelar(self) -> None:
        if self._pendiente is not None:
            self._widget.after_cancel(self._pendiente)
            self._pendiente = None

    def mostrar(self) -> None:
        self._pendiente = None
        texto = self._texto()
        if not texto or not self._widget.winfo_exists():
            return
        self.ocultar()
        paleta = get_palette(self._widget)
        ventana = tk.Toplevel(self._widget)
        ventana.wm_overrideredirect(True)
        ventana.configure(background=paleta.fg)
        tk.Label(
            ventana,
            text=texto,
            background=paleta.fg,
            foreground=paleta.bg,
            font=tkfont.nametofont("TkDefaultFont"),
            padx=10,
            pady=4,
        ).pack()
        x = self._widget.winfo_rootx() + self._widget.winfo_width() + 8
        y = self._widget.winfo_rooty() + (self._widget.winfo_height() // 2) - 14
        ventana.wm_geometry(f"+{x}+{y}")
        self._ventana = ventana

    def ocultar(self) -> None:
        if self._ventana is not None and self._ventana.winfo_exists():
            self._ventana.destroy()
        self._ventana = None
