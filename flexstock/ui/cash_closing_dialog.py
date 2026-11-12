"""
Dialogo de cierre de caja: cuanto se facturo (status ISSUED) agrupado
por forma de pago, para cuadrar la caja al final del dia y tener
panorama del mes. Notebook con 2 pestanas (Diario/Mensual), mismo
patron general que BackupDialog (paint_window, centrado, grab_set).

Es de SOLO LECTURA: nunca escribe en invoices/invoice_lines ni en
ninguna otra tabla. Los totales salen de
BillingService.cierre_diario()/cierre_mensual(), que a su vez hacen UNA
consulta en InvoiceRepository - nunca un bucle pidiendo factura por
factura (Politica de carga del CLAUDE.md: eso seria el N+1 que prohibe).

Doble clic en una fila abre las facturas que la componen
(_FacturasDelGrupoDialog), y desde ahi el detalle de cualquiera de
ellas: el mismo InvoiceDetailDialog que "Ver detalle" en Facturas, con
su boton de PDF. Es un atajo - antes, para mirar una de las 12 facturas
que el cierre resumia en una linea, habia que ir a la pestana Facturas
y filtrar por fecha a mano. Tambien son dos consultas fijas, no una por
factura: la lista del grupo sin líneas, y las lineas recien al abrir
una.
"""

import tkinter as tk
from datetime import date
from tkinter import messagebox, ttk

import customtkinter as ctk

from ..domain.enums import PaymentMethod
from ..domain.money import convert_cents, format_money
from ..services.billing_service import BillingService
from . import kit
from .theme import ctk_font, paint_window, pair

_MESES = [
    "Enero",
    "Febrero",
    "Marzo",
    "Abril",
    "Mayo",
    "Junio",
    "Julio",
    "Agosto",
    "Septiembre",
    "Octubre",
    "Noviembre",
    "Diciembre",
]

# Mismo texto que _FORMA_PAGO_DISPLAY en ui/billing_page.py - duplicado
# a proposito y no importado desde ahi: billing_page.py abre este
# dialogo desde el boton "Cierre de caja" de su barra, asi que
# importar de vuelta hacia billing_page.py cerraria un ciclo de
# imports entre los dos modulos. Mismo criterio que export_service.py
# usa con _ESTADO_CSV (duplicar un dict chico en vez de pelear con el
# grafo de imports), aca por una razon distinta (ciclo ui<->ui en vez
# de la regla services/ no importa de ui/).
_FORMA_PAGO_DISPLAY = {
    PaymentMethod.EFECTIVO_BS: "Efectivo Bs",
    PaymentMethod.EFECTIVO_USD: "Efectivo USD",
    PaymentMethod.TRANSFERENCIA: "Transferencia / Pago movil",
    PaymentMethod.TARJETA: "Tarjeta (punto de venta)",
    PaymentMethod.ZELLE: "Zelle",
}
_SIN_ESPECIFICAR = "Sin especificar (factura anterior a este cambio)"
# iid de la fila del grupo sin forma de pago. Un iid vacio no es valido
# en Tk y None no se puede guardar ahi, asi que hace falta un centinela.
_SIN_METODO_IID = "__sin_metodo__"


def _forma_pago_texto(metodo: PaymentMethod | None) -> str:
    return _FORMA_PAGO_DISPLAY.get(metodo, _SIN_ESPECIFICAR)


def _recuperar_modal_al_cerrarse(hijo: tk.Toplevel, padre: tk.Toplevel) -> None:
    """
    Le devuelve el grab modal a 'padre' cuando 'hijo' se cierra.

    Con wait_window() en su lugar, quien abre el hijo se queda BLOQUEADO
    hasta que alguien lo cierre. Eso esta bien cuando el hijo se cierra
    solo, pero acá el hijo es una ventana que el usuario cierra a mano:
    bloquear ahi deja al que abre sin poder responder (y cuelga
    cualquier test que llame al handler sin un mainloop de verdad
    atras). Con <Destroy> no se bloquea nadie.

    El 'is' importa: <Destroy> tambien sube desde cada widget de adentro
    del hijo, y sin el chequeo el grab volveria al padre con la ventana
    todavia abierta.
    """

    def _al_cerrarse(evento: tk.Event) -> None:
        if evento.widget is hijo and padre.winfo_exists():
            padre.grab_set()

    hijo.bind("<Destroy>", _al_cerrarse)


