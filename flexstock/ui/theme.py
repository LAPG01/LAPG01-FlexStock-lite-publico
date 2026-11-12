"""
Tema visual (claro/oscuro) para toda la aplicacion.

DECISION DE DISENO: fuerza ttk theme_use('clam') en vez de dejar el
tema nativo de cada SO (vista/xpnative en Windows, aqua en mac, default
en Linux). Los temas nativos ignoran la mayoria de los colores que se
les pide via Style.configure() -por eso "modo oscuro" con el tema
nativo de Windows simplemente no se ve. 'clam' viene incluido con
Tcl/Tk (no es una dependencia nueva) y es el unico que respeta
background/foreground en todos los widgets. Efecto secundario: la app
se ve identica en Windows y Arch Linux, que ya es un requisito propio
(Regla 8 del CLAUDE.md).

Los widgets ttk (Button, Entry, Notebook, Treeview, etc.) toman su
color de ttk.Style, que es GLOBAL a la aplicacion: configurarlo una
vez repinta de una sola pasada todos los widgets ttk que ya existen Y
los que se creen despues (una pestana nueva, un dialogo nuevo). No
hace falta recorrer nada para esos.

Los pocos widgets tk "clasicos" que usa la app (Menu, Listbox, Text) no
pasan por Style: cada uno se pinta a mano. Los que ya estan en pantalla
al cambiar de tema los repinta ThemeManager.apply() recorriendo el
arbol de widgets una vez; los que se crean despues (ej. el Listbox de
"Nueva factura", que es un dialogo nuevo cada vez) se pintan solos al
nacer, leyendo la paleta vigente con get_palette().
"""

from __future__ import annotations

import tkinter as tk
import tkinter.font as tkfont
from dataclasses import dataclass
from tkinter import ttk

import customtkinter as ctk


@dataclass(frozen=True)
class Palette:
    bg: str  # fondo general (ventana, frames)
    surface: str  # fondo de tablas/paneles (Treeview, pestanas)
    fg: str  # texto normal
    muted_fg: str  # texto secundario (status, filas canceladas/inactivas)
    entry_bg: str  # fondo de Entry/Combobox/Listbox/Text
    border: str  # bordes y separadores
    accent: str  # foco / pestana activa
    select_bg: str  # fondo de fila o texto seleccionado
    select_fg: str  # texto sobre select_bg
    button_bg: str
    button_active_bg: str
    disabled_fg: str
    success_bg: str  # boton de accion positiva (ej. "Crear factura")
    success_active_bg: str
    success_fg: str
    danger_bg: str  # boton de accion destructiva (ej. "Eliminar")
    danger_active_bg: str
    danger_fg: str
    danger_soft_bg: str  # relleno suave del boton "Eliminar" de contorno (hover)
    notice_bg: str  # aviso suave (recuadro amarillo, ver kit.Notice)
    notice_border: str
    notice_fg: str
    warning_bg: str  # accion de peso (ej. "Cancelar factura"): ni exito ni borrado
    warning_active_bg: str
    warning_fg: str
    edit_bg: str  # accion de edicion (ej. "Editar" en cualquier tabla)
    edit_active_bg: str
    edit_fg: str
    accent_hover: str  # hover de lo que ya es color de acento (casillas, nav)
    heading_bg: str  # encabezado de las tablas
    heading_fg: str  # texto del encabezado: gris oscuro, mas firme que muted_fg
    hover_bg: str  # fila de la tabla bajo el cursor (mas suave que select_bg)
    stripe_bg: str  # fila par de las tablas, apenas distinta de surface
    sidebar_bg: str  # barra lateral de navegacion (CustomTkinter)
    sidebar_fg: str
    sidebar_muted_fg: str
    sidebar_hover_bg: str
    sidebar_active_bg: str
    sidebar_active_fg: str


