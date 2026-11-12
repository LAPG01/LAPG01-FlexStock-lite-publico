"""
Tests de SettingsService.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.services.settings_service import SettingsService, StoreInfo


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def service(conn):
    return SettingsService(conn)


def test_advanced_mode_apagado_por_defecto(service):
    assert service.advanced_mode() is False


def test_set_advanced_mode_prende_y_persiste(service):
    service.set_advanced_mode(True)

    assert service.advanced_mode() is True


def test_set_advanced_mode_apaga_de_nuevo(service):
    service.set_advanced_mode(True)
    service.set_advanced_mode(False)

    assert service.advanced_mode() is False


def test_tamano_de_letra_arranca_en_cien_por_ciento(service):
    assert service.font_scale() == 100


@pytest.mark.parametrize("scale", (125, 150))
def test_tamano_de_letra_se_guarda_y_se_lee(service, scale):
    service.set_font_scale(scale)

    assert service.font_scale() == scale


@pytest.mark.parametrize("scale", (120, 175))
def test_tamano_de_letra_invalido_no_se_guarda(service, scale):
    with pytest.raises(ValueError, match="Tamano"):
        service.set_font_scale(scale)


def test_tamano_de_letra_desconocido_guardado_vuelve_a_cien(conn, service):
    set_setting(conn, "font_scale", "175")

    assert service.font_scale() == 100


def test_last_dir_arranca_vacio(service):
    assert service.last_dir("respaldo") == ""


def test_set_last_dir_guarda_y_devuelve_la_carpeta(service):
    service.set_last_dir("respaldo", "/media/pendrive")

    assert service.last_dir("respaldo") == "/media/pendrive"


def test_last_dir_es_independiente_por_accion(service):
    service.set_last_dir("respaldo", "/media/pendrive")
    service.set_last_dir("exportar_catalogo", "/home/leo/reportes")

    assert service.last_dir("respaldo") == "/media/pendrive"
    assert service.last_dir("exportar_catalogo") == "/home/leo/reportes"
    assert service.last_dir("exportar_facturas") == ""  # nunca elegida


# ------------------------------------------------------------
# Datos de la tienda (Configuracion > Tienda)
# ------------------------------------------------------------
def test_store_info_arranca_con_el_default_del_esquema(service):
    info = service.store_info()

    assert info.name == "Ferreteria"
    assert info.tax_id == ""
    assert info.address == ""
    assert info.phone == ""


def test_set_store_info_guarda_los_cuatro_campos(service):
    service.set_store_info(
        StoreInfo(
            name="Ferreteria El Tornillo",
            tax_id="J-12345678-9",
            address="Av. Principal, Local 3",
            phone="0212-5551234",
        )
    )

    info = service.store_info()
    assert info.name == "Ferreteria El Tornillo"
    assert info.tax_id == "J-12345678-9"
    assert info.address == "Av. Principal, Local 3"
    assert info.phone == "0212-5551234"


def test_set_store_info_recorta_los_espacios(service):
    service.set_store_info(StoreInfo(name="  Mi Tienda  ", tax_id="  J-1  "))

    info = service.store_info()
    assert info.name == "Mi Tienda"
    assert info.tax_id == "J-1"


def test_set_store_info_sin_nombre_lanza_valueerror(service):
    """El nombre es lo unico que no puede faltar: sin el, la factura que
    recibe el cliente queda anonima."""
    with pytest.raises(ValueError, match="nombre"):
        service.set_store_info(StoreInfo(name="   "))


def test_set_store_info_no_valida_el_formato_del_rif(service):
    """A proposito: hay J-, V-, G- y tambien quien todavia no lo tiene.
    Frenar una venta por el formato seria peor que imprimirlo como venga."""
    service.set_store_info(StoreInfo(name="Mi Tienda", tax_id="lo-que-sea"))

    assert service.store_info().tax_id == "lo-que-sea"


# ------------------------------------------------------------
# Logo
# ------------------------------------------------------------
def _imagen(tmp_path, nombre="logo.png", tamano=(800, 400), formato="PNG"):
    from PIL import Image

    ruta = tmp_path / nombre
    Image.new("RGB", tamano, "white").save(ruta, formato)
    return ruta


def test_sin_logo_devuelve_none(service):
    assert service.logo() is None


def test_set_logo_guarda_la_imagen_en_la_base(service, tmp_path, conn):
    service.set_logo(_imagen(tmp_path))

    crudo = service.logo()
    assert crudo is not None
    assert crudo[:8] == b"\x89PNG\r\n\x1a\n"  # se normalizo a PNG
    # y vive en settings, no en un archivo suelto: por eso el respaldo
    # de la base se lo lleva solo
    fila = conn.execute(
        "SELECT value FROM settings WHERE key = 'company_logo'"
    ).fetchone()
    assert fila is not None and fila["value"]


def test_set_logo_acepta_jpg_y_lo_pasa_a_png(service, tmp_path):
    service.set_logo(_imagen(tmp_path, nombre="logo.jpg", formato="JPEG"))

    assert service.logo()[:8] == b"\x89PNG\r\n\x1a\n"


def test_set_logo_achica_una_imagen_grande(service, tmp_path):
    """Una foto del telefono no puede entrar entera en una fila de settings."""
    from PIL import Image

    service.set_logo(_imagen(tmp_path, tamano=(4000, 3000)))

    import io

    with Image.open(io.BytesIO(service.logo())) as guardada:
        assert max(guardada.size) <= 600


def test_set_logo_conserva_la_proporcion(service, tmp_path):
    from PIL import Image

    service.set_logo(_imagen(tmp_path, tamano=(800, 400)))  # 2:1

    import io

    with Image.open(io.BytesIO(service.logo())) as guardada:
        ancho, alto = guardada.size
    assert ancho == 2 * alto


def test_set_logo_con_un_archivo_que_no_es_imagen_avisa(service, tmp_path):
    basura = tmp_path / "no_es_imagen.png"
    basura.write_text("esto no es una imagen", encoding="utf-8")

    with pytest.raises(ValueError, match="imagen"):
        service.set_logo(basura)

    assert service.logo() is None  # no quedo nada a medias


def test_clear_logo_lo_saca(service, tmp_path):
    service.set_logo(_imagen(tmp_path))

    service.clear_logo()

    assert service.logo() is None


def test_un_logo_corrupto_en_la_base_no_rompe(service, conn):
    """Si alguien edita la base a mano, la factura tiene que poder
    imprimirse igual: sin logo, pero imprimirse."""
    conn.execute(
        "INSERT INTO settings (key, value) VALUES ('company_logo', 'no-es-base64!!')"
    )

    assert service.logo() is None


def test_existencia_inicial_arranca_permitida(conn):
    assert SettingsService(conn).allow_initial_stock() is True


def test_existencia_inicial_se_puede_apagar_y_prender(conn):
    settings = SettingsService(conn)

    settings.set_allow_initial_stock(False)
    assert settings.allow_initial_stock() is False

    settings.set_allow_initial_stock(True)
    assert settings.allow_initial_stock() is True


def test_barra_lateral_arranca_abierta_y_recuerda_si_se_plego(conn):
    settings = SettingsService(conn)
    assert settings.sidebar_collapsed() is False

    settings.set_sidebar_collapsed(True)

    assert settings.sidebar_collapsed() is True


def test_el_correo_de_la_tienda_se_guarda_y_se_recorta(service):
    service.set_store_info(StoreInfo(name="Mi Tienda", email="  ventas@tienda.com  "))

    assert service.store_info().email == "ventas@tienda.com"


def test_el_correo_arranca_vacio(service):
    assert service.store_info().email == ""


def test_los_datos_nuevos_de_la_tienda_se_guardan_y_se_recortan(service):
    service.set_store_info(
        StoreInfo(
            name="Mi Tienda",
            legal_name="  Inversiones Mi Tienda C.A.  ",
            web="  @mitienda  ",
            payment_info="  Pago movil 0414-1234567\nBanco X  ",
            footer_text="  Garantia 30 días  ",
        )
    )

    info = service.store_info()

    assert info.legal_name == "Inversiones Mi Tienda C.A."
    assert info.web == "@mitienda"
    assert info.payment_info == "Pago movil 0414-1234567\nBanco X"  # conserva el salto
    assert info.footer_text == "Garantia 30 días"


def test_los_datos_nuevos_de_la_tienda_arrancan_vacios(service):
    info = service.store_info()

    assert (info.legal_name, info.web, info.payment_info, info.footer_text) == (
        "",
        "",
        "",
        "",
    )


def test_store_info_for_pdf_trae_todo_lo_que_imprimen_los_pdf(service, conn):
    from flexstock.services.settings_service import store_info_for_pdf

    service.set_store_info(
        StoreInfo(
            name="Mi Tienda",
            address="Av. 1",
            email="a@b.com",
            legal_name="Mi Tienda C.A.",
            web="@mitienda",
            payment_info="Zelle x",
            footer_text="Garantia",
        )
    )

    assert store_info_for_pdf(conn) == {
        "company_address": "Av. 1",
        "company_email": "a@b.com",
        "company_legal_name": "Mi Tienda C.A.",
        "company_web": "@mitienda",
        "company_payment_info": "Zelle x",
        "company_footer": "Garantia",
    }


# ------------------------------------------------------------
# QR de pago
# ------------------------------------------------------------
def test_sin_qr_devuelve_none(service):
    assert service.payment_qr() is None


def test_set_payment_qr_lo_guarda_en_la_base_aparte_del_logo(service, tmp_path):
    service.set_payment_qr(_imagen(tmp_path, nombre="qr.png", tamano=(500, 500)))

    assert service.payment_qr()[:8] == b"\x89PNG\r\n\x1a\n"
    assert service.logo() is None  # no pisa el logo


def test_un_qr_grande_se_achica_sin_deformarse(service, tmp_path):
    import io

    from PIL import Image

    service.set_payment_qr(_imagen(tmp_path, nombre="qr.png", tamano=(3000, 3000)))

    with Image.open(io.BytesIO(service.payment_qr())) as guardada:
        assert guardada.size == (800, 800)


def test_una_foto_pesada_se_guarda_como_jpg_y_cabe(service, tmp_path):
    """Una foto (ruido, no cuadritos limpios) en PNG pasa del medio mega: se
    guarda como JPG, que reconocen solos Pillow y ReportLab."""
    import os

    from PIL import Image

    ruta = tmp_path / "foto.png"
    Image.frombytes("RGB", (1200, 1200), os.urandom(1200 * 1200 * 3)).save(ruta)

    service.set_payment_qr(ruta)

    crudo = service.payment_qr()
    assert crudo[:3] == b"\xff\xd8\xff"  # JPG
    assert len(crudo) <= 512 * 1024


def test_quitar_el_qr(service, tmp_path):
    service.set_payment_qr(_imagen(tmp_path, nombre="qr.png"))

    service.clear_payment_qr()

    assert service.payment_qr() is None


def test_un_qr_que_no_es_imagen_avisa(service, tmp_path):
    basura = tmp_path / "qr.png"
    basura.write_text("no soy una imagen", encoding="utf-8")

    with pytest.raises(ValueError, match="imagen"):
        service.set_payment_qr(basura)
