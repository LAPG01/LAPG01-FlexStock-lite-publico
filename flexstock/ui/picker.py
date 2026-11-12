"""
FloatingPicker: un campo donde se escribe para buscar y, debajo, una lista
flotante con lo que coincide (clientes, productos...). Es el patron de
"escribe y Enter" de los puntos de venta: un solo campo en vez de un
buscador mas una tabla grande, y la lista no ocupa lugar en el formulario.

Teclado (el mismo que el selector de proveedor):
  - escribir filtra; la lista se abre sola;
  - flecha abajo / arriba mueven la fila resaltada sin sacar el foco;
  - Enter elige la resaltada (o la primera) y avisa con ``on_activate``;
  - Tab elige la primera coincidencia y sigue al campo siguiente (sin
    ``on_activate``: pasar de campo no es "confirmar");
  - Esc cierra la lista (con la lista cerrada, Esc sigue a la ventana).

Todo se filtra en memoria (``split_search_results``): una pasada por tecla
sobre unos miles de filas, nunca una consulta a la base por tecla.
"""

import tkinter as tk
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from . import kit
from .search import split_search_results
from .theme import get_palette, paint_listbox

_NORMAL, _OK, _APAGADO = "normal", "ok", "muted"


def posicion_en_ventana(widget: tk.Misc) -> tuple[int, int]:
    """Donde esta 'widget' dentro de su ventana (no en la pantalla: mover la
    ventana entera no cambia esto)."""
    ventana = widget.winfo_toplevel()
    return (
        widget.winfo_rootx() - ventana.winfo_rootx(),
        widget.winfo_rooty() - ventana.winfo_rooty(),
    )


