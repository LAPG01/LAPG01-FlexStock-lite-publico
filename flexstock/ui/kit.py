"""
Widgets CustomTkinter con la misma "forma" que los ttk que reemplazan.

Las paginas de ui/ se escribieron contra ttk: `style="Success.TButton"`,
`width` en CARACTERES, `boton.state(["disabled"])`, `combo.current()`,
el evento `<<ComboboxSelected>>`, `ttk.LabelFrame(text=..., padding=...)`.
CustomTkinter dibuja todo con esquinas redondeadas y colores por modo,
pero su interfaz es otra: colores sueltos en vez de estilos, ancho en
pixeles, `configure(state=...)`, sin LabelFrame ni `current()`.

Este modulo es el traductor: cada clase ES un widget CTk (hereda de el,
se ve y se comporta como CTk) y ademas entiende lo de ttk que la app usa.
Asi migrar una pantalla es cambiar `ttk.Button(` por `kit.Button(` y la
logica de la pagina no se toca. Lo que no tiene equivalente en CTk se
queda en ttk/tk clasico, con el estilo de theme.py: la tabla
(ttk.Treeview), el Spinbox, el Notebook del cierre de caja y las listas
y textos (tk.Listbox, tk.Text).

Los colores salen de la paleta de theme.py como tuplas (claro, oscuro):
CTk elige solo cuando cambia el modo, sin recorrer el arbol de widgets.
Las letras son fuentes compartidas por rol (theme.ctk_font), que
Ctrl+/Ctrl- reescala de una sola vez.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from typing import Any

import customtkinter as ctk

from . import icons
from .theme import ctk_font, find_app_root, install_ctk_theme, pair, set_ctk_mode

# "system" haria que CTk pregunte el tema del escritorio cada 30 ms (en
# Linux, lanzando un proceso de gsettings cada vez). La app tiene su
# propio boton claro/oscuro: el modo lo fija siempre ThemeManager.
set_ctk_mode("light")
install_ctk_theme()


# ------------------------------------------------------------
# utilidades
# ------------------------------------------------------------
def _chars_to_px(master: tk.Misc, chars: int | None, *, extra: int = 20) -> int:
    """Ancho ttk (en caracteres de la letra normal) a pixeles CTk."""
    if not chars:
        return 0
    return int(chars) * ctk_font(master, "body").measure("0") + extra


def _line_height(master: tk.Misc, role: str = "body") -> int:
    return ctk_font(master, role).metrics("linespace")


def _pad4(padding: Any) -> tuple[int, int, int, int]:
    """Padding ttk (n, (x, y) o (izq, arriba, der, abajo)) a 4 valores."""
    if padding is None or padding == "":
        return (0, 0, 0, 0)
    if isinstance(padding, (int, float)):
        p = int(padding)
        return (p, p, p, p)
    valores = [int(v) for v in padding]
    if len(valores) == 1:
        return (valores[0],) * 4
    if len(valores) == 2:
        return (valores[0], valores[1], valores[0], valores[1])
    if len(valores) == 3:
        return (valores[0], valores[1], valores[2], valores[1])
    return tuple(valores[:4])  # type: ignore[return-value]


class _ItemAccess:
    """`widget["opcion"]` como en tk (CTk solo tiene cget/configure)."""

    def __getitem__(self, key: str) -> Any:
        return self.cget(key)

    def __setitem__(self, key: str, value: Any) -> None:
        self.configure(**{key: value})


class _TtkState:
    """`state()` / `instate()` de ttk sobre el `state=` de CTk."""

    def state(self, spec: list[str] | tuple[str, ...] | None = None) -> tuple:
        if spec is None:
            return ("disabled",) if str(self.cget("state")) == "disabled" else ()
        for item in spec:
            if item == "disabled":
                self.configure(state="disabled")
            elif item == "!disabled":
                self.configure(state="normal")
        return ()

    def instate(self, spec: list[str] | tuple[str, ...]) -> bool:
        deshabilitado = str(self.cget("state")) == "disabled"
        for item in spec:
            if item == "disabled" and not deshabilitado:
                return False
            if item == "!disabled" and deshabilitado:
                return False
        return True


class _Enfocable:
    """
    Tab llega a este widget y una tecla lo activa: sin esto, CTk dibuja
    botones, casillas y radios que el teclado no alcanza, y recorrer un
    formulario de corrido se cortaba en el primer boton. Mientras tiene el
    foco se ve un borde de acento (lo dibuja _marcar_foco de cada clase).
    """

    def _hacer_enfocable(self, teclas: tuple[str, ...]) -> None:
        lienzo = self._canvas
        lienzo.configure(takefocus=1)
        lienzo.bind("<FocusIn>", lambda _e: self._marcar_foco(True), add="+")
        lienzo.bind("<FocusOut>", lambda _e: self._marcar_foco(False), add="+")
        for tecla in teclas:
            lienzo.bind(tecla, self._activar_con_teclado)

    def enter_tambien_activa(self) -> None:
        """
        Enter hace lo mismo que Espacio (marca/desmarca, elige la opcion).
        Opt-in: en un formulario donde Enter guarda, la casilla no se lo
        puede quedar; en uno donde Enter nunca confirma (la factura), es lo
        natural para quien recorre todo con Tab y Enter.
        """
        for tecla in ("<Return>", "<KP_Enter>"):
            self._canvas.bind(tecla, self._activar_con_teclado)

    def _activar_con_teclado(self, _evento: object = None) -> str:
        if str(self.cget("state")) != "disabled":
            self.invoke()
        return "break"  # el Enter de un boton no debe seguir hasta el dialogo

    def _marcar_foco(self, con_foco: bool) -> None:  # pragma: no cover
        raise NotImplementedError


# ------------------------------------------------------------
# botones
# ------------------------------------------------------------
# estilo ttk -> (fondo, hover, texto) de la paleta.
_BUTTON_COLORS: dict[str, tuple[str, str, str]] = {
    "TButton": ("button_bg", "button_active_bg", "fg"),
    "Success.TButton": ("success_bg", "success_active_bg", "success_fg"),
    "Big.Success.TButton": ("success_bg", "success_active_bg", "success_fg"),
    "Danger.TButton": ("danger_bg", "danger_active_bg", "danger_fg"),
    # De contorno: acciones destructivas que van al lado de otras de uso
    # diario (Editar). Un rojo solido ahi invita al clic equivocado.
    "Danger.Outline.TButton": ("surface", "danger_soft_bg", "danger_bg"),
    "Warning.TButton": ("warning_bg", "warning_active_bg", "warning_fg"),
    "Edit.TButton": ("edit_bg", "edit_active_bg", "edit_fg"),
    "Accent.TButton": ("accent", "accent_hover", "select_fg"),
}
# Estilos de contorno -> color (de la paleta) del borde.
_BUTTON_BORDERS: dict[str, str] = {"Danger.Outline.TButton": "danger_bg"}


class Button(_ItemAccess, _TtkState, _Enfocable, ctk.CTkButton):
    """
    Boton redondeado. `style` acepta los mismos nombres que antes
    (Success/Danger/Warning/Edit, Big.Success, y Accent para el azul) y
    `width` sigue en caracteres. Sin `width`, el boton mide lo que su
    texto, con un minimo para que "OK" no quede de 30 px.

    `icon` es el nombre de un PNG de assets/icons/ (ver ui/icons.py): va
    a la izquierda del texto y del MISMO color que el texto, asi cambia
    solo con el estilo, el estado y el modo claro/oscuro.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        command: Callable[[], Any] | None = None,
        style: str | None = None,
        width: int | None = None,
        padding: Any = None,
        state: str = "normal",
        icon: str | None = None,
        tab: bool = True,
        **kwargs: Any,
    ) -> None:
        # tab=False: boton secundario que Tab se salta (sigue funcionando con
        # el mouse), para no meter paradas de mas entre dos campos seguidos.
        self._con_tab = tab
        self._style_name = style or "TButton"
        self._icon_name = icon
        grande = self._style_name.startswith("Big.")
        rol = "big" if grande else "body"
        alto = _line_height(master, rol) + (40 if grande else 14)
        if padding is not None:
            alto = max(alto, _line_height(master, rol) + 2 * _pad4(padding)[1])
        ancho = (
            _chars_to_px(master, width, extra=12) if width else (260 if grande else 88)
        )
        super().__init__(
            master,
            text=text,
            command=command,
            width=ancho,
            height=alto,
            font=ctk_font(master, rol),
            state=state,
            corner_radius=12 if grande else 8,
            **kwargs,
        )
        # Tab llega al boton; espacio y Enter lo activan.
        self._hacer_enfocable(("<space>", "<Return>", "<KP_Enter>"))
        self._apply_style()  # despues: decide si el boton es parada de Tab

    def _marcar_foco(self, con_foco: bool) -> None:
        if con_foco:
            ctk.CTkButton.configure(self, border_width=2, border_color=pair("accent"))
        else:
            self._apply_style()

    def _apply_style(self) -> None:
        fondo, hover, texto = _BUTTON_COLORS.get(
            self._style_name, _BUTTON_COLORS["TButton"]
        )
        borde = _BUTTON_BORDERS.get(self._style_name)
        if str(self.cget("state")) == "disabled":
            # Apagado se ve apagado: gris, no un verde desteñido que
            # parece clickeable ("Confirmar factura" sin líneas).
            fondo = hover = "button_bg"
            borde = "disabled_fg" if borde else None
        ctk.CTkButton.configure(
            self,
            fg_color=pair(fondo),
            hover_color=pair(hover),
            text_color=pair(texto),
            text_color_disabled=pair("disabled_fg"),
            border_width=2 if borde else 0,
            border_color=pair(borde or "border"),
        )
        # Un boton apagado no es una parada de Tab.
        if hasattr(self, "_canvas"):
            self._canvas.configure(
                takefocus=0
                if str(self.cget("state")) == "disabled" or not self._con_tab
                else 1
            )
        if self._icon_name:
            deshabilitado = str(self.cget("state")) == "disabled"
            color = pair("disabled_fg") if deshabilitado else pair(texto)
            ctk.CTkButton.configure(
                self,
                image=icons.icon(self, self._icon_name, color, size=18),
                compound="left",
            )

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        estilo = kwargs.pop("style", None)
        if estilo is not None:
            self._style_name = estilo
        if "width" in kwargs and kwargs["width"] is not None:
            kwargs["width"] = _chars_to_px(self, kwargs["width"], extra=12)
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)
        # Los colores dependen del estilo Y del estado (ver _apply_style).
        if estilo is not None or "state" in kwargs:
            self._apply_style()

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "style":
            return self._style_name
        return super().cget(attribute_name)


