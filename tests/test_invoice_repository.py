"""
Tests de InvoiceRepository. SQLite en memoria, igual que los demas.

A diferencia de product_repository.py/customer_repository.py, create()
y mark_cancelled() no abren su propia transaccion (ver docstring del
modulo): los tests la abren a mano con 'with transaction(conn):' para
simular como los va a usar BillingService.

    pytest -q
"""

import sqlite3
from datetime import datetime
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema, transaction
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.domain.entities import Invoice, InvoiceLine
from flexstock.domain.enums import PaymentMethod
from tests._datos_viejos import editar_factura_vieja


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repo(conn):
    return InvoiceRepository(conn)


def _crear_producto(conn, sku="FER-0001", name="Tornillo"):
    cur = conn.execute("INSERT INTO products (sku, name) VALUES (?, ?)", (sku, name))
    return cur.lastrowid


def _crear_cliente(conn, name="Juan"):
    cur = conn.execute(
        "INSERT INTO customers (sku, name) VALUES (?, ?)", ("CLI-0001", name)
    )
    return cur.lastrowid


def _factura(customer_id, *, lines=None, payment_method=None):
    return Invoice(
        id=None,
        display_number="",
        customer_name="Juan",
        tax_rate=Decimal("16.00"),
        customer_id=customer_id,
        lines=lines or [],
        payment_method=payment_method,
    )


def _linea(product_id, *, sku="FER-0001", name="Tornillo", qty=3000, price_cents=150):
    return InvoiceLine(
        id=None,
        product_id=product_id,
        product_sku=sku,
        product_name=name,
        unit="UNI",
        quantity_milli=qty,
        unit_price_cents=price_cents,
    )


# ------------------------------------------------------------
# create: numeracion
# ------------------------------------------------------------
def test_create_asigna_numero_desde_document_series(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid)

    with transaction(conn):
        repo.create(factura)

    assert factura.number == 1
    assert factura.display_number == "FAC-000001"
    assert factura.series_id is not None


def test_create_sin_issued_at_usa_ahora(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid)
    assert factura.issued_at is None

    with transaction(conn):
        repo.create(factura)

    assert factura.issued_at is not None
    recargada = repo.get(factura.id)
    assert recargada.issued_at is not None


def test_create_respeta_el_issued_at_que_ya_trae_la_factura(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid)
    factura.issued_at = datetime(2024, 1, 15, 9, 30)  # noqa: DTZ001

    with transaction(conn):
        repo.create(factura)

    recargada = repo.get(factura.id)
    assert recargada.issued_at == datetime(2024, 1, 15, 9, 30)  # noqa: DTZ001


def test_create_incrementa_next_number_en_document_series(conn, repo):
    cid = _crear_cliente(conn)

    with transaction(conn):
        repo.create(_factura(cid))
    with transaction(conn):
        segunda = _factura(cid)
        repo.create(segunda)

    assert segunda.number == 2
    assert segunda.display_number == "FAC-000002"


def test_create_serie_inexistente_lanza_valueerror(conn, repo):
    cid = _crear_cliente(conn)

    with pytest.raises(ValueError, match="serie"), transaction(conn):
        repo.create(_factura(cid), doc_type="NO_EXISTE")


# ------------------------------------------------------------
# create: factura + lineas
# ------------------------------------------------------------
def test_create_inserta_factura_y_lineas(conn, repo):
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    factura = _factura(cid, lines=[_linea(pid, qty=3000, price_cents=150)])

    with transaction(conn):
        repo.create(factura)

    assert factura.id is not None
    assert factura.lines[0].id is not None

    fila = conn.execute("SELECT * FROM invoices WHERE id = ?", (factura.id,)).fetchone()
    assert fila["customer_name"] == "Juan"
    assert fila["subtotal_cents"] == 450  # 150 * 3 unidades
    assert fila["tax_cents"] == 72  # 450 * 16%
    assert fila["total_cents"] == 522

    lineas = conn.execute(
        "SELECT * FROM invoice_lines WHERE invoice_id = ?", (factura.id,)
    ).fetchall()
    assert len(lineas) == 1
    assert lineas[0]["line_total_cents"] == 450


def test_create_snapshotea_precio_aunque_el_producto_cambie_despues(conn, repo):
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    factura = _factura(cid, lines=[_linea(pid, price_cents=150)])

    with transaction(conn):
        repo.create(factura)

    conn.execute("UPDATE products SET name = 'OTRO NOMBRE' WHERE id = ?", (pid,))

    recuperada = repo.get(factura.id)
    assert recuperada.lines[0].product_name == "Tornillo"
    assert recuperada.lines[0].unit_price_cents == 150


