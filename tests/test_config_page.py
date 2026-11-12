"""
Tests de ConfigPage (la pagina Configuracion de la barra lateral): menu
de secciones, tema, tamano de letra, columnas, datos de la tienda y que
el interruptor de Modo avanzado lea/escriba SettingsService.

    pytest -q
"""

import sqlite3
import tkinter as tk

import pytest

from flexstock.data.connection import init_schema
from flexstock.services.backup_service import BackupService
from flexstock.services.settings_service import SettingsService, StoreInfo
from flexstock.ui import columns
from flexstock.ui import config_page as config_dialog_module
from flexstock.ui.config_page import ConfigPage


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


class _ExportServiceFalso:
    """No se ejercita en estas pruebas (eso ya lo cubre
    test_backup_dialog.py): alcanza con que BackupPanel lo pueda guardar."""


def _dialogo(root, conn):
    return ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
    )


def test_el_contenido_va_en_un_cuerpo_desplazable(root, conn):
    """
    A 150 % la seccion mas alta pide mas que la pantalla (1174 px en una
    de 1080). Como pagina no hay ventana que agrandar: el contenido se
    desplaza dentro de su cuerpo, y el menu de secciones queda fijo.
    """
    from flexstock.ui.widgets import ScrollableDialogBody

    pagina = _dialogo(root, conn)

    assert isinstance(pagina._scroll, ScrollableDialogBody)
    assert all(panel.master is pagina._scroll.content for panel in pagina._paneles)
    pagina.destroy()


def test_menu_lateral_lista_las_secciones(root, conn):
    dialogo = _dialogo(root, conn)

    assert dialogo.section_titles() == [
        # "Tienda" primera a proposito: es lo que hay que llenar antes de
        # emitir la primera factura (ver _secciones en config_dialog.py).
        "Tienda",
        "Respaldo y exportación",
        "Apariencia",
        "Inventario",
        "Columnas",
        "Modo avanzado",
    ]
    dialogo.destroy()


def test_arranca_mostrando_la_primera_seccion(root, conn):
    dialogo = _dialogo(root, conn)

    assert dialogo._seccion_actual == 0
    assert dialogo._paneles[0].winfo_manager() == "pack"
    assert dialogo._paneles[1].winfo_manager() == ""
    dialogo.destroy()


def test_elegir_otra_seccion_cambia_el_panel_visible(root, conn):
    dialogo = _dialogo(root, conn)

    dialogo.mostrar_seccion(1)

    assert dialogo._paneles[0].winfo_manager() == ""
    assert dialogo._paneles[1].winfo_manager() == "pack"
    assert dialogo._seccion_actual == 1
    dialogo.destroy()


# ------------------------------------------------------------
# Personalizacion: que columnas muestra cada listado
# ------------------------------------------------------------
def test_personalizacion_arranca_en_productos_con_el_default(root, conn):
    dialogo = _dialogo(root, conn)

    marcadas = {k for k, var in dialogo._columna_vars.items() if var.get()}

    assert dialogo._modulo_actual() == columns.PRODUCTS
    assert marcadas == set(columns.default_keys(columns.PRODUCTS))
    assert "cost" in dialogo._columna_vars  # esta disponible, apagada
    dialogo.destroy()


def test_personalizacion_cambia_tamano_y_avisa_a_la_ventana(root, conn):
    avisos = []
    dialogo = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        on_font_scale_changed=avisos.append,
    )

    dialogo._cambiar_tamano(1)

    assert SettingsService(conn).font_scale() == 110
    assert dialogo._font_scale_var.get() == "110 %  (15 pt)"
    assert avisos == [110]
    dialogo.destroy()


def test_desmarcar_una_columna_la_persiste(root, conn):
    dialogo = _dialogo(root, conn)

    dialogo._columna_vars["sku"].set(False)
    dialogo._columna_vars["cost"].set(True)
    dialogo._on_cambiar_columnas()

    guardadas = SettingsService(conn).visible_columns(columns.PRODUCTS)
    assert "sku" not in guardadas
    assert "cost" in guardadas
    dialogo.destroy()


def test_cambiar_de_modulo_muestra_sus_columnas(root, conn):
    dialogo = _dialogo(root, conn)

    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.CUSTOMERS)
    )
    dialogo._mostrar_columnas()

    assert set(dialogo._columna_vars) == {
        c.key for c in columns.catalog(columns.CUSTOMERS)
    }
    dialogo.destroy()