# ------------------------------------------------------------
# etiquetas
# ------------------------------------------------------------
# estilo ttk -> (color de texto, rol de fuente).
_LABEL_STYLES: dict[str, tuple[str, str]] = {
    "TLabel": ("fg", "body"),
    "Muted.TLabel": ("muted_fg", "body"),
    "Heading.TLabel": ("fg", "heading"),
    "Title.TLabel": ("fg", "title"),
    "Bold.TLabel": ("fg", "bold"),
    "Ok.TLabel": ("success_bg", "body"),
    "Error.TLabel": ("danger_bg", "body"),
    "Section.TLabel": ("fg", "heading"),
    "Small.TLabel": ("muted_fg", "small"),
    "Notice.TLabel": ("notice_fg", "body"),
    "CardTitle.TLabel": ("muted_fg", "small_bold"),
    "CardValue.TLabel": ("fg", "card_value"),
    "Total.TLabel": ("fg", "total"),
    "CardDate.TLabel": ("muted_fg", "small"),
    "CardText.TLabel": ("fg", "body"),
    "CardAccentTitle.TLabel": ("select_fg", "small_bold"),
    "CardAccentValue.TLabel": ("select_fg", "card_value"),
    "CardAccentDate.TLabel": ("select_fg", "small"),
}
_ACCENT_TEXT = ("#ffffff", "#ffffff")  # texto sobre la tarjeta de acento