# ------------------------------------------------------------
# get / list_summary
# ------------------------------------------------------------
def test_la_tasa_en_bs_se_guarda_y_se_lee_exacta(conn, repo):
    """Snapshot de la tasa con que se paso la factura a Bs (regla 5): un
    Decimal exacto, no un float que cambie un centavo al reimprimir."""
    factura = _factura(_crear_cliente(conn))
    factura.bs_rate = Decimal("848.5458")
    with transaction(conn):
        factura_id = repo.create(factura)

    assert repo.get(factura_id).bs_rate == Decimal("848.5458")
    assert repo.list_summary()[0].bs_rate == Decimal("848.5458")
    assert repo.list_by_customer(factura.customer_id)[0].bs_rate == Decimal("848.5458")


def test_una_factura_sin_tasa_se_lee_con_none(conn, repo):
    with transaction(conn):
        factura_id = repo.create(_factura(_crear_cliente(conn)))

    assert repo.get(factura_id).bs_rate is None


def test_get_factura_inexistente_devuelve_none(repo):
    assert repo.get(999) is None


def test_create_guarda_direccion_del_cliente(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid)
    factura.customer_address = "Calle Falsa 123"

    with transaction(conn):
        repo.create(factura)

    recuperada = repo.get(factura.id)
    assert recuperada.customer_address == "Calle Falsa 123"


def test_create_direccion_ausente_queda_null(conn, repo):
    factura = _factura(None)

    with transaction(conn):
        repo.create(factura)

    assert repo.get(factura.id).customer_address is None


def test_get_trae_cabecera_y_lineas(conn, repo):
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    factura = _factura(cid, lines=[_linea(pid)])
    with transaction(conn):
        repo.create(factura)

    recuperada = repo.get(factura.id)

    assert recuperada.display_number == "FAC-000001"
    assert len(recuperada.lines) == 1
    assert recuperada.lines[0].product_sku == "FER-0001"


def test_list_summary_no_trae_lineas(conn, repo):
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    with transaction(conn):
        repo.create(_factura(cid, lines=[_linea(pid)]))

    resumen = repo.list_summary()

    assert len(resumen) == 1
    assert resumen[0].lines == []


def test_list_summary_trae_los_totales_aunque_no_traiga_lineas(conn, repo):
    """
    Regresion: total_cents/subtotal_cents/tax_cents son propiedades que
    suman 'lines' - sin el snapshot que guarda _row_to_invoice(), una
    factura sin líneas cargadas (como las de list_summary()) mostraba
    siempre 0, aunque la fila en 'invoices' tuviera el monto real.
    """
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    with transaction(conn):
        repo.create(_factura(cid, lines=[_linea(pid, qty=3000, price_cents=150)]))

    resumen = repo.list_summary()

    assert resumen[0].subtotal_cents == 450
    assert resumen[0].total_cents == 522  # 450 + 16% de IVA


def test_list_summary_mas_reciente_primero(conn, repo):
    cid = _crear_cliente(conn)
    with transaction(conn):
        repo.create(_factura(cid))
    with transaction(conn):
        repo.create(_factura(cid))

    resumen = repo.list_summary()

    assert [f.number for f in resumen] == [2, 1]


# ------------------------------------------------------------
# list_by_customer: historial de compras, para la ficha del cliente
# ------------------------------------------------------------
def test_list_by_customer_trae_solo_las_facturas_de_ese_cliente(conn, repo):
    c1 = _crear_cliente(conn, name="Juan")
    c2 = conn.execute(
        "INSERT INTO customers (sku, name) VALUES ('CLI-0002', 'Maria')"
    ).lastrowid
    with transaction(conn):
        repo.create(_factura(c1))
    with transaction(conn):
        repo.create(_factura(c2))

    historial = repo.list_by_customer(c1)

    assert len(historial) == 1
    assert historial[0].customer_id == c1


def test_list_by_customer_mas_reciente_primero(conn, repo):
    cid = _crear_cliente(conn)
    with transaction(conn):
        repo.create(_factura(cid))
    with transaction(conn):
        repo.create(_factura(cid))

    historial = repo.list_by_customer(cid)

    assert [f.number for f in historial] == [2, 1]


def test_list_by_customer_no_trae_lineas(conn, repo):
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    with transaction(conn):
        repo.create(_factura(cid, lines=[_linea(pid)]))

    historial = repo.list_by_customer(cid)

    assert historial[0].lines == []


def test_list_by_customer_sin_facturas_devuelve_lista_vacia(conn, repo):
    cid = _crear_cliente(conn)

    assert repo.list_by_customer(cid) == []


# ------------------------------------------------------------
# mark_cancelled
# ------------------------------------------------------------
def test_mark_cancelled_cambia_estado_y_guarda_motivo(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid)
    with transaction(conn):
        repo.create(factura)

    with transaction(conn):
        repo.mark_cancelled(factura.id, "Error en el cliente")

    recuperada = repo.get(factura.id)
    assert recuperada.is_cancelled
    assert recuperada.cancel_reason == "Error en el cliente"
    assert recuperada.cancelled_at is not None


