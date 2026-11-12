"""
Pagina de facturacion: listado de facturas + alta (formulario con lineas
en vivo) + detalle + cancelacion.

El BORRADOR de una factura nueva vive entero en memoria, adentro de
InvoiceDialog (ver services/billing_service.py): nada se escribe en la
base hasta que se aprieta "Confirmar factura". Cerrar el dialogo antes
de confirmar no deja rastro.

ui/ solo llama a BillingService/ProductService/CustomerService: nunca
importa nada de flexstock.data.
"""

import tkinter as tk
from datetime import datetime
from decimal import Decimal, InvalidOperation
from tkinter import messagebox, ttk

from ..domain.bs_breakdown import BsBreakdown, BsLine, breakdown, catalog_total
from ..domain.entities import Invoice, InvoiceLine
from ..domain.enums import InvoiceStatus, PaymentMethod
from ..domain.errors import DomainError
from ..domain.money import (
    convert_cents,
    format_money,
    format_quantity,
    from_milli,
    parse_local_decimal,
    to_cents,
)
from ..services.billing_service import BillingService
from ..services.customer_service import CustomerService
from ..services.preview_service import PreviewService
from ..services.product_service import ProductService
from ..services.rate_display_service import (
    BCV as RATE_BCV,
)
from ..services.rate_display_service import (
    PERSONAL as RATE_PERSONAL,
)
from ..services.rate_display_service import (
    RateDisplayService,
    RateSnapshot,
)
from ..services.settings_service import SettingsService
from . import columns, kit, simpledialog
from .cash_closing_dialog import CashClosingDialog
from .files import open_in_viewer
from .picker import FloatingPicker
from .search import split_search_results
from .theme import paint_window
from .widgets import (
    SearchableTable,
    TextoVacio,
    Tooltip,
    fixed_height_frame,
    focus_first_entry,
    lines_tree,
    size_dialog_to_screen,
    toast,
    wrap_to_width,
)

_ESTADO_DISPLAY = {
    InvoiceStatus.ISSUED: "Confirmada",
    InvoiceStatus.CANCELLED: "Cancelada",
}

# Texto en espanol para cada forma de pago (5 opciones ya decididas con
# el dueno, ver CLAUDE.md - no se agregan mas sin confirmar con el). El
# enum en domain/enums.py es solo el vocabulario cerrado; como se ve en
# pantalla vive aca, mismo criterio que _ESTADO_DISPLAY. Una lista (no
# solo el dict) porque el Combobox necesita un orden estable para poder
# mapear "indice elegido" -> PaymentMethod.
_FORMA_PAGO_DISPLAY = {
    PaymentMethod.EFECTIVO_BS: "Efectivo Bs",
    PaymentMethod.EFECTIVO_USD: "Efectivo USD",
    PaymentMethod.TRANSFERENCIA: "Transferencia / Pago movil",
    PaymentMethod.TARJETA: "Tarjeta (punto de venta)",
    PaymentMethod.ZELLE: "Zelle",
}
_FORMA_PAGO_OPCIONES = list(_FORMA_PAGO_DISPLAY.items())

# Que columnas se ven las decide ui/columns.py + Configuracion >
# Personalizacion; aca solo vive el texto de cada celda (_valores_de).
MODULO = columns.INVOICES

# Líneas de Nueva factura: (columna, titulo, ancho). Montos en $ (lo que
# se guarda) y, a la derecha, en Bs a la tasa elegida como en una
# factura fiscal: sin IVA, el IVA de esa linea y el total.
_COLUMNAS_LINEAS = (
    ("sku", "SKU", 110),
    ("nombre", "Nombre", 240),
    ("cantidad", "Cant.", 70),
    ("precio", "Precio unit. $", 140),
    ("total", "Total $ BCV", 150),
    ("total_bs", "Total Bs", 160),
    ("quitar", "", 44),
)

# Lista de clientes de Nueva factura: se ve a quien se le esta
# facturando (no solo su nombre) antes de emitir.
_COLUMNAS_CLIENTES = (
    ("nombre", "Cliente", 220),
    ("cedula", "Cédula/RIF", 140),
    ("telefono", "Teléfono", 140),
    ("direccion", "Dirección", 220),
)
_SIN_CLIENTE = "Ningún cliente elegido."
# (clave, etiqueta en negrita, ancho del dato en caracteres). Ancho fijo:
# lo que no entra se corta en vez de agrandar el panel (ver
# InvoiceDialog._build_cliente_section).
_CAMPOS_CLIENTE = (
    ("nombre", "Nombre:", 22),
    ("cedula", "Cédula/RIF:", 12),
    ("telefono", "Tel:", 14),
    ("direccion", "Dir:", 22),
)
_SIN_PRODUCTO = "Elige un producto para ver su precio."

# A que tasa se le cobra al cliente (solo con Modo avanzado). Los montos
# de esta pantalla se le muestran al cliente, asi que el segundo nombre
# no dice de donde sale: cambiarlo es cambiar esta linea.
_ETIQUETAS_TASA = {RATE_BCV: "Tasa BCV", RATE_PERSONAL: "Tasa de referencia"}


def valores_de_linea(
    line: InvoiceLine,
    bs: BsLine | None,
    simbolo: str,
) -> tuple[str, ...]:
    """
    Las celdas de UNA linea, en el orden de _COLUMNAS_LINEAS (sin la x
    de quitar). La comparten el formulario de alta y el detalle de una
    factura guardada, para que las dos pantallas muestren lo mismo.

    Los montos en $ ya son a tasa BCV: con Modo avanzado el precio se
    altero al agregar la linea (ver InvoiceDialog._factor_de_precio), y
    sin el son los del catalogo, que se cobran a la BCV igual. 'bs' es
    esa misma linea en bolívares (None = sin tasa cargada).
    """
    return (
        line.product_sku,
        line.product_name,
        format_quantity(line.quantity_milli),
        format_money(line.unit_price_cents, simbolo),
        format_money(line.line_total_cents, simbolo),
        format_money(bs.sin_iva, " Bs") if bs else "-",
    )


def _parse_fecha_hora(fecha_texto: str, hora_texto: str) -> datetime:
    """
    'dd/mm/aaaa' + 'HH:MM' -> datetime. Mismo formato de fecha que el
    resto de la UI (ver format_money/etc en domain/money.py, que usan
    convencion local); separado de esa funcion porque no es plata.
    Lanza ValueError con el texto original de Python (quien llama lo
    traduce a un messagebox, ver InvoiceDialog._on_confirmar) - no hace
    falta un mensaje mas elaborado aca, el caller ya sabe el contexto.
    """
    return datetime.strptime(  # noqa: DTZ007 - hora local, igual que el resto del esquema
        f"{fecha_texto.strip()} {hora_texto.strip()}", "%d/%m/%Y %H:%M"
    )