class Label(_ItemAccess, ctk.CTkLabel):
    """
    Texto. `style` elige color y tamano (Muted, Heading, Ok, Error,
    Card*...). Alineado a la izquierda como ttk -CTk centra por defecto,
    y un label estirado con fill="x" quedaba con el texto en el medio.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        textvariable: tk.Variable | None = None,
        style: str | None = None,
        width: int | None = None,
        anchor: str = "w",
        justify: str = "left",
        wraplength: int = 0,
        **kwargs: Any,
    ) -> None:
        self._style_name = style or "TLabel"
        color, rol = self._colores()
        super().__init__(
            master,
            text=text,
            textvariable=textvariable,
            width=0,
            height=0,
            anchor=anchor,
            justify=justify,
            wraplength=wraplength,
            text_color=color,
            font=ctk_font(master, rol),
            **kwargs,
        )
        if width:
            # En ttk el ancho de un Label es FIJO (lo que no entra se
            # corta); el `width` de CTk es solo un minimo. El tk.Label de
            # adentro si lo respeta en caracteres, como antes.
            self._label.configure(width=int(width))

    def _colores(self) -> tuple[tuple[str, str], str]:
        color, rol = _LABEL_STYLES.get(self._style_name, _LABEL_STYLES["TLabel"])
        texto = _ACCENT_TEXT if "Accent" in self._style_name else pair(color)
        return texto, rol

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        estilo = kwargs.pop("style", None)
        if estilo is not None:
            self._style_name = estilo
            color, rol = self._colores()
            kwargs["text_color"] = color
            kwargs["font"] = ctk_font(self, rol)
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "style":
            return self._style_name
        return super().cget(attribute_name)

    def bind(self, sequence: str | None = None, command: Any = None, add: Any = True):
        # <Configure> va al marco de afuera: CTk lo pondria en el texto de
        # adentro, que mide lo que el texto (no lo que se estiro el
        # label), y wrap_to_width() se achicaria en cada vuelta.
        if sequence == "<Configure>":
            return tk.Misc.bind(self, sequence, command, add="+")
        return super().bind(sequence, command, add=True)


# ------------------------------------------------------------
# contenedores
# ------------------------------------------------------------
# estilo ttk -> (fondo, borde, radio). None = transparente.
_FRAME_STYLES: dict[str, tuple[str | None, str | None, int]] = {
    "TFrame": (None, None, 0),
    "Card.TFrame": ("surface", "border", 12),
    "CardHover.TFrame": ("surface", "accent", 12),
    "CardAccent.TFrame": ("accent", "accent", 12),
    "CardAccentHover.TFrame": ("accent", "accent_hover", 12),
    "Section.TFrame": ("surface", "border", 12),
    # Marco de una tabla o lista: igual a una tarjeta, otro nombre para
    # no confundirla con un panel de datos (ej. los totales).
    "Table.TFrame": ("surface", "border", 8),
    "Notice.TFrame": ("notice_bg", "notice_border", 8),
}


class Frame(_ItemAccess, ctk.CTkFrame):
    """
    Contenedor. Sin `style` es transparente (toma el fondo de quien lo
    contiene); "Card.TFrame" es una tarjeta redondeada con borde.
    `padding` funciona como en ttk: margen interior.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        padding: Any = None,
        style: str | None = None,
        width: int = 0,
        height: int = 0,
        **kwargs: Any,
    ) -> None:
        self._style_name = style or "TFrame"
        fondo, borde, radio = self._colores()
        super().__init__(
            master,
            width=width,
            height=height,
            fg_color=fondo,
            border_color=borde or pair("border"),
            border_width=1 if borde else 0,
            corner_radius=radio,
            **kwargs,
        )
        self._set_padding(padding)

    def _colores(self):
        fondo, borde, radio = _FRAME_STYLES.get(
            self._style_name, _FRAME_STYLES["TFrame"]
        )
        return (
            pair(fondo) if fondo else "transparent",
            pair(borde) if borde else None,
            radio,
        )

    def _set_padding(self, padding: Any) -> None:
        izq, arriba, der, abajo = _pad4(padding)
        if not any((izq, arriba, der, abajo)):
            return
        # Un tk.Frame solo tiene padx/pady simetricos: se usa el
        # promedio de cada eje. El lienzo de CTk (fondo y bordes
        # redondeados) se estira sobre ese margen con bordermode="ignore";
        # si no, el padding se veria del color del padre.
        tk.Frame.configure(self, padx=(izq + der) // 2, pady=(arriba + abajo) // 2)
        self._canvas.place_configure(bordermode="ignore")

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        estilo = kwargs.pop("style", None)
        if estilo is not None:
            self._style_name = estilo
            fondo, borde, radio = self._colores()
            kwargs.update(fg_color=fondo, corner_radius=radio)
            if borde:
                kwargs.update(border_color=borde, border_width=1)
        padding = kwargs.pop("padding", None)
        if padding is not None:
            self._set_padding(padding)
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "style":
            return self._style_name
        return super().cget(attribute_name)


class LabelFrame(Frame):
    """
    Seccion con titulo, el reemplazo de ttk.LabelFrame (CTk no tiene).

    Es en realidad DOS marcos: una tarjeta de afuera (con el titulo
    arriba) y este, transparente, adentro. Los hijos se crean en este
    -igual que con ttk- y todo lo que posiciona la seccion entera (pack,
    grid, place y sus *_forget) se le pasa a la tarjeta de afuera.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        padding: Any = 8,
        title_style: str = "Section.TLabel",
        **kwargs: Any,
    ) -> None:
        izq, arriba, der, abajo = _pad4(padding)
        self._en_destroy = False
        self._outer = _SectionCard(master, style="Section.TFrame")
        self._outer._inner = self
        self._title = Label(self._outer, text=text, style=title_style)
        self._title.pack(side="top", anchor="w", padx=(izq + 4, der), pady=(10, 0))
        super().__init__(self._outer, **kwargs)
        ctk.CTkFrame.pack(
            self,
            side="top",
            fill="both",
            expand=True,
            padx=(izq + 4, der + 4),
            pady=(max(arriba // 2, 4), abajo + 4),
        )

    # El titulo
    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "text" in kwargs:
            self._title.configure(text=kwargs.pop("text"))
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "text":
            return self._title.cget("text")
        return super().cget(attribute_name)

    # Posicionar la seccion = posicionar la tarjeta de afuera.
    def pack(self, **kwargs: Any) -> None:
        self._outer.pack(**_outer_refs(kwargs))

    pack_configure = pack

    def grid(self, **kwargs: Any) -> None:
        self._outer.grid(**kwargs)

    grid_configure = grid

    def place(self, **kwargs: Any) -> None:
        self._outer.place(**kwargs)

    place_configure = place

    def tkraise(self, aboveThis: tk.Misc | None = None) -> None:
        self._outer.tkraise(aboveThis)

    lift = tkraise

    def pack_forget(self) -> None:
        self._outer.pack_forget()

    def grid_forget(self) -> None:
        self._outer.grid_forget()

    def grid_remove(self) -> None:
        self._outer.grid_remove()

    def place_forget(self) -> None:
        self._outer.place_forget()

    def pack_info(self) -> dict:
        return self._outer.pack_info()

    def grid_info(self) -> dict:
        return self._outer.grid_info()

    def destroy(self) -> None:
        if self._en_destroy:
            super().destroy()
        else:
            self._outer.destroy()  # me destruye a mi tambien: soy su hijo


def _outer_refs(kwargs: dict) -> dict:
    """
    `pack(before=seccion)` tiene que apuntar a la tarjeta de afuera de
    una seccion: con la de adentro, Tk empacaba el widget DENTRO de la
    otra seccion (before/after tambien eligen el contenedor).
    """
    for clave in ("before", "after", "in_"):
        destino = kwargs.get(clave)
        if isinstance(destino, LabelFrame):
            kwargs[clave] = destino._outer
    return kwargs


class _SectionCard(Frame):
    """La tarjeta de afuera de un LabelFrame."""

    _inner: LabelFrame | None = None

    def destroy(self) -> None:
        if self._inner is not None:
            self._inner._en_destroy = True
        super().destroy()


# ------------------------------------------------------------
# campos
# ------------------------------------------------------------
class _InnerEntryBind:
    """
    Los bindings de un campo CTk viven en el tk.Entry de adentro. Sin
    `command`, ttk devuelve lo que esta atado (o ""); CTk devolvia None.
    """

    def bind(self, sequence: str | None = None, command: Any = None, add: Any = True):
        if command is None:
            return self._entry.bind(sequence)
        return super().bind(sequence, command, add=True)


class Entry(_ItemAccess, _InnerEntryBind, _TtkState, ctk.CTkEntry):
    """Campo de texto. `width` en caracteres, como ttk.Entry (20 por defecto)."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        textvariable: tk.Variable | None = None,
        width: int | None = 20,
        state: str = "normal",
        style: str | None = None,
        **kwargs: Any,
    ) -> None:
        self._style_name = style or "TEntry"
        super().__init__(
            master,
            textvariable=textvariable,
            width=_chars_to_px(master, width or 20),
            height=_line_height(master) + 14,
            font=ctk_font(master, "body"),
            state=state,
            **kwargs,
        )
        # Foco visible: el borde se pinta del color de acento mientras se
        # escribe, como en cualquier formulario moderno.
        self._borde_reposo = pair("border")
        self._entry.bind("<FocusIn>", self._con_foco, add="+")
        self._entry.bind("<FocusOut>", self._sin_foco, add="+")

    def _con_foco(self, _evento: object = None) -> None:
        ctk.CTkEntry.configure(self, border_color=pair("accent"))

    def _sin_foco(self, _evento: object = None) -> None:
        ctk.CTkEntry.configure(self, border_color=self._borde_reposo)

    def resaltar(self) -> None:
        """
        Borde de acento fijo (el campo "principal" de un formulario, como
        el Precio Detal): se distingue aunque este vacio y sin parecer un
        boton, que era lo que pasaba con un fondo de color.
        """
        self._borde_reposo = pair("accent")
        ctk.CTkEntry.configure(self, border_color=self._borde_reposo, border_width=2)

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        kwargs.pop("style", None)
        if "width" in kwargs and kwargs["width"] is not None:
            kwargs["width"] = _chars_to_px(self, kwargs["width"])
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "style":
            return self._style_name
        return super().cget(attribute_name)


