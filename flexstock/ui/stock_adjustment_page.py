"""
Pagina de ajuste manual de stock: conteo fisico, merma, error de sistema.

Pide la cantidad CONTADA (lo que el ferretero ve al contar), no un delta
con signo: StockService.ajustar() calcula la diferencia contra el stock
actual - pedir "-3" a mano es mas facil de invertir por error que pedir
"conte 47".

PENSADA PARA REPETIR. El caso real no es ajustar un producto suelto: es
sentarse a cargar el inventario de a poco, producto tras producto,
porque las existencias del sistema viejo nunca se migraron. La version
anterior cobraba un peaje en cada vuelta - modal de confirmacion,
motivo que se borraba, seleccion perdida y recarga del catalogo entero -
asi que todo eso esta pensado al reves aca:

  - El resultado sale como texto al lado del boton, no como messagebox:
    un modal por producto son 1.936 clicks de mas.
  - El motivo NO se borra: se escribe una vez ("Conteo fisico") y sirve
    para toda la sesion.
  - Al registrar, la tabla salta sola al producto siguiente y el cursor
    vuelve a "Cantidad contada": contar de corrido es escribir numero,
    Enter, numero, Enter.
  - Solo se refresca la fila tocada, no las 1.936: reload() completo
    despues de cada ajuste era lo que hacia perder el lugar en la lista.
  - Antes de registrar se ve que va a pasar ("va a restar 3"), y lo ya
    hecho queda a la vista en "Ajustes de esta sesion" para cazar un
    numero mal tecleado. Los movimientos son append-only (regla 3): un
    error no se borra, se corrige contando de nuevo - por eso conviene
    verlo enseguida.

ui/ solo llama a ProductService/StockService: nunca importa nada de
flexstock.data.
"""

import tkinter as tk
from decimal import InvalidOperation
from tkinter import ttk

import customtkinter as ctk

from ..domain.entities import InventoryMovement, Product
from ..domain.enums import MovementType, ReferenceType
from ..domain.errors import DomainError
from ..domain.money import format_quantity, parse_local_decimal, to_milli
from ..services.product_service import ProductService
from ..services.stock_service import StockService
from . import kit
from .search import split_search_results
from .theme import ctk_font, paint_listbox, paint_window
from .widgets import SearchableTable, fixed_height_frame, wrap_to_width

_MOTIVOS_PRESET = (
    "Conteo físico",
    "Carga inicial de inventario",
    "Merma",
    "Perdida",
    "Error de conteo",
    "Entrada de mercancia",
)
_MOTIVO_POR_DEFECTO = "Conteo físico"

# "Ajustar existencia" (ficha del producto): que se escribe en el campo.
# Sumar y Restar solo ahorran la cuenta: el movimiento que queda es el mismo
# ajuste (StockService.ajustar recibe siempre el total resultante).
_MODO_CONTAR = "Contar total"
_MODO_SUMAR = "Sumar"
_MODO_RESTAR = "Restar"
_MODOS = (_MODO_CONTAR, _MODO_SUMAR, _MODO_RESTAR)
_ETIQUETA_CANTIDAD = {
    _MODO_CONTAR: "Cantidad contada",
    _MODO_SUMAR: "Cantidad a sumar",
    _MODO_RESTAR: "Cantidad a restar",
}
# Motivo que se propone al cambiar de modo (solo si el motivo puesto es
# uno de estos: uno escrito a mano no se pisa).
_MOTIVO_SUGERIDO = {
    _MODO_CONTAR: "Conteo físico",
    _MODO_SUMAR: "Entrada de mercancia",
    _MODO_RESTAR: "Merma",
}

_SIN_PRODUCTO = "Elige un producto"  # corto: a 150 % no entra en 2 renglones

_COLUMNAS = (
    ("sku", "SKU", 110, "w"),
    ("name", "Nombre", 320, "w"),
    ("unit", "Unidad", 90, "w"),
    ("stock", "Stock actual", 130, "e"),
)

# HistorialProductoDialog: como se ve cada tipo/origen de movimiento (el
# enum es solo el vocabulario cerrado, ver domain/enums.py).
_TIPO_DISPLAY = {
    MovementType.IN: "Entrada",
    MovementType.OUT: "Salida",
    MovementType.ADJUSTMENT: "Ajuste",
    MovementType.INITIAL: "Carga inicial",
}
_ORIGEN_DISPLAY = {
    ReferenceType.INVOICE: "Factura",
    ReferenceType.CREDIT_NOTE: "Nota de crédito",
    ReferenceType.PURCHASE: "Compra",
    ReferenceType.COUNT: "Conteo",
    ReferenceType.IMPORT: "Importación",
}
_COLUMNAS_HISTORIAL = (
    ("fecha", "Fecha", 130, "w"),
    ("tipo", "Tipo", 90, "w"),
    ("cantidad", "Cantidad", 90, "e"),
    ("origen", "Origen", 110, "w"),
    ("motivo", "Motivo", 150, "w"),
    ("contado_por", "Contado por", 110, "w"),
    ("nota", "Nota", 220, "w"),
)


