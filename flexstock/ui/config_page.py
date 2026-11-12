"""
Pagina de Configuracion: la ultima entrada de la barra lateral de
MainWindow. Antes era un dialogo aparte que abria la tuerquita del
encabezado; como pagina queda a la vista igual que Productos o Facturas,
sin una ventana encima, y junta lo que antes estaba repartido entre el
menu "Archivo", el boton de tema y el dialogo: respaldar, modo
claro/oscuro, tamano de letra y los datos de la tienda.

Menu de secciones a la izquierda y el contenido de la elegida a la
derecha. Agregar una seccion nueva es sumar una tupla (titulo,
constructor) en _secciones(), nada mas.

Secciones:
- "Tienda": el nombre, RIF, direccion, telefono y logo que salen
  impresos en el encabezado de cada factura. Es lo primero de la lista
  porque es lo que hay que llenar antes de emitir la primera: sin esto
  la factura sale a nombre de "Ferreteria" y sin RIF.
- "Respaldo y exportacion": BackupPanel completo (ver backup_dialog.py).
- "Apariencia": modo claro/oscuro y tamano de letra.
- "Columnas": que columnas muestra cada listado (ver ui/columns.py).
- "Modo avanzado": un solo interruptor para lo que depende de la tasa
  propia (la pagina Cotizaciones y las columnas "(tasa propia)").
"""

import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox

import customtkinter as ctk

from ..services.backup_service import BackupService
from ..services.export_service import ExportService
from ..services.settings_service import FONT_SCALES, SettingsService, StoreInfo
from . import columns, kit
from .backup_dialog import BackupPanel
from .rif import TIPOS_RIF, armar_rif, partir_rif
from .theme import ctk_font, paint_text, pair
from .widgets import ScrollableDialogBody, tab_pasa_al_siguiente, wrap_to_width

_TIPOS_RIF = TIPOS_RIF
# Para un negocio lo normal es J (empresa); V/E si el RIF es de una persona.
_TIPO_RIF_POR_DEFECTO = "J"
_partir_rif = partir_rif
_armar_rif = armar_rif


def _mostrar_imagen(
    etiqueta: kit.Label,
    crudo: bytes | None,
    tope: tuple[int, int],
    *,
    vacio: str,
    ilegible: str,
) -> "ctk.CTkImage | None":
    """
    Pone en 'etiqueta' la imagen guardada (logo, QR), achicada a 'tope'.
    Devuelve la CTkImage para que quien llama guarde la referencia: Tk
    descarta una imagen sin referencias vivas y la vista quedaria en blanco.

    Pasa por Pillow (viene con ReportLab): abre cualquier formato que lea
    Pillow, no solo PNG/GIF como tk.PhotoImage, y CTk la escala bien en
    pantallas de alta densidad. Una imagen ilegible no rompe Configuracion.
    Se muestra sobre fondo blanco, que es como se imprime.
    """
    if not crudo:
        etiqueta.configure(image="", text=vacio)
        return None
    try:
        import io

        from PIL import Image

        with Image.open(io.BytesIO(crudo)) as imagen:
            rgba = imagen.convert("RGBA")
        # Sobre blanco, como sale en el papel: un QR sin fondo (cuadritos
        # negros sobre transparente) en modo oscuro quedaba negro sobre
        # negro, invisible.
        chica = Image.new("RGB", rgba.size, "white")
        chica.paste(rgba, mask=rgba.getchannel("A"))
        chica.thumbnail(tope)
        foto = ctk.CTkImage(light_image=chica, size=chica.size)
        etiqueta.configure(image=foto, text="")
        return foto
    except Exception:  # noqa: BLE001
        etiqueta.configure(image="", text=ilegible)
        return None


