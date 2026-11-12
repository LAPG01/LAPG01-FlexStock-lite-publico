"""
Tests de ExchangeRateService. La red se mockea siempre: nunca se le
pega de verdad a bcv.today/criptoya.com desde el suite (ni deberia, ni
este entorno tiene salida a internet).

    pytest -q
"""

import sqlite3
from datetime import date, datetime, timedelta

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.services.exchange_rate_service import (
    ExchangeRateService,
    _parse_bcv_today,
    _parse_criptoya_price,
    _parse_dolarapi,
    fetch_eur_oficial_now,
    fetch_rate_now,
    fetch_usdt_now,
)


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
    return ExchangeRateService(conn)


# ------------------------------------------------------------
# bcv_rate_display: solo lectura de settings
# ------------------------------------------------------------
def test_display_sin_tasa_guardada(service):
    assert service.bcv_rate_display() == "Tasa BCV: no disponible"


def test_display_con_tasa_guardada(conn, service):
    set_setting(conn, "bcv_rate", "45.20")
    set_setting(conn, "bcv_rate_published_at", "2026-09-03 08:00:00")

    assert service.bcv_rate_display() == "Tasa BCV: 45,20 (03/09)"


def test_display_usa_la_fecha_de_publicacion_no_la_de_consulta(conn, service):
    """
    Regresion: el BCV publica una vez por dia habil. Si la app consulto
    hoy pero recibio la misma tasa del viernes, tiene que seguir
    mostrando la fecha del viernes (cuando el BCV la publico de
    verdad), no la de hoy - mostrar "hoy" haria parecer actualizada una
    tasa de varios dias.
    """
    set_setting(conn, "bcv_rate", "45.20")
    set_setting(conn, "bcv_rate_updated_at", "2026-09-08 09:00:00")  # consulta: hoy
    set_setting(conn, "bcv_rate_published_at", "2026-09-05 00:00:00")  # BCV: viernes

    assert service.bcv_rate_display() == "Tasa BCV: 45,20 (05/09)"


def test_display_con_tasa_pero_sin_fecha(conn, service):
    set_setting(conn, "bcv_rate", "45.20")

    assert service.bcv_rate_display() == "Tasa BCV: 45,20"


def test_rate_parts_separa_valor_y_fecha(conn, service):
    set_setting(conn, "bcv_rate", "45.20")
    set_setting(conn, "bcv_rate_published_at", "2026-09-03 08:00:00")

    assert service.rate_parts("usd_oficial") == ("45,20", "03/09")


def test_rate_parts_sin_tasa(service):
    assert service.rate_parts("usdt") == ("no disponible", "")


# ------------------------------------------------------------
# should_refresh
# ------------------------------------------------------------
def test_should_refresh_true_si_nunca_se_consulto(service):
    assert service.should_refresh() is True


def test_should_refresh_false_si_ya_se_consulto_hoy(conn, service):
    ahora = datetime.now().strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    set_setting(conn, "bcv_rate_updated_at", ahora)

    assert service.should_refresh() is False


def test_should_refresh_true_si_la_cache_es_de_otro_dia(conn, service):
    ayer = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d %H:%M:%S")  # noqa: DTZ005
    set_setting(conn, "bcv_rate_updated_at", ayer)

    assert service.should_refresh() is True


def test_should_refresh_true_si_la_fecha_guardada_es_invalida(conn, service):
    set_setting(conn, "bcv_rate_updated_at", "no-es-una-fecha")

    assert service.should_refresh() is True


# ------------------------------------------------------------
# apply_fetched_rate
# ------------------------------------------------------------
def test_apply_fetched_rate_guarda_y_marca_hoy(conn, service):
    service.apply_fetched_rate("45.20")

    assert service.bcv_rate_display().startswith("Tasa BCV: 45,20")
    assert service.should_refresh() is False


