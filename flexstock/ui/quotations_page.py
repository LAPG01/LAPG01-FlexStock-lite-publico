"""
Pestana Cotizaciones (solo con Modo avanzado): notas de entrega /
facturas informales con el Precio 3 de cada producto en tres monedas.

    $        precio del catalogo
    Bs       $ x tasa propia
    $ BCV    Bs / tasa BCV

Mismo flujo que Facturas (borrador en memoria, "Crear" escribe todo de
una vez, se anulan en vez de borrarse), pero NO mueve stock ni aparece
en el cierre de caja - ver services/quotation_service.py.

ui/ solo llama a QuotationService/ProductService/CustomerService.
"""

import tkinter as tk
from collections.abc import Callable
from decimal import Decimal, InvalidOperation
from tkinter import messagebox, ttk

from ..domain.entities import Quotation
from ..domain.enums import InvoiceStatus, QuoteCurrency
from ..domain.errors import DomainError
from ..domain.money import format_money, format_quantity, parse_local_decimal
from ..services.customer_service import CustomerService
from ..services.preview_service import PreviewService
from ..services.product_service import ProductService
from ..services.quotation_service import QuotationService
from ..services.settings_service import SettingsService
from . import columns, kit, simpledialog
from .files import open_in_viewer
from .search import split_search_results
from .theme import find_app_root, paint_window
from .widgets import (
    SearchableTable,
    TextoVacio,
    Tooltip,
    fill_product_listbox,
    fixed_height_frame,
    focus_first_entry,
    lines_tree,
    product_at_listbox_index,
    product_list_min_height,
    product_listbox,
    size_dialog_to_screen,
    wrap_to_width,
)

_ESTADO_DISPLAY = {
    InvoiceStatus.ISSUED: "Emitida",
    InvoiceStatus.CANCELLED: "Anulada",
}

_SIMBOLO = {
    QuoteCurrency.USD: "$",
    QuoteCurrency.BS: " Bs",
    QuoteCurrency.USD_BCV: "$",
}

# En el orden en que se ofrecen: primero los bolivares, porque las otras dos
# se explican a partir de ellos.
_MONEDA_OPCIONES = (
    (QuoteCurrency.BS, "Bolívares"),
    (QuoteCurrency.USD_BCV, "Dólares a tasa BCV"),
    (QuoteCurrency.USD, "Dólares de lista"),
)
# La que viene marcada la primera vez (despues, la ultima elegida).
_MONEDA_POR_DEFECTO = QuoteCurrency.USD_BCV

# Encabezado de cada columna del panel de totales (QuotationDialog).
_MONEDA_TITULOS = {
    QuoteCurrency.USD: "$",
    QuoteCurrency.BS: "Bs",
    QuoteCurrency.USD_BCV: "$ BCV",
}

# Líneas de Nueva cotizacion: (columna, titulo, ancho). El precio y el
# total de cada linea en las tres monedas.
_COLUMNAS_LINEAS = (
    ("sku", "SKU", 90),
    ("nombre", "Nombre", 200),
    ("cantidad", "Cant.", 70),
    ("precio_usd", "Precio unit. $", 140),
    ("precio_bs", "Precio unit. Bs", 150),
    ("precio_bcv", "Precio unit. $ BCV", 180),
    ("total_usd", "Subtotal $", 115),
    ("total_bs", "Subtotal Bs", 130),
    ("total_bcv", "Subtotal $ BCV", 155),
    ("quitar", "", 44),
)
# identify_column() devuelve "#N" (1-based) de la columna clickeada.
_COLUMNA_QUITAR = f"#{len(_COLUMNAS_LINEAS)}"

# Que columnas se ven las decide ui/columns.py + Configuracion >
# Personalizacion; aca solo vive el texto de cada celda (_valores_de).
MODULO = columns.QUOTATIONS


def _monto(cents: int | None, currency: QuoteCurrency) -> str:
    return "-" if cents is None else format_money(cents, _SIMBOLO[currency])


def _tasa(rate: Decimal | None) -> str:
    if rate is None:
        return "sin cargar"
    return str(rate.quantize(Decimal("0.01"))).replace(".", ",")


def pedir_moneda(
    parent: tk.Misc, quotation: Quotation | None = None
) -> QuoteCurrency | None:
    """Pregunta en que moneda generar el PDF. None si se cancela."""
    dialogo = CurrencyChoiceDialog(parent, quotation)
    parent.wait_window(dialogo)
    return dialogo.resultado


