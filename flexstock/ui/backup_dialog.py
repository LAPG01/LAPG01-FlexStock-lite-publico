"""
Panel de respaldo y exportacion: respaldar a la carpeta de siempre,
respaldar a una carpeta elegida (pendrive, nube sincronizada), ver los
respaldos recientes, exportar Productos/Proveedores/Clientes a Excel
(CSV) o PDF eligiendo columnas, y exportar Facturas a CSV.

Es un kit.Frame (no un dialogo propio): vive dentro de ConfigPage,
como una de sus secciones (ver config_page.py) - mismo patron que
ProductsPage/CustomersPage/etc. viviendo dentro del Notebook de
MainWindow, solo que este Notebook esta un nivel mas adentro.

Un CSV/PDF de listado NO es un respaldo (ver ExportService): por eso
viven en el mismo panel pero en secciones separadas, para no confundir
"esto se puede restaurar" con "esto es de solo consulta".
"""

import tkinter as tk
import tkinter.font as tkfont
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from ..config import BACKUP_DIR
from ..services.backup_service import BackupService
from ..services.export_service import ExportColumn, ExportService
from ..services.settings_service import SettingsService
from . import columns, kit
from .files import reveal_in_folder
from .theme import paint_window
from .widgets import alinear_encabezados, con_pantalla_de_carga, toast

# Entregas (product_supplier_entries) no es un modulo de ui/columns.py:
# es una tabla DENTRO de la ficha de un producto, no una pantalla de
# listado con columnas configurables propias (ver _visibles_hoy, que
# por eso no la busca ahi y arranca con todas las columnas marcadas).
_SUPPLIER_ENTRIES = "supplier_entries"

# (modulo, etiqueta en espanol) - un checkbox por entrada, en este
# orden, tanto en el panel como en "Elegir columnas...".
_ENTIDADES = (
    (columns.PRODUCTS, "Productos"),
    (columns.SUPPLIERS, "Proveedores"),
    (columns.CUSTOMERS, "Clientes"),
    (_SUPPLIER_ENTRIES, "Entregas"),
)

# modulo -> singular, para "{singular}_export_columns" (_fn_columnas_
# disponibles). Valores de puro texto, nunca tocan export_service, asi
# que construir esto no rompe un doble de pruebas que no implemente
# los metodos nuevos (ver _ExportServiceFalso en test_config_page.py).
_SINGULAR = {
    columns.PRODUCTS: "product",
    columns.SUPPLIERS: "supplier",
    columns.CUSTOMERS: "customer",
    _SUPPLIER_ENTRIES: "supplier_entry",
}

# modulo -> plural, para "export_{plural}_{formato}" (_fn_exportar).
# Separado de _SINGULAR porque "supplier_entry" + "s" da el plural
# incorrecto "supplier_entrys": no toda entidad pluraliza con un +s.
_PLURAL = {
    columns.PRODUCTS: "products",
    columns.SUPPLIERS: "suppliers",
    columns.CUSTOMERS: "customers",
    _SUPPLIER_ENTRIES: "supplier_entries",
}


def _tamano_legible(bytes_: int) -> str:
    """'2,1 MB' / '340 KB' - coma decimal, como el resto de la app."""
    if bytes_ >= 1024 * 1024:
        return f"{bytes_ / (1024 * 1024):.1f} MB".replace(".", ",")
    return f"{max(1, round(bytes_ / 1024))} KB"


