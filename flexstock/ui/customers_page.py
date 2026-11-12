"""
Pagina de clientes: tabla + busqueda + alta/edicion/baja basicas.

Mismo patron que ProductsPage: carga todo en memoria una vez y filtra
con una comprension de lista (la cantidad de clientes va a ser bastante
menor que las 1.936 filas del catalogo, asi que ni hace falta remedirlo).

ui/ solo llama a CustomerService: nunca importa nada de flexstock.data.
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import messagebox, ttk

from ..domain.entities import Customer, Invoice
from ..domain.money import format_money
from ..services.billing_service import BillingService
from ..services.customer_service import TAX_ID_TYPES, CustomerService
from ..services.rate_display_service import RateDisplayService
from ..services.settings_service import SettingsService
from . import columns, kit
from .search import split_search_results
from .theme import paint_text, paint_window
from .widgets import (
    ScrollableDialogBody,
    SearchableTable,
    fit_scrollable_dialog_to_screen,
    focus_first_entry,
    info_hint,
    tab_pasa_al_siguiente,
)

# Que columnas se ven las decide ui/columns.py + Configuracion >
# Personalizacion; aca solo vive el texto de cada celda (_valores_de).
MODULO = columns.CUSTOMERS


class CustomersPage(kit.Frame):
    """Contenedor autonomo: se puede empotrar en cualquier ventana padre."""

    def __init__(
        self,
        master: tk.Misc,
        customer_service: CustomerService,
        *,
        billing_service: BillingService | None = None,
        settings_service: SettingsService | None = None,
        rate_display_service: RateDisplayService | None = None,
    ) -> None:
        super().__init__(master)
        self._service = customer_service
        # Opcional (igual criterio que "Crear factura" en ProductsPage):
        # sin el, la pagina funciona igual, solo que CustomerDialog no
        # puede mostrar el historial de compras (ver _on_editar).
        self._billing_service = billing_service
        # Sin el, la tabla usa las columnas por defecto del catalogo.
        self._settings_service = settings_service
        # Solo se usa de paso: el detalle de una factura vieja (sin
        # bs_rate guardada, regla 5) la pasa a Bs con la BCV de hoy.
        self._rate_display_service = rate_display_service
        self._clientes: list[Customer] = []  # catalogo completo, en memoria
        self._columnas = self._columnas_visibles()

        self._build_widgets()
        self._search_var.trace_add("write", self._on_buscar)

        self.reload()

    # ------------------------------------------------------------
    # construccion de widgets
    # ------------------------------------------------------------
    def _build_widgets(self) -> None:
        self._table = SearchableTable(
            self,
            columns=tuple(c.key for c in self._columnas),
            headings={c.key: c.heading for c in self._columnas},
            column_widths={c.key: c.width for c in self._columnas},
            column_anchors={c.key: c.anchor for c in self._columnas},
            search_label="Buscar (nombre o cédula):",
            buttons=(
                ("Nuevo cliente", self._on_alta, "Success.TButton"),
                ("Editar", self._on_editar, "Edit.TButton"),
                ("Eliminar", self._on_eliminar, "Danger.Outline.TButton"),
            ),
            on_double_click=self._on_editar,
        )
        self._table.pack(side="top", fill="both", expand=True)

        self._search_var = self._table.search_var
        self._tree = self._table.tree
        self._status = self._table.status

    # ------------------------------------------------------------
    # carga y filtrado (todo en memoria, sin paginacion)
    # ------------------------------------------------------------
    def reload(self) -> None:
        self._clientes = self._service.list_customers()
        self._aplicar_filtro()

    def _on_buscar(self, *_args: object) -> None:
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        principales, coincidencias = split_search_results(
            self._clientes,
            self._search_var.get(),
            lambda c: c.name,
            lambda c: c.tax_id,
        )
        filtrados = principales + coincidencias
        self._poblar_tabla(filtrados)

    def _poblar_tabla(self, clientes: list[Customer]) -> None:
        self._table.set_rows([(str(c.id), self._valores_de(c)) for c in clientes])
        self._table.set_status(
            f"Mostrando {len(clientes)} de {len(self._clientes)} clientes"
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

    def _valores_de(self, cliente: Customer) -> tuple:
        celdas = {
            "sku": lambda: cliente.sku,
            "name": lambda: cliente.name,
            "tax_id": lambda: cliente.tax_id or "",
            "phone": lambda: cliente.phone or "",
            "email": lambda: cliente.email or "",
            "address": lambda: cliente.address or "",
            # En una sola linea: una nota con saltos de linea partiria la fila.
            "notes": lambda: " ".join((cliente.notes or "").split()),
            "mayorista": lambda: "Si" if cliente.is_wholesale else "No",
            "activo": lambda: "Si" if cliente.is_active else "No",
        }
        return tuple(celdas[c.key]() for c in self._columnas)

    # ------------------------------------------------------------
    # seleccion
    # ------------------------------------------------------------
    def _selected_customer_id(self) -> int | None:
        iid = self._table.selected_iid()
        return int(iid) if iid else None

    # ------------------------------------------------------------
    # botones
    # ------------------------------------------------------------
    def _on_alta(self) -> None:
        dialogo = CustomerDialog(self, self._service, titulo="Nuevo cliente")
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_editar(self) -> None:
        customer_id = self._selected_customer_id()
        if customer_id is None:
            messagebox.showinfo("Editar", "Selecciona un cliente primero.", parent=self)
            return

        dialogo = CustomerDialog(
            self,
            self._service,
            titulo="Editar cliente",
            customer_id=customer_id,
            billing_service=self._billing_service,
            settings_service=self._settings_service,
            rate_display_service=self._rate_display_service,
        )
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_eliminar(self) -> None:
        customer_id = self._selected_customer_id()
        if customer_id is None:
            messagebox.showinfo(
                "Eliminar", "Selecciona un cliente primero.", parent=self
            )
            return

        cliente = next((c for c in self._clientes if c.id == customer_id), None)
        nombre = cliente.name if cliente else str(customer_id)
        if not messagebox.askyesno(
            "Eliminar",
            f"¿Desactivar '{nombre}'?\n\nNo se borra: queda como inactivo.",
            parent=self,
        ):
            return

        try:
            self._service.deactivate_customer(customer_id)
        except ValueError as error:
            messagebox.showerror("Eliminar", str(error), parent=self)
            return
        self.reload()


def alinear_columna_etiquetas(etiquetas: list[kit.Label], *secciones: tk.Misc) -> None:
    """Da a la columna 0 de cada seccion el ancho de la etiqueta mas larga,
    para que los campos de todas arranquen en la misma x (cada LabelFrame
    mide su propia columna, y "Nombre:" y "Telefono:" no miden lo mismo)."""
    if not etiquetas:
        return
    etiquetas[0].update_idletasks()
    ancho = max(etiqueta.winfo_reqwidth() for etiqueta in etiquetas)
    for seccion in secciones:
        seccion.columnconfigure(0, minsize=ancho)


def ancho_columna(encabezado: str, muestra: str = "") -> int:
    """Ancho en px de una columna de Treeview que no recorta ni el
    encabezado ni un dato tipico. Se mide con la letra de ahora: un ancho
    fijo en px se quedaba corto a 150 %."""
    titulo = tkfont.nametofont("TkHeadingFont").measure(encabezado)
    dato = tkfont.nametofont("TkDefaultFont").measure(muestra)
    return max(titulo, dato) + 20


class CustomerDialog(kit.Dialog):
    """
    Formulario de alta/edicion: nombre y cedula/RIF (obligatorios),
    telefono/email/direccion (opcionales) y el check de mayorista.

    La cedula es obligatoria en toda ficha, no solo al facturar: un
    cliente sin ella no se puede facturar despues. Quien necesite vender
    sin pedir datos usa una nota de entrega (ver Cotizaciones).

    El SKU no aparece aca: se genera solo al guardar (alta) o se conserva
    tal cual estaba (edicion). Deliberadamente fuera de este formulario:
    'notes', que hoy no tiene un caso de uso en la UI todavia.
    """

    def __init__(
        self,
        master: tk.Misc,
        service: CustomerService,
        *,
        titulo: str,
        customer_id: int | None = None,
        billing_service: BillingService | None = None,
        settings_service: SettingsService | None = None,
        rate_display_service: RateDisplayService | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(titulo)
        self.transient(master)
        self.resizable(True, True)

        self._service = service
        self._billing_service = billing_service
        # Los dos son de paso: van tal cual a InvoiceDetailDialog cuando
        # se abre una factura del historial (ver _on_ver_factura).
        self._settings_service = settings_service
        self._rate_display_service = rate_display_service
        self._customer_id = customer_id
        self.guardado = False

        self._name_var = tk.StringVar()
        # La cedula se edita en dos partes (tipo + numero) y se vuelve a
        # unir en _tax_id().
        self._tax_type_var = tk.StringVar(value=TAX_ID_TYPES[0][0])
        self._tax_number_var = tk.StringVar()
        self._phone_var = tk.StringVar()
        self._email_var = tk.StringVar()
        self._address_var = tk.StringVar()
        self._wholesale_var = tk.BooleanVar(value=False)
        self._active_var = tk.BooleanVar(value=True)
        # Historial de compras (solo lectura, ver _build_facturas_section):
        # viene de otro service (BillingService), no de CustomerService -
        # por eso es opcional y se carga aparte, no en _cargar_cliente().
        self._facturas_cliente: list[Invoice] = []
        # Notas: tk.Text (texto largo), se crea en _build_form(); aca solo
        # lo que hay que insertarle cuando exista.
        self._notes_initial = ""

        if customer_id is not None:
            self._cargar_cliente(customer_id)
            if self._billing_service is not None:
                self._facturas_cliente = (
                    self._billing_service.list_invoices_for_customer(customer_id)
                )

        self._build_form()
        self.bind("<Return>", lambda _evento: self._on_guardar())
        self.bind("<Escape>", lambda _evento: self.destroy())
        focus_first_entry(self)  # cursor en "Nombre": sin click extra para escribir
        fit_scrollable_dialog_to_screen(
            self,
            master,
            self._cuerpo_formulario,
            self._pie_botones,
            minimum_width=520,
        )
        # transient() sola no siempre alcanza para que el gestor de
        # ventanas apile este dialogo ENCIMA de quien lo abrio (se vio
        # pasar en Windows): lift() lo fuerza, sin depender de que el
        # usuario lo note y lo traiga al frente el mismo.
        self.lift()
        self.focus_force()
        self.grab_set()  # modal: despues de construir, para no bloquear errores propios

    def _cargar_cliente(self, customer_id: int) -> None:
        cliente = self._service.get_customer(customer_id)
        if cliente is None:
            return  # se creo el dialogo pero el cliente desaparecio: campos vacios
        self._name_var.set(cliente.name)
        self._set_tax_id(cliente.tax_id or "")
        self._phone_var.set(cliente.phone or "")
        self._email_var.set(cliente.email or "")
        self._address_var.set(cliente.address or "")
        self._wholesale_var.set(cliente.is_wholesale)
        self._active_var.set(cliente.is_active)
        self._notes_initial = cliente.notes or ""

    def _tax_id(self) -> str:
        """'V' + '12345678' -> 'V-12345678'. Vacio si no hay numero: sin
        el, guardar el tipo solo ('V-') seria un formato invalido."""
        numero = self._tax_number_var.get().strip()
        return f"{self._tax_type_var.get()}-{numero}" if numero else ""

    def _set_tax_id(self, tax_id: str) -> None:
        """Parte 'V-12345678' en sus dos campos. Lo que no tenga un tipo
        conocido va entero al numero, para no perder lo que ya estaba
        guardado (el formulario lo mostrara tal cual y el servicio
        avisara al guardar)."""
        tipos = [tipo for tipo, _etiqueta in TAX_ID_TYPES]
        tipo, _, numero = tax_id.partition("-")
        if numero and tipo.upper() in tipos:
            self._tax_type_var.set(tipo.upper())
            self._tax_number_var.set(numero)
        else:
            self._tax_number_var.set(tax_id)

    def _build_form(self) -> None:
        # Datos personales separados de Contacto (mismo patron que
        # InvoiceDialog/ProductDialog): nombre+cedula+mayorista+activo
        # son "quien es", telefono/email/direccion son "como ubicarlo".
        contenedor = kit.Frame(self)
        contenedor.pack(fill="both", expand=True)
        cuerpo = ScrollableDialogBody(contenedor, padding=20)
        cuerpo.pack(side="top", fill="both", expand=True)
        self._cuerpo_formulario = cuerpo
        frame = cuerpo.content
        frame.columnconfigure(0, weight=1)

        identificacion = kit.LabelFrame(frame, text="Datos personales", padding=10)
        identificacion.grid(row=0, column=0, sticky="ew")
        identificacion.columnconfigure(1, weight=1)

        # Nombre y cedula/RIF son obligatorios en toda ficha de cliente:
        # una factura los necesita, y un cliente sin cedula no se puede
        # facturar despues. Para vender sin ficha estan las notas de
        # entrega (Cotizaciones), que aceptan un nombre suelto.
        etiquetas = [
            kit.Label(identificacion, text="Nombre:"),
            kit.Label(identificacion, text="Cédula/RIF:"),
        ]
        etiquetas[0].grid(row=0, column=0, sticky="w", pady=4)
        self._name_entry = kit.Entry(
            identificacion, textvariable=self._name_var, width=30
        )
        self._name_entry.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=4)

        # Tipo (V-, E-, J-...) aparte del numero: el tipo se elige de una
        # lista en vez de escribirlo, que es de donde salian la mitad de
        # los errores de formato. Se unen al guardar (ver _tax_id()).
        etiquetas[1].grid(row=1, column=0, sticky="w", pady=4)
        cedula = kit.Frame(identificacion)
        cedula.grid(row=1, column=1, sticky="w", padx=(8, 0), pady=4)
        self._tax_type_combo = kit.Combobox(
            cedula,
            state="readonly",
            width=6,
            textvariable=self._tax_type_var,
            values=[tipo for tipo, _etiqueta in TAX_ID_TYPES],
        )
        self._tax_type_combo.grid(row=0, column=0, sticky="w")
        kit.Entry(cedula, textvariable=self._tax_number_var, width=18).grid(
            row=0, column=1, sticky="w", padx=(6, 0)
        )

        # Mayorista y Activo en UNA fila. "Activo" solo aparece al editar:
        # en el alta siempre es True, y mostrarlo tildado sin poder tener
        # otro valor es ruido visual.
        banderas = kit.Frame(identificacion)
        banderas.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=4)
        kit.Checkbutton(banderas, text="Mayorista", variable=self._wholesale_var).pack(
            side="left"
        )
        if self._customer_id is not None:
            kit.Checkbutton(banderas, text="Activo", variable=self._active_var).pack(
                side="left", padx=(20, 0)
            )

        contacto = kit.LabelFrame(frame, text="Contacto", padding=10)
        contacto.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        contacto.columnconfigure(1, weight=1)

        # El telefono es corto: no necesita el ancho del email o la direccion.
        for fila, (texto, var, largo) in enumerate(
            (
                ("Teléfono:", self._phone_var, 20),
                ("Email:", self._email_var, 30),
                ("Dirección:", self._address_var, 30),
            )
        ):
            etiqueta = kit.Label(contacto, text=texto)
            etiqueta.grid(row=fila, column=0, sticky="w", pady=4)
            etiquetas.append(etiqueta)
            campo = kit.Entry(contacto, textvariable=var, width=largo)
            if var is self._phone_var:
                self._phone_entry = campo  # para tests de alineacion
            campo.grid(
                row=fila,
                column=1,
                sticky="w" if var is self._phone_var else "ew",
                padx=(8, 0),
                pady=4,
            )
        # Los campos de las dos secciones arrancan en la misma x.
        alinear_columna_etiquetas(etiquetas, identificacion, contacto)

        # Notas libres sobre el cliente ("paga a fin de mes", "le gusta que
        # lo llamen antes"). Opcional: vacio se guarda como NULL.
        notas = kit.LabelFrame(frame, text="Notas (opcional)", padding=10)
        notas.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        notas.columnconfigure(0, weight=1)
        self._notes_text = tk.Text(notas, width=30, height=3, wrap="word")
        tab_pasa_al_siguiente(self._notes_text)
        paint_text(self._notes_text)
        self._notes_text.grid(row=0, column=0, sticky="ew", padx=(8, 0), pady=4)
        if self._notes_initial:
            self._notes_text.insert("1.0", self._notes_initial)
        # El <Return> del dialogo guarda (ver __init__): dentro de las notas
        # tiene que escribir un salto de linea, no guardar a mitad de una
        # nota. 'break' corta la propagacion hasta el Toplevel.
        self._notes_text.bind("<Return>", self._on_return_en_notas)

        # Solo al editar y si nos pasaron con que ver las facturas (ver
        # __init__): un cliente nuevo todavia no compro nada.
        siguiente_fila = 3
        if self._customer_id is not None and self._billing_service is not None:
            self._build_facturas_section(frame, row=siguiente_fila)
            siguiente_fila += 1

        botones = kit.Frame(contenedor)
        botones.pack(side="bottom", pady=(8, 12))
        self._pie_botones = botones
        cuerpo.pack_forget()
        cuerpo.pack(side="top", fill="both", expand=True)
        kit.Button(
            botones, text="Guardar", command=self._on_guardar, style="Success.TButton"
        ).pack(side="left", padx=4)
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="left", padx=4
        )

    def _build_facturas_section(self, parent: kit.Frame, *, row: int) -> None:
        """
        Solo lectura: que facturas tiene este cliente (BillingService.
        list_invoices_for_customer, sin líneas - las lineas las carga
        _on_ver_factura, una sola factura por vez, al abrir el detalle).
        Puede ser una lista larga para un cliente frecuente, asi que va
        con scroll, mismo criterio que "Productos que le compramos" en
        SupplierDialog.
        """
        seccion = kit.LabelFrame(parent, text="Historial de compras", padding=10)
        seccion.grid(row=row, column=0, sticky="ew", pady=(10, 0))

        if not self._facturas_cliente:
            kit.Label(
                seccion, text="Sin compras registradas.", style="Muted.TLabel"
            ).pack(anchor="w")
            return

        moneda = self._billing_service.currency_symbol()
        tabla_frame = kit.Frame(seccion)
        tabla_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            tabla_frame,
            columns=("numero", "fecha", "estado", "total"),
            show="headings",
            height=min(len(self._facturas_cliente), 6),
        )
        for col, texto in (
            ("numero", "Número"),
            ("fecha", "Fecha"),
            ("estado", "Estado"),
            ("total", "Total"),
        ):
            tree.heading(col, text=texto)
        # Anchos medidos con la letra actual: el defecto de Treeview es
        # 200 px por columna y ensancharia la ficha; un valor fijo en px
        # recortaba los titulos a 150 %.
        tree.column("numero", width=ancho_columna("Número", "F-0000000"))
        tree.column(
            "fecha", width=ancho_columna("Fecha", "00/00/0000"), anchor="center"
        )
        tree.column(
            "estado", width=ancho_columna("Estado", "Confirmada"), anchor="center"
        )
        tree.column("total", width=ancho_columna("Total", "0.000.000,00 $"), anchor="e")
        for factura in self._facturas_cliente:
            tree.insert(
                "",
                "end",
                # El iid es el id de la factura: con eso _on_ver_factura
                # la vuelve a pedir completa (con lineas) sin tener que
                # buscarla por numero en la lista.
                iid=str(factura.id),
                values=(
                    factura.display_number,
                    factura.issued_at.strftime("%d/%m/%Y") if factura.issued_at else "",
                    "Cancelada" if factura.is_cancelled else "Confirmada",
                    format_money(factura.total_cents, moneda),
                ),
            )
        scrollbar = kit.Scrollbar(tabla_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        # Doble clic abre la misma ventana que "Ver detalle" en Facturas
        # (la que tiene "Generar PDF"): mismo gesto que en todas las
        # tablas de la app (ver SearchableTable.on_double_click).
        tree.bind("<Double-1>", lambda _evento: self._on_ver_factura())
        self._tree_facturas = tree  # para tests; nadie mas lo necesita
        fila_ver = kit.Frame(seccion)
        fila_ver.pack(anchor="w", pady=(8, 0))
        kit.Button(fila_ver, text="Ver detalle", command=self._on_ver_factura).pack(
            side="left"
        )
        info_hint(
            fila_ver, "Doble clic en una factura para verla y generar su PDF."
        ).pack(side="left", padx=(8, 0))

    def _on_ver_factura(self) -> None:
        """
        Abre la factura elegida del historial en InvoiceDetailDialog, el
        mismo dialogo que "Ver detalle" de Facturas, con su boton de PDF.

        El import es local a proposito: billing_page ya importa este
        modulo (para CustomerDialog), asi que un import arriba haria un
        ciclo. Es el mismo truco que usa billing_page al reves.
        """
        from .billing_page import InvoiceDetailDialog

        seleccion = self._tree_facturas.selection()
        if not seleccion:
            messagebox.showinfo(
                "Ver detalle", "Selecciona una factura primero.", parent=self
            )
            return

        factura = self._billing_service.get_invoice(int(seleccion[0]))
        if factura is None:
            messagebox.showerror("Ver detalle", "La factura ya no existe.", parent=self)
            return

        dialogo = InvoiceDetailDialog(
            self,
            factura,
            currency_symbol=self._billing_service.currency_symbol(),
            billing_service=self._billing_service,
            settings_service=self._settings_service,
            rate_display_service=self._rate_display_service,
        )
        # El detalle toma el grab modal al abrirse y lo suelta del todo
        # al cerrarse: sin esto, esta ficha se quedaría sin modal.
        self.wait_window(dialogo)
        self.grab_set()

    def _on_return_en_notas(self, _evento: object) -> str:
        self._notes_text.insert("insert", "\n")
        return "break"

    def _on_guardar(self) -> None:
        name = self._name_var.get()
        kwargs = {
            "name": name,
            "tax_id": self._tax_id(),
            "phone": self._phone_var.get(),
            "email": self._email_var.get(),
            "address": self._address_var.get(),
            "notes": self._notes_text.get("1.0", "end"),
            "is_wholesale": self._wholesale_var.get(),
        }

        # Nombre y cedula/RIF, siempre (el telefono y la direccion salen
        # en el PDF si estan, pero no frenan una venta). El nombre lo
        # valida el servicio, que ya lo exigia.
        if not kwargs["tax_id"]:
            messagebox.showerror(
                "Guardar",
                "Falta la cédula/RIF: una factura la necesita.",
                parent=self,
            )
            return

        try:
            if self._customer_id is None:
                self._service.create_customer(**kwargs)
            else:
                self._service.update_customer(
                    self._customer_id, is_active=self._active_var.get(), **kwargs
                )
        except ValueError as error:
            messagebox.showerror("Guardar", str(error), parent=self)
            return

        self.guardado = True
        self.destroy()
