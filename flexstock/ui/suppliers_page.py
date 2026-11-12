"""
Pagina de proveedores: tabla + busqueda + alta/edicion/baja basicas.

Mismo patron que CustomersPage: carga todo en memoria una vez y filtra
con una comprension de lista (80-150 proveedores reales, muy por debajo
de las 1.936 filas del catalogo).

ui/ solo llama a SupplierService: nunca importa nada de flexstock.data.
"""

import tkinter as tk
from tkinter import messagebox, ttk

from ..domain.entities import ProductLink, Supplier
from ..domain.money import format_money_milli, format_quantity
from ..services.product_service import ProductService
from ..services.settings_service import SettingsService
from ..services.supplier_service import SupplierService
from . import columns, kit
from .customers_page import alinear_columna_etiquetas, ancho_columna
from .rif import TIPOS_RIF, armar_rif, partir_rif
from .search import split_search_results
from .theme import paint_window
from .widgets import (
    ScrollableDialogBody,
    SearchableTable,
    fit_scrollable_dialog_to_screen,
    focus_first_entry,
)

# Que columnas se ven las decide ui/columns.py + Configuracion >
# Personalizacion; aca solo vive el texto de cada celda (_valores_de).
MODULO = columns.SUPPLIERS


class SuppliersPage(kit.Frame):
    """Contenedor autonomo: se puede empotrar en cualquier ventana padre."""

    def __init__(
        self,
        master: tk.Misc,
        supplier_service: SupplierService,
        *,
        product_service: ProductService | None = None,
        settings_service: SettingsService | None = None,
    ) -> None:
        super().__init__(master)
        self._service = supplier_service
        # Opcional (igual criterio que "Crear factura" en ProductsPage):
        # sin el, la pagina funciona igual, solo que SupplierDialog no
        # puede mostrar "que productos le compramos" (ver _on_editar).
        self._product_service = product_service
        # Sin el, la tabla usa las columnas por defecto del catalogo.
        self._settings_service = settings_service
        self._proveedores: list[Supplier] = []  # catalogo completo, en memoria
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
            search_label="Buscar (nombre o SKU):",
            buttons=(
                ("Nuevo proveedor", self._on_alta, "Success.TButton"),
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
        self._proveedores = self._service.list_suppliers()
        self._aplicar_filtro()

    def _on_buscar(self, *_args: object) -> None:
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        principales, coincidencias = split_search_results(
            self._proveedores,
            self._search_var.get(),
            lambda p: p.name,
            lambda p: p.sku,
        )
        filtrados = principales + coincidencias
        self._poblar_tabla(filtrados)

    def _poblar_tabla(self, proveedores: list[Supplier]) -> None:
        self._table.set_rows([(str(p.id), self._valores_de(p)) for p in proveedores])
        self._table.set_status(
            f"Mostrando {len(proveedores)} de {len(self._proveedores)} proveedores"
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

    def _valores_de(self, proveedor: Supplier) -> tuple:
        celdas = {
            "sku": lambda: proveedor.sku,
            "name": lambda: proveedor.name,
            "contact_name": lambda: proveedor.contact_name or "",
            "phone": lambda: proveedor.phone or "",
            "email": lambda: proveedor.email or "",
            "tax_id": lambda: proveedor.tax_id or "",
            "activo": lambda: "Si" if proveedor.is_active else "No",
        }
        return tuple(celdas[c.key]() for c in self._columnas)

    # ------------------------------------------------------------
    # seleccion
    # ------------------------------------------------------------
    def _selected_supplier_id(self) -> int | None:
        iid = self._table.selected_iid()
        return int(iid) if iid else None

    # ------------------------------------------------------------
    # botones
    # ------------------------------------------------------------
    def _on_alta(self) -> None:
        dialogo = SupplierDialog(self, self._service, titulo="Nuevo proveedor")
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_editar(self) -> None:
        supplier_id = self._selected_supplier_id()
        if supplier_id is None:
            messagebox.showinfo(
                "Editar", "Selecciona un proveedor primero.", parent=self
            )
            return

        dialogo = SupplierDialog(
            self,
            self._service,
            titulo="Editar proveedor",
            supplier_id=supplier_id,
            product_service=self._product_service,
        )
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_eliminar(self) -> None:
        supplier_id = self._selected_supplier_id()
        if supplier_id is None:
            messagebox.showinfo(
                "Eliminar", "Selecciona un proveedor primero.", parent=self
            )
            return

        proveedor = next((p for p in self._proveedores if p.id == supplier_id), None)
        nombre = proveedor.name if proveedor else str(supplier_id)
        if not messagebox.askyesno(
            "Eliminar",
            f"¿Desactivar '{nombre}'?\n\nNo se borra: queda como inactivo.",
            parent=self,
        ):
            return

        try:
            self._service.deactivate_supplier(supplier_id)
        except ValueError as error:
            messagebox.showerror("Eliminar", str(error), parent=self)
            return
        self.reload()


class SupplierDialog(kit.Dialog):
    """
    Formulario de alta/edicion: nombre (obligatorio), contacto/cedula/
    telefono/email/direccion (opcionales).

    El SKU no aparece aca: se genera solo al guardar (alta) o se
    conserva tal cual estaba (edicion).
    """

    def __init__(
        self,
        master: tk.Misc,
        service: SupplierService,
        *,
        titulo: str,
        supplier_id: int | None = None,
        product_service: ProductService | None = None,
        nombre_inicial: str = "",
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(titulo)
        self.transient(master)
        self.resizable(True, True)

        self._service = service
        self._product_service = product_service
        self._supplier_id = supplier_id
        self.guardado = False

        # Lo que ya se habia escrito en el buscador de proveedores de donde
        # se pidio el alta: no hay que volver a teclear el nombre.
        self._name_var = tk.StringVar(value=nombre_inicial.strip())
        self._contact_var = tk.StringVar()
        # RIF en dos partes (tipo + numero), como en Tienda: el tipo se
        # elige de una lista. Un proveedor casi siempre es una empresa: J.
        self._tax_tipo_var = tk.StringVar(value="J")
        self._tax_numero_var = tk.StringVar()
        self._tax_id_original = ""
        self._nombre_original = ""
        self._phone_var = tk.StringVar()
        self._email_var = tk.StringVar()
        self._address_var = tk.StringVar()
        self._active_var = tk.BooleanVar(value=True)
        # Que productos le compra la ferreteria a este proveedor (solo
        # lectura, ver _build_productos_section): viene de otro service
        # (ProductService), no de SupplierService - por eso es opcional
        # y se carga aparte, no en _cargar_proveedor().
        self._productos_proveedor: list[ProductLink] = []

        if supplier_id is not None:
            self._cargar_proveedor(supplier_id)
            if self._product_service is not None:
                self._productos_proveedor = (
                    self._product_service.list_products_for_supplier(supplier_id)
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
        # Ver el mismo comentario en customers_page.py: lift() fuerza el
        # apilamiento encima de quien lo abrio, transient() sola no alcanza.
        self.lift()
        self.focus_force()
        self.grab_set()  # modal: despues de construir, para no bloquear errores propios

    def _cargar_proveedor(self, supplier_id: int) -> None:
        proveedor = self._service.get_supplier(supplier_id)
        if proveedor is None:
            return  # se creo el dialogo pero el proveedor desaparecio: campos vacios
        self._name_var.set(proveedor.name)
        self._nombre_original = proveedor.name
        self._contact_var.set(proveedor.contact_name or "")
        self._tax_id_original = proveedor.tax_id or ""
        tipo, numero = partir_rif(self._tax_id_original)
        self._tax_tipo_var.set(tipo)
        self._tax_numero_var.set(numero)
        self._phone_var.set(proveedor.phone or "")
        self._email_var.set(proveedor.email or "")
        self._address_var.set(proveedor.address or "")
        self._active_var.set(proveedor.is_active)

    def _build_form(self) -> None:
        # Mismo patron que CustomerDialog: identificacion separada de
        # los medios de contacto, en vez de 6 filas sueltas.
        contenedor = kit.Frame(self)
        contenedor.pack(fill="both", expand=True)
        cuerpo = ScrollableDialogBody(contenedor, padding=20)
        cuerpo.pack(side="top", fill="both", expand=True)
        self._cuerpo_formulario = cuerpo
        frame = cuerpo.content
        frame.columnconfigure(0, weight=1)

        identificacion = kit.LabelFrame(frame, text="Datos del proveedor", padding=10)
        identificacion.grid(row=0, column=0, sticky="ew")
        identificacion.columnconfigure(1, weight=1)

        etiquetas: list[kit.Label] = []
        for fila, (texto, var) in enumerate(
            (("Nombre:", self._name_var), ("Persona de contacto:", self._contact_var))
        ):
            etiqueta = kit.Label(identificacion, text=texto)
            etiqueta.grid(row=fila, column=0, sticky="w", pady=4)
            etiquetas.append(etiqueta)
            campo = kit.Entry(identificacion, textvariable=var, width=30)
            if var is self._name_var:
                self._name_entry = campo  # para tests de alineacion
            campo.grid(row=fila, column=1, sticky="ew", padx=(8, 0), pady=4)

        # La cedula/RIF es corta: "Activo" va a su lado, en la misma fila, y
        # solo aparece al editar (en el alta siempre es True, y mostrarlo
        # tildado sin poder tener otro valor es ruido visual).
        etiqueta = kit.Label(identificacion, text="Cédula/RIF:")
        etiqueta.grid(row=2, column=0, sticky="w", pady=4)
        etiquetas.append(etiqueta)
        cedula = kit.Frame(identificacion)
        cedula.grid(row=2, column=1, sticky="w", padx=(8, 0), pady=4)
        self._tax_tipo_combo = kit.Combobox(
            cedula,
            state="readonly",
            width=6,
            textvariable=self._tax_tipo_var,
            values=TIPOS_RIF,
        )
        self._tax_tipo_combo.pack(side="left")
        kit.Entry(cedula, textvariable=self._tax_numero_var, width=18).pack(
            side="left", padx=(6, 0)
        )
        if self._supplier_id is not None:
            kit.Checkbutton(cedula, text="Activo", variable=self._active_var).pack(
                side="left", padx=(20, 0)
            )

        contacto = kit.LabelFrame(frame, text="Medios de contacto", padding=10)
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

        # Solo al editar y si nos pasaron con que ver los productos
        # (ver __init__): un proveedor nuevo todavia no le vendio nada.
        siguiente_fila = 2
        if self._supplier_id is not None and self._product_service is not None:
            self._build_productos_section(frame, row=siguiente_fila)
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

    def _build_productos_section(self, parent: kit.Frame, *, row: int) -> None:
        """
        Solo lectura: que le compramos a este proveedor (reverso de la
        seccion "Proveedores" de ProductDialog, ver ProductLink en
        domain/entities.py). Puede ser una lista larga (un proveedor
        real le vende muchos productos a la ferreteria, a diferencia de
        los 0-2 proveedores por producto), asi que esta va con scroll.
        """
        seccion = kit.LabelFrame(parent, text="Productos que le compramos", padding=10)
        seccion.grid(row=row, column=0, sticky="ew", pady=(10, 0))

        if not self._productos_proveedor:
            kit.Label(
                seccion, text="Sin productos registrados.", style="Muted.TLabel"
            ).pack(anchor="w")
            return

        tabla_frame = kit.Frame(seccion)
        tabla_frame.pack(fill="both", expand=True)
        tree = ttk.Treeview(
            tabla_frame,
            columns=("sku", "producto", "stock", "ultima_compra", "ultimo_costo"),
            show="headings",
            height=min(len(self._productos_proveedor), 6),
        )
        for col, texto in (
            ("sku", "SKU"),
            ("producto", "Producto"),
            ("stock", "Stock"),
            ("ultima_compra", "Última compra"),
            ("ultimo_costo", "Último costo"),
        ):
            tree.heading(col, text=texto)
        # Anchos medidos con la letra actual (ver ancho_columna). "producto"
        # es la que estira con lo que sobre.
        tree.column("sku", width=ancho_columna("SKU", "GEN-0000"))
        tree.column("producto", width=ancho_columna("Producto", "MARTILLO DE"))
        tree.column("stock", width=ancho_columna("Stock", "0.000"), anchor="e")
        tree.column(
            "ultima_compra",
            width=ancho_columna("Última compra", "00/00/0000"),
            anchor="center",
        )
        tree.column(
            "ultimo_costo",
            width=ancho_columna("Último costo", "0.000,00"),
            anchor="e",
        )
        for link in self._productos_proveedor:
            tree.insert(
                "",
                "end",
                values=(
                    link.product_sku,
                    link.product_name,
                    format_quantity(link.current_stock),
                    link.last_purchase.strftime("%d/%m/%Y")
                    if link.last_purchase
                    else "",
                    format_money_milli(link.last_cost_milli, symbol="")
                    if link.last_cost_milli is not None
                    else "",
                ),
            )
        scrollbar = kit.Scrollbar(tabla_frame, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scrollbar.set)
        tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")
        self._tree_productos = tree  # para tests; nadie mas lo necesita

    def _tax_id(self) -> str:
        """
        'J' + '12345678-9' -> 'J-12345678-9' (ValueError si el numero no
        parece un RIF). Un valor viejo que no se toco se conserva tal cual:
        hay proveedores migrados del sistema DOS con la cedula en formatos
        que esta validacion rechazaria, y editarles el telefono no debe
        obligar a corregirla.
        """
        tipo, numero = self._tax_tipo_var.get(), self._tax_numero_var.get()
        if (tipo, numero.strip().upper()) == partir_rif(self._tax_id_original):
            return self._tax_id_original
        return armar_rif(tipo, numero)

    def _confirmar_nombre_parecido(self) -> bool:
        """
        Si ya hay un proveedor con un nombre parecido, pregunta antes de
        guardar (el catalogo viejo tenia ~700 variantes para ~100
        proveedores: duplicarlos es el error de siempre). Al editar solo
        pregunta si el nombre cambio. True = seguir guardando.
        """
        nombre = self._name_var.get().strip()
        if not nombre or (
            self._supplier_id is not None and nombre == self._nombre_original
        ):
            return True
        parecidos = self._service.find_similar(nombre, exclude_id=self._supplier_id)
        if not parecidos:
            return True
        lista = "\n".join(f"  \u2022 {p.name}" for p in parecidos)
        return messagebox.askyesno(
            "Proveedor parecido",
            f"Ya hay proveedores con un nombre parecido:\n\n{lista}\n\n"
            f"¿Guardar «{nombre}» como un proveedor distinto?",
            parent=self,
        )

    def _on_guardar(self) -> None:
        try:
            tax_id = self._tax_id()
        except ValueError as error:
            messagebox.showerror("Guardar", str(error), parent=self)
            return
        if not self._confirmar_nombre_parecido():
            return
        kwargs = {
            "name": self._name_var.get(),
            "contact_name": self._contact_var.get(),
            "tax_id": tax_id,
            "phone": self._phone_var.get(),
            "email": self._email_var.get(),
            "address": self._address_var.get(),
        }

        try:
            if self._supplier_id is None:
                nuevo = self._service.create_supplier(**kwargs)
                # Quien abrio el alta puede necesitar el id del recien
                # creado (ej. registrar al proveedor en un producto
                # apenas se lo da de alta, ver RegistrarProveedorDialog).
                self._supplier_id = nuevo.id
            else:
                self._service.update_supplier(
                    self._supplier_id, is_active=self._active_var.get(), **kwargs
                )
        except ValueError as error:
            messagebox.showerror("Guardar", str(error), parent=self)
            return

        self.guardado = True
        self.destroy()

    @property
    def supplier_id(self) -> int | None:
        """El id del proveedor editado, o del recien creado si fue un alta
        y se guardo. None si era un alta y se cancelo."""
        return self._supplier_id