class Combobox(_ItemAccess, _InnerEntryBind, _TtkState, ctk.CTkComboBox):
    """
    Combo con la interfaz de ttk.Combobox: `textvariable`, `values`,
    `current()` y el evento `<<ComboboxSelected>>` (que CTk no tiene:
    avisa con un `command`, que aca se reparte a quien se haya atado a
    ese evento). En "readonly", un click en cualquier parte del campo
    abre la lista, no solo en la flecha.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        values: Any = (),
        textvariable: tk.Variable | None = None,
        state: str = "normal",
        width: int | None = 20,
        **kwargs: Any,
    ) -> None:
        self._al_elegir: list[Callable[[Any], Any]] = []
        super().__init__(
            master,
            values=[str(v) for v in values],
            variable=textvariable,
            state=state,
            width=_chars_to_px(master, width or 20, extra=48),
            height=_line_height(master) + 14,
            font=ctk_font(master, "body"),
            dropdown_font=ctk_font(master, "body"),
            command=self._elegido,
            **kwargs,
        )
        if textvariable is None:
            # CTk muestra el primer valor de entrada; ttk arranca vacio.
            self.set("")
        self._entry.bind("<Button-1>", self._click_en_campo, add="+")
        # Con el teclado: se ve donde esta el cursor (borde de acento), las
        # flechas cambian la opcion y Espacio / F4 / Alt+Abajo despliegan la
        # lista (ver _flecha y _desplegar_con_teclado).
        self._borde_reposo = self.cget("border_color")
        self._entry.bind("<FocusIn>", lambda _e: self._marcar_foco(True), add="+")
        self._entry.bind("<FocusOut>", lambda _e: self._marcar_foco(False), add="+")
        self._entry.bind("<Up>", lambda _e: self._flecha(-1), add="+")
        self._entry.bind("<Down>", lambda _e: self._flecha(+1), add="+")
        for tecla in ("<space>", "<F4>", "<Alt-Down>"):
            self._entry.bind(tecla, self._desplegar_con_teclado, add="+")

    def _marcar_foco(self, con_foco: bool) -> None:
        ctk.CTkComboBox.configure(
            self, border_color=pair("accent") if con_foco else self._borde_reposo
        )

    def _flecha(self, delta: int) -> str | None:
        """Arriba / Abajo pasan a la opcion anterior / siguiente sin abrir la
        lista (solo en un combo de eleccion; en uno editable no se toca)."""
        if str(self.cget("state")) != "readonly":
            return None
        valores = self.cget("values")
        if not valores:
            return "break"
        actual = self.current()
        nuevo = 0 if actual < 0 else max(0, min(len(valores) - 1, actual + delta))
        if nuevo != actual:
            self.set(valores[nuevo])
            self._elegido(valores[nuevo])
        return "break"

    def _desplegar_con_teclado(self, _evento: object = None) -> str | None:
        if str(self.cget("state")) != "readonly" or not self.cget("values"):
            return None
        self._open_dropdown_menu()
        return "break"

    def enter_pasa_al_siguiente(self) -> None:
        """Enter acepta la opcion y salta al campo que sigue en el recorrido de
        Tab. Es opt-in: en un formulario donde Enter guarda, el combo no lo
        debe tragar."""

        def _siguiente(_evento: object) -> str:
            self._entry.tk_focusNext().focus_set()
            return "break"

        for tecla in ("<Return>", "<KP_Enter>"):
            self._entry.bind(tecla, _siguiente, add="+")

    def _click_en_campo(self, _evento: object = None) -> None:
        if str(self.cget("state")) == "readonly" and self.cget("values"):
            self._open_dropdown_menu()

    def _elegido(self, _valor: str) -> None:
        evento = tk.Event()
        evento.widget = self
        for callback in list(self._al_elegir):
            callback(evento)

    def bind(self, sequence: str | None = None, command: Any = None, add: Any = True):
        if sequence == "<<ComboboxSelected>>":
            if command is not None:
                self._al_elegir.append(command)
            return None
        return super().bind(sequence, command, add)

    def current(self, index: int | None = None) -> int:
        valores = list(self.cget("values") or ())
        if index is None:
            try:
                return valores.index(self.get())
            except ValueError:
                return -1
        self.set(valores[index])
        return index

    def configure(self, require_redraw: bool = False, **kwargs: Any) -> None:
        if "values" in kwargs:
            kwargs["values"] = [str(v) for v in kwargs["values"]]
        if "textvariable" in kwargs:
            kwargs["variable"] = kwargs.pop("textvariable")
        if "width" in kwargs and kwargs["width"] is not None:
            kwargs["width"] = _chars_to_px(self, kwargs["width"], extra=48)
        if kwargs or require_redraw:
            super().configure(require_redraw=require_redraw, **kwargs)

    def cget(self, attribute_name: str) -> Any:
        if attribute_name == "values":
            return tuple(super().cget("values") or ())
        return super().cget(attribute_name)

    def __setitem__(self, key: str, value: Any) -> None:
        self.configure(**{key: value})

    def __getitem__(self, key: str) -> Any:
        return self.cget(key)


class Checkbutton(_ItemAccess, _TtkState, _Enfocable, ctk.CTkCheckBox):
    """Casilla. `invoke()` la alterna y llama a `command`, como en ttk."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        variable: tk.Variable | None = None,
        command: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text,
            variable=variable,
            command=command,
            font=ctk_font(master, "body"),
            checkbox_width=22,
            checkbox_height=22,
            **kwargs,
        )
        self._borde_reposo = self.cget("border_color")
        # Espacio la alterna; Enter sigue siendo el del formulario (guardar).
        self._hacer_enfocable(("<space>",))

    def _marcar_foco(self, con_foco: bool) -> None:
        ctk.CTkCheckBox.configure(
            self, border_color=pair("accent") if con_foco else self._borde_reposo
        )

    def invoke(self) -> None:
        if str(self.cget("state")) != "disabled":
            self.toggle()