def test_personalizacion_de_facturas_ofrece_cedula_y_direccion(root, conn):
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.INVOICES)
    )
    dialogo._mostrar_columnas()

    marcadas = {k for k, var in dialogo._columna_vars.items() if var.get()}

    assert {"cedula", "direccion"} <= set(dialogo._columna_vars)
    assert "cedula" in marcadas  # la principal: visible por defecto
    assert "direccion" not in marcadas  # disponible, se prende si se quiere
    dialogo.destroy()


def test_poner_y_quitar_columnas_de_datos_del_cliente_en_facturas(root, conn):
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.INVOICES)
    )
    dialogo._mostrar_columnas()

    dialogo._columna_vars["direccion"].set(True)
    dialogo._columna_vars["cedula"].set(False)
    dialogo._on_cambiar_columnas()

    guardadas = SettingsService(conn).visible_columns(columns.INVOICES)
    assert "direccion" in guardadas
    assert "cedula" not in guardadas
    dialogo.destroy()


def test_personalizacion_de_facturas_ofrece_los_tres_montos(root, conn):
    """Con Modo avanzado: el $ cobrado, en Bs y a precio de catalogo."""
    SettingsService(conn).set_advanced_mode(True)
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.INVOICES)
    )
    dialogo._mostrar_columnas()

    marcadas = {k for k, var in dialogo._columna_vars.items() if var.get()}

    assert {"total", "total_bs", "total_catalogo"} <= set(dialogo._columna_vars)
    assert "total" in marcadas  # el principal, visible por defecto
    assert not {"total_bs", "total_catalogo"} & marcadas  # se prenden si se quieren
    dialogo.destroy()


def test_sin_modo_avanzado_no_se_ofrece_el_total_a_precio_de_catalogo(root, conn):
    """Sin ese modo los precios nunca se alteran: seria el mismo numero
    que "Total $ BCV"."""
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.INVOICES)
    )
    dialogo._mostrar_columnas()

    assert "total_bs" in dialogo._columna_vars
    assert "total_catalogo" not in dialogo._columna_vars
    dialogo.destroy()


def test_prender_los_montos_en_facturas_los_persiste(root, conn):
    SettingsService(conn).set_advanced_mode(True)
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.INVOICES)
    )
    dialogo._mostrar_columnas()

    dialogo._columna_vars["total_bs"].set(True)
    dialogo._columna_vars["total_catalogo"].set(True)
    dialogo._on_cambiar_columnas()

    guardadas = SettingsService(conn).visible_columns(columns.INVOICES)
    assert "total_bs" in guardadas
    assert "total_catalogo" in guardadas
    dialogo.destroy()


def _modulos_en_combo(dialogo):
    return list(dialogo._modulo_combo.cget("values"))


def test_sin_modo_avanzado_cotizaciones_no_se_puede_personalizar(root, conn):
    dialogo = _dialogo(root, conn)

    assert "Cotizaciones" not in _modulos_en_combo(dialogo)
    assert "Productos" in _modulos_en_combo(dialogo)
    dialogo.destroy()


def test_con_modo_avanzado_cotizaciones_aparece(root, conn):
    SettingsService(conn).set_advanced_mode(True)

    dialogo = _dialogo(root, conn)

    assert "Cotizaciones" in _modulos_en_combo(dialogo)
    dialogo.destroy()


def test_prender_modo_avanzado_la_agrega_sin_reabrir(root, conn):
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.SUPPLIERS)
    )
    dialogo._mostrar_columnas()

    dialogo._advanced_mode_var.set(True)
    dialogo._on_toggle_modo_avanzado()

    assert "Cotizaciones" in _modulos_en_combo(dialogo)
    assert dialogo._modulo_actual() == columns.SUPPLIERS  # no pierde la eleccion
    dialogo.destroy()


def test_apagar_modo_avanzado_estando_en_cotizaciones_vuelve_al_primero(root, conn):
    SettingsService(conn).set_advanced_mode(True)
    dialogo = _dialogo(root, conn)
    dialogo._modulo_combo.current(
        dialogo._modulos_disponibles().index(columns.QUOTATIONS)
    )
    dialogo._mostrar_columnas()

    dialogo._advanced_mode_var.set(False)
    dialogo._on_toggle_modo_avanzado()

    assert "Cotizaciones" not in _modulos_en_combo(dialogo)
    assert dialogo._modulo_actual() == columns.PRODUCTS
    # sin Modo avanzado, tampoco se ofrecen las columnas "(propia)"
    assert set(dialogo._columna_vars) == {
        c.key for c in columns.catalog(columns.PRODUCTS, advanced=False)
    }
    dialogo.destroy()