def vista_previa_ajuste(stock_milli: int, texto: str) -> tuple[str, str]:
    """
    Que va a pasar si se cuenta 'texto' habiendo 'stock_milli' (texto y
    estilo del label). Verde si suma, rojo si resta o es invalido, gris
    si no cambia: el color se lee antes que el texto, y un "va a restar"
    en rojo es la ultima oportunidad de ver un conteo mal tipeado (un
    movimiento no se borra, regla 3). La usan la pantalla Ajuste de
    stock y la ventana "Ajustar existencia" de la ficha del producto.
    """
    texto = texto.strip()
    if not texto:
        return "", "Muted.TLabel"
    try:
        contado = parse_local_decimal(texto)
    except InvalidOperation:
        return "No es un número (ej. 47 o 12,5).", "Error.TLabel"
    if contado < 0:
        return "La cantidad contada no puede ser negativa.", "Error.TLabel"

    return mensaje_de_delta(to_milli(contado) - stock_milli)


def mensaje_de_delta(delta_milli: int) -> tuple[str, str]:
    """El "va a sumar / va a restar" de un cambio de existencia (texto y estilo)."""
    if delta_milli == 0:
        return "Igual al stock: no hay nada que ajustar.", "Muted.TLabel"
    if delta_milli > 0:
        return f"Va a sumar +{format_quantity(delta_milli)}", "Ok.TLabel"
    return f"Va a restar {format_quantity(-delta_milli)}", "Error.TLabel"


