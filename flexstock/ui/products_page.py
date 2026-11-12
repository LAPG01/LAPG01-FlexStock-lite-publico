"""
Pagina de productos: tabla + busqueda + alta/edicion/baja basicas.

Carga el catalogo completo (~1.936 filas) UNA sola vez al abrir y
filtra en memoria con una comprension de lista, sin paginacion:
medido en esta maquina, list_products() + poblar el Treeview tarda
~110ms para 1.936 filas, y repoblar tras una busqueda (list() completo
+ insert()) es imperceptible. Paginar con LIMIT/OFFSET obligaria a que
cada tecla de busqueda vaya a SQLite, mas lento que filtrar en RAM.

ui/ solo llama a ProductService: nunca importa nada de flexstock.data.
"""

import tkinter as tk
from collections.abc import Callable, Sequence
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from tkinter import font as tkfont
from tkinter import messagebox, ttk

from ..domain.entities import Product, SupplierEntry, SupplierLink
from ..domain.money import (
    format_money,
    format_money_milli,
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
    BCV,
    PERSONAL,
    RateDisplayService,
    RateSnapshot,
)
from ..services.settings_service import SettingsService
from ..services.stock_service import StockService
from ..services.supplier_service import SupplierService
from . import columns, kit
from .billing_page import InvoiceDialog
from .picker import posicion_en_ventana
from .search import split_search_results
from .stock_adjustment_page import AjustarExistenciaDialog
from .suppliers_page import SupplierDialog
from .theme import (
    find_app_root,
    get_palette,
    paint_listbox,
    paint_text,
    paint_window,
)
from .widgets import (
    ScrollableDialogBody,
    SearchableTable,
    alinear_encabezados,
    fit_scrollable_dialog_to_screen,
    focus_first_entry,
    info_hint,
    repartir_anchos,
    tab_pasa_al_siguiente,
    wrap_to_width,
)

# Que columnas se ven (y en que orden) lo decide ui/columns.py +
# Configuracion > Personalizacion: aca solo vive COMO se calcula el
# texto de cada celda, en _valores_de().


class ProductsPage(kit.Frame):
    """Contenedor autonomo: se puede empotrar en cualquier ventana padre."""

    def __init__(
        self,
        master: tk.Misc,
        product_service: ProductService,
        *,
        currency_symbol: str = "$",
        billing_service: BillingService | None = None,
        customer_service: CustomerService | None = None,
        settings_service: SettingsService | None = None,
        rate_display_service: RateDisplayService | None = None,
        preview_service: PreviewService | None = None,
        supplier_service: SupplierService | None = None,
        stock_service: StockService | None = None,
    ) -> None:
        super().__init__(master)
        self._service = product_service
        # "Ajustar..." en la ficha de un producto abre la ventana "Ajustar
        # existencia" (ver AjustarExistenciaDialog). Sin el servicio, la
        # ficha muestra la existencia pero no ofrece el boton.
        self._stock_service = stock_service
        # Motivo y "Contado por" de la ultima vez: quedan puestos para el
        # siguiente producto que se ajuste en esta sesion.
        self._recuerdo_ajuste: dict[str, str] = {}
        self._currency_symbol = currency_symbol
        # Solo para pasarselo a Nueva factura (atajo "Crear factura").
        self._preview_service = preview_service
        # Todos opcionales: el boton "Crear factura" es un atajo para no
        # cambiar de pestana, no una dependencia real de esta pagina -
        # sin ellos (ej. los tests que no arman todo el arbol de
        # servicios), la pagina funciona igual sin el boton.
        self._billing_service = billing_service
        self._customer_service = customer_service
        # Opcional, mismo criterio que billing_service: sin el, la ficha
        # del producto muestra sus proveedores pero no deja vincular uno
        # nuevo ni abrir su ficha (ver ProductDialog).
        self._supplier_service = supplier_service
        self._settings_service = settings_service
        # Sin el, las columnas de precio en Bs / a BCV muestran '-' (ver
        # _valores_de): la pagina sigue funcionando igual que antes.
        self._rates = rate_display_service
        self._tasas: RateSnapshot | None = None
        self._productos: list[Product] = []  # catalogo completo, en memoria

        # Columna de precio del listado: se resuelve por position=3 (no
        # por indice de lista, ver PriceTierRepository), porque el id
        # real depende del autoincrement de price_tiers. Se guarda el
        # tier completo, no solo el id, para poder titular la columna
        # con su nombre real ('Detal') en vez de un generico 'Precio 3'.
        self._precio3_tier = next(
            (t for t in product_service.list_price_tiers() if t.position == 3),
            None,
        )
        self._precio3_tier_id = (
            self._precio3_tier.id if self._precio3_tier is not None else None
        )
        # position -> nombre real del nivel ('Detal'), para titular las
        # columnas de precio con el nombre que usa el negocio.
        self._tiers = product_service.list_price_tiers()
        self._tier_id_por_position = {t.position: t.id for t in self._tiers}
        self._columnas = self._columnas_visibles()

        self._build_widgets()
        self._search_var.trace_add("write", self._on_buscar)

        self.reload()

    # ------------------------------------------------------------
    # construccion de widgets
    # ------------------------------------------------------------
    def _columnas_visibles(self) -> tuple[columns.ColumnDef, ...]:
        if self._settings_service is None:
            guardadas, avanzado = None, False
        else:
            guardadas = self._settings_service.visible_columns(columns.PRODUCTS)
            avanzado = self._settings_service.advanced_mode()
        catalogo = columns.catalog(
            columns.PRODUCTS,
            tier_names={t.position: t.name for t in self._tiers},
            advanced=avanzado,
        )
        return columns.visible(catalogo, guardadas)

    def apply_column_settings(self) -> None:
        """Re-arma la tabla tras tocar Configuracion > Personalizacion o
        prender/apagar Modo avanzado."""
        self._columnas = self._columnas_visibles()
        self._table.set_columns(
            tuple(c.key for c in self._columnas),
            {c.key: c.heading for c in self._columnas},
            {c.key: c.width for c in self._columnas},
            {c.key: c.anchor for c in self._columnas},
        )
        self._aplicar_filtro()

    def _build_widgets(self) -> None:
        botones = (
            ("Nuevo producto", self._on_alta, "Success.TButton"),
            ("Editar", self._on_editar, "Edit.TButton"),
            ("Eliminar", self._on_eliminar, "Danger.Outline.TButton"),
        )
        # A la derecha y aparte: no es una accion de "administrar el
        # catalogo" como las de arriba, es un atajo hacia Facturas.
        boton_derecha = ()
        if self._billing_service is not None and self._customer_service is not None:
            boton_derecha = (
                ("Crear factura", self._on_crear_factura, "Success.TButton"),
            )

        self._table = SearchableTable(
            self,
            columns=tuple(c.key for c in self._columnas),
            headings={c.key: c.heading for c in self._columnas},
            column_widths={c.key: c.width for c in self._columnas},
            column_anchors={c.key: c.anchor for c in self._columnas},
            search_label="Buscar (nombre o SKU):",
            buttons=botones,
            right_buttons=boton_derecha,
            on_double_click=self._on_editar,
        )
        self._table.pack(side="top", fill="both", expand=True)
        # El Detal es el precio que mas se usa: su titulo resalta.
        self._table.set_highlighted_column("price3")

        # Alias: el resto de esta clase (y sus tests) ya conocen estos
        # nombres de cuando el Treeview vivia inline aca mismo.
        self._search_var = self._table.search_var
        self._tree = self._table.tree
        self._status = self._table.status

    # ------------------------------------------------------------
    # carga y filtrado (todo en memoria, sin paginacion)
    # ------------------------------------------------------------
    def reload(self) -> None:
        """Vuelve a pedirle el catalogo completo a ProductService."""
        self._productos = self._service.list_products()
        self._aplicar_filtro()

    def _on_buscar(self, *_args: object) -> None:
        self._aplicar_filtro()

    def _aplicar_filtro(self) -> None:
        principales, coincidencias = split_search_results(
            self._productos,
            self._search_var.get(),
            lambda p: p.name,
            lambda p: p.sku,
        )
        self._poblar_tabla(principales, coincidencias)

    def _precio_milli(self, producto: Product, position: int) -> int | None:
        tier_id = self._tier_id_por_position.get(position)
        return None if tier_id is None else producto.price_milli_for(tier_id)

    def _precio_convertido(self, producto: Product, como: str) -> str:
        """
        Precio 3 convertido, segun 'como':
            "bs_bcv"   $ x tasa BCV            -> Bs
            "bs"       $ x tasa propia          -> Bs   (Modo avanzado)
            "usd_bcv"  $ x tasa propia / BCV    -> $    (Modo avanzado)
        '-' si falta el precio o la tasa: una columna prendida sin tasa
        cargada no debe romper la tabla.
        """
        precio_milli = self._precio_milli(producto, 3)
        if precio_milli is None or self._tasas is None:
            return "-"
        cents = to_cents(from_milli(precio_milli))
        if como == "usd_bcv":
            convertido = self._tasas.usd_at_bcv(cents)
        elif como == "bs_bcv":
            convertido = self._tasas.to_bs(cents, BCV)
        else:
            convertido = self._tasas.to_bs(cents, PERSONAL)
        if convertido is None:
            return "-"
        return format_money(convertido, "$" if como == "usd_bcv" else " Bs")

    def _valores_de(self, producto: Product) -> tuple:
        def precio(position: int) -> str:
            milli = self._precio_milli(producto, position)
            return (
                format_money_milli(milli, self._currency_symbol)
                if milli is not None
                else ""
            )

        celdas = {
            "sku": lambda: producto.sku,
            "legacy_code": lambda: producto.legacy_code or "",
            "name": lambda: producto.name,
            "unit": lambda: producto.unit,
            "cost": lambda: format_money_milli(
                producto.cost_milli, self._currency_symbol
            ),
            "price1": lambda: precio(1),
            "price2": lambda: precio(2),
            "price3": lambda: precio(3),
            "price3_bs_bcv": lambda: self._precio_convertido(producto, "bs_bcv"),
            "price3_bs": lambda: self._precio_convertido(producto, "bs"),
            "price3_bcv": lambda: self._precio_convertido(producto, "usd_bcv"),
            "stock": lambda: format_quantity(producto.current_stock),
            "min_stock": lambda: format_quantity(producto.min_stock),
            "activo": lambda: "Si" if producto.is_active else "No",
        }
        return tuple(celdas[c.key]() for c in self._columnas)

    @staticmethod
    def _tags_de_stock(producto: Product) -> tuple[str, ...]:
        """Color de la fila: rojo si la existencia es negativa, naranja si es baja."""
        if producto.current_stock < 0:
            return ("stock_negativo",)
        return ("bajo_stock",) if producto.is_low_stock else ()

    def _poblar_tabla(
        self, productos: list[Product], coincidencias: list[Product] | None = None
    ) -> None:
        # Una foto de las tasas por poblada (3 consultas), no una lectura
        # de settings por celda convertida - ver RateSnapshot.
        self._tasas = self._rates.snapshot() if self._rates is not None else None
        # Sin cebra: el tag de fondo alternado ("impar") se saco porque
        # en algunas versiones de Tk pisaba mal el alto/fondo de la fila
        # (rayado ilegible, tanto en claro como en oscuro - no vale la
        # pena el riesgo por un adorno visual). Solo queda el color de
        # bajo_stock, que es foreground puro (no toca el fondo de la
        # fila) y no tuvo ese problema. Se relee de la paleta vigente en
        # cada poblada para que un cambio de tema se refleje en la
        # proxima busqueda/reload sin engancharse a ThemeManager (que no
        # sabe de tags de Treeview, solo de widgets - ver theme.py).
        p = get_palette(self)
        self._tree.tag_configure("bajo_stock", foreground=p.warning_bg)
        # Existencia negativa (se vendio sin stock cargado): en rojo, es
        # lo primero que conviene encontrar. Gana sobre el naranja.
        self._tree.tag_configure("stock_negativo", foreground=p.danger_bg)

        self._tree.delete(*self._tree.get_children())
        for p_producto in productos:
            tags = self._tags_de_stock(p_producto)
            self._tree.insert(
                "",
                "end",
                iid=str(p_producto.id),  # el id real: evita mapear fila->producto
                values=self._valores_de(p_producto),
                tags=tags,
            )
        if coincidencias:
            self._tree.insert(
                "",
                "end",
                iid="__coincidencias__",
                values=("COINCIDENCIAS",) + ("",) * (len(self._columnas) - 1),
                tags=("coincidencias",),
            )
            self._tree.tag_configure("coincidencias", foreground=p.muted_fg)
            for p_producto in coincidencias:
                tags = self._tags_de_stock(p_producto)
                self._tree.insert(
                    "",
                    "end",
                    iid=str(p_producto.id),
                    values=self._valores_de(p_producto),
                    tags=tags,
                )
        self._status.configure(
            text=(
                f"Mostrando {len(productos) + len(coincidencias or [])} "
                f"de {len(self._productos)} productos"
            )
        )

    # ------------------------------------------------------------
    # seleccion
    # ------------------------------------------------------------
    def _selected_product_id(self) -> int | None:
        seleccion = self._tree.selection()
        if not seleccion or seleccion[0] == "__coincidencias__":
            return None
        return int(seleccion[0])

    # ------------------------------------------------------------
    # botones
    # ------------------------------------------------------------
    def _on_alta(self) -> None:
        dialogo = ProductDialog(
            self,
            self._service,
            titulo="Nuevo producto",
            supplier_service=self._supplier_service,
            settings_service=self._settings_service,
        )
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_editar(self) -> None:
        product_id = self._selected_product_id()
        if product_id is None:
            messagebox.showinfo(
                "Editar", "Selecciona un producto primero.", parent=self
            )
            return

        dialogo = ProductDialog(
            self,
            self._service,
            titulo="Editar producto",
            product_id=product_id,
            supplier_service=self._supplier_service,
            settings_service=self._settings_service,
            stock_service=self._stock_service,
            recuerdo_ajuste=self._recuerdo_ajuste,
        )
        self.wait_window(dialogo)
        # Un ajuste de existencia ya quedo en la base aunque la ficha se
        # cierre sin guardar: la tabla tiene que mostrar el numero nuevo.
        if dialogo.guardado or dialogo.stock_cambiado:
            self.reload()

    def _on_crear_factura(self) -> None:
        """
        Mismo InvoiceDialog que "Nueva factura" en la pestana Facturas
        (ver BillingPage._on_nueva): un atajo para no cambiar de
        pestana, no una pantalla nueva. reload() al guardar porque
        confirmar() mueve stock (StockService) y esta tabla lo muestra.
        """
        dialogo = InvoiceDialog(
            self,
            self._billing_service,
            self._service,
            self._customer_service,
            currency_symbol=self._currency_symbol,
            settings_service=self._settings_service,
            # Sin esto la factura abierta desde aca no mostraba los
            # montos en Bs (columnas de las lineas y panel de totales).
            rate_display_service=self._rates,
            preview_service=self._preview_service,
        )
        self.wait_window(dialogo)
        if dialogo.guardado:
            self.reload()

    def _on_eliminar(self) -> None:
        product_id = self._selected_product_id()
        if product_id is None:
            messagebox.showinfo(
                "Eliminar", "Selecciona un producto primero.", parent=self
            )
            return

        producto = next((p for p in self._productos if p.id == product_id), None)
        nombre = producto.name if producto else str(product_id)
        if not messagebox.askyesno(
            "Eliminar",
            f"¿Desactivar '{nombre}'?\n\nNo se borra: queda como inactivo.",
            parent=self,
        ):
            return

        try:
            self._service.deactivate_product(product_id)
        except ValueError as error:
            messagebox.showerror("Eliminar", str(error), parent=self)
            return
        self.reload()