class QuotationsPage(kit.Frame):
    def __init__(
        self,
        master: tk.Misc,
        quotation_service: QuotationService,
        product_service: ProductService,
        customer_service: CustomerService,
        *,
        settings_service: SettingsService | None = None,
        on_personal_rate_changed: Callable[[], None] | None = None,
        preview_service: PreviewService | None = None,
    ) -> None:
        super().__init__(master)
        self._service = quotation_service
        self._products_svc = product_service
        self._customers_svc = customer_service
        # Solo se le pasa a Nueva cotizacion (boton "Vista previa").
        self._preview_service = preview_service
        # Sin el, la tabla usa las columnas por defecto del catalogo.
        self._settings_service = settings_service
        # Para que la tarjeta "Tasa propia" del encabezado se entere al
        # instante cuando la tasa se cambia desde aca.
        self._on_personal_rate_changed = on_personal_rate_changed
        self._cotizaciones: list[Quotation] = []
        self._columnas = self._columnas_visibles()

        self._tasas_var = tk.StringVar()
        self._build_widgets()
        self._search_var.trace_add("write", lambda *_a: self._aplicar_filtro())
        self.reload()

    def _build_widgets(self) -> None:
        # Sin titulo propio: el encabezado de la ventana ya dice
        # "Cotizaciones" (ver MainWindow._PAGINAS). Queda solo la tasa.
        header = kit.Frame(self, padding=(8, 8, 8, 0))
        header.pack(side="top", fill="x")
        kit.Label(header, textvariable=self._tasas_var).pack(side="left")
        kit.Button(
            header,
            text="Editar tasa propia",
            icon="pencil",
            command=self._on_editar_tasa_propia,
        ).pack(side="left", padx=12)

        self._table = SearchableTable(
            self,
            columns=tuple(c.key for c in self._columnas),
            headings={c.key: c.heading for c in self._columnas},
            column_widths={c.key: c.width for c in self._columnas},
            column_anchors={c.key: c.anchor for c in self._columnas},
            search_label="Buscar (número o cliente):",
            buttons=(
                ("Nueva cotización", self._on_nueva, "Success.TButton"),
                ("Ver detalle", self._on_detalle, "Edit.TButton"),
                ("Generar PDF", self._on_generar_pdf),
                ("Anular", self._on_anular, "Warning.TButton"),
            ),
            on_double_click=self._on_detalle,
        )
        self._table.pack(side="top", fill="both", expand=True)
        self._table.tag_style("anulada", foreground="gray")
        self._search_var = self._table.search_var
        self._tree = self._table.tree

    # ------------------------------------------------------------
    def reload(self) -> None:
        self._tasas_var.set(
            f"Tasa propia: {_tasa(self._service.personal_rate())}   |   "
            f"Tasa BCV: {_tasa(self._service.bcv_rate())}"
        )
        self._cotizaciones = self._service.list_quotations()
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        principales, coincidencias = split_search_results(
            self._cotizaciones,
            self._search_var.get(),
            lambda q: q.display_number,
            lambda q: q.customer_name,
        )
        filtradas = principales + coincidencias
        self._table.set_rows([(str(q.id), self._valores_de(q)) for q in filtradas])
        for q in filtradas:
            if q.is_cancelled:
                self._table.set_row_tag(str(q.id), "anulada")
        self._table.set_status(
            f"Mostrando {len(filtradas)} de {len(self._cotizaciones)} cotizaciones"
        )

    # ------------------------------------------------------------
    # columnas configurables (Configuracion > Personalizacion)
    # ------------------------------------------------------------
    def _columnas_visibles(self) -> tuple[columns.ColumnDef, ...]:
        guardadas = (
            self._settings_service.visible_columns(MODULO)
            if self._settings_service is not None
            else None
        )
        return columns.visible(columns.catalog(MODULO), guardadas)

    def apply_column_settings(self) -> None:
        self._columnas = self._columnas_visibles()
        self._table.set_columns(
            tuple(c.key for c in self._columnas),
            {c.key: c.heading for c in self._columnas},
            {c.key: c.width for c in self._columnas},
            {c.key: c.anchor for c in self._columnas},
        )
        self._aplicar_filtro()

    def _valores_de(self, cotizacion: Quotation) -> tuple:
        celdas = {
            "numero": lambda: cotizacion.display_number,
            "fecha": lambda: (
                cotizacion.issued_at.strftime("%d/%m/%Y")
                if cotizacion.issued_at
                else ""
            ),
            "cliente": lambda: cotizacion.customer_name,
            "estado": lambda: _ESTADO_DISPLAY[cotizacion.status],
            "total_usd": lambda: _monto(
                cotizacion.total_in(QuoteCurrency.USD), QuoteCurrency.USD
            ),
            "total_bs": lambda: _monto(
                cotizacion.total_in(QuoteCurrency.BS), QuoteCurrency.BS
            ),
            "total_bcv": lambda: _monto(
                cotizacion.total_in(QuoteCurrency.USD_BCV), QuoteCurrency.USD_BCV
            ),
        }
        return tuple(celdas[c.key]() for c in self._columnas)

    def _selected_id(self, titulo: str) -> int | None:
        iid = self._table.selected_iid()
        if not iid:
            messagebox.showinfo(
                titulo, "Selecciona una cotización primero.", parent=self
            )
            return None
        return int(iid)

    # ------------------------------------------------------------
    def _on_editar_tasa_propia(self) -> None:
        actual = self._service.personal_rate()
        texto = simpledialog.askstring(
            "Tasa propia",
            "Tasa propia (Bs. por USD):",
            initialvalue=_tasa(actual) if actual else "",
            parent=self,
        )
        if texto is None:
            return
        try:
            tasa = parse_local_decimal(texto).quantize(Decimal("0.01"))
            self._service.set_personal_rate(tasa)
        except InvalidOperation:
            messagebox.showerror(
                "Tasa propia", "Ingresa un número válido, ej: 95,50", parent=self
            )
            return
        except ValueError as error:
            messagebox.showerror("Tasa propia", str(error), parent=self)
            return
        self.reload()
        if self._on_personal_rate_changed is not None:
            self._on_personal_rate_changed()

    def _on_nueva(self) -> None:
        dialogo = QuotationDialog(
            self,
            self._service,
            self._products_svc,
            self._customers_svc,
            preview_service=self._preview_service,
        )
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_detalle(self) -> None:
        quotation_id = self._selected_id("Ver detalle")
        if quotation_id is None:
            return
        cotizacion = self._service.get_quotation(quotation_id)
        if cotizacion is None:
            messagebox.showerror(
                "Ver detalle", "La cotización ya no existe.", parent=self
            )
            return
        QuotationDetailDialog(self, cotizacion, self._service)

    def _on_generar_pdf(self) -> None:
        quotation_id = self._selected_id("Generar PDF")
        if quotation_id is None:
            return
        generar_pdf(self, self._service, quotation_id)

    def _on_anular(self) -> None:
        quotation_id = self._selected_id("Anular")
        if quotation_id is None:
            return
        motivo = simpledialog.askstring(
            "Anular cotización", "Motivo de la anulación:", parent=self
        )
        if motivo is None:
            return
        try:
            self._service.cancelar(quotation_id, motivo)
        except (ValueError, DomainError) as error:
            messagebox.showerror("Anular cotización", str(error), parent=self)
            return
        self.reload()