class StockAdjustmentPage(kit.Frame):
    """Contenedor autonomo: se puede empotrar en cualquier ventana padre."""

    def __init__(
        self,
        master: tk.Misc,
        stock_service: StockService,
        product_service: ProductService,
    ) -> None:
        super().__init__(master)
        self._stock = stock_service
        self._products_svc = product_service
        self._productos: list[Product] = []  # catalogo completo, en memoria
        self._filtrados: list[Product] = []  # lo que se ve ahora en la tabla
        self._producto: Product | None = None

        self._titulo_var = tk.StringVar(value=_SIN_PRODUCTO)
        # Solo el numero y la unidad ("7,5 UNI"): va en letra grande, con
        # el titulo "STOCK" chico al lado (ver _build_panel).
        self._stock_actual_var = tk.StringVar(value="-")
        self._contado_var = tk.StringVar()
        self._motivo_var = tk.StringVar(value=_MOTIVO_POR_DEFECTO)
        # Igual que el motivo: no hay login en la app, asi que es texto
        # libre en vez de un usuario de verdad. Se queda puesto entre un
        # conteo y el siguiente -lo normal es que sea la misma persona
        # contando un estante entero.
        self._contado_por_var = tk.StringVar()
        # A diferencia de motivo/contado por, esto SI se borra despues de
        # registrar (ver _on_registrar): es una nota de ESTE ajuste
        # puntual ("llegaron 4 pero vienen 6 mas"), no algo que se repita.
        self._nota_var = tk.StringVar()
        self._preview_var = tk.StringVar()
        self._estado_var = tk.StringVar()

        self._build_widgets()
        self._buscar_var.trace_add("write", self._on_filtrar)
        # La vista previa del ajuste se recalcula con cada tecla: ver el
        # "va a restar 3" ANTES de apretar es lo que evita el ajuste
        # equivocado, que despues no se puede borrar (regla 3).
        self._contado_var.trace_add("write", self._refrescar_preview)

        self.reload()

    # ------------------------------------------------------------
    # construccion de widgets
    # ------------------------------------------------------------
    def _build_widgets(self) -> None:
        frame = kit.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        # La tabla se lleva el espacio que sobra; el panel de la derecha
        # pide lo suyo y no crece.
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(0, weight=1)

        self._table = SearchableTable(
            frame,
            columns=tuple(c[0] for c in _COLUMNAS),
            headings={c[0]: c[1] for c in _COLUMNAS},
            column_widths={c[0]: c[2] for c in _COLUMNAS},
            column_anchors={c[0]: c[3] for c in _COLUMNAS},
            search_label="Buscar producto (SKU o nombre):",
        )
        self._table.grid(row=0, column=0, sticky="nsew")
        self._buscar_var = self._table.search_var
        self._tree = self._table.tree
        # Elegir una fila (con el mouse o con las flechas, que
        # SearchableTable ya ata al buscador) llena el panel de al lado.
        self._tree.bind("<<TreeviewSelect>>", self._on_seleccionar_producto)
        # Verde para lo ya contado en esta sesion: al bajar por un
        # estante se ve de un vistazo por donde se iba.
        self._table.tag_style("contado", foreground="#2f9e44")

        self._build_panel(frame)

    def _build_panel(self, parent: kit.Frame) -> None:
        # Una tarjeta, como el "ticket" de una caja: todo lo del producto
        # elegido junto, separado de la tabla. Relleno vertical chico a
        # proposito: cada pixel de alto que se gasta aca es uno menos para
        # "Ajustes de esta sesion" a 150 % (ver el test de 150 %).
        contenedor = kit.Frame(parent, padding=(16, 0, 0, 0))
        contenedor.grid(row=0, column=1, sticky="ns")
        panel = kit.Frame(contenedor, style="Card.TFrame", padding=(14, 10))
        panel.pack(fill="both", expand=True)
        self._panel = panel

        # fixed_height_frame + wraplength: el titulo cambia con cada
        # producto elegido (hasta 40 caracteres de nombre, ver
        # schema.sql), y sin un tope el panel entero se ensanchaba o
        # achicaba segun cual estuviera elegido -el "recuadro que se
        # ajusta al texto" que reporto el autor.
        marco_titulo = fixed_height_frame(panel, lines=2)
        marco_titulo.pack(anchor="w", fill="x")
        titulo = kit.Label(
            marco_titulo,
            textvariable=self._titulo_var,
            style="Heading.TLabel",
            wraplength=240,
            justify="left",
        )
        titulo.pack(fill="both", expand=True)
        wrap_to_width(titulo, max_width=240)

        # El boton en la misma fila que el stock (no una propia: a 150 %
        # no sobraba otro renglon). La etiqueta va en su marco de alto
        # fijo (cambia con cada producto); el boton afuera de ese marco,
        # porque un boton es mas alto que un renglon de texto y adentro
        # se recortaria.
        fila_stock = kit.Frame(panel)
        fila_stock.pack(anchor="w", fill="x", pady=(0, 8))
        kit.Label(fila_stock, text="STOCK", style="CardTitle.TLabel").pack(
            side="left", anchor="s", pady=(0, 6)
        )
        # El numero grande: es lo que se mira antes de contar. Ancho fijo
        # en caracteres (como un ttk.Label): "1.234,5 METRO" no ensancha
        # el panel, y un numero mas largo se corta en vez de empujar todo.
        self._label_stock = kit.Label(
            fila_stock,
            textvariable=self._stock_actual_var,
            style="CardValue.TLabel",
            width=9,
        )
        self._label_stock.pack(side="left", padx=(8, 0))
        # TODO lo que le paso a este producto -ventas, ajustes, carga
        # inicial-, no solo lo contado en esta sesion (ver
        # HistorialProductoDialog).
        self._btn_historial = kit.Button(
            fila_stock,
            text="Historial",
            icon="file-text",
            command=self._on_ver_historial,
            state="disabled",
        )
        self._btn_historial.pack(side="right", padx=(8, 0))

        # Etiqueta y campo en la misma fila (aca y en Motivo): a 150 %
        # cada fila de mas son ~45 px, y apiladas dejaban sin lugar la
        # lista de "Ajustes de esta sesion".
        fila_contado = kit.Frame(panel)
        fila_contado.pack(anchor="w", fill="x", pady=(0, 2))
        kit.Label(fila_contado, text="Cantidad contada:").pack(side="left")
        self._entry_contado = kit.Entry(
            fila_contado, textvariable=self._contado_var, width=12
        )
        self._entry_contado.pack(side="left", padx=(8, 0))
        # Enter registra: contar de corrido es numero, Enter, numero,
        # Enter - sin ir al boton con el mouse en cada producto.
        self._entry_contado.bind("<Return>", lambda _e: self._on_registrar())

        # Mismo motivo que el titulo: "No es un numero (ej. 47 o 12,5)."
        # es mas largo que "Va a restar 3" y sin tope tambien ensanchaba
        # el panel al escribir.
        marco_preview = fixed_height_frame(panel, lines=1)
        marco_preview.pack(anchor="w", fill="x", pady=(0, 10))
        self._label_preview = kit.Label(
            marco_preview,
            textvariable=self._preview_var,
            style="Muted.TLabel",
            wraplength=240,
            justify="left",
        )
        self._label_preview.pack(fill="both", expand=True)
        wrap_to_width(self._label_preview, max_width=240)

        # Motivo y Contado por comparten fila (igual criterio que
        # Cantidad/Nivel en Nueva factura): son los dos campos que se
        # escriben una vez y quedan puestos para el resto de la sesion,
        # a diferencia de Cantidad contada (cambia en cada producto) y
        # Nota (de este ajuste puntual). Separarlos en una fila cada uno
        # era lo que no entraba a 150 %.
        fila_motivo = kit.Frame(panel)
        fila_motivo.pack(anchor="w", fill="x", pady=(0, 2))
        kit.Label(fila_motivo, text="Motivo:").pack(side="left")
        kit.Combobox(
            fila_motivo,
            textvariable=self._motivo_var,
            values=_MOTIVOS_PRESET,
            width=16,
        ).pack(side="left", padx=(6, 0))
        kit.Label(fila_motivo, text="Por:").pack(side="left", padx=(10, 0))
        kit.Entry(fila_motivo, textvariable=self._contado_por_var, width=10).pack(
            side="left", padx=(6, 0)
        )

        fila_nota = kit.Frame(panel)
        fila_nota.pack(anchor="w", fill="x", pady=(2, 6))
        kit.Label(fila_nota, text="Nota:").pack(side="left")
        self._entry_nota = kit.Entry(fila_nota, textvariable=self._nota_var, width=22)
        self._entry_nota.pack(side="left", padx=(10, 0))

        # Sin cartel de ayuda aca a proposito (lo tenia, "Motivo y Por
        # quedan puestos."): una frase corta en espanol igual envuelve a
        # DOS renglones a 150 % con este wraplength, y esos renglones de
        # mas eran los que le sacaban toda su altura a "Ajustes de esta
        # sesion" de abajo - quedaba con la lista en 12 px, inservible.
        # Se aprende usandolo una vez (que no se borra al registrar).

        self._btn_registrar = kit.Button(
            panel,
            text="Registrar ajuste",
            icon="clipboard-check",
            command=self._on_registrar,
            style="Success.TButton",
            padding=(18, 10),
        )
        self._btn_registrar.pack(fill="x")

        self._label_estado = kit.Label(
            panel, textvariable=self._estado_var, style="Ok.TLabel", wraplength=240
        )
        self._label_estado.pack(anchor="w", pady=(6, 0))

        # title_style chico: el de LabelFrame por defecto usa la misma
        # fuente que el titulo del producto (rol "heading"), y a 150 %
        # eso solo son los 46 px que le faltaban a la lista de abajo
        # (ver el test de 150 % de este archivo). "CardTitle.TLabel" es
        # el mismo estilo chico y en mayuscula que ya usa "STOCK" mas
        # arriba en este mismo panel.
        historial = kit.LabelFrame(
            panel,
            text="Ajustes de esta sesión",
            padding=4,
            title_style="CardTitle.TLabel",
        )
        historial.pack(anchor="w", fill="both", expand=True, pady=(10, 0))
        # width en caracteres: es lo que fija el ancho de TODO el panel
        # y por lo tanto lo que le queda a la tabla. 30 entra comodo en
        # el minimo de la ventana (1000 px, ver MainWindow.minsize).
        #
        # Sin el cartel "Un ajuste no se borra..." que tenia antes a
        # proposito: a 150 % envolvia a dos renglones (82 px) y, siendo
        # esta la unica seccion con scroll del panel (la que cede cuando
        # falta alto - ver las demas filas de _build_panel), esos 82 px
        # eran los que le faltaban a la lista de abajo para mostrar algo
        # mas que una franja de 12 px. El motivo ("no se borra, se
        # corrige contando de nuevo") ya esta en el docstring del modulo
        # y se aprende la primera vez que se usa.
        #
        # height=1: el minimo real de Tk para un Listbox (0 lo deja en su
        # alto por defecto, que son varias filas) - es solo el piso para
        # cuando el espacio sobrante es poco; con lugar de sobra crece
        # igual (fill/expand).
        self._lista_historial = tk.Listbox(historial, height=1, width=30)
        paint_listbox(self._lista_historial)  # tk.Listbox no sigue el tema ttk solo
        self._lista_historial.pack(fill="both", expand=True)

    # ------------------------------------------------------------
    # carga y filtrado (todo en memoria, sin paginacion)
    # ------------------------------------------------------------
    def reload(self) -> None:
        """
        Relee el catalogo entero. Se llama al construir la pagina y
        cuando alguien la pide de afuera - NO despues de cada ajuste:
        para eso esta _refrescar_fila(), que toca una sola fila.
        """
        self._productos = self._products_svc.list_products()
        self._aplicar_filtro()

    def select_product(self, product_id: int) -> None:
        """
        Deja elegido ese producto, listo para escribir lo contado. Lo usa
        "Ajustar..." de la ficha de un producto (ver MainWindow). Limpia
        la busqueda primero: con un filtro de antes, la fila podia no
        estar en la tabla.
        """
        self._buscar_var.set("")
        iid = str(product_id)
        if not self._tree.exists(iid):
            return
        self._tree.selection_set(iid)
        self._tree.see(iid)
        # Directo y no esperando <<TreeviewSelect>>: ese evento llega en
        # la proxima vuelta del mainloop, y el foco tiene que quedar ya en
        # "Cantidad contada".
        self._on_seleccionar_producto()

    def _on_filtrar(self, *_args: object) -> None:
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        principales, coincidencias = split_search_results(
            self._productos,
            self._buscar_var.get(),
            lambda p: p.name,
            lambda p: p.sku,
        )
        self._filtrados = principales + coincidencias
        self._table.set_grouped_rows(
            [(str(p.id), self._valores_de(p)) for p in principales],
            [(str(p.id), self._valores_de(p)) for p in coincidencias],
        )
        self._table.set_status(
            f"Mostrando {len(self._filtrados)} de {len(self._productos)} productos"
        )

    @staticmethod
    def _valores_de(producto: Product) -> tuple:
        return (
            producto.sku,
            producto.name,
            producto.unit,
            format_quantity(producto.current_stock),
        )

    # ------------------------------------------------------------
    # seleccion de producto
    # ------------------------------------------------------------
    def _on_seleccionar_producto(self, *_args: object) -> None:
        iid = self._table.selected_iid()
        if not iid or iid == "__coincidencias__":
            return
        # Vuelve a pedir el producto (no confia en el snapshot en memoria
        # de reload()): puede haber pasado tiempo desde que se cargo la
        # lista y otra pantalla pudo haber vendido/ajustado este mismo
        # producto mientras tanto.
        self._producto = self._products_svc.get_product(int(iid))
        if self._producto is None:
            return

        self._titulo_var.set(f"{self._producto.sku} - {self._producto.name}")
        self._stock_actual_var.set(
            f"{format_quantity(self._producto.current_stock)} {self._producto.unit}"
        )
        self._contado_var.set("")
        self._estado_var.set("")
        self._btn_historial.configure(state="normal")
        self._entry_contado.focus_set()

    def _on_ver_historial(self) -> None:
        if self._producto is None:
            return
        movimientos = self._stock.history_for(self._producto.id)
        HistorialProductoDialog(self, self._producto, movimientos)

    # ------------------------------------------------------------
    # vista previa del ajuste
    # ------------------------------------------------------------
    def _refrescar_preview(self, *_args: object) -> None:
        texto, estilo = self._texto_de_preview()
        self._preview_var.set(texto)
        # Verde si suma, rojo si resta o es invalido, gris si no cambia:
        # el color se lee antes que el texto, y un "va a restar" en rojo
        # es la ultima oportunidad de ver un conteo mal tipeado (un
        # movimiento no se borra, regla 3).
        self._label_preview.configure(style=estilo)

    def _texto_de_preview(self) -> tuple[str, str]:
        if self._producto is None:
            return "", "Muted.TLabel"
        return vista_previa_ajuste(
            self._producto.current_stock, self._contado_var.get()
        )

    # ------------------------------------------------------------
    # registrar ajuste
    # ------------------------------------------------------------
    def _avisar(self, mensaje: str, *, ok: bool) -> None:
        """
        El resultado va al lado del boton, no a un messagebox: en una
        pantalla que se usa muchas veces seguidas, un modal por ajuste
        es un click de mas cada vez. El color distingue un ajuste hecho
        de uno rechazado (ver Ok/Error.TLabel en theme.py).
        """
        self._estado_var.set(mensaje)
        self._label_estado.configure(style="Ok.TLabel" if ok else "Error.TLabel")

    def _on_registrar(self) -> None:
        if self._producto is None:
            self._avisar("Elige un producto de la lista primero.", ok=False)
            return

        try:
            contado = parse_local_decimal(self._contado_var.get() or "")
        except InvalidOperation:
            self._avisar("La cantidad contada debe ser un número (ej. 47).", ok=False)
            return
        if contado < 0:
            self._avisar("La cantidad contada no puede ser negativa.", ok=False)
            return

        producto = self._producto
        try:
            movimiento = self._stock.ajustar(
                product_id=producto.id,
                counted_milli=to_milli(contado),
                reason=self._motivo_var.get(),
                counted_by=self._contado_por_var.get().strip() or None,
                note=self._nota_var.get().strip() or None,
            )
        except (DomainError, ValueError) as error:
            self._avisar(str(error), ok=False)
            return

        signo = "+" if movimiento.quantity_milli > 0 else "-"
        cantidad = format_quantity(abs(movimiento.quantity_milli))
        contado_txt = format_quantity(to_milli(contado))
        self._avisar(
            f"{producto.sku}: {signo}{cantidad}, queda en {contado_txt}.", ok=True
        )
        # HH:MM adelante: con created_at ya resuelto por StockService (ver
        # su docstring) no hace falta una consulta aparte para saber
        # cuando se hizo cada conteo de la sesion. Quien lo hizo, si se
        # cargo, va al final entre guiones - la linea ya es larga.
        hora = (
            movimiento.created_at.strftime("%H:%M")
            if movimiento.created_at
            else "--:--"
        )
        texto = f"{hora}  {signo}{cantidad}  {producto.sku} - {producto.name} (ahora {contado_txt})"
        if movimiento.counted_by:
            texto += f" — {movimiento.counted_by}"
        self._lista_historial.insert(0, texto)

        self._refrescar_fila(producto.id, to_milli(contado))
        # Motivo y Contado por NO se limpian a proposito (ver docstring
        # del modulo): la nota si, es de ESTE ajuste puntual.
        self._contado_var.set("")
        self._nota_var.set("")
        self._siguiente_producto()

    def _refrescar_fila(self, product_id: int, nuevo_stock_milli: int) -> None:
        """
        Actualiza SOLO la fila tocada, en memoria y en la tabla. Volver
        a llamar a reload() aca seria releer las 1.936 filas del
        catalogo (dos consultas) por cada ajuste, y de paso perder el
        lugar en la lista - que es lo que hacia incomodo contar seguido.
        """
        for producto in self._productos:
            if producto.id == product_id:
                producto.current_stock = nuevo_stock_milli
                if self._tree.exists(str(product_id)):
                    self._tree.item(str(product_id), values=self._valores_de(producto))
                    self._table.set_row_tag(str(product_id), "contado")
                return

    def _siguiente_producto(self) -> None:
        """
        Salta al producto de abajo y deja el cursor en "Cantidad
        contada". Contar un estante es bajar de a una fila; si era la
        ultima, se queda donde esta en vez de saltar al principio.
        """
        filas = self._tree.get_children()
        actual = self._table.selected_iid()
        if not filas or actual is None:
            return
        indice = filas.index(actual) if actual in filas else -1
        siguiente = next(
            (fila for fila in filas[indice + 1 :] if fila != "__coincidencias__"),
            None,
        )
        if siguiente is None:
            self._entry_contado.focus_set()
            return
        self._tree.selection_set(siguiente)
        self._tree.see(siguiente)
        # selection_set dispara <<TreeviewSelect>> recien en el proximo
        # ciclo de Tk: se llama derecho para que el panel ya muestre el
        # producto nuevo al volver de aca.
        self._on_seleccionar_producto()