# success/danger/warning toman su tono de Open Color
# (github.com/open-color/open-color, MIT) en vez de inventados a ojo: es
# una escala pensada para que cada color lea bien de fondo solido con
# texto blanco encima, en claro y en oscuro. green[8]/red[7]/orange[7]
# para el tono base, un escalon mas oscuro para el hover en claro; en
# oscuro se invierte -el hover aclara en vez de oscurecer, se ve
# "apagado" si no.
#
# Los grises de fondo/superficie/borde son de la escala "slate" de
# Tailwind (MIT): fondo apenas azulado, tarjetas blancas encima, y la
# barra lateral oscura en los dos modos para que la navegacion se lea
# como algo aparte del contenido.
LIGHT = Palette(
    bg="#f1f4f9",
    surface="#ffffff",
    fg="#1e293b",  # slate-800
    muted_fg="#64748b",  # slate-500
    entry_bg="#ffffff",
    border="#d5dce6",
    accent="#2563eb",  # blue-600
    select_bg="#dbe7ff",
    select_fg="#1e3a8a",  # blue-900 sobre celeste: se lee sin gritar
    button_bg="#e5eaf2",
    button_active_bg="#d5dde9",
    disabled_fg="#a0aab8",
    success_bg="#2f9e44",  # Open Color green[8]
    success_active_bg="#268a3a",  # green[9]
    success_fg="#ffffff",
    danger_bg="#e03131",  # Open Color red[7]
    danger_active_bg="#c92a2a",  # red[8]
    danger_fg="#ffffff",
    danger_soft_bg="#ffe3e3",  # red[1]
    notice_bg="#fff9db",  # yellow[0]
    notice_border="#ffe066",  # yellow[3]
    notice_fg="#5f4b00",
    warning_bg="#f76707",  # Open Color orange[7]
    warning_active_bg="#e8590c",  # orange[8]
    warning_fg="#ffffff",
    # Editar / Ver detalle: celeste del acento con texto azul, en vez del
    # amarillo de antes, que gritaba mas que "Crear" al lado. Se
    # distingue de un boton comun (gris) sin competir con el verde.
    edit_bg="#dbe7ff",
    edit_active_bg="#c4d7fb",
    edit_fg="#1e40af",
    accent_hover="#1d4ed8",  # blue-700
    heading_bg="#f1f5f9",  # slate-100
    heading_fg="#475569",  # slate-600
    hover_bg="#e9eef5",
    stripe_bg="#f8fafc",  # slate-50
    # La barra sigue al tema: blanca en modo claro (oscura solo en el modo
    # oscuro). El azul del item activo es el mismo en los dos.
    sidebar_bg="#ffffff",
    sidebar_fg="#1e293b",  # slate-800
    sidebar_muted_fg="#64748b",  # slate-500
    sidebar_hover_bg="#eef2f7",
    sidebar_active_bg="#2563eb",
    sidebar_active_fg="#ffffff",
)

# Modo oscuro suavizado (nov 2026): el texto ya no es casi blanco (#e5e7eb ->
# #d5d9e0, 11:1 de contraste: de sobra) y el fondo, las tarjetas y los campos
# se separan mas entre si. Con menos brillo en las letras y tonos mas
# marcados hace falta menos borde, y se cansa menos la vista en una jornada
# de facturar.
DARK = Palette(
    bg="#171a20",
    surface="#21252d",
    fg="#d5d9e0",
    muted_fg="#9aa3b2",
    entry_bg="#2b303a",
    border="#3a4150",
    accent="#4f8cff",
    select_bg="#2b4a80",
    select_fg="#ffffff",
    button_bg="#2e3340",
    button_active_bg="#3a4150",
    disabled_fg="#5f6878",
    success_bg="#2ea043",
    success_active_bg="#3fb950",
    success_fg="#ffffff",
    danger_bg="#c92a2a",  # Open Color red[8]
    danger_active_bg="#e03131",  # red[7], mas claro: el hover aclara en oscuro
    danger_fg="#ffffff",
    danger_soft_bg="#3b1f23",  # rojo muy oscuro, apenas distinto del fondo
    notice_bg="#2f2a18",  # amarillo muy oscuro
    notice_border="#6b5a1e",
    notice_fg="#f0dc9a",
    warning_bg="#e8590c",  # Open Color orange[8]
    warning_active_bg="#f76707",  # orange[7], mas claro: el hover aclara en oscuro
    warning_fg="#ffffff",
    edit_bg="#23365a",
    edit_active_bg="#2c4473",
    edit_fg="#c7d9ff",
    accent_hover="#6ea0ff",  # en oscuro el hover aclara
    heading_bg="#272c36",
    heading_fg="#b8c0cc",
    hover_bg="#2f3541",
    stripe_bg="#252a33",
    sidebar_bg="#0d0f13",
    sidebar_fg="#d8dde6",
    sidebar_muted_fg="#7d8696",
    sidebar_hover_bg="#1b1f27",
    sidebar_active_bg="#2f5fc4",
    sidebar_active_fg="#ffffff",
)

_PALETTES: dict[str, Palette] = {"light": LIGHT, "dark": DARK}


def pair(attr: str) -> tuple[str, str]:
    """
    El mismo color de la paleta en claro y en oscuro, como lo pide
    CustomTkinter: un widget CTk recibe la tupla (claro, oscuro) y elige
    solo cuando cambia el modo, sin que nadie tenga que repintarlo.
    """
    return getattr(LIGHT, attr), getattr(DARK, attr)