class BackupPanel(kit.Frame):
    def __init__(
        self,
        master: tk.Misc,
        backup_service: BackupService,
        export_service: ExportService,
        settings_service: SettingsService | None = None,
        on_backup_done: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(master)
        # Avisa a MainWindow para que la barra lateral muestre la fecha
        # del respaldo nuevo (ver MainWindow._refrescar_estado_respaldo).
        self._on_backup_done = on_backup_done
        self._backup_service = backup_service
        self._export_service = export_service
        # Opcional (igual criterio que "Crear factura" en ProductsPage):
        # sin el, "Elegir columnas..." arranca con todo marcado en vez
        # de con lo que hoy se ve en cada tabla - el resto funciona igual.
        self._settings_service = settings_service

        self._marcadas = {modulo: tk.BooleanVar(value=True) for modulo, _ in _ENTIDADES}
        self._formato_var = tk.StringVar(value="csv")
        # None = ficha completa (el default de siempre). Se llena SOLO
        # si se abre "Elegir columnas..." y se confirma - no se guarda
        # en settings, es una eleccion de esta exportacion nomas.
        self._columnas_elegidas: dict[str, tuple[str, ...] | None] = {
            modulo: None for modulo, _ in _ENTIDADES
        }

        self._build_form()
        self._refrescar_lista()

    # ------------------------------------------------------------
    def _build_form(self) -> None:
        frame = kit.Frame(self, padding=20)
        frame.pack(fill="both", expand=True)

        respaldo = kit.LabelFrame(
            frame, text="Respaldo de la base de datos", padding=10
        )
        respaldo.pack(fill="x")
        kit.Label(
            respaldo,
            text=f"Carpeta por defecto:\n{BACKUP_DIR}",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(0, 8))

        botones_respaldo = kit.Frame(respaldo)
        botones_respaldo.pack(fill="x")
        kit.Button(
            botones_respaldo,
            text="Respaldar ahora",
            command=self._on_respaldar_ahora,
            style="Success.TButton",
        ).pack(side="left", padx=(0, 6))
        kit.Button(
            botones_respaldo, text="Respaldar en...", command=self._on_respaldar_en
        ).pack(side="left")

        kit.Label(
            respaldo, text="Respaldos recientes (doble clic abre su carpeta):"
        ).pack(anchor="w", pady=(12, 4))
        # Tabla y no una lista de nombres: el nombre lleva la fecha pero en
        # crudo (20261103_150845_102744), asi que se muestra aparte legible.
        marco_lista = kit.Frame(respaldo)
        marco_lista.pack(fill="x")
        self._lista_respaldos = ttk.Treeview(
            marco_lista,
            columns=("archivo", "fecha", "tamano"),
            show="headings",
            height=5,
            selectmode="browse",
        )
        # Fecha y tamano miden lo que su texto con la letra de ahora (un
        # ancho fijo los cortaba a 150 %); el nombre se lleva el resto.
        dato, titulo = (
            tkfont.nametofont("TkDefaultFont"),
            tkfont.nametofont("TkHeadingFont"),
        )
        ancho_fecha = max(dato.measure("00/00/0000  00:00"), titulo.measure("Fecha"))
        ancho_tamano = max(dato.measure("000,0 MB"), titulo.measure("Tamano"))
        for col, texto, ancho, ancla in (
            ("archivo", "Archivo", 260, "w"),
            ("fecha", "Fecha", ancho_fecha + 36, "e"),
            ("tamano", "Tamano", ancho_tamano + 36, "e"),
        ):
            self._lista_respaldos.heading(col, text=texto)
            self._lista_respaldos.column(
                col, width=ancho, anchor=ancla, stretch=(col == "archivo")
            )
        alinear_encabezados(self._lista_respaldos)
        barra = kit.Scrollbar(
            marco_lista, orient="vertical", command=self._lista_respaldos.yview
        )
        self._lista_respaldos.configure(yscrollcommand=barra.set)
        self._lista_respaldos.pack(side="left", fill="x", expand=True)
        barra.pack(side="right", fill="y")
        self._lista_respaldos.bind("<Double-1>", self._on_abrir_respaldo)
        self._lista_respaldos.bind("<Return>", self._on_abrir_respaldo)

        self._build_exportar_catalogo(frame)
        self._build_exportar_facturas(frame)

    def _build_exportar_catalogo(self, parent: kit.Frame) -> None:
        exportar = kit.LabelFrame(
            parent, text="Exportar catálogo (Excel o PDF)", padding=10
        )
        exportar.pack(fill="x", pady=(16, 0))

        fila_check = kit.Frame(exportar)
        fila_check.pack(fill="x")
        for modulo, etiqueta in _ENTIDADES:
            kit.Checkbutton(
                fila_check, text=etiqueta, variable=self._marcadas[modulo]
            ).pack(side="left", padx=(0, 16))

        fila_formato = kit.Frame(exportar)
        fila_formato.pack(fill="x", pady=(8, 0))
        kit.Label(fila_formato, text="Formato:").pack(side="left")
        kit.Radiobutton(
            fila_formato, text="Excel (CSV)", value="csv", variable=self._formato_var
        ).pack(side="left", padx=(6, 0))
        kit.Radiobutton(
            fila_formato, text="PDF", value="pdf", variable=self._formato_var
        ).pack(side="left", padx=(6, 0))

        fila_botones = kit.Frame(exportar)
        fila_botones.pack(fill="x", pady=(8, 0))
        kit.Button(
            fila_botones, text="Elegir columnas...", command=self._on_elegir_columnas
        ).pack(side="left")
        kit.Button(
            fila_botones,
            text="Exportar seleccionados...",
            command=self._on_exportar,
            style="Success.TButton",
        ).pack(side="left", padx=(6, 0))

    def _build_exportar_facturas(self, parent: kit.Frame) -> None:
        # Aparte del catalogo: Facturas no tiene PDF de listado (cada
        # factura ya tiene su propio PDF, ver "Generar PDF" en
        # BillingPage) ni columnas elegibles - siempre el mismo CSV
        # fijo, mismo comportamiento que antes de este panel crecer.
        facturas = kit.LabelFrame(parent, text="Exportar facturas", padding=10)
        facturas.pack(fill="x", pady=(16, 0))
        kit.Button(
            facturas,
            text="Exportar facturas a CSV...",
            command=self._on_exportar_facturas,
        ).pack(anchor="w")

    def _refrescar_lista(self) -> None:
        self._lista_respaldos.delete(*self._lista_respaldos.get_children())
        for ruta in self._backup_service.list_backups():
            try:
                info = ruta.stat()
            except OSError:  # borrado justo ahora
                continue
            fecha = datetime.fromtimestamp(info.st_mtime)  # noqa: DTZ006
            self._lista_respaldos.insert(
                "",
                "end",
                iid=str(ruta),
                values=(
                    ruta.name,
                    fecha.strftime("%d/%m/%Y  %H:%M"),
                    _tamano_legible(info.st_size),
                ),
            )

    def _on_abrir_respaldo(self, _evento: object = None) -> str:
        """Abre la carpeta donde esta el respaldo elegido (doble clic o Enter)."""
        seleccion = self._lista_respaldos.selection()
        if not seleccion:
            return "break"
        try:
            reveal_in_folder(Path(seleccion[0]))
        except OSError as error:
            messagebox.showerror(
                "Abrir carpeta",
                f"No se pudo abrir la carpeta del respaldo: {error}",
                parent=self,
            )
        return "break"  # el Enter no debe llegar a nada mas

    # ------------------------------------------------------------
    # recordar la ultima carpeta elegida, por accion (ver
    # SettingsService.last_dir): initialdir de askdirectory() para no
    # arrancar siempre en la misma carpeta de siempre.
    # ------------------------------------------------------------
    def _elegir_carpeta(self, accion: str, titulo: str) -> str:
        inicial = (
            self._settings_service.last_dir(accion)
            if self._settings_service is not None
            else ""
        )
        carpeta = filedialog.askdirectory(title=titulo, initialdir=inicial, parent=self)
        if carpeta and self._settings_service is not None:
            self._settings_service.set_last_dir(accion, carpeta)
        return carpeta

    # ------------------------------------------------------------
    # respaldo
    # ------------------------------------------------------------
    def _on_respaldar_ahora(self) -> None:
        try:
            ruta = self._backup_service.backup_now()
        except OSError as error:
            messagebox.showerror(
                "Respaldar ahora", f"No se pudo respaldar: {error}", parent=self
            )
            return
        toast(self, f"Respaldo creado: {ruta.name}")
        self._respaldo_hecho()

    def _on_respaldar_en(self) -> None:
        carpeta = self._elegir_carpeta("respaldo", "Elegir carpeta de respaldo")
        if not carpeta:  # el usuario cerro el dialogo sin elegir
            return

        try:
            ruta = self._backup_service.backup_now(Path(carpeta))
        except OSError as error:
            messagebox.showerror(
                "Respaldar en...", f"No se pudo respaldar: {error}", parent=self
            )
            return
        toast(self, f"Respaldo creado en {ruta.parent}")
        self._respaldo_hecho()

    def _respaldo_hecho(self) -> None:
        self._refrescar_lista()
        if self._on_backup_done is not None:
            self._on_backup_done()

    # ------------------------------------------------------------
    # exportar catalogo (Excel/PDF, columnas elegibles)
    # ------------------------------------------------------------
    def _modulos_marcados(self) -> list[str]:
        return [modulo for modulo, _ in _ENTIDADES if self._marcadas[modulo].get()]

    def _fn_exportar(self, modulo: str, formato: str):
        return getattr(self._export_service, f"export_{_PLURAL[modulo]}_{formato}")

    def _fn_columnas_disponibles(self, modulo: str):
        return getattr(self._export_service, f"{_SINGULAR[modulo]}_export_columns")

    def _on_elegir_columnas(self) -> None:
        marcados = self._modulos_marcados()
        if not marcados:
            messagebox.showinfo(
                "Elegir columnas", "Marca al menos una categoría primero.", parent=self
            )
            return

        secciones = []
        for modulo, etiqueta in _ENTIDADES:
            if modulo not in marcados:
                continue
            disponibles = self._fn_columnas_disponibles(modulo)()
            preseleccion = self._columnas_elegidas[modulo] or self._visibles_hoy(
                modulo, disponibles
            )
            secciones.append((modulo, etiqueta, disponibles, preseleccion))

        dialogo = ColumnsChoiceDialog(self, secciones)
        self.wait_window(dialogo)
        if dialogo.resultado is None:  # se cancelo, no tocar lo que ya habia
            return
        self._columnas_elegidas.update(dialogo.resultado)

    def _visibles_hoy(
        self, modulo: str, disponibles: list[ExportColumn]
    ) -> tuple[str, ...]:
        """
        Punto de partida al abrir "Elegir columnas...": las mismas que
        hoy se ven en esa tabla (Configuracion > Personalizacion) -
        "Marcar todas"/"Desmarcar todas" quedan ahi mismo para
        cambiarlo rapido, ver ColumnsChoiceDialog.

        Entregas no tiene una tabla de listado propia en Personalizacion
        (ui/columns.MODULOS no la incluye: ver _SUPPLIER_ENTRIES), asi
        que arranca con todas las columnas marcadas.
        """
        if self._settings_service is None or modulo not in columns.MODULOS:
            return tuple(c.key for c in disponibles)
        visibles = columns.visible(
            columns.catalog(modulo), self._settings_service.visible_columns(modulo)
        )
        exportables = {c.key for c in disponibles}
        return tuple(c.key for c in visibles if c.key in exportables)

    def _on_exportar(self) -> None:
        marcados = self._modulos_marcados()
        if not marcados:
            messagebox.showinfo(
                "Exportar", "Selecciona al menos una categoría.", parent=self
            )
            return

        carpeta = self._elegir_carpeta(
            "exportar_catalogo", "Elegir carpeta para exportar"
        )
        if not carpeta:
            return

        destino = Path(carpeta)
        formato = self._formato_var.get()
        resultado: dict[str, object] = {}

        def _exportar() -> None:
            try:
                resultado["rutas"] = [
                    self._fn_exportar(modulo, formato)(
                        destino, columns=self._columnas_elegidas[modulo]
                    )
                    for modulo in marcados
                ]
            except OSError as error:
                resultado["error"] = error

        def _mostrar_resultado() -> None:
            if "error" in resultado:
                messagebox.showerror(
                    "Exportar",
                    f"No se pudo exportar: {resultado['error']}",
                    parent=self,
                )
                return
            rutas = resultado["rutas"]
            listado = "\n".join(str(ruta) for ruta in rutas)
            messagebox.showinfo(
                "Exportar",
                f"Se exportaron {len(rutas)} archivo(s):\n{listado}",
                parent=self,
            )

        con_pantalla_de_carga(
            self, "Exportando...", _exportar, al_terminar=_mostrar_resultado
        )

    # ------------------------------------------------------------
    # exportar facturas (CSV fijo, sin columnas elegibles)
    # ------------------------------------------------------------
    def _on_exportar_facturas(self) -> None:
        carpeta = self._elegir_carpeta(
            "exportar_facturas", "Elegir carpeta para las facturas"
        )
        if not carpeta:
            return

        resultado: dict[str, object] = {}

        def _exportar() -> None:
            try:
                resultado["ruta"] = self._export_service.export_invoices_csv(
                    Path(carpeta)
                )
            except OSError as error:
                resultado["error"] = error

        def _mostrar_resultado() -> None:
            if "error" in resultado:
                messagebox.showerror(
                    "Exportar facturas",
                    f"No se pudo exportar: {resultado['error']}",
                    parent=self,
                )
                return
            messagebox.showinfo(
                "Exportar facturas",
                f"Facturas exportadas en:\n{resultado['ruta']}",
                parent=self,
            )

        con_pantalla_de_carga(
            self, "Exportando...", _exportar, al_terminar=_mostrar_resultado
        )


class ColumnsChoiceDialog(kit.Dialog):
    """
    Elegir que columnas van en la exportacion, una seccion por
    categoria marcada en el panel. Arranca con las columnas que
    BackupPanel le paso como preseleccion (hoy visibles en pantalla, o
    lo ultimo elegido aca mismo) - "Marcar todas"/"Desmarcar todas" por
    seccion para no tener que tildar una por una. No se guarda en
    settings: es una eleccion de ESTA exportacion, no una preferencia
    permanente (a diferencia de Configuracion > Personalizacion).
    """

    def __init__(
        self,
        master: tk.Misc,
        secciones: list[tuple[str, str, list[ExportColumn], tuple[str, ...]]],
    ) -> None:
        super().__init__(master)
        paint_window(self)
        self.title("Elegir columnas")
        self.transient(master)
        self.resizable(True, True)
        self.resultado: dict[str, tuple[str, ...]] | None = None
        self._vars: dict[str, dict[str, tk.BooleanVar]] = {}

        frame = kit.Frame(self, padding=16)
        frame.pack(fill="both", expand=True)

        for modulo, etiqueta, disponibles, preseleccion in secciones:
            self._build_seccion(frame, modulo, etiqueta, disponibles, preseleccion)

        botones = kit.Frame(frame)
        botones.pack(fill="x", pady=(8, 0))
        kit.Button(
            botones,
            text="Aceptar",
            command=self._on_aceptar,
            style="Success.TButton",
        ).pack(side="left", padx=4)
        kit.Button(botones, text="Cancelar", command=self.destroy).pack(
            side="left", padx=4
        )

        self.bind("<Escape>", lambda _evento: self.destroy())
        self.update_idletasks()
        self.minsize(self.winfo_reqwidth(), self.winfo_reqheight())
        self.lift()
        self.focus_force()
        self.grab_set()

    def _build_seccion(
        self,
        parent: kit.Frame,
        modulo: str,
        etiqueta: str,
        disponibles: list[ExportColumn],
        preseleccion: tuple[str, ...],
    ) -> None:
        seccion = kit.LabelFrame(parent, text=etiqueta, padding=8)
        seccion.pack(fill="x", pady=(0, 8))

        atajos = kit.Frame(seccion)
        atajos.pack(fill="x", pady=(0, 4))
        kit.Button(
            atajos, text="Marcar todas", command=lambda m=modulo: self._marcar(m, True)
        ).pack(side="left")
        kit.Button(
            atajos,
            text="Desmarcar todas",
            command=lambda m=modulo: self._marcar(m, False),
        ).pack(side="left", padx=(6, 0))

        grilla = kit.Frame(seccion)
        grilla.pack(fill="x")
        self._vars[modulo] = {}
        for indice, columna in enumerate(disponibles):
            var = tk.BooleanVar(value=columna.key in preseleccion)
            self._vars[modulo][columna.key] = var
            kit.Checkbutton(grilla, text=columna.label, variable=var).grid(
                row=indice // 4, column=indice % 4, sticky="w", padx=(0, 16), pady=2
            )

    def _marcar(self, modulo: str, valor: bool) -> None:
        for var in self._vars[modulo].values():
            var.set(valor)

    def _on_aceptar(self) -> None:
        self.resultado = {
            modulo: tuple(clave for clave, var in variables.items() if var.get())
            for modulo, variables in self._vars.items()
        }
        self.destroy()