def test_apply_fetched_rate_sin_published_at_usa_hoy(service):
    """Carga manual desde la UI (MainWindow._on_editar_tasa_bcv): no hay
    fecha de publicacion de una API, asi que cae a hoy - exacta, la
    persona la tipeo ahora."""
    service.apply_fetched_rate("45.20")

    hoy = datetime.now().strftime("%d/%m")  # noqa: DTZ005
    assert service.bcv_rate_display() == f"Tasa BCV: 45,20 ({hoy})"


def test_apply_fetched_rate_con_published_at_lo_usa_para_mostrar(service):
    service.apply_fetched_rate("45.20", published_at="2026-09-05T00:00:00-04:00")

    assert service.bcv_rate_display() == "Tasa BCV: 45,20 (05/09)"


# ------------------------------------------------------------
# _parse_bcv_today: forma real verificada contra bcv.today
# (GET https://bcv.today/api/v1/rate.json) el 2026-09-14. USD y EUR
# vienen juntos en el mismo objeto (una sola consulta cubre las 2).
# ------------------------------------------------------------
def test_parse_bcv_today_forma_esperada():
    data = {
        "USD": 832.4883,
        "EUR": 968.06734453,
        "updated_at": "2026-09-10T21:20:41.135709+00:00",
        "effective_date": "2026-09-11",
        "date": "2026-09-14",
    }

    assert _parse_bcv_today(data, "USD") == ("832.49", "2026-09-11")
    assert _parse_bcv_today(data, "EUR") == ("968.07", "2026-09-11")


def test_parse_bcv_today_redondea_a_dos_decimales():
    data = {"USD": 45.198}

    assert _parse_bcv_today(data, "USD") == ("45.20", None)


def test_parse_bcv_today_sin_fecha_efectiva_da_none():
    data = {"USD": 45.2, "effective_date": None}

    assert _parse_bcv_today(data, "USD") == ("45.20", None)


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"USD": None},
        {"USD": "no-es-un-numero"},
        {"otra_forma_de_json": True},
    ],
)
def test_parse_bcv_today_forma_inesperada_devuelve_none(data):
    assert _parse_bcv_today(data, "USD") is None


# ------------------------------------------------------------
# fetch_rate_now / fetch_eur_oficial_now: red mockeada
# ------------------------------------------------------------
def test_fetch_rate_now_exito(monkeypatch):
    import json
    from io import BytesIO

    respuesta = json.dumps(
        {"USD": 45.2, "EUR": 50.1, "effective_date": "2026-09-14"}
    ).encode("utf-8")

    class _RespuestaFalsa:
        def __enter__(self):
            return BytesIO(respuesta)

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(
        "flexstock.services.exchange_rate_service.urllib.request.urlopen",
        lambda *a, **k: _RespuestaFalsa(),
    )

    assert fetch_rate_now() == ("45.20", "2026-09-14")
    assert fetch_eur_oficial_now() == ("50.10", "2026-09-14")


def test_fetch_rate_now_sin_red_devuelve_none(monkeypatch):
    def _sin_conexion(*_a, **_k):
        raise OSError("sin red")

    monkeypatch.setattr(
        "flexstock.services.exchange_rate_service.urllib.request.urlopen",
        _sin_conexion,
    )

    assert fetch_rate_now() is None
    assert fetch_eur_oficial_now() is None


def test_fetch_rate_now_json_invalido_devuelve_none(monkeypatch):
    from io import BytesIO

    class _RespuestaFalsa:
        def __enter__(self):
            return BytesIO(b"esto no es json")

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(
        "flexstock.services.exchange_rate_service.urllib.request.urlopen",
        lambda *a, **k: _RespuestaFalsa(),
    )

    assert fetch_rate_now() is None


# ------------------------------------------------------------
# USDT/VES: mismo contrato que fetch_rate_now(), pero otra fuente
# (criptoya.com/api/USDT/VES, agrega Binance P2P y otros exchanges) con
# otra forma de respuesta - ver _parse_criptoya_price.
# ------------------------------------------------------------
def test_usdt_rate_display_sin_tasa_guardada(service):
    assert service.usdt_rate_display() == "Tasa USDT: no disponible"