class BillingPage(kit.Frame):
    """Contenedor autonomo: se puede empotrar en cualquier ventana padre."""

    def __init__(
        self,
        master: tk.Misc,
        billing_service: BillingService,
        product_service: ProductService,
        customer_service: CustomerService,
        *,
        settings_service: SettingsService | None = None,
        rate_display_service: RateDisplayService | None = None,
        preview_service: PreviewService | None = None,
    ) -> None:
        super().__init__(master)
        self._service = billing_service
        self._products_svc = product_service
        self._customers_svc = customer_service
        # Solo se le pasa a Nueva factura (boton "Vista previa").
        self._preview_service = preview_service
        # Opcional: sin el, InvoiceDialog trata Modo avanzado como
        # apagado (ver su propio __init__).
        self._settings_service = settings_service
        # Sin el: sin columnas de total convertido en la tabla y sin
        # selector de tasa en Nueva factura (ver InvoiceDialog).
        self._rates = rate_display_service
        self._tasas: RateSnapshot | None = None
        self._currency_symbol = billing_service.currency_symbol()
        self._facturas: list[Invoice] = []
        self._columnas = self._columnas_visibles()

        self._build_widgets()
        self._search_var.trace_add("write", self._on_buscar)

        self.reload()

    # ------------------------------------------------------------
    # construccion de widgets
    # ------------------------------------------------------------
    def _build_widgets(self) -> None:
        # Encabezado con el nombre de empresa: el PDF (Fase 7) reutiliza
        # el mismo settings.company_name para su membrete.
        header = kit.Frame(self, padding=(8, 8, 8, 0))
        header.pack(side="top", fill="x")
        # La tasa BCV/Euro ya se muestra una sola vez, en el encabezado
        # de MainWindow (visible en cualquier pestana): repetirla aca
        # era la misma informacion dos veces en pantalla.
        self._membrete_var = tk.StringVar(value=self._service.company_name())
        kit.Label(
            header,
            textvariable=self._membrete_var,
            style="Heading.TLabel",
        ).pack(side="left")

        self._table = SearchableTable(
            self,
            columns=tuple(c.key for c in self._columnas),
            headings={c.key: c.heading for c in self._columnas},
            column_widths={c.key: c.width for c in self._columnas},
            column_anchors={c.key: c.anchor for c in self._columnas},
            search_label="Buscar (número, cliente o cédula):",
            buttons=(
                # Mismo verde que "Crear factura" en Productos: es la
                # misma accion (InvoiceDialog), no dos distintas.
                ("Nueva factura", self._on_nueva, "Success.TButton"),
                # Mismo amarillo que "Editar" en las demas paginas: ocupa
                # el mismo lugar en el flujo (elegis una fila, se abre un
                # dialogo) aunque aca sea de solo lectura - las facturas
                # no se editan (regla 4 del CLAUDE.md).
                ("Ver detalle", self._on_detalle, "Edit.TButton"),
                ("Generar PDF", self._on_generar_pdf),
                ("Cierre de caja", self._on_cierre_caja),
                ("Cancelar factura", self._on_cancelar, "Warning.TButton"),
            ),
            on_double_click=self._on_detalle,
        )
        self._table.pack(side="top", fill="both", expand=True)
        self._table.tag_style("cancelada", foreground="gray")

        self._search_var = self._table.search_var
        self._tree = self._table.tree
        self._status = self._table.status

    # ------------------------------------------------------------
    # carga y filtrado (todo en memoria, sin paginacion)
    # ------------------------------------------------------------
    def reload(self) -> None:
        self._facturas = self._service.list_invoices()
        self._aplicar_filtro()

    def _on_buscar(self, *_args: object) -> None:
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        # Numero, nombre o cedula del cliente: escribir "12345678" o
        # "V-1234" encuentra sus facturas sin saber el numero.
        principales, coincidencias = split_search_results(
            self._facturas,
            self._search_var.get(),
            lambda f: f.display_number,
            lambda f: f.customer_name,
            lambda f: f.customer_tax_id,
        )
        filtradas = principales + coincidencias
        self._poblar_tabla(filtradas)

    def _poblar_tabla(self, facturas: list[Invoice]) -> None:
        # Una foto de las tasas por poblada, no una lectura por fila.
        self._tasas = self._rates.snapshot() if self._rates is not None else None
        self._table.set_rows([(str(f.id), self._valores_de(f)) for f in facturas])
        for f in facturas:
            if f.is_cancelled:
                self._table.set_row_tag(str(f.id), "cancelada")
        self._table.set_status(
            f"Mostrando {len(facturas)} de {len(self._facturas)} facturas"
        )

    # ------------------------------------------------------------
    # columnas configurables (Configuracion > Personalizacion)
    # ------------------------------------------------------------
    def _columnas_visibles(self) -> tuple[columns.ColumnDef, ...]:
        if self._settings_service is None:
            guardadas, avanzado = None, False
        else:
            guardadas = self._settings_service.visible_columns(MODULO)
            avanzado = self._settings_service.advanced_mode()
        return columns.visible(columns.catalog(MODULO, advanced=avanzado), guardadas)

    def apply_column_settings(self) -> None:
        self._columnas = self._columnas_visibles()
        self._table.set_columns(
            tuple(c.key for c in self._columnas),
            {c.key: c.heading for c in self._columnas},
            {c.key: c.width for c in self._columnas},
            {c.key: c.anchor for c in self._columnas},
        )
        self._aplicar_filtro()

    def apply_store_info(self) -> None:
        """
        Repinta el membrete tras cambiar el nombre en Configuracion >
        Tienda, sin reiniciar la aplicacion (lo llama MainWindow).
        """
        self._membrete_var.set(self._service.company_name())

    def _valores_de(self, factura: Invoice) -> tuple:
        def en_bolívares() -> str:
            """
            Con la tasa que la factura guardo al emitirse (la misma del
            detalle y del PDF), no con las de hoy: es el monto que de
            verdad se cobro. Una factura anterior a ese campo no la
            tiene y cae a la tasa BCV vigente.
            """
            tasa = factura.bs_rate or (self._tasas.bcv if self._tasas else None)
            if tasa is None:
                return "-"
            return format_money(convert_cents(factura.total_cents, tasa), " Bs")

        celdas = {
            "numero": lambda: factura.display_number,
            "fecha": lambda: (
                factura.issued_at.strftime("%d/%m/%Y") if factura.issued_at else ""
            ),
            "cliente": lambda: factura.customer_name,
            # Los del snapshot de la factura (lo que se le facturo), no
            # los de la ficha de hoy: si el cliente cambia de direccion,
            # las facturas viejas siguen diciendo la de ese dia.
            "cedula": lambda: factura.customer_tax_id or "-",
            "direccion": lambda: factura.customer_address or "-",
            "estado": lambda: _ESTADO_DISPLAY[factura.status],
            "total": lambda: format_money(factura.total_cents, self._currency_symbol),
            "total_bs": en_bolívares,
            "total_catalogo": lambda: format_money(
                catalog_total(factura), self._currency_symbol
            ),
        }
        return tuple(celdas[c.key]() for c in self._columnas)

    # ------------------------------------------------------------
    # seleccion
    # ------------------------------------------------------------
    def _selected_invoice_id(self) -> int | None:
        iid = self._table.selected_iid()
        return int(iid) if iid else None

    # ------------------------------------------------------------
    # botones
    # ------------------------------------------------------------
    def _on_nueva(self) -> None:
        dialogo = InvoiceDialog(
            self,
            self._service,
            self._products_svc,
            self._customers_svc,
            currency_symbol=self._currency_symbol,
            settings_service=self._settings_service,
            rate_display_service=self._rates,
            preview_service=self._preview_service,
        )
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_detalle(self) -> None:
        invoice_id = self._selected_invoice_id()
        if invoice_id is None:
            messagebox.showinfo(
                "Ver detalle", "Selecciona una factura primero.", parent=self
            )
            return

        factura = self._service.get_invoice(invoice_id)
        if factura is None:
            messagebox.showerror("Ver detalle", "La factura ya no existe.", parent=self)
            return
        InvoiceDetailDialog(
            self,
            factura,
            currency_symbol=self._currency_symbol,
            billing_service=self._service,
            settings_service=self._settings_service,
            rate_display_service=self._rates,
        )

    def _on_generar_pdf(self) -> None:
        invoice_id = self._selected_invoice_id()
        if invoice_id is None:
            messagebox.showinfo(
                "Generar PDF", "Selecciona una factura primero.", parent=self
            )
            return
        generar_pdf(self, self._service, invoice_id)

    def _on_cierre_caja(self) -> None:
        """
        Solo lectura (ver cash_closing_dialog.py): no hace falta
        reload() al volver, a diferencia de _on_nueva()/_on_cancelar(),
        que si escriben facturas nuevas o las anulan.
        """
        CashClosingDialog(self, self._service, currency_symbol=self._currency_symbol)

    def _on_cancelar(self) -> None:
        invoice_id = self._selected_invoice_id()
        if invoice_id is None:
            messagebox.showinfo(
                "Cancelar factura", "Selecciona una factura primero.", parent=self
            )
            return

        factura = next((f for f in self._facturas if f.id == invoice_id), None)
        if factura is not None and factura.is_cancelled:
            messagebox.showinfo(
                "Cancelar factura", "Esa factura ya esta cancelada.", parent=self
            )
            return

        motivo = simpledialog.askstring(
            "Cancelar factura", "Motivo de la cancelación:", parent=self
        )
        if motivo is None:  # el usuario cerro el dialogo, no escribio ""
            return

        try:
            self._service.cancelar(invoice_id, motivo)
        except (ValueError, DomainError) as error:
            messagebox.showerror("Cancelar factura", str(error), parent=self)
            return
        self.reload()


def generar_pdf(parent: tk.Misc, service: BillingService, invoice_id: int) -> None:
    """
    Genera el PDF de una factura y avisa donde quedo. Modulo, no metodo
    de ninguna clase: la usan BillingPage (boton de la tabla) e
    InvoiceDetailDialog (boton del detalle), sin duplicar el intento y
    los dos messagebox.
    """
    try:
        ruta = service.generate_pdf(invoice_id)
    except ValueError as error:
        messagebox.showerror("Generar PDF", str(error), parent=parent)
        return
    messagebox.showinfo("Generar PDF", f"PDF generado en:\n{ruta}", parent=parent)


