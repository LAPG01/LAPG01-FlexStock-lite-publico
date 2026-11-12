"""
Tests de BackupPanel: que cada boton llame al service correcto con los
argumentos correctos, y que "Respaldar en.../Exportar seleccionados..."
no hagan nada si el usuario cierra el selector de carpeta sin elegir
(filedialog.askdirectory devuelve '' en ese caso).

    pytest -q
"""

import sqlite3
import tkinter as tk

import pytest

from flexstock.data import connection as connection_module
from flexstock.data.connection import init_schema
from flexstock.services import backup_service as backup_service_module
from flexstock.services.backup_service import BackupService
from flexstock.services.export_service import ExportColumn
from flexstock.services.settings_service import SettingsService
from flexstock.ui import backup_dialog as backup_dialog_module
from flexstock.ui import columns
from flexstock.ui.backup_dialog import BackupPanel, ColumnsChoiceDialog


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    monkeypatch.setattr(
        backup_dialog_module.messagebox, "showerror", lambda *a, **k: None
    )
    monkeypatch.setattr(
        backup_dialog_module.messagebox, "showinfo", lambda *a, **k: None
    )


@pytest.fixture(autouse=True)
def _sin_pantalla_de_carga(monkeypatch):
    """
    con_pantalla_de_carga() de verdad difiere 'trabajo' con after() (ver
    widgets.py) para que Tk pueda pintar la ventanita antes de bloquear -
    en un test eso significa que _on_exportar()/_on_exportar_facturas()
    volverian ANTES de haber exportado nada. Correrlo sincronico aca es
    lo mismo que probar la logica de exportar sin la ventanita, que ya
    tiene sus propios tests (ver test_con_pantalla_de_carga_*)."""

    def _sincronico(master, texto, trabajo, *, al_terminar=None):
        trabajo()
        if al_terminar is not None:
            al_terminar()

    monkeypatch.setattr(backup_dialog_module, "con_pantalla_de_carga", _sincronico)


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def backup_service(conn, tmp_path, monkeypatch):
    # Dos nombres de modulo distintos apuntan a config.BACKUP_DIR
    # (connection.py lo usa para el destino por default de backup(),
    # backup_service.py para el default de list_backups()): hay que
    # redirigir los dos o list_backups() sin argumento (como hace
    # BackupPanel._refrescar_lista()) sigue mirando la carpeta real.
    monkeypatch.setattr(connection_module, "BACKUP_DIR", tmp_path)
    monkeypatch.setattr(backup_service_module, "BACKUP_DIR", tmp_path)
    return BackupService(conn)


_COLUMNAS_FALSAS = [
    ExportColumn("sku", "sku", "SKU", lambda o: o),
    ExportColumn("name", "nombre", "Nombre", lambda o: o),
]


class _ExportServiceFalso:
    """Doble de ExportService: solo registra que se le pidio, sin tocar
    disco - lo que se prueba aca es que BackupPanel llame a lo correcto,
    no que ExportService escriba bien un CSV/PDF (eso ya lo prueba
    test_export_service.py). 'columns' se registra pero se ignora, igual
    forma que el real cuando no hay filtro."""

    def __init__(self) -> None:
        self.llamadas: list[tuple[str, object, object]] = []

    def export_products_csv(self, dest_dir, *, columns=None):
        self.llamadas.append(("productos_csv", dest_dir, columns))
        return dest_dir / "productos.csv"

    def export_products_pdf(self, dest_dir, *, columns=None):
        self.llamadas.append(("productos_pdf", dest_dir, columns))
        return dest_dir / "productos.pdf"

    def export_suppliers_csv(self, dest_dir, *, columns=None):
        self.llamadas.append(("proveedores_csv", dest_dir, columns))
        return dest_dir / "proveedores.csv"

    def export_suppliers_pdf(self, dest_dir, *, columns=None):
        self.llamadas.append(("proveedores_pdf", dest_dir, columns))
        return dest_dir / "proveedores.pdf"

    def export_customers_csv(self, dest_dir, *, columns=None):
        self.llamadas.append(("clientes_csv", dest_dir, columns))
        return dest_dir / "clientes.csv"

    def export_customers_pdf(self, dest_dir, *, columns=None):
        self.llamadas.append(("clientes_pdf", dest_dir, columns))
        return dest_dir / "clientes.pdf"

    def export_supplier_entries_csv(self, dest_dir, *, columns=None):
        self.llamadas.append(("entregas_csv", dest_dir, columns))
        return dest_dir / "entregas.csv"

    def export_supplier_entries_pdf(self, dest_dir, *, columns=None):
        self.llamadas.append(("entregas_pdf", dest_dir, columns))
        return dest_dir / "entregas.pdf"

    def export_invoices_csv(self, dest_dir):
        self.llamadas.append(("facturas_csv", dest_dir, None))
        return dest_dir / "facturas.csv"

    def product_export_columns(self):
        return _COLUMNAS_FALSAS

    def supplier_export_columns(self):
        return _COLUMNAS_FALSAS

    def customer_export_columns(self):
        return _COLUMNAS_FALSAS

    def supplier_entry_export_columns(self):
        return _COLUMNAS_FALSAS