class CashClosingDialog(kit.Dialog):
    def __init__(
        self,
        master: tk.Misc,
        billing_service: BillingService,
        *,
        currency_symbol: str = "$",
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Cierre de caja")
        self.transient(master)
        self.resizable(True, True)

        self._billing = billing_service
        self._currency_symbol = currency_symbol

        hoy = date.today()  # noqa: DTZ011 - hora local, igual que issued_at
        self._dia_anio_var = tk.StringVar(value=str(hoy.year))
        self._dia_mes_var = tk.StringVar(value=_MESES[hoy.month - 1])
        self._dia_dia_var = tk.StringVar(value=str(hoy.day))
        self._mes_anio_var = tk.StringVar(value=str(hoy.year))
        self._mes_mes_var = tk.StringVar(value=_MESES[hoy.month - 1])

        self._build_form()
        self.bind("<Escape>", lambda _evento: self.destroy())
        self.update_idletasks()
        self._centrar_sobre(master)
        # lift() fuerza el apilamiento encima de BillingPage: transient()
        # sola no siempre alcanza (se vio pasar en Windows).
        self.lift()
        self.focus_force()
        self.grab_set()

        # Cargar el cierre de hoy/este mes apenas se abre: no tiene
        # sentido mostrar una tabla vacia esperando un primer click en
        # "Calcular" cuando el 99% de las veces es justo hoy/este mes
        # lo que se quiere ver.
        self._on_calcular_diario()
        self._on_calcular_mensual()

    def _centrar_sobre(self, master: tk.Misc) -> None:
        # req*: el dialogo todavia esta escondido (ver kit.Dialog) y
        # winfo_width() de una ventana sin mostrar vale 1.
        self.update_idletasks()
        ancho, alto = self.winfo_reqwidth(), self.winfo_reqheight()
        x = master.winfo_rootx() + (master.winfo_width() - ancho) // 2
        y = master.winfo_rooty() + (master.winfo_height() - alto) // 2
        self.geometry(f"{ancho}x{alto}+{max(x, 0)}+{max(y, 0)}")
        self._posicion_inicial = (max(x, 0), max(y, 0))
        self.minsize(ancho, alto)

    # ------------------------------------------------------------
    # construccion
    # ------------------------------------------------------------
    def _build_form(self) -> None:
        frame = kit.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        # Pestanas de CustomTkinter: un selector segmentado arriba en vez
        # de las solapas de ttk.Notebook.
        pestanas = ctk.CTkTabview(
            frame,
            fg_color=pair("surface"),
            border_width=1,
            border_color=pair("border"),
            corner_radius=12,
        )
        pestanas.pack(fill="both", expand=True)
        pestanas._segmented_button.configure(font=ctk_font(self, "body"))
        self._pestanas = pestanas

        diario = kit.Frame(pestanas.add("Diario"), padding=6)
        diario.pack(fill="both", expand=True)
        self._build_diario(diario)

        mensual = kit.Frame(pestanas.add("Mensual"), padding=6)
        mensual.pack(fill="both", expand=True)
        self._build_mensual(mensual)

        kit.Button(frame, text="Cerrar", command=self.destroy).pack(pady=(12, 0))

    def _build_diario(self, parent: kit.Frame) -> None:
        selector = kit.Frame(parent)
        selector.pack(side="top", fill="x")
        kit.Label(selector, text="Fecha:").pack(side="left")
        kit.Spinbox(
            selector, from_=2000, to=2100, width=6, textvariable=self._dia_anio_var
        ).pack(side="left", padx=(4, 4))
        kit.Combobox(
            selector,
            state="readonly",
            width=11,
            textvariable=self._dia_mes_var,
            values=_MESES,
        ).pack(side="left", padx=(0, 4))
        kit.Spinbox(
            selector, from_=1, to=31, width=4, textvariable=self._dia_dia_var
        ).pack(side="left")
        kit.Button(selector, text="Calcular", command=self._on_calcular_diario).pack(
            side="left", padx=(12, 0)
        )

        self._tree_diario, self._total_diario_var = self._build_tabla_y_total(parent)

    def _build_mensual(self, parent: kit.Frame) -> None:
        selector = kit.Frame(parent)
        selector.pack(side="top", fill="x")
        kit.Label(selector, text="Mes:").pack(side="left")
        kit.Spinbox(
            selector, from_=2000, to=2100, width=6, textvariable=self._mes_anio_var
        ).pack(side="left", padx=(4, 4))
        kit.Combobox(
            selector,
            state="readonly",
            width=11,
            textvariable=self._mes_mes_var,
            values=_MESES,
        ).pack(side="left")
        kit.Button(selector, text="Calcular", command=self._on_calcular_mensual).pack(
            side="left", padx=(12, 0)
        )

        self._tree_mensual, self._total_mensual_var = self._build_tabla_y_total(parent)

    def _build_tabla_y_total(
        self, parent: kit.Frame
    ) -> tuple[ttk.Treeview, tk.StringVar]:
        tree = ttk.Treeview(
            parent,
            columns=("forma_pago", "cantidad", "total", "total_bs"),
            show="headings",
            height=6,
        )
        tree.heading("forma_pago", text="Forma de pago")
        tree.heading("cantidad", text="Facturas")
        tree.heading("total", text="Total $")
        # Lo que de verdad hay que contar en el cajon. Cada factura pone
        # sus bolívares con la tasa que ella guardo (ver
        # PaymentMethodTotal), no con la de hoy.
        tree.heading("total_bs", text="Total Bs")
        tree.column("forma_pago", width=240)
        tree.column("cantidad", width=80, anchor="center")
        tree.column("total", width=120, anchor="e")
        tree.column("total_bs", width=160, anchor="e")
        # Doble clic abre las facturas que hay detras de la fila: el
        # cierre dice "12 facturas, 67,56$" y desde ahi se llega a
        # cualquiera de las 12 sin pasar por la lista de Facturas y
        # ponerse a filtrar por fecha a mano.
        tree.bind("<Double-1>", lambda _evento, t=tree: self._on_ver_facturas(t))
        tree.pack(side="top", fill="both", expand=True, pady=(10, 4))

        total_var = tk.StringVar(value="Total general: -")
        kit.Label(parent, textvariable=total_var, style="Heading.TLabel").pack(
            anchor="e"
        )
        return tree, total_var

    # ------------------------------------------------------------
    # calcular / poblar
    # ------------------------------------------------------------
    def _on_calcular_diario(self) -> None:
        try:
            dia = self._fecha_diaria_elegida()
        except ValueError:
            messagebox.showerror(
                "Cierre de caja",
                "La fecha elegida no es válida (ej. 31 de febrero).",
                parent=self,
            )
            return
        cierre = self._billing.cierre_diario(dia)
        self._poblar(self._tree_diario, self._total_diario_var, cierre)

    def _on_calcular_mensual(self) -> None:
        try:
            anio, mes = self._mes_elegido()
        except ValueError:
            messagebox.showerror(
                "Cierre de caja", "El mes elegido no es válido.", parent=self
            )
            return
        cierre = self._billing.cierre_mensual(anio, mes)
        self._poblar(self._tree_mensual, self._total_mensual_var, cierre)

    def _fecha_diaria_elegida(self) -> date:
        anio = int(self._dia_anio_var.get())
        mes = _MESES.index(self._dia_mes_var.get()) + 1
        dia = int(self._dia_dia_var.get())
        return date(anio, mes, dia)  # ValueError si el dia no existe en ese mes

    def _mes_elegido(self) -> tuple[int, int]:
        anio = int(self._mes_anio_var.get())
        mes = _MESES.index(self._mes_mes_var.get()) + 1
        return anio, mes

    def _poblar(
        self,
        tree: ttk.Treeview,
        total_var: tk.StringVar,
        cierre,
    ) -> None:
        tree.delete(*tree.get_children())
        total_general_cents = 0
        total_general_bs = 0
        for fila in cierre:
            tree.insert(
                "",
                "end",
                # El iid guarda la forma de pago de la fila, para que
                # _on_ver_facturas pueda volver a pedir exactamente ese
                # grupo. _SIN_METODO_IID y no "" porque un iid vacio no
                # es valido en Tk, y None no se puede serializar.
                iid=(
                    fila.payment_method.value
                    if fila.payment_method is not None
                    else _SIN_METODO_IID
                ),
                values=(
                    _forma_pago_texto(fila.payment_method),
                    fila.count,
                    format_money(fila.total_cents, self._currency_symbol),
                    format_money(fila.total_bs_cents, " Bs"),
                ),
            )
            total_general_cents += fila.total_cents
            total_general_bs += fila.total_bs_cents
        total_var.set(
            f"Total general: {format_money(total_general_cents, self._currency_symbol)}"
            f"    |    {format_money(total_general_bs, ' Bs')}"
        )

    # ------------------------------------------------------------
    # ver las facturas detras de una fila
    # ------------------------------------------------------------
    def _on_ver_facturas(self, tree: ttk.Treeview) -> None:
        """
        Doble clic en una forma de pago: abre la lista de las facturas
        que la componen. Es el atajo que faltaba - el cierre dice "12
        facturas, 67,56$" y antes, para mirar una, habia que ir a la
        pestana Facturas y filtrar por fecha a mano.

        El periodo sale de la pestana en la que se hizo el doble clic:
        el mismo rango que ya sumo esa fila, para que la lista y el
        total no puedan discrepar.
        """
        seleccion = tree.selection()
        if not seleccion:
            return
        iid = seleccion[0]
        metodo = None if iid == _SIN_METODO_IID else PaymentMethod(iid)

        try:
            if tree is self._tree_diario:
                facturas = self._billing.facturas_del_cierre_diario(
                    self._fecha_diaria_elegida(), metodo
                )
                periodo = self._fecha_diaria_elegida().strftime("%d/%m/%Y")
            else:
                anio, mes = self._mes_elegido()
                facturas = self._billing.facturas_del_cierre_mensual(anio, mes, metodo)
                periodo = f"{_MESES[mes - 1]} {anio}"
        except ValueError:
            # La fecha ya se valido al calcular; si igual fallara, se
            # avisa en vez de reventar con la ventana abierta.
            messagebox.showerror(
                "Cierre de caja", "La fecha elegida no es válida.", parent=self
            )
            return

        if not facturas:
            messagebox.showinfo(
                "Cierre de caja",
                "Esa fila no tiene facturas para mostrar.",
                parent=self,
            )
            return

        dialogo = _FacturasDelGrupoDialog(
            self,
            facturas,
            billing_service=self._billing,
            titulo=f"{_forma_pago_texto(metodo)} - {periodo}",
            currency_symbol=self._currency_symbol,
        )
        # El hijo toma el grab modal y lo suelta del todo al cerrarse:
        # sin esto, este dialogo se quedaría sin modal.
        _recuperar_modal_al_cerrarse(dialogo, self)


class _FacturasDelGrupoDialog(kit.Dialog):
    """
    Las facturas de una fila del cierre. Solo lectura: doble clic (o el
    boton) abre el detalle de la factura, la misma ventana que "Ver
    detalle" en Facturas, con su boton de PDF.
    """

    _COLUMNAS = (
        ("numero", "Número", 140, "w"),
        ("fecha", "Fecha", 150, "w"),
        ("cliente", "Cliente", 220, "w"),
        ("total", "Total $", 120, "e"),
        ("total_bs", "Total Bs", 160, "e"),
    )

    def __init__(
        self,
        master: tk.Misc,
        facturas: list,
        *,
        billing_service: BillingService,
        titulo: str,
        currency_symbol: str = "$",
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(f"Facturas - {titulo}")
        self.transient(master)
        self.resizable(True, True)
        self.bind("<Escape>", lambda _evento: self.destroy())

        self._billing = billing_service
        self._simbolo = currency_symbol

        frame = kit.Frame(self, padding=12)
        frame.pack(fill="both", expand=True)
        kit.Label(frame, text=titulo, style="Heading.TLabel").pack(anchor="w")

        tabla = kit.Frame(frame)
        tabla.pack(fill="both", expand=True, pady=(8, 4))
        self._tree = ttk.Treeview(
            tabla,
            columns=tuple(c[0] for c in self._COLUMNAS),
            show="headings",
            height=min(len(facturas), 12),
        )
        for clave, encabezado, ancho, anclaje in self._COLUMNAS:
            self._tree.heading(clave, text=encabezado)
            self._tree.column(clave, width=ancho, anchor=anclaje)
        scroll = kit.Scrollbar(tabla, orient="vertical", command=self._tree.yview)
        self._tree.configure(yscrollcommand=scroll.set)
        self._tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self._tree.bind("<Double-1>", lambda _evento: self._on_detalle())

        for factura in facturas:
            self._tree.insert(
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
                    # Los Bs de cada factura salen de SU tasa (regla 5),
                    # igual que la columna del cierre. Sin tasa guardada
                    # se muestra un guion en vez de inventar un monto.
                    format_money(
                        convert_cents(factura.total_cents, factura.bs_rate),
                        " Bs",
                    )
                    if factura.bs_rate is not None
                    else "-",
                ),
            )

        kit.Label(
            frame,
            text="Doble clic en una factura para verla y generar su PDF.",
            style="Muted.TLabel",
        ).pack(anchor="w")

        botones = kit.Frame(frame)
        botones.pack(side="top", pady=(10, 0))
        kit.Button(botones, text="Ver detalle", command=self._on_detalle).pack(
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

    def _on_detalle(self) -> None:
        """
        Abre InvoiceDetailDialog. El import es local a proposito:
        billing_page importa este modulo (para el boton "Cierre de
        caja" de su barra), asi que un import arriba cerraria un ciclo.
        """
        from .billing_page import InvoiceDetailDialog

        seleccion = self._tree.selection()
        if not seleccion:
            messagebox.showinfo(
                "Ver detalle", "Selecciona una factura primero.", parent=self
            )
            return

        factura = self._billing.get_invoice(int(seleccion[0]))
        if factura is None:
            messagebox.showerror("Ver detalle", "La factura ya no existe.", parent=self)
            return

        dialogo = InvoiceDetailDialog(
            self,
            factura,
            currency_symbol=self._simbolo,
            billing_service=self._billing,
        )
        _recuperar_modal_al_cerrarse(dialogo, self)