class FloatingPicker(kit.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        claves: Callable[[Any], Sequence[str | None]],
        fila: Callable[[Any], tuple[str, str]],
        texto_elegido: Callable[[Any], str],
        items: Iterable[Any] = (),
        altura: int = 7,
        ancho_lista: int = 0,
        on_change: Callable[[Any | None], None] | None = None,
        on_activate: Callable[[Any], None] | None = None,
        nuevo: tuple[str, Callable[[], None]] | None = None,
    ) -> None:
        """
        claves(item)         textos por los que se busca (nombre, codigo...).
        fila(item)           (texto de la fila, estilo: normal|ok|muted).
        texto_elegido(item)  lo que queda escrito en el campo al elegir.
        nuevo                (texto, accion): ultima fila "+ texto" de la
                             lista, para dar de alta lo que no esta.
        ancho_lista          ancho minimo de la lista (px); mas ancha que el
                             campo cuando las filas son largas.
        """
        super().__init__(master)
        self._claves, self._fila, self._texto_elegido = claves, fila, texto_elegido
        self._on_change, self._on_activate = on_change, on_activate
        self._nuevo = nuevo
        self._altura, self._ancho_lista = altura, ancho_lista
        self._items: list[Any] = list(items)
        self._filtrados: list[Any] = []
        self._elegido: Any | None = None
        self._fijo = False
        self._escribiendo_codigo = False
        self._abierta = False
        self._pos_campo: tuple[int, int] | None = None
        # Con el campo vacio, Enter solo elige si se bajo a una fila con las
        # flechas: al enfocar, la lista muestra todo y un Enter apurado no
        # debe elegir "el primero" solo.
        self._navego = False

        self.var = tk.StringVar()
        self.entry = kit.Entry(self, textvariable=self.var, width=30)
        self.entry.pack(fill="x")
        e = self.entry
        e.bind("<Down>", lambda _e: self._mover(1))
        e.bind("<Up>", lambda _e: self._mover(-1))
        e.bind("<Return>", self._on_return)
        e.bind("<KP_Enter>", self._on_return)
        e.bind("<Escape>", self._on_escape)
        e.bind("<Tab>", self._on_tab)
        e.bind("<FocusIn>", self._on_focus, add="+")
        e.bind("<Button-1>", self._on_click, add="+")
        e.bind("<KeyRelease>", self._on_tecla, add="+")
        e.bind("<FocusOut>", self._on_sin_foco, add="+")

        # La lista flota ENCIMA del formulario, dentro de la misma ventana
        # (place() sobre la ventana, no una ventana aparte). Antes era una
        # ventanita sin marco (overrideredirect): en GNOME/Wayland esas
        # ventanas sueltas pasan por XWayland y el compositor, y al
        # scrollear la lista parpadeaba. Dentro de la ventana no hay nada
        # que el compositor pueda mover, tapar o re-enfocar, y si se mueve
        # la ventana la lista va con ella.
        self._capa = tk.Frame(
            self.winfo_toplevel(), background=get_palette(self).border, bd=0
        )
        self._capa._flex_repintar = lambda: self._capa.configure(
            background=get_palette(self).border
        )
        self.bind("<Destroy>", self._al_destruir, add="+")
        self.winfo_toplevel().bind("<Configure>", self._al_cambiar_ventana, add="+")
        self.lista = tk.Listbox(
            self._capa,
            height=altura,
            exportselection=False,
            activestyle="none",
            takefocus=0,  # Tab no entra a la lista: sigue al campo siguiente
        )
        paint_listbox(self.lista)
        # Barra al costado solo cuando hay mas filas que las que caben: con
        # 1.936 productos avisa que hay mas abajo y deja arrastrar; con pocas
        # coincidencias no estorba.
        self._barra = kit.Scrollbar(
            self._capa, orient="vertical", command=self.lista.yview
        )
        self.lista.configure(yscrollcommand=self._barra.set)
        self.lista.pack(side="left", fill="both", expand=True, padx=1, pady=1)
        self.lista.bind("<ButtonRelease-1>", self._on_clic_en_lista)

        self.var.trace_add("write", self._al_escribir)
        self._filtrar()

    # ------------------------------------------------------------
    # datos
    # ------------------------------------------------------------
    def set_items(self, items: Iterable[Any]) -> None:
        """Cambia lo que se puede elegir; conserva el elegido si sigue estando."""
        self._items = list(items)
        if self._elegido is not None and self._elegido not in self._items:
            self._elegido = None
            self._fijo = False
        self._filtrar()

    def elegido(self) -> Any | None:
        return self._elegido

    def elegir(self, item: Any, *, avisar: bool = True) -> None:
        """Deja elegido 'item' y su texto en el campo."""
        self._elegido = item
        self._fijo = True
        self._poner_texto(self._texto_elegido(item))
        self.cerrar()
        if avisar and self._on_change is not None:
            self._on_change(item)

    def elegir_indice(self, indice: int) -> Any | None:
        """Elige la fila 'indice' de lo que coincide ahora (para tests y atajos)."""
        if not 0 <= indice < len(self._filtrados):
            return None
        item = self._filtrados[indice]
        self.elegir(item)
        return item

    def limpiar(self, *, avisar: bool = True) -> None:
        hubo = self._elegido is not None
        self._elegido = None
        self._fijo = False
        self._poner_texto("")
        self.cerrar()
        if hubo and avisar and self._on_change is not None:
            self._on_change(None)

    def habilitar(self, activo: bool) -> None:
        self.entry.configure(state="normal" if activo else "disabled")

    def enfocar(self) -> None:
        self.entry.focus_set()
        self.entry.select_range(0, "end")

    @property
    def filtrados(self) -> list[Any]:
        return list(self._filtrados)

    # ------------------------------------------------------------
    # filtrado y lista
    # ------------------------------------------------------------
    def _poner_texto(self, texto: str) -> None:
        self._escribiendo_codigo = True
        self.var.set(texto)
        self._escribiendo_codigo = False
        self._filtrar()

    def _al_escribir(self, *_args: object) -> None:
        if self._escribiendo_codigo:
            return
        self._navego = False
        if self._fijo:
            # Escribio de nuevo sobre un elegido: vuelve a buscar.
            self._fijo = False
            self._elegido = None
            if self._on_change is not None:
                self._on_change(None)
        self._filtrar()

    def _filtrar(self) -> None:
        texto = "" if self._fijo else self.var.get()
        principales, coincidencias = split_search_results(
            self._items, texto, *(lambda i, n=n: self._clave(i, n) for n in range(3))
        )
        self._filtrados = principales + coincidencias
        self._pintar_lista()

    def _clave(self, item: Any, n: int) -> str | None:
        claves = self._claves(item)
        return claves[n] if n < len(claves) else None

    def _pintar_lista(self) -> None:
        paleta = get_palette(self)
        self.lista.delete(0, "end")
        for item in self._filtrados:
            texto, estilo = self._fila(item)
            self.lista.insert("end", texto)
            color = {_OK: paleta.success_bg, _APAGADO: paleta.muted_fg}.get(estilo)
            if color:
                self.lista.itemconfig("end", foreground=color)
        if self._nuevo is not None:
            self.lista.insert("end", f"+ {self._nuevo[0]}")
            self.lista.itemconfig("end", foreground=paleta.accent)
        if self.lista.size():
            self.lista.selection_clear(0, "end")
            self.lista.selection_set(0)

    def _abrir(self) -> None:
        total = self.lista.size()
        if not total:
            self.cerrar()
            return
        self.lista.configure(height=min(total, self._altura))
        if total > self._altura:
            self._barra.pack(side="right", fill="y", padx=(0, 1), pady=1)
        else:
            self._barra.pack_forget()
        self._ubicar()
        self._capa.lift()
        self._abierta = True

    def _ubicar(self) -> None:
        """Debajo del campo (o encima, si abajo no entra), en coordenadas de
        la ventana: place() es relativo a ella, no a la pantalla."""
        ventana = self._capa.master
        ventana.update_idletasks()
        alto_ventana, ancho_ventana = ventana.winfo_height(), ventana.winfo_width()
        ancho = max(self.entry.winfo_width(), self._ancho_lista)
        ancho = max(1, min(ancho, ancho_ventana - 16))
        alto = self._capa.winfo_reqheight()
        x = self.entry.winfo_rootx() - ventana.winfo_rootx()
        arriba = self.entry.winfo_rooty() - ventana.winfo_rooty()
        y = arriba + self.entry.winfo_height() + 2
        x = max(0, min(x, ancho_ventana - ancho - 8))
        if y + alto > alto_ventana - 8 and arriba - alto - 2 >= 0:
            y = arriba - alto - 2  # abajo no entra: se abre hacia arriba
        self._pos_campo = posicion_en_ventana(self.entry)
        self._capa.place(x=x, y=y, width=ancho, height=alto)

    def _al_cambiar_ventana(self, evento: tk.Event) -> None:
        """
        Algo de la ventana se movio o cambio de tamano. Si el campo ya no
        esta donde se abrio la lista (se scrolleo el formulario, se achico
        la ventana), la lista se cierra: quedaria flotando lejos de su campo.
        """
        if not self._abierta or str(evento.widget).startswith(str(self._capa)):
            return
        if posicion_en_ventana(self.entry) != self._pos_campo:
            self.cerrar()

    def _al_destruir(self, evento: tk.Event) -> None:
        if evento.widget is self and self._capa.winfo_exists():
            self._capa.destroy()

    def cerrar(self) -> None:
        if self._abierta:
            self._capa.place_forget()
            self._abierta = False

    @property
    def abierta(self) -> bool:
        return self._abierta

    # ------------------------------------------------------------
    # teclado y mouse
    # ------------------------------------------------------------
    _NO_ABREN = frozenset(
        {"Return", "KP_Enter", "Escape", "Up", "Down", "Tab", "ISO_Left_Tab"}
        | {"Shift_L", "Shift_R", "Control_L", "Control_R", "Alt_L", "Alt_R"}
    )

    def _on_focus(self, _e: object = None) -> None:
        self._navego = False
        if self._fijo:
            self.entry.select_range(0, "end")  # escribir reemplaza al elegido
        else:
            self._abrir()

    def _on_click(self, _e: object = None) -> None:
        if not self._fijo and not self._abierta:
            self._abrir()

    def _on_tecla(self, evento: tk.Event) -> None:
        if evento.keysym not in self._NO_ABREN and not self._fijo:
            self._abrir()

    def _indice_resaltado(self) -> int | None:
        actual = self.lista.curselection()
        return actual[0] if actual else None

    def _mover(self, delta: int) -> str:
        total = self.lista.size()
        if not total:
            return "break"
        if not self._abierta:
            self._abrir()
        self._navego = True
        actual = self._indice_resaltado()
        indice = (0 if delta > 0 else total - 1) if actual is None else actual + delta
        indice = max(0, min(total - 1, indice))
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(indice)
        self.lista.see(indice)
        return "break"

    def _elegir_fila(self, indice: int, *, activar: bool) -> None:
        """Una fila de la lista: un item (se elige) o la ultima "+ Nuevo..."."""
        if indice >= len(self._filtrados):  # la fila de "nuevo"
            self.cerrar()
            if self._nuevo is not None:
                self._nuevo[1]()
            return
        item = self._filtrados[indice]
        self.elegir(item)
        if activar and self._on_activate is not None:
            self._on_activate(item)

    def _on_return(self, _e: object = None) -> str | None:
        """Enter: elige la resaltada. Sin nada escrito ni lista, no hace nada
        (el Enter no debe elegir "el primero" solo)."""
        if self._fijo:
            if self._elegido is not None and self._on_activate is not None:
                self._on_activate(self._elegido)
            return "break"
        if not self.var.get().strip() and not self._navego:
            return None
        indice = self._indice_resaltado()
        if indice is None or not self.lista.size():
            return "break"
        self._elegir_fila(indice, activar=True)
        return "break"

    def _on_tab(self, _e: object = None) -> None:
        """Tab con algo escrito y sin eleccion: elige la primera y sigue."""
        if self._fijo or not self.var.get().strip() or not self._filtrados:
            return
        indice = self._indice_resaltado()
        if indice is None or indice >= len(self._filtrados):
            indice = 0
        self.elegir(self._filtrados[indice])

    def _on_escape(self, _e: object = None) -> str | None:
        if self._abierta:
            self.cerrar()
            return "break"
        return None

    def _on_clic_en_lista(self, evento: tk.Event) -> None:
        indice = self.lista.nearest(evento.y)
        if 0 <= indice < self.lista.size():
            self._elegir_fila(indice, activar=True)
            self.entry.focus_set()

    def _on_sin_foco(self, _e: object = None) -> None:
        # Con demora: un clic en la lista le saca el foco al campo un
        # instante antes de elegir.
        self.after(150, self._cerrar_si_perdio_foco)

    def _cerrar_si_perdio_foco(self) -> None:
        """
        Cierra la lista solo si el foco se fue DE VERDAD (a otro campo u
        otra ventana). Un FocusOut pasajero no la cierra si el foco volvio al
        campo: cerrarla y reabrirla con el FocusIn siguiente se ve como
        parpadeo.
        """
        if not self.winfo_exists():
            return
        foco = self.focus_get()
        if foco is not None and (
            foco is self.lista or str(foco).startswith(str(self.entry))
        ):
            return
        self.cerrar()