@pytest.fixture
def export_service():
    return _ExportServiceFalso()


# ------------------------------------------------------------
# respaldo
# ------------------------------------------------------------
def test_respaldar_ahora_llama_al_service_y_refresca_la_lista(
    root, backup_service, export_service, tmp_path
):
    dialogo = BackupPanel(root, backup_service, export_service)

    dialogo._on_respaldar_ahora()

    assert len(list(tmp_path.glob("flexstock_*.db"))) == 1
    assert len(dialogo._lista_respaldos.get_children()) == 1
    dialogo.destroy()


def test_respaldar_en_usa_la_carpeta_elegida(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    otra = tmp_path / "pendrive"
    otra.mkdir()
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: str(otra)
    )

    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._on_respaldar_en()

    assert len(list(otra.glob("flexstock_*.db"))) == 1
    assert len(list(tmp_path.glob("flexstock_*.db"))) == 0  # no fue a BACKUP_DIR
    dialogo.destroy()


def test_respaldar_en_no_hace_nada_si_se_cancela_el_selector(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: ""
    )

    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._on_respaldar_en()

    assert list(tmp_path.glob("flexstock_*.db")) == []
    dialogo.destroy()


# ------------------------------------------------------------
# exportar catalogo (Productos/Proveedores/Clientes, Excel o PDF)
# ------------------------------------------------------------
def test_exportar_llama_solo_a_las_categorias_tildadas(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: str(tmp_path)
    )

    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._marcadas[columns.SUPPLIERS].set(False)

    dialogo._on_exportar()

    assert [nombre for nombre, _, _ in export_service.llamadas] == [
        "productos_csv",
        "clientes_csv",
        "entregas_csv",
    ]
    dialogo.destroy()


def test_exportar_sin_categorias_tildadas_no_abre_el_selector(
    root, backup_service, export_service, monkeypatch
):
    llamado = []
    monkeypatch.setattr(
        backup_dialog_module.filedialog,
        "askdirectory",
        lambda **_k: llamado.append(1),
    )

    dialogo = BackupPanel(root, backup_service, export_service)
    for modulo, _ in backup_dialog_module._ENTIDADES:
        dialogo._marcadas[modulo].set(False)

    dialogo._on_exportar()

    assert llamado == []
    assert export_service.llamadas == []
    dialogo.destroy()


def test_exportar_no_hace_nada_si_se_cancela_el_selector(
    root, backup_service, export_service, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: ""
    )

    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._on_exportar()

    assert export_service.llamadas == []
    dialogo.destroy()


