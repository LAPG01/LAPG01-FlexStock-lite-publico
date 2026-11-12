"""
Inicio: el tablero con el que arranca la aplicacion.

Cuatro numeros que el dueno quiere saber apenas abre (cuanto se vendio
hoy y en el mes, cuantos productos estan sin existencia, cuando fue el
ultimo respaldo), las ultimas facturas, y atajos a lo que mas se hace.

No agrega reglas de negocio ni consultas propias: todo sale de los
servicios que ya usan las otras paginas. Al abrir la pagina son cinco
consultas chicas:

    cierre_diario(hoy)        1 consulta agregada (la del cierre de caja)
    cierre_mensual(mes)       1 consulta agregada
    stock_overview()          1 consulta con tres conteos
    list_invoices()           1 consulta (sin líneas)
    last_backup_at()          ninguna: mira la carpeta de respaldos

Los bolívares de "Ventas" son los del cierre de caja: cada factura con
la tasa con que se emitio (regla 5), nunca el total en $ por la tasa de
hoy.
"""

from __future__ import annotations

import tkinter as tk
from collections.abc import Callable
from datetime import date, datetime
from tkinter import font as tkfont
from tkinter import ttk

import customtkinter as ctk

from ..domain.entities import Invoice, PaymentMethodTotal, Product
from ..domain.enums import InvoiceStatus
from ..domain.money import format_money, format_quantity
from ..services.backup_service import BackupService
from ..services.billing_service import BillingService
from ..services.product_service import ProductService
from ..services.stock_service import StockService
from . import icons, kit
from .cash_closing_dialog import CashClosingDialog
from .stock_adjustment_page import AjustarExistenciaDialog
from .theme import ctk_font, get_palette, pair
from .widgets import alinear_encabezados, wrap_to_width

# Despues de cuantos dias sin respaldar la tarjeta se pone en rojo.
DIAS_SIN_RESPALDO_ALERTA = 7

_MESES = (
    "enero",
    "febrero",
    "marzo",
    "abril",
    "mayo",
    "junio",
    "julio",
    "agosto",
    "septiembre",
    "octubre",
    "noviembre",
    "diciembre",
)


def _repartir(
    tree: ttk.Treeview, disponible: int, muestras: dict[str, str], *, cede: str
) -> None:
    """
    Anchos de 'tree' para 'disponible' px: cada columna mide lo que su
    titulo o su dato de muestra (lo mayor, con la letra de ahora) y la
    columna 'cede' se lleva lo que sobra, o pierde lo que falta (sin bajar
    del ancho de su titulo). Se llama en cada <Configure>: ttk solo agranda columnas,
    nunca las achica, y al angostar la ventana el final quedaba cortado.
    """
    if disponible <= 1:
        return
    fuente_titulo = tkfont.Font(
        font=ttk.Style(tree).lookup("Treeview.Heading", "font") or "TkHeadingFont"
    )
    fuente = tkfont.nametofont("TkDefaultFont")
    anchos = {
        col: max(
            fuente_titulo.measure(str(tree.heading(col, "text"))),
            fuente.measure(muestra),
        )
        + 24
        for col, muestra in muestras.items()
    }
    resto = disponible - 4 - sum(a for c, a in anchos.items() if c != cede)
    # Nunca por debajo de su propio titulo ("Clie" no dice nada).
    anchos[cede] = max(
        resto, fuente_titulo.measure(str(tree.heading(cede, "text"))) + 24
    )
    for col, ancho in anchos.items():
        if int(tree.column(col, "width")) != ancho:
            tree.column(col, width=ancho)