def test_restaurar_por_defecto_borra_lo_guardado(root, conn):
    settings = SettingsService(conn)
    settings.set_visible_columns(columns.PRODUCTS, ("name",))
    dialogo = _dialogo(root, conn)

    dialogo._on_restaurar_columnas()

    assert settings.visible_columns(columns.PRODUCTS) is None
    marcadas = {k for k, var in dialogo._columna_vars.items() if var.get()}
    assert marcadas == set(columns.default_keys(columns.PRODUCTS))
    dialogo.destroy()


def test_cambiar_columnas_avisa_al_callback_con_el_modulo(root, conn):
    avisos = []
    dialogo = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        on_columns_changed=avisos.append,
    )

    dialogo._columna_vars["cost"].set(True)
    dialogo._on_cambiar_columnas()

    assert avisos == [columns.PRODUCTS]
    dialogo.destroy()


def test_tier_names_titulan_las_columnas_de_precio(root, conn):
    dialogo = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        tier_names={1: "Mayor", 2: "Especial", 3: "Detal"},
    )

    titulos = [hijo.cget("text") for hijo in dialogo._columnas_frame.winfo_children()]

    assert "Detal $" in titulos
    assert "Detal Bs (BCV)" in titulos
    assert "Detal Bs (tasa propia)" not in titulos  # Modo avanzado apagado
    dialogo.destroy()


def test_con_modo_avanzado_personalizacion_ofrece_las_columnas_de_tasa_propia(
    root, conn
):
    SettingsService(conn).set_advanced_mode(True)
    dialogo = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        tier_names={1: "Mayor", 2: "Especial", 3: "Detal"},
    )

    titulos = [hijo.cget("text") for hijo in dialogo._columnas_frame.winfo_children()]

    assert "Detal Bs (tasa propia)" in titulos
    assert "Detal $ equivalente BCV" in titulos
    dialogo.destroy()


def test_tocar_una_casilla_sin_modo_avanzado_no_pierde_las_columnas_ocultas(root, conn):
    settings = SettingsService(conn)
    settings.set_visible_columns(columns.PRODUCTS, ("name", "price3_bs"))
    dialogo = _dialogo(root, conn)  # Modo avanzado apagado: price3_bs no se ve

    dialogo._columna_vars["cost"].set(True)
    dialogo._on_cambiar_columnas()

    guardadas = settings.visible_columns(columns.PRODUCTS)
    assert "cost" in guardadas
    assert "price3_bs" in guardadas  # vuelve a aparecer al prender el modo
    dialogo.destroy()


def test_toggle_modo_avanzado_avisa_al_callback(root, conn):
    avisos = []
    dialogo = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        on_advanced_mode_changed=avisos.append,
    )

    dialogo._advanced_mode_var.set(True)
    dialogo._on_toggle_modo_avanzado()

    assert avisos == [True]
    dialogo.destroy()


def test_modo_avanzado_arranca_apagado_por_defecto(root, conn):
    dialogo = _dialogo(root, conn)

    assert dialogo._advanced_mode_var.get() is False
    dialogo.destroy()


def test_modo_avanzado_prellena_desde_settings(root, conn):
    SettingsService(conn).set_advanced_mode(True)

    dialogo = _dialogo(root, conn)

    assert dialogo._advanced_mode_var.get() is True
    dialogo.destroy()


def test_activar_modo_avanzado_lo_persiste_en_settings(root, conn):
    dialogo = _dialogo(root, conn)

    dialogo._advanced_mode_var.set(True)
    dialogo._on_toggle_modo_avanzado()

    assert SettingsService(conn).advanced_mode() is True
    dialogo.destroy()


def test_desactivar_modo_avanzado_lo_persiste_en_settings(root, conn):
    SettingsService(conn).set_advanced_mode(True)
    dialogo = _dialogo(root, conn)

    dialogo._advanced_mode_var.set(False)
    dialogo._on_toggle_modo_avanzado()

    assert SettingsService(conn).advanced_mode() is False
    dialogo.destroy()


def _png(tmp_path, nombre="logo.png", tamano=(800, 400), formato="PNG"):
    from PIL import Image

    ruta = tmp_path / nombre
    Image.new("RGB", tamano, "white").save(ruta, formato)
    return ruta