def generar_pdf(parent: tk.Misc, service: QuotationService, quotation_id: int) -> None:
    moneda = pedir_moneda(parent, service.get_quotation(quotation_id))
    if moneda is None:
        return
    try:
        ruta = service.generate_pdf(quotation_id, moneda)
    except (ValueError, OSError) as error:
        messagebox.showerror("Generar PDF", str(error), parent=parent)
        return
    messagebox.showinfo("Generar PDF", f"PDF generado en:\n{ruta}", parent=parent)


class CurrencyChoiceDialog(kit.Dialog):
    """
    En que moneda sale el PDF de una nota de entrega. Cada opcion muestra
    el TOTAL que va a imprimir y de donde sale, para no tener que adivinar
    que es "dolares a tasa BCV": antes eran tres nombres solos y habia que
    saber de memoria cual era cual. Recuerda la ultima eleccion mientras la
    app siga abierta (lo normal es usar siempre la misma).
    """

    def __init__(self, master: tk.Misc, quotation: Quotation | None = None) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Moneda del PDF")
        self.transient(master)
        self.resizable(False, False)
        self.resultado: QuoteCurrency | None = None
        raiz = find_app_root(self)
        anterior = getattr(raiz, "_flex_moneda_pdf", _MONEDA_POR_DEFECTO)
        self._moneda_var = tk.StringVar(value=anterior.value)

        frame = kit.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)
        kit.Label(
            frame, text="¿En qué moneda va la nota de entrega?", style="Heading.TLabel"
        ).pack(anchor="w", pady=(0, 10))
        for moneda, texto in _MONEDA_OPCIONES:
            opcion = kit.Frame(frame, style="Card.TFrame", padding=(12, 8))
            opcion.pack(fill="x", pady=(0, 8))
            fila = kit.Frame(opcion)
            fila.pack(fill="x")
            kit.Radiobutton(
                fila, text=texto, value=moneda.value, variable=self._moneda_var
            ).pack(side="left")
            if quotation is not None:
                kit.Label(
                    fila,
                    text=_monto(quotation.total_in(moneda), moneda),
                    style="Bold.TLabel",
                ).pack(side="right")
            kit.Label(
                opcion,
                text=_explicacion_moneda(moneda, quotation),
                style="Muted.TLabel",
                # A la letra de ahora: a 150 % un ancho fijo partia el texto
                # en cinco renglones angostos.
                wraplength=round(440 * getattr(raiz, "_flex_font_scale", 100) / 100),
                justify="left",
            ).pack(anchor="w", padx=(30, 0))
            # Toda la tarjeta elige, no solo el circulo.
            self._atar_eleccion(opcion, moneda)

        botones = kit.Frame(frame)
        botones.pack(fill="x", pady=(8, 0))
        kit.Button(
            botones,
            text="Generar PDF",
            command=self._on_generar,
            style="Success.TButton",
        ).pack(side="right")
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="right", padx=(0, 8)
        )

        self.bind("<Escape>", lambda _evento: self.destroy())
        self.bind("<Return>", lambda _evento: self._on_generar())
        self.update_idletasks()
        x = master.winfo_rootx() + (master.winfo_width() - self.winfo_reqwidth()) // 2
        y = master.winfo_rooty() + (master.winfo_height() - self.winfo_reqheight()) // 3
        self._posicion_inicial = (max(x, 0), max(y, 0))
        self.geometry(f"+{max(x, 0)}+{max(y, 0)}")
        self.lift()
        self.focus_force()
        self.grab_set()

    def _atar_eleccion(self, widget: tk.Misc, moneda: QuoteCurrency) -> None:
        widget.bind(
            "<Button-1>", lambda _e: self._moneda_var.set(moneda.value), add="+"
        )
        for hijo in widget.winfo_children():
            self._atar_eleccion(hijo, moneda)

    def _on_generar(self) -> None:
        self.resultado = QuoteCurrency(self._moneda_var.get())
        find_app_root(self)._flex_moneda_pdf = self.resultado
        self.destroy()