# Tamanos de letra (en puntos, al 100 %) de cada rol tipografico de los
# widgets CustomTkinter. Los tk/ttk clasicos (Treeview, Listbox, Text)
# siguen leyendo las fuentes nombradas TkDefaultFont & cia., que se
# escalan con la misma preferencia (ver ThemeManager.set_font_scale).
FONT_ROLES: dict[str, tuple[int, str]] = {
    "body": (14, "normal"),
    "bold": (14, "bold"),
    "heading": (16, "bold"),
    "title": (22, "bold"),
    "small": (11, "normal"),
    "small_bold": (11, "bold"),
    "card_value": (18, "bold"),
    "total": (26, "bold"),  # el TOTAL de una factura: lo primero que se mira
    "big": (17, "bold"),
    "brand": (20, "bold"),
}


def _base_family() -> str:
    """La misma familia que ya usa Tk por defecto en cada sistema (Segoe UI
    en Windows, Noto/DejaVu Sans en Linux): CTk trae 'Roboto' de fabrica,
    que casi nunca esta instalada y cae a una fuente de reemplazo fea."""
    return tkfont.nametofont("TkDefaultFont").actual("family")


def _font_px(widget: tk.Misc, points: int, scale: int) -> int:
    """
    CTkFont mide en PIXELES, las fuentes nombradas de Tk en PUNTOS. Se
    convierte con el 'tk scaling' real (pixeles por punto) y se divide
    por el escalado propio de CTk, que en Windows con alta densidad ya
    multiplica todo: sin esa division, la letra quedaría escalada dos
    veces.
    """
    px_por_punto = float(widget.tk.call("tk", "scaling"))
    tracker = ctk.ScalingTracker
    raiz = find_app_root(widget)
    # Antes del primer widget CTk, la raiz todavia no esta registrada en
    # el ScalingTracker: se le pregunta el escalado directo.
    dpi = tracker.window_dpi_scaling_dict.get(raiz)
    if dpi is None:
        dpi = tracker.get_window_dpi_scaling(raiz)
    ctk_scaling = dpi * tracker.widget_scaling
    return max(8, round(points * scale / 100 * px_por_punto / ctk_scaling))


def ctk_font(widget: tk.Misc, role: str = "body"):
    """
    Fuente compartida de un rol ('body', 'heading', ...) para un widget
    CTk. Una sola instancia por rol y por ventana raiz: cambiar el tamano
    de letra configura esas pocas fuentes y CTk re-dibuja solo todo lo
    que las usa, sin recorrer el arbol de widgets.
    """
    root = find_app_root(widget)
    fuentes = getattr(root, "_flex_fonts", None)
    if fuentes is None:
        fuentes = root._flex_fonts = {}
    fuente = fuentes.get(role)
    if fuente is None:
        puntos, peso = FONT_ROLES[role]
        escala = getattr(root, "_flex_font_scale", 100)
        fuente = ctk.CTkFont(
            family=_base_family(), size=_font_px(root, puntos, escala), weight=peso
        )
        fuentes[role] = fuente
    return fuente


def _rescale_ctk_fonts(root: tk.Misc, scale: int) -> None:
    root._flex_font_scale = scale
    for role, fuente in getattr(root, "_flex_fonts", {}).items():
        puntos, _peso = FONT_ROLES[role]
        fuente.configure(size=_font_px(root, puntos, scale))