def test_mark_cancelled_factura_inexistente_lanza_valueerror(conn, repo):
    with pytest.raises(ValueError, match="No existe"), transaction(conn):
        repo.mark_cancelled(999, "motivo")


# ------------------------------------------------------------
# payment_method
# ------------------------------------------------------------
def test_create_guarda_la_forma_de_pago(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid, payment_method=PaymentMethod.ZELLE)

    with transaction(conn):
        repo.create(factura)

    fila = conn.execute(
        "SELECT payment_method FROM invoices WHERE id = ?", (factura.id,)
    ).fetchone()
    assert fila["payment_method"] == "ZELLE"
    assert repo.get(factura.id).payment_method is PaymentMethod.ZELLE


def test_create_sin_forma_de_pago_queda_null(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura(cid)  # payment_method=None por defecto

    with transaction(conn):
        repo.create(factura)

    assert repo.get(factura.id).payment_method is None


# ------------------------------------------------------------
# totals_by_payment_method: cierre de caja
# ------------------------------------------------------------
def _factura_en(conn, repo, cid, *, issued_at, payment_method, price_cents=1000):
    """Crea una factura confirmada con un issued_at fijo (para poder armar
    escenarios de rango de fechas de forma determinista) y un total
    conocido. Simula lo que hace BillingService.confirmar(), pero sin
    pasar por StockService (no hace falta para estos tests: solo importa
    lo que queda en 'invoices')."""
    pid = _crear_producto(conn, sku=f"FER-{issued_at}-{payment_method}", name="X")
    factura = _factura(
        cid,
        lines=[_linea(pid, price_cents=price_cents)],
        payment_method=payment_method,
    )
    with transaction(conn):
        repo.create(factura)
    editar_factura_vieja(
        conn, "UPDATE invoices SET issued_at = ? WHERE id = ?", (issued_at, factura.id)
    )
    return factura


def test_totals_by_payment_method_agrupa_y_suma(conn, repo):
    cid = _crear_cliente(conn)
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
        price_cents=1000,
    )
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 15:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
        price_cents=2000,
    )
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 18:00:00",
        payment_method=PaymentMethod.ZELLE,
        price_cents=500,
    )

    totales = repo.totals_by_payment_method(
        start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
    )
    por_forma = {t.payment_method: t for t in totales}

    assert por_forma[PaymentMethod.EFECTIVO_BS].count == 2
    # factura 1: 1000*3=3000 +16% = 3480 | factura 2: 2000*3=6000 +16% = 6960
    assert por_forma[PaymentMethod.EFECTIVO_BS].total_cents == 10440
    assert por_forma[PaymentMethod.ZELLE].count == 1
    assert por_forma[PaymentMethod.ZELLE].total_cents == 1740  # 500*3*1.16


def test_totals_by_payment_method_suma_bs_con_la_tasa_de_cada_factura(conn, repo):
    """
    Lo que hace util al cierre: dos facturas de la misma forma de pago
    emitidas a tasas distintas aportan cada una SUS bolívares. Sumar
    los dolares y multiplicar una vez por una sola tasa daria otro
    numero, y el cajon no cuadraria.
    """
    cid = _crear_cliente(conn)
    una = _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
        price_cents=1000,  # total 3480 centavos
    )
    otra = _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 15:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
        price_cents=2000,  # total 6960 centavos
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = '40.00' WHERE id = ?", (una.id,)
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = '50.00' WHERE id = ?", (otra.id,)
    )

    totales = repo.totals_by_payment_method(
        start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
    )

    (fila,) = totales
    assert fila.total_cents == 10440
    # 3480*40 + 6960*50 = 139.200 + 348.000
    assert fila.total_bs_cents == 487200
    # con UNA sola tasa para todo el grupo darian 417.600 (a 40) o
    # 522.000 (a 50): ninguno de los dos es lo que se cobro
    assert fila.total_bs_cents != 10440 * 40
    assert fila.total_bs_cents != 10440 * 50


def test_totals_by_payment_method_factura_sin_tasa_no_inventa_bolívares(conn, repo):
    """
    Una factura anterior a bs_rate (regla 5) no tiene con que
    convertirse: cuenta y suma en dolares, pero no aporta Bs. Mejor un
    total en Bs corto y explicable que uno inventado con la tasa de hoy.
    """
    cid = _crear_cliente(conn)
    vieja = _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
        price_cents=1000,
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = NULL WHERE id = ?", (vieja.id,)
    )

    (fila,) = repo.totals_by_payment_method(
        start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
    )

    assert fila.count == 1
    assert fila.total_cents == 3480
    assert fila.total_bs_cents == 0