def test_exportar_formato_pdf_llama_a_los_metodos_pdf(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: str(tmp_path)
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._formato_var.set("pdf")

    dialogo._on_exportar()

    assert [nombre for nombre, _, _ in export_service.llamadas] == [
        "productos_pdf",
        "proveedores_pdf",
        "clientes_pdf",
        "entregas_pdf",
    ]
    dialogo.destroy()


def test_exportar_manda_las_columnas_elegidas(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: str(tmp_path)
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._columnas_elegidas[columns.PRODUCTS] = ("sku", "name")

    dialogo._on_exportar()

    productos = next(l for l in export_service.llamadas if l[0] == "productos_csv")
    assert productos[2] == ("sku", "name")
    proveedores = next(l for l in export_service.llamadas if l[0] == "proveedores_csv")
    assert proveedores[2] is None  # sin elegir -> ficha completa
    dialogo.destroy()


# ------------------------------------------------------------
# exportar facturas (separado, siempre CSV)
# ------------------------------------------------------------
def test_exportar_facturas_llama_al_service(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: str(tmp_path)
    )
    dialogo = BackupPanel(root, backup_service, export_service)

    dialogo._on_exportar_facturas()

    assert [n for n, _, _ in export_service.llamadas] == ["facturas_csv"]
    dialogo.destroy()


def test_exportar_facturas_no_hace_nada_si_se_cancela(
    root, backup_service, export_service, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module.filedialog, "askdirectory", lambda **_k: ""
    )
    dialogo = BackupPanel(root, backup_service, export_service)

    dialogo._on_exportar_facturas()

    assert export_service.llamadas == []
    dialogo.destroy()


# ------------------------------------------------------------
# recordar la ultima carpeta elegida, por accion
# ------------------------------------------------------------
def _capturar_initialdir(monkeypatch, devuelve):
    """askdirectory falso: guarda el 'initialdir' con el que lo llamaron."""
    vistos = []

    def _falso(**kwargs):
        vistos.append(kwargs.get("initialdir"))
        return devuelve

    monkeypatch.setattr(backup_dialog_module.filedialog, "askdirectory", _falso)
    return vistos


def test_sin_settings_el_dialogo_arranca_sin_carpeta_previa(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    vistos = _capturar_initialdir(monkeypatch, str(tmp_path))
    dialogo = BackupPanel(root, backup_service, export_service)

    dialogo._on_respaldar_en()

    assert vistos == [""]  # Tk lo trata como "sin preferencia"
    dialogo.destroy()


def test_respaldar_en_recuerda_la_carpeta_para_la_proxima(
    root, conn, backup_service, export_service, tmp_path, monkeypatch
):
    settings = SettingsService(conn)
    otra = tmp_path / "pendrive"
    otra.mkdir()
    vistos = _capturar_initialdir(monkeypatch, str(otra))
    dialogo = BackupPanel(
        root, backup_service, export_service, settings_service=settings
    )

    dialogo._on_respaldar_en()
    dialogo._on_respaldar_en()

    assert vistos == ["", str(otra)]  # 1ra vez sin nada, 2da donde se dejo
    assert settings.last_dir("respaldo") == str(otra)
    dialogo.destroy()


def test_cancelar_no_pisa_la_carpeta_recordada(
    root, conn, backup_service, export_service, monkeypatch
):
    settings = SettingsService(conn)
    settings.set_last_dir("respaldo", "/media/pendrive")
    _capturar_initialdir(monkeypatch, "")  # el usuario cierra sin elegir
    dialogo = BackupPanel(
        root, backup_service, export_service, settings_service=settings
    )

    dialogo._on_respaldar_en()

    assert settings.last_dir("respaldo") == "/media/pendrive"
    dialogo.destroy()


def test_cada_accion_recuerda_su_propia_carpeta(
    root, conn, backup_service, export_service, tmp_path, monkeypatch
):
    settings = SettingsService(conn)
    respaldos = tmp_path / "respaldos"
    reportes = tmp_path / "reportes"
    facturas = tmp_path / "facturas"
    for carpeta in (respaldos, reportes, facturas):
        carpeta.mkdir()
    dialogo = BackupPanel(
        root, backup_service, export_service, settings_service=settings
    )

    for carpeta, accion in (
        (respaldos, dialogo._on_respaldar_en),
        (reportes, dialogo._on_exportar),
        (facturas, dialogo._on_exportar_facturas),
    ):
        _capturar_initialdir(monkeypatch, str(carpeta))
        accion()

    assert settings.last_dir("respaldo") == str(respaldos)
    assert settings.last_dir("exportar_catalogo") == str(reportes)
    assert settings.last_dir("exportar_facturas") == str(facturas)
    dialogo.destroy()


# ------------------------------------------------------------
# elegir columnas
# ------------------------------------------------------------
def test_elegir_columnas_sin_marcar_nada_avisa(root, backup_service, export_service):
    dialogo = BackupPanel(root, backup_service, export_service)
    for modulo, _ in backup_dialog_module._ENTIDADES:
        dialogo._marcadas[modulo].set(False)

    dialogo._on_elegir_columnas()  # no debe abrir ningun Toplevel ni fallar
    dialogo.destroy()


def test_elegir_columnas_sin_settings_arranca_todo_marcado(
    root, backup_service, export_service, monkeypatch
):
    capturado = {}
    monkeypatch.setattr(
        backup_dialog_module,
        "ColumnsChoiceDialog",
        lambda master, secciones: _capturar_y_falsear(capturado, secciones),
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._marcadas[columns.SUPPLIERS].set(False)
    monkeypatch.setattr(dialogo, "wait_window", lambda *_a: None)

    dialogo._on_elegir_columnas()

    modulos = [modulo for modulo, *_ in capturado["secciones"]]
    assert modulos == [
        columns.PRODUCTS,
        columns.CUSTOMERS,
        backup_dialog_module._SUPPLIER_ENTRIES,
    ]
    _, _, _, preseleccion = capturado["secciones"][0]
    assert set(preseleccion) == {"sku", "name"}
    dialogo.destroy()


def test_elegir_columnas_con_settings_arranca_con_las_visibles(
    root, conn, backup_service, export_service, monkeypatch
):
    settings = SettingsService(conn)
    settings.set_visible_columns(columns.PRODUCTS, ("sku",))
    capturado = {}
    monkeypatch.setattr(
        backup_dialog_module,
        "ColumnsChoiceDialog",
        lambda master, secciones: _capturar_y_falsear(capturado, secciones),
    )
    dialogo = BackupPanel(
        root, backup_service, export_service, settings_service=settings
    )
    monkeypatch.setattr(dialogo, "wait_window", lambda *_a: None)

    dialogo._on_elegir_columnas()

    _, _, _, preseleccion = capturado["secciones"][0]
    assert preseleccion == ("sku",)
    dialogo.destroy()


def test_elegir_columnas_cancelado_no_cambia_nada(
    root, backup_service, export_service, monkeypatch
):
    monkeypatch.setattr(
        backup_dialog_module,
        "ColumnsChoiceDialog",
        lambda master, secciones: _DialogoFalso(),
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    monkeypatch.setattr(dialogo, "wait_window", lambda *_a: None)

    dialogo._on_elegir_columnas()

    assert dialogo._columnas_elegidas[columns.PRODUCTS] is None
    dialogo.destroy()


def test_elegir_columnas_confirmado_guarda_la_eleccion(
    root, backup_service, export_service, monkeypatch
):
    resultado = {columns.PRODUCTS: ("sku",)}
    monkeypatch.setattr(
        backup_dialog_module,
        "ColumnsChoiceDialog",
        lambda master, secciones: _DialogoFalso(resultado),
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._marcadas[columns.SUPPLIERS].set(False)
    dialogo._marcadas[columns.CUSTOMERS].set(False)
    monkeypatch.setattr(dialogo, "wait_window", lambda *_a: None)

    dialogo._on_elegir_columnas()

    assert dialogo._columnas_elegidas[columns.PRODUCTS] == ("sku",)
    dialogo.destroy()


class _DialogoFalso:
    """Doble de ColumnsChoiceDialog: no abre ninguna ventana real, solo
    guarda el 'resultado' que BackupPanel lee despues de wait_window()."""

    def __init__(self, resultado=None) -> None:
        self.resultado = resultado

    def destroy(self) -> None:
        pass


def _capturar_y_falsear(capturado: dict, secciones) -> "_DialogoFalso":
    """Guarda 'secciones' (lo que BackupPanel le paso al constructor real)
    y devuelve el doble - por separado de un dict.setdefault()+'or', que
    devuelve 'secciones' en vez del doble porque una lista no vacia ya es
    verdadera para Python."""
    capturado["secciones"] = secciones
    return _DialogoFalso()


# ------------------------------------------------------------
# ColumnsChoiceDialog
# ------------------------------------------------------------
def test_columns_choice_dialog_preselecciona_lo_recibido(root):
    dialogo = ColumnsChoiceDialog(
        root, [(columns.PRODUCTS, "Productos", _COLUMNAS_FALSAS, ("sku",))]
    )

    assert dialogo._vars[columns.PRODUCTS]["sku"].get() is True
    assert dialogo._vars[columns.PRODUCTS]["name"].get() is False
    dialogo.destroy()


def test_columns_choice_dialog_marcar_todas(root):
    dialogo = ColumnsChoiceDialog(
        root, [(columns.PRODUCTS, "Productos", _COLUMNAS_FALSAS, ())]
    )

    dialogo._marcar(columns.PRODUCTS, True)

    assert all(v.get() for v in dialogo._vars[columns.PRODUCTS].values())
    dialogo.destroy()


def test_columns_choice_dialog_desmarcar_todas(root):
    dialogo = ColumnsChoiceDialog(
        root, [(columns.PRODUCTS, "Productos", _COLUMNAS_FALSAS, ("sku", "name"))]
    )

    dialogo._marcar(columns.PRODUCTS, False)

    assert not any(v.get() for v in dialogo._vars[columns.PRODUCTS].values())
    dialogo.destroy()


def test_columns_choice_dialog_aceptar_arma_el_resultado(root):
    dialogo = ColumnsChoiceDialog(
        root, [(columns.PRODUCTS, "Productos", _COLUMNAS_FALSAS, ("sku",))]
    )

    dialogo._on_aceptar()

    assert dialogo.resultado == {columns.PRODUCTS: ("sku",)}


def test_columns_choice_dialog_cancelar_deja_resultado_en_none(root):
    dialogo = ColumnsChoiceDialog(
        root, [(columns.PRODUCTS, "Productos", _COLUMNAS_FALSAS, ("sku",))]
    )

    dialogo.destroy()

    assert dialogo.resultado is None


# ------------------------------------------------------------
# lista de respaldos: fecha legible y abrir la carpeta
# ------------------------------------------------------------
def _respaldo_con_fecha(carpeta, nombre, *, tamano, fecha):
    import os

    ruta = carpeta / nombre
    ruta.write_bytes(b"x" * tamano)
    marca = fecha.timestamp()
    os.utime(ruta, (marca, marca))
    return ruta


def test_la_lista_muestra_el_archivo_su_fecha_legible_y_su_tamano(
    root, backup_service, export_service, tmp_path
):
    from datetime import datetime

    _respaldo_con_fecha(
        tmp_path,
        "flexstock_20261103_150845_102744.db",
        tamano=2_200_000,
        fecha=datetime(2026, 11, 3, 15, 8),  # noqa: DTZ001
    )
    dialogo = BackupPanel(root, backup_service, export_service)

    (fila,) = dialogo._lista_respaldos.get_children()
    valores = dialogo._lista_respaldos.item(fila, "values")

    assert valores[0] == "flexstock_20261103_150845_102744.db"
    assert valores[1] == "03/11/2026  15:08"
    assert valores[2] == "2,1 MB"
    dialogo.destroy()


def test_los_respaldos_mas_recientes_van_primero(
    root, backup_service, export_service, tmp_path
):
    from datetime import datetime

    for nombre in ("flexstock_20261020_100000.db", "flexstock_20261103_100000.db"):
        _respaldo_con_fecha(tmp_path, nombre, tamano=10, fecha=datetime(2026, 1, 1))  # noqa: DTZ001
    dialogo = BackupPanel(root, backup_service, export_service)

    nombres = [
        dialogo._lista_respaldos.item(f, "values")[0]
        for f in dialogo._lista_respaldos.get_children()
    ]

    assert nombres == [
        "flexstock_20261103_100000.db",
        "flexstock_20261020_100000.db",
    ]
    dialogo.destroy()


def test_doble_clic_en_un_respaldo_abre_su_carpeta(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    from datetime import datetime

    ruta = _respaldo_con_fecha(
        tmp_path,
        "flexstock_20261103_100000.db",
        tamano=10,
        fecha=datetime(2026, 1, 1),  # noqa: DTZ001
    )
    abiertas = []
    monkeypatch.setattr(
        backup_dialog_module, "reveal_in_folder", lambda r: abiertas.append(r)
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._lista_respaldos.selection_set(str(ruta))

    resultado = dialogo._on_abrir_respaldo()

    assert abiertas == [ruta]
    assert resultado == "break"
    dialogo.destroy()


def test_abrir_sin_elegir_un_respaldo_no_hace_nada(
    root, backup_service, export_service, monkeypatch
):
    abiertas = []
    monkeypatch.setattr(
        backup_dialog_module, "reveal_in_folder", lambda r: abiertas.append(r)
    )
    dialogo = BackupPanel(root, backup_service, export_service)

    dialogo._on_abrir_respaldo()

    assert abiertas == []
    dialogo.destroy()


def test_si_el_sistema_no_puede_abrir_la_carpeta_avisa_sin_romperse(
    root, backup_service, export_service, tmp_path, monkeypatch
):
    from datetime import datetime

    ruta = _respaldo_con_fecha(
        tmp_path,
        "flexstock_20261103_100000.db",
        tamano=10,
        fecha=datetime(2026, 1, 1),  # noqa: DTZ001
    )

    def sin_xdg_open(_ruta):
        raise OSError("xdg-open no esta instalado")

    errores = []
    monkeypatch.setattr(backup_dialog_module, "reveal_in_folder", sin_xdg_open)
    monkeypatch.setattr(
        backup_dialog_module.messagebox,
        "showerror",
        lambda t, m, **k: errores.append(m),
    )
    dialogo = BackupPanel(root, backup_service, export_service)
    dialogo._lista_respaldos.selection_set(str(ruta))

    dialogo._on_abrir_respaldo()

    assert len(errores) == 1
    assert "xdg-open" in errores[0]
    dialogo.destroy()
