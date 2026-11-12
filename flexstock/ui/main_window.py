"""
Ventana principal (CustomTkinter): una barra lateral con las paginas
agrupadas y, a la derecha, el encabezado con las tasas y la pagina
elegida.

    +----------------+--------------------------------------------+
    | [F] FlexStock  | Inicio            [BCV] [USDT] [EUR] [IVA] |
    |     La Esquina  |--------------------------------------------|
    |  Inicio        |                                            |
    | VENTAS         |                                            |
    |  Facturas      |   la pagina elegida (ui/home_page.py,      |
    |  Clientes      |   ui/products_page.py, ...)                |
    | INVENTARIO     |                                            |
    |  Productos     |                                            |
    |  ...           |                                            |
    | SISTEMA        |                                            |
    |  Configuracion |                                            |
    | (o) Respaldo:  |                                            |
    |     hoy 14:32  |                                            |
    +----------------+--------------------------------------------+

Antes eran pestanas de un ttk.Notebook, un menu "Archivo" y botones
sueltos para el tema y Configuracion. Ahora todo es una pagina de la
barra: lo que es "de toda la app" (respaldar, modo claro/oscuro,
tamano de letra, datos de la tienda) vive en Configuracion, la ultima.
Abajo de todo queda a la vista cuando fue el ultimo respaldo, en rojo
si hace mucho: es lo unico de la app cuyo descuido puede costar TODO.

Cada pagina es una de las de ui/ ya probadas por separado (ver
test_products_page.py, test_customers_page.py, etc.): este archivo solo
las instancia, las muestra de a una y las agrupa. No agrega logica de
negocio propia.
"""

import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from datetime import datetime
from decimal import Decimal, InvalidOperation
from tkinter import messagebox

import customtkinter as ctk

from ..domain.money import parse_local_decimal
from ..services.backup_service import BackupService
from ..services.billing_service import BillingService
from ..services.customer_service import CustomerService
from ..services.exchange_rate_service import (
    ExchangeRateService,
    fetch_eur_oficial_now,
    fetch_rate_now,
    fetch_usdt_now,
)
from ..services.export_service import ExportService
from ..services.preview_service import PreviewService
from ..services.product_service import ProductService
from ..services.quotation_service import QuotationService
from ..services.rate_display_service import RateDisplayService
from ..services.settings_service import FONT_SCALES, SettingsService
from ..services.stock_service import StockService
from ..services.supplier_service import SupplierService
from . import columns, icons, kit, simpledialog
from .billing_page import BillingPage
from .config_page import ConfigPage
from .customers_page import CustomersPage
from .home_page import DIAS_SIN_RESPALDO_ALERTA, HomePage
from .products_page import ProductsPage
from .quotations_page import QuotationsPage
from .stock_adjustment_page import StockAdjustmentPage
from .suppliers_page import SuppliersPage
from .theme import ThemeManager, ctk_font, font_shortcut_direction, pair
from .widgets import RateCard, Tooltip, toast

# Titulo (el de la barra lateral y el del encabezado) -> icono de
# assets/icons/ (ver ui/icons.py) y una linea que dice para que sirve.
_PAGINAS: dict[str, tuple[str, str]] = {
    "Inicio": ("layout-dashboard", "Cómo viene el día, de un vistazo"),
    "Facturas": ("receipt", "Ventas emitidas, PDF y cierre de caja"),
    "Cotizaciones": ("file-text", "Notas de entrega a tasa propia (Modo avanzado)"),
    "Clientes": ("users", "Fichas de clientes e historial de compras"),
    "Productos": ("package", "Catálogo, precios y existencia"),
    "Ajuste de stock": (
        "clipboard-check",
        "Conteo de inventario, producto por producto",
    ),
    "Proveedores": ("truck", "A quién se le compra cada producto"),
    "Configuración": ("settings", "Tienda, respaldos, apariencia y columnas"),
}

# Grupos de la barra lateral, en orden. Configuracion va aparte, abajo.
_GRUPOS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("", ("Inicio",)),
    ("VENTAS", ("Facturas", "Cotizaciones", "Clientes")),
    ("INVENTARIO", ("Productos", "Ajuste de stock", "Proveedores")),
)
_CONFIGURACION = "Configuración"

# Barra plegable: ancho plegada (lo justo para el cuadrado de la marca, 44,
# con 17 de aire a cada lado) y como se anima el cambio.
_ANCHO_PLEGADA = 78
# Margen lateral de la marca y de los botones: plegada, el boton queda del
# mismo ancho que el cuadrado de la marca (78 - 2 x 17 = 44).
_MARGEN_BARRA = 17
_ANCHO_BOTON_PLEGADO = _ANCHO_PLEGADA - 2 * _MARGEN_BARRA
_DURACION_SIDEBAR_MS = 170
_PASO_SIDEBAR_MS = 10