def test_totals_by_payment_method_excluye_fuera_de_rango(conn, repo):
    cid = _crear_cliente(conn)
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-04 23:59:59",
        payment_method=PaymentMethod.ZELLE,
    )
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-06 00:00:00",
        payment_method=PaymentMethod.ZELLE,
    )

    totales = repo.totals_by_payment_method(
        start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
    )

    assert totales == []


def test_totals_by_payment_method_excluye_canceladas(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.TARJETA,
    )
    with transaction(conn):
        repo.mark_cancelled(factura.id, "Se arrepintio")

    totales = repo.totals_by_payment_method(
        start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
    )

    assert totales == []


def test_totals_by_payment_method_agrupa_las_sin_especificar_en_none(conn, repo):
    """Facturas viejas (payment_method NULL) tienen que seguir apareciendo
    en el cierre, no desaparecer en silencio (regla del brief)."""
    cid = _crear_cliente(conn)
    _factura_en(conn, repo, cid, issued_at="2026-03-05 10:00:00", payment_method=None)

    totales = repo.totals_by_payment_method(
        start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
    )

    assert len(totales) == 1
    assert totales[0].payment_method is None
    assert totales[0].count == 1


def test_totals_by_payment_method_sin_facturas_devuelve_vacio(conn, repo):
    assert (
        repo.totals_by_payment_method(
            start="2026-03-05 00:00:00", end="2026-03-06 00:00:00"
        )
        == []
    )


# ------------------------------------------------------------
# list_by_period: las facturas detras de una fila del cierre
# ------------------------------------------------------------
_UN_DIA = {"start": "2026-03-05 00:00:00", "end": "2026-03-06 00:00:00"}


def test_list_by_period_devuelve_solo_las_de_esa_forma_de_pago(conn, repo):
    cid = _crear_cliente(conn)
    zelle = _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 11:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )

    facturas = repo.list_by_period(**_UN_DIA, payment_method=PaymentMethod.ZELLE)

    assert [f.id for f in facturas] == [zelle.id]


def test_list_by_period_con_none_trae_las_que_no_tienen_forma_de_pago(conn, repo):
    """
    payment_method=None es el grupo "Sin especificar", NO "todas": tiene
    que dar exactamente la fila que muestra el cierre. En SQL hace falta
    IS NULL, porque '= NULL' no matchea nunca, ni siquiera contra NULL.
    """
    cid = _crear_cliente(conn)
    sin_metodo = _factura_en(
        conn, repo, cid, issued_at="2026-03-05 10:00:00", payment_method=None
    )
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 11:00:00",
        payment_method=PaymentMethod.ZELLE,
    )

    facturas = repo.list_by_period(**_UN_DIA, payment_method=None)

    assert [f.id for f in facturas] == [sin_metodo.id]


def test_list_by_period_coincide_con_lo_que_sumo_el_cierre(conn, repo):
    """La lista y el total no pueden discrepar: misma cantidad, misma suma."""
    cid = _crear_cliente(conn)
    for hora in ("09:00:00", "12:00:00", "17:00:00"):
        _factura_en(
            conn,
            repo,
            cid,
            issued_at=f"2026-03-05 {hora}",
            payment_method=PaymentMethod.EFECTIVO_BS,
        )

    (total,) = repo.totals_by_payment_method(**_UN_DIA)
    facturas = repo.list_by_period(**_UN_DIA, payment_method=PaymentMethod.EFECTIVO_BS)

    assert len(facturas) == total.count
    assert sum(f.total_cents for f in facturas) == total.total_cents


def test_list_by_period_excluye_canceladas_igual_que_el_cierre(conn, repo):
    cid = _crear_cliente(conn)
    factura = _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    repo.mark_cancelled(factura.id, "prueba")

    assert repo.list_by_period(**_UN_DIA, payment_method=PaymentMethod.ZELLE) == []


def test_list_by_period_excluye_fuera_de_rango(conn, repo):
    cid = _crear_cliente(conn)
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-06 00:00:00",  # el limite es semiabierto
        payment_method=PaymentMethod.ZELLE,
    )

    assert repo.list_by_period(**_UN_DIA, payment_method=PaymentMethod.ZELLE) == []


def test_list_by_period_no_trae_las_lineas(conn, repo):
    """Igual criterio que list_summary(): esta lista no las usa."""
    cid = _crear_cliente(conn)
    _factura_en(
        conn,
        repo,
        cid,
        issued_at="2026-03-05 10:00:00",
        payment_method=PaymentMethod.ZELLE,
    )

    (factura,) = repo.list_by_period(**_UN_DIA, payment_method=PaymentMethod.ZELLE)

    assert factura.lines == []