class Radiobutton(_ItemAccess, _TtkState, _Enfocable, ctk.CTkRadioButton):
    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str = "",
        variable: tk.Variable | None = None,
        value: Any = None,
        command: Callable[[], Any] | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(
            master,
            text=text,
            variable=variable,
            value=value,
            command=command,
            font=ctk_font(master, "body"),
            radiobutton_width=20,
            radiobutton_height=20,
            **kwargs,
        )
        self._borde_reposo = self.cget("border_color")
        self._hacer_enfocable(("<space>",))
        # Flechas: pasan a la opcion vecina del mismo grupo y la eligen.
        for tecla in ("<Left>", "<Up>"):
            self._canvas.bind(tecla, lambda _e: self._mover_en_grupo(-1))
        for tecla in ("<Right>", "<Down>"):
            self._canvas.bind(tecla, lambda _e: self._mover_en_grupo(+1))

    def _mover_en_grupo(self, delta: int) -> str:
        grupo = [
            w
            for w in self.master.winfo_children()
            if isinstance(w, Radiobutton) and w._variable is self._variable
        ]
        destino = grupo[max(0, min(len(grupo) - 1, grupo.index(self) + delta))]
        if destino is not self:
            destino.invoke()
            destino._canvas.focus_set()
        return "break"

    def _marcar_foco(self, con_foco: bool) -> None:
        ctk.CTkRadioButton.configure(
            self, border_color=pair("accent") if con_foco else self._borde_reposo
        )