def install_ctk_theme() -> None:
    """
    Vuelca la paleta de este archivo al tema global de CustomTkinter, asi
    CUALQUIER widget CTk (los de ui/kit.py y tambien los que CTk arma por
    dentro, como el menu desplegable de un combo) sale con los colores de
    FlexStock en vez del azul de fabrica. Idempotente.
    """
    t = ctk.ThemeManager.theme
    bg, surface, border = pair("bg"), pair("surface"), pair("border")
    fg, muted = pair("fg"), pair("muted_fg")
    accent, accent_hover = pair("accent"), pair("accent_hover")
    t["CTk"]["fg_color"] = list(bg)
    t["CTkToplevel"]["fg_color"] = list(bg)
    t["CTkFrame"].update(
        fg_color=list(surface),
        top_fg_color=list(surface),
        border_color=list(border),
        corner_radius=10,
    )
    t["CTkButton"].update(
        fg_color=list(pair("button_bg")),
        hover_color=list(pair("button_active_bg")),
        text_color=list(fg),
        text_color_disabled=list(pair("disabled_fg")),
        border_color=list(border),
        corner_radius=8,
    )
    t["CTkLabel"]["text_color"] = list(fg)
    t["CTkEntry"].update(
        fg_color=list(pair("entry_bg")),
        border_color=list(border),
        text_color=list(fg),
        placeholder_text_color=list(muted),
        border_width=1,
        corner_radius=8,
    )
    for clave in ("CTkCheckBox", "CTkRadioButton"):
        t[clave].update(
            fg_color=list(accent),
            hover_color=list(accent_hover),
            border_color=list(muted),
            text_color=list(fg),
            text_color_disabled=list(pair("disabled_fg")),
        )
    t["CTkCheckBox"].update(border_width=2, corner_radius=5)
    t["CTkRadioButton"].update(border_width_unchecked=2)
    t["CTkSwitch"].update(progress_color=list(accent), text_color=list(fg))
    t["CTkProgressBar"].update(
        progress_color=list(accent), fg_color=list(pair("button_bg"))
    )
    t["CTkComboBox"].update(
        fg_color=list(pair("entry_bg")),
        border_color=list(border),
        button_color=list(border),
        button_hover_color=list(muted),
        text_color=list(fg),
        border_width=1,
        corner_radius=8,
    )
    t["CTkOptionMenu"].update(
        fg_color=list(accent), button_color=list(accent_hover), corner_radius=8
    )
    t["CTkScrollbar"].update(
        button_color=list(pair("button_active_bg")), button_hover_color=list(muted)
    )
    t["CTkSegmentedButton"].update(
        fg_color=list(pair("button_bg")),
        unselected_color=list(pair("button_bg")),
        unselected_hover_color=list(pair("button_active_bg")),
        # El segmento elegido, claro y bien separado del resto (blanco en
        # modo claro, azul de seleccion en oscuro) y no el azul del acento:
        # el texto del boton es UNO solo para todos los segmentos, y sobre
        # el azul fuerte no se leia en modo claro.
        selected_color=[LIGHT.surface, DARK.select_bg],
        selected_hover_color=[LIGHT.surface, DARK.select_bg],
        text_color=list(fg),
        border_width=3,
    )
    t["CTkTextbox"].update(
        fg_color=list(pair("entry_bg")), border_color=list(border), text_color=list(fg)
    )
    t["DropdownMenu"].update(
        fg_color=list(surface),
        hover_color=list(pair("select_bg")),
        text_color=list(fg),
    )


def set_ctk_mode(mode: str) -> None:
    """Modo claro/oscuro global de CTk (nunca 'system': ver kit.py)."""
    ctk.set_appearance_mode("dark" if mode == "dark" else "light")


def font_shortcut_direction(keysym: str) -> int | None:
    """Direccion del zoom para las variantes que reporta cada teclado."""
    if keysym in {"plus", "equal", "KP_Add"}:
        return 1
    if keysym in {"minus", "underscore", "KP_Subtract"}:
        return -1
    return None


# Clases winfo_class() de los widgets tk "clasicos" que ThemeManager
# pinta a mano. Todo lo que no aparece aca es ttk (ya cubierto por
# Style) o no necesita color propio (ej. un Frame ttk).
_CLASSIC_CLASSES = {"Canvas", "Menu", "Listbox", "Text", "Toplevel", "Tk", "Treeview"}


def _heading_family() -> str:
    return tkfont.nametofont("TkHeadingFont").actual("family")


def find_app_root(widget: tk.Misc) -> tk.Misc:
    """
    Sube por 'master' hasta el tk.Tk() de la aplicacion, sin importar
    cuantos Toplevel (dialogos) haya en el medio - a diferencia de
    winfo_toplevel(), que desde adentro de un dialogo devuelve el
    dialogo mismo, no la ventana principal.
    """
    while widget.master is not None:
        widget = widget.master
    return widget


def get_palette(widget: tk.Misc) -> Palette:
    """
    Paleta vigente para quien la necesite al crearse (ej. un
    tk.Listbox nuevo dentro de un dialogo). 'light' si todavia no se
    aplico ningun tema - el caso de los tests, que arman paginas
    sueltas sobre un tk.Tk() propio sin pasar por ThemeManager.
    """
    root = find_app_root(widget)
    return getattr(root, "_flex_palette", LIGHT)


def paint_listbox(widget: tk.Listbox) -> None:
    """
    Para un tk.Listbox recien creado (ej. el buscador de productos de
    InvoiceDialog, un Toplevel nuevo en cada 'Nueva factura'): toma la
    paleta vigente de una y no depende de que ThemeManager pase a
    recorrer el arbol de nuevo.
    """
    p = get_palette(widget)
    widget.configure(
        background=p.entry_bg,
        foreground=p.fg,
        selectbackground=p.select_bg,
        selectforeground=p.select_fg,
        highlightbackground=p.border,
        highlightcolor=p.accent,
        # Plano, con un borde fino del color de los demas campos: el
        # relieve hundido de Tk desentonaba al lado de los CTkEntry.
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
    )