class InvoiceDialog(kit.Dialog):
    """
    Formulario de alta: primero se elige el cliente y se arma el
    borrador (crear_borrador), despues se agregan lineas una por una
    (agregar_linea) mientras los totales se recalculan en vivo, y por
    ultimo "Confirmar factura" dispara la transaccion atomica.

    Nada de esto toca la base hasta ese ultimo paso: cerrar la ventana
    antes de confirmar no deja ningun rastro (ver BillingService).
    """

    def __init__(
        self,
        master: tk.Misc,
        billing_service: BillingService,
        product_service: ProductService,
        customer_service: CustomerService,
        *,
        currency_symbol: str = "$",
        settings_service: SettingsService | None = None,
        rate_display_service: RateDisplayService | None = None,
        preview_service: PreviewService | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Nueva factura")
        self.transient(master)
        self.resizable(True, True)

        self._billing = billing_service
        self._currency_symbol = currency_symbol
        # Opcional (mismo criterio que billing_service/customer_service
        # en ProductsPage): sin el, Modo avanzado se trata como apagado
        # y esta ventana se ve exactamente igual que hoy.
        self._advanced_mode = (
            settings_service.advanced_mode() if settings_service is not None else False
        )
        # Solo para ver el total convertido a Bs mientras se factura: la
        # factura se guarda y se imprime en $ con el IVA de siempre.
        self._rates = rate_display_service
        # Opcional: sin el no hay boton "Vista previa" (ver
        # _build_lineas_section).
        self._preview = preview_service
        self.guardado = False
        self._invoice: Invoice | None = None

        # Catalogo y clientes completos en memoria, igual criterio que
        # ProductsPage/CustomersPage: son unos pocos miles de filas como
        # mucho, filtrar en RAM es mas rapido que ir a SQLite por tecla.
        self._productos = product_service.list_products()
        self._productos_filtrados = list(self._productos)
        self._coincidencias_inicio: int | None = None
        self._customers_svc = customer_service  # para el alta desde aca
        self._clientes = [c for c in customer_service.list_customers()]
        self._tiers = product_service.list_price_tiers()

        # Cliente elegido de la lista: una factura siempre lleva ficha
        # (ver _build_cliente_section).
        self._cliente_id: int | None = None
        # "1" ya puesto: lo normal es una unidad, y Enter agrega la linea sin
        # tener que escribir nada.
        self._cantidad_var = tk.StringVar(value="1")
        self._tier_var = tk.StringVar()
        self._forma_pago_var = tk.StringVar()
        # Fecha de elaboracion: arranca en "ahora" (mismo valor que
        # crear_borrador() le pone a Invoice.issued_at) pero editable -
        # para retomar en papel una venta de un rato antes, o cargar una
        # de ayer sin haber podido facturarla en el momento.
        _ahora = datetime.now()  # noqa: DTZ005
        self._fecha_var = tk.StringVar(value=_ahora.strftime("%d/%m/%Y"))
        self._hora_var = tk.StringVar(value=_ahora.strftime("%H:%M"))
        # Panel de totales: columna $ (lo que se guarda) y columna Bs (a
        # la tasa elegida, solo para mostrar).
        self._subtotal_var = tk.StringVar(value="-")
        self._iva_titulo_var = tk.StringVar(value="IVA")
        self._iva_var = tk.StringVar(value="-")
        self._total_var = tk.StringVar(value="-")
        self._subtotal_bs_var = tk.StringVar(value="")
        self._iva_bs_var = tk.StringVar(value="")
        self._total_convertido_var = tk.StringVar(value="")
        self._tasa_info_var = tk.StringVar(value="")
        # Guia de precios del producto elegido (Modo avanzado): lo que
        # costaria antes de agregarlo. Ver _refrescar_guia_de_precio.
        self._guia_precio_var = tk.StringVar(value=_SIN_PRODUCTO)
        # Desmarcado por defecto: la mayoria de las ventas del dia no
        # llevan IVA, asi que se tilda a proposito cuando hace falta. El
        # check se ve SIEMPRE (no solo con Modo avanzado): si no, no
        # habria como cobrarlo.
        self._cobrar_iva_var = tk.BooleanVar(value=False)
        # A que tasa se cobra. Con Modo avanzado arranca en la de
        # referencia (es para lo que se prende el modo) y se puede
        # cambiar a la BCV; sin el modo, siempre BCV y no hay selector.
        self._tasa_var = tk.StringVar(
            value=RATE_PERSONAL if self._advanced_mode else RATE_BCV
        )

        self._build_form()
        self._tier_var.trace_add("write", self._on_cambiar_nivel_de_precio)
        # Solo Esc, no Enter: el formulario tiene dos acciones posibles
        # (agregar linea, confirmar factura) y atar Enter a una sola
        # arriesgaria confirmar la factura sin querer mientras se tipea
        # la cantidad de una linea.
        self.bind("<Escape>", lambda _evento: self._on_cerrar())
        # F12 confirma (Enter nunca: ver arriba). Es una tecla distinta y esta
        # escrita en el boton, asi no se confirma sin querer.
        self.bind("<F12>", lambda _evento: self._on_confirmar())
        self.protocol("WM_DELETE_WINDOW", self._on_cerrar)  # la X de la ventana
        focus_first_entry(self)  # cursor en el campo de cliente, sin click extra
        size_dialog_to_screen(self)  # ventana grande, adaptable
        # lift() fuerza el apilamiento encima de quien lo abrio (Facturas
        # o el atajo "Crear factura" de Productos): transient() sola no
        # siempre alcanza (se vio pasar en Windows).
        self.lift()
        self.focus_force()
        self.grab_set()

    # ------------------------------------------------------------
    # construccion
    # ------------------------------------------------------------
    def _build_form(self) -> None:
        """
        Una sola columna, de arriba abajo, en el orden en que se factura:

            Cliente   [ escribe el nombre o la cedula ........ ]
            Producto  [ escribe el nombre o el codigo ........ ] Cant. Nivel
            Lineas de la factura  (todo el ancho: lo que se esta cobrando)
            fecha, Cerrar, Vista previa ........ Totales, IVA, Pago, Confirmar

        Cliente y producto son un solo campo con lista flotante (ver
        picker.FloatingPicker): escribir, Enter y seguir. Elegir al cliente
        inicia la factura. Lo que se usa poco (fecha y hora) va plegado.

        Adaptable: la tabla de lineas es lo unico que se estira; el pie
        (totales y botones) no se recorta nunca.
        """
        frame = kit.Frame(self, padding=14)
        frame.pack(fill="both", expand=True)
        frame.columnconfigure(0, weight=1)
        frame.rowconfigure(2, weight=1)

        self._build_barra_cliente(frame)
        self._build_barra_producto(frame)
        self._build_lineas_section(frame)
        self._build_pie(frame)
        # Tab recorre los widgets en el orden en que estan apilados: subir la
        # tabla de lineas la deja al FINAL del recorrido (despues de Confirmar).
        # Asi de Nivel se pasa directo a IVA, forma de pago y fecha, y la tabla
        # (Supr, + y -) sigue alcanzable con Tab o con un clic.
        self._seccion_lineas.lift()

    # ------------------------------------------------------------
    # cliente
    # ------------------------------------------------------------
    def _build_barra_cliente(self, parent: kit.Frame) -> None:
        """
        Una factura siempre lleva un cliente de la ficha: no hay "venta
        sin cliente" (eso vive en Cotizaciones/notas de entrega, donde
        alcanza con un nombre suelto). Un solo campo: se escribe nombre o
        cedula, la lista flotante filtra y Enter elige. Elegir al cliente
        INICIA la factura (antes habia un boton "Iniciar factura" aparte);
        elegir otro con la factura ya empezada le cambia el cliente sin
        perder las lineas. "Nuevo cliente..." es la ultima fila de la lista.
        """
        barra = kit.Frame(parent)
        barra.grid(row=0, column=0, sticky="ew")
        barra.columnconfigure(1, weight=1)
        kit.Label(barra, text="Cliente", style="Bold.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        self._cliente_picker = FloatingPicker(
            barra,
            claves=lambda c: (c.name, c.tax_id, c.sku),
            fila=self._fila_cliente,
            texto_elegido=lambda c: c.name,
            items=self._clientes,
            altura=7,
            ancho_lista=640,
            on_change=self._on_cliente_cambio,
            nuevo=("Nuevo cliente...", self._on_nuevo_cliente),
        )
        self._cliente_picker.grid(row=0, column=1, sticky="ew")
        self._entrada_cliente = self._cliente_picker.entry
        self._entrada_cliente.bind(
            "<FocusOut>", lambda _e: self.after(250, self._restaurar_cliente), add="+"
        )

        # Los datos del elegido, en un renglon de alto fijo: se ve a quien se
        # le factura (y si le falta la cedula) sin que la ventana cambie de
        # tamano segun el cliente.
        self._datos_cliente_var = tk.StringVar(value=_SIN_CLIENTE)
        marco = fixed_height_frame(barra, lines=1, padding=2)
        marco.grid(row=1, column=1, sticky="ew", pady=(4, 0))
        detalle = kit.Label(
            marco, textvariable=self._datos_cliente_var, style="Muted.TLabel"
        )
        detalle.pack(fill="both", expand=True)
        wrap_to_width(detalle)

    @staticmethod
    def _fila_cliente(cliente: object) -> tuple[str, str]:
        return (
            f"{cliente.name}   {cliente.tax_id or 'sin cédula'}   "
            f"{cliente.phone or ''}".rstrip(),
            "normal",
        )

    def _mostrar_cliente(self, cliente: object | None) -> None:
        if cliente is None:
            self._datos_cliente_var.set(_SIN_CLIENTE)
            return
        # Con etiqueta cada dato: un numero suelto no dice si es cedula o telefono.
        partes = [
            f"Cédula/RIF: {cliente.tax_id}" if cliente.tax_id else "FALTA cédula/RIF",
            f"Tel: {cliente.phone}" if cliente.phone else "",
            f"Dir: {cliente.address}" if cliente.address else "",
        ]
        self._datos_cliente_var.set("   ·   ".join(p for p in partes if p))

    def _on_cliente_cambio(self, cliente: object | None) -> None:
        """Eligio (o soltó) un cliente en el campo."""
        if cliente is None:
            # Esta escribiendo para buscar otro: la factura no se toca hasta
            # que elija (si no elige, _restaurar_cliente deja el de siempre).
            self._cliente_id = None
            return
        self._cliente_id = cliente.id
        self._mostrar_cliente(cliente)
        if self._invoice is None:
            self._on_iniciar()
        else:
            self._on_usar_cliente()

    def _restaurar_cliente(self) -> None:
        """Con la factura ya iniciada, si el campo se queda sin cliente
        elegido, vuelve a mostrar el de la factura."""
        if not self.winfo_exists() or self._invoice is None:
            return
        if self._cliente_picker.elegido() is not None:
            return
        self._restaurar_cliente_anterior(self._invoice.customer_id)

    def _recargar_clientes(self, seleccionar_id: int | None = None) -> None:
        """Vuelve a leer los clientes (uno nuevo o recien completado) y
        deja elegido a 'seleccionar_id', si esta."""
        self._clientes = list(self._customers_svc.list_customers())
        self._cliente_picker.set_items(self._clientes)
        if seleccionar_id is not None:
            nuevo = next((c for c in self._clientes if c.id == seleccionar_id), None)
            if nuevo is not None:
                self._cliente_picker.elegir(nuevo)

    def _on_nuevo_cliente(self) -> None:
        """
        Crea un cliente sin cambiar de pestana y lo deja elegido. El
        formulario ya exige nombre y cedula/RIF por su cuenta (ver
        CustomerDialog). Import local: customers_page importa de
        billing_page (historial de compras en la ficha del cliente), asi
        que hacerlo arriba seria un import circular.
        """
        from .customers_page import CustomerDialog

        dialogo = CustomerDialog(self, self._customers_svc, titulo="Nuevo cliente")
        self.wait_window(dialogo)
        if not dialogo.guardado:
            return
        antes = {c.id for c in self._clientes}
        self._clientes = list(self._customers_svc.list_customers())
        nuevo = next((c for c in self._clientes if c.id not in antes), None)
        self._recargar_clientes(nuevo.id if nuevo is not None else None)

    def _completar_cliente(self, cliente_id: int, faltan: list[str]) -> bool:
        """
        El cliente elegido no tiene lo que una factura necesita: ofrece
        completar su ficha ahora mismo, sin ir a la pestana Clientes.
        True si se guardo (hay que volver a intentar iniciar la factura).
        """
        from .customers_page import CustomerDialog

        cliente = next(c for c in self._clientes if c.id == cliente_id)
        if not messagebox.askyesno(
            "Datos del cliente",
            f"{cliente.name} no tiene {' ni '.join(faltan)}, y una factura la "
            "necesita.\n\n¿Completar su ficha ahora?",
            parent=self,
        ):
            return False
        dialogo = CustomerDialog(
            self,
            self._customers_svc,
            titulo="Completar cliente",
            customer_id=cliente_id,
        )
        self.wait_window(dialogo)
        if not dialogo.guardado:
            return False
        self._recargar_clientes(cliente_id)
        return True

    # ------------------------------------------------------------
    # producto
    # ------------------------------------------------------------
    def _build_barra_producto(self, parent: kit.Frame) -> None:
        """
        Producto, cantidad y nivel de precio en una fila. Escribir el nombre
        o el codigo, Enter elige y salta a Cantidad (ya en "1"); Enter otra
        vez agrega la linea y el cursor vuelve al producto, vacio: tres
        teclas por producto. Apagado hasta elegir al cliente.
        """
        barra = kit.Frame(parent)
        barra.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        barra.columnconfigure(1, weight=1)
        kit.Label(barra, text="Producto", style="Bold.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 12)
        )
        self._producto_picker = FloatingPicker(
            barra,
            claves=lambda p: (p.name, p.sku),
            fila=self._fila_producto,
            texto_elegido=lambda p: f"{p.sku} - {p.name}",
            items=self._productos,
            altura=9,
            ancho_lista=780,
            on_change=self._on_producto_cambio,
            on_activate=self._on_producto_activado,
        )
        self._producto_picker.grid(row=0, column=1, sticky="ew")

        kit.Label(barra, text="Cant.").grid(row=0, column=2, padx=(14, 6))
        self._cantidad_entry = kit.Entry(
            barra, textvariable=self._cantidad_var, width=6
        )
        self._cantidad_entry.grid(row=0, column=3)
        # Enter en Cantidad agrega la linea. Enter queda atado SOLO a este
        # campo y al buscador, nunca a la ventana: asi no hay forma de
        # confirmar la factura sin querer (se confirma con F12 o el boton).
        self._cantidad_entry.bind("<Return>", lambda _e: self._on_agregar_linea())
        kit.Label(barra, text="Nivel").grid(row=0, column=4, padx=(14, 6))
        self._tier_combo = kit.Combobox(
            barra,
            state="readonly",
            width=14,
            textvariable=self._tier_var,
            # "Precio 1 - Mayor": el numero adelante (el mismo de las
            # columnas Precio 1/2/3 del catalogo y de la ficha de un
            # producto) y el nombre que le puso el negocio atras.
            values=[f"Precio {t.position} - {t.name}" for t in self._tiers],
        )
        indice_default = next((i for i, t in enumerate(self._tiers) if t.is_default), 0)
        if self._tiers:
            self._tier_combo.current(indice_default)
        self._tier_combo.grid(row=0, column=5)
        # Tab llega aca despues de Cantidad: flechas eligen el nivel y Enter
        # sigue al proximo campo (IVA).
        self._tier_combo.enter_pasa_al_siguiente()

        # Guia (Modo avanzado): lo que va a costar ese producto, en las tres
        # monedas, ANTES de agregarlo. En un renglon de alto fijo.
        if self._advanced_mode:
            marco_guia = fixed_height_frame(barra, lines=1, padding=2)
            marco_guia.grid(row=1, column=1, columnspan=5, sticky="ew", pady=(4, 0))
            guia = kit.Label(
                marco_guia, textvariable=self._guia_precio_var, style="Muted.TLabel"
            )
            guia.pack(fill="both", expand=True)
            wrap_to_width(guia)
        self._habilitar_productos(False)

    @staticmethod
    def _fila_producto(producto: object) -> tuple[str, str]:
        """Existencia adelante (ancho fijo) y en color: verde el que hay, gris
        el que esta en cero o en negativo."""
        stock = format_quantity(producto.current_stock)
        return (
            f"{stock:>6}   {producto.sku} - {producto.name}",
            "ok" if producto.current_stock > 0 else "muted",
        )

    def _habilitar_productos(self, activo: bool) -> None:
        self._producto_picker.habilitar(activo)
        self._cantidad_entry.configure(state="normal" if activo else "disabled")
        self._tier_combo.configure(state="readonly" if activo else "disabled")

    def _on_producto_cambio(self, _producto: object | None) -> None:
        self._refrescar_guia_de_precio()

    def _on_producto_activado(self, _producto: object) -> None:
        """Enter sobre un producto: el cursor pasa a Cantidad, ya en "1"."""
        self._cantidad_entry.focus_set()
        self._cantidad_entry.select_range(0, "end")

    # ------------------------------------------------------------
    # lineas
    # ------------------------------------------------------------
    def _build_lineas_section(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(
            parent,
            text="Líneas de la factura",
            padding=8,
            title_style="CardTitle.TLabel",
        )
        seccion.grid(row=2, column=0, sticky="nsew", pady=(12, 0))
        self._seccion_lineas = seccion

        # Se adapta al ancho que le toque (ver widgets.lines_tree).
        self._lineas_tree = lines_tree(seccion, _COLUMNAS_LINEAS, plano=True)
        self._vacio_lineas = TextoVacio(
            self._lineas_tree,
            "Aún no hay líneas. Escribe un producto arriba y pulsa Enter.",
        )
        self._vacio_lineas.mostrar(True)
        # Columna angosta y centrada, solo para la "x" de quitar linea:
        # un click ahi (ver _on_click_lineas) saca esa linea del borrador
        # sin tocar la base (nada se escribe hasta "Confirmar factura").
        # "❌" en vez de tag_configure(foreground=...): un tag de Treeview
        # tiñe la FILA entera (no hay color por celda en ttk.Treeview), y
        # solo se quiere roja la x, no el resto de la linea.
        self._lineas_tree.bind("<Button-1>", self._on_click_lineas)
        # Con el teclado: Supr quita la linea elegida, + y - cambian su cantidad.
        self._lineas_tree.bind("<Delete>", self._on_supr_en_lineas)
        for tecla in ("<plus>", "<KP_Add>"):
            self._lineas_tree.bind(tecla, lambda _e: self._on_cantidad_en_lineas(1))
        for tecla in ("<minus>", "<KP_Subtract>"):
            self._lineas_tree.bind(tecla, lambda _e: self._on_cantidad_en_lineas(-1))

    # ------------------------------------------------------------
    # pie: totales, cobro y botones
    # ------------------------------------------------------------
    def _build_pie(self, parent: kit.Frame) -> None:
        """
        Abajo a la derecha, todo lo que tiene que ver con COBRAR junto: los
        totales, el IVA, la tasa, la forma de pago y Confirmar. Se arma
        antes que los botones de la izquierda para que Tab los recorra en
        ese orden.
        """
        pie = kit.Frame(parent)
        pie.grid(row=3, column=0, sticky="ew", pady=(12, 0))
        pie.columnconfigure(0, weight=1)
        izquierda = kit.Frame(pie)
        izquierda.grid(row=0, column=0, sticky="sw")
        # Orden de creacion = orden de Tab: IVA, tasa y pago, luego Confirmar,
        # y al final la fecha y los botones de menos uso.
        self._build_cobro(izquierda)
        self._build_totales_section(pie)
        self._build_fecha_y_botones(izquierda)

    def _build_fecha_y_botones(self, parent: kit.Frame) -> None:
        # Fecha y hora a la vista y editables: casi siempre es "ahora", pero
        # cargar una venta de ayer es cuestión de escribirla, sin abrir nada.
        fila_fecha = kit.Frame(parent)
        fila_fecha.pack(anchor="w")
        kit.Label(fila_fecha, text="Fecha:").pack(side="left")
        kit.Entry(fila_fecha, textvariable=self._fecha_var, width=11).pack(
            side="left", padx=(6, 12)
        )
        kit.Label(fila_fecha, text="Hora:").pack(side="left")
        kit.Entry(fila_fecha, textvariable=self._hora_var, width=6).pack(
            side="left", padx=(6, 0)
        )

        botones = kit.Frame(parent)
        botones.pack(anchor="w", pady=(10, 0))
        # Fuera del recorrido de Tab (Esc ya cierra): despues de la hora, Tab
        # llega a Confirmar sin pasar por un boton que descarta la factura.
        kit.Button(
            botones, text="Cerrar", command=self._on_cerrar, padding=(16, 10), tab=False
        ).pack(side="left")
        if self._preview is not None:
            # Muestra el borrador como una COTIZACION en PDF, sin guardar nada
            # (ver PreviewService). Con su icono, apagado hasta que haya algo
            # que mostrar, y un cartelito que dice que NO guarda.
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
                    "Abre el borrador en PDF para verlo o mandarlo. "
                    "No guarda nada ni gasta un número."
                ),
            )

    def _factor_de_precio(self) -> Decimal | None:
        """
        Cobrando a la tasa de referencia: esa tasa / BCV. Multiplicado
        por el precio del catalogo da el precio "a tasa BCV" que se
        guarda en la linea, de forma que cobrarlo a la BCV de el mismo
        monto en Bs que cobrar el precio del catalogo a la de referencia:

            Bs = precio x referencia = (precio x referencia / BCV) x BCV

        Asi la factura queda como una venta normal a tasa BCV. Cobrando a
        la BCV (o sin Modo avanzado, o sin tasas) devuelve None: el
        precio del catalogo va tal cual.
        """
        if self._tasa_var.get() != RATE_PERSONAL or self._rates is None:
            return None
        tasas = self._rates.snapshot()
        if tasas.personal is None or tasas.bcv is None:
            return None
        return tasas.personal / tasas.bcv

    def _on_cambiar_tasa(self) -> None:
        """
        Cambiar de tasa recalcula las lineas que ya estan cargadas: toda
        la factura va a UNA tasa, no una mitad a cada una. Se rearman con
        el precio de catalogo de ahora (el mismo que usaría agregarlas de
        nuevo), no con el que tenian.
        """
        self._refrescar_guia_de_precio()
        if self._invoice is None or not self._invoice.lines:
            return
        pedidos = [
            (line.product_id, from_milli(line.quantity_milli), line.tier_id)
            for line in self._invoice.lines
        ]
        factor = self._factor_de_precio()
        self._invoice.lines.clear()
        try:
            for product_id, cantidad, tier_id in pedidos:
                self._billing.agregar_linea(
                    self._invoice,
                    product_id=product_id,
                    quantity=cantidad,
                    tier_id=tier_id,
                    price_factor=factor,
                )
        except ValueError as error:  # un producto que ya no esta
            messagebox.showerror("Cambiar de tasa", str(error), parent=self)
        self._refrescar_lineas()

    def _on_cambiar_nivel_de_precio(self, *_args: object) -> None:
        self._refrescar_guia_de_precio()

    def _producto_elegido(self):
        return self._producto_picker.elegido()

    def _tier_elegido_id(self) -> int | None:
        indice = self._tier_combo.current()
        return self._tiers[indice].id if indice >= 0 else None

    def _refrescar_guia_de_precio(self) -> None:
        """
        El precio del nivel elegido en las tres monedas, ANTES de
        agregar el producto: el del catalogo, en Bs a la tasa de
        referencia, y ese mismo monto en $ a la BCV (lo que va a quedar
        en la factura). Misma cuenta que la cotizacion.
        """
        if not self._advanced_mode:
            return
        producto = self._producto_elegido()
        if producto is None:
            self._guia_precio_var.set(_SIN_PRODUCTO)
            return
        precio = producto.price_for(self._tier_elegido_id())
        if precio is None:
            self._guia_precio_var.set("Sin precio en ese nivel.")
            return

        tasas = self._rates.snapshot() if self._rates is not None else None
        if tasas is None or tasas.personal is None or tasas.bcv is None:
            self._guia_precio_var.set(
                f"{format_money(to_cents(precio), self._currency_symbol)}"
                "   |   tasas sin cargar"
            )
            return
        en_bs = convert_cents(to_cents(precio), tasas.personal)
        a_bcv = to_cents(precio * tasas.personal / tasas.bcv)
        self._guia_precio_var.set(
            f"{format_money(to_cents(precio), self._currency_symbol)}"
            f"   |   {format_money(en_bs, ' Bs')}"
            f"   |   {format_money(a_bcv, self._currency_symbol)} BCV"
        )

    def _build_cobro(self, parent: kit.Frame) -> None:
        """Lo que se decide al cobrar y no es un monto: si lleva IVA, a que
        tasa y la forma de pago. A la izquierda de los totales, que es lo
        que quedaba vacio, para que el panel del TOTAL no crezca."""
        # Siempre visible y DESMARCADO: la mayoria de las ventas del dia
        # no llevan IVA, y tenerlo a la vista evita cobrarlo (o no
        # cobrarlo) sin darse cuenta - ver _on_toggle_iva/_on_confirmar.
        # Enter tambien la marca/desmarca (y en las tasas, elige): en la
        # factura Enter nunca confirma, asi que no hay nada que proteger.
        self._check_iva = kit.Checkbutton(
            parent,
            text="Cobrar IVA en esta factura",
            variable=self._cobrar_iva_var,
            command=self._on_toggle_iva,
        )
        self._check_iva.pack(anchor="w")
        self._check_iva.enter_tambien_activa()

        # A que tasa se le cobra al cliente. Solo con Modo avanzado: sin
        # el hay una sola tasa posible (la BCV) y no hay que elegir.
        if self._advanced_mode:
            fila_tasa = kit.Frame(parent)
            fila_tasa.pack(anchor="w", pady=(8, 0))
            kit.Label(fila_tasa, text="Cobrar a:").pack(side="left")
            self._radios_tasa = []
            for clave in (RATE_BCV, RATE_PERSONAL):
                radio = kit.Radiobutton(
                    fila_tasa,
                    text=_ETIQUETAS_TASA[clave],
                    value=clave,
                    variable=self._tasa_var,
                    command=self._on_cambiar_tasa,
                )
                radio.pack(side="left", padx=(12, 0))
                radio.enter_tambien_activa()
                self._radios_tasa.append(radio)

        # Forma de pago junto al boton que confirma: antes estaba arriba a la
        # derecha, lejos, y olvidarla daba el error recien al confirmar. Sin
        # seleccion por defecto (current() empieza en -1): obliga a elegir
        # una a proposito - _on_confirmar() rechaza confirmar sin una.
        fila_pago = kit.Frame(parent)
        fila_pago.pack(anchor="w", fill="x", pady=(8, 0))
        fila_pago.columnconfigure(1, weight=1)
        kit.Label(fila_pago, text="Forma de pago").grid(row=0, column=0, sticky="w")
        self._forma_pago_combo = kit.Combobox(
            fila_pago,
            state="readonly",
            width=26,
            textvariable=self._forma_pago_var,
            values=[texto for _metodo, texto in _FORMA_PAGO_OPCIONES],
        )
        self._forma_pago_combo.grid(row=0, column=1, sticky="ew", padx=(10, 0))
        self._forma_pago_combo.enter_pasa_al_siguiente()

    def _build_totales_section(self, parent: kit.Frame) -> None:
        """
        Panel de cobro como en una factura fiscal: base imponible, IVA y
        total, en $ (lo que se guarda), en Bs a la tasa elegida y -con la
        tasa de referencia- en $ a tasa BCV (las dos ultimas columnas solo
        se muestran: la factura se guarda y se imprime en $). Debajo, lo
        que se decide al cobrar: IVA, a que tasa, forma de pago y Confirmar.
        """
        panel = kit.Frame(parent, style="Card.TFrame", padding=(20, 10))
        panel.grid(row=0, column=1, sticky="se", padx=(12, 0))

        kit.Label(panel, text="", style="CardText.TLabel").grid(row=0, column=0)
        kit.Label(panel, text="$ BCV", style="CardTitle.TLabel").grid(
            row=0, column=1, sticky="e", padx=(24, 0)
        )
        if self._rates is not None:
            kit.Label(panel, text="Bs", style="CardTitle.TLabel").grid(
                row=0, column=2, sticky="e", padx=(32, 0)
            )

        filas = (
            ("Base imponible", self._subtotal_var, self._subtotal_bs_var),
            (self._iva_titulo_var, self._iva_var, self._iva_bs_var),
        )
        for fila, (titulo, usd, bs) in enumerate(filas, start=1):
            etiqueta = kit.Label(panel, style="CardText.TLabel")
            if isinstance(titulo, tk.StringVar):
                etiqueta.configure(textvariable=titulo)
            else:
                etiqueta.configure(text=titulo)
            etiqueta.grid(row=fila, column=0, sticky="w", pady=(4, 0))
            kit.Label(panel, textvariable=usd, style="CardText.TLabel").grid(
                row=fila, column=1, sticky="e", padx=(24, 0), pady=(4, 0)
            )
            if self._rates is not None:
                kit.Label(panel, textvariable=bs, style="CardText.TLabel").grid(
                    row=fila, column=2, sticky="e", padx=(32, 0), pady=(4, 0)
                )

        ttk.Separator(panel).grid(row=3, column=0, columnspan=3, sticky="ew", pady=6)
        # El TOTAL, en grande: es lo que el cliente paga y lo primero que se
        # busca con la vista.
        kit.Label(panel, text="TOTAL", style="Total.TLabel").grid(
            row=4, column=0, sticky="w"
        )
        kit.Label(panel, textvariable=self._total_var, style="Total.TLabel").grid(
            row=4, column=1, sticky="e", padx=(24, 0)
        )
        if self._rates is not None:
            kit.Label(
                panel, textvariable=self._total_convertido_var, style="Total.TLabel"
            ).grid(row=4, column=2, sticky="e", padx=(32, 0))
        kit.Label(
            panel, textvariable=self._tasa_info_var, style="CardDate.TLabel"
        ).grid(row=5, column=0, columnspan=3, sticky="e", pady=(4, 8))

        self._btn_confirmar = kit.Button(
            panel,
            text="Confirmar factura   F12",
            style="Success.TButton",
            command=self._on_confirmar,
            state="disabled",
            padding=(32, 12),
        )
        self._btn_confirmar.grid(
            row=6, column=0, columnspan=3, sticky="ew", pady=(4, 0)
        )

    def _on_vista_previa(self) -> None:
        """
        Abre en el visor de PDF el borrador como COTIZACION, con los
        montos de la tasa elegida en pantalla. No guarda la factura, no
        mueve stock y no gasta un numero de factura.
        """
        if self._invoice is None or not self._invoice.lines:
            messagebox.showinfo(
                "Vista previa",
                "Inicia la factura y agrega al menos una línea.",
                parent=self,
            )
            return
        try:
            fecha = _parse_fecha_hora(self._fecha_var.get(), self._hora_var.get())
        except ValueError:
            fecha = None  # fecha mal escrita: la de hoy, esto no es la factura
        try:
            ruta = self._preview.invoice_pdf(self._invoice, RATE_BCV, issued_at=fecha)
            open_in_viewer(ruta)
        except (ValueError, OSError) as error:
            messagebox.showerror("Vista previa", str(error), parent=self)

    def _desglose_bs(self) -> BsBreakdown | None:
        """
        La factura en bolívares, con UNA sola lectura de tasas - no una
        por linea (el N+1 del CLAUDE.md). Siempre a tasa BCV: los montos
        en $ de la factura ya estan expresados a esa tasa (ver
        _factor_de_precio). None si no hay tasa cargada.
        """
        if self._rates is None or self._invoice is None:
            return None
        return self._rates.snapshot().invoice_in_bs(self._invoice, RATE_BCV)

    def _refrescar_lineas_si_hay_factura(self) -> None:
        if self._invoice is not None:
            self._refrescar_lineas()

    def _refrescar_total_convertido(self, desglose: BsBreakdown | None = None) -> None:
        # Sin argumento vuelve a leer las tasas; quien ya las leyo (el
        # refresco de las lineas) le pasa lo que calculo.
        if desglose is None:
            desglose = self._desglose_bs()
        if self._rates is None or self._invoice is None:
            for var in (
                self._subtotal_bs_var,
                self._iva_bs_var,
                self._total_convertido_var,
                self._tasa_info_var,
            ):
                var.set("")
            return
        if desglose is None:
            self._subtotal_bs_var.set("-")
            self._iva_bs_var.set("-")
            self._total_convertido_var.set("-")
            self._tasa_info_var.set("Tasa BCV sin cargar: no hay montos en Bs.")
            return
        self._subtotal_bs_var.set(format_money(desglose.subtotal, " Bs"))
        self._iva_bs_var.set(format_money(desglose.tax, " Bs"))
        self._total_convertido_var.set(format_money(desglose.total, " Bs"))
        self._tasa_info_var.set(
            f"Tasa BCV: {str(desglose.rate).replace('.', ',')} Bs/$"
        )

    # ------------------------------------------------------------
    # cliente / iniciar factura
    # ------------------------------------------------------------
    def _on_iniciar(self) -> None:
        """Arma el borrador con el cliente elegido. Corre solo, al elegir al
        cliente en el campo; con la factura ya iniciada no hace nada."""
        if self._invoice is not None:
            return
        if self._cliente_id is None:
            messagebox.showinfo(
                "Iniciar factura", "Elige un cliente de la lista.", parent=self
            )
            return
        try:
            borrador = self._billing.crear_borrador(customer_id=self._cliente_id)
        except ValueError as error:
            messagebox.showerror("Iniciar factura", str(error), parent=self)
            return

        # La cedula/RIF es obligatoria: se avisa ACA, antes de armar las
        # lineas, no recien al confirmar.
        faltan = self._billing.missing_fiscal_data(borrador)
        if faltan:
            if self._completar_cliente(borrador.customer_id, faltan):
                self._on_iniciar()  # con la ficha ya completa
            else:
                # No la completo: la factura no arranca; el campo queda vacio
                # para elegir otro cliente.
                self._cliente_picker.limpiar(avisar=False)
                self._cliente_id = None
                self._mostrar_cliente(None)
            return
        self._invoice = borrador

        # Con cliente, ya se pueden cargar productos: el cursor salta al
        # campo de producto (no hace falta un clic ni buscar un boton).
        self._habilitar_productos(True)
        self._btn_confirmar.configure(state="normal")
        # El borrador nace con la tasa de IVA de settings; el check
        # arranca desmarcado. _on_toggle_iva los deja de acuerdo, para
        # que en pantalla se vea desde el principio lo que se va a
        # cobrar (y no un IVA que el check dice que no).
        self._on_toggle_iva()
        self._producto_picker.enfocar()

    def _on_usar_cliente(self) -> None:
        """
        Le pone al borrador el cliente elegido, sin tocar sus lineas.
        Mismo control que al iniciar: sin cedula/RIF se ofrece completar
        la ficha, y si no se completa la factura sigue con el cliente
        que tenia.
        """
        if self._cliente_id is None:
            messagebox.showinfo(
                "Cambiar cliente", "Elige un cliente de la lista.", parent=self
            )
            return
        anterior = self._invoice.customer_id
        nuevo = self._cliente_id
        if nuevo == anterior:
            return
        try:
            self._billing.cambiar_cliente(self._invoice, nuevo)
            faltan = self._billing.missing_fiscal_data(self._invoice)
            if faltan:
                if self._completar_cliente(nuevo, faltan):
                    # La ficha cambio: se vuelve a copiar al borrador.
                    self._billing.cambiar_cliente(self._invoice, nuevo)
                    faltan = self._billing.missing_fiscal_data(self._invoice)
                if faltan:
                    self._billing.cambiar_cliente(self._invoice, anterior)
                    self._restaurar_cliente_anterior(anterior)
                    return
        except ValueError as error:
            messagebox.showerror("Cambiar cliente", str(error), parent=self)
            self._restaurar_cliente_anterior(anterior)
            return
        self._mostrar_cliente(next((c for c in self._clientes if c.id == nuevo), None))

    def _restaurar_cliente_anterior(self, cliente_id: int | None) -> None:
        """No se pudo cambiar: el campo vuelve a mostrar el cliente de la factura."""
        anterior = next((c for c in self._clientes if c.id == cliente_id), None)
        if anterior is not None:
            self._cliente_picker.elegir(anterior, avisar=False)
            self._cliente_id = anterior.id
            self._mostrar_cliente(anterior)

    # ------------------------------------------------------------
    # lineas
    # ------------------------------------------------------------
    def _on_agregar_linea(self) -> None:
        if self._invoice is None:
            messagebox.showinfo(
                "Agregar linea", "Elige primero al cliente.", parent=self
            )
            return

        producto = self._producto_picker.elegido()
        if producto is None:
            messagebox.showinfo(
                "Agregar linea", "Elige un producto de la lista.", parent=self
            )
            return

        try:
            cantidad = parse_local_decimal(self._cantidad_var.get() or "1")
        except InvalidOperation:
            messagebox.showerror(
                "Agregar linea",
                "La cantidad debe ser un número (ej. 2,5).",
                parent=self,
            )
            return

        try:
            nueva = self._billing.agregar_linea(
                self._invoice,
                product_id=producto.id,
                quantity=cantidad,
                tier_id=self._tier_elegido_id(),
                # Con Modo avanzado el precio entra ya alterado (ver
                # _factor_de_precio): la linea queda en $ a tasa BCV.
                price_factor=self._factor_de_precio(),
            )
        except ValueError as error:
            messagebox.showerror("Agregar linea", str(error), parent=self)
            return

        self._juntar_con_linea_igual(nueva)
        # Listo para el siguiente producto: cantidad en 1, campo vacio y el
        # cursor ahi.
        self._cantidad_var.set("1")
        self._producto_picker.limpiar()
        self._refrescar_lineas()
        self._producto_picker.enfocar()

    def _juntar_con_linea_igual(self, nueva: InvoiceLine) -> None:
        """
        El mismo producto, al mismo nivel y al mismo precio, se suma a su
        linea en vez de repetirse (dos "Tornillo x1" en la factura son
        confusos). Con otro nivel de precio (otro precio unitario) va como
        linea aparte: no son lo mismo.
        """
        lineas = self._invoice.lines
        previa = next(
            (
                i
                for i, linea in enumerate(lineas[:-1])
                if linea.product_id == nueva.product_id
                and linea.tier_id == nueva.tier_id
                and linea.unit_price_cents == nueva.unit_price_cents
            ),
            None,
        )
        if previa is None:
            return
        total = from_milli(lineas[previa].quantity_milli + nueva.quantity_milli)
        self._billing.quitar_linea(self._invoice, len(lineas) - 1)
        self._billing.cambiar_cantidad(self._invoice, previa, total)

    def _linea_elegida(self) -> int | None:
        seleccion = self._lineas_tree.selection()
        return int(seleccion[0]) if seleccion else None

    def _on_supr_en_lineas(self, _evento: object = None) -> str:
        """Supr: quita la linea elegida de la tabla."""
        indice = self._linea_elegida()
        if indice is not None and self._invoice is not None:
            self._billing.quitar_linea(self._invoice, indice)
            self._refrescar_lineas()
        return "break"

    def _on_cantidad_en_lineas(self, delta: int) -> str:
        """+ / -: una unidad mas o menos en la linea elegida (minimo 1)."""
        indice = self._linea_elegida()
        if indice is not None and self._invoice is not None:
            actual = self._invoice.lines[indice].quantity_milli
            nueva = from_milli(actual) + delta
            if nueva >= 1:
                self._billing.cambiar_cantidad(self._invoice, indice, nueva)
                self._refrescar_lineas()
                if self._lineas_tree.exists(str(indice)):
                    self._lineas_tree.selection_set(str(indice))
        return "break"

    def _refrescar_lineas(self) -> None:
        # iid = indice en invoice.lines: se reconstruye entero en cada
        # cambio (delete + insert de todas las filas), asi que la
        # posicion siempre esta al dia para _on_click_lineas/quitar_linea.
        self._lineas_tree.delete(*self._lineas_tree.get_children())
        desglose = self._desglose_bs()
        for indice, line in enumerate(self._invoice.lines):
            self._lineas_tree.insert(
                "",
                "end",
                iid=str(indice),
                values=valores_de_linea(
                    line,
                    desglose.lines[indice] if desglose else None,
                    self._currency_symbol,
                )
                + ("❌",),
            )
        self._refrescar_totales(desglose)
        self._habilitar_vista_previa(bool(self._invoice.lines))
        self._vacio_lineas.mostrar(not self._invoice.lines)

    def _habilitar_vista_previa(self, hay_lineas: bool) -> None:
        boton = getattr(self, "_btn_vista_previa", None)
        if boton is not None:
            boton.configure(state="normal" if hay_lineas else "disabled")

    def _on_click_lineas(self, evento: tk.Event) -> None:
        """Click en la x de una linea: la saca del borrador (ver
        BillingService.quitar_linea). Cualquier otra columna, un click
        normal de seleccion - no hace nada especial aca."""
        if self._lineas_tree.identify_region(evento.x, evento.y) != "cell":
            return
        # Por nombre y no por indice: "#N" depende de que columnas se
        # esten mostrando, la x es la ultima.
        columna = self._lineas_tree.identify_column(evento.x)
        if not columna or self._lineas_tree.column(columna, "id") != "quitar":
            return
        fila = self._lineas_tree.identify_row(evento.y)
        if not fila:
            return
        self._billing.quitar_linea(self._invoice, int(fila))
        self._refrescar_lineas()

    def _refrescar_totales(
        self, desgloses: tuple[BsBreakdown | None, BsBreakdown | None] | None = None
    ) -> None:
        simbolo = self._currency_symbol
        self._subtotal_var.set(format_money(self._invoice.subtotal_cents, simbolo))
        self._iva_titulo_var.set(f"IVA ({self._invoice.tax_rate}%)")
        self._iva_var.set(format_money(self._invoice.tax_cents, simbolo))
        self._total_var.set(format_money(self._invoice.total_cents, simbolo))
        self._refrescar_total_convertido(desgloses)

    def _on_toggle_iva(self) -> None:
        """Recalcula los totales en vivo con la tasa de settings o en 0,
        para que se vea el efecto antes de confirmar, no recien en el
        PDF. Tambien corre al iniciar la factura (ver _on_iniciar)."""
        if self._invoice is None:
            return
        self._invoice.tax_rate = (
            self._billing.default_tax_rate()
            if self._cobrar_iva_var.get()
            else Decimal(0)
        )
        # _refrescar_lineas y no solo los totales: el IVA en Bs de cada
        # linea tambien cambia.
        self._refrescar_lineas()

    # ------------------------------------------------------------
    # cerrar
    # ------------------------------------------------------------
    def _on_cerrar(self) -> None:
        """
        El borrador vive en memoria a proposito (ver services/
        billing_service.py): cerrar sin confirmar no deja rastro en la
        base, pero si ya se armaron lineas, perderlas por un click sin
        querer no deberia pasar en silencio.
        """
        if (
            self._invoice is not None
            and self._invoice.lines
            and not messagebox.askyesno(
                "Cerrar",
                f"Hay {len(self._invoice.lines)} linea(s) sin confirmar. "
                "¿Cerrar igual? Se van a perder.",
                parent=self,
            )
        ):
            return
        self.destroy()

    # ------------------------------------------------------------
    # confirmar
    # ------------------------------------------------------------
    def _on_confirmar(self) -> None:
        if self._invoice is None or not self._invoice.lines:
            messagebox.showinfo(
                "Confirmar factura", "Agrega al menos una linea.", parent=self
            )
            return

        indice_forma_pago = self._forma_pago_combo.current()
        if indice_forma_pago < 0:
            messagebox.showerror(
                "Confirmar factura",
                "Elige una forma de pago antes de confirmar.",
                parent=self,
            )
            return
        self._invoice.payment_method = _FORMA_PAGO_OPCIONES[indice_forma_pago][0]

        try:
            self._invoice.issued_at = _parse_fecha_hora(
                self._fecha_var.get(), self._hora_var.get()
            )
        except ValueError:
            messagebox.showerror(
                "Confirmar factura",
                "Fecha/hora invalida. Ejemplo: 15/09/2026 y 14:30.",
                parent=self,
            )
            return

        faltantes = self._billing.lines_below_stock(self._invoice)
        if faltantes:
            detalle = "\n".join(
                f"- {line.product_name}: hay {format_quantity(disponible)}, "
                f"se piden {format_quantity(line.quantity_milli)}"
                for line, disponible in faltantes
            )
            # parent=self (no el default implicito de tkinter, que es la
            # ventana raiz): sin esto, este aviso -el que se dispara
            # justo con un producto en stock 0- se abre detras del
            # propio InvoiceDialog (que tiene grab_set() activo) en vez
            # de encima, y queda invisible hasta mover la ventana.
            if not messagebox.askyesno(
                "Stock insuficiente",
                f"Estas lineas van a quedar con stock negativo:\n\n{detalle}\n\n"
                "¿Confirmar la factura igual?",
                parent=self,
            ):
                return

        # None cuando Modo avanzado esta apagado O quedo tildado "Cobrar
        # IVA" (el default): confirmar() se comporta exactamente igual
        # que siempre, releyendo la tasa de settings. Solo manda un
        # override cuando de verdad se eligio no cobrar en ESTE
        # documento (ver _on_toggle_iva).
        override = None if self._cobrar_iva_var.get() else Decimal(0)

        # La tasa BCV se guarda EN la factura: sus montos en $ ya estan
        # expresados a esa tasa, asi que su PDF en bolívares sale igual
        # hoy que dentro de un mes (regla 5).
        bs_rate = self._rates.rate(RATE_BCV) if self._rates is not None else None
        if (
            self._rates is not None
            and bs_rate is None
            and not messagebox.askyesno(
                "Tasa sin cargar",
                "No hay tasa BCV cargada, así que la factura se guardaría sin "
                "tasa: su PDF en bolívares usaría la tasa BCV vigente el día "
                "que se genere.\n\n¿Confirmar igual?",
                parent=self,
            )
        ):
            return

        # La de referencia solo se guarda para poder volver al precio de
        # catalogo (columna "Total $ catalogo"): ningun monto sale de ella.
        ref_rate = None
        if self._tasa_var.get() == RATE_PERSONAL and self._rates is not None:
            ref_rate = self._rates.rate(RATE_PERSONAL)

        try:
            self._billing.confirmar(
                self._invoice,
                tax_rate_override=override,
                require_fiscal_customer=True,
                bs_rate=bs_rate,
                ref_rate=ref_rate,
            )
        except DomainError as error:
            messagebox.showerror("Confirmar factura", str(error), parent=self)
            return

        self.guardado = True
        numero = self._invoice.display_number
        ventana = self.master
        self.destroy()
        # Un aviso que se va solo, no un cuadro que haya que cerrar con otro
        # clic: la factura ya esta guardada y el cajero sigue con la siguiente.
        toast(ventana, f"Factura {numero} confirmada")


