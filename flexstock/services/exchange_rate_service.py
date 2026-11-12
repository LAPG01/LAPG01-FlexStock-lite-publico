"""
Tasa BCV del dia, como referencia informativa para decidir precios (no
se usa para calcular nada del sistema: ni impuestos, ni totales de
factura - Regla 7 sigue aplicando solo sobre default_tax_rate).

Cachea en settings (bcv_rate, bcv_rate_updated_at): el BCV publica una
vez por dia habil, asi que no tiene sentido pegarle a la red cada vez
que se abre una pantalla. Solo se vuelve a consultar si la cache tiene
mas de un dia (o no existe todavia).

Ademas del dolar oficial (BCV) tambien se sigue el euro oficial. Son 2
tasas independientes - ver _CLAVES_POR_TIPO - cada una con su propia
clave en settings y su propio dia de cache.

Usa urllib.request (libreria estandar), no 'requests': es un solo GET
que devuelve JSON, no hace falta una dependencia nueva para eso. Es el
primer modulo del proyecto que toca la red - antes de esto, FlexStock-
lite corria 100% offline, y este modulo esta escrito para que un fallo
de red (sin conexion, API caida, JSON con otra forma) nunca rompa nada:
la UI se queda con la ultima tasa conocida, o "no disponible".

DISENO PARA TKINTER: fetch_rate_now() y companeros no tocan la conexion
a la base a proposito, para poder correr en un hilo aparte sin violar
la regla de sqlite3 de que una conexion solo se usa desde el hilo que
la abrio. La escritura a settings (apply_fetched_rate) la hace quien
llamo, de vuelta en el hilo principal.
"""

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

from ..data.connection import get_setting, set_setting

# USD/EUR oficiales: bcv.today scrapea bcv.org.ve directo (fuente de
# primera mano, un solo salto) y devuelve USD+EUR juntos en un solo
# objeto (una sola consulta cubre ambas, a diferencia del endpoint por
# moneda que tenia dolarapi.com). Trae ademas 'effective_date' separado
# de 'date' -el dia que el BCV publico esa tasa de verdad, contra el dia
# que bcv.today genero el archivo- justo lo que rate_display() necesita
# para no mostrar como "de hoy" una tasa vieja (ver _parse_bcv_today).
_BCV_TODAY_URL = "https://bcv.today/api/v1/rate.json"
# Segunda fuente del dolar/euro oficial, por si bcv.today se cae o se
# queda atras: dolarapi.com trae la misma tasa del BCV (un endpoint por
# moneda, con 'promedio' y 'fechaActualizacion'). Solo se consulta si la
# principal fallo o su fecha es anterior a hoy - ver _oficial_mas_reciente.
_DOLARAPI_URLS = {
    "USD": "https://ve.dolarapi.com/v1/dolares/oficial",
    "EUR": "https://ve.dolarapi.com/v1/euros/oficial",
}
# USDT/VES: dolarapi.com tiene un endpoint "paralelo", pero es un indice
# propio (no el precio de USDT en si) y no sirve para lo que se necesita
# aca. criptoya.com es una API publica y documentada (usada por varios
# "monitores" de la region) que agrega el precio de USDT/VES de varios
# exchanges P2P a la vez; se usa "binancep2p" porque Binance P2P es la
# referencia mas citada en Venezuela para USDT. No hay API oficial de
# Binance para esto (su P2P es un endpoint interno, sin documentar y mas
# fragil), asi que un agregador publico y estable es mejor opcion.
_USDT_VES_URL = "https://criptoya.com/api/USDT/VES"
_TIMEOUT_SECONDS = 5

# Cada "tipo" es una tasa independiente: su propia clave en settings, su
# propio dia de cache y su propia fuente (ver las URLs de arriba). Las 3
# tienen ademas carga manual desde la UI (ver MainWindow).
_CLAVES_POR_TIPO: dict[str, str] = {
    "usd_oficial": "bcv_rate",
    "eur_oficial": "bcv_rate_eur",
    "usdt": "bcv_rate_usdt",
}