def _explicacion_moneda(moneda: QuoteCurrency, quotation: Quotation | None) -> str:
    """De donde sale cada total, con las tasas congeladas en la nota."""
    propia = _tasa(quotation.personal_rate) if quotation is not None else ""
    bcv = _tasa(quotation.bcv_rate) if quotation is not None else ""
    if moneda is QuoteCurrency.BS:
        detalle = f", a tu tasa propia ({propia})" if propia else ""
        return f"Lo que el cliente paga en bolívares{detalle}."
    if moneda is QuoteCurrency.USD_BCV:
        detalle = f" ({bcv})" if bcv else ""
        return f"Esos mismos bolívares en dólares, a la tasa oficial BCV{detalle}."
    return "El precio del catálogo en dólares, sin ninguna conversión."


class QuotationDialog(kit.Dialog):
    """
    Formulario de alta. Borrador en memoria (QuotationService): nada se
    escribe hasta "Crear". Al elegir un producto de la lista muestra su
    Precio 3 en las tres monedas antes de agregarlo.
    """

    def __init__(
        self,
        master: tk.Misc,
        quotation_service: QuotationService,
        product_service: ProductService,
        customer_service: CustomerService,
        *,
        preview_service: PreviewService | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Nueva cotización / nota de entrega")
        self.transient(master)
        self.resizable(True, True)

        self._service = quotation_service
        # Opcional: sin el no hay boton "Vista previa".
        self._preview = preview_service
        self.guardado = False
        self._quotation: Quotation | None = None

        # Catalogo con precios cargados en lote (una consulta IN, ver
        # ProductService.list_products): filtrar y previsualizar precios
        # en memoria, sin ir a SQLite por tecla ni por click.
        self._productos = product_service.list_products()
        self._productos_filtrados = list(self._productos)
        self._coincidencias_inicio: int | None = None
        self._clientes = customer_service.list_customers()

        self._modo_cliente = tk.StringVar(value="directo")
        self._nombre_directo_var = tk.StringVar()
        self._buscar_producto_var = tk.StringVar()
        self._cantidad_var = tk.StringVar(value="1")
        self._preview_var = tk.StringVar(
            value="Elige un producto para ver su Precio 3."
        )
        self._tasas_var = tk.StringVar(value="")
        # Desmarcado por defecto, igual que en Nueva factura: una nota de
        # entrega normalmente no lleva IVA, se tilda a proposito cuando
        # si (_on_iniciar se lo pasa al borrador).
        self._cobrar_iva_var = tk.BooleanVar(value=False)
        # Panel de totales: una columna por moneda, tres filas.
        self._subtotal_vars = {c: tk.StringVar(value="-") for c in QuoteCurrency}
        self._iva_titulo_var = tk.StringVar(value="IVA")
        self._iva_vars = {c: tk.StringVar(value="-") for c in QuoteCurrency}
        self._totales_vars = {c: tk.StringVar(value="-") for c in QuoteCurrency}

        self._build_form()
        self._buscar_producto_var.trace_add("write", self._on_filtrar_productos)
        self.bind("<Escape>", lambda _evento: self._on_cerrar())
        self.protocol("WM_DELETE_WINDOW", self._on_cerrar)
        focus_first_entry(self)
        size_dialog_to_screen(self)
        self.lift()
        self.focus_force()
        self.grab_set()

    # ------------------------------------------------------------
    def _build_form(self) -> None:
        """
        Mismo reparto de pantalla que Nueva factura (ver billing_page):
        arriba cliente y datos, en el medio el buscador de productos a
        la izquierda y las lineas a la derecha, abajo los botones y el
        panel de totales. Las funciones NO se mezclan: una cotizacion
        no mueve stock y muestra sus tres monedas propias.

        Adaptable: si la ventana queda mas baja de lo que pide el
        contenido, Tk le quita espacio a lo ULTIMO que se empaco. Por eso
        "abajo" (totales y botones) se empaca antes que "medio": lo que
        se encoge es la parte del medio (lista y tabla, que tienen barra
        de desplazamiento) y los totales nunca quedan recortados.
        """
        frame = kit.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        arriba = kit.Frame(frame)
        arriba.pack(side="top", fill="x", pady=(0, 12))
        abajo = kit.Frame(frame)
        abajo.pack(side="bottom", fill="x")
        medio = kit.Frame(frame)
        medio.pack(side="top", fill="both", expand=True, pady=(0, 12))
        # Productos (izquierda) y lineas (derecha) en cuadricula: lo que
        # falte o sobre de ancho se reparte 1:3, y la lista nunca baja
        # de 320 px.
        medio.columnconfigure(0, weight=1, minsize=320)
        medio.columnconfigure(1, weight=3)
        medio.rowconfigure(0, weight=1)

        self._build_cliente_section(arriba)
        self._build_datos_section(arriba)
        self._build_productos_section(medio)
        self._build_lineas_section(medio)
        self._build_totales_section(abajo)

        botones = kit.Frame(abajo)
        botones.pack(side="left", anchor="sw")
        self._btn_crear = kit.Button(
            botones,
            text="Crear",
            command=self._on_crear,
            style="Success.TButton",
            state="disabled",
            padding=(24, 10),
        )
        self._btn_crear.pack(side="left")
        kit.Button(
            botones, text="Cerrar", command=self._on_cerrar, padding=(16, 10)
        ).pack(side="left", padx=(12, 0))
        if self._preview is not None:
            # Con las demas acciones, igual que en Nueva factura: muestra
            # el borrador como COTIZACION en PDF, sin crearla (ver
            # PreviewService).
            # Con su icono, apagado hasta que haya algo que mostrar, y un
            # cartelito que dice que NO guarda: "vista previa" solo no
            # aclaraba si ya quedaba registrada.
            self._btn_vista_previa = kit.Button(
                botones,
                text="Vista previa (PDF)",
                icon="eye",
                command=self._on_vista_previa,
                padding=(16, 10),
                state="disabled",
            )
            self._btn_vista_previa.pack(side="left", padx=(12, 0))
            Tooltip(
                self._btn_vista_previa,
                lambda: (
                    "Abre el borrador en PDF para verlo o mandarlo. No guarda nada ni gasta un número."
                ),
            )

    def _build_cliente_section(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(
            parent, text="Cliente", padding=12, title_style="CardTitle.TLabel"
        )
        seccion.pack(side="left", fill="both", expand=True, padx=(0, 12))

        kit.Radiobutton(
            seccion, text="Nombre libre", variable=self._modo_cliente, value="directo"
        ).grid(row=0, column=0, sticky="w")
        kit.Entry(seccion, textvariable=self._nombre_directo_var, width=42).grid(
            row=0, column=1, sticky="ew", padx=4
        )
        kit.Radiobutton(
            seccion,
            text="Cliente registrado",
            variable=self._modo_cliente,
            value="registrado",
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))
        self._cliente_combo = kit.Combobox(
            seccion,
            state="readonly",
            width=40,
            values=[f"{c.sku} - {c.name}" for c in self._clientes],
        )
        self._cliente_combo.grid(row=1, column=1, sticky="ew", padx=4, pady=(4, 0))

        self._btn_iniciar = kit.Button(
            seccion,
            text="Iniciar cotización",
            style="Success.TButton",
            command=self._on_iniciar,
            padding=(16, 8),
        )
        self._btn_iniciar.grid(row=0, column=2, rowspan=2, sticky="ns", padx=(12, 0))
        seccion.columnconfigure(1, weight=1)

    def _build_datos_section(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(
            parent,
            text="Datos de la cotización",
            padding=12,
            title_style="CardTitle.TLabel",
        )
        seccion.pack(side="left", fill="both")

        # Las dos tasas se congelan al crear el borrador (Regla 5), asi
        # que aca se muestran, no se editan: se cambian desde la tarjeta
        # "Tasa propia" del encabezado.
        kit.Label(seccion, textvariable=self._tasas_var, style="Muted.TLabel").pack(
            side="top", anchor="w"
        )
        kit.Checkbutton(
            seccion,
            text="Cobrar IVA en esta cotización",
            variable=self._cobrar_iva_var,
            command=self._on_toggle_iva,
        ).pack(side="top", anchor="w", pady=(8, 0))

    def _build_productos_section(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(
            parent,
            text="Agregar producto",
            padding=12,
            title_style="CardTitle.TLabel",
        )
        seccion.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        # En cuadricula, y no con pack, para que al faltar alto se encoja
        # SOLO la lista (fila 2): el precio, la cantidad y el boton no se
        # recortan nunca.
        seccion.columnconfigure(0, weight=1)
        # Mismo criterio que Facturas: cede SOLO la lista, hasta tres
        # renglones, y el resto va en las menos filas posibles para que a
        # 150 % no quede nada afuera.
        seccion.rowconfigure(1, weight=1, minsize=product_list_min_height(lines=3))

        buscador_fila = kit.Frame(seccion)
        buscador_fila.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        buscador_fila.columnconfigure(1, weight=1)
        kit.Label(buscador_fila, text="Buscar:").grid(row=0, column=0, sticky="w")
        kit.Entry(buscador_fila, textvariable=self._buscar_producto_var).grid(
            row=0, column=1, sticky="ew", padx=(8, 0)
        )

        lista = kit.Frame(seccion)
        lista.grid(row=1, column=0, sticky="nsew")
        self._lista_productos = product_listbox(lista, height=6, plano=True)
        self._vacio_productos = TextoVacio(
            self._lista_productos,
            "Escribe arriba el nombre o el código del producto para buscarlo.",
        )
        self._lista_productos.bind("<<ListboxSelect>>", self._on_elegir_producto)
        self._poblar_lista_productos()

        # fixed_height_frame: sin alto fijo, un nombre de producto largo
        # pasaba a dos renglones y la ventana se estiraba sola al cambiar
        # de producto (mismo bug que _datos_cliente_label en billing_page).
        marco_preview = fixed_height_frame(seccion, lines=2)
        marco_preview.grid(row=2, column=0, sticky="ew", pady=(8, 0))
        preview = kit.Label(
            marco_preview,
            textvariable=self._preview_var,
            style="Heading.TLabel",
            wraplength=300,
        )
        preview.pack(fill="both", expand=True)
        wrap_to_width(preview)

        # Cantidad y "Agregar linea" en la misma fila: una fila menos.
        agregar = kit.Frame(seccion)
        agregar.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        agregar.columnconfigure(2, weight=1)
        kit.Label(agregar, text="Cant.:").grid(row=0, column=0, sticky="w")
        cantidad = kit.Entry(agregar, textvariable=self._cantidad_var, width=6)
        cantidad.grid(row=0, column=1, sticky="w", padx=(6, 0))

        self._btn_agregar = kit.Button(
            agregar,
            text="Agregar linea",
            style="Success.TButton",
            command=self._on_agregar_linea,
            state="disabled",
            padding=(12, 6),
        )
        self._btn_agregar.grid(row=0, column=2, sticky="ew", padx=(12, 0))

        # Mismos atajos que Nueva factura: del producto a la cantidad, y
        # de la cantidad a agregar la linea. Enter nunca crea el
        # documento.
        self._lista_productos.bind(
            "<Double-Button-1>", lambda _evento: cantidad.focus_set()
        )
        self._lista_productos.bind("<Return>", lambda _evento: cantidad.focus_set())
        cantidad.bind("<Return>", lambda _evento: self._on_agregar_linea())

    def _build_lineas_section(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(
            parent,
            text="Líneas de la cotización",
            padding=12,
            title_style="CardTitle.TLabel",
        )
        seccion.grid(row=0, column=1, sticky="nsew")

        # Se adapta al ancho que le toque (ver widgets.lines_tree).
        self._lineas_tree = lines_tree(seccion, _COLUMNAS_LINEAS, plano=True)
        self._vacio_lineas = TextoVacio(
            self._lineas_tree,
            "Aún no hay líneas. Busca un producto a la izquierda, pon la cantidad y "
            "pulsa «Agregar línea».",
        )
        self._vacio_lineas.mostrar(True)
        self._lineas_tree.bind("<Button-1>", self._on_click_lineas)

    def _build_totales_section(self, parent: kit.Frame) -> None:
        """Una columna por moneda ($, Bs, $ BCV) y tres filas:
        subtotal, IVA y total. Mismo panel que Nueva factura."""
        panel = kit.Frame(parent, style="Card.TFrame", padding=(20, 12))
        panel.pack(side="right", anchor="se", padx=(12, 0))

        for columna, moneda in enumerate(QuoteCurrency, start=1):
            kit.Label(
                panel, text=_MONEDA_TITULOS[moneda], style="CardTitle.TLabel"
            ).grid(row=0, column=columna, sticky="e", padx=(24, 0))

        filas = (
            ("Base imponible", self._subtotal_vars),
            (self._iva_titulo_var, self._iva_vars),
        )
        for fila, (titulo, variables) in enumerate(filas, start=1):
            etiqueta = kit.Label(panel, style="CardText.TLabel")
            if isinstance(titulo, tk.StringVar):
                etiqueta.configure(textvariable=titulo)
            else:
                etiqueta.configure(text=titulo)
            etiqueta.grid(row=fila, column=0, sticky="w", pady=(6, 0))
            for columna, moneda in enumerate(QuoteCurrency, start=1):
                kit.Label(
                    panel, textvariable=variables[moneda], style="CardText.TLabel"
                ).grid(row=fila, column=columna, sticky="e", padx=(24, 0), pady=(6, 0))

        ttk.Separator(panel).grid(row=3, column=0, columnspan=4, sticky="ew", pady=8)
        kit.Label(panel, text="TOTAL", style="CardValue.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        for columna, moneda in enumerate(QuoteCurrency, start=1):
            kit.Label(
                panel, textvariable=self._totales_vars[moneda], style="CardValue.TLabel"
            ).grid(row=4, column=columna, sticky="e", padx=(24, 0))

    # ------------------------------------------------------------
    def _on_iniciar(self) -> None:
        try:
            if self._modo_cliente.get() == "registrado":
                indice = self._cliente_combo.current()
                if indice < 0:
                    raise ValueError("Selecciona un cliente de la lista.")
                self._quotation = self._service.crear_borrador(
                    customer_id=self._clientes[indice].id
                )
            else:
                self._quotation = self._service.crear_borrador(
                    customer_name=self._nombre_directo_var.get()
                )
        except ValueError as error:
            messagebox.showerror("Iniciar cotización", str(error), parent=self)
            return

        self._service.set_cobrar_iva(self._quotation, self._cobrar_iva_var.get())
        self._btn_iniciar.configure(state="disabled")
        self._cliente_combo.configure(state="disabled")
        self._btn_agregar.configure(state="normal")
        self._btn_crear.configure(state="normal")
        self._tasas_var.set(
            f"Tasa propia: {_tasa(self._quotation.personal_rate)}   |   "
            f"Tasa BCV: {_tasa(self._quotation.bcv_rate)}"
        )
        self._refrescar_lineas()

    def _poblar_lista_productos(self) -> None:
        fill_product_listbox(
            self._lista_productos,
            [
                (
                    format_quantity(p.current_stock),
                    f"{p.sku} - {p.name}",
                    p.current_stock > 0,
                )
                for p in self._productos_filtrados
            ],
            coincidences_at=self._coincidencias_inicio,
        )
        vacio = getattr(self, "_vacio_productos", None)
        if vacio is not None:
            vacio.mostrar(not self._productos_filtrados)

    def _on_filtrar_productos(self, *_args: object) -> None:
        principales, coincidencias = split_search_results(
            self._productos,
            self._buscar_producto_var.get(),
            lambda p: p.name,
            lambda p: p.sku,
        )
        self._productos_filtrados = principales + coincidencias
        self._coincidencias_inicio = len(principales) if coincidencias else None
        self._poblar_lista_productos()

    def _on_elegir_producto(self, _evento: object = None) -> None:
        seleccion = self._lista_productos.curselection()
        if not seleccion:
            return
        producto = product_at_listbox_index(
            self._productos_filtrados, seleccion[0], self._coincidencias_inicio
        )
        if producto is None:
            self._preview_var.set("Selecciona un producto, no el titulo.")
            return
        precios = self._service.price_preview(producto)
        if precios is None:
            self._preview_var.set(f"{producto.name}: sin Precio 3 cargado.")
            return
        self._preview_var.set(
            f"Precio 3:  {_monto(precios[QuoteCurrency.USD], QuoteCurrency.USD)}"
            f"   |   {_monto(precios[QuoteCurrency.BS], QuoteCurrency.BS)}"
            f"   |   {_monto(precios[QuoteCurrency.USD_BCV], QuoteCurrency.USD_BCV)}"
            " BCV"
        )

    def _on_agregar_linea(self) -> None:
        if self._quotation is None:
            return
        seleccion = self._lista_productos.curselection()
        if not seleccion:
            messagebox.showinfo(
                "Agregar linea", "Selecciona un producto de la lista.", parent=self
            )
            return
        producto = product_at_listbox_index(
            self._productos_filtrados, seleccion[0], self._coincidencias_inicio
        )
        if producto is None:
            messagebox.showinfo(
                "Agregar linea", "Selecciona un producto, no el titulo.", parent=self
            )
            return
        try:
            cantidad = parse_local_decimal(self._cantidad_var.get() or "")
            self._service.agregar_linea(
                self._quotation, product_id=producto.id, quantity=cantidad
            )
        except InvalidOperation:
            messagebox.showerror(
                "Agregar linea",
                "La cantidad debe ser un número (ej. 2,5).",
                parent=self,
            )
            return
        except ValueError as error:
            messagebox.showerror("Agregar linea", str(error), parent=self)
            return
        self._cantidad_var.set("1")
        self._refrescar_lineas()

    def _refrescar_lineas(self) -> None:
        q = self._quotation
        self._lineas_tree.delete(*self._lineas_tree.get_children())
        for indice, line in enumerate(q.lines):
            self._lineas_tree.insert(
                "",
                "end",
                iid=str(indice),
                values=(
                    line.product_sku,
                    line.product_name,
                    format_quantity(line.quantity_milli),
                    *(_monto(q.unit_price_in(line, c), c) for c in QuoteCurrency),
                    *(_monto(q.line_total_in(line, c), c) for c in QuoteCurrency),
                    "❌",
                ),
            )
        self._refrescar_totales()
        self._vacio_lineas.mostrar(not q.lines)
        boton = getattr(self, "_btn_vista_previa", None)
        if boton is not None:
            boton.configure(state="normal" if q.lines else "disabled")

    def _refrescar_totales(self) -> None:
        q = self._quotation
        self._iva_titulo_var.set(f"IVA ({q.tax_rate}%)")
        for moneda in QuoteCurrency:
            subtotal, iva, total = q.totals_in(moneda)
            self._subtotal_vars[moneda].set(_monto(subtotal, moneda))
            self._iva_vars[moneda].set(_monto(iva, moneda))
            self._totales_vars[moneda].set(_monto(total, moneda))

    def _on_click_lineas(self, evento: tk.Event) -> None:
        if self._lineas_tree.identify_region(evento.x, evento.y) != "cell":
            return
        if self._lineas_tree.identify_column(evento.x) != _COLUMNA_QUITAR:
            return
        fila = self._lineas_tree.identify_row(evento.y)
        if fila:
            self._service.quitar_linea(self._quotation, int(fila))
            self._refrescar_lineas()

    def _on_vista_previa(self) -> None:
        """
        Abre en el visor de PDF el borrador como COTIZACION: los Bs y los
        $ a tasa BCV, nunca el $ del catalogo. No crea la cotizacion ni
        gasta un numero NE.
        """
        if self._quotation is None or not self._quotation.lines:
            messagebox.showinfo(
                "Vista previa",
                "Inicia la cotización y agrega al menos una línea.",
                parent=self,
            )
            return
        try:
            open_in_viewer(self._preview.quotation_pdf(self._quotation))
        except (ValueError, OSError) as error:
            messagebox.showerror("Vista previa", str(error), parent=self)

    def _on_toggle_iva(self) -> None:
        if self._quotation is None:
            return
        self._service.set_cobrar_iva(self._quotation, self._cobrar_iva_var.get())
        self._refrescar_totales()

    # ------------------------------------------------------------
    def _on_cerrar(self) -> None:
        if (
            self._quotation is not None
            and self._quotation.lines
            and not messagebox.askyesno(
                "Cerrar",
                f"Hay {len(self._quotation.lines)} linea(s) sin crear. "
                "¿Cerrar igual? Se van a perder.",
                parent=self,
            )
        ):
            return
        self.destroy()

    def _on_crear(self) -> None:
        if self._quotation is None or not self._quotation.lines:
            messagebox.showinfo("Crear", "Agrega al menos una linea.", parent=self)
            return
        try:
            self._service.confirmar(
                self._quotation, cobrar_iva=self._cobrar_iva_var.get()
            )
        except (ValueError, DomainError) as error:
            messagebox.showerror("Crear", str(error), parent=self)
            return

        self.guardado = True
        numero = self._quotation.display_number
        if messagebox.askyesno(
            "Nota de entrega creada",
            f"{numero} creada. ¿Generar el PDF ahora?",
            parent=self,
        ):
            generar_pdf(self, self._service, self._quotation.id)
        self.destroy()


class QuotationDetailDialog(kit.Dialog):
    """Vista de solo lectura de una cotizacion ya creada."""

    def __init__(
        self,
        master: tk.Misc,
        quotation: Quotation,
        quotation_service: QuotationService | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(f"Cotización {quotation.display_number}")
        self.transient(master)
        self.resizable(True, True)
        self.bind("<Escape>", lambda _evento: self.destroy())
        # Opcional (igual criterio que "Crear factura" en ProductsPage):
        # sin el, este dialogo queda de solo lectura, sin el boton de PDF.
        self._service = quotation_service
        self._quotation = quotation

        frame = kit.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        kit.Label(frame, text=f"Cliente: {quotation.customer_name}").pack(anchor="w")
        kit.Label(frame, text=f"Estado: {_ESTADO_DISPLAY[quotation.status]}").pack(
            anchor="w"
        )
        if quotation.is_cancelled:
            kit.Label(
                frame, text=f"Motivo de anulación: {quotation.cancel_reason or ''}"
            ).pack(anchor="w")
        kit.Label(
            frame,
            text=f"Tasa propia: {_tasa(quotation.personal_rate)}   |   "
            f"Tasa BCV: {_tasa(quotation.bcv_rate)}",
        ).pack(anchor="w")

        tree = ttk.Treeview(
            frame,
            columns=("nombre", "cantidad", "total_usd", "total_bs", "total_bcv"),
            show="headings",
            height=8,
        )
        for col, texto in (
            ("nombre", "Producto"),
            ("cantidad", "Cantidad"),
            ("total_usd", "Subtotal $"),
            ("total_bs", "Subtotal Bs"),
            ("total_bcv", "Subtotal $ BCV"),
        ):
            tree.heading(col, text=texto)
        tree.column("nombre", width=260)
        for line in quotation.lines:
            tree.insert(
                "",
                "end",
                values=(
                    line.product_name,
                    format_quantity(line.quantity_milli),
                    *(
                        _monto(quotation.line_total_in(line, c), c)
                        for c in QuoteCurrency
                    ),
                ),
            )
        tree.pack(side="top", fill="both", expand=True, pady=(8, 8))

        for moneda in QuoteCurrency:
            subtotal, iva, total = quotation.totals_in(moneda)
            kit.Label(
                frame,
                text=f"Subtotal {_monto(subtotal, moneda)}   "
                f"IVA ({quotation.tax_rate}%) {_monto(iva, moneda)}   "
                f"Total {_monto(total, moneda)}",
            ).pack(anchor="e")

        botones = kit.Frame(frame)
        botones.pack(side="top", pady=(8, 0))
        if self._service is not None:
            kit.Button(botones, text="Generar PDF", command=self._on_generar_pdf).pack(
                side="left", padx=4
            )
        kit.Button(botones, text="Cerrar", command=self.destroy).pack(
            side="left", padx=4
        )
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.lift()
        self.focus_force()
        self.grab_set()

    def _on_generar_pdf(self) -> None:
        generar_pdf(self, self._service, self._quotation.id)
