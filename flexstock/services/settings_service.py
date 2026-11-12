"""
Preferencias de la aplicacion que no encajan en ningun otro servicio:
los datos de la tienda (los que salen en la factura), tema visual
(claro/oscuro), Modo avanzado y que columnas muestra cada listado.
Mismo patron minimo que ExchangeRateService: lee/escribe 'settings'
directo, sin repositorio, porque es una tabla clave/valor y no una
entidad con reglas de negocio.

EL LOGO VIVE EN LA BASE, como texto base64 en settings, no como un
archivo suelto al lado. Tres razones:
  - el respaldo copia la base entera, asi que se lleva el logo solo;
    un archivo aparte habria que acordarse de copiarlo a mano;
  - mover la base a otra PC (o a Windows) se lleva el logo con ella;
  - si alguien borra o mueve la imagen original, las facturas siguen
    saliendo igual.
Un logo normalizado pesa ~20 KB en base64: nada para una fila de
settings, y ReportLab lo lee desde memoria sin tocar el disco.
"""

import base64
import binascii
import io
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..data.connection import get_setting, set_setting

_TEMAS_VALIDOS = ("light", "dark")
# 150 % estuvo afuera porque escondia listas y botones en Nueva factura y
# Nueva cotizacion; volvio cuando esos formularios se compactaron (cliente
# que se pliega al iniciar, filas combinadas, Vista previa abajo).
FONT_SCALES = (90, 100, 110, 125, 150)

# Lado largo al que se reduce el logo antes de guardarlo. 600 px alcanza
# de sobra para los 2,5 cm que ocupa en el papel (a 300 dpi son ~300 px)
# y evita meter una foto de 4 MB del telefono en una fila de settings.
_LADO_MAXIMO = 600
# Techo duro, por si una imagen rarisima no baja de tamano al reducirla.
_MAXIMO_BYTES = 512 * 1024
# El QR de pago: 800 px de lado largo. Impreso ocupa ~3 cm; con 800 px
# cada cuadrito del QR queda nitido aunque la imagen sea una foto.
_LADO_MAXIMO_QR = 800


@dataclass(frozen=True, slots=True)
class StoreInfo:
    """Lo que identifica a la tienda en el papel que recibe el cliente."""

    name: str = ""
    tax_id: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    # Razon social: el nombre LEGAL, aparte del nombre comercial de arriba.
    legal_name: str = ""
    web: str = ""  # pagina web y/o redes ("@laesquina"), una linea
    # Textos libres de varias lineas que salen al pie de los documentos:
    payment_info: str = ""  # pago movil, cuenta, Zelle...
    footer_text: str = ""  # garantia, condiciones de devolucion...


def store_info_for_pdf(conn: sqlite3.Connection) -> dict[str, str]:
    """
    Los datos de la tienda que los PDF aceptan de mas (ademas de nombre,
    RIF, telefono y logo): como kwargs de render_invoice / render_quotation
    / render_preview. Un solo lugar para que facturas, notas de entrega y
    cotizaciones impriman lo mismo sin que cada servicio repita las claves.
    """
    info = SettingsService(conn).store_info()
    return {
        "company_address": info.address,
        "company_email": info.email,
        "company_legal_name": info.legal_name,
        "company_web": info.web,
        "company_payment_info": info.payment_info,
        "company_footer": info.footer_text,
    }


def _normalizar_imagen(origen: Path, lado_maximo: int) -> bytes:
    """Abre la imagen (cualquier formato que lea Pillow), la achica a
    'lado_maximo' sin agrandarla nunca y la devuelve en PNG."""
    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(origen) as imagen:
            imagen.load()  # fuerza la lectura: detecta un archivo truncado
            normalizada = imagen.convert("RGBA")
    except (UnidentifiedImageError, OSError) as error:
        raise ValueError(
            f"No se pudo leer la imagen: {error}. "
            "Tiene que ser un archivo de imagen (PNG, JPG...)."
        ) from error
    normalizada.thumbnail((lado_maximo, lado_maximo))
    buffer = io.BytesIO()
    normalizada.save(buffer, "PNG")
    return buffer.getvalue()


def _a_jpg(png: bytes) -> bytes:
    """PNG -> JPG calidad 90, con lo transparente sobre blanco (JPG no
    tiene transparencia; en el papel el fondo es blanco igual)."""
    from PIL import Image

    with Image.open(io.BytesIO(png)) as imagen:
        fondo = Image.new("RGB", imagen.size, "white")
        fondo.paste(imagen, mask=imagen.getchannel("A"))
    buffer = io.BytesIO()
    fondo.save(buffer, "JPEG", quality=90)
    return buffer.getvalue()