class AjustarExistenciaDialog(kit.Dialog):
    """
    "Ajustar existencia" desde la ficha de un producto, sin salir de ella.

    Hace lo mismo que la pantalla Ajuste de stock -pide lo CONTADO, no un
    delta, y pasa por StockService.ajustar(), asi que queda el mismo
    movimiento con su motivo y quien conto (reglas 2 y 3)-, pero en una
    ventana corta: para cambiar un solo numero no hace falta irse a otra
    pantalla y perder la ficha.

    'recuerdo' es un dict que comparten las ventanas de una misma sesion
    (lo guarda ProductsPage): motivo y "Contado por" quedan puestos de
    una vez a la siguiente, igual que en la pantalla de Ajuste.

    Al aceptar, 'resultado' trae la existencia nueva (milesimas) y la
    ventana se cierra; si se cancela, queda en None.
    """

    def __init__(
        self,
        master: tk.Misc,
        stock_service: StockService,
        *,
        product_id: int,
        sku: str,
        nombre: str,
        unidad: str,
        existencia_milli: int,
        recuerdo: dict[str, str] | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Ajustar existencia")
        self.transient(master)
        self.resizable(False, False)

        self._stock = stock_service
        self._product_id = product_id
        self._existencia_milli = existencia_milli
        self._recuerdo = recuerdo if recuerdo is not None else {}
        self.resultado: int | None = None

        self._contado_var = tk.StringVar()
        self._motivo_var = tk.StringVar(
            value=self._recuerdo.get("motivo", _MOTIVO_POR_DEFECTO)
        )
        self._contado_por_var = tk.StringVar(
            value=self._recuerdo.get("contado_por", "")
        )
        self._nota_var = tk.StringVar()
        self._queda_var = tk.StringVar(value="-")
        self._mensaje_var = tk.StringVar()
        self._unidad = unidad
        self._modo_var = tk.StringVar(value=_MODO_CONTAR)

        self._build(sku, nombre, existencia_milli)
        # El "queda en" y el "va a sumar" siguen a cada tecla: ver el
        # resultado ANTES de registrar es lo que evita el ajuste
        # equivocado, que despues no se puede borrar.
        self._contado_var.trace_add("write", self._refrescar)
        self.bind("<Escape>", lambda _e: self.destroy())
        self.bind("<Return>", lambda _e: self._on_registrar())

        self.update_idletasks()
        self._centrar_sobre(master)
        self.lift()
        self.focus_force()
        self._entry_contado.focus_set()
        self.grab_set()

    def _centrar_sobre(self, master: tk.Misc) -> None:
        # req*: la ventana todavia esta escondida (ver kit.Dialog).
        ancho = self.winfo_reqwidth()
        # Los textos que se parten solos (wrap_to_width) lo hacen recien al
        # mostrarse; medir el alto antes los contaria con el wraplength
        # inicial y la ventana quedaría con un hueco abajo. Se les da ya el
        # ancho final (el del contenido: sin el relleno de 20 px por lado).
        util = ancho - 40
        for etiqueta in (self._titulo, self._label_mensaje):
            etiqueta.configure(wraplength=util)
        if self._aviso is not None:
            self._aviso._texto.configure(wraplength=util - 70)
        self.update_idletasks()
        alto = self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - ancho) // 2
        y = master.winfo_rooty() + (master.winfo_height() - alto) // 3
        x, y = max(x, 0), max(y, 0)
        self.geometry(f"{ancho}x{alto}+{x}+{y}")
        self._posicion_inicial = (x, y)

    def _build(self, sku: str, nombre: str, existencia_milli: int) -> None:
        marco = kit.Frame(self, padding=20)
        marco.pack(fill="both", expand=True)
        marco.columnconfigure(0, weight=1, uniform="campos")
        marco.columnconfigure(1, weight=1, uniform="campos")

        titulo = kit.Label(
            marco,
            text=nombre,
            style="Heading.TLabel",
            # wraplength inicial: sin el, un nombre largo pide su ancho de
            # un solo renglon y ensancha toda la ventana.
            wraplength=420,
            justify="left",
        )
        titulo.grid(row=0, column=0, columnspan=2, sticky="ew")
        wrap_to_width(titulo)
        self._titulo = titulo
        kit.Label(
            marco, text=f"{sku}  \u00b7  {self._unidad}", style="Muted.TLabel"
        ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(2, 14))

        # Lo de hoy y lo que va a quedar, lado a lado en una tarjeta: es lo
        # unico que importa mirar antes de apretar "Registrar".
        tarjeta = kit.Frame(marco, style="Card.TFrame", padding=(16, 12))
        tarjeta.grid(row=2, column=0, columnspan=2, sticky="ew")
        tarjeta.columnconfigure(0, weight=1, uniform="valores")
        tarjeta.columnconfigure(2, weight=1, uniform="valores")
        kit.Label(tarjeta, text="HAY AHORA", style="CardTitle.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        kit.Label(
            tarjeta,
            text=f"{format_quantity(existencia_milli)} {self._unidad}",
            style="CardValue.TLabel",
        ).grid(row=1, column=0, sticky="w")
        kit.Label(tarjeta, text="\u2192", style="Heading.TLabel").grid(
            row=1, column=1, padx=12
        )
        kit.Label(tarjeta, text="QUEDARA EN", style="CardTitle.TLabel").grid(
            row=0, column=2, sticky="w"
        )
        kit.Label(tarjeta, textvariable=self._queda_var, style="CardValue.TLabel").grid(
            row=1, column=2, sticky="w"
        )

        # Un renglon fijo para el mensaje: cambia de largo con cada tecla y
        # sin alto fijo la ventana se estiraria y encogeria sola.
        marco_mensaje = fixed_height_frame(marco, lines=1)
        marco_mensaje.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(6, 4))
        self._label_mensaje = kit.Label(
            marco_mensaje,
            textvariable=self._mensaje_var,
            style="Muted.TLabel",
            wraplength=420,
            justify="left",
        )
        self._label_mensaje.pack(fill="both", expand=True)
        wrap_to_width(self._label_mensaje)

        fila = 4
        self._aviso: kit.Notice | None = None
        if existencia_milli < 0:
            self._aviso = kit.Notice(
                marco,
                text=(
                    "La existencia está en negativo: se vendió más de lo que "
                    "había cargado. Cuenta lo que hay en el estante y "
                    "registralo aquí."
                ),
            )
            self._aviso.grid(
                row=fila, column=0, columnspan=2, sticky="ew", pady=(0, 10)
            )
            fila += 1

        # Contar / Sumar / Restar: que numero se escribe. Un selector
        # segmentado y no una lista: son tres opciones y se ven todas.
        self._selector_modo = ctk.CTkSegmentedButton(
            marco,
            values=list(_MODOS),
            variable=self._modo_var,
            command=self._on_modo,
            font=ctk_font(self, "body"),
        )
        self._selector_modo.grid(
            row=fila, column=0, columnspan=2, sticky="ew", pady=(0, 12)
        )
        fila += 1

        self._label_cantidad = kit.Label(
            marco, text=_ETIQUETA_CANTIDAD[_MODO_CONTAR], style="Bold.TLabel"
        )
        self._label_cantidad.grid(row=fila, column=0, columnspan=2, sticky="w")
        self._entry_contado = kit.Entry(marco, textvariable=self._contado_var, width=16)
        self._entry_contado.grid(
            row=fila + 1, column=0, columnspan=2, sticky="ew", pady=(4, 12)
        )

        kit.Label(marco, text="Motivo").grid(row=fila + 2, column=0, sticky="w")
        kit.Label(marco, text="Contado por").grid(
            row=fila + 2, column=1, sticky="w", padx=(10, 0)
        )
        kit.Combobox(
            marco, textvariable=self._motivo_var, values=_MOTIVOS_PRESET, width=16
        ).grid(row=fila + 3, column=0, sticky="ew", pady=(4, 12))
        kit.Entry(marco, textvariable=self._contado_por_var, width=16).grid(
            row=fila + 3, column=1, sticky="ew", pady=(4, 12), padx=(10, 0)
        )

        kit.Label(marco, text="Nota (opcional)").grid(
            row=fila + 4, column=0, columnspan=2, sticky="w"
        )
        kit.Entry(marco, textvariable=self._nota_var, width=16).grid(
            row=fila + 5, column=0, columnspan=2, sticky="ew", pady=(4, 18)
        )

        botones = kit.Frame(marco)
        botones.grid(row=fila + 6, column=0, columnspan=2, sticky="e")
        self._btn_registrar = kit.Button(
            botones,
            text="Registrar ajuste",
            icon="clipboard-check",
            command=self._on_registrar,
            style="Success.TButton",
            padding=(18, 10),
        )
        self._btn_registrar.pack(side="right")
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

    def _on_modo(self, modo: str) -> None:
        """Cambia lo que se escribe (total, cuanto suma o cuanto resta)."""
        self._label_cantidad.configure(text=_ETIQUETA_CANTIDAD[modo])
        if self._motivo_var.get() in _MOTIVO_SUGERIDO.values():
            self._motivo_var.set(_MOTIVO_SUGERIDO[modo])
        self._refrescar()
        self._entry_contado.focus_set()

    def _resultante(self) -> tuple[int | None, str | None]:
        """
        La existencia que quedaría (milesimas) segun el modo y lo
        escrito, o un mensaje de error. (None, None): todavia no hay nada
        escrito.
        """
        texto = self._contado_var.get().strip()
        if not texto:
            return None, None
        try:
            cantidad = parse_local_decimal(texto)
        except InvalidOperation:
            return None, "No es un número (ej. 47 o 12,5)."
        if cantidad < 0:
            return None, "La cantidad no puede ser negativa."
        milli = to_milli(cantidad)
        modo = self._modo_var.get()
        if modo == _MODO_SUMAR:
            return self._existencia_milli + milli, None
        if modo == _MODO_RESTAR:
            return self._existencia_milli - milli, None
        return milli, None

    def _refrescar(self, *_args: object) -> None:
        resultante, error = self._resultante()
        if error is not None:
            self._mensaje_var.set(error)
            self._label_mensaje.configure(style="Error.TLabel")
        elif resultante is None:
            self._mensaje_var.set("")
            self._label_mensaje.configure(style="Muted.TLabel")
        else:
            texto, estilo = mensaje_de_delta(resultante - self._existencia_milli)
            if resultante < 0 <= self._existencia_milli:
                texto += ": quedaría en negativo."
            self._mensaje_var.set(texto)
            self._label_mensaje.configure(style=estilo)
        if resultante is None:
            self._queda_var.set("-")
        else:
            self._queda_var.set(f"{format_quantity(resultante)} {self._unidad}")

    def _avisar_error(self, mensaje: str) -> None:
        self._mensaje_var.set(mensaje)
        self._label_mensaje.configure(style="Error.TLabel")

    def _on_registrar(self) -> None:
        resultante, error = self._resultante()
        if error is not None:
            self._avisar_error(error)
            return
        if resultante is None:
            self._avisar_error("Escribe la cantidad (ej. 47 o 12,5).")
            return
        try:
            self._stock.ajustar(
                product_id=self._product_id,
                counted_milli=resultante,
                reason=self._motivo_var.get(),
                counted_by=self._contado_por_var.get().strip() or None,
                note=self._nota_var.get().strip() or None,
            )
        except (DomainError, ValueError) as error:
            self._avisar_error(str(error))
            return
        self._recuerdo["motivo"] = self._motivo_var.get()
        self._recuerdo["contado_por"] = self._contado_por_var.get().strip()
        self.resultado = resultante
        self.destroy()