class Switch(_ItemAccess, _Enfocable, ctk.CTkSwitch):
    """
    Interruptor (Modo oscuro, Modo avanzado...). Con Tab y Espacio como las
    casillas; `invoke()` lo alterna.
    """

    def __init__(self, master: tk.Misc, **kwargs: Any) -> None:
        kwargs.setdefault("font", ctk_font(master, "body"))
        kwargs.setdefault("progress_color", pair("accent"))
        kwargs.setdefault("switch_width", 48)
        kwargs.setdefault("switch_height", 24)
        super().__init__(master, **kwargs)
        self._borde_reposo = (self.cget("border_width"), self.cget("border_color"))
        self._hacer_enfocable(("<space>",))

    def invoke(self) -> None:
        if str(self.cget("state")) != "disabled":
            self.toggle()

    def _marcar_foco(self, con_foco: bool) -> None:
        if con_foco:
            ctk.CTkSwitch.configure(self, border_width=2, border_color=pair("accent"))
        else:
            ancho, color = self._borde_reposo
            ctk.CTkSwitch.configure(self, border_width=ancho, border_color=color)


class Scrollbar(_ItemAccess, ctk.CTkScrollbar):
    """Barra fina y redondeada; `orient` como en ttk."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        orient: str = "vertical",
        command: Callable[..., Any] | None = None,
        **kwargs: Any,
    ) -> None:
        # El largo por defecto de CTk (200 px) se volvia un ALTO MINIMO
        # para toda tabla o lista con barra: la barra se estira sola con
        # fill/sticky, asi que basta con un largo chico.
        if orient == "vertical":
            tamano = {"width": 14, "height": 40}
        else:
            tamano = {"height": 14, "width": 40}
        super().__init__(
            master,
            orientation=orient,
            command=command,
            fg_color="transparent",
            **tamano,
            **kwargs,
        )


class Spinbox(Frame):
    """
    Numero con botones − / + (el reemplazo de ttk.Spinbox, que CTk no
    tiene). Se escribe igual que en un campo comun; los botones suman o
    restan 1 sin salirse de [from_, to]. Un texto que no es numero se
    deja como esta: lo valida quien lo lee, igual que antes.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        from_: int = 0,
        to: int = 100,
        textvariable: tk.Variable | None = None,
        width: int = 6,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, **kwargs)
        self._desde, self._hasta = int(from_), int(to)
        self._var = textvariable if textvariable is not None else tk.StringVar()
        alto = _line_height(master) + 14
        Button(self, text="−", width=1, command=lambda: self._sumar(-1)).pack(
            side="left"
        )
        self.entry = Entry(self, textvariable=self._var, width=width, justify="center")
        self.entry.pack(side="left", padx=4)
        Button(self, text="+", width=1, command=lambda: self._sumar(1)).pack(
            side="left"
        )
        for hijo in self.winfo_children():
            if isinstance(hijo, Button):
                hijo.configure(height=alto)

    def _sumar(self, paso: int) -> None:
        try:
            actual = int(str(self._var.get()).strip())
        except ValueError:
            return
        self._var.set(str(max(self._desde, min(self._hasta, actual + paso))))

    def get(self) -> str:
        return str(self._var.get())