class ExchangeRateService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def rate_display(self, tipo: str, etiqueta: str) -> str:
        """
        '{etiqueta}: 45,20 (03/09)' o '{etiqueta}: no disponible' si
        nunca se pudo obtener nada todavia. Solo lee settings: no
        dispara ninguna consulta de red por si sola.

        La fecha mostrada es la de PUBLICACION (la que informa la API,
        ej. cuando el BCV realmente actualizo esa tasa) y no la de
        CONSULTA (cuando esta app pregunto): son distintas -el BCV
        publica una vez por dia habil, asi que consultar hoy y recibir
        el mismo numero de ayer/viernes es normal. Mostrar la fecha de
        consulta haria parecer "de hoy" una tasa que en realidad tiene
        varios dias, justo la confusion que esto evita.
        """
        valor, fecha = self.rate_parts(tipo)
        return f"{etiqueta}: {valor}" + (f" ({fecha})" if fecha else "")

    def rate_parts(self, tipo: str) -> tuple[str, str]:
        """
        ('45,20', '03/09') - valor y fecha de publicacion por separado,
        para mostrarlos en renglones distintos (tarjetas del encabezado
        de MainWindow). ('no disponible', '') si nunca se obtuvo nada.
        """
        rate = self.current_rate(tipo)
        if not rate:
            return "no disponible", ""
        publicado_en = get_setting(self._conn, self._clave_publicacion(tipo), "")
        return rate.replace(".", ","), _formatear_fecha(publicado_en)

    def bcv_rate_display(self) -> str:
        return self.rate_display("usd_oficial", "Tasa BCV")

    def usdt_rate_display(self) -> str:
        return self.rate_display("usdt", "Tasa USDT")

    def current_rate(self, tipo: str = "usd_oficial") -> str:
        """
        Valor crudo cacheado ('45.20', formato Decimal con punto) o ''
        si no hay ninguno todavia. Para prellenar la carga manual desde
        la UI (ver MainWindow._on_editar_tasa_bcv) sin que la UI toque
        settings directamente.
        """
        return get_setting(self._conn, _CLAVES_POR_TIPO[tipo], "")

    def should_refresh(self, tipo: str = "usd_oficial") -> bool:
        """True si nunca se consulto ese tipo, o si fue otro dia."""
        updated_at = get_setting(self._conn, self._clave_fecha(tipo), "")
        if not updated_at:
            return True
        try:
            fecha_guardada = datetime.fromisoformat(updated_at).date()
        except ValueError:
            return True
        return fecha_guardada != date.today()  # noqa: DTZ011 - hora local, igual que el resto del esquema

    def apply_fetched_rate(
        self, rate: str, tipo: str = "usd_oficial", *, published_at: str | None = None
    ) -> None:
        """
        Guarda una tasa ya obtenida (ver fetch_rate_now y companeros,
        modulos aparte a proposito). Llamar SIEMPRE desde el hilo que
        abrio self._conn.

        Guarda DOS fechas con roles distintos:
        - _clave_fecha ("cuando la consultamos"): la usa should_refresh()
          para decidir si vale la pena volver a pegarle a la red - una
          vez por dia, sin importar si vino un numero nuevo o el mismo
          de siempre.
        - _clave_publicacion ("cuando se publico de verdad", ej. el
          fechaActualizacion que manda dolarapi.com): la que se MUESTRA
          en pantalla (ver rate_display). published_at=None -en la carga
          manual desde la UI, que no tiene ese dato- cae a la fecha de
          hoy, que ahi si es exacta: la persona la tipeo ahora mismo.
        """
        set_setting(self._conn, _CLAVES_POR_TIPO[tipo], rate)
        ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
        set_setting(self._conn, self._clave_fecha(tipo), ahora)
        set_setting(self._conn, self._clave_publicacion(tipo), published_at or ahora)

    def _clave_fecha(self, tipo: str) -> str:
        return f"{_CLAVES_POR_TIPO[tipo]}_updated_at"

    def _clave_publicacion(self, tipo: str) -> str:
        return f"{_CLAVES_POR_TIPO[tipo]}_published_at"


def _get_json(url: str) -> dict | None:
    """
    GET compartido por las 4 funciones fetch_*_now: mismo manejo de
    errores (sin red, timeout, HTTP raro, JSON invalido) para cualquier
    endpoint que devuelva un objeto JSON, sin importar la forma de cada
    uno (eso lo resuelve el _parse_* que corresponda).
    """
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "FlexStock-lite/1.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError):
        return None


def fetch_rate_now() -> tuple[str, str | None] | None:
    """
    Dolar oficial (BCV), via bcv.today y, si esa fallo o quedo atras,
    dolarapi.com (ver _oficial_mas_reciente). Devuelve (tasa,
    fecha_efectiva) - ej. ('832.49', '2026-09-11') - o None si ninguna
    fuente respondio (sin red, timeout, HTTP no-200, JSON con otra
    forma). fecha_efectiva puede ser None si el campo no vino en la
    respuesta. No toca sqlite3: pensada para correr en un hilo de fondo.
    """
    return _oficial_mas_reciente("USD")


def fetch_eur_oficial_now() -> tuple[str, str | None] | None:
    """Euro oficial (BCV), misma fuente/contrato que fetch_rate_now()."""
    return _oficial_mas_reciente("EUR")