class ConfigPage(kit.Frame):
    def __init__(
        self,
        master: tk.Misc,
        *,
        backup_service: BackupService,
        export_service: ExportService,
        settings_service: SettingsService,
        on_advanced_mode_changed: Callable[[bool], None] | None = None,
        on_columns_changed: Callable[[str], None] | None = None,
        on_store_info_changed: Callable[[], None] | None = None,
        on_font_scale_changed: Callable[[int], None] | None = None,
        on_theme_changed: Callable[[str], None] | None = None,
        on_backup_done: Callable[[], None] | None = None,
        tier_names: dict[int, str] | None = None,
    ) -> None:
        super().__init__(master)
        self._backup_service = backup_service
        self._export_service = export_service
        self._settings_service = settings_service
        self._on_advanced_mode_changed = on_advanced_mode_changed
        self._on_columns_changed = on_columns_changed
        # Avisa a MainWindow para que repinte su membrete con el nombre
        # nuevo sin tener que reiniciar la aplicacion.
        self._on_store_info_changed = on_store_info_changed
        self._on_font_scale_changed = on_font_scale_changed
        self._on_theme_changed = on_theme_changed
        self._on_backup_done = on_backup_done
        self._tier_names = tier_names
        self._advanced_mode_var = tk.BooleanVar(value=settings_service.advanced_mode())
        self._paneles: list[kit.Frame] = []

        self._build_form()

    def reload(self) -> None:
        """
        Al volver a la pagina: lo que pudo cambiar desde afuera (Ctrl+/-
        cambia la letra desde cualquier pantalla, el modo oscuro se
        guarda en settings) se ve al dia. Son lecturas de settings, sin
        tocar las tablas.
        """
        self._actualizar_texto_tamano()
        self._modo_oscuro_var.set(self._settings_service.ui_theme() == "dark")

    # ------------------------------------------------------------
    def _secciones(self) -> list[tuple[str, Callable[[kit.Frame], None]]]:
        # "Tienda" va primera a proposito: es lo que hay que llenar
        # ANTES de emitir la primera factura (sin esto sale a nombre de
        # "Ferreteria" y sin RIF), asi que es lo que tiene que ver
        # alguien que abre Configuracion por primera vez.
        return [
            ("Tienda", self._build_tienda),
            ("Respaldo y exportación", self._build_respaldo),
            ("Apariencia", self._build_apariencia),
            ("Inventario", self._build_inventario),
            ("Columnas", self._build_columnas),
            ("Modo avanzado", self._build_modo_avanzado),
        ]

    def _build_form(self) -> None:
        secciones = self._secciones()
        # Menu lateral: una tarjeta con un boton por seccion, el elegido
        # resaltado (mismo criterio que la barra lateral de la ventana
        # principal, en version clara).
        self._menu = kit.Frame(self, style="Card.TFrame", padding=8)
        self._menu.pack(side="left", fill="y", padx=(0, 12))
        self._menu_botones: list[ctk.CTkButton] = []

        # Desplazable: con la letra al 150 % la seccion mas alta pide mas
        # alto que la pantalla; sin scroll, su final quedaba afuera.
        self._scroll = ScrollableDialogBody(self)
        self._scroll.pack(side="left", fill="both", expand=True)
        self._contenido = self._scroll.content

        for indice, (titulo, construir) in enumerate(secciones):
            boton = ctk.CTkButton(
                self._menu,
                text=titulo,
                anchor="w",
                width=220,
                height=40,
                corner_radius=8,
                font=ctk_font(self, "body"),
                command=lambda i=indice: self.mostrar_seccion(i),
            )
            boton.pack(side="top", fill="x", pady=2)
            self._menu_botones.append(boton)
            panel = kit.Frame(self._contenido)
            construir(panel)
            self._paneles.append(panel)

        self._seccion_actual = 0
        self.mostrar_seccion(0)

    def section_titles(self) -> list[str]:
        return [boton.cget("text") for boton in self._menu_botones]

    def mostrar_seccion(self, indice: int) -> None:
        for panel in self._paneles:
            panel.pack_forget()
        self._paneles[indice].pack(fill="both", expand=True)
        for i, boton in enumerate(self._menu_botones):
            if i == indice:
                boton.configure(
                    fg_color=pair("select_bg"),
                    hover_color=pair("select_bg"),
                    text_color=pair("select_fg"),
                )
            else:
                boton.configure(
                    fg_color="transparent",
                    hover_color=pair("button_bg"),
                    text_color=pair("fg"),
                )
        self._seccion_actual = indice

    # ------------------------------------------------------------
    # Tienda: lo que sale impreso en la factura
    # ------------------------------------------------------------
    def _build_tienda(self, parent: kit.Frame) -> None:
        """
        Los datos del emisor y el logo. Lo que se guarda aca es
        exactamente lo que imprime el PDF: no hay una segunda copia en
        ningun lado (ver SettingsService.store_info y
        BillingService.company_*).
        """
        interior = kit.Frame(parent, padding=16)
        interior.pack(fill="both", expand=True)

        kit.Label(interior, text="Datos de la tienda", style="Heading.TLabel").grid(
            row=0, column=0, columnspan=2, sticky="w"
        )
        # La pista del RIF va aca y no al lado del campo: a 150 % un texto
        # junto al selector se salia del panel; este se parte al ancho.
        subtitulo = kit.Label(
            interior,
            text="Es lo que sale en el encabezado de cada factura. En el RIF "
            "escribe los numeros con el digito verificador al final "
            "(ej. 12345678-9).",
            style="Muted.TLabel",
            wraplength=520,
            justify="left",
        )
        subtitulo.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(0, 12))
        wrap_to_width(subtitulo)

        info = self._settings_service.store_info()
        tipo_rif, numero_rif = _partir_rif(info.tax_id)
        self._tienda_vars = {
            "name": tk.StringVar(value=info.name),
            "legal_name": tk.StringVar(value=info.legal_name),
            "address": tk.StringVar(value=info.address),
            "phone": tk.StringVar(value=info.phone),
            "email": tk.StringVar(value=info.email),
            "web": tk.StringVar(value=info.web),
        }
        self._tienda_rif_tipo = tk.StringVar(value=tipo_rif)
        self._tienda_rif_numero = tk.StringVar(value=numero_rif)

        def etiqueta(fila: int, texto: str) -> None:
            kit.Label(interior, text=texto).grid(row=fila, column=0, sticky="w", pady=4)

        def campo(fila: int, clave: str, *, largo: int | None = None) -> None:
            kit.Entry(
                interior, textvariable=self._tienda_vars[clave], width=largo or 34
            ).grid(
                row=fila,
                column=1,
                sticky="w" if largo else "ew",
                pady=4,
                padx=(8, 0),
            )

        etiqueta(2, "Nombre del negocio:")
        campo(2, "name")
        etiqueta(3, "Razon social:")
        campo(3, "legal_name")

        # RIF = tipo (se elige de una lista, como la cedula de un cliente) +
        # numero. Se guarda unido: 'J-12345678-9'.
        etiqueta(4, "RIF:")
        rif = kit.Frame(interior)
        rif.grid(row=4, column=1, sticky="w", pady=4, padx=(8, 0))
        kit.Combobox(
            rif,
            state="readonly",
            width=6,
            textvariable=self._tienda_rif_tipo,
            values=_TIPOS_RIF,
        ).pack(side="left")
        kit.Entry(rif, textvariable=self._tienda_rif_numero, width=18).pack(
            side="left", padx=(6, 0)
        )

        etiqueta(5, "Domicilio fiscal:")
        campo(5, "address")
        etiqueta(6, "Teléfono:")
        campo(6, "phone", largo=22)
        etiqueta(7, "Correo:")
        campo(7, "email")
        etiqueta(8, "Web / redes:")
        campo(8, "web")
        interior.columnconfigure(1, weight=1)

        # Textos libres de varios renglones que salen AL PIE de facturas,
        # notas de entrega y cotizaciones.
        pie = kit.LabelFrame(
            interior, text="Al pie de facturas y notas de entrega", padding=10
        )
        pie.grid(row=9, column=0, columnspan=2, sticky="ew", pady=(14, 0))
        pie.columnconfigure(0, weight=1)
        self._tienda_textos: dict[str, tk.Text] = {}
        for numero, (clave, titulo, ayuda, valor) in enumerate(
            (
                (
                    "payment_info",
                    "Datos de pago",
                    "Pago movil, cuenta, Zelle...",
                    info.payment_info,
                ),
                (
                    "footer_text",
                    "Texto al pie",
                    "Garantía, condiciones de devolución...",
                    info.footer_text,
                ),
            )
        ):
            base = numero * 4  # titulo, ayuda, texto y (en pago) el QR
            kit.Label(pie, text=titulo, style="Bold.TLabel").grid(
                row=base, column=0, sticky="w", pady=(0 if numero == 0 else 10, 0)
            )
            kit.Label(pie, text=ayuda, style="Muted.TLabel").grid(
                row=base + 1, column=0, sticky="w"
            )
            texto = tk.Text(pie, height=3, wrap="word", width=40)
            tab_pasa_al_siguiente(texto)
            paint_text(texto)
            texto.grid(row=base + 2, column=0, sticky="ew", pady=(4, 0))
            if valor:
                texto.insert("1.0", valor)
            self._tienda_textos[clave] = texto
            if clave == "payment_info":
                self._build_qr_de_pago(pie, row=base + 3)

        fila = 9  # ultima fila usada; el logo va debajo

        # ---- logo ----
        logo_frame = kit.LabelFrame(interior, text="Logo", padding=10)
        logo_frame.grid(row=fila + 1, column=0, columnspan=2, sticky="ew", pady=(16, 0))

        # La vista previa es un Label con imagen; se guarda la referencia
        # en self porque Tk descarta un PhotoImage sin referencias vivas
        # y el logo desapareceria apenas pase el recolector.
        self._logo_preview = kit.Label(logo_frame)
        self._logo_preview.pack(anchor="w")
        self._logo_foto: ctk.CTkImage | None = None

        botones = kit.Frame(logo_frame)
        botones.pack(anchor="w", pady=(8, 0))
        kit.Button(botones, text="Elegir imagen...", command=self._on_elegir_logo).pack(
            side="left", padx=(0, 6)
        )
        kit.Button(botones, text="Quitar", command=self._on_quitar_logo).pack(
            side="left"
        )
        kit.Label(
            logo_frame,
            text="PNG o JPG. Se guarda dentro de la base, así el respaldo\n"
            "se lo lleva y no depende de dónde esté el archivo original.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        self._refrescar_logo()

        self._tienda_estado_var = tk.StringVar()
        self._tienda_estado = kit.Label(
            interior, textvariable=self._tienda_estado_var, style="Ok.TLabel"
        )
        self._tienda_estado.grid(row=fila + 3, column=0, columnspan=2, sticky="w")

        kit.Button(
            interior,
            text="Guardar datos",
            command=self._on_guardar_tienda,
            style="Success.TButton",
            padding=(16, 8),
        ).grid(row=fila + 2, column=0, columnspan=2, sticky="w", pady=(16, 6))

    def _refrescar_logo(self) -> None:
        self._logo_foto = _mostrar_imagen(
            self._logo_preview,
            self._settings_service.logo(),
            (180, 100),
            vacio="(sin logo)",
            ilegible="(no se pudo mostrar el logo guardado)",
        )

    # ---- QR de pago ----
    def _build_qr_de_pago(self, parent: kit.Frame, *, row: int) -> None:
        """
        Debajo de "Datos de pago": la imagen del QR (o una foto donde se
        vea). Sale en el recuadro DATOS DE PAGO de facturas, notas de
        entrega y vistas previas, para pagar escaneandolo con el telefono.
        """
        marco = kit.Frame(parent)
        marco.grid(row=row, column=0, sticky="ew", pady=(8, 0))
        self._qr_preview = kit.Label(marco)
        self._qr_preview.pack(side="left", anchor="n")
        self._qr_foto: ctk.CTkImage | None = None

        lado = kit.Frame(marco)
        lado.pack(side="left", anchor="n", padx=(12, 0))
        kit.Label(lado, text="QR de pago (opcional)", style="Bold.TLabel").pack(
            anchor="w"
        )
        ayuda = kit.Label(
            lado,
            text="Sale junto a los datos de pago en la factura, para pagar "
            "escaneándolo con el teléfono. Mejor el QR solo, sin fondo; una "
            "foto donde se vea nítido también sirve.",
            style="Muted.TLabel",
            justify="left",
        )
        ayuda.pack(anchor="w", fill="x")
        wrap_to_width(ayuda, max_width=420)
        botones = kit.Frame(lado)
        botones.pack(anchor="w", pady=(6, 0))
        kit.Button(botones, text="Elegir imagen...", command=self._on_elegir_qr).pack(
            side="left", padx=(0, 6)
        )
        self._btn_quitar_qr = kit.Button(
            botones, text="Quitar", command=self._on_quitar_qr
        )
        self._btn_quitar_qr.pack(side="left")
        self._refrescar_qr()

    def _refrescar_qr(self) -> None:
        crudo = self._settings_service.payment_qr()
        self._qr_foto = _mostrar_imagen(
            self._qr_preview,
            crudo,
            (110, 110),
            vacio="(sin QR)",
            ilegible="(no se pudo mostrar el QR guardado)",
        )
        self._btn_quitar_qr.configure(state="normal" if crudo else "disabled")

    def _on_elegir_qr(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Elegir la imagen del QR de pago",
            parent=self,
            filetypes=[
                ("Imagenes", "*.png *.jpg *.jpeg *.gif *.bmp *.webp"),
                ("Todos", "*.*"),
            ],
        )
        if not ruta:
            return
        try:
            self._settings_service.set_payment_qr(Path(ruta))
        except ValueError as error:
            messagebox.showerror("QR de pago", str(error), parent=self)
            return
        self._refrescar_qr()
        self._avisar_tienda(
            "QR guardado. Sale junto a los datos de pago en las facturas.", ok=True
        )

    def _on_quitar_qr(self) -> None:
        self._settings_service.clear_payment_qr()
        self._refrescar_qr()
        self._avisar_tienda("QR quitado.", ok=True)

    def _on_elegir_logo(self) -> None:
        ruta = filedialog.askopenfilename(
            title="Elegir el logo de la tienda",
            parent=self,
            filetypes=[
                ("Imagenes", "*.png *.jpg *.jpeg *.gif *.bmp"),
                ("Todos", "*.*"),
            ],
        )
        if not ruta:
            return
        try:
            self._settings_service.set_logo(Path(ruta))
        except ValueError as error:
            messagebox.showerror("Logo", str(error), parent=self)
            return
        self._refrescar_logo()
        self._avisar_tienda("Logo guardado. Ya sale en las facturas nuevas.", ok=True)

    def _on_quitar_logo(self) -> None:
        self._settings_service.clear_logo()
        self._refrescar_logo()
        self._avisar_tienda("Logo quitado.", ok=True)

    def _avisar_tienda(self, mensaje: str, *, ok: bool) -> None:
        self._tienda_estado_var.set(mensaje)
        self._tienda_estado.configure(style="Ok.TLabel" if ok else "Error.TLabel")

    def _on_guardar_tienda(self) -> None:
        try:
            self._settings_service.set_store_info(
                StoreInfo(
                    name=self._tienda_vars["name"].get(),
                    tax_id=_armar_rif(
                        self._tienda_rif_tipo.get(), self._tienda_rif_numero.get()
                    ),
                    address=self._tienda_vars["address"].get(),
                    phone=self._tienda_vars["phone"].get(),
                    email=self._tienda_vars["email"].get(),
                    legal_name=self._tienda_vars["legal_name"].get(),
                    web=self._tienda_vars["web"].get(),
                    payment_info=self._tienda_textos["payment_info"].get("1.0", "end"),
                    footer_text=self._tienda_textos["footer_text"].get("1.0", "end"),
                )
            )
        except ValueError as error:
            self._avisar_tienda(str(error), ok=False)
            return
        # Las facturas YA emitidas no cambian: su PDF se vuelve a armar
        # con estos datos al reimprimir, pero los montos y el cliente son
        # snapshots de la fila (regla 5) y no los toca nada de aca.
        self._avisar_tienda(
            "Datos guardados. Salen en las facturas que generes de ahora en más.",
            ok=True,
        )
        if self._on_store_info_changed is not None:
            self._on_store_info_changed()

    # ------------------------------------------------------------
    def _build_respaldo(self, parent: kit.Frame) -> None:
        self._backup_panel = BackupPanel(
            parent,
            self._backup_service,
            self._export_service,
            settings_service=self._settings_service,
            on_backup_done=self._on_backup_done,
        )
        self._backup_panel.pack(fill="both", expand=True)

    def _build_apariencia(self, parent: kit.Frame) -> None:
        """Modo claro/oscuro y tamano de letra: como se VE la app."""
        interior = kit.Frame(parent, padding=16)
        interior.pack(fill="both", expand=True)

        modo = kit.LabelFrame(interior, text="Tema", padding=10)
        modo.pack(fill="x")
        self._modo_oscuro_var = tk.BooleanVar(
            value=self._settings_service.ui_theme() == "dark"
        )
        self._switch_modo_oscuro = kit.Switch(
            modo,
            text="Modo oscuro",
            variable=self._modo_oscuro_var,
            command=self._on_cambiar_modo,
        )
        self._switch_modo_oscuro.pack(anchor="w")
        kit.Label(
            modo,
            text="Fondo oscuro para trabajar de noche o con poca luz. Se recuerda "
            "al volver a abrir la aplicación.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(8, 0))

        tamano = kit.LabelFrame(interior, text="Tamano del texto", padding=10)
        tamano.pack(fill="x", pady=(12, 0))
        kit.Label(
            tamano,
            text=(
                "También puedes usar Ctrl + y Ctrl - desde cualquier pantalla "
                f"(máximo {FONT_SCALES[-1]} %)."
            ),
            style="Muted.TLabel",
        ).pack(anchor="w")
        controles_tamano = kit.Frame(tamano)
        controles_tamano.pack(anchor="w", pady=(8, 0))
        self._font_scale_var = tk.StringVar()
        kit.Button(
            controles_tamano,
            text="−",
            width=3,
            command=lambda: self._cambiar_tamano(-1),
        ).pack(side="left")
        kit.Label(
            controles_tamano,
            textvariable=self._font_scale_var,
            width=14,
            anchor="center",
        ).pack(side="left", padx=6)
        kit.Button(
            controles_tamano, text="+", width=3, command=lambda: self._cambiar_tamano(1)
        ).pack(side="left")
        kit.Button(
            controles_tamano, text="Restaurar", command=self._restaurar_tamano
        ).pack(side="left", padx=(10, 0))
        self._actualizar_texto_tamano()
        # Ctrl+/Ctrl- cambian la letra desde cualquier pantalla (ver
        # MainWindow): ThemeManager avisa a quien tenga este atributo, asi
        # el porcentaje de aca no queda viejo.
        controles_tamano.on_font_scale_changed = self._actualizar_texto_tamano

    def _build_inventario(self, parent: kit.Frame) -> None:
        interior = kit.Frame(parent, padding=16)
        interior.pack(fill="both", expand=True)

        seccion = kit.LabelFrame(interior, text="Productos nuevos", padding=10)
        seccion.pack(fill="x")
        self._existencia_inicial_var = tk.BooleanVar(
            value=self._settings_service.allow_initial_stock()
        )
        self._switch_existencia_inicial = kit.Switch(
            seccion,
            text="Permitir cargar existencia al crear un producto",
            variable=self._existencia_inicial_var,
            command=self._on_cambiar_existencia_inicial,
        )
        self._switch_existencia_inicial.pack(anchor="w")
        kit.Label(
            seccion,
            text=(
                "Prendido, Nuevo producto pide cuánto hay y quién lo contó, y lo\n"
                "guarda como carga inicial (se ve en el Historial del producto).\n"
                "Apagado, todo producto nuevo arranca en cero y se cuenta en\n"
                "Ajuste de stock. Editar un producto nunca cambia su existencia."
            ),
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def _on_cambiar_existencia_inicial(self) -> None:
        self._settings_service.set_allow_initial_stock(
            self._existencia_inicial_var.get()
        )

    def _on_cambiar_modo(self) -> None:
        modo = "dark" if self._modo_oscuro_var.get() else "light"
        if self._on_theme_changed is not None:
            self._on_theme_changed(modo)

    def _build_columnas(self, parent: kit.Frame) -> None:
        interior = kit.Frame(parent, padding=16)
        interior.pack(fill="both", expand=True)

        aviso = kit.Notice(
            interior,
            text=(
                "Elige qué columnas muestra cada listado. El orden es siempre "
                "el mismo: volver a marcar una columna la devuelve a su lugar."
            ),
            closable=True,
            key="columnas",
        )
        aviso.pack(fill="x")
        aviso.esconder_si_cerrado()

        fila = kit.Frame(interior)
        fila.pack(fill="x", pady=(16, 8))
        kit.Label(fila, text="Modulo:").pack(side="left")
        self._modulo_combo = kit.Combobox(
            fila,
            state="readonly",
            width=20,
            values=[columns.MODULO_LABELS[m] for m in self._modulos_disponibles()],
        )
        self._modulo_combo.current(0)
        self._modulo_combo.pack(side="left", padx=(4, 12))
        self._modulo_combo.bind(
            "<<ComboboxSelected>>", lambda _e: self._mostrar_columnas()
        )
        kit.Button(
            fila, text="Restaurar por defecto", command=self._on_restaurar_columnas
        ).pack(side="left")

        self._columnas_frame = kit.LabelFrame(interior, text="Columnas", padding=10)
        self._columnas_frame.pack(fill="both", expand=True)
        self._columna_vars: dict[str, tk.BooleanVar] = {}
        self._mostrar_columnas()

    def _actualizar_texto_tamano(self) -> None:
        scale = self._settings_service.font_scale()
        puntos = round(14 * scale / 100)
        self._font_scale_var.set(f"{scale} %  ({puntos} pt)")

    def _cambiar_tamano(self, direction: int) -> None:
        actual = self._settings_service.font_scale()
        indice = FONT_SCALES.index(actual)
        nuevo = FONT_SCALES[max(0, min(len(FONT_SCALES) - 1, indice + direction))]
        if nuevo == actual:
            return
        self._settings_service.set_font_scale(nuevo)
        self._actualizar_texto_tamano()
        if self._on_font_scale_changed is not None:
            self._on_font_scale_changed(nuevo)

    def _restaurar_tamano(self) -> None:
        actual = self._settings_service.font_scale()
        if actual == 100:
            return
        self._settings_service.set_font_scale(100)
        self._actualizar_texto_tamano()
        if self._on_font_scale_changed is not None:
            self._on_font_scale_changed(100)

    def _modulos_disponibles(self) -> tuple[str, ...]:
        """Cotizaciones solo existe con Modo avanzado: si su pestana esta
        oculta en MainWindow, tampoco se ofrece personalizarla."""
        if self._advanced_mode_var.get():
            return columns.MODULOS
        return tuple(m for m in columns.MODULOS if m != columns.QUOTATIONS)

    def _modulo_actual(self) -> str:
        return self._modulos_disponibles()[self._modulo_combo.current()]

    def _refrescar_modulos(self) -> None:
        """Re-arma la lista de modulos tras prender/apagar Modo avanzado,
        sin perder el modulo elegido si sigue disponible."""
        anterior = self._modulo_combo.get()
        disponibles = self._modulos_disponibles()
        etiquetas = [columns.MODULO_LABELS[m] for m in disponibles]
        self._modulo_combo.configure(values=etiquetas)
        indice = etiquetas.index(anterior) if anterior in etiquetas else 0
        self._modulo_combo.current(indice)
        self._mostrar_columnas()

    def _mostrar_columnas(self) -> None:
        modulo = self._modulo_actual()
        for hijo in self._columnas_frame.winfo_children():
            hijo.destroy()

        catalogo = columns.catalog(
            modulo,
            tier_names=self._tier_names,
            advanced=self._advanced_mode_var.get(),
        )
        visibles = {
            c.key
            for c in columns.visible(
                catalogo, self._settings_service.visible_columns(modulo)
            )
        }
        self._columna_vars = {}
        for definicion in catalogo:
            var = tk.BooleanVar(value=definicion.key in visibles)
            self._columna_vars[definicion.key] = var
            kit.Checkbutton(
                self._columnas_frame,
                text=definicion.heading,
                variable=var,
                command=self._on_cambiar_columnas,
            ).pack(anchor="w")

    def _on_cambiar_columnas(self) -> None:
        modulo = self._modulo_actual()
        elegidas = tuple(k for k, var in self._columna_vars.items() if var.get())
        # Las columnas de Modo avanzado que no estan a la vista (modo
        # apagado) se conservan: al volver a prenderlo, reaparecen como
        # estaban en vez de perderse por tocar otra casilla.
        guardadas = self._settings_service.visible_columns(modulo) or ()
        fuera_de_vista = tuple(k for k in guardadas if k not in self._columna_vars)
        self._settings_service.set_visible_columns(modulo, elegidas + fuera_de_vista)
        self._avisar_columnas(modulo)

    def _on_restaurar_columnas(self) -> None:
        modulo = self._modulo_actual()
        self._settings_service.reset_visible_columns(modulo)
        self._mostrar_columnas()
        self._avisar_columnas(modulo)

    def _avisar_columnas(self, modulo: str) -> None:
        if self._on_columns_changed is not None:
            self._on_columns_changed(modulo)

    def _build_modo_avanzado(self, parent: kit.Frame) -> None:
        interior = kit.Frame(parent, padding=16)
        interior.pack(fill="both", expand=True)
        kit.Checkbutton(
            interior,
            text="Modo avanzado",
            variable=self._advanced_mode_var,
            command=self._on_toggle_modo_avanzado,
        ).pack(anchor="w")
        kit.Label(
            interior,
            text=(
                "Con esto activo:\n"
                "- Nueva factura muestra un selector para decidir,\n"
                "  documento por documento, si cobra IVA o no.\n"
                "- Aparece la pestaña Cotizaciones, al lado de Facturas,\n"
                "  para notas de entrega con precio en $, en Bs a tasa\n"
                "  propia y en $ a tasa BCV.\n"
                "Apagado (el default), todo se ve como siempre."
            ),
            style="Muted.TLabel",
            justify="left",
        ).pack(anchor="w", pady=(8, 0))

    def _on_toggle_modo_avanzado(self) -> None:
        activo = self._advanced_mode_var.get()
        self._settings_service.set_advanced_mode(activo)
        self._refrescar_modulos()
        if self._on_advanced_mode_changed is not None:
            self._on_advanced_mode_changed(activo)