class SettingsService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Datos de la tienda (Configuracion > Tienda)
    # ------------------------------------------------------------
    def store_info(self) -> StoreInfo:
        """
        Lo que va en el encabezado de la factura. Las mismas claves que
        lee BillingService para armar el PDF (ver _CLAVES_TIENDA alla):
        lo que se guarda aca es exactamente lo que sale impreso.
        """
        return StoreInfo(
            name=get_setting(self._conn, "company_name", "Ferreteria"),
            tax_id=get_setting(self._conn, "company_tax_id", ""),
            address=get_setting(self._conn, "company_address", ""),
            phone=get_setting(self._conn, "company_phone", ""),
            email=get_setting(self._conn, "company_email", ""),
            legal_name=get_setting(self._conn, "company_legal_name", ""),
            web=get_setting(self._conn, "company_web", ""),
            payment_info=get_setting(self._conn, "company_payment_info", ""),
            footer_text=get_setting(self._conn, "company_footer", ""),
        )

    def set_store_info(self, info: StoreInfo) -> None:
        """
        El nombre es obligatorio: es lo unico del encabezado que no
        puede quedar en blanco sin que la factura quede anonima. El
        RIF no se valida con formato a proposito - hay RIF (J-, V-, G-)
        y tambien quien todavia no lo tiene, y frenar una venta por eso
        seria peor que imprimirlo como venga.
        """
        nombre = info.name.strip()
        if not nombre:
            raise ValueError("El nombre de la tienda no puede quedar vacío.")

        set_setting(self._conn, "company_name", nombre)
        set_setting(self._conn, "company_tax_id", info.tax_id.strip())
        set_setting(self._conn, "company_address", info.address.strip())
        set_setting(self._conn, "company_phone", info.phone.strip())
        set_setting(self._conn, "company_email", info.email.strip())
        set_setting(self._conn, "company_legal_name", info.legal_name.strip())
        set_setting(self._conn, "company_web", info.web.strip())
        set_setting(self._conn, "company_payment_info", info.payment_info.strip())
        set_setting(self._conn, "company_footer", info.footer_text.strip())

    # ------------------------------------------------------------
    # Logo
    # ------------------------------------------------------------
    def logo(self) -> bytes | None:
        """
        El logo listo para usar (PNG), o None si no hay ninguno. Si lo
        guardado no se puede decodificar -base editada a mano, fila
        truncada- devuelve None en vez de romper: una factura sin logo
        se imprime igual, una excepcion acá no dejaria imprimir nada.
        """
        guardado = get_setting(self._conn, "company_logo", "")
        if not guardado:
            return None
        try:
            return base64.b64decode(guardado, validate=True)
        except (binascii.Error, ValueError):
            return None

    def set_logo(self, origen: Path) -> None:
        """
        Toma la imagen que eligio el usuario (PNG, JPG, lo que Pillow
        sepa abrir), la normaliza y la guarda.

        Normalizar = pasarla a PNG y reducirla a _LADO_MAXIMO. Asi el
        resto del sistema trabaja con UN solo formato (nada de "si es
        JPG hace esto"), el PNG conserva la transparencia del logo, y
        una foto de 4 MB no termina adentro de una fila de settings.

        Pillow no es una dependencia nueva: ReportLab la exige, asi que
        ya esta instalada en cualquier PC donde corra la aplicacion.
        """
        crudo = _normalizar_imagen(origen, _LADO_MAXIMO)
        if len(crudo) > _MAXIMO_BYTES:
            raise ValueError(
                "La imagen sigue pesando demasiado después de reducirla. "
                "Prueba con un logo más simple."
            )
        set_setting(self._conn, "company_logo", base64.b64encode(crudo).decode("ascii"))

    def clear_logo(self) -> None:
        """Sin logo, la factura imprime solo los datos de la tienda."""
        set_setting(self._conn, "company_logo", "")

    # ------------------------------------------------------------
    # QR de pago (sale junto a "Datos de pago" en facturas y notas)
    # ------------------------------------------------------------
    def payment_qr(self) -> bytes | None:
        """
        La imagen del QR de pago (PNG, o JPG si era una foto pesada), o
        None si no hay. Mismo criterio que logo(): algo ilegible vuelve
        None y el documento se imprime igual, sin QR.
        """
        guardado = get_setting(self._conn, "company_payment_qr", "")
        if not guardado:
            return None
        try:
            return base64.b64decode(guardado, validate=True)
        except (binascii.Error, ValueError):
            return None

    def set_payment_qr(self, origen: Path) -> None:
        """
        Guarda el QR (o la foto del QR) dentro de la base, igual que el
        logo: el respaldo se lo lleva y no depende del archivo original.

        Un QR "limpio" (cuadritos negros sobre blanco o transparente) queda
        en PNG, nitido y liviano. Una FOTO en PNG puede pesar mas de
        medio mega: en ese caso se guarda como JPG de buena calidad, que
        para una foto pesa una fraccion y se ve igual. ReportLab y Pillow
        reconocen el formato solos por el contenido.
        """
        crudo = _normalizar_imagen(origen, _LADO_MAXIMO_QR)
        if len(crudo) > _MAXIMO_BYTES:
            crudo = _a_jpg(crudo)
        if len(crudo) > _MAXIMO_BYTES:
            raise ValueError(
                "La imagen sigue pesando demasiado después de reducirla. "
                "Prueba recortándola para que se vea solo el QR."
            )
        set_setting(
            self._conn, "company_payment_qr", base64.b64encode(crudo).decode("ascii")
        )

    def clear_payment_qr(self) -> None:
        set_setting(self._conn, "company_payment_qr", "")

    def ui_theme(self) -> str:
        tema = get_setting(self._conn, "ui_theme", "light")
        return tema if tema in _TEMAS_VALIDOS else "light"

    def set_ui_theme(self, theme: str) -> None:
        if theme not in _TEMAS_VALIDOS:
            raise ValueError(f"Tema desconocido: {theme!r}")
        set_setting(self._conn, "ui_theme", theme)

    def font_scale(self) -> int:
        """Porcentaje de letra elegido para toda la interfaz."""
        try:
            scale = int(get_setting(self._conn, "font_scale", "100"))
        except ValueError:
            return 100
        return scale if scale in FONT_SCALES else 100

    def set_font_scale(self, scale: int) -> None:
        if scale not in FONT_SCALES:
            raise ValueError(f"Tamano de letra desconocido: {scale!r}")
        set_setting(self._conn, "font_scale", str(scale))

    def advanced_mode(self) -> bool:
        """
        Hoy solo controla si se ve el toggle de IVA por documento (ver
        Configuracion > Modo avanzado); apagado por defecto para no
        agregar controles que la mayoria de dias no hacen falta.
        """
        return get_setting(self._conn, "advanced_mode", "0") == "1"

    def set_advanced_mode(self, enabled: bool) -> None:
        set_setting(self._conn, "advanced_mode", "1" if enabled else "0")

    def sidebar_collapsed(self) -> bool:
        """Si la barra lateral quedo plegada (solo iconos) la ultima vez."""
        return get_setting(self._conn, "sidebar_collapsed", "0") == "1"

    def set_sidebar_collapsed(self, collapsed: bool) -> None:
        set_setting(self._conn, "sidebar_collapsed", "1" if collapsed else "0")

    def allow_initial_stock(self) -> bool:
        """
        Si Nuevo producto ofrece cargar la existencia inicial
        (Configuracion > Inventario). Prendido por defecto.

        Es el reemplazo TEMPORAL de un permiso por rol (decidido el
        22/10/2026): mientras no haya usuarios, decide si el campo se ve,
        no quien lo puede usar. Cuando haya roles, esta pregunta pasa a
        ser "puede este usuario?" y el resto del codigo no cambia.
        """
        return get_setting(self._conn, "allow_initial_stock", "1") == "1"

    def set_allow_initial_stock(self, enabled: bool) -> None:
        set_setting(self._conn, "allow_initial_stock", "1" if enabled else "0")

    def visible_columns(self, modulo: str) -> tuple[str, ...] | None:
        """
        Columnas elegidas para ese listado (ver Configuracion >
        Personalizacion), o None si nunca se personalizo - que no es lo
        mismo que una seleccion vacia. Se guardan como CSV de claves;
        el orden guardado no importa, lo fija el catalogo de ui/columns.py.
        """
        guardado = get_setting(self._conn, f"columns_{modulo}", "")
        if not guardado:
            return None
        return tuple(clave for clave in guardado.split(",") if clave)

    def set_visible_columns(self, modulo: str, keys: tuple[str, ...]) -> None:
        set_setting(self._conn, f"columns_{modulo}", ",".join(keys))

    def reset_visible_columns(self, modulo: str) -> None:
        """Vuelve al default del catalogo (borra la preferencia guardada)."""
        set_setting(self._conn, f"columns_{modulo}", "")

    def last_dir(self, accion: str) -> str:
        """
        Ultima carpeta elegida para una accion de destino puntual
        (respaldo, exportar catalogo, exportar facturas - ver
        ui/backup_dialog.py). Es el initialdir de
        filedialog.askdirectory(): sin esto, el dialogo siempre arranca
        en la misma carpeta de siempre en vez de donde se dejo la
        ultima vez. '' (que Tk trata como "sin preferencia") si esa
        accion nunca eligio una.
        """
        return get_setting(self._conn, f"last_dir_{accion}", "")

    def set_last_dir(self, accion: str, carpeta: str) -> None:
        set_setting(self._conn, f"last_dir_{accion}", carpeta)