def paint_text(widget: tk.Text) -> None:
    """Mismo rol que paint_listbox(), para un tk.Text recien creado."""
    p = get_palette(widget)
    widget.configure(
        background=p.entry_bg,
        foreground=p.fg,
        insertbackground=p.fg,
        selectbackground=p.select_bg,
        selectforeground=p.select_fg,
        highlightbackground=p.border,
        highlightcolor=p.accent,
        # Plano, con un borde fino del color de los demas campos: el
        # relieve hundido de Tk desentonaba al lado de los CTkEntry.
        relief="flat",
        borderwidth=0,
        highlightthickness=1,
    )


def paint_window(widget: tk.Misc) -> None:
    """
    Para un tk.Toplevel recien creado (cualquiera de los dialogos de
    alta/edicion): sin esto, el fondo del dialogo se queda con el gris
    por defecto de Tk aunque todo lo de adentro (ttk) ya este oscuro,
    porque el propio Toplevel no es un widget ttk y ThemeManager solo
    repinta lo que ya existia al momento del cambio de tema.
    """
    if isinstance(widget, (ctk.CTk, ctk.CTkToplevel)):
        return  # CTk ya pinta su fondo y lo cambia solo con el modo
    p = get_palette(widget)
    widget.configure(background=p.bg)


def paint_canvas(widget: tk.Canvas) -> None:
    """Fondo de los paneles desplazables, que Canvas no toma de ttk."""
    widget.configure(background=get_palette(widget).bg, highlightthickness=0)