def test_tienda_es_la_primera_seccion(root, conn):
    """Es lo que hay que llenar antes de emitir la primera factura."""
    dialogo = _dialogo(root, conn)

    assert dialogo.section_titles()[0] == "Tienda"
    dialogo.destroy()


def test_tienda_arranca_con_lo_guardado(root, conn):
    SettingsService(conn).set_store_info(
        StoreInfo(name="Ferreteria El Tornillo", tax_id="J-123456")
    )

    dialogo = _dialogo(root, conn)

    assert dialogo._tienda_vars["name"].get() == "Ferreteria El Tornillo"
    assert dialogo._tienda_rif_tipo.get() == "J"
    assert dialogo._tienda_rif_numero.get() == "123456"
    dialogo.destroy()


def test_guardar_tienda_persiste_los_datos(root, conn):
    dialogo = _dialogo(root, conn)
    dialogo._tienda_vars["name"].set("Ferreteria El Tornillo")
    dialogo._tienda_rif_tipo.set("J")
    dialogo._tienda_rif_numero.set("12345678-9")
    dialogo._tienda_vars["address"].set("Av. Principal")
    dialogo._tienda_vars["phone"].set("0212-5551234")

    dialogo._on_guardar_tienda()

    info = SettingsService(conn).store_info()
    assert info.name == "Ferreteria El Tornillo"
    assert info.tax_id == "J-12345678-9"
    assert info.address == "Av. Principal"
    assert info.phone == "0212-5551234"
    dialogo.destroy()


def test_guardar_tienda_sin_nombre_avisa_y_no_guarda(root, conn):
    dialogo = _dialogo(root, conn)
    dialogo._tienda_vars["name"].set("   ")

    dialogo._on_guardar_tienda()

    assert "nombre" in dialogo._tienda_estado_var.get()
    assert str(dialogo._tienda_estado.cget("style")) == "Error.TLabel"
    assert SettingsService(conn).store_info().name == "Ferreteria"  # sin tocar
    dialogo.destroy()


def test_guardar_tienda_avisa_a_quien_lo_pidio(root, conn):
    """MainWindow repinta el membrete de Facturas sin reiniciar."""
    avisos = []
    dialogo = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        on_store_info_changed=lambda: avisos.append(1),
    )
    dialogo._tienda_vars["name"].set("Mi Tienda")

    dialogo._on_guardar_tienda()

    assert avisos == [1]
    dialogo.destroy()


def test_elegir_logo_lo_guarda_y_lo_previsualiza(root, conn, tmp_path, monkeypatch):
    dialogo = _dialogo(root, conn)
    monkeypatch.setattr(
        config_dialog_module.filedialog,
        "askopenfilename",
        lambda **_k: str(_png(tmp_path)),
    )

    dialogo._on_elegir_logo()

    assert SettingsService(conn).logo() is not None
    assert dialogo._logo_foto is not None  # la vista previa se armo
    dialogo.destroy()


def test_elegir_logo_y_cancelar_no_cambia_nada(root, conn, monkeypatch):
    dialogo = _dialogo(root, conn)
    monkeypatch.setattr(
        config_dialog_module.filedialog, "askopenfilename", lambda **_k: ""
    )

    dialogo._on_elegir_logo()

    assert SettingsService(conn).logo() is None
    dialogo.destroy()


def test_elegir_un_archivo_que_no_es_imagen_avisa(root, conn, tmp_path, monkeypatch):
    basura = tmp_path / "roto.png"
    basura.write_text("no soy una imagen", encoding="utf-8")
    errores = []
    monkeypatch.setattr(
        config_dialog_module.messagebox,
        "showerror",
        lambda _t, m, **_k: errores.append(m),
    )
    dialogo = _dialogo(root, conn)
    monkeypatch.setattr(
        config_dialog_module.filedialog, "askopenfilename", lambda **_k: str(basura)
    )

    dialogo._on_elegir_logo()

    assert errores and "imagen" in errores[0]
    assert SettingsService(conn).logo() is None
    dialogo.destroy()


def test_quitar_logo_lo_saca(root, conn, tmp_path):
    SettingsService(conn).set_logo(_png(tmp_path))
    dialogo = _dialogo(root, conn)

    dialogo._on_quitar_logo()

    assert SettingsService(conn).logo() is None
    assert dialogo._logo_foto is None
    dialogo.destroy()


# ------------------------------------------------------------
# Apariencia: modo claro/oscuro (antes era un boton suelto del encabezado)
# ------------------------------------------------------------
def test_el_interruptor_de_modo_oscuro_arranca_como_esta_guardado(root, conn):
    SettingsService(conn).set_ui_theme("dark")

    pagina = _dialogo(root, conn)

    assert pagina._modo_oscuro_var.get() is True
    pagina.destroy()