class HistorialProductoDialog(kit.Dialog):
    """
    Historial COMPLETO de un producto (StockService.history_for): no
    solo lo contado en esta sesion ("Ajustes de esta sesion", que se
    pierde al cerrar la pantalla), sino TODO movimiento de
    inventory_movements -ventas, ajustes, carga inicial, reversiones por
    cancelacion- con todos los datos que se guardan de cada uno: fecha,
    tipo, cantidad, origen, motivo, quien conto y la nota. Solo lectura:
    esto es un visor, no se escribe nada aca (regla 3, append-only).
    """

    def __init__(
        self,
        master: tk.Misc,
        producto: Product,
        movimientos: list[InventoryMovement],
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(f"Historial de {producto.sku} - {producto.name}")
        self.transient(master)
        self.resizable(True, True)
        self.bind("<Escape>", lambda _e: self.destroy())

        frame = kit.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        kit.Label(
            frame, text=f"{producto.sku} - {producto.name}", style="Heading.TLabel"
        ).pack(anchor="w")
        kit.Label(
            frame,
            text=(
                f"Stock actual: {format_quantity(producto.current_stock)} "
                f"{producto.unit}"
            ),
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 12))

        if not movimientos:
            # El caso mas comun hoy: los 1.936 productos migrados
            # entraron en 0 (no hubo importador de stock inicial, ver
            # CLAUDE.md) y todavia nadie los conto.
            kit.Label(
                frame,
                text="Todavía no hay movimientos registrados para este producto.",
                style="Muted.TLabel",
            ).pack(anchor="w")
        else:
            self._build_tabla(frame, movimientos)

        kit.Button(frame, text="Cerrar", command=self.destroy).pack(
            anchor="e", pady=(12, 0)
        )

        self.lift()
        self.focus_force()
        self.grab_set()

    def _build_tabla(
        self, parent: kit.Frame, movimientos: list[InventoryMovement]
    ) -> None:
        tabla = kit.Frame(parent)
        tabla.pack(fill="both", expand=True)
        # height=16: una pantalla completa de movimientos sin scrollear.
        # Con mas, esta la barra vertical - no hay reparto de ancho
        # adaptable (ver widgets.lines_tree) porque estas columnas, a
        # diferencia de las de una factura, son todas de ancho fijo.
        tree = ttk.Treeview(
            tabla,
            columns=[c[0] for c in _COLUMNAS_HISTORIAL],
            show="headings",
            height=16,
        )
        for clave, encabezado, ancho, anchor in _COLUMNAS_HISTORIAL:
            tree.heading(clave, text=encabezado)
            tree.column(clave, width=ancho, anchor=anchor)
        scrollbar = kit.Scrollbar(tabla, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tree_historial = tree

        for movimiento in movimientos:
            tree.insert("", "end", values=self._fila(movimiento))

    @staticmethod
    def _fila(movimiento: InventoryMovement) -> tuple:
        signo = "+" if movimiento.quantity_milli > 0 else "-"
        return (
            movimiento.created_at.strftime("%d/%m/%Y %H:%M")
            if movimiento.created_at
            else "-",
            _TIPO_DISPLAY.get(movimiento.type, movimiento.type.value),
            f"{signo}{format_quantity(abs(movimiento.quantity_milli))}",
            _ORIGEN_DISPLAY.get(movimiento.reference_type, "-")
            if movimiento.reference_type
            else "-",
            movimiento.reason or "-",
            movimiento.counted_by or "-",
            movimiento.note or "-",
        )