def test_parse_criptoya_price_promedia_bid_y_ask_de_binance():
    data = {
        "binancep2p": {"ask": 953.2, "bid": 954, "time": 1789422754},
        "okexp2p": {"ask": 951.9, "bid": 951, "time": 1789422779},
    }

    tasa, fecha = _parse_criptoya_price(data)

    assert tasa == "953.60"  # (953.2 + 954) / 2
    assert fecha == datetime.fromtimestamp(1789422754).isoformat()  # noqa: DTZ006


@pytest.mark.parametrize(
    "data",
    [
        {},
        {"binancep2p": {}},
        {"binancep2p": {"ask": "no-es-un-numero", "bid": 954}},
        {"otra_forma_de_json": True},
    ],
)
def test_parse_criptoya_price_forma_inesperada_devuelve_none(data):
    assert _parse_criptoya_price(data) is None


def test_fetch_usdt_now_exito(monkeypatch):
    import json
    from io import BytesIO

    respuesta = json.dumps(
        {"binancep2p": {"ask": 953.2, "bid": 954, "time": 1789422754}}
    ).encode("utf-8")

    class _RespuestaFalsa:
        def __enter__(self):
            return BytesIO(respuesta)

        def __exit__(self, *_a):
            return False

    monkeypatch.setattr(
        "flexstock.services.exchange_rate_service.urllib.request.urlopen",
        lambda *a, **k: _RespuestaFalsa(),
    )

    assert fetch_usdt_now() == (
        "953.60",
        datetime.fromtimestamp(1789422754).isoformat(),  # noqa: DTZ006
    )


def test_usdt_es_independiente_de_la_tasa_oficial(service):
    """Cache/dia/fecha propios: consultar 'usdt' no debe pisar ni
    depender de 'usd_oficial'."""
    service.apply_fetched_rate("45.20", "usd_oficial", published_at="2026-09-14")
    service.apply_fetched_rate("94.35", "usdt", published_at="2026-09-14")

    assert service.bcv_rate_display() == "Tasa BCV: 45,20 (14/09)"
    assert service.usdt_rate_display() == "Tasa USDT: 94,35 (14/09)"
    assert service.should_refresh("usdt") is False
    assert service.current_rate("usdt") == "94.35"


# ------------------------------------------------------------
# Segunda fuente (dolarapi.com) para el dolar/euro oficial
# ------------------------------------------------------------
def test_parse_dolarapi_forma_esperada():
    data = {
        "moneda": "USD",
        "promedio": 848.5458,
        "fechaActualizacion": "2026-09-18T00:00:00-04:00",
    }

    assert _parse_dolarapi(data) == ("848.55", "2026-09-18T00:00:00-04:00")


def test_parse_dolarapi_sin_fecha_da_none_en_la_fecha():
    assert _parse_dolarapi({"promedio": 45.2}) == ("45.20", None)


@pytest.mark.parametrize(
    "data",
    [{}, {"promedio": None}, {"promedio": "no es numero"}, {"promedio": 0}],
)
def test_parse_dolarapi_forma_inesperada_devuelve_none(data):
    assert _parse_dolarapi(data) is None


def _fuentes_falsas(monkeypatch, *, principal, respaldo):
    """
    Reemplaza _get_json: bcv.today devuelve 'principal' y dolarapi
    devuelve 'respaldo' (un dict, o None = sin conexion). Devuelve la
    lista de URLs consultadas, para comprobar que se le pego a la red
    solo cuando hacia falta.
    """
    llamadas = []

    def _falso(url):
        llamadas.append(url)
        return principal if "bcv.today" in url else respaldo

    monkeypatch.setattr("flexstock.services.exchange_rate_service._get_json", _falso)
    return llamadas


def _bcv_today(usd, fecha):
    return {"USD": usd, "EUR": usd + 100, "effective_date": fecha}