def test_el_interruptor_de_modo_oscuro_avisa_a_la_ventana(root, conn):
    modos = []
    pagina = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        on_theme_changed=modos.append,
    )

    pagina._switch_modo_oscuro.toggle()
    pagina._switch_modo_oscuro.toggle()

    assert modos == ["dark", "light"]
    pagina.destroy()


def test_reload_pone_al_dia_lo_que_cambio_desde_afuera(root, conn):
    settings = SettingsService(conn)
    pagina = _dialogo(root, conn)

    # Ctrl+ desde otra pagina y el tema guardado por la ventana.
    settings.set_font_scale(125)
    settings.set_ui_theme("dark")
    pagina.reload()

    assert pagina._font_scale_var.get().startswith("125 %")
    assert pagina._modo_oscuro_var.get() is True
    pagina.destroy()


def test_respaldar_desde_configuracion_avisa_a_la_ventana(
    root, conn, monkeypatch, tmp_path
):
    from flexstock.data import connection as connection_module

    monkeypatch.setattr(connection_module, "BACKUP_DIR", tmp_path)
    avisos = []
    pagina = ConfigPage(
        root,
        backup_service=BackupService(conn),
        export_service=_ExportServiceFalso(),
        settings_service=SettingsService(conn),
        on_backup_done=lambda: avisos.append(1),
    )

    pagina._backup_panel._on_respaldar_ahora()

    assert avisos == [1]
    pagina.destroy()


# ------------------------------------------------------------
# Tienda: RIF con selector de tipo, domicilio fiscal y correo
# ------------------------------------------------------------
@pytest.mark.parametrize(
    ("tipo", "numero", "esperado"),
    [
        ("J", "12345678-9", "J-12345678-9"),
        ("J", "12345678 9", "J-12345678-9"),  # espacio en vez de guion
        ("V", "12345678", "V-12345678"),  # sin verificador
        ("J", "", ""),  # sin RIF: queda vacio, no frena nada
        ("J", "  ", ""),
    ],
)
def test_armar_rif(tipo, numero, esperado):
    assert config_dialog_module._armar_rif(tipo, numero) == esperado


@pytest.mark.parametrize("numero", ["12AB5678", "12345", "12345678901"])
def test_armar_rif_rechaza_lo_que_no_es_un_rif(numero):
    with pytest.raises(ValueError):
        config_dialog_module._armar_rif("J", numero)


def test_partir_rif_separa_el_tipo_y_conserva_lo_ya_guardado():
    partir = config_dialog_module._partir_rif

    assert partir("J-12345678-9") == ("J", "12345678-9")
    assert partir("v-12345678") == ("V", "12345678")
    assert partir("") == ("J", "")  # un negocio es J salvo que se diga otra cosa
    assert partir("lo-que-sea")[1] == "LO-QUE-SEA"  # no se pierde un dato viejo


def test_la_tienda_muestra_lo_guardado_y_el_rif_en_dos_partes(root, conn):
    SettingsService(conn).set_store_info(
        StoreInfo(
            name="La Esquina",
            tax_id="V-12345678-9",
            address="Av. Bolivar",
            phone="0241-1234567",
            email="ventas@laesquina.com",
        )
    )

    pagina = _dialogo(root, conn)

    assert pagina._tienda_vars["name"].get() == "La Esquina"
    assert pagina._tienda_rif_tipo.get() == "V"
    assert pagina._tienda_rif_numero.get() == "12345678-9"
    assert pagina._tienda_vars["email"].get() == "ventas@laesquina.com"
    pagina.destroy()


def test_guardar_la_tienda_une_el_tipo_y_el_numero_del_rif(root, conn):
    pagina = _dialogo(root, conn)
    pagina._tienda_vars["name"].set("La Esquina")
    pagina._tienda_rif_tipo.set("J")
    pagina._tienda_rif_numero.set("12345678 9")
    pagina._tienda_vars["address"].set("Av. Bolivar, Valencia")
    pagina._tienda_vars["phone"].set("0241-1234567")
    pagina._tienda_vars["email"].set("ventas@laesquina.com")

    pagina._on_guardar_tienda()

    info = SettingsService(conn).store_info()
    assert info == StoreInfo(
        name="La Esquina",
        tax_id="J-12345678-9",
        address="Av. Bolivar, Valencia",
        phone="0241-1234567",
        email="ventas@laesquina.com",
    )
    pagina.destroy()