class StatCard(kit.Frame):
    """Una cifra del tablero: icono, titulo chico, valor grande y detalle."""

    def __init__(
        self,
        master: tk.Misc,
        *,
        title: str,
        icon: str,
        on_click: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master, style="Card.TFrame", padding=(16, 14))
        self._on_click = on_click
        self.value_var = tk.StringVar(value="-")
        self.detail_var = tk.StringVar(value="")

        self._insignia = ctk.CTkLabel(
            self,
            text="",
            width=44,
            height=44,
            corner_radius=12,
            fg_color=pair("select_bg"),
            image=icons.icon(self, icon, pair("accent"), size=22),
        )
        self._insignia.pack(side="left", anchor="n", padx=(0, 14))

        textos = kit.Frame(self)
        textos.pack(side="left", fill="both", expand=True)
        # Titulo y detalle se parten en dos renglones si no entran (letra al
        # 150 % en una pantalla chica), en vez de cortarse.
        titulo = kit.Label(textos, text=title.upper(), style="CardTitle.TLabel")
        titulo.pack(anchor="w", fill="x")
        wrap_to_width(titulo)
        self.value_label = kit.Label(
            textos, textvariable=self.value_var, style="CardValue.TLabel"
        )
        self.value_label.pack(anchor="w")
        self.detail_label = kit.Label(
            textos, textvariable=self.detail_var, style="CardDate.TLabel"
        )
        self.detail_label.pack(anchor="w", fill="x")
        wrap_to_width(self.detail_label)
        if on_click is not None:
            # Toda la tarjeta es el boton: el clic se ata a ella y a cada
            # hijo (CTk dibuja con widgets internos que se comen el evento).
            self._atar_clic(self)

    def _atar_clic(self, widget: tk.Misc) -> None:
        widget.bind("<Button-1>", lambda _e: self._on_click(), add="+")
        try:
            widget.configure(cursor="hand2")
        except (tk.TclError, ValueError):
            pass  # algun widget interno de CTk no acepta cursor
        for hijo in widget.winfo_children():
            self._atar_clic(hijo)

    def set(self, value: str, detail: str = "", *, alerta: bool = False) -> None:
        self.value_var.set(value)
        self.detail_var.set(detail)
        self.value_label.configure(
            style="Error.TLabel" if alerta else "CardValue.TLabel"
        )
        if alerta:
            self.value_label.configure(font=ctk_font(self, "card_value"))