def _dolarapi(promedio, fecha):
    return {"promedio": promedio, "fechaActualizacion": f"{fecha}T00:00:00-04:00"}


def _dia(dias_atras=0):
    """'2026-09-18' de hace 'dias_atras' dias, en hora local: la misma
    que usa el servicio para decidir si una tasa quedo "vieja"."""
    return (date.today() - timedelta(days=dias_atras)).isoformat()  # noqa: DTZ011


def test_principal_al_dia_no_consulta_la_segunda_fuente(monkeypatch):
    hoy = _dia()
    llamadas = _fuentes_falsas(
        monkeypatch, principal=_bcv_today(45.2, hoy), respaldo=_dolarapi(99, hoy)
    )

    assert fetch_rate_now() == ("45.20", hoy)
    assert len(llamadas) == 1  # una sola consulta de red


def test_principal_atrasada_y_respaldo_mas_nuevo_gana_el_respaldo(monkeypatch):
    ayer, hoy = _dia(1), _dia()
    llamadas = _fuentes_falsas(
        monkeypatch, principal=_bcv_today(45.2, ayer), respaldo=_dolarapi(46.5, hoy)
    )

    assert fetch_rate_now() == ("46.50", f"{hoy}T00:00:00-04:00")
    assert len(llamadas) == 2


def test_principal_atrasada_y_respaldo_igual_gana_la_principal(monkeypatch):
    ayer = _dia(1)
    _fuentes_falsas(
        monkeypatch, principal=_bcv_today(45.2, ayer), respaldo=_dolarapi(45.21, ayer)
    )

    assert fetch_rate_now() == ("45.20", ayer)


def test_principal_atrasada_y_respaldo_mas_viejo_gana_la_principal(monkeypatch):
    ayer, antier = _dia(1), _dia(2)
    _fuentes_falsas(
        monkeypatch, principal=_bcv_today(45.2, ayer), respaldo=_dolarapi(44.0, antier)
    )

    assert fetch_rate_now() == ("45.20", ayer)


def test_si_la_principal_falla_se_usa_la_segunda_fuente(monkeypatch):
    ayer = _dia(1)
    _fuentes_falsas(monkeypatch, principal=None, respaldo=_dolarapi(46.5, ayer))

    assert fetch_rate_now() == ("46.50", f"{ayer}T00:00:00-04:00")


def test_si_falla_la_segunda_fuente_se_queda_la_principal_atrasada(monkeypatch):
    ayer = _dia(1)
    _fuentes_falsas(monkeypatch, principal=_bcv_today(45.2, ayer), respaldo=None)

    assert fetch_rate_now() == ("45.20", ayer)


def test_si_fallan_las_dos_fuentes_devuelve_none(monkeypatch):
    _fuentes_falsas(monkeypatch, principal=None, respaldo=None)

    assert fetch_rate_now() is None
    assert fetch_eur_oficial_now() is None


def test_el_euro_consulta_el_endpoint_de_euros_del_respaldo(monkeypatch):
    ayer = _dia(1)
    llamadas = _fuentes_falsas(
        monkeypatch, principal=_bcv_today(45.2, ayer), respaldo=_dolarapi(51, ayer)
    )

    fetch_eur_oficial_now()

    assert llamadas[-1].endswith("/euros/oficial")


# ------------------------------------------------------------
# La guarda de red de conftest.py
# ------------------------------------------------------------
def test_la_suite_no_puede_salir_a_la_red_sin_querer():
    """
    conftest.py parchea urlopen para TODA la suite: un test que llame a
    la red de verdad falla con un mensaje claro en vez de tardar, salir
    a internet y dar un resultado distinto segun el dia. Los tests de
    aca arriba prueban la red justamente porque la parchean ellos
    mismos, lo que pisa esta guarda.
    """
    import urllib.request

    with pytest.raises(AssertionError, match="salir a la red"):
        urllib.request.urlopen("https://ejemplo.invalido/")