def test_un_rif_mal_escrito_avisa_y_no_guarda_nada(root, conn):
    pagina = _dialogo(root, conn)
    pagina._tienda_vars["name"].set("La Esquina")
    pagina._tienda_rif_numero.set("12AB")

    pagina._on_guardar_tienda()

    assert "RIF" in pagina._tienda_estado_var.get()
    assert SettingsService(conn).store_info().name != "La Esquina"  # nada se guardo
    pagina.destroy()


def test_la_tienda_ya_no_marca_campos_con_asterisco(root, conn):
    pagina = _dialogo(root, conn)
    textos = []

    def recorrer(widget):
        try:
            textos.append(str(widget.cget("text")))
        except (tk.TclError, ValueError):
            pass
        for hijo in widget.winfo_children():
            recorrer(hijo)

    recorrer(pagina._paneles[0])

    assert not [t for t in textos if "*" in t]
    assert "Nombre del negocio:" in textos
    assert "Domicilio fiscal:" in textos
    pagina.destroy()


def test_la_tienda_guarda_razon_social_web_datos_de_pago_y_pie(root, conn):
    pagina = _dialogo(root, conn)
    pagina._tienda_vars["name"].set("La Esquina")
    pagina._tienda_vars["legal_name"].set("Inversiones La Esquina C.A.")
    pagina._tienda_vars["web"].set("@laesquina")
    pagina._tienda_textos["payment_info"].insert(
        "1.0", "Pago movil 0414-1234567\nZelle: pagos@laesquina.com"
    )
    pagina._tienda_textos["footer_text"].insert("1.0", "Garantia 30 días.")

    pagina._on_guardar_tienda()

    info = SettingsService(conn).store_info()
    assert info.legal_name == "Inversiones La Esquina C.A."
    assert info.web == "@laesquina"
    assert info.payment_info == "Pago movil 0414-1234567\nZelle: pagos@laesquina.com"
    assert info.footer_text == "Garantia 30 días."
    pagina.destroy()


def test_la_tienda_muestra_los_textos_de_pago_y_pie_ya_guardados(root, conn):
    SettingsService(conn).set_store_info(
        StoreInfo(
            name="La Esquina",
            payment_info="Banco X\nCuenta 0102",
            footer_text="Gracias",
        )
    )

    pagina = _dialogo(root, conn)

    assert pagina._tienda_textos["payment_info"].get("1.0", "end").strip() == (
        "Banco X\nCuenta 0102"
    )
    assert pagina._tienda_textos["footer_text"].get("1.0", "end").strip() == "Gracias"
    pagina.destroy()


def test_dejar_vacios_el_pago_y_el_pie_los_borra(root, conn):
    SettingsService(conn).set_store_info(
        StoreInfo(name="La Esquina", payment_info="Banco X", footer_text="Gracias")
    )
    pagina = _dialogo(root, conn)
    pagina._tienda_textos["payment_info"].delete("1.0", "end")
    pagina._tienda_textos["footer_text"].delete("1.0", "end")

    pagina._on_guardar_tienda()

    info = SettingsService(conn).store_info()
    assert (info.payment_info, info.footer_text) == ("", "")
    pagina.destroy()


def test_elegir_qr_de_pago_lo_guarda_y_lo_previsualiza(
    root, conn, tmp_path, monkeypatch
):
    dialogo = _dialogo(root, conn)
    assert str(dialogo._btn_quitar_qr.cget("state")) == "disabled"  # nada que quitar
    monkeypatch.setattr(
        config_dialog_module.filedialog,
        "askopenfilename",
        lambda **_k: str(_png(tmp_path, nombre="qr.png", tamano=(500, 500))),
    )

    dialogo._on_elegir_qr()

    assert SettingsService(conn).payment_qr() is not None
    assert SettingsService(conn).logo() is None
    assert dialogo._qr_foto is not None
    assert str(dialogo._btn_quitar_qr.cget("state")) == "normal"
    dialogo.destroy()


def test_quitar_el_qr_de_pago(root, conn, tmp_path):
    SettingsService(conn).set_payment_qr(_png(tmp_path, nombre="qr.png"))
    dialogo = _dialogo(root, conn)
    assert dialogo._qr_foto is not None  # el guardado se ve al abrir

    dialogo._on_quitar_qr()

    assert SettingsService(conn).payment_qr() is None
    assert dialogo._qr_foto is None
    dialogo.destroy()