class Progressbar(_ItemAccess, ctk.CTkProgressBar):
    def __init__(
        self,
        master: tk.Misc,
        *,
        mode: str = "indeterminate",
        length: int = 200,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, mode=mode, width=length, height=8, **kwargs)

    def start(self, _interval: int | None = None) -> None:  # ttk recibe el paso
        super().start()


# ------------------------------------------------------------
# ventanas
# ------------------------------------------------------------
class PlainWindowGeometry:
    """
    geometry/minsize/maxsize como en Tk puro, en pixeles reales.

    CTk.geometry() y companeros multiplican lo que reciben por su escalado
    de pantalla, y la app siempre los calcula YA en pixeles reales
    (winfo_screenwidth, winfo_reqwidth): en Windows a 150 % quedaba todo
    escalado dos veces. Ademas CTk, un segundo despues de abrir la
    ventana, vuelve a aplicar SU minimo y maximo (0 y 1.000.000 si nadie
    uso sus metodos): eso borraba en silencio el tope que impide que un
    dialogo crezca mas alla de la pantalla (ver size_dialog_to_screen).
    """

    def geometry(self, geometry_string: str | None = None) -> str:
        return tk.Wm.wm_geometry(self, geometry_string)

    def minsize(self, width: int | None = None, height: int | None = None):
        return tk.Wm.wm_minsize(self, width, height)

    def maxsize(self, width: int | None = None, height: int | None = None):
        return tk.Wm.wm_maxsize(self, width, height)

    def _set_scaled_min_max(self) -> None:
        pass  # ver docstring: no pisar el minimo/maximo que puso la app


class Dialog(PlainWindowGeometry, ctk.CTkToplevel):
    """Base de todos los dialogos: fondo de la app y modo claro/oscuro solos."""

    # Cuantas veces reintentar grab_set() (cada 20 ms) antes de rendirse.
    _GRAB_REINTENTOS = 100

    def __init__(self, master: tk.Misc | None = None, **kwargs: Any) -> None:
        self._grab_intentos = 0
        self._id_mostrar: str | None = None
        super().__init__(master, **kwargs)
        # Escondido hasta que termine de armarse. Todos los dialogos miden
        # su contenido con update_idletasks() y recien despues se
        # centran / se agrandan: ese update_idletasks() MUESTRA una
        # ventana nueva (chica, arriba a la izquierda) y se veia el salto
        # al tamano y lugar finales. after(0) corre recien cuando el
        # constructor ya termino y el dialogo tiene su geometria.
        self.withdraw()
        self._id_mostrar = self.after(0, self._mostrar)

    def _mostrar(self) -> None:
        self._id_mostrar = None
        try:
            if not self.winfo_exists():
                return
            if self.state() == "withdrawn":
                # Los ayudantes que centran el dialogo (widgets.fit_*) anotan
                # donde lo pusieron. Alguna ficha (la de Cliente, con su
                # combo del tipo de cedula) llegaba aca con la posicion
                # vuelta a +0+0 y se abria pegada a la esquina de la
                # pantalla: se vuelve a pedir justo antes de mostrarla.
                posicion = getattr(self, "_posicion_inicial", None)
                if posicion is not None:
                    tk.Wm.wm_geometry(self, f"+{posicion[0]}+{posicion[1]}")
                self.deiconify()
            self.lift()
            # focus_force() de los constructores corrio con la ventana
            # escondida y no hizo nada.
            self.focus_force()
            # Lo mismo con el campo donde debia quedar el cursor (ver
            # widgets.focus_first_entry): se pierde mientras esta oculta.
            destino = getattr(self, "_foco_inicial", None)
            if destino is not None and destino.winfo_exists():
                destino.focus_set()
        except tk.TclError:
            pass  # se destruyo justo ahora

    def destroy(self) -> None:
        if self._id_mostrar is not None:
            try:
                self.after_cancel(self._id_mostrar)
            except tk.TclError:
                pass
            self._id_mostrar = None
        super().destroy()

    def grab_set(self) -> None:
        """
        Modal, aunque la ventana todavia no se vea.

        En Windows, CTkToplevel se ESCONDE unos milisegundos al nacer
        para repintar la barra de titulo en claro u oscuro, y los
        dialogos llaman a grab_set() en su __init__: Tk responde "grab
        failed: window not viewable" y el dialogo no se abria. En Linux
        pasa a veces, segun el orden de los eventos. En vez de fallar, se
        reintenta cada 20 ms hasta que la ventana se ve (o hasta ~2 s,
        p.ej. una ventana que nunca se muestra, como en los tests).
        """
        try:
            super().grab_set()
        except tk.TclError:
            self._grab_intentos += 1
            if self._grab_intentos <= self._GRAB_REINTENTOS:
                self.after(20, self._reintentar_grab)

    def _reintentar_grab(self) -> None:
        if self.winfo_exists():
            self.grab_set()