class MainWindow(kit.PlainWindowGeometry, ctk.CTk):
    def __init__(
        self,
        *,
        product_service: ProductService,
        customer_service: CustomerService,
        supplier_service: SupplierService,
        billing_service: BillingService,
        stock_service: StockService,
        backup_service: BackupService,
        export_service: ExportService,
        exchange_rate_service: ExchangeRateService,
        settings_service: SettingsService,
        quotation_service: QuotationService,
        rate_display_service: RateDisplayService,
        preview_service: PreviewService | None = None,
    ) -> None:
        super().__init__()
        self.title("FlexStock-lite")
        # Casi toda la pantalla, con 1280x820 de piso, en vez de 1280x820
        # fijo: con la letra al 150 %, en esa ventana el panel de Ajuste
        # de stock se quedaba sin lugar para su historial. Mismos margenes
        # que size_dialog_to_screen (barra del escritorio y marco).
        # tk.Wm directo: CTk.geometry() multiplicaria estos pixeles reales
        # por su escalado de pantalla.
        ancho_pantalla = self.winfo_screenwidth()
        alto_pantalla = self.winfo_screenheight()
        ancho = min(max(1280, int(ancho_pantalla * 0.9)), ancho_pantalla - 24)
        alto = min(max(820, int(alto_pantalla * 0.88)), alto_pantalla - 90)
        tk.Wm.wm_geometry(self, f"{ancho}x{alto}+{(ancho_pantalla - ancho) // 2}+0")
        tk.Wm.wm_minsize(self, min(1000, ancho), min(650, alto))

        self._backup_service = backup_service
        self._export_service = export_service
        self._exchange_rate_service = exchange_rate_service
        self._billing_service = billing_service
        self._settings_service = settings_service
        self._rate_display_service = rate_display_service
        # Opcional: sin el, Nueva factura / Nueva cotizacion no muestran
        # el boton "Vista previa".
        self._preview_service = preview_service

        self._build_estilos()
        self._theme = ThemeManager(
            self,
            initial=settings_service.ui_theme(),
            font_scale=settings_service.font_scale(),
        )
        # Un solo binding generico: segun el teclado Tk llama al menos
        # "minus", "underscore" o "KP_Subtract". Los bindings literales
        # dejaban Ctrl - sin funcionar en algunos layouts.
        self.bind_all("<Control-KeyPress>", self._on_atajo_tamano)
        self._build_layout()
        self._build_encabezado()
        self._build_paginas(
            product_service=product_service,
            customer_service=customer_service,
            supplier_service=supplier_service,
            billing_service=billing_service,
            stock_service=stock_service,
            quotation_service=quotation_service,
        )
        # Las paginas y sus widgets tk "clasicos" (Listbox de productos,
        # Text de descripcion, tablas) recien existen a partir de aca: una
        # segunda pasada del mismo tema los pinta a todos de una vez (ver
        # ThemeManager/theme.py). Los CTk ya se pintan solos.
        self._theme.apply(self._theme.mode)
        self._actualizar_tasas_si_hace_falta()

    # ------------------------------------------------------------
    # construccion
    # ------------------------------------------------------------
    def _build_estilos(self) -> None:
        """
        Tk arranca con fuentes chicas por default (~9pt). Los widgets
        ttk que quedan (Treeview, Spinbox) y los tk clasicos (Text,
        Listbox) toman su fuente de estas fuentes NOMBRADAS, asi que
        agrandarlas ACA agranda todo eso de una sola vez. Los widgets
        CustomTkinter usan sus propias fuentes compartidas
        (theme.ctk_font), que salen del mismo tamano.
        Tiene que llamarse antes de construir cualquier otro widget.
        """
        # 14pt es el 100 %. ThemeManager aplica despues la preferencia
        # persistida y los atajos Ctrl+/Ctrl- sobre estas fuentes base.
        for nombre in ("TkDefaultFont", "TkTextFont", "TkHeadingFont", "TkMenuFont"):
            tkfont.nametofont(nombre).configure(size=14)
        # El rowheight del Treeview NO va aca: ThemeManager (construido
        # despues, ver __init__) llama theme_use('clam'), que resetea
        # cualquier configure() de estilo hecho antes. Vive en
        # theme.py::_configurar_ttk, la unica funcion que corre DESPUES
        # de theme_use() en cada cambio de tema - ver esa nota ahi
        # (bug real: las filas se veian cortadas, con la coma decimal
        # de los montos leyendose como punto).

    def _build_layout(self) -> None:
        """Barra lateral a la izquierda, encabezado + pagina a la derecha."""
        self.grid_columnconfigure(1, weight=1)
        self.grid_rowconfigure(0, weight=1)

        self._sidebar = ctk.CTkFrame(
            self, fg_color=pair("sidebar_bg"), corner_radius=0, width=256
        )
        self._sidebar.grid(row=0, column=0, sticky="nsw")
        self._sidebar.grid_propagate(False)
        self._sidebar.pack_propagate(False)

        # Marca: un cuadrado con el logo de la tienda (o una "F" de
        # FlexStock si no cargaron ninguno), el nombre de la app y debajo
        # el de la tienda (Configuracion > Tienda). Tocar el cuadrado
        # pliega la barra a solo iconos, y la vuelve a abrir.
        self._sidebar_plegada = self._settings_service.sidebar_collapsed()
        # Lo que esta DIBUJADO (texto o solo iconos): durante la animacion
        # va un paso detras de _sidebar_plegada, que es la intencion.
        self._sidebar_en_iconos = False
        self._sidebar_animacion: str | None = None
        self._ancho_abierto = 256
        self._titulo_activo: str | None = None
        self._alto_encabezado = 0
        marca = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        marca.pack(side="top", fill="x", padx=17, pady=(22, 10))
        self._marca_boton = ctk.CTkButton(
            marca,
            text="F",
            width=44,
            height=44,
            corner_radius=12,
            border_spacing=0,
            fg_color=pair("sidebar_active_bg"),
            hover_color=pair("accent_hover"),
            text_color=pair("sidebar_active_fg"),
            font=ctk_font(self, "brand"),
            command=self._alternar_sidebar,
        )
        self._marca_boton.pack(side="left")
        Tooltip(
            self._marca_boton,
            lambda: "Mostrar menu" if self._sidebar_plegada else "Plegar menu",
        )
        textos = ctk.CTkFrame(marca, fg_color="transparent")
        self._marca_textos = textos
        ctk.CTkLabel(
            textos,
            text="FlexStock",
            font=ctk_font(self, "heading"),
            text_color=pair("sidebar_fg"),
            anchor="w",
            height=0,
        ).pack(anchor="w")
        self._tienda_var = tk.StringVar(value=self._billing_service.company_name())
        ctk.CTkLabel(
            textos,
            textvariable=self._tienda_var,
            font=ctk_font(self, "small"),
            text_color=pair("sidebar_muted_fg"),
            anchor="w",
            height=0,
            wraplength=170,
            justify="left",
        ).pack(anchor="w")
        # Desplegada, el nombre se ve desde el arranque: _poner_modo_sidebar
        # es idempotente y no lo empaca si el modo "con texto" ya era el actual.
        if not self._sidebar_plegada:
            textos.pack(side="left", fill="x", expand=True, padx=(12, 0))
        self._refrescar_marca()

        # Abajo, reservado ANTES que la navegacion (pack reparte el alto
        # en orden): estado del respaldo y Configuracion nunca se cortan.
        self._pie = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._pie.pack(side="bottom", fill="x", padx=_MARGEN_BARRA, pady=(0, 16))
        # Dos botones por cada cosa de la barra, uno con texto y otro de solo
        # icono: plegar/desplegar es cambiar cual esta empacado, sin
        # redibujar ninguno (reconfigurar los 9 con texto nuevo y ancho
        # nuevo tardaba ~50 ms y los iconos parpadeaban a mitad de camino).
        self._estado_respaldo = self._boton_respaldo(solo_icono=False)
        self._estado_respaldo_icono = self._boton_respaldo(solo_icono=True)
        self._texto_respaldo = ""
        # El mismo texto partido en dos renglones (ver _refrescar_estado_
        # respaldo): "Ultimo respaldo: hace 12 dias" en uno solo no entra
        # en el ancho de la barra y se cortaba en "hace 1".
        self._texto_respaldo_barra = ""
        # El alto sale de la letra (2 renglones), asi que hay que
        # recalcularlo con Ctrl+/Ctrl-.
        self._pie.on_font_scale_changed = self._ajustar_alto_respaldo
        self._estado_respaldo_icono._flex_tooltip = Tooltip(
            self._estado_respaldo_icono,
            lambda: self._texto_respaldo if self._sidebar_plegada else None,
        )

        # Los botones de cada pagina se agregan en _build_paginas, aca.
        self._nav = ctk.CTkFrame(self._sidebar, fg_color="transparent")
        self._nav.pack(side="top", fill="x", padx=_MARGEN_BARRA)

        self._main = ctk.CTkFrame(self, fg_color="transparent", corner_radius=0)
        self._main.grid(row=0, column=1, sticky="nsew")
        self._contenido = kit.Frame(self._main)
        self._contenido.pack(
            side="bottom", fill="both", expand=True, padx=16, pady=(0, 16)
        )

    def _alto_boton_lateral(self) -> int:
        """
        Lo que mide un boton con texto (renglon + 2 x 10 de border_spacing +
        2 de borde). Con "" CTk lo dejaba en 42: al plegar los iconos
        saltaban de lugar. Fijarlo igual en los dos modos los deja quietos.
        """
        return max(42, ctk_font(self, "body").metrics("linespace") + 22)

    def _boton_lateral(
        self, master: tk.Misc, titulo: str, comando, *, solo_icono: bool = False
    ) -> ctk.CTkButton:
        boton = ctk.CTkButton(
            master,
            text="" if solo_icono else f"  {titulo}",
            command=comando,
            anchor="center" if solo_icono else "w",
            width=_ANCHO_BOTON_PLEGADO if solo_icono else 140,
            height=self._alto_boton_lateral(),
            corner_radius=10,
            font=ctk_font(self, "body"),
            border_spacing=10,
            compound="left",
        )
        boton._flex_icono = _PAGINAS[titulo][0]
        boton._flex_titulo = titulo
        boton._flex_activo = None
        # Plegado no sigue el ancho de la barra: asi no hay nada que
        # redibujar mientras se anima. Abierto llena el ancho.
        pady = 0 if titulo == _CONFIGURACION else 1
        boton._flex_pack = (
            {"side": "top", "anchor": "w", "pady": pady}
            if solo_icono
            else {"side": "top", "fill": "x", "pady": pady}
        )
        self._pintar_boton_lateral(boton, activo=False)
        if solo_icono:
            # Plegada, el nombre aparece al pasar el mouse por el icono.
            boton._flex_tooltip = Tooltip(
                boton, lambda: titulo if self._sidebar_plegada else None
            )
        return boton

    def _boton_respaldo(self, *, solo_icono: bool) -> ctk.CTkButton:
        boton = ctk.CTkButton(
            self._pie,
            text="",
            anchor="center" if solo_icono else "w",
            width=_ANCHO_BOTON_PLEGADO if solo_icono else 140,
            height=48,
            corner_radius=10,
            border_width=1,
            # Con 10 el icono queda donde estara plegada (centrado bajo la
            # marca), asi no salta al plegar.
            border_spacing=10,
            border_color=pair("sidebar_hover_bg"),
            fg_color=pair("sidebar_bg"),
            hover_color=pair("sidebar_hover_bg"),
            font=ctk_font(self, "small"),
            command=lambda: self._abrir_configuracion("Respaldo y exportación"),
        )
        boton._flex_pack = (
            {"side": "bottom", "anchor": "w", "pady": (10, 0)}
            if solo_icono
            else {"side": "bottom", "fill": "x", "pady": (10, 0)}
        )
        if not solo_icono:
            boton.pack(**boton._flex_pack)
        return boton

    # ------------------------------------------------------------
    # barra plegable
    # ------------------------------------------------------------
    def _refrescar_marca(self) -> None:
        """
        El logo de la tienda en el cuadrado de la marca, si lo cargaron
        en Configuracion > Tienda; si no, la "F" de FlexStock. Mismo logo
        que sale en las facturas, achicado una sola vez aca.
        """
        logo = self._billing_service.company_logo()
        if not logo:
            self._marca_boton.configure(text="F", image=None)
            return
        import io

        from PIL import Image

        try:
            with Image.open(io.BytesIO(logo)) as imagen:
                chica = imagen.convert("RGBA")
                chica.thumbnail((36, 36))
        except Exception:  # noqa: BLE001 - un logo ilegible no rompe la ventana
            self._marca_boton.configure(text="F", image=None)
            return
        self._logo_marca = ctk.CTkImage(light_image=chica, size=chica.size)
        self._marca_boton.configure(
            text="", image=self._logo_marca, fg_color=pair("surface")
        )

    def _alternar_sidebar(self) -> None:
        self._sidebar_plegada = not self._sidebar_plegada
        self._settings_service.set_sidebar_collapsed(self._sidebar_plegada)
        self._animar_sidebar()

    def _aplicar_sidebar(self) -> None:
        """Estado final de golpe, sin animar (arranque, cambio de letra)."""
        self._cancelar_animacion_sidebar()
        self._poner_modo_sidebar(iconos=self._sidebar_plegada)
        self._ajustar_ancho_sidebar()

    def _animar_sidebar(self) -> None:
        """
        Plegar/desplegar en pasos de ancho con ease-out. El cambio texto <->
        solo iconos se hace UNA vez y con la barra quieta: al plegar, antes
        de achicarla (los iconos ya son botones de ancho fijo, asi que no
        se redibujan en cada paso); al desplegar, al llegar al ancho final.
        Medido: cada redibujo de un boton CTk cuesta ~6 ms, y con 9 botones
        siguiendo el ancho un paso pasaba de 20 ms y la animacion salia
        entrecortada, con iconos que se perdian a mitad de camino. El
        tiempo manda y no la cantidad de pasos: si la maquina va lenta se
        saltan pasos, la duracion no se estira. Tocar de nuevo a mitad de
        camino reanuda desde el ancho actual.
        """
        self._cancelar_animacion_sidebar()
        if self._sidebar_plegada:
            self._poner_modo_sidebar(iconos=True)
        self._anim_desde = int(self._sidebar.cget("width"))
        self._anim_hasta = self._ancho_objetivo_sidebar()
        self._anim_inicio = time.perf_counter()
        self._paso_animacion_sidebar()

    def _paso_animacion_sidebar(self) -> None:
        self._sidebar_animacion = None
        t = (time.perf_counter() - self._anim_inicio) * 1000 / _DURACION_SIDEBAR_MS
        if t >= 1 or self._anim_desde == self._anim_hasta:
            self._aplicar_sidebar()
            return
        facil = 1 - (1 - t) ** 3  # ease-out cubico
        ancho = round(self._anim_desde + (self._anim_hasta - self._anim_desde) * facil)
        self._sidebar.configure(width=ancho)
        # Pinta este paso ya: sin esto, Tk deja el redibujado para cuando
        # no haya nada vencido y los pasos se amontonan en uno solo.
        self.update_idletasks()
        self._sidebar_animacion = self.after(
            _PASO_SIDEBAR_MS, self._paso_animacion_sidebar
        )

    def _cancelar_animacion_sidebar(self) -> None:
        if self._sidebar_animacion is not None:
            self.after_cancel(self._sidebar_animacion)
            self._sidebar_animacion = None

    def destroy(self) -> None:
        # Un paso pendiente tocaria widgets ya destruidos.
        if getattr(self, "_sidebar_animacion", None) is not None:
            self.after_cancel(self._sidebar_animacion)
            self._sidebar_animacion = None
        super().destroy()

    def _ancho_objetivo_sidebar(self) -> int:
        return _ANCHO_PLEGADA if self._sidebar_plegada else self._ancho_abierto

    def _poner_modo_sidebar(self, *, iconos: bool) -> None:
        """
        Texto o solo iconos. Plegada: sin titulos de grupo ni nombre de la
        tienda; el nombre de cada pagina aparece al pasar el mouse
        (Tooltip). Idempotente: si ya esta asi, no redibuja nada. Las
        paginas no se enteran: solo cambia el ancho de la columna izquierda.
        """
        if iconos == self._sidebar_en_iconos:
            return
        self._sidebar_en_iconos = iconos
        if iconos:
            self._marca_textos.pack_forget()
        else:
            self._marca_textos.pack(side="left", fill="x", expand=True, padx=(12, 0))
        viejos, nuevos = (
            (self._nav_buttons, self._nav_iconos)
            if iconos
            else (self._nav_iconos, self._nav_buttons)
        )
        pares = [(viejos[t], nuevos[t]) for t in viejos]
        if iconos:
            pares.append((self._estado_respaldo, self._estado_respaldo_icono))
        else:
            pares.append((self._estado_respaldo_icono, self._estado_respaldo))
        for viejo, nuevo in pares:
            if not viejo.winfo_manager():  # Cotizaciones oculta sigue oculta
                continue
            nuevo.pack(before=viejo, **nuevo._flex_pack)
            viejo.pack_forget()
            self._sincronizar_activo(nuevo)
        self._cambiar_encabezados_de_grupo()

    def _botones_a_la_vista(self) -> dict[str, ctk.CTkButton]:
        return self._nav_iconos if self._sidebar_en_iconos else self._nav_buttons

    def _sincronizar_activo(self, boton: ctk.CTkButton) -> None:
        """El boton que estuvo oculto no se enteró de la pagina elegida."""
        if getattr(boton, "_flex_titulo", None) is None:
            return
        activo = boton._flex_titulo == self._titulo_activo
        if boton._flex_activo != activo:
            self._pintar_boton_lateral(boton, activo)

    def _ajustar_alto_respaldo(self) -> None:
        """Dos renglones de letra chica con texto; uno (solo el icono)
        plegada. Alineado a la izquierda: CTk centra el texto."""
        linea = ctk_font(self, "small").metrics("linespace")
        self._estado_respaldo.configure(height=max(48, 2 * linea + 24))
        self._estado_respaldo_icono.configure(height=max(48, linea + 24))
        etiqueta = getattr(self._estado_respaldo, "_text_label", None)
        if etiqueta is not None:
            etiqueta.configure(justify="left")

    def _pintar_boton_lateral(self, boton: ctk.CTkButton, activo: bool) -> None:
        boton._flex_activo = activo
        texto = pair("sidebar_active_fg") if activo else pair("sidebar_fg")
        boton.configure(
            fg_color=pair("sidebar_active_bg") if activo else pair("sidebar_bg"),
            hover_color=pair("sidebar_active_bg")
            if activo
            else pair("sidebar_hover_bg"),
            text_color=texto,
            image=icons.icon(self, boton._flex_icono, texto, size=20),
        )

    def _titulo_de_grupo(self, texto: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            self._nav,
            text=texto,
            font=ctk_font(self, "small_bold"),
            text_color=pair("sidebar_muted_fg"),
            anchor="w",
            height=0,
        )

    def _build_encabezado(self) -> None:
        """
        Una sola fila arriba de la pagina. A la izquierda, el titulo de la
        pagina elegida y una linea de que es; a la derecha, una tarjeta por
        valor global: tasa BCV, USDT, Euro e IVA. Cada tarjeta muestra
        nombre chico, numero grande y fecha de publicacion chica
        -jerarquia para que el ojo vaya al numero, en vez de una fila de
        texto toda al mismo tamano- y un click la edita.

        Las tasas son solo informativas (Regla 7: ningun calculo de factura
        las lee). Arrancan con el valor cacheado en settings;
        _actualizar_tasas_si_hace_falta() las refresca en un hilo aparte si
        la cache tiene mas de un dia, y el boton de refresco las fuerza.
        """
        fila = kit.Frame(self._main)
        # before=: la pagina se empaco antes (en _build_layout) y pack
        # reparte el alto en ese orden. Sin esto, a 150 % la pagina se
        # quedaba con todo y las tarjetas de tasas salian cortadas.
        fila.pack(side="top", fill="x", padx=16, pady=(16, 12), before=self._contenido)

        # Se empaca al FINAL (ver mas abajo): pack reparte el ancho en
        # orden y el ultimo es el que cede. Si falta lugar (letra al
        # 150 %), que se corte el subtitulo y no una tasa.
        titulos = kit.Frame(fila)
        self._titulo_pagina_var = tk.StringVar()
        self._subtitulo_pagina_var = tk.StringVar()
        kit.Label(
            titulos, textvariable=self._titulo_pagina_var, style="Title.TLabel"
        ).pack(anchor="w")
        kit.Label(
            titulos, textvariable=self._subtitulo_pagina_var, style="Muted.TLabel"
        ).pack(anchor="w")

        # Valor y fecha en variables separadas: van en renglones distintos
        # de cada tarjeta (ver _refrescar_tarjetas).
        self._tasa_bcv_var = tk.StringVar()
        self._tasa_usdt_var = tk.StringVar()
        self._tasa_bcv_eur_var = tk.StringVar()
        self._fecha_bcv_var = tk.StringVar()
        self._fecha_usdt_var = tk.StringVar()
        self._fecha_eur_var = tk.StringVar()
        self._iva_var = tk.StringVar()
        self._tasa_propia_var = tk.StringVar()
        self._refrescar_tarjetas()

        # Icono y no el glifo "⟳": depende de la fuente del sistema y en
        # algunas se dibuja como una "C" cortada. Ancho en caracteres para
        # que entre el icono con aire a cualquier tamano de letra.
        self._refrescar_tasas_button = kit.Button(
            fila,
            text="",
            icon="refresh-cw",
            width=4,
            command=self._on_refrescar_tasas,
        )
        self._refrescar_tasas_button.pack(side="right", padx=(8, 0), fill="y")
        Tooltip(self._refrescar_tasas_button, lambda: "Actualizar tasas")

        # pack(side="right") apila de derecha a izquierda: el orden de esta
        # tupla es el inverso del que se ve (BCV, USDT, Euro, IVA).
        self._tarjetas: dict[str, RateCard] = {}
        for titulo, valor, fecha, editar in (
            ("IVA", self._iva_var, None, self._on_editar_iva),
            (
                "Euro",
                self._tasa_bcv_eur_var,
                self._fecha_eur_var,
                self._on_editar_tasa_eur,
            ),
            (
                "USDT",
                self._tasa_usdt_var,
                self._fecha_usdt_var,
                self._on_editar_tasa_usdt,
            ),
            ("BCV", self._tasa_bcv_var, self._fecha_bcv_var, self._on_editar_tasa_bcv),
        ):
            tarjeta = RateCard(
                fila, title=titulo, value_var=valor, date_var=fecha, on_click=editar
            )
            tarjeta.pack(side="right", padx=(8, 0))
            self._tarjetas[titulo] = tarjeta

        titulos.pack(side="left", fill="both", expand=True)

        # Tasa propia: solo con Modo avanzado (ver _aplicar_modo_avanzado),
        # destacada y a la izquierda de BCV. Es la MISMA tasa de
        # Cotizaciones y del "Ver total en" de Nueva factura.
        self._tarjeta_propia = RateCard(
            fila,
            title="Tasa propia",
            value_var=self._tasa_propia_var,
            on_click=self._on_editar_tasa_propia,
            highlighted=True,
        )

    def _mostrar_tarjeta_propia(self, visible: bool) -> None:
        if visible:
            # after=BCV en un pack(side="right"): queda a la IZQUIERDA de BCV.
            self._tarjeta_propia.pack(
                side="right", padx=(8, 0), after=self._tarjetas["BCV"]
            )
        else:
            self._tarjeta_propia.pack_forget()

    def _on_editar_tasa_propia(self) -> None:
        actual = self._rate_display_service.personal_rate()
        texto = simpledialog.askstring(
            "Tasa propia",
            "Tasa propia (Bs. por USD):",
            initialvalue=str(actual).replace(".", ",") if actual else "",
            parent=self,
        )
        if texto is None:  # el usuario cerro el dialogo
            return
        try:
            tasa = parse_local_decimal(texto).quantize(Decimal("0.01"))
            self._rate_display_service.set_personal_rate(tasa)
        except InvalidOperation:
            messagebox.showerror(
                "Tasa propia", "Ingresa un número válido, ej: 1200,00", parent=self
            )
            return
        except ValueError as error:
            messagebox.showerror("Tasa propia", str(error), parent=self)
            return
        self._refrescar_tarjetas()
        # Las tablas que muestran precios a tasa propia se ven al dia ya.
        self._recargar_pestana_visible()

    def _refrescar_tarjetas(self) -> None:
        """Relee de settings las tasas y el IVA (5 consultas chicas)."""
        for tipo, valor_var, fecha_var in (
            ("usd_oficial", self._tasa_bcv_var, self._fecha_bcv_var),
            ("usdt", self._tasa_usdt_var, self._fecha_usdt_var),
            ("eur_oficial", self._tasa_bcv_eur_var, self._fecha_eur_var),
        ):
            valor, fecha = self._exchange_rate_service.rate_parts(tipo)
            valor_var.set(valor)
            fecha_var.set(fecha)
        self._iva_var.set(self._iva_display())
        propia = self._rate_display_service.personal_rate()
        self._tasa_propia_var.set(
            str(propia).replace(".", ",") if propia else "sin cargar"
        )

    def _actualizar_tasas_si_hace_falta(self) -> None:
        if not (
            self._exchange_rate_service.should_refresh("usd_oficial")
            or self._exchange_rate_service.should_refresh("eur_oficial")
            or self._exchange_rate_service.should_refresh("usdt")
        ):
            return

        def _en_hilo_aparte() -> None:
            # Ninguna fetch_*_now() toca sqlite3 a proposito (ver
            # exchange_rate_service.py): por eso es seguro correrlas
            # fuera del hilo principal sin violar la regla de sqlite3
            # de "una conexion, un solo hilo". La escritura a settings
            # se hace de vuelta en el hilo principal, via after().
            oficial = fetch_rate_now()
            euro = fetch_eur_oficial_now()
            usdt = fetch_usdt_now()
            self.after(0, lambda: self._aplicar_tasas(oficial, euro, usdt))

        threading.Thread(target=_en_hilo_aparte, daemon=True).start()

    def _aplicar_tasas(
        self,
        oficial: tuple[str, str | None] | None,
        euro: tuple[str, str | None] | None,
        usdt: tuple[str, str | None] | None = None,
    ) -> None:
        # Cada fetch_*_now() devuelve (tasa, fecha_publicacion) o None
        # si fallo (ver exchange_rate_service.py) - la fecha se guarda
        # aparte para mostrar cuando la fuente publico de verdad, no
        # cuando esta app pregunto (rate_display()).
        if oficial is not None:
            tasa, publicado_en = oficial
            self._exchange_rate_service.apply_fetched_rate(
                tasa, "usd_oficial", published_at=publicado_en
            )
        if euro is not None:
            tasa, publicado_en = euro
            self._exchange_rate_service.apply_fetched_rate(
                tasa, "eur_oficial", published_at=publicado_en
            )
        if usdt is not None:
            tasa, publicado_en = usdt
            self._exchange_rate_service.apply_fetched_rate(
                tasa, "usdt", published_at=publicado_en
            )
        self._refrescar_tarjetas()

    def _on_refrescar_tasas(self) -> None:
        """
        Refresco manual: a diferencia de _actualizar_tasas_si_hace_falta
        (que respeta la cache de un dia), este siempre pega a la red -
        el usuario lo pidio a proposito. Un solo boton para las 3 tasas:
        se deshabilita mientras dura la consulta para no lanzar varios
        hilos en paralelo.
        """
        self._refrescar_tasas_button.state(["disabled"])

        def _en_hilo_aparte() -> None:
            oficial = fetch_rate_now()
            euro = fetch_eur_oficial_now()
            usdt = fetch_usdt_now()
            self.after(0, lambda: self._aplicar_tasas_manual(oficial, euro, usdt))

        threading.Thread(target=_en_hilo_aparte, daemon=True).start()

    def _aplicar_tasas_manual(
        self,
        oficial: tuple[str, str | None] | None,
        euro: tuple[str, str | None] | None,
        usdt: tuple[str, str | None] | None = None,
    ) -> None:
        self._aplicar_tasas(oficial, euro, usdt)
        self._refrescar_tasas_button.state(["!disabled"])
        if oficial is None and euro is None and usdt is None:
            messagebox.showwarning(
                "Tasas de cambio",
                "No se pudo consultar ninguna tasa. Verifica tu conexion e intenta de nuevo.",
                parent=self,
            )

    def _on_editar_tasa_bcv(self) -> None:
        """
        Carga manual para cuando la consulta automatica no esta
        disponible (API caida, sin red). Reutiliza apply_fetched_rate:
        pisa la cache y marca el dia como ya consultado, asi no hace
        falta una columna ni un metodo nuevo para distinguir el origen.
        """
        actual = self._exchange_rate_service.current_rate().replace(".", ",")
        texto = simpledialog.askstring(
            "Tasa BCV",
            "Tasa BCV (Bs. por USD):",
            initialvalue=actual,
            parent=self,
        )
        if texto is None:  # el usuario cerro el dialogo
            return

        try:
            tasa = parse_local_decimal(texto).quantize(Decimal("0.01"))
        except InvalidOperation:
            messagebox.showerror(
                "Tasa BCV", "Ingresa un número válido, ej: 827,74", parent=self
            )
            return
        if tasa <= 0:
            messagebox.showerror(
                "Tasa BCV", "La tasa debe ser mayor que cero.", parent=self
            )
            return

        self._exchange_rate_service.apply_fetched_rate(str(tasa))
        self._refrescar_tarjetas()

    def _on_editar_tasa_eur(self) -> None:
        actual = self._exchange_rate_service.current_rate("eur_oficial").replace(
            ".", ","
        )
        texto = simpledialog.askstring(
            "Tasa Euro",
            "Tasa Euro (Bs. por EUR):",
            initialvalue=actual,
            parent=self,
        )
        if texto is None:  # el usuario cerro el dialogo
            return

        try:
            tasa = parse_local_decimal(texto).quantize(Decimal("0.01"))
        except InvalidOperation:
            messagebox.showerror(
                "Tasa Euro", "Ingresa un número válido, ej: 963,21", parent=self
            )
            return
        if tasa <= 0:
            messagebox.showerror(
                "Tasa Euro", "La tasa debe ser mayor que cero.", parent=self
            )
            return

        self._exchange_rate_service.apply_fetched_rate(str(tasa), "eur_oficial")
        self._refrescar_tarjetas()

    def _on_editar_tasa_usdt(self) -> None:
        actual = self._exchange_rate_service.current_rate("usdt").replace(".", ",")
        texto = simpledialog.askstring(
            "Tasa USDT",
            "Tasa USDT (Bs. por USDT):",
            initialvalue=actual,
            parent=self,
        )
        if texto is None:  # el usuario cerro el dialogo
            return

        try:
            tasa = parse_local_decimal(texto).quantize(Decimal("0.01"))
        except InvalidOperation:
            messagebox.showerror(
                "Tasa USDT", "Ingresa un número válido, ej: 943,50", parent=self
            )
            return
        if tasa <= 0:
            messagebox.showerror(
                "Tasa USDT", "La tasa debe ser mayor que cero.", parent=self
            )
            return

        self._exchange_rate_service.apply_fetched_rate(str(tasa), "usdt")
        self._refrescar_tarjetas()

    def _aplicar_tema(self, modo: str) -> None:
        """Callback de Configuracion > Apariencia (el interruptor)."""
        self._theme.apply(modo)
        self._settings_service.set_ui_theme(self._theme.mode)

    def _cambiar_tamano_letra(self, direction: int) -> str:
        actual = self._theme.font_scale
        indice = FONT_SCALES.index(actual)
        nuevo = FONT_SCALES[max(0, min(len(FONT_SCALES) - 1, indice + direction))]
        if nuevo != actual:
            self._theme.set_font_scale(nuevo)
            self._settings_service.set_font_scale(nuevo)
        return "break"

    def _aplicar_tamano_letra(self, scale: int) -> None:
        """Callback de Configuracion > Apariencia."""
        self._theme.set_font_scale(scale)

    def _on_atajo_tamano(self, evento: tk.Event) -> str | None:
        direction = font_shortcut_direction(evento.keysym)
        return self._cambiar_tamano_letra(direction) if direction is not None else None

    def _iva_display(self) -> str:
        tasa = self._billing_service.default_tax_rate()
        return f"{str(tasa).replace('.', ',')} %"

    def _on_editar_iva(self) -> None:
        actual = str(self._billing_service.default_tax_rate()).replace(".", ",")
        texto = simpledialog.askstring(
            "IVA",
            "Tasa de IVA (%):",
            initialvalue=actual,
            parent=self,
        )
        if texto is None:  # el usuario cerro el dialogo
            return

        try:
            tasa = parse_local_decimal(texto).quantize(Decimal("0.01"))
        except InvalidOperation:
            messagebox.showerror(
                "IVA", "Ingresa un número válido, ej: 16,00", parent=self
            )
            return

        try:
            self._billing_service.set_default_tax_rate(tasa)
        except ValueError as error:
            messagebox.showerror("IVA", str(error), parent=self)
            return

        self._refrescar_tarjetas()

    def _build_paginas(
        self,
        *,
        product_service: ProductService,
        customer_service: CustomerService,
        supplier_service: SupplierService,
        billing_service: BillingService,
        stock_service: StockService,
        quotation_service: QuotationService,
    ) -> None:
        # ProductService no tiene acceso a settings (a diferencia de
        # BillingService, que ya lo usa para company_name/etc.), asi que
        # el simbolo se lee una vez aca y se reparte - ver
        # BillingService.currency_symbol().
        currency_symbol = billing_service.currency_symbol()
        # Para titular las columnas de precio con el nombre real del
        # nivel ('Precio Detal') en Configuracion > Columnas.
        self._price_tiers = product_service.list_price_tiers()

        inicio = HomePage(
            self._contenido,
            billing_service=billing_service,
            product_service=product_service,
            backup_service=self._backup_service,
            on_nueva_factura=lambda: self._billing_page._on_nueva(),
            on_ir_a=lambda titulo: self._mostrar_pagina(self._pagina(titulo)),
            on_respaldar=self._on_respaldar,
            on_ver_respaldos=lambda: self._abrir_configuracion(
                "Respaldo y exportación"
            ),
            stock_service=stock_service,
        )
        self._home_page = inicio

        productos = ProductsPage(
            self._contenido,
            product_service,
            currency_symbol=currency_symbol,
            billing_service=billing_service,
            customer_service=customer_service,
            settings_service=self._settings_service,
            rate_display_service=self._rate_display_service,
            preview_service=self._preview_service,
            supplier_service=supplier_service,
            stock_service=stock_service,
        )
        clientes = CustomersPage(
            self._contenido,
            customer_service,
            billing_service=billing_service,
            settings_service=self._settings_service,
            rate_display_service=self._rate_display_service,
        )
        proveedores = SuppliersPage(
            self._contenido,
            supplier_service,
            product_service=product_service,
            settings_service=self._settings_service,
        )
        self._billing_page = BillingPage(
            self._contenido,
            billing_service,
            product_service,
            customer_service,
            settings_service=self._settings_service,
            rate_display_service=self._rate_display_service,
            preview_service=self._preview_service,
        )
        # Cotizaciones existe siempre, pero solo se muestra (al lado de
        # Facturas) con Modo avanzado activo - ver
        # _actualizar_pestana_cotizaciones.
        self._quotations_page = QuotationsPage(
            self._contenido,
            quotation_service,
            product_service,
            customer_service,
            settings_service=self._settings_service,
            on_personal_rate_changed=self._refrescar_tarjetas,
            preview_service=self._preview_service,
        )
        # "Ajustes" queda reservado para la pantalla de Configuracion:
        # esto es ajuste de INVENTARIO, nombre distinto a proposito.
        ajuste = StockAdjustmentPage(self._contenido, stock_service, product_service)
        self._config_page = ConfigPage(
            self._contenido,
            backup_service=self._backup_service,
            export_service=self._export_service,
            settings_service=self._settings_service,
            on_advanced_mode_changed=self._aplicar_modo_avanzado,
            on_columns_changed=self._on_columnas_cambiadas,
            on_store_info_changed=self._on_datos_de_tienda_cambiados,
            on_font_scale_changed=self._aplicar_tamano_letra,
            on_theme_changed=self._aplicar_tema,
            on_backup_done=self._refrescar_estado_respaldo,
            tier_names={t.position: t.name for t in self._price_tiers},
        )

        # Todas las paginas por titulo. El ORDEN de la barra lo dan
        # _GRUPOS; Cotizaciones va siempre en la lista y
        # _actualizar_pestana_cotizaciones decide si su boton se ve.
        self._paginas: list[tuple[str, tk.Misc]] = [
            ("Inicio", inicio),
            ("Facturas", self._billing_page),
            ("Cotizaciones", self._quotations_page),
            ("Clientes", clientes),
            ("Productos", productos),
            ("Ajuste de stock", ajuste),
            ("Proveedores", proveedores),
            (_CONFIGURACION, self._config_page),
        ]
        # Con texto (barra abierta) y de solo icono (plegada): ver _build_layout.
        self._nav_buttons: dict[str, ctk.CTkButton] = {}
        self._nav_iconos: dict[str, ctk.CTkButton] = {}
        for titulo, pagina in self._paginas:
            contenedor = self._pie if titulo == _CONFIGURACION else self._nav
            comando = lambda p=pagina: self._mostrar_pagina(p)
            self._nav_buttons[titulo] = self._boton_lateral(contenedor, titulo, comando)
            self._nav_iconos[titulo] = self._boton_lateral(
                contenedor, titulo, comando, solo_icono=True
            )
        configuracion = self._nav_buttons[_CONFIGURACION]
        configuracion.pack(**configuracion._flex_pack)
        self._titulos_de_grupo = {
            grupo: self._titulo_de_grupo(grupo) for grupo, _ in _GRUPOS if grupo
        }
        self._separadores = {
            grupo: ctk.CTkFrame(
                self._nav,
                width=_ANCHO_BOTON_PLEGADO,
                height=1,
                fg_color=pair("sidebar_hover_bg"),
                corner_radius=0,
            )
            for grupo, _ in _GRUPOS
            if grupo
        }
        self._pagina_actual: tk.Misc | None = None
        self._cotizaciones_visibles = False
        self._empacar_navegacion()
        # ThemeManager avisa a todo widget con este atributo cuando cambia
        # el tamano de letra (ver theme._avisar_cambio_de_fuente).
        self._sidebar.on_font_scale_changed = self._aplicar_sidebar
        self._aplicar_sidebar()

        # Una pagina por modulo de ui/columns.py, para poder re-armar su
        # tabla cuando Configuracion > Personalizacion cambia (ver
        # _on_columnas_cambiadas).
        self._paginas_por_modulo = {
            columns.PRODUCTS: productos,
            columns.CUSTOMERS: clientes,
            columns.SUPPLIERS: proveedores,
            columns.INVOICES: self._billing_page,
            columns.QUOTATIONS: self._quotations_page,
        }
        self._aplicar_modo_avanzado(
            self._settings_service.advanced_mode(), rearmar_columnas=False
        )
        self._refrescar_estado_respaldo()
        self._mostrar_pagina(inicio)

    def _ajustar_ancho_sidebar(self) -> None:
        """
        Ancho fijo (la barra no cambia de tamano al cambiar de pagina),
        pero medido con la letra actual: con un numero fijo, a 150 %
        "Proveedores" o "Cotizaciones" salian cortados. Tambien el alto del
        encabezado de grupo, para que el separador de la barra plegada
        ocupe lo mismo y los iconos no suban ni bajen al plegar.
        """
        fuente = ctk_font(self, "body")
        textos = [f"  {b._flex_titulo}" for b in self._nav_buttons.values()]
        # icono (20) + 2 x 10 de border_spacing del boton + margen de la
        # barra a los dos lados + un poco de aire.
        ancho = (
            max(fuente.measure(t) for t in textos) + 20 + 20 + 2 * _MARGEN_BARRA + 24
        )
        self._ancho_abierto = max(256, ancho)
        alto = ctk_font(self, "small_bold").metrics("linespace")
        if alto != self._alto_encabezado:
            self._alto_encabezado = alto
            self._empacar_navegacion()
        alto_boton = self._alto_boton_lateral()
        for boton in (*self._nav_buttons.values(), *self._nav_iconos.values()):
            if int(boton.cget("height")) != alto_boton:
                boton.configure(height=alto_boton)
        self._sidebar.configure(width=self._ancho_objetivo_sidebar())

    def _opciones_de_encabezado(self, grupo: str) -> tuple[tk.Misc, dict]:
        """El widget que va sobre un grupo segun lo dibujado, y como se empaca."""
        if not self._sidebar_en_iconos:
            return self._titulos_de_grupo[grupo], {
                "fill": "x",
                "padx": 10,  # alineado con el icono de los botones
                "pady": (14, 4),
            }
        # Un respiro entre grupos en vez del titulo: 1 px de linea y el
        # resto de aire hasta sumar el mismo alto que el titulo.
        resto = 14 + 4 + self._alto_encabezado - 1
        return self._separadores[grupo], {
            "fill": "none",
            "anchor": "w",
            "pady": (resto // 2, resto - resto // 2),
        }

    def _empacar_navegacion(self) -> None:
        """Titulo de cada grupo y sus botones visibles, en el orden de _GRUPOS."""
        for hijo in self._nav.winfo_children():
            hijo.pack_forget()
        visibles = set(self._titulos_visibles())
        for grupo, titulos in _GRUPOS:
            de_este_grupo = [t for t in titulos if t in visibles]
            if not de_este_grupo:
                continue
            if grupo:
                encabezado, opciones = self._opciones_de_encabezado(grupo)
                encabezado.pack(side="top", **opciones)
            for titulo in de_este_grupo:
                boton = self._botones_a_la_vista()[titulo]
                boton.pack(**boton._flex_pack)
                self._sincronizar_activo(boton)

    def _cambiar_encabezados_de_grupo(self) -> None:
        """
        Titulo <-> separador sin tocar los botones: solo se cambia el
        encabezado de cada grupo, empacado justo antes de su primer boton
        (volver a empacar toda la barra la dejaba en blanco un instante).
        """
        visibles = set(self._titulos_visibles())
        for grupo, titulos in _GRUPOS:
            primero = next((t for t in titulos if t in visibles), None)
            if not grupo or primero is None:
                continue
            self._titulos_de_grupo[grupo].pack_forget()
            self._separadores[grupo].pack_forget()
            encabezado, opciones = self._opciones_de_encabezado(grupo)
            encabezado.pack(
                side="top", before=self._botones_a_la_vista()[primero], **opciones
            )

    def _titulos_visibles(self) -> list[str]:
        """Las paginas de la barra lateral, en el orden en que se ven (sin
        Cotizaciones si Modo avanzado esta apagado; Configuracion al final)."""
        en_grupos = [t for _grupo, titulos in _GRUPOS for t in titulos]
        return [
            titulo
            for titulo in (*en_grupos, _CONFIGURACION)
            if titulo != "Cotizaciones" or self._cotizaciones_visibles
        ]

    def _titulo_de(self, pagina: tk.Misc) -> str:
        return next(titulo for titulo, p in self._paginas if p is pagina)

    def _pagina(self, titulo: str) -> tk.Misc:
        return next(p for t, p in self._paginas if t == titulo)

    def _mostrar_pagina(self, pagina: tk.Misc) -> None:
        """
        Cambiar de pagina. Cada una carga su lista una sola vez al
        construirse (ver docstring de ProductsPage): sin recargarla al
        volver, ajustar stock o cargar un cliente/proveedor en una pagina
        no se veria en las demas hasta reiniciar la app. Recargar la
        visible es una consulta a SQLite (list_products() tarda ~110ms con
        las 1.936 filas del catalogo) - insignificante comparado con
        mostrar datos viejos.
        """
        if self._pagina_actual is not None and self._pagina_actual is not pagina:
            self._pagina_actual.pack_forget()
        self._pagina_actual = pagina
        pagina.pack(fill="both", expand=True)
        titulo = self._titulo_de(pagina)
        self._titulo_pagina_var.set(titulo)
        self._subtitulo_pagina_var.set(_PAGINAS[titulo][1])
        self._titulo_activo = titulo
        for boton in self._botones_a_la_vista().values():
            self._sincronizar_activo(boton)
        self._recargar_pestana_visible()
        # La tasa propia tambien se edita desde Cotizaciones: al cambiar de
        # pagina la tarjeta del encabezado queda al dia.
        self._refrescar_tarjetas()

    def _on_columnas_cambiadas(self, modulo: str) -> None:
        self._paginas_por_modulo[modulo].apply_column_settings()

    def _on_datos_de_tienda_cambiados(self) -> None:
        """
        El nombre nuevo se ve en el acto en el membrete de Facturas y en
        la barra lateral, sin reiniciar. El PDF no necesita nada: lo lee
        de settings cada vez que genera uno (ver BillingService.company_*).
        """
        self._billing_page.apply_store_info()
        self._tienda_var.set(self._billing_service.company_name())
        self._refrescar_marca()

    def _aplicar_modo_avanzado(
        self, activo: bool, *, rearmar_columnas: bool = True
    ) -> None:
        """
        Todo lo que depende de la tasa propia aparece o desaparece junto:
        la pagina Cotizaciones, la tarjeta "Tasa propia" del encabezado y
        las columnas "(tasa propia)" de Productos y Facturas.

        'rearmar_columnas' es False al arrancar: las paginas ya nacieron
        leyendo ese mismo ajuste, y rearmarles la tabla era volver a
        cargar las 1.900 filas de Productos para dejarlas igual.
        """
        self._actualizar_pestana_cotizaciones(activo)
        self._mostrar_tarjeta_propia(activo)
        if rearmar_columnas:
            for modulo in (columns.PRODUCTS, columns.INVOICES):
                self._paginas_por_modulo[modulo].apply_column_settings()

    def _recargar_pestana_visible(self) -> None:
        if self._pagina_actual is not None:
            self._pagina_actual.reload()

    def _actualizar_pestana_cotizaciones(self, visible: bool) -> None:
        if visible == self._cotizaciones_visibles:
            return
        self._cotizaciones_visibles = visible
        self._empacar_navegacion()
        if not visible and self._pagina_actual is self._quotations_page:
            self._mostrar_pagina(self._billing_page)

    # ------------------------------------------------------------
    # respaldo
    # ------------------------------------------------------------
    def _on_respaldar(self) -> None:
        """Respaldar ahora desde Inicio (Configuracion tiene el suyo)."""
        try:
            ruta = self._backup_service.backup_now()
        except OSError as error:
            messagebox.showerror(
                "Respaldar ahora", f"No se pudo respaldar: {error}", parent=self
            )
            return
        toast(self, f"Respaldo creado: {ruta.name}")
        self._refrescar_estado_respaldo()

    def _refrescar_estado_respaldo(self) -> None:
        """
        El pie de la barra lateral: cuando fue el ultimo respaldo, en rojo
        si nunca se hizo o si pasaron DIAS_SIN_RESPALDO_ALERTA dias. Mira
        la carpeta de respaldos (sin tocar la base).
        """
        ultimo = self._backup_service.last_backup_at()
        if ultimo is None:
            texto, alerta = "Sin respaldos todavía", True
            texto_barra = f"  {texto}"
        else:
            dias = (datetime.now() - ultimo).days  # noqa: DTZ005
            cuando = (
                "hoy" if dias == 0 else ("ayer" if dias == 1 else f"hace {dias} días")
            )
            texto = f"Último respaldo: {cuando}"
            texto_barra = f"  Último respaldo\n  {cuando}"
            alerta = dias >= DIAS_SIN_RESPALDO_ALERTA
        color = pair("danger_bg") if alerta else pair("sidebar_muted_fg")
        self._texto_respaldo = texto
        self._texto_respaldo_barra = texto_barra
        imagen = icons.icon(
            self, "triangle-alert" if alerta else "database-backup", color, size=18
        )
        for boton, texto_boton in (
            (self._estado_respaldo, texto_barra),
            (self._estado_respaldo_icono, ""),
        ):
            boton.configure(
                text=texto_boton,
                text_color=color,
                border_color=pair("danger_bg") if alerta else pair("sidebar_hover_bg"),
                image=imagen,
                compound="left",
            )
        self._ajustar_alto_respaldo()
        if hasattr(self, "_home_page"):
            self._home_page.refrescar_respaldo()

    # ------------------------------------------------------------
    # Configuracion: la ultima pagina de la barra
    # ------------------------------------------------------------
    def _abrir_configuracion(self, seccion: str | None = None) -> None:
        self._mostrar_pagina(self._config_page)
        if seccion is not None:
            titulos = self._config_page.section_titles()
            if seccion in titulos:
                self._config_page.mostrar_seccion(titulos.index(seccion))