class InvoiceDetailDialog(kit.Dialog):
    """Vista de solo lectura de una factura ya confirmada o cancelada."""

    def __init__(
        self,
        master: tk.Misc,
        invoice: Invoice,
        *,
        currency_symbol: str = "$",
        billing_service: BillingService | None = None,
        settings_service: SettingsService | None = None,
        rate_display_service: RateDisplayService | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(f"Factura {invoice.display_number}")
        self.transient(master)
        self.resizable(True, True)
        # Unico dialogo de la app al que le faltaba: en todos los demas
        # Esc cierra sin tener que ir hasta el boton "Cerrar" con el mouse.
        self.bind("<Escape>", lambda _evento: self.destroy())
        # Opcional (igual criterio que "Crear factura" en ProductsPage):
        # sin el, este dialogo queda de solo lectura, sin el boton de PDF.
        self._service = billing_service
        self._invoice = invoice
        self._simbolo = currency_symbol
        self._rates = rate_display_service
        self._advanced_mode = (
            settings_service.advanced_mode() if settings_service is not None else False
        )

        frame = kit.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)

        kit.Label(frame, text=f"Cliente: {invoice.customer_name}").pack(anchor="w")
        kit.Label(frame, text=f"Estado: {_ESTADO_DISPLAY[invoice.status]}").pack(
            anchor="w"
        )
        if invoice.is_cancelled:
            kit.Label(
                frame, text=f"Motivo de cancelación: {invoice.cancel_reason or ''}"
            ).pack(anchor="w")

        tabla = kit.Frame(frame)
        tabla.pack(side="top", fill="both", expand=True, pady=(8, 8))
        self._tree = lines_tree(
            tabla, tuple(c for c in _COLUMNAS_LINEAS if c[0] != "quitar")
        )

        self._totales_var = tk.StringVar()
        self._totales_bs_var = tk.StringVar()
        kit.Label(frame, textvariable=self._totales_var, style="Heading.TLabel").pack(
            anchor="e"
        )
        kit.Label(frame, textvariable=self._totales_bs_var, style="Muted.TLabel").pack(
            anchor="e"
        )
        self._refrescar()

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

    def _refrescar(self) -> None:
        """
        Mismo formato que Nueva factura. Los Bs salen de la tasa que la
        factura guardo al emitirse (regla 5): volver a abrirla da lo
        mismo aunque el dolar haya subido. Una factura anterior a ese
        campo usa la tasa BCV de hoy.
        """
        guardada = self._invoice.bs_rate
        if guardada is not None:
            desglose = breakdown(self._invoice, guardada)
        elif self._rates is not None:
            # una sola lectura de tasas, no una por linea
            desglose = self._rates.snapshot().invoice_in_bs(self._invoice, RATE_BCV)
        else:
            desglose = None

        self._tree.delete(*self._tree.get_children())
        for indice, line in enumerate(self._invoice.lines):
            self._tree.insert(
                "",
                "end",
                values=valores_de_linea(
                    line,
                    desglose.lines[indice] if desglose else None,
                    self._simbolo,
                ),
            )

        simbolo = self._simbolo
        self._totales_var.set(
            f"Base imponible: {format_money(self._invoice.subtotal_cents, simbolo)}"
            f"    IVA ({self._invoice.tax_rate}%): "
            f"{format_money(self._invoice.tax_cents, simbolo)}"
            f"    TOTAL: {format_money(self._invoice.total_cents, simbolo)}"
        )
        if desglose is None:
            self._totales_bs_var.set(
                "" if self._rates is None else "Tasa sin cargar: no hay montos en Bs."
            )
            return
        cual = "de la factura" if guardada is not None else "de hoy"
        self._totales_bs_var.set(
            f"En Bs (a la tasa {cual}, {str(desglose.rate).replace('.', ',')} Bs/$): "
            f"base {format_money(desglose.subtotal, ' Bs')}"
            f"    IVA {format_money(desglose.tax, ' Bs')}"
            f"    TOTAL {format_money(desglose.total, ' Bs')}"
        )

    def _on_generar_pdf(self) -> None:
        generar_pdf(self, self._service, self._invoice.id)