# Cuantas entregas (las mas recientes) se ven en la ficha; el resto, en "Ver todas".
_ENTREGAS_EN_FICHA = 3


# Ayuda de la tabla de entregas (icono "i" junto al resumen).
_AYUDA_ENTREGAS = (
    "Doble clic en una fila: abre la ficha de su proveedor.\n"
    "Doble clic en la X: elimina esa entrega."
)


class ProductDialog(kit.Dialog):
    """
    Formulario de alta/edicion: nombre, unidad, costo, descripcion y un
    precio por price_tier existente (Detal/Mayor/Especial).

    Deliberadamente fuera de este formulario: categoria (el alta usa
    la categoria semilla via ProductService.default_category_id()) y
    min_stock (no hay todavia una pantalla de bajo-stock que lo pida).
    Cuando haya un combobox de categorias, esto se completa.
    """

    def __init__(
        self,
        master: tk.Misc,
        service: ProductService,
        *,
        titulo: str,
        product_id: int | None = None,
        supplier_service: SupplierService | None = None,
        settings_service: SettingsService | None = None,
        stock_service: StockService | None = None,
        recuerdo_ajuste: dict[str, str] | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title(titulo)
        self.transient(master)
        self.resizable(True, True)

        self._service = service
        self._supplier_service = supplier_service
        self._product_id = product_id
        self.guardado = False
        # True si se ajusto la existencia desde esta ficha: quien la abrio
        # recarga la tabla (ver ProductsPage._on_editar), aunque la ficha
        # se cierre sin "Guardar".
        self.stock_cambiado = False
        self._stock_service = stock_service
        self._recuerdo_ajuste = recuerdo_ajuste
        # Existencia inicial en el ALTA, solo si Configuracion > Inventario
        # lo permite (sin settings_service, como antes: no se ofrece).
        self._con_existencia_inicial = (
            product_id is None
            and settings_service is not None
            and settings_service.allow_initial_stock()
        )
        self._existencia_var = tk.StringVar()
        self._contado_por_var = tk.StringVar()
        # Al editar: lo que hay hoy, solo para mostrar (ver
        # _build_existencia). Se llena en _cargar_producto.
        self._existencia_actual = ""
        self._existencia_milli = 0
        self._sku = ""
        self._nombre_guardado = ""
        self._unidad_guardada = ""

        self._tiers = service.list_price_tiers()
        self._name_var = tk.StringVar()
        self._unit_var = tk.StringVar(value="UNI")
        self._cost_var = tk.StringVar()
        self._price_vars = {tier.id: tk.StringVar() for tier in self._tiers}
        self._active_var = tk.BooleanVar(value=True)
        # Descripcion no es un StringVar: tk.Text (texto largo, ver
        # schema.sql) todavia no existe aca, se crea en _build_form().
        # Se guarda para insertarla ahi una vez que el widget exista.
        self._description_initial = ""
        # Proveedores del producto (Product.suppliers, ya cargado por
        # ProductService.get_product): el vinculo ACTUAL de cada uno,
        # para precargar "Esta entrega" en "Registrar entrada..." (ver
        # RegistrarProveedorDialog). NO es lo que se muestra en la
        # tabla: eso es _entregas_producto, el historial completo.
        self._proveedores_producto: list[SupplierLink] = []
        # Todas las entregas de este producto, de cualquier proveedor
        # (ver _build_proveedores_section). Con supplier_service se
        # pueden agregar, abrir la ficha de quien las trajo y borrar una
        # puntual; sin el, solo se ven.
        self._entregas_producto: list[SupplierEntry] = []

        if product_id is not None:
            self._cargar_producto(product_id)

        self._build_form()
        self._estado_inicial = self._estado_formulario()
        self.bind("<Return>", lambda _evento: self._on_guardar())
        self.bind("<Escape>", lambda _evento: self.destroy())
        focus_first_entry(self)  # cursor en "Nombre": sin click extra para escribir
        self._ajustar_a_pantalla(master)
        # transient() sola no siempre alcanza para apilar este dialogo
        # ENCIMA de quien lo abrio (se vio pasar en Windows): lift() lo
        # fuerza en vez de confiar en que el usuario lo traiga al frente.
        self.lift()
        self.focus_force()
        self.grab_set()  # modal: despues de construir, para no bloquear errores propios

    def _ajustar_a_pantalla(self, master: tk.Misc) -> None:
        """Ajusta el dialogo al zoom sin esconder Guardar bajo la pantalla."""
        fit_scrollable_dialog_to_screen(
            self,
            master,
            self._cuerpo_formulario,
            self._pie_botones,
            minimum_width=760,
        )

    def _cargar_producto(self, product_id: int) -> None:
        producto = self._service.get_product(product_id)
        if producto is None:
            return  # se creo el dialogo pero el producto desaparecio: campos vacios
        self._name_var.set(producto.name)
        self._unit_var.set(producto.unit)
        self._existencia_actual = (
            f"{format_quantity(producto.current_stock)} {producto.unit}"
        )
        self._existencia_milli = producto.current_stock
        self._sku = producto.sku
        self._nombre_guardado = producto.name
        self._unidad_guardada = producto.unit
        self._cost_var.set(_format_input(producto.cost))
        self._active_var.set(producto.is_active)
        self._description_initial = producto.description
        self._proveedores_producto = producto.suppliers
        self._entregas_producto = self._service.list_all_supplier_entries(product_id)
        for price in producto.prices:
            if price.tier_id in self._price_vars:
                self._price_vars[price.tier_id].set(_format_input(price.price))

    def _build_form(self) -> None:
        # Dos secciones (LabelFrame) en vez de una lista plana de 6-7
        # filas: mismo patron que ya usa InvoiceDialog para "Cliente"/
        # "Lineas" (ver billing_page.py), separando identidad del
        # producto de sus precios en vez de mezclarlos en una sola
        # columna sin jerarquia visual.
        contenedor = kit.Frame(self)
        contenedor.pack(fill="both", expand=True)
        cuerpo = ScrollableDialogBody(contenedor, padding=20)
        cuerpo.pack(side="top", fill="both", expand=True)
        self._cuerpo_formulario = cuerpo
        frame = cuerpo.content
        frame.columnconfigure(0, weight=1)

        datos = kit.LabelFrame(frame, text="Datos generales", padding=10)
        datos.grid(row=0, column=0, sticky="ew")
        datos.columnconfigure(1, weight=1)

        fila = 0
        kit.Label(datos, text="Nombre:").grid(
            row=fila, column=0, sticky="w", pady=6, padx=(0, 8)
        )
        self._name_entry = kit.Entry(datos, textvariable=self._name_var, width=44)
        self._name_entry.grid(row=fila, column=1, sticky="ew", pady=6)

        fila += 1
        kit.Label(datos, text="Descripción:").grid(
            row=fila, column=0, sticky="nw", pady=6, padx=(0, 8)
        )
        self._description_text = tk.Text(datos, width=44, height=3, wrap="word")
        paint_text(self._description_text)
        tab_pasa_al_siguiente(self._description_text)
        self._description_text.grid(row=fila, column=1, sticky="ew", pady=6)
        if self._description_initial:
            self._description_text.insert("1.0", self._description_initial)
        # El <Return> del dialogo entero guarda (ver __init__): dentro de
        # esta caja, Enter tiene que insertar un salto de linea en vez de
        # disparar Guardar a mitad de una descripcion larga. 'break' corta
        # la propagacion antes de que llegue al binding del Toplevel.
        self._description_text.bind("<Return>", self._on_return_en_descripcion)

        # Unidad es un codigo corto (UNI, KG, METRO): no necesita el ancho
        # de la ficha. "Activo" va a su lado, en la misma fila, y solo
        # aparece al editar: en el alta siempre es True, y mostrarlo
        # tildado sin poder tener otro valor es ruido visual.
        fila += 1
        kit.Label(datos, text="Unidad:").grid(
            row=fila, column=0, sticky="w", pady=6, padx=(0, 8)
        )
        unidad = kit.Frame(datos)
        unidad.grid(row=fila, column=1, sticky="w", pady=6)
        kit.Entry(unidad, textvariable=self._unit_var, width=12).pack(side="left")
        if self._product_id is not None:
            kit.Checkbutton(unidad, text="Activo", variable=self._active_var).pack(
                side="left", padx=(20, 0)
            )

        # Costo y los precios de a DOS por fila: son numeros cortos, y
        # cuatro renglones de campos de 900 px era lo que volvia alta esta
        # ficha. Columnas 1 y 3 (los campos) se reparten el ancho.
        precios = kit.LabelFrame(frame, text="Precios", padding=10)
        precios.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        precios.columnconfigure(1, weight=1, uniform="precio")
        precios.columnconfigure(3, weight=1, uniform="precio")

        # El Detal (nivel 3, ver PriceTierRepository) es el que mas se usa:
        # etiqueta en negrita y campo con borde de acento fijo. El tamano no
        # cambia: es solo color y peso de letra.
        campos_de_precio = [("Costo:", self._cost_var, False)] + [
            (f"Precio {tier.name}:", self._price_vars[tier.id], tier.position == 3)
            for tier in self._tiers
        ]
        for indice, (etiqueta, variable, principal) in enumerate(campos_de_precio):
            fila, base = divmod(indice, 2)
            col = base * 2
            kit.Label(
                precios, text=etiqueta, style="Bold.TLabel" if principal else None
            ).grid(
                row=fila,
                column=col,
                sticky="w",
                pady=6,
                padx=(0 if col == 0 else 20, 8),
            )
            entrada = kit.Entry(precios, textvariable=variable, width=12)
            entrada.grid(row=fila, column=col + 1, sticky="ew", pady=6)
            if principal:
                entrada.resaltar()
            ultimo_precio = entrada
        # Tab desde el ultimo precio sigue a lo opcional: si esta plegado,
        # lo abre y cae en su primer campo (ver _on_tab_ultimo_precio).
        ultimo_precio.bind("<Tab>", self._on_tab_ultimo_precio)

        siguiente_fila = 2
        if self._product_id is None:
            # En el alta, existencia inicial y proveedor son opcionales y
            # casi nunca se usan al crear un producto: van plegados en una
            # sola seccion, para que lo comun (nombre y precios) se vea de
            # un vistazo y la ficha no pase de alto. Cada uno se ofrece solo
            # si corresponde (Configuracion > Inventario, supplier_service).
            if self._con_existencia_inicial or self._supplier_service is not None:
                self._build_opcionales(frame, row=siguiente_fila)
        else:
            self._build_existencia(frame, row=siguiente_fila)
            siguiente_fila += 1
            # Al editar: la tabla de entregas del producto.
            self._build_proveedores_section(frame, row=siguiente_fila)

        botones = kit.Frame(contenedor)
        botones.pack(side="bottom", pady=(8, 12))
        self._pie_botones = botones
        # El pie se empaca primero para reservar su alto antes de dar el
        # espacio restante al cuerpo desplazable.
        cuerpo.pack_forget()
        cuerpo.pack(side="top", fill="both", expand=True)
        kit.Button(
            botones, text="Guardar", command=self._on_guardar, style="Success.TButton"
        ).pack(side="left", padx=6)
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="left", padx=6
        )

    def _build_existencia(self, parent: kit.Frame, *, row: int) -> None:
        """
        ALTA (si Configuracion > Inventario lo permite): cuanto hay, y
        quien lo conto. Se guarda como un movimiento INITIAL en la misma
        transaccion que el producto (ver ProductService.create_product).

        EDICION: la existencia se ve pero NO se cambia aca (decidido el
        22/10/2026). Cambiarla con un campo suelto perderia el rastro de
        por que cambio; "Ajustar..." abre una ventana corta encima de la
        ficha donde todo cambio queda con su motivo y quien conto.
        """
        seccion = kit.LabelFrame(parent, text="Existencia", padding=10)
        seccion.grid(row=row, column=0, sticky="ew", pady=(12, 0))
        seccion.columnconfigure(1, weight=1)

        if self._product_id is None:
            # Existencia y quien conto en UNA fila; el aviso debajo, partido
            # al ancho de la ficha (no cabe en un renglon).
            fila_existencia = kit.Frame(seccion)
            fila_existencia.grid(row=0, column=0, columnspan=2, sticky="w")
            kit.Label(fila_existencia, text="Existencia inicial:").pack(side="left")
            self._existencia_entry = kit.Entry(
                fila_existencia, textvariable=self._existencia_var, width=10
            )
            self._existencia_entry.pack(side="left", padx=(8, 20), pady=6)
            kit.Label(fila_existencia, text="Contado por:").pack(side="left")
            kit.Entry(
                fila_existencia, textvariable=self._contado_por_var, width=18
            ).pack(side="left", padx=(8, 0), pady=6)
            ayuda = kit.Label(
                seccion,
                text="Opcional. Si lo dejas vacío, se cuenta después en Ajuste de stock.",
                style="Muted.TLabel",
                # wraplength inicial: sin el, el texto pide su ancho de un
                # solo renglon (~850 px) y ensancha toda la ficha antes de
                # que wrap_to_width lo parta al ancho real.
                wraplength=460,
            )
            ayuda.grid(row=1, column=0, columnspan=2, sticky="ew")
            wrap_to_width(ayuda)
            return

        # El numero a la vista (es lo que se mira) y, a la derecha, la unica
        # forma de cambiarlo: "Ajustar...", que deja el motivo y quien conto.
        fila = kit.Frame(seccion)
        fila.grid(row=0, column=0, columnspan=2, sticky="ew")
        fila.columnconfigure(1, weight=1)
        bloque = kit.Frame(fila)
        bloque.grid(row=0, column=0, sticky="w")
        kit.Label(bloque, text="EN EXISTENCIA", style="CardTitle.TLabel").pack(
            anchor="w"
        )
        self._existencia_texto_var = tk.StringVar(value=self._existencia_actual)
        kit.Label(
            bloque, textvariable=self._existencia_texto_var, style="CardValue.TLabel"
        ).pack(anchor="w")
        if self._stock_service is not None:
            self._btn_ajustar = kit.Button(
                fila,
                text="Ajustar...",
                icon="clipboard-check",
                command=self._on_ajustar,
            )
            self._btn_ajustar.grid(row=0, column=2, sticky="e")

    def _estado_formulario(self) -> tuple:
        """Lo editable de la ficha, para saber si hay cambios sin guardar."""
        return (
            self._name_var.get(),
            self._unit_var.get(),
            self._cost_var.get(),
            self._active_var.get(),
            self._description_text.get("1.0", "end-1c"),
            tuple(var.get() for var in self._price_vars.values()),
        )

    def _on_ajustar(self) -> None:
        """
        Abre "Ajustar existencia" ENCIMA de la ficha: no se sale de ella ni
        se pierde lo que se haya tecleado, asi que ya no hace falta la
        pregunta de "guardar antes de irse". Si se registra el ajuste, el
        numero de la ficha se actualiza al instante.
        """
        dialogo = AjustarExistenciaDialog(
            self,
            self._stock_service,
            product_id=self._product_id,
            sku=self._sku,
            nombre=self._name_var.get() or self._nombre_guardado,
            unidad=self._unidad_guardada,
            existencia_milli=self._existencia_milli,
            recuerdo=self._recuerdo_ajuste,
        )
        self.wait_window(dialogo)
        self.grab_set()  # la ventana de ajuste solto el modal al cerrarse
        if dialogo.resultado is None:
            return
        self._existencia_milli = dialogo.resultado
        self._existencia_actual = (
            f"{format_quantity(dialogo.resultado)} {self._unidad_guardada}"
        )
        self._existencia_texto_var.set(self._existencia_actual)
        self.stock_cambiado = True

    def _build_opcionales(self, parent: kit.Frame, *, row: int) -> None:
        """
        Alta: "Existencia inicial" y/o "Proveedor", plegados detras de un
        boton. Plegado es el estado normal; los campos existen igual (con
        su valor) aunque no se vean.
        """
        partes = []
        if self._con_existencia_inicial:
            partes.append("existencia inicial")
        if self._supplier_service is not None:
            partes.append("proveedor")
        self._titulo_opcionales = " y ".join(partes).capitalize() + " (opcional)"
        self._opcionales_abiertos = False
        self._btn_opcionales = kit.Button(
            parent,
            text=f"\u25b8  {self._titulo_opcionales}",
            command=self._on_opcionales,
        )
        self._btn_opcionales.grid(row=row, column=0, sticky="w", pady=(12, 0))

        self._opcionales = kit.Frame(parent)
        self._opcionales.grid(row=row + 1, column=0, sticky="ew")
        self._opcionales.columnconfigure(0, weight=1)
        fila = 0
        if self._con_existencia_inicial:
            self._build_existencia(self._opcionales, row=fila)
            fila += 1
        if self._supplier_service is not None:
            self._build_proveedor_del_alta(self._opcionales, row=fila)
        self._opcionales.grid_remove()

    def _on_tab_ultimo_precio(self, _evento: object) -> str | None:
        """
        Tab en el ultimo precio, con lo opcional plegado: lo despliega y
        pone el cursor en su primer campo, para recorrer todo el
        formulario sin tocar el mouse. Desplegado (o en la edicion, donde
        no hay seccion opcional), Tab sigue su camino normal.
        """
        if not hasattr(self, "_opcionales") or self._opcionales_abiertos:
            return None
        self._on_opcionales()
        focus_first_entry(self._opcionales)
        return "break"

    def _on_opcionales(self) -> None:
        """Pliega o despliega lo opcional y ajusta el alto de la ventana."""
        self._opcionales_abiertos = not self._opcionales_abiertos
        if self._opcionales_abiertos:
            self._opcionales.grid()
        else:
            self._opcionales.grid_remove()
        flecha = "\u25be" if self._opcionales_abiertos else "\u25b8"
        self._btn_opcionales.configure(text=f"{flecha}  {self._titulo_opcionales}")
        self._reajustar_alto()

    def _reajustar_alto(self) -> None:
        """
        Alto de la ventana al de su contenido (hasta lo que entra en la
        pantalla), sin moverla de donde esta: lo mismo que calcula
        fit_scrollable_dialog_to_screen al abrir, pero conservando x/y.
        """
        self.update_idletasks()
        pantalla = self.winfo_screenheight()
        pedido = (
            self._cuerpo_formulario.content.winfo_reqheight()
            + self._pie_botones.winfo_reqheight()
            + 32
        )
        alto = min(pedido, pantalla - 90)
        y = max(0, min(self.winfo_y(), pantalla - alto - 60))
        tk.Wm.wm_geometry(self, f"{self.winfo_width()}x{alto}+{self.winfo_x()}+{y}")

    def _build_proveedor_del_alta(self, parent: kit.Frame, *, row: int) -> None:
        """
        Nuevo producto: quien lo trajo, opcional. Mismo selector que
        "Registrar entrada..." (buscador que filtra + lista + "Nuevo
        proveedor..."). Al guardar se vincula con el costo del producto y
        la fecha de hoy, que es lo que pasa cuando se da de alta un
        producto porque acaba de llegar. El contacto de la empresa se
        puede asignar aqui; la nota de la entrega se completa despues
        desde la ficha, con "Registrar entrada...".
        """
        seccion = kit.LabelFrame(parent, text="Proveedor (opcional)", padding=10)
        seccion.grid(row=row, column=0, sticky="ew", pady=(12, 0))

        # Antes que el picker: este se llena al construirse y avisa del
        # cambio enseguida, asi que la variable tiene que existir ya.
        self._proveedor_alta_var = tk.StringVar()
        self._contacto_alta_var = tk.StringVar()
        self._proveedor_alta_id: int | None = None
        self._contacto_alta_original = ""
        self._picker_alta = SupplierPicker(
            seccion,
            self._supplier_service,
            altura=5,
            on_change=self._refrescar_proveedor_del_alta,
            boton_nuevo=False,
        )
        self._picker_alta.pack(fill="x")

        contacto = kit.Frame(seccion)
        contacto.pack(fill="x", pady=(8, 0))
        kit.Label(contacto, text="Contacto de la empresa:").pack(side="left")
        # Combo editable: los contactos ya vistos de ese proveedor se
        # eligen de la lista, y uno nuevo se escribe igual.
        self._contacto_alta_entry = kit.Combobox(
            contacto, textvariable=self._contacto_alta_var, state="disabled"
        )
        self._contacto_alta_entry.pack(side="left", fill="x", expand=True, padx=(8, 0))

        # Una sola fila para el aviso y los dos botones: el selector es
        # opcional y no deberia agrandar el alta mas de lo que ocupa.
        pie = kit.Frame(seccion)
        pie.pack(fill="x", pady=(6, 0))
        kit.Label(
            pie, textvariable=self._proveedor_alta_var, style="Muted.TLabel"
        ).pack(side="left")
        kit.Button(
            pie, text="Ninguno", command=self._picker_alta.limpiar, tab=False
        ).pack(side="right")
        kit.Button(
            pie,
            text="Nuevo proveedor...",
            command=self._picker_alta.on_nuevo_proveedor,
            tab=False,
        ).pack(side="right", padx=(0, 6))
        self._refrescar_proveedor_del_alta()

    def _refrescar_proveedor_del_alta(self) -> None:
        # SupplierPicker avisa durante su propio __init__; el campo de
        # contacto se crea justo despues, asi que la primera notificacion
        # no puede intentar habilitar un widget que todavia no existe.
        if not hasattr(self, "_contacto_alta_entry"):
            return
        elegido = self._picker_alta.elegido() if hasattr(self, "_picker_alta") else None
        if elegido is None:
            self._proveedor_alta_var.set("Sin proveedor")
            self._proveedor_alta_id = None
            self._contacto_alta_var.set("")
            self._contacto_alta_original = ""
            self._contacto_alta_entry.configure(state="disabled")
            return
        if elegido.id != self._proveedor_alta_id:
            self._proveedor_alta_id = elegido.id
            self._contacto_alta_original = elegido.contact_name or ""
            self._contacto_alta_var.set(self._contacto_alta_original)
            self._contacto_alta_entry.configure(
                values=self._contactos_conocidos_alta(
                    elegido.id, self._contacto_alta_original
                )
            )
        self._proveedor_alta_var.set(f"Se registra: {elegido.name}")
        self._contacto_alta_entry.configure(state="normal")

    def _contactos_conocidos_alta(self, supplier_id: int, contacto_ficha: str):
        """
        Los nombres de contacto ya vistos en entregas de este proveedor (en
        cualquier producto, una consulta) mas el de su ficha general: lo
        mismo que ofrece "Registrar entrada...".
        """
        contactos = set(self._service.distinct_contacts_for_supplier(supplier_id))
        if contacto_ficha:
            contactos.add(contacto_ficha)
        return sorted(contactos)

    def _build_proveedores_section(self, parent: kit.Frame, *, row: int) -> None:
        """
        TODAS las entregas de este producto, de cualquier proveedor,
        juntas y la mas reciente primero (product_supplier_entries, ver
        ProductService.list_all_supplier_entries) - no una fila por
        proveedor con su ULTIMA entrega nada mas (eso es
        product_suppliers, el vinculo ACTUAL que arma SupplierLink).
        Mostrar solo esa ultima fue justamente lo que le hizo pensar al
        autor que "Registrar entrada..." pisaba la entrega vieja -no
        es asi, quedaba en el historial, pero no habia forma de VERLO
        sin reabrir "Registrar entrada..." y elegir el mismo
        proveedor de nuevo.

        Con supplier_service ademas se puede:
          - doble clic (o Enter) en una entrega: abre la ficha de SU
            proveedor, la misma de la pestana Proveedores;
          - "Registrar entrada...": anotar quien trajo este producto,
            cuando y a cuanto. Agrega una entrega nueva, nunca pisa una
            vieja (regla 6: no toca a los otros proveedores);
          - la "X" de cada fila: borra ESA entrega puntual, cargada por
            error, con confirmacion (ver _on_eliminar_entrega). Dejar
            de comprarle a un proveedor es, con esto, borrar sus
            entregas una por una - no hace falta un "Quitar" aparte.
        Sin supplier_service queda como antes: solo lectura, sin la "X".
        """
        seccion = kit.LabelFrame(parent, text="Entregas", padding=10)
        seccion.grid(row=row, column=0, sticky="ew", pady=(12, 0))
        seccion.columnconfigure(0, weight=1)
        editable = self._supplier_service is not None

        if not self._entregas_producto and not editable:
            kit.Label(
                seccion, text="Sin entregas registradas.", style="Muted.TLabel"
            ).grid(row=0, column=0, sticky="w")
            return

        # Arriba, junto a lo que afectan: cuantas hay y las acciones. Antes
        # los botones vivian en una barra aparte, abajo de todo, lejos de
        # la tabla a la que se refieren.
        cabecera = kit.Frame(seccion)
        cabecera.grid(row=0, column=0, sticky="ew", pady=(0, 8))
        self._entregas_resumen_var = tk.StringVar()
        kit.Label(
            cabecera, textvariable=self._entregas_resumen_var, style="Muted.TLabel"
        ).pack(side="left")
        if editable:
            # La pista de uso, en un icono: no ocupa lugar ni mueve nada.
            self._info_entregas = info_hint(cabecera, _AYUDA_ENTREGAS)
            self._info_entregas.pack(side="left", padx=(8, 0))
            # pack(side="right") apila de derecha a izquierda: "Ver
            # proveedor" queda al borde y la accion principal a su lado.
            self._boton_ver_proveedor = kit.Button(
                cabecera, text="Ver proveedor", command=self._on_ver_proveedor
            )
            self._boton_ver_proveedor.pack(side="right")
            kit.Button(
                cabecera,
                text="Registrar entrada...",
                icon="plus",
                command=self._on_registrar_proveedor,
                style="Success.TButton",
            ).pack(side="right", padx=(0, 8))

        # Sin entregas no hay nada que tabular: un estado vacio que dice
        # que hacer, en su propio renglon (junto a los botones no entra a
        # 150 %) y que parte en renglones si hace falta.
        self._entregas_vacio = kit.Label(
            seccion,
            text="Aún no hay entregas. Registra la primera con "
            "\u00abRegistrar entrada...\u00bb.",
            style="Muted.TLabel",
        )
        self._entregas_vacio.grid(row=1, column=0, sticky="ew")
        wrap_to_width(self._entregas_vacio)

        self._tabla_entregas, self._tree_proveedores = self._crear_tabla_entregas(
            seccion, editable=editable
        )
        self._tabla_entregas.grid(row=2, column=0, sticky="ew")

        # Si hay mas de _ENTREGAS_EN_FICHA, el resto vive en una ventana.
        self._pie_entregas = kit.Frame(seccion)
        self._pie_entregas.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        self._pie_entregas_var = tk.StringVar()
        kit.Label(
            self._pie_entregas,
            textvariable=self._pie_entregas_var,
            style="Muted.TLabel",
        ).pack(side="left")
        self._boton_ver_todas = kit.Button(
            self._pie_entregas, text="Ver todas...", command=self._on_ver_todas
        )
        self._boton_ver_todas.pack(side="right")

        self._pintar_proveedores()

    def _crear_tabla_entregas(
        self, parent: tk.Misc, *, editable: bool, alto: int = 3, scroll: bool = False
    ) -> tuple[kit.Frame, ttk.Treeview]:
        """
        La tabla de entregas, la misma en la ficha (las ultimas) y en la
        ventana "Ver todas": columnas, atajos y la X de cada fila. Los
        atajos reciben la tabla en la que se hizo el clic, asi sirven en
        las dos. Con 'scroll' lleva barra vertical: la ficha no la necesita
        (nunca muestra mas de _ENTREGAS_EN_FICHA filas), "Ver todas" si.
        """
        columnas = ("proveedor", "contacto", "fecha", "costo", "nota")
        if editable:
            columnas += ("quitar",)
        marco = kit.Frame(parent)
        tree = ttk.Treeview(
            marco,
            columns=columnas,
            show="headings",
            height=alto,
            selectmode="browse",
        )
        for col, texto in (
            ("proveedor", "Proveedor"),
            ("contacto", "Contacto"),
            ("fecha", "Fecha"),
            ("costo", "Costo"),
            ("nota", "Nota"),
            ("quitar", ""),
        ):
            if col in columnas:
                tree.heading(col, text=texto)
        # Anchos a la letra de ahora (a 150 % un nombre de proveedor no
        # entra en 120 px) y nunca por debajo de lo que mide el titulo.
        escala = getattr(find_app_root(parent), "_flex_font_scale", 100)
        fuente = tkfont.nametofont("TkHeadingFont")
        ideal: dict[str, int] = {}
        minimo: dict[str, int] = {}
        for col, base in (
            ("proveedor", 140),
            ("contacto", 120),
            ("fecha", 112),
            ("costo", 80),
            ("nota", 160),
            ("quitar", 40),
        ):
            if col not in columnas:
                continue
            titulo = str(tree.heading(col, "text"))
            piso = fuente.measure(titulo) + 24 if titulo else 0
            ideal[col] = max(round(base * escala / 100), piso)
            minimo[col] = max(piso, ideal[col] * 3 // 5)
            tree.column(
                col,
                width=ideal[col],
                minwidth=minimo[col],
                anchor={"fecha": "center", "costo": "e", "quitar": "center"}.get(
                    col, "w"
                ),
                stretch=False,  # el reparto lo hace repartir_anchos()
            )
        alinear_encabezados(tree)

        # El marco pide un ancho fijo (el de las columnas a su tamano
        # ideal) y no el que sumen en cada reparto: si pidiera lo segundo,
        # cada reparto cambiaria lo pedido y el siguiente cambiaria el
        # tamano, en bucle (ver lines_tree).
        marco.pack_propagate(False)
        marco.configure(width=sum(ideal.values()) + (16 if scroll else 0))
        self._alto_marco_entregas(tree, alto)

        def _repartir(evento: tk.Event) -> None:
            anchos = repartir_anchos(ideal, minimo, evento.width - 4, fijas=("quitar",))
            for col, ancho in anchos.items():
                tree.column(col, width=ancho)

        tree.bind("<Configure>", _repartir)
        if scroll:
            scrollbar = kit.Scrollbar(marco, orient="vertical", command=tree.yview)
            tree.configure(yscrollcommand=scrollbar.set)
            scrollbar.pack(side="right", fill="y")
        tree.pack(side="left", fill="x", expand=True)

        if editable:
            # Doble clic y Enter abren la ficha del proveedor de esa fila. El
            # "break" del Enter importa: este dialogo ata <Return> a Guardar
            # en la ventana entera, y sin cortarlo aca, abrir un proveedor
            # ademas guardaría (y cerraria) el producto.
            tree.bind("<Double-1>", lambda e: self._on_click_entregas(e, tree))
            tree.bind("<Return>", lambda _e: (self._on_ver_proveedor(tree), "break")[1])
        return marco, tree

    @staticmethod
    def _alto_marco_entregas(tree: ttk.Treeview, filas: int) -> None:
        """Alto del marco para 'filas' filas mas el encabezado."""
        fila = int(ttk.Style(tree).lookup("Treeview", "rowheight") or 24)
        tree.master.configure(height=(max(filas, 1) + 1) * fila + 2)

    def _llenar_tabla_entregas(
        self, tree: ttk.Treeview, entradas: Sequence[SupplierEntry]
    ) -> None:
        """
        El iid de cada fila es el id de la ENTREGA (no el supplier_id: el
        mismo proveedor puede aparecer en varias filas, una por entrega).
        """
        tree.delete(*tree.get_children())
        editable = self._supplier_service is not None
        for entrada in entradas:
            valores = (
                entrada.supplier_name,
                entrada.contact_name or "",
                entrada.entry_date.strftime("%d/%m/%Y") if entrada.entry_date else "",
                format_money_milli(entrada.cost_milli, symbol="")
                if entrada.cost_milli is not None
                else "",
                entrada.note or "",
            )
            if editable:
                valores += ("❌",)
            tree.insert("", "end", iid=str(entrada.id), values=valores)

    def _pintar_proveedores(self) -> None:
        """
        Pone la ficha al dia con self._entregas_producto: la tabla con
        las ultimas _ENTREGAS_EN_FICHA, el resumen, el estado vacio y el
        pie con "Ver todas" cuando hay mas de las que se ven.
        """
        cantidad = len(self._entregas_producto)
        visibles = self._entregas_producto[:_ENTREGAS_EN_FICHA]
        tree = self._tree_proveedores
        self._llenar_tabla_entregas(tree, visibles)
        self._alto_marco_entregas(tree, len(visibles))

        hay = cantidad > 0
        for widget, visible in (
            (self._tabla_entregas, hay),
            (self._entregas_vacio, not hay),
            (self._pie_entregas, cantidad > _ENTREGAS_EN_FICHA),
        ):
            if visible:
                widget.grid()
            else:
                widget.grid_remove()
        boton = getattr(self, "_boton_ver_proveedor", None)
        if boton is not None:
            boton.state(["!disabled"] if hay else ["disabled"])

        self._pie_entregas_var.set(
            f"Se muestran las {_ENTREGAS_EN_FICHA} más recientes."
        )
        self._boton_ver_todas.configure(text=f"Ver todas ({cantidad})...")
        resumen = getattr(self, "_entregas_resumen_var", None)
        if resumen is not None:
            resumen.set(
                ""
                if cantidad == 0
                else "1 entrega registrada"
                if cantidad == 1
                else f"{cantidad} entregas registradas"
            )

    def _on_ver_todas(self) -> None:
        dialogo = EntregasProductoDialog(self)
        self._dialogo_entregas = dialogo
        self.wait_window(dialogo)
        self._dialogo_entregas = None
        self.grab_set()

    def _recargar_proveedores(self) -> None:
        """
        Vuelve a pedir el producto despues de agregar o borrar una
        entrega. Son las mismas 3 consultas fijas de get() (producto +
        precios + proveedores) mas list_all_supplier_entries, UNA vez
        por accion del usuario: no es un bucle.
        """
        producto = self._service.get_product(self._product_id)
        self._proveedores_producto = producto.suppliers if producto else []
        self._entregas_producto = self._service.list_all_supplier_entries(
            self._product_id
        )
        self._pintar_proveedores()
        ventana = getattr(self, "_dialogo_entregas", None)
        if ventana is not None:
            ventana.refrescar()

    def _entrada_elegida(
        self, accion: str, tree: ttk.Treeview | None = None
    ) -> SupplierEntry | None:
        tree = tree or self._tree_proveedores
        seleccion = tree.selection()
        if not seleccion:
            messagebox.showinfo(
                accion,
                "Elige una entrega de la lista primero.",
                parent=tree.winfo_toplevel(),
            )
            return None
        entry_id = int(seleccion[0])
        return next((e for e in self._entregas_producto if e.id == entry_id), None)

    def _on_click_entregas(
        self, evento: tk.Event, tree: ttk.Treeview | None = None
    ) -> str | None:
        """
        Doble clic en una entrega: abre la ficha de SU proveedor -salvo
        que el clic haya sido en la "X", que la elimina (ver
        _on_eliminar_entrega). Mismo criterio que la x de las lineas de
        factura (BillingPage._on_click_lineas): se mira la COLUMNA por
        nombre, no por posicion, porque sin supplier_service "quitar" ni
        siquiera existe. 'tree' es la tabla donde se hizo el clic: la de
        la ficha o la de "Ver todas".
        """
        tree = tree or self._tree_proveedores
        columna = tree.identify_column(evento.x)
        if columna and tree.column(columna, "id") == "quitar":
            fila = tree.identify_row(evento.y)
            if fila:
                tree.selection_set(fila)
                self._on_eliminar_entrega(tree)
            return "break"
        self._on_ver_proveedor(tree)
        return None

    def _on_ver_proveedor(self, tree: ttk.Treeview | None = None) -> None:
        """
        Abre la ficha del proveedor de la entrega elegida: la misma
        ventana que "Editar" en la pestana Proveedores, con los
        productos que se le compran. Si ahi se cambia el nombre, al
        volver se refresca esta tabla.
        """
        entrada = self._entrada_elegida("Ver proveedor", tree)
        if entrada is None:
            return
        # La ventana que tiene el grab: este dialogo o "Ver todas".
        ventana = (tree or self._tree_proveedores).winfo_toplevel()
        dialogo = SupplierDialog(
            ventana,
            self._supplier_service,
            titulo="Editar proveedor",
            supplier_id=entrada.supplier_id,
            product_service=self._service,
        )
        ventana.wait_window(dialogo)
        # La ficha tomo el grab modal y lo solto del todo al cerrarse.
        ventana.grab_set()
        if dialogo.guardado:
            self._recargar_proveedores()

    def _on_eliminar_entrega(self, tree: ttk.Treeview | None = None) -> None:
        """
        Borra UNA entrega puntual, cargada por error - no todo el
        vinculo con el proveedor (eso ya no existe como accion aparte:
        si se borran todas sus entregas, el vinculo actual desaparece
        solo, ver ProductRepository.delete_entry). Pide confirmacion
        porque, a diferencia de quitar una linea de una factura todavia
        sin confirmar, esto SI borra una fila ya guardada.
        """
        entrada = self._entrada_elegida("Eliminar entrega", tree)
        if entrada is None:
            return
        ventana = (tree or self._tree_proveedores).winfo_toplevel()
        fecha = (
            entrada.entry_date.strftime("%d/%m/%Y")
            if entrada.entry_date
            else "sin fecha"
        )
        if not messagebox.askyesno(
            "Eliminar entrega",
            f"¿Eliminar la entrega de {entrada.supplier_name} del {fecha}?\n\n"
            "No se puede deshacer.",
            parent=ventana,
        ):
            return
        try:
            self._service.delete_supplier_entry(entrada.id)
        except ValueError as error:
            messagebox.showerror("Eliminar entrega", str(error), parent=ventana)
            return
        self._recargar_proveedores()

    def _on_registrar_proveedor(self) -> None:
        dialogo = RegistrarProveedorDialog(
            self,
            self._supplier_service,
            costo_sugerido=self._cost_var.get(),
            vinculos=self._proveedores_producto,
            product_service=self._service,
            product_id=self._product_id,
        )
        self.wait_window(dialogo)
        self.grab_set()
        if dialogo.resultado is None:
            return

        datos = dialogo.resultado
        try:
            # Primero la ficha general (si el usuario confirmo cambiarla),
            # despues el vinculo. Son dos escrituras separadas: si la
            # segunda fallara, la ficha igual queda como el usuario la
            # confirmo, que es lo que pidio.
            if datos.get("ficha"):
                self._supplier_service.update_contact(
                    datos["supplier_id"],
                    contact_name=datos["ficha"]["contact_name"],
                    phone=datos["ficha"]["phone"],
                )
            self._service.link_supplier(
                self._product_id,
                datos["supplier_id"],
                contact_name=datos["contacto"],
                last_purchase=datos["fecha"],
                last_cost=datos["costo"],
                note=datos["nota"],
            )
        except ValueError as error:
            messagebox.showerror("Registrar entrada", str(error), parent=self)
            return
        self._recargar_proveedores()

    def _on_return_en_descripcion(self, _evento: object) -> str:
        self._description_text.insert("insert", "\n")
        return "break"

    def _on_guardar(self) -> None:
        try:
            cost = parse_local_decimal(self._cost_var.get() or "0")
            prices = self._leer_precios()
        except InvalidOperation:
            messagebox.showerror(
                "Guardar", "Costo y precios deben ser numeros (ej. 1,50).", parent=self
            )
            return
        try:
            existencia = parse_local_decimal(self._existencia_var.get().strip() or "0")
        except InvalidOperation:
            messagebox.showerror(
                "Guardar",
                "La existencia inicial debe ser un número (ej. 12).",
                parent=self,
            )
            return

        name = self._name_var.get()
        unit = self._unit_var.get().strip() or "UNI"
        description = self._description_text.get("1.0", "end-1c").strip()

        try:
            if self._product_id is None:
                if not self._confirmar_contacto_del_alta():
                    return
                nuevo = self._service.create_product(
                    name=name,
                    category_id=self._service.default_category_id(),
                    prices=prices,
                    cost=cost,
                    unit=unit,
                    description=description,
                    initial_stock=existencia if self._con_existencia_inicial else 0,
                    counted_by=self._contado_por_var.get(),
                )
                self._guardar_contacto_del_alta()
                self._vincular_proveedor_del_alta(nuevo.id, cost)
            else:
                self._service.update_product(
                    self._product_id,
                    name=name,
                    prices=prices,
                    cost=cost,
                    is_active=self._active_var.get(),
                    unit=unit,
                    description=description,
                )
        except ValueError as error:
            messagebox.showerror("Guardar", str(error), parent=self)
            return

        self.guardado = True
        self.destroy()

    def _vincular_proveedor_del_alta(self, product_id: int, cost: Decimal) -> None:
        """
        Son DOS escrituras, no una transaccion: create_product y
        link_supplier abren cada una la suya, y SQLite no anida BEGIN.
        Si la segunda fallara (el proveedor se desactivo en el medio,
        por ejemplo), el producto igual queda creado: se avisa y el
        vinculo se puede hacer despues desde la ficha. Es el orden
        correcto - perder el producto por un vinculo seria peor.
        """
        picker = getattr(self, "_picker_alta", None)
        elegido = picker.elegido() if picker is not None else None
        if elegido is None:
            return
        try:
            self._service.link_supplier(
                product_id,
                elegido.id,
                contact_name=self._contacto_alta_var.get(),
                last_purchase=date.today(),  # noqa: DTZ011 - hora local
                last_cost=cost if cost > 0 else None,
            )
        except ValueError as error:
            messagebox.showwarning(
                "Guardar",
                f"El producto se guardo, pero no se pudo registrar el proveedor: "
                f"{error}\nSe puede hacer desde la ficha del producto.",
                parent=self,
            )

    def _confirmar_contacto_del_alta(self) -> bool:
        """Pide permiso antes de cambiar la ficha general del proveedor."""
        picker = getattr(self, "_picker_alta", None)
        elegido = picker.elegido() if picker is not None else None
        if elegido is None:
            return True
        nuevo_contacto = self._contacto_alta_var.get().strip()
        if nuevo_contacto == self._contacto_alta_original.strip():
            return True
        return messagebox.askyesno(
            "Cambiar la ficha del proveedor",
            f"Esto cambia el contacto GENERAL de {elegido.name}:\n\n"
            f"'{self._contacto_alta_original or '-'}'  ->  "
            f"'{nuevo_contacto or '-'}'\n\n"
            "Vale para todos los productos de este proveedor. ¿Guardar el cambio?",
            parent=self,
        )

    def _guardar_contacto_del_alta(self) -> None:
        """Actualiza el contacto confirmado sin impedir guardar el producto."""
        picker = getattr(self, "_picker_alta", None)
        elegido = picker.elegido() if picker is not None else None
        if elegido is None or self._supplier_service is None:
            return
        nuevo_contacto = self._contacto_alta_var.get().strip()
        if nuevo_contacto == self._contacto_alta_original.strip():
            return
        try:
            self._supplier_service.update_contact(
                elegido.id,
                contact_name=nuevo_contacto,
                phone=elegido.phone,
            )
        except ValueError as error:
            messagebox.showwarning(
                "Guardar",
                "El producto se guardo, pero no se pudo actualizar el contacto "
                f"del proveedor: {error}",
                parent=self,
            )

    def _leer_precios(self) -> dict[int, Decimal]:
        """Los tiers con el campo vacio no entran en el dict: no es un precio en 0."""
        precios = {}
        for tier_id, var in self._price_vars.items():
            texto = var.get().strip()
            if texto:
                precios[tier_id] = parse_local_decimal(texto)
        return precios


class EntregasProductoDialog(kit.Dialog):
    """
    Todas las entregas de un producto, la mas reciente primero: la ficha
    solo muestra las ultimas _ENTREGAS_EN_FICHA. Misma tabla y mismos
    atajos (doble clic abre al proveedor, doble clic en la X elimina); la
    logica vive en ProductDialog y esta ventana solo la muestra.
    """

    def __init__(self, producto: "ProductDialog") -> None:
        super().__init__(producto)
        paint_window(self)
        self._producto = producto
        self.title(f"Entregas - {producto._name_var.get()}")
        self.transient(producto)
        self.resizable(True, True)
        self.bind("<Escape>", lambda _e: self.destroy())

        marco = kit.Frame(self, padding=16)
        marco.pack(fill="both", expand=True)
        self._resumen_var = tk.StringVar()
        encabezado = kit.Frame(marco)
        encabezado.pack(fill="x", pady=(0, 8))
        kit.Label(
            encabezado, textvariable=self._resumen_var, style="Heading.TLabel"
        ).pack(side="left")
        editable = producto._supplier_service is not None
        tabla, self._tree = producto._crear_tabla_entregas(
            marco, editable=editable, alto=12, scroll=True
        )
        tabla.pack(fill="both", expand=True)
        if editable:
            info_hint(encabezado, _AYUDA_ENTREGAS).pack(side="left", padx=(8, 0))
        kit.Button(marco, text="Cerrar", command=self.destroy).pack(
            anchor="e", pady=(12, 0)
        )
        self.refrescar()
        self.lift()
        self.focus_force()
        self.grab_set()

    def refrescar(self) -> None:
        """Vuelve a pintar con las entregas actuales; sin ninguna, se cierra."""
        entradas = self._producto._entregas_producto
        if not entradas:
            self.destroy()
            return
        self._producto._llenar_tabla_entregas(self._tree, entradas)
        self._producto._alto_marco_entregas(self._tree, min(len(entradas), 12))
        self._resumen_var.set(
            "1 entrega registrada"
            if len(entradas) == 1
            else f"{len(entradas)} entregas registradas"
        )


def _format_input(value: Decimal) -> str:
    """
    Decimal('5.000') -> '5,00'   (2 decimales: el caso normal)
    Decimal('1.125') -> '1,125'  (3 solo si el catalogo los usa de
                                   verdad - venta fraccionada - nunca
                                   se recorta precision real)
    """
    dos_decimales = Decimal("0.01")
    if value == value.quantize(dos_decimales):
        value = value.quantize(dos_decimales)
    return f"{value:f}".replace(".", ",")


class SupplierPicker(kit.Frame):
    """
    Selector de proveedor: un campo donde se escribe para filtrar y, debajo,
    una lista desplegable (flotante, sin empujar el formulario) con los
    proveedores activos; "Nuevo proveedor..." da de alta al que falte
    (queda elegido al volver). Lo usan
    RegistrarProveedorDialog y el alta de un producto, asi la forma de
    elegir un proveedor es la misma en los dos lados.

    Una sola consulta al armarlo (list_suppliers, ~80-150 filas segun
    CLAUDE.md) y despues todo el filtrado es en memoria: cada tecla es
    un recorrido de la lista en Python, sin ir a SQLite.

    Teclado: flecha abajo/arriba desde el campo mueve la eleccion sin
    sacarle el foco, igual que en las tablas; Enter elige; Esc cierra la
    lista. Al elegir un proveedor, la lista se cierra y el nombre queda
    fijo en el campo; escribir de nuevo la reabre para cambiarlo.
    """

    def __init__(
        self,
        master: tk.Misc,
        supplier_service: SupplierService,
        *,
        altura: int = 6,
        on_activate: Callable[[], None] | None = None,
        on_change: Callable[[], None] | None = None,
        boton_nuevo: bool = True,
    ) -> None:
        super().__init__(master)
        self._supplier_service = supplier_service
        self._on_activate = on_activate
        # Se llama despues de CUALQUIER cambio de eleccion: click, flechas,
        # filtro que deja un unico resultado, alta nueva, limpiar. Hace
        # falta porque <<ListboxSelect>> solo lo dispara el mouse, no un
        # selection_set() hecho desde el codigo.
        self._on_change = on_change
        self._proveedores: list = []  # activos, en memoria
        self._filtrados: list = []
        self._selection_fixed = False
        self._setting_fixed_text = False
        self._altura = altura  # filas maximas de la lista desplegable
        self._abierta = False

        self.buscar_var = tk.StringVar()
        self.entry = kit.Entry(self, textvariable=self.buscar_var, width=30)
        self.entry.pack(fill="x")
        self.entry.bind("<Down>", lambda _e: self._mover(1))
        self.entry.bind("<Up>", lambda _e: self._mover(-1))
        self.entry.bind("<Return>", self._on_entry_return)
        self.entry.bind("<Escape>", self._on_entry_escape)
        self.entry.bind("<Tab>", self._on_entry_tab)
        self.entry.bind("<FocusIn>", self._on_entry_focus, add="+")
        self.entry.bind("<Button-1>", self._on_entry_click, add="+")
        self.entry.bind("<KeyRelease>", self._on_entry_tecla, add="+")
        self.entry.bind("<FocusOut>", self._on_entry_sin_foco, add="+")

        # La lista flota sobre el formulario (en vez de empujar lo de abajo)
        # DENTRO de la misma ventana, con place(): una ventanita aparte sin
        # marco parpadeaba en GNOME/Wayland (ver picker.FloatingPicker). Se
        # arma una vez y se muestra/esconde.
        ventana = self.winfo_toplevel()
        self._capa = tk.Frame(ventana, background=get_palette(self).border, bd=0)
        self._capa._flex_repintar = lambda: self._capa.configure(
            background=get_palette(self).border
        )
        self.bind(
            "<Destroy>",
            lambda e: (
                self._capa.destroy()
                if e.widget is self and self._capa.winfo_exists()
                else None
            ),
            add="+",
        )
        self._pos_campo: tuple[int, int] | None = None
        ventana.bind("<Configure>", self._al_cambiar_ventana, add="+")
        self.lista = tk.Listbox(
            self._capa,
            height=altura,
            exportselection=False,
            activestyle="none",
            takefocus=0,
        )
        paint_listbox(self.lista)
        self.lista.pack(fill="both", expand=True, padx=1, pady=1)
        self.lista.bind("<Double-Button-1>", self._on_doble_clic)
        self.lista.bind("<<ListboxSelect>>", self._on_list_select)

        # boton_nuevo=False: quien lo usa lo pone el mismo, en otra fila
        # (el alta de producto lo junta con "Ninguno" para ahorrar alto).
        if boton_nuevo:
            kit.Button(
                self,
                text="Nuevo proveedor...",
                command=self.on_nuevo_proveedor,
                tab=False,
            ).pack(anchor="w", pady=(4, 0))

        self.buscar_var.trace_add("write", lambda *_a: self._filtrar())
        self.cargar()

    # ------------------------------------------------------------
    def cargar(self, elegir_id: int | None = None) -> None:
        self._proveedores = sorted(
            (s for s in self._supplier_service.list_suppliers() if s.is_active),
            key=lambda s: s.name.upper(),
        )
        self._filtrar()
        if elegir_id is not None:
            self.elegir(elegir_id)

    def elegido(self):
        """El proveedor marcado en la lista, o None."""
        seleccion = self.lista.curselection()
        return self._filtrados[seleccion[0]] if seleccion else None

    def elegir(self, supplier_id: int) -> None:
        self.buscar_var.set("")
        for indice, s in enumerate(self._filtrados):
            if s.id == supplier_id:
                self.lista.selection_clear(0, "end")
                self.lista.selection_set(indice)
                self.lista.see(indice)
                break
        self._fijar_elegido()

    def limpiar(self) -> None:
        self.buscar_var.set("")
        self.lista.selection_clear(0, "end")
        self._selection_fixed = False
        self._cerrar_lista()
        self._avisar()

    def _avisar(self) -> None:
        if self._on_change is not None:
            self._on_change()

    def on_nuevo_proveedor(self) -> None:
        """
        Alta de proveedor sin salir de donde se esta. El dialogo de alta
        toma el grab modal y lo suelta del todo al cerrarse, asi que se
        le devuelve a la ventana que contiene este selector.
        """
        ventana = self.winfo_toplevel()
        # Lo que ya se escribio en el campo (sin eleccion hecha) pasa como
        # nombre: "Nuevo proveedor..." desde una busqueda sin resultados no
        # obliga a teclearlo de nuevo.
        escrito = "" if self._selection_fixed else self.buscar_var.get()
        dialogo = SupplierDialog(
            ventana,
            self._supplier_service,
            titulo="Nuevo proveedor",
            nombre_inicial=escrito,
        )
        self.wait_window(dialogo)
        if ventana.winfo_exists():
            ventana.grab_set()
        if dialogo.guardado:
            self.cargar(elegir_id=dialogo.supplier_id)

    # ------------------------------------------------------------
    def _filtrar(self) -> None:
        if self._selection_fixed and not self._setting_fixed_text:
            # El usuario volvio a escribir: vuelve a elegir entre la lista.
            self._selection_fixed = False
        principales, coincidencias = split_search_results(
            self._proveedores, self.buscar_var.get(), lambda s: s.name
        )
        self._filtrados = principales + coincidencias
        self.lista.delete(0, "end")
        for s in self._filtrados:
            self.lista.insert("end", s.name)
        if len(self._filtrados) == 1:
            # Un solo resultado: queda elegido, se puede seguir con Enter.
            self.lista.selection_set(0)
        self._avisar()

    def _on_list_select(self, _event: tk.Event) -> None:
        self._fijar_elegido()

    def _fijar_elegido(self) -> None:
        """Muestra la empresa elegida en el campo y cierra su lista."""
        proveedor = self.elegido()
        if proveedor is None:
            self._avisar()
            return
        self._setting_fixed_text = True
        self.buscar_var.set(proveedor.name)
        self._setting_fixed_text = False
        for indice, candidate in enumerate(self._filtrados):
            if candidate.id == proveedor.id:
                self.lista.selection_clear(0, "end")
                self.lista.selection_set(indice)
                break
        self._selection_fixed = True
        self._cerrar_lista()
        self._avisar()

    # ------------------------------------------------------------
    # lista desplegable
    # ------------------------------------------------------------
    def _abrir_lista(self) -> None:
        """Muestra (o reacomoda) la lista justo debajo del campo."""
        if not self._filtrados:
            self._cerrar_lista()
            return
        self.lista.configure(height=min(len(self._filtrados), self._altura))
        ventana = self._capa.master
        ventana.update_idletasks()
        ancho = self.entry.winfo_width()
        alto = self._capa.winfo_reqheight()
        # Coordenadas de la ventana, no de la pantalla: place() es relativo.
        x = self.entry.winfo_rootx() - ventana.winfo_rootx()
        arriba = self.entry.winfo_rooty() - ventana.winfo_rooty()
        y = arriba + self.entry.winfo_height() + 2
        if y + alto > ventana.winfo_height() - 8 and arriba - alto - 2 >= 0:
            y = arriba - alto - 2  # sin lugar abajo: arriba
        self._pos_campo = posicion_en_ventana(self.entry)
        self._capa.place(x=max(x, 0), y=y, width=ancho, height=alto)
        self._capa.lift()
        self._abierta = True

    def _al_cambiar_ventana(self, evento: tk.Event) -> None:
        """El formulario se scrolleo (o la ventana cambio de tamano) y el
        campo ya no esta donde se abrio la lista: se cierra en vez de quedar
        flotando lejos de su campo."""
        if not self._abierta or str(evento.widget).startswith(str(self._capa)):
            return
        if posicion_en_ventana(self.entry) != self._pos_campo:
            self._cerrar_lista()

    def _cerrar_lista(self) -> None:
        if self._abierta:
            self._capa.place_forget()
            self._abierta = False

    def _on_entry_focus(self, _evento: object = None) -> None:
        if self._selection_fixed:
            # Elegido: al volver al campo queda todo seleccionado, asi
            # escribir reemplaza el nombre para elegir otro.
            self.entry.select_range(0, "end")
        else:
            self._abrir_lista()

    def _on_entry_click(self, _evento: object = None) -> None:
        if not self._selection_fixed and not self._abierta:
            self._abrir_lista()

    _TECLAS_QUE_NO_ABREN = frozenset(
        {"Return", "Escape", "Up", "Down", "Tab", "ISO_Left_Tab", "Shift_L", "Shift_R"}
    )

    def _on_entry_tecla(self, evento: tk.Event) -> None:
        if evento.keysym not in self._TECLAS_QUE_NO_ABREN and not self._selection_fixed:
            self._abrir_lista()

    def _on_entry_tab(self, _evento: object = None) -> None:
        """
        Tab con algo escrito y sin eleccion: elige la primera coincidencia
        (la resaltada, si se bajo con las flechas) y sigue al campo
        siguiente. Sin esto, recorrer el formulario con Tab dejaba el
        proveedor sin elegir y el resto apagado. A diferencia de Enter,
        NO llama a on_activate: pasar de campo no registra nada.
        """
        if (
            self._selection_fixed
            or not self.buscar_var.get().strip()
            or not self._filtrados
        ):
            return
        if not self.lista.curselection():
            self.lista.selection_set(0)
        self._fijar_elegido()

    def _on_entry_escape(self, _evento: object = None) -> str | None:
        """Esc con la lista abierta solo la cierra: no cierra la ventana."""
        if self._abierta:
            self._cerrar_lista()
            return "break"
        return None

    def _on_entry_sin_foco(self, _evento: object = None) -> None:
        # Con demora: un clic en la lista le saca el foco al campo un
        # instante antes de elegir, y cerrarla de golpe tragaba el clic.
        self.after(150, self._cerrar_si_perdio_foco)

    def _cerrar_si_perdio_foco(self) -> None:
        """Solo si el foco se fue de verdad: si volvio al campo, un FocusOut
        pasajero no la cierra (cerrar y reabrir se ve como parpadeo)."""
        if not self.winfo_exists():
            return
        foco = self.focus_get()
        if foco is not None and (
            foco is self.lista or str(foco).startswith(str(self.entry))
        ):
            return
        self._cerrar_lista()

    def _mover(self, delta: int) -> str:
        if not self._filtrados:
            return "break"
        if not self._abierta and not self._selection_fixed:
            self._abrir_lista()
        actual = self.lista.curselection()
        if actual:
            indice = actual[0] + delta
        else:
            indice = 0 if delta > 0 else len(self._filtrados) - 1
        indice = max(0, min(len(self._filtrados) - 1, indice))
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(indice)
        self.lista.see(indice)
        self._avisar()
        return "break"

    def _on_doble_clic(self, evento: tk.Event) -> str | None:
        """
        Elige el renglon bajo el mouse (no el que estaba elegido antes)
        y lo fija. ``on_activate`` queda disponible para un futuro uso
        que necesite confirmar una eleccion explicitamente. nearest()
        porque el doble clic de un Listbox se procesa a nivel del widget
        ANTES que la seleccion propia de la clase Listbox para ese mismo
        clic.
        """
        if not self._filtrados:
            return None
        indice = self.lista.nearest(evento.y)
        self.lista.selection_clear(0, "end")
        self.lista.selection_set(indice)
        self._fijar_elegido()
        if self._on_activate is not None:
            self._on_activate()
            return "break"
        return None

    def _on_entry_return(self, _evento: tk.Event) -> str | None:
        """
        Enter en el buscador: si el filtro ya dejo elegido un unico
        resultado (ver _filtrar) esto no hace falta, pero con varias
        coincidencias (nombres parecidos: "MOK" y "MOK DISTRIBUIDORA")
        nada quedaba elegido y Enter no hacia nada, sin avisar por que -
        quedaba identico a apretar Enter con la lista vacia. Ahora, con
        algo escrito y sin eleccion, toma la primera de la lista (la de
        arriba: coincidencia de "empieza por" antes que interna, ver
        split_search_results) y la deja fija en el campo.

        "break" siempre que haya algo filtrado: este Enter es del
        buscador, no del formulario que lo contiene. Sin esto, en el
        selector opcional de "Nuevo producto" (sin on_activate) el mismo
        Enter seguia de largo y terminaba GUARDANDO EL PRODUCTO con el
        primer proveedor que matcheara el filtro, sin que nadie lo haya
        elegido a proposito.

        Buscador VACIO queda afuera: ahi _filtrados son TODOS los
        proveedores activos (ver _filtrar), y un Enter sin haber escrito
        nada no puede elegir "el primero alfabetico" solo.
        """
        if not self.buscar_var.get().strip() or not self._filtrados:
            return None
        if not self.lista.curselection():
            self.lista.selection_set(0)
        self._fijar_elegido()
        if self._on_activate is not None:
            self._on_activate()
        return "break"


class RegistrarProveedorDialog(kit.Dialog):
    """
    "Registrar entrada": quien trajo este producto, cuando y a cuanto.
    Primero se elige el proveedor
    (SupplierPicker: buscador + lista + "Nuevo proveedor..."); recien ahi
    se habilita el resto, ya lleno con lo que el sistema tiene guardado:

      Ficha del proveedor   contacto y telefono de SU ficha general
                            (tabla suppliers). Se pueden corregir, pero
                            eso cambia la ficha para TODOS sus productos:
                            al registrar se pide confirmacion. El combo
                            de Contacto, con product_service, se llena
                            con los nombres ya vistos en entregas de ese
                            proveedor (ver distinct_contacts_for_
                            supplier): elegir en vez de tipear de nuevo.
      Esta entrega          fecha, costo y nota de la entrada que se va
                            a registrar AHORA. Arranca con la fecha de
                            hoy y el costo que se acaba de poner en la
                            ficha del producto: es lo que pasa casi
                            siempre. Las anteriores no se pisan: quedan
                            en el Historial de abajo.
      Historial de entregas con product_service, la lista de entregas
                            previas de ESTE producto con el proveedor
                            elegido (product_supplier_entries), la mas
                            reciente primero. Sin product_service (o
                            proveedor recien elegido, sin entregas) no
                            se muestra.

    No escribe nada: deja todo en self.resultado y ProductDialog llama a
    los servicios, asi las reglas viven en un solo lugar. Enter en
    cualquier parte del dialogo, o doble clic en un proveedor, registra.
    """

    def __init__(
        self,
        master: tk.Misc,
        supplier_service: SupplierService,
        *,
        costo_sugerido: str = "",
        vinculos: "Sequence[SupplierLink]" = (),
        product_service: ProductService | None = None,
        product_id: int | None = None,
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Registrar entrada")
        self.transient(master)
        self.resizable(True, True)
        self.bind("<Escape>", lambda _e: self.destroy())

        self._supplier_service = supplier_service
        self._costo_sugerido = costo_sugerido
        # Lo que este producto ya tiene registrado con cada proveedor,
        # para precargar "Esta entrega" (ya vino con get_product, ninguna
        # consulta nueva).
        self._vinculos = {v.supplier_id: v for v in vinculos}
        # Opcionales: sin ellos (ej. tests que arman el dialogo suelto)
        # el Historial no se muestra y el combo de Contacto queda sin
        # sugerencias, pero el resto funciona igual que antes.
        self._product_service = product_service
        self._product_id = product_id
        self.resultado: dict | None = None
        # A quien corresponden los campos que se ven, y como estaban en
        # su ficha al cargarlos: contra eso se compara para saber si el
        # usuario cambio la ficha general.
        self._cargado_id: int | None = None
        self._ficha_original = ("", "")

        self._contacto_var = tk.StringVar()
        self._telefono_var = tk.StringVar()
        # Hoy desde que se abre, sin esperar a elegir proveedor: lo normal es
        # registrar la entrada el dia que llega la mercancia (se cambia a
        # mano para cargar una vieja).
        self._fecha_var = tk.StringVar(
            value=date.today().strftime("%d/%m/%Y")  # noqa: DTZ011
        )
        self._costo_var = tk.StringVar()
        self._nota_var = tk.StringVar()
        self._estado_var = tk.StringVar()

        self._build_form()
        self._on_elegir()  # arranca todo deshabilitado (o con el unico elegido)

        fit_scrollable_dialog_to_screen(
            self,
            master,
            self._cuerpo_formulario,
            self._pie_botones,
            minimum_width=1100,
        )
        self.lift()
        self.focus_force()
        self._entry_buscar.focus_set()
        self.grab_set()

    def _build_form(self) -> None:
        contenedor = kit.Frame(self)
        contenedor.pack(fill="both", expand=True)
        cuerpo = ScrollableDialogBody(contenedor, padding=14)
        cuerpo.pack(side="top", fill="both", expand=True)
        self._cuerpo_formulario = cuerpo
        frame = cuerpo.content
        frame.columnconfigure(0, weight=1)

        kit.Label(frame, text="Proveedor *", style="Heading.TLabel").grid(
            row=0, column=0, sticky="w"
        )
        self._campos: list[kit.Entry] = []
        self._picker = SupplierPicker(
            frame,
            self._supplier_service,
            on_change=self._on_elegir,
        )
        self._picker.grid(row=1, column=0, sticky="ew", pady=(4, 0))
        # Nombres cortos para las partes del selector (los usan los tests).
        self._entry_buscar = self._picker.entry
        self._buscar_var = self._picker.buscar_var
        self._lista = self._picker.lista

        kit.Label(frame, textvariable=self._estado_var, style="Muted.TLabel").grid(
            row=2, column=0, sticky="w", pady=(8, 0)
        )

        ficha = kit.LabelFrame(frame, text="Ficha del proveedor", padding=10)
        ficha.grid(row=3, column=0, sticky="ew", pady=(8, 0))
        ficha.columnconfigure(1, weight=1)
        kit.Label(ficha, text="Contacto:").grid(row=0, column=0, sticky="w", pady=3)
        # Combobox en vez de Entry: con product_service (ver _on_elegir)
        # las 'values' son los contactos ya vistos en entregas de este
        # proveedor -elegir a Ramon en vez de tipearlo de nuevo-, pero
        # sigue siendo editable para el primero (todavia sin historial).
        self._contacto_combo = kit.Combobox(
            ficha, textvariable=self._contacto_var, width=28
        )
        self._contacto_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0), pady=3)
        self._campos: list[kit.Entry] = [self._contacto_combo]
        self._agregar_campos(
            ficha, (("Teléfono:", self._telefono_var),), fila_inicial=1
        )
        kit.Label(
            ficha,
            text="Si los cambias, se actualiza su ficha general:\n"
            "vale para todos los productos de este proveedor.",
            style="Muted.TLabel",
        ).grid(row=2, column=0, columnspan=2, sticky="w", pady=(4, 0))

        entrega = kit.LabelFrame(frame, text="Esta entrega", padding=10)
        entrega.grid(row=4, column=0, sticky="ew", pady=(8, 0))
        self._agregar_campos(
            entrega,
            (
                ("Fecha (dd/mm/aaaa):", self._fecha_var),
                ("Costo:", self._costo_var),
                ("Nota:", self._nota_var),
            ),
        )

        self._historial_frame = kit.LabelFrame(
            frame, text="Historial de entregas", padding=10
        )
        self._historial_frame.grid(row=5, column=0, sticky="ew", pady=(8, 0))
        self._historial_tree = ttk.Treeview(
            self._historial_frame,
            columns=("fecha", "costo", "contacto", "nota"),
            show="headings",
            height=3,
        )
        for col, texto, ancho, anchor in (
            ("fecha", "Fecha", 90, "center"),
            ("costo", "Costo", 80, "e"),
            ("contacto", "Contacto", 110, "w"),
            ("nota", "Nota", 160, "w"),
        ):
            self._historial_tree.heading(col, text=texto)
            self._historial_tree.column(col, width=ancho, anchor=anchor)
        self._historial_tree.pack(fill="x")
        self._historial_frame.grid_remove()  # oculto hasta que haya algo que mostrar

        botones = kit.Frame(contenedor)
        botones.pack(side="bottom", pady=(8, 12))
        self._pie_botones = botones
        cuerpo.pack_forget()
        cuerpo.pack(side="top", fill="both", expand=True)
        self._btn_registrar = kit.Button(
            botones,
            text="Registrar",
            command=self._on_registrar,
            style="Success.TButton",
        )
        self._btn_registrar.pack(side="left", padx=4)
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="left", padx=4
        )
        self.bind("<Return>", lambda _e: self._on_registrar())

    def _agregar_campos(
        self, parent: kit.Frame, campos, *, fila_inicial: int = 0
    ) -> None:
        parent.columnconfigure(1, weight=1)
        for indice, (texto, var) in enumerate(campos):
            fila = fila_inicial + indice
            kit.Label(parent, text=texto).grid(row=fila, column=0, sticky="w", pady=3)
            entrada = kit.Entry(parent, textvariable=var, width=30)
            entrada.grid(row=fila, column=1, sticky="ew", padx=(8, 0), pady=3)
            self._campos.append(entrada)

    # ------------------------------------------------------------
    def _elegido(self):
        return self._picker.elegido()

    def _on_nuevo_proveedor(self) -> None:
        self._picker.on_nuevo_proveedor()

    def _on_elegir(self) -> None:
        """
        Cada vez que cambia la eleccion. Si el proveedor es OTRO que el
        que ya estaba cargado, rellena los campos con sus datos; si es
        el mismo (ej. se siguio escribiendo en el buscador y quedo
        elegido igual) no toca nada, para no pisar lo que el usuario ya
        estaba corrigiendo.
        """
        if not hasattr(self, "_btn_registrar"):
            # El picker avisa ya al construirse, antes de que exista el
            # resto del formulario: __init__ vuelve a llamar al final.
            return
        proveedor = self._elegido()
        estado = "normal" if proveedor is not None else "disabled"
        for campo in self._campos:
            campo.configure(state=estado)
        self._btn_registrar.configure(state=estado)

        if proveedor is None:
            self._estado_var.set("Elige un proveedor para ver y completar sus datos.")
            self._historial_frame.grid_remove()
            return
        if proveedor.id == self._cargado_id:
            return

        self._cargado_id = proveedor.id
        contacto, telefono = proveedor.contact_name or "", proveedor.phone or ""
        self._ficha_original = (contacto, telefono)
        self._contacto_var.set(contacto)
        self._telefono_var.set(telefono)
        self._contacto_combo.configure(
            values=self._contactos_conocidos(proveedor.id, contacto)
        )

        # Siempre hoy y el costo que se acaba de poner en la ficha (cambiado
        # el 23/10/2026, a pedido del autor): lo normal es registrar la
        # entrada el dia que llega la mercancia, al costo al que vino. Antes,
        # si el proveedor ya traia el producto, se precargaba la entrega
        # ANTERIOR (fecha y costo viejos) y habia que corregirlos a mano
        # cada vez. Esa anterior se sigue viendo en el Historial de abajo;
        # para cargar una entrada vieja, se cambia la fecha a mano.
        vinculo = self._vinculos.get(proveedor.id)
        self._fecha_var.set(date.today().strftime("%d/%m/%Y"))  # noqa: DTZ011
        costo_anterior = (
            _format_input(from_milli(vinculo.last_cost_milli))
            if vinculo is not None and vinculo.last_cost_milli is not None
            else ""
        )
        # Sin costo en la ficha (producto sin costo cargado), el de la
        # ultima entrega es mejor punto de partida que un campo vacio.
        self._costo_var.set(self._costo_sugerido.strip() or costo_anterior)
        self._nota_var.set("")
        if vinculo is not None:
            self._estado_var.set(
                f"{proveedor.name} ya trae este producto: esta entrada se suma "
                "a las anteriores, no las pisa."
            )
        else:
            self._estado_var.set(f"Primera vez que se registra a {proveedor.name} aca.")

        self._refrescar_historial(proveedor.id)

    def _contactos_conocidos(self, supplier_id: int, contacto_ficha: str) -> list[str]:
        """
        Para las 'values' del combo de Contacto: los nombres ya vistos en
        entregas de este proveedor (cualquier producto) mas el de su
        ficha general, si tiene uno y todavia no aparecio en ninguna
        entrega. Sin product_service (tests que arman el dialogo suelto,
        o el autor usandolo sin esa pieza) el combo queda sin
        sugerencias: sigue siendo editable, igual que un Entry.
        """
        if self._product_service is None:
            return []
        contactos = set(
            self._product_service.distinct_contacts_for_supplier(supplier_id)
        )
        if contacto_ficha:
            contactos.add(contacto_ficha)
        return sorted(contactos)

    def _refrescar_historial(self, supplier_id: int) -> None:
        """
        Entregas previas de ESTE producto con el proveedor elegido, la
        mas reciente primero (ver ProductService.list_supplier_entries).
        Sin product_service/product_id (alta de producto, donde todavia
        no hay id; o un dialogo armado sin esa pieza en los tests) el
        historial no tiene de donde salir y la seccion queda oculta.
        """
        self._historial_tree.delete(*self._historial_tree.get_children())
        if self._product_service is None or self._product_id is None:
            self._historial_frame.grid_remove()
            return

        entradas: list[SupplierEntry] = self._product_service.list_supplier_entries(
            self._product_id, supplier_id
        )
        if not entradas:
            self._historial_frame.grid_remove()
            return

        for entrada in entradas:
            self._historial_tree.insert(
                "",
                "end",
                values=(
                    entrada.entry_date.strftime("%d/%m/%Y")
                    if entrada.entry_date
                    else "",
                    _format_input(from_milli(entrada.cost_milli))
                    if entrada.cost_milli is not None
                    else "",
                    entrada.contact_name or "",
                    entrada.note or "",
                ),
            )
        self._historial_frame.grid()

    def _cambios_en_la_ficha(self) -> list[str]:
        """Que datos de la ficha GENERAL cambio el usuario, en texto."""
        antes_contacto, antes_telefono = self._ficha_original
        cambios = []
        for etiqueta, antes, ahora in (
            ("Contacto", antes_contacto, self._contacto_var.get().strip()),
            ("Teléfono", antes_telefono, self._telefono_var.get().strip()),
        ):
            if antes.strip() != ahora:
                cambios.append(f"{etiqueta}: '{antes or '-'}'  ->  '{ahora or '-'}'")
        return cambios

    def _on_registrar(self) -> None:
        proveedor = self._elegido()
        if proveedor is None:
            messagebox.showerror(
                "Registrar entrada", "Elige un proveedor de la lista.", parent=self
            )
            return

        texto_fecha = self._fecha_var.get().strip()
        try:
            fecha = (
                # Solo el dia (hora local, igual que issued_at): la zona
                # horaria no significa nada para "cuando llego la mercancia".
                datetime.strptime(texto_fecha, "%d/%m/%Y").date()  # noqa: DTZ007
                if texto_fecha
                else None
            )
        except ValueError:
            messagebox.showerror(
                "Registrar entrada",
                "La fecha tiene que ser dd/mm/aaaa (ej. 27/09/2026), o dejarla vacía.",
                parent=self,
            )
            return

        texto_costo = self._costo_var.get().strip()
        try:
            costo = parse_local_decimal(texto_costo) if texto_costo else None
        except InvalidOperation:
            messagebox.showerror(
                "Registrar entrada",
                "El costo tiene que ser un número (ej. 1,24), o dejarlo vacío.",
                parent=self,
            )
            return

        cambios = self._cambios_en_la_ficha()
        if cambios and not messagebox.askyesno(
            "Cambiar la ficha del proveedor",
            f"Esto cambia la ficha GENERAL de {proveedor.name}:\n\n"
            + "\n".join(cambios)
            + "\n\nVale para todos los productos de este proveedor, no solo "
            "para este. ¿Guardar el cambio?",
            parent=self,
        ):
            return  # el dialogo queda abierto para corregir o volver atras

        self.resultado = {
            "supplier_id": proveedor.id,
            "contacto": self._contacto_var.get(),
            "fecha": fecha,
            "costo": costo,
            "nota": self._nota_var.get(),
            # Solo si hubo cambios confirmados: None = no tocar la ficha.
            "ficha": (
                {
                    "contact_name": self._contacto_var.get(),
                    "phone": self._telefono_var.get(),
                }
                if cambios
                else None
            ),
        }
        self.destroy()