def _oficial_mas_reciente(moneda: str) -> tuple[str, str | None] | None:
    """
    Consulta bcv.today (la principal); solo si fallo o su fecha es
    anterior a hoy consulta dolarapi.com, y se queda con la de fecha
    efectiva mas nueva. Si empatan gana bcv.today. Un dia habil con la
    principal al dia cuesta UNA sola consulta de red; un fin de semana
    (donde el BCV no publica y toda tasa es "vieja") cuesta dos.
    """
    data = _get_json(_BCV_TODAY_URL)
    principal = None if data is None else _parse_bcv_today(data, moneda)
    if principal is not None and _fecha_de(principal[1]) >= date.today():  # noqa: DTZ011 - hora local, igual que el resto del esquema
        return principal

    data = _get_json(_DOLARAPI_URLS[moneda])
    respaldo = None if data is None else _parse_dolarapi(data)

    candidatas = [c for c in (principal, respaldo) if c is not None]
    if not candidatas:
        return None
    # max() devuelve la primera entre empatadas: la principal.
    return max(candidatas, key=lambda c: _fecha_de(c[1]))


def _fecha_de(publicado_en: str | None) -> date:
    """'2026-09-18' o '2026-09-18T00:00:00-04:00' -> date. Sin fecha
    (o ilegible) -> date.min: pierde contra cualquier fecha real."""
    try:
        return date.fromisoformat((publicado_en or "")[:10])
    except ValueError:
        return date.min


def fetch_usdt_now() -> tuple[str, str | None] | None:
    """USDT/VES (Binance P2P, via criptoya.com). Mismo contrato que
    fetch_rate_now(), otra forma de respuesta - ver _parse_criptoya_price."""
    data = _get_json(_USDT_VES_URL)
    return None if data is None else _parse_criptoya_price(data)


def _parse_bcv_today(data: dict, moneda: str) -> tuple[str, str | None] | None:
    """
    Separado de fetch_rate_now()/fetch_eur_oficial_now() para poder
    probarlo con un JSON de ejemplo, sin red de por medio. Si bcv.today
    cambia la forma de su respuesta, el arreglo es ajustar esta funcion
    sola. 'moneda': "USD" o "EUR", la clave dentro del mismo objeto.
    """
    try:
        precio = data[moneda]
        valor = Decimal(str(precio)).quantize(Decimal("0.01"))
    except (KeyError, TypeError, InvalidOperation):
        return None

    fecha_publicacion = data.get("effective_date")
    if not isinstance(fecha_publicacion, str):
        fecha_publicacion = None

    return str(valor), fecha_publicacion


def _parse_dolarapi(data: dict) -> tuple[str, str | None] | None:
    """
    dolarapi.com/v1/{dolares,euros}/oficial devuelve un objeto por
    moneda: 'promedio' (la tasa) y 'fechaActualizacion' (la fecha que
    informa la fuente, ej. '2026-09-18T00:00:00-04:00'). Mismo contrato
    que _parse_bcv_today: (tasa, fecha) o None si la forma no es la
    esperada.
    """
    try:
        valor = Decimal(str(data["promedio"])).quantize(Decimal("0.01"))
    except (KeyError, TypeError, InvalidOperation):
        return None
    if valor <= 0:
        return None

    fecha = data.get("fechaActualizacion")
    return str(valor), fecha if isinstance(fecha, str) else None


def _parse_criptoya_price(data: dict) -> tuple[str, str | None] | None:
    """
    criptoya.com/api/USDT/VES devuelve un objeto por exchange (ask/bid/
    time en epoch). Se usa "binancep2p": Binance P2P es la referencia
    de USDT mas citada en Venezuela, y el promedio de compra/venta da
    un numero unico, mismo criterio que "promedio" en _parse_bcv_price.
    """
    try:
        binance = data["binancep2p"]
        ask = Decimal(str(binance["ask"]))
        bid = Decimal(str(binance["bid"]))
        valor = ((ask + bid) / 2).quantize(Decimal("0.01"))
    except (KeyError, TypeError, InvalidOperation):
        return None

    fecha_publicacion = None
    marca_de_tiempo = binance.get("time")
    if isinstance(marca_de_tiempo, (int, float)):
        try:
            fecha_publicacion = datetime.fromtimestamp(marca_de_tiempo).isoformat()  # noqa: DTZ006
        except (OSError, OverflowError, ValueError):
            fecha_publicacion = None

    return str(valor), fecha_publicacion


def _formatear_fecha(iso_datetime: str) -> str:
    if not iso_datetime:
        return ""
    try:
        return datetime.fromisoformat(iso_datetime).strftime("%d/%m")
    except ValueError:
        return ""