class HomePage(kit.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        billing_service: BillingService,
        product_service: ProductService,
        backup_service: BackupService,
        on_nueva_factura: Callable[[], None] | None = None,
        on_ir_a: Callable[[str], None] | None = None,
        on_respaldar: Callable[[], None] | None = None,
        on_ver_respaldos: Callable[[], None] | None = None,
        stock_service: StockService | None = None,
    ) -> None:
        super().__init__(master)
        self._billing = billing_service
        self._products = product_service
        self._backups = backup_service
        self._stock = stock_service
        self._on_ver_respaldos = on_ver_respaldos
        # Motivo y "Contado por" de "Ajustar existencia", para el siguiente.
        self._recuerdo_ajuste: dict[str, str] = {}
        self._atencion: dict[str, Product] = {}
        self._on_nueva_factura = on_nueva_factura
        self._on_ir_a = on_ir_a or (lambda _titulo: None)
        self._on_respaldar = on_respaldar
        self._simbolo = billing_service.currency_symbol()

        self._build()
        self.reload()

    # ------------------------------------------------------------
    def _build(self) -> None:
        tarjetas = kit.Frame(self)
        tarjetas.pack(side="top", fill="x")
        # Cada tarjeta lleva a donde se ve el detalle de su cifra.
        self.card_hoy = StatCard(
            tarjetas, title="Ventas de hoy", icon="receipt", on_click=self._abrir_cierre
        )
        self.card_mes = StatCard(
            tarjetas,
            title="Ventas del mes",
            icon="calculator",
            on_click=self._abrir_cierre,
        )
        self.card_stock = StatCard(
            tarjetas,
            title="En negativo",
            icon="package",
            on_click=lambda: self._on_ir_a("Productos"),
        )
        self.card_respaldo = StatCard(
            tarjetas,
            title="Último respaldo",
            icon="database-backup",
            on_click=self._ver_respaldos,
        )
        for columna, tarjeta in enumerate(
            (self.card_hoy, self.card_mes, self.card_stock, self.card_respaldo)
        ):
            tarjetas.columnconfigure(columna, weight=1, uniform="tarjetas")
            tarjeta.grid(
                row=0,
                column=columna,
                sticky="nsew",
                padx=(0 if columna == 0 else 12, 0),
            )

        abajo = kit.Frame(self)
        abajo.pack(side="top", fill="both", expand=True, pady=(16, 0))
        # La derecha (atajos y "Requiere atencion") con lugar para leer el
        # nombre de un producto; las facturas se llevan el resto.
        abajo.columnconfigure(0, weight=3)
        abajo.columnconfigure(1, weight=2, minsize=340)
        abajo.rowconfigure(0, weight=1)

        self._build_ultimas_facturas(abajo)
        derecha = kit.Frame(abajo)
        derecha.grid(row=0, column=1, sticky="nsew")
        derecha.columnconfigure(0, weight=1)
        derecha.rowconfigure(1, weight=1)
        self._build_atajos(derecha)
        self._build_atencion(derecha)

    def _build_ultimas_facturas(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(parent, text="Últimas facturas", padding=12)
        seccion.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
        columnas = (
            ("numero", "Número", 130, "w"),
            ("fecha", "Fecha", 190, "w"),
            ("cliente", "Cliente", 220, "w"),
            ("total", "Total", 120, "e"),
            ("estado", "Estado", 120, "w"),
        )
        # El boton antes que la tabla, abajo: cuando falta alto (letra al
        # 150 %) cede la tabla, que tiene scroll, y no el boton.
        kit.Button(
            seccion,
            text="Ver todas las facturas",
            icon="receipt",
            command=lambda: self._on_ir_a("Facturas"),
        ).pack(side="bottom", anchor="e", pady=(10, 0))
        self.tree = ttk.Treeview(
            seccion,
            columns=[c[0] for c in columnas],
            show="headings",
            height=8,
            selectmode="browse",
        )
        for clave, titulo, ancho, anchor in columnas:
            self.tree.heading(clave, text=titulo)
            self.tree.column(clave, width=ancho, anchor=anchor, stretch=False)
        alinear_encabezados(self.tree)
        self.tree.pack(fill="both", expand=True)
        # Ttk no achica columnas cuando la tabla se angosta: se reparte el
        # ancho a mano. Cada columna conserva lo que mide su dato tipico (con
        # la letra de ahora) y el Cliente cede primero.
        muestras = {
            "numero": "FAC-000000",
            "fecha": "00/00/0000 00:00",
            "cliente": "Cliente",
            "total": "00.000,00$",
            "estado": "Anulada",
        }
        self.tree.bind(
            "<Configure>",
            lambda e: _repartir(self.tree, e.width, muestras, cede="cliente"),
        )
        self._vacio = kit.Label(
            seccion, text="Todavía no hay facturas emitidas.", style="Muted.TLabel"
        )

    def _build_atajos(self, parent: kit.Frame) -> None:
        seccion = kit.LabelFrame(parent, text="Acciones rápidas", padding=12)
        seccion.grid(row=0, column=0, sticky="ew")
        for texto, icono, estilo, comando in (
            ("Nueva factura", "plus", "Success.TButton", self._nueva_factura),
            (
                "Ajustar stock",
                "clipboard-check",
                None,
                lambda: self._on_ir_a("Ajuste de stock"),
            ),
            ("Buscar producto", "search", None, lambda: self._on_ir_a("Productos")),
            ("Respaldar ahora", "database-backup", None, self._respaldar),
        ):
            kit.Button(
                seccion, text=texto, icon=icono, style=estilo, command=comando
            ).pack(fill="x", pady=4)

    def _build_atencion(self, parent: kit.Frame) -> None:
        """
        Lo que conviene revisar hoy: existencia negativa (en rojo) y bajo
        su minimo (naranja). Doble clic o Enter abre "Ajustar existencia"
        de ese producto ahi mismo.
        """
        seccion = kit.LabelFrame(parent, text="Requiere atención", padding=12)
        seccion.grid(row=1, column=0, sticky="nsew", pady=(16, 0))
        self.tree_atencion = ttk.Treeview(
            seccion,
            columns=("producto", "existencia"),
            show="headings",
            height=4,
            selectmode="browse",
        )
        self.tree_atencion.heading("producto", text="Producto")
        self.tree_atencion.heading("existencia", text="Existencia")
        # Existencia a la medida de su titulo y de "-1.000 METRO" con la letra
        # de ahora (a 150 % un ancho fijo cortaba el titulo); el nombre del
        # producto se lleva el resto.
        fuente_titulo = tkfont.Font(
            font=ttk.Style(self).lookup("Treeview.Heading", "font") or "TkHeadingFont"
        )
        fuente = tkfont.nametofont("TkDefaultFont")
        ancho_existencia = (
            max(fuente_titulo.measure("Existencia"), fuente.measure("-1.000 METRO"))
            + 28
        )
        self.tree_atencion.column("producto", width=160, anchor="w", stretch=False)
        self.tree_atencion.column(
            "existencia", width=ancho_existencia, anchor="e", stretch=False
        )
        alinear_encabezados(self.tree_atencion)
        self.tree_atencion.pack(fill="both", expand=True)
        self.tree_atencion.bind(
            "<Configure>",
            lambda e: _repartir(
                self.tree_atencion,
                e.width,
                {"producto": "Producto", "existencia": "-1.000 METRO"},
                cede="producto",
            ),
        )
        self.tree_atencion.bind("<Double-1>", lambda _e: self._ajustar_elegido())
        self.tree_atencion.bind("<Return>", lambda _e: self._ajustar_elegido())
        self._atencion_vacio = kit.Label(
            seccion, text="Nada pendiente.", style="Muted.TLabel"
        )

    # ------------------------------------------------------------
    def reload(self) -> None:
        hoy = date.today()  # noqa: DTZ011
        self._mostrar_ventas(self.card_hoy, self._billing.cierre_diario(hoy), "hoy")
        self._mostrar_ventas(
            self.card_mes,
            self._billing.cierre_mensual(hoy.year, hoy.month),
            f"en {_MESES[hoy.month - 1]}",
        )
        self._mostrar_stock()
        self._mostrar_atencion()
        self.refrescar_respaldo()
        self._mostrar_ultimas(self._billing.list_invoices())

    def _mostrar_ventas(
        self, tarjeta: StatCard, filas: list[PaymentMethodTotal], cuando: str
    ) -> None:
        total = sum(f.total_cents for f in filas)
        total_bs = sum(f.total_bs_cents for f in filas)
        cantidad = sum(f.count for f in filas)
        if cantidad == 0:
            tarjeta.set(format_money(0, self._simbolo), f"Sin ventas {cuando}")
            return
        facturas = "factura" if cantidad == 1 else "facturas"
        tarjeta.set(
            format_money(total, self._simbolo),
            f"{cantidad} {facturas} · {format_money(total_bs, ' Bs')}",
        )

    def _mostrar_stock(self) -> None:
        """
        Lo que se puede corregir (negativos), no lo que falta contar: el
        inventario se cuenta de a poco (CLAUDE.md), y un "1.932 sin
        existencia" en rojo durante meses se aprende a ignorar. Abajo, el
        avance del conteo.
        """
        resumen = self._products.stock_overview()
        self.card_stock.set(
            str(resumen.negative),
            f"{resumen.counted} de {resumen.active} contados",
            alerta=resumen.negative > 0,
        )

    def _mostrar_atencion(self) -> None:
        listar = getattr(self._products, "list_needing_attention", None)
        productos = listar() if listar is not None else []
        tree = self.tree_atencion
        tree.delete(*tree.get_children())
        self._atencion = {}
        paleta = get_palette(self)
        tree.tag_configure("negativo", foreground=paleta.danger_bg)
        tree.tag_configure("bajo", foreground=paleta.warning_bg)
        for producto in productos:
            iid = str(producto.id)
            self._atencion[iid] = producto
            tree.insert(
                "",
                "end",
                iid=iid,
                values=(
                    producto.name,
                    f"{format_quantity(producto.current_stock)} {producto.unit}",
                ),
                tags=("negativo",) if producto.current_stock < 0 else ("bajo",),
            )
        if productos:
            self._atencion_vacio.pack_forget()
        else:
            self._atencion_vacio.pack(before=tree, anchor="w", pady=(0, 8))

    def _ajustar_elegido(self) -> None:
        seleccion = self.tree_atencion.selection()
        producto = self._atencion.get(seleccion[0]) if seleccion else None
        if producto is None:
            return
        if self._stock is None:
            self._on_ir_a("Productos")
            return
        dialogo = AjustarExistenciaDialog(
            self,
            self._stock,
            product_id=producto.id,
            sku=producto.sku,
            nombre=producto.name,
            unidad=producto.unit,
            existencia_milli=producto.current_stock,
            recuerdo=self._recuerdo_ajuste,
        )
        self.wait_window(dialogo)
        if dialogo.resultado is not None:
            self.reload()

    def _abrir_cierre(self) -> None:
        dialogo = CashClosingDialog(self, self._billing, currency_symbol=self._simbolo)
        self.wait_window(dialogo)

    def _ver_respaldos(self) -> None:
        if self._on_ver_respaldos is not None:
            self._on_ver_respaldos()

    def refrescar_respaldo(self) -> None:
        ultimo = self._backups.last_backup_at()
        if ultimo is None:
            self.card_respaldo.set("Nunca", "Respalda hoy: es un minuto", alerta=True)
            return
        dias = (datetime.now() - ultimo).days  # noqa: DTZ005
        if dias == 0:
            valor = "Hoy"
        elif dias == 1:
            valor = "Ayer"
        else:
            valor = f"Hace {dias} días"
        self.card_respaldo.set(
            valor,
            ultimo.strftime("%d/%m/%Y %H:%M"),
            alerta=dias >= DIAS_SIN_RESPALDO_ALERTA,
        )

    def _mostrar_ultimas(self, facturas: list[Invoice], limite: int = 8) -> None:
        self.tree.delete(*self.tree.get_children())
        # Por id y no por issued_at: la fecha de elaboracion se puede
        # editar a mano (una venta de ayer cargada hoy), el id no.
        recientes = sorted(facturas, key=lambda f: f.id or 0, reverse=True)[:limite]
        for indice, factura in enumerate(recientes):
            anulada = factura.status == InvoiceStatus.CANCELLED
            self.tree.insert(
                "",
                "end",
                iid=str(factura.id),
                values=(
                    factura.display_number,
                    factura.issued_at.strftime("%d/%m/%Y %H:%M")
                    if factura.issued_at
                    else "",
                    factura.customer_name,
                    format_money(factura.total_cents, self._simbolo),
                    "Anulada" if anulada else "Emitida",
                ),
                tags=("anulada",) if anulada else (("franja",) if indice % 2 else ()),
            )
        self.tree.tag_configure("anulada", foreground=get_palette(self).muted_fg)
        if recientes:
            self._vacio.pack_forget()
        else:
            self._vacio.pack(before=self.tree, anchor="w", pady=(0, 8))

    # ------------------------------------------------------------
    def _nueva_factura(self) -> None:
        if self._on_nueva_factura is not None:
            self._on_nueva_factura()
        self.reload()

    def _respaldar(self) -> None:
        if self._on_respaldar is not None:
            self._on_respaldar()
        self.refrescar_respaldo()