class Notice(Frame):
    """
    Aviso suave: recuadro amarillo con un icono y un texto que se parte
    solo al ancho que tenga. Para una pista que conviene ver pero no
    interrumpe (un messagebox seria demasiado) - mas ordenado que una
    linea de texto gris pegada al borde de otra cosa.

    Con ``closable`` lleva una "x" para cerrarlo, como una notificacion:
    una pista se lee una vez y despues solo estorba. Con ``key`` el
    cierre se recuerda mientras la app siga abierta (en todas las
    ventanas que usen esa misma clave); al volver a abrirla reaparece.
    """

    def __init__(
        self,
        master: tk.Misc,
        *,
        text: str,
        icon: str = "info",
        closable: bool = False,
        key: str | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(master, style="Notice.TFrame", padding=(12, 8), **kwargs)
        self._key = key
        self.cerrado = key is not None and key in _avisos_cerrados(self)
        self.columnconfigure(1, weight=1)
        # El icono arriba a la izquierda y el texto aparte: con el icono
        # dentro del mismo label, un texto de dos renglones lo dejaba
        # centrado y pegado a la primera letra.
        Label(
            self,
            text="",
            style="Notice.TLabel",
            image=icons.icon(self, icon, pair("notice_fg"), size=18),
        ).grid(row=0, column=0, sticky="n", padx=(0, 8), pady=(1, 0))
        # wraplength inicial: sin el, el texto pide su ancho de un solo
        # renglon y ensancha toda la ventana antes de poder partirse.
        self._texto = Label(self, text=text, style="Notice.TLabel", wraplength=360)
        self._texto.grid(row=0, column=1, sticky="ew")
        if closable:
            self._boton_cerrar = ctk.CTkButton(
                self,
                text="",
                image=icons.icon(self, "x", pair("notice_fg"), size=18),
                width=30,
                height=30,
                corner_radius=6,
                fg_color="transparent",
                hover_color=list(pair("notice_border")),
                command=self.cerrar,
            )
            self._boton_cerrar.grid(row=0, column=2, sticky="ne", padx=(8, 0))
        self.bind("<Configure>", self._ajustar, add="+")

    def _ajustar(self, evento: tk.Event) -> None:
        # Menos el icono con su margen, la "x" y el relleno del recuadro.
        extra = 100 if hasattr(self, "_boton_cerrar") else 70
        self._texto.configure(wraplength=max(120, evento.width - extra))

    def cerrar(self) -> None:
        """Lo esconde y, si tiene clave, recuerda que ya se cerro."""
        self.cerrado = True
        if self._key is not None:
            _avisos_cerrados(self).add(self._key)
        self._esconder()

    def _esconder(self) -> None:
        manager = self.winfo_manager()
        if manager == "grid":
            self.grid_remove()
        elif manager == "pack":
            self.pack_forget()

    def esconder_si_cerrado(self) -> None:
        """Para quien lo empaca con pack: llamarlo justo despues de pack()."""
        if self.cerrado:
            self._esconder()

    def mostrar(self, visible: bool) -> None:
        """Con grid: lo muestra o lo esconde, salvo que ya se haya cerrado."""
        if visible and not self.cerrado:
            self.grid()
        else:
            self.grid_remove()


def _avisos_cerrados(widget: tk.Misc) -> set[str]:
    """Las claves de avisos cerrados en esta sesion (viven en la ventana raiz)."""
    raiz = find_app_root(widget)
    if not hasattr(raiz, "_avisos_cerrados"):
        raiz._avisos_cerrados = set()
    return raiz._avisos_cerrados


def is_text_input(widget: tk.Misc) -> bool:
    """Lo que cuenta como "campo" para poner el foco (Entry o Combobox)."""
    return isinstance(widget, (Entry, Combobox, tk.Entry)) and not isinstance(
        widget.master, (Entry, Combobox)
    )