class ThemeManager:
    """
    Dueno del tema activo de toda la app: una sola instancia, vive en
    MainWindow (mismo rol que ExchangeRateService para las tasas).
    """

    def __init__(
        self, root: tk.Tk, *, initial: str = "light", font_scale: int = 100
    ) -> None:
        self._root = root
        self._style = ttk.Style(root)
        self._style.theme_use("clam")
        self._mode = "light"
        self._font_scale = 100
        self.set_font_scale(font_scale, repaint=False)
        self.apply(initial)

    @property
    def mode(self) -> str:
        return self._mode

    def toggle(self) -> str:
        self.apply("dark" if self._mode == "light" else "light")
        return self._mode

    @property
    def font_scale(self) -> int:
        return self._font_scale

    def set_font_scale(self, scale: int, *, repaint: bool = True) -> None:
        """Cambia las fuentes nombradas y recalcula los estilos dependientes."""
        self._font_scale = scale
        for name, base_size in (
            ("TkDefaultFont", 14),
            ("TkTextFont", 14),
            ("TkHeadingFont", 14),
            ("TkMenuFont", 14),
        ):
            tkfont.nametofont(name).configure(size=self._scaled(base_size))
        # Y las fuentes de los widgets CustomTkinter (ver ctk_font()).
        _rescale_ctk_fonts(self._root, scale)
        self._avisar_cambio_de_fuente(self._root)
        if repaint:
            self.apply(self._mode)

    def _avisar_cambio_de_fuente(self, widget: tk.Misc) -> None:
        """
        Casi todo se re-mide solo al cambiar la fuente. Lo que fijo un
        alto en pixeles al construirse (ver widgets.fixed_height_frame)
        no: si no se le avisa, tras Ctrl+ con un dialogo abierto el texto
        queda recortado adentro de un marco medido con la letra vieja.
        """
        avisar = getattr(widget, "on_font_scale_changed", None)
        if avisar is not None:
            avisar()
        for hijo in widget.winfo_children():
            self._avisar_cambio_de_fuente(hijo)

    def apply(self, mode: str) -> None:
        self._mode = "dark" if mode == "dark" else "light"
        palette = _PALETTES[self._mode]
        self._root._flex_palette = palette  # leido por get_palette()
        # Los widgets CTk eligen solos su color (claro, oscuro) segun el
        # modo global: basta con cambiarlo una vez.
        set_ctk_mode(self._mode)
        self._configurar_ttk(palette)
        self._retemar_widgets_clasicos(self._root, palette)

    def _scaled(self, size: int) -> int:
        return max(8, round(size * self._font_scale / 100))

    # ------------------------------------------------------------
    def _configurar_ttk(self, p: Palette) -> None:
        s = self._style
        s.configure(".", background=p.bg, foreground=p.fg, fieldbackground=p.entry_bg)
        s.configure("TFrame", background=p.bg)
        s.configure("TLabel", background=p.bg, foreground=p.fg)
        # Jerarquia tipografica minima: texto secundario (status de una
        # tabla, ayuda) y titulos de seccion, en vez de que todo compita
        # al mismo tamano/color de TLabel. El tamano sale de la fuente
        # nombrada TkDefaultFont (ver MainWindow._build_estilos: ya la
        # agranda a 12pt una sola vez para toda la app), asi que acá solo
        # se le suma el `+N` relativo, no un numero de punto fijo.
        s.configure("Muted.TLabel", background=p.bg, foreground=p.muted_fg)
        s.configure(
            "Heading.TLabel",
            background=p.bg,
            foreground=p.fg,
            font=("TkDefaultFont", self._scaled(15), "bold"),
        )
        # Negrita al tamano normal: etiquetas de un dato ("Nombre:",
        # "Tel:") al lado de su valor, sin agrandar la fila.
        s.configure(
            "Bold.TLabel",
            background=p.bg,
            foreground=p.fg,
            font=("TkDefaultFont", self._scaled(14), "bold"),
        )
        # Resultado de una accion, en texto y en el lugar donde se
        # trabaja, en vez de un messagebox modal. Para una pantalla que
        # se usa muchas veces seguidas (ver StockAdjustmentPage: cargar
        # el inventario producto por producto), un modal por ajuste es
        # un click de mas cada vez; el color hace el mismo trabajo de
        # avisar sin frenar a nadie.
        s.configure("Ok.TLabel", background=p.bg, foreground=p.success_bg)
        s.configure("Error.TLabel", background=p.bg, foreground=p.danger_bg)
        s.configure(
            "TLabelframe", background=p.bg, foreground=p.fg, bordercolor=p.border
        )
        s.configure("TLabelframe.Label", background=p.bg, foreground=p.fg)
        s.configure(
            "TButton",
            background=p.button_bg,
            foreground=p.fg,
            bordercolor=p.border,
            focuscolor=p.accent,
        )
        s.map(
            "TButton",
            background=[("disabled", p.bg), ("active", p.button_active_bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        # Variante para una accion positiva puntual (ej. "Crear
        # factura"): un estilo aparte en vez de tocar TButton, para no
        # pintar de verde todos los botones de la app.
        s.configure(
            "Success.TButton",
            background=p.success_bg,
            foreground=p.success_fg,
            bordercolor=p.border,
            focuscolor=p.accent,
        )
        s.map(
            "Success.TButton",
            background=[("disabled", p.bg), ("active", p.success_active_bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        # La misma variante, pero grande: para EL boton con el que
        # arranca una pantalla ("Iniciar factura"), que se busca con la
        # vista y no compite con los de al lado. Hereda el verde de
        # Success.TButton (ttk arma el estilo por puntos), solo cambia
        # la letra y el relleno.
        s.configure(
            "Big.Success.TButton",
            font=("TkDefaultFont", self._scaled(15), "bold"),
            padding=(44, 30),
        )
        s.map(
            "Big.Success.TButton",
            background=[("disabled", p.bg), ("active", p.success_active_bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        # Misma logica para una accion destructiva (ej. "Eliminar"):
        # rojo propio, sin tocar TButton.
        s.configure(
            "Danger.TButton",
            background=p.danger_bg,
            foreground=p.danger_fg,
            bordercolor=p.border,
            focuscolor=p.accent,
        )
        s.map(
            "Danger.TButton",
            background=[("disabled", p.bg), ("active", p.danger_active_bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        # Y para una accion de peso que no es ni exito ni borrado (ej.
        # "Cancelar factura": anula, no borra - Regla 4 del CLAUDE.md).
        s.configure(
            "Warning.TButton",
            background=p.warning_bg,
            foreground=p.warning_fg,
            bordercolor=p.border,
            focuscolor=p.accent,
        )
        s.map(
            "Warning.TButton",
            background=[("disabled", p.bg), ("active", p.warning_active_bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        # Y para una accion de edicion (ej. "Editar" en cualquier tabla de
        # listado): amarillo propio, distinto del naranja de Warning (que
        # ya significa "peso/precaucion" en otro lado, ej. bajo_stock en
        # ProductsPage) y del verde de Success (que significa "crear").
        s.configure(
            "Edit.TButton",
            background=p.edit_bg,
            foreground=p.edit_fg,
            bordercolor=p.border,
            focuscolor=p.accent,
        )
        s.map(
            "Edit.TButton",
            background=[("disabled", p.bg), ("active", p.edit_active_bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        # Tarjetas de tasa del encabezado (ver widgets.RateCard): tres
        # tamanos distintos a proposito -nombre chico, numero grande,
        # fecha chica- para que el ojo vaya primero al numero en vez de
        # leer una fila entera de texto al mismo tamano. Fondo 'surface'
        # (el de las tablas) para despegarlas del fondo de la ventana.
        s.configure(
            "Card.TFrame", background=p.surface, bordercolor=p.border, relief="solid"
        )
        s.configure(
            "CardHover.TFrame",
            background=p.surface,
            bordercolor=p.accent,
            relief="solid",
        )
        s.configure(
            "CardTitle.TLabel",
            background=p.surface,
            foreground=p.muted_fg,
            font=("TkDefaultFont", self._scaled(10), "bold"),
        )
        s.configure(
            "CardValue.TLabel",
            background=p.surface,
            foreground=p.fg,
            font=("TkDefaultFont", self._scaled(17), "bold"),
        )
        s.configure(
            "CardDate.TLabel",
            background=p.surface,
            foreground=p.muted_fg,
            font=("TkDefaultFont", self._scaled(10)),
        )
        # Texto normal sobre una tarjeta (ej. el panel de totales de
        # Nueva factura): mismo fondo 'surface' que Card.TFrame.
        s.configure("CardText.TLabel", background=p.surface, foreground=p.fg)
        # Variante destacada (tarjeta "Tasa propia", Modo avanzado): fondo
        # del color de acento con texto claro, para que se distinga de un
        # vistazo de las tasas oficiales que tiene al lado.
        s.configure(
            "CardAccent.TFrame",
            background=p.accent,
            bordercolor=p.accent,
            relief="solid",
        )
        s.configure(
            "CardAccentHover.TFrame",
            background=p.accent,
            bordercolor=p.fg,
            relief="solid",
        )
        for estilo, tamano, peso in (
            ("CardAccentTitle.TLabel", 10, "bold"),
            ("CardAccentValue.TLabel", 17, "bold"),
            ("CardAccentDate.TLabel", 10, "normal"),
        ):
            s.configure(
                estilo,
                background=p.accent,
                foreground=p.select_fg,
                font=("TkDefaultFont", self._scaled(tamano), peso),
            )
        s.configure(
            "TEntry",
            fieldbackground=p.entry_bg,
            foreground=p.fg,
            bordercolor=p.border,
            insertcolor=p.fg,
        )
        s.map(
            "TEntry",
            fieldbackground=[("disabled", p.bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        s.configure(
            "TCombobox",
            fieldbackground=p.entry_bg,
            background=p.button_bg,
            foreground=p.fg,
            arrowcolor=p.fg,
        )
        s.map(
            "TCombobox",
            fieldbackground=[("readonly", p.entry_bg), ("disabled", p.bg)],
            foreground=[("disabled", p.disabled_fg)],
        )
        self._root.option_add("*TCombobox*Listbox.background", p.entry_bg)
        self._root.option_add("*TCombobox*Listbox.foreground", p.fg)
        self._root.option_add("*TCombobox*Listbox.selectBackground", p.select_bg)
        self._root.option_add("*TCombobox*Listbox.selectForeground", p.select_fg)
        s.configure("TCheckbutton", background=p.bg, foreground=p.fg)
        s.map("TCheckbutton", background=[("active", p.bg)])
        s.configure("TRadiobutton", background=p.bg, foreground=p.fg)
        s.map("TRadiobutton", background=[("active", p.bg)])
        s.configure(
            "TNotebook",
            background=p.bg,
            bordercolor=p.border,
            lightcolor=p.bg,
            darkcolor=p.bg,
            tabmargins=(0, 4, 0, 0),
        )
        s.configure(
            "TNotebook.Tab",
            background=p.button_bg,
            foreground=p.fg,
            padding=(16, 8),
            bordercolor=p.border,
            lightcolor=p.button_bg,
            darkcolor=p.button_bg,
        )
        s.map(
            "TNotebook.Tab",
            background=[("selected", p.accent)],
            foreground=[("selected", p.select_fg)],
        )
        s.configure(
            "Treeview",
            background=p.surface,
            fieldbackground=p.surface,
            foreground=p.fg,
            bordercolor=p.surface,
            lightcolor=p.surface,
            darkcolor=p.surface,
            borderwidth=0,
            # rowheight TIENE que fijarse aca, no en MainWindow._build_
            # estilos: theme_use('clam') (ver ThemeManager.__init__,
            # corre antes de la primera llamada a esto) resetea
            # cualquier configure() de estilo hecho antes de cambiar de
            # tema, asi que un rowheight puesto afuera se perdia en
            # silencio - las filas quedaban mas bajas que el texto de
            # 14pt (self.ui/main_window.py::_build_estilos) y se veian
            # cortadas por abajo (bug real: la coma decimal de un monto
            # se leia como punto, "2.50$" en vez de "2,50$").
            # metrics('linespace') mide la fuente DE VERDAD en vez de un
            # numero fijo a ojo, asi que sigue funcionando si el tamano
            # de fuente cambia. +18 (no +10) porque ademas de "no se
            # corte" se pidio que las filas se vean mas espaciosas.
            rowheight=tkfont.nametofont("TkDefaultFont").metrics("linespace") + 18,
        )
        s.map(
            "Treeview",
            background=[("selected", p.select_bg)],
            foreground=[("selected", p.select_fg)],
        )
        # Sin el marco 3D de 'clam': la tabla vive adentro de una tarjeta
        # CTk de bordes redondeados (ver kit.table_card), que ya la
        # enmarca. El layout queda solo con el area de filas.
        s.layout("Treeview", [("Treeview.treearea", {"sticky": "nswe"})])
        s.configure(
            "Treeview.Heading",
            background=p.heading_bg,
            foreground=p.heading_fg,
            # Linea fina del color de los bordes: separa el encabezado de
            # los datos (ttk no permite un borde solo abajo).
            bordercolor=p.border,
            lightcolor=p.heading_bg,
            darkcolor=p.heading_bg,
            relief="flat",
            padding=(8, 6),
            font=(_heading_family(), self._scaled(12), "bold"),
        )
        s.map(
            "Treeview.Heading",
            background=[("active", p.button_active_bg)],
            foreground=[("active", p.fg)],
        )
        s.configure(
            "TSpinbox",
            fieldbackground=p.entry_bg,
            background=p.button_bg,
            foreground=p.fg,
            arrowcolor=p.fg,
            bordercolor=p.border,
            lightcolor=p.entry_bg,
            darkcolor=p.entry_bg,
            insertcolor=p.fg,
            padding=(6, 4),
        )
        s.configure(
            "TScrollbar",
            background=p.button_bg,
            troughcolor=p.bg,
            bordercolor=p.border,
            arrowcolor=p.fg,
        )
        s.configure("TSeparator", background=p.border)
        # LoadingDialog (ver widgets.py): barra indeterminada, mismo azul
        # que accent/select_bg para que se lea como "en curso" en los dos
        # temas, sin inventar un color nuevo en la paleta.
        s.configure(
            "TProgressbar",
            background=p.accent,
            troughcolor=p.entry_bg,
            bordercolor=p.border,
        )

    def _retemar_widgets_clasicos(self, widget: tk.Misc, p: Palette) -> None:
        if isinstance(widget, ctk.CTkCanvas):
            # El lienzo con el que CTk dibuja cada boton/marco redondeado:
            # su fondo lo maneja CTk segun el modo. Pintarlo de 'bg' dejaba
            # las esquinas de los botones de la barra lateral en claro.
            return
        # Widgets propios que se pintan solos (ver widgets.TextoVacio).
        repintar = getattr(widget, "_flex_repintar", None)
        if repintar is not None:
            try:
                repintar()
            except tk.TclError:
                pass
        clase = widget.winfo_class()
        if clase in _CLASSIC_CLASSES:
            try:
                self._pintar_widget_clasico(widget, clase, p)
            except tk.TclError:
                pass  # widget a mitad de destruirse durante el recorrido

        for hijo in widget.winfo_children():
            self._retemar_widgets_clasicos(hijo, p)

    @staticmethod
    def _pintar_widget_clasico(widget: tk.Misc, clase: str, p: Palette) -> None:
        if clase == "Menu":
            widget.configure(
                background=p.surface,
                foreground=p.fg,
                activebackground=p.select_bg,
                activeforeground=p.select_fg,
            )
        elif clase == "Listbox":
            paint_listbox(widget)
        elif clase == "Text":
            paint_text(widget)
        elif clase == "Canvas":
            paint_canvas(widget)
        elif clase == "Treeview":
            # Los colores de las tablas viven en ttk.Style; lo unico que no
            # son los tags de fila que define widgets.SearchableTable.
            widget.tag_configure("franja", background=p.stripe_bg)
            widget.tag_configure("hover_row", background=p.hover_bg)
            widget.tag_configure("separator", foreground=p.muted_fg)
            # Titulo resaltado de SearchableTable (ver set_highlighted_column).
            repintar = getattr(widget, "_flex_repintar_resaltado", None)
            if repintar is not None:
                repintar()
        else:  # Toplevel o Tk
            paint_window(widget)
