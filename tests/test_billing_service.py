"""
Tests de BillingService: borrador en memoria, confirmar() como
transaccion atomica, cancelar() con reversion de stock.

    pytest -q
"""

import sqlite3
from datetime import date
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema, set_setting
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.domain.entities import Customer
from flexstock.domain.enums import PaymentMethod
from flexstock.domain.errors import (
    EmptyDocument,
    InsufficientStock,
    InvoiceAlreadyCancelled,
    MissingCustomerData,
    ReasonRequired,
)
from flexstock.services.billing_service import BillingService
from flexstock.services.stock_service import StockService
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
def service(conn):
    stock = StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )
    return BillingService(
        conn,
        InvoiceRepository(conn),
        ProductRepository(conn),
        CustomerRepository(conn),
        PriceTierRepository(conn),
        stock,
    )


def _crear_producto(
    conn,
    *,
    sku="FER-0001",
    name="Tornillo",
    stock=10000,
    tier_id=None,
    price_milli=1500,
):
    if tier_id is None:
        # El tier por defecto (is_default=1, hoy 'Detal') no es
        # necesariamente el id=1: no asumirlo.
        tier_id = next(
            t.id for t in PriceTierRepository(conn).list_all() if t.is_default
        )
    cur = conn.execute(
        "INSERT INTO products (sku, name, current_stock) VALUES (?, ?, ?)",
        (sku, name, stock),
    )
    pid = cur.lastrowid
    conn.execute(
        "INSERT INTO product_prices (product_id, tier_id, price_milli) VALUES (?, ?, ?)",
        (pid, tier_id, price_milli),
    )
    return pid


def _crear_cliente(conn, name="Juan Perez", address="Calle Falsa 123"):
    repo = CustomerRepository(conn)
    cliente = Customer(
        id=None, sku="CLI-0001", name=name, tax_id="V-12345678", address=address
    )
    repo.create(cliente)
    return cliente.id


# ------------------------------------------------------------
# crear_borrador
# ------------------------------------------------------------
def test_crear_borrador_con_cliente_registrado(conn, service):
    cid = _crear_cliente(conn)

    factura = service.crear_borrador(customer_id=cid)

    assert factura.customer_id == cid
    assert factura.customer_name == "Juan Perez"
    assert factura.customer_tax_id == "V-12345678"
    assert factura.customer_address == "Calle Falsa 123"
    assert factura.tax_rate == Decimal("16.00")
    assert factura.lines == []
    assert factura.id is None
    assert factura.issued_at is not None  # "ahora", editable en el formulario


def test_crear_borrador_cliente_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.crear_borrador(customer_id=999)


def test_crear_borrador_sin_cliente_usa_nombre_suelto(service):
    factura = service.crear_borrador(customer_name="Consumidor Final")

    assert factura.customer_id is None
    assert factura.customer_name == "Consumidor Final"
    assert factura.customer_tax_id is None


def test_crear_borrador_sin_cliente_ni_nombre_lanza_valueerror(service):
    with pytest.raises(ValueError, match="cliente"):
        service.crear_borrador()


# ------------------------------------------------------------
# cambiar_cliente
# ------------------------------------------------------------
def _otro_cliente(conn):
    cliente = Customer(
        id=None,
        sku="CLI-0002",
        name="Ferreteria Lopez",
        tax_id="J-11111111",
        address="Av. Bolivar",
    )
    CustomerRepository(conn).create(cliente)
    return cliente.id


def test_cambiar_cliente_copia_los_datos_de_la_ficha_y_conserva_lineas(conn, service):
    factura = service.crear_borrador(customer_id=_crear_cliente(conn))
    service.agregar_linea(
        factura, product_id=_crear_producto(conn), quantity=Decimal(1)
    )
    otro = _otro_cliente(conn)

    service.cambiar_cliente(factura, otro)

    assert factura.customer_id == otro
    assert factura.customer_name == "Ferreteria Lopez"
    assert factura.customer_tax_id == "J-11111111"
    assert factura.customer_address == "Av. Bolivar"
    assert len(factura.lines) == 1


def test_cambiar_cliente_inexistente_no_toca_el_borrador(conn, service):
    cid = _crear_cliente(conn)
    factura = service.crear_borrador(customer_id=cid)

    with pytest.raises(ValueError, match="No existe"):
        service.cambiar_cliente(factura, 999)

    assert factura.customer_id == cid
    assert factura.customer_name == "Juan Perez"


def test_una_factura_emitida_no_cambia_de_cliente(conn, service):
    factura = service.crear_borrador(customer_id=_crear_cliente(conn))
    factura.id = 1  # como la deja confirmar()

    with pytest.raises(ValueError, match="emitida"):
        service.cambiar_cliente(factura, _otro_cliente(conn))


# ------------------------------------------------------------
# agregar_linea
# ------------------------------------------------------------
def test_agregar_linea_snapshotea_producto_con_tier_default(conn, service):
    pid = _crear_producto(conn, price_milli=1500)
    factura = service.crear_borrador(customer_name="Mostrador")

    linea = service.agregar_linea(factura, product_id=pid, quantity=Decimal(3))

    assert linea.product_sku == "FER-0001"
    assert linea.quantity_milli == 3000
    assert linea.unit_price_cents == 150  # 1.500 -> 1,50 -> 150 centavos
    assert factura.lines == [linea]


def test_agregar_linea_usa_tier_explicito(conn, service):
    pid = _crear_producto(conn, tier_id=2, price_milli=2000)
    factura = service.crear_borrador(customer_name="Mostrador")

    linea = service.agregar_linea(
        factura, product_id=pid, quantity=Decimal(1), tier_id=2
    )

    assert linea.tier_id == 2
    assert linea.unit_price_cents == 200


def test_agregar_linea_cantidad_cero_lanza_valueerror(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(ValueError, match="cantidad"):
        service.agregar_linea(factura, product_id=pid, quantity=Decimal(0))


def test_agregar_linea_producto_inexistente_lanza_valueerror(service):
    factura = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(ValueError, match="No existe"):
        service.agregar_linea(factura, product_id=999, quantity=Decimal(1))


def test_agregar_linea_producto_inactivo_lanza_valueerror(conn, service):
    pid = _crear_producto(conn)
    conn.execute("UPDATE products SET is_active = 0 WHERE id = ?", (pid,))
    factura = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(ValueError, match="inactivo"):
        service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))


def test_agregar_linea_sin_precio_en_tier_lanza_valueerror(conn, service):
    pid = _crear_producto(conn, tier_id=1)  # solo tiene precio en tier 1
    factura = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(ValueError, match="precio"):
        service.agregar_linea(factura, product_id=pid, quantity=Decimal(1), tier_id=2)


def test_agregar_linea_no_escribe_en_la_base(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")

    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 0


# ------------------------------------------------------------
# quitar_linea
# ------------------------------------------------------------
def test_quitar_linea_la_saca_del_borrador(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    service.quitar_linea(factura, 0)

    assert factura.lines == []


def test_quitar_linea_respeta_la_posicion(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    linea_1 = service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    linea_2 = service.agregar_linea(factura, product_id=pid, quantity=Decimal(2))

    service.quitar_linea(factura, 0)

    assert factura.lines == [linea_2]
    assert linea_1 not in factura.lines


def test_quitar_linea_indice_invalido_lanza_valueerror(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    with pytest.raises(ValueError, match="posición"):
        service.quitar_linea(factura, 5)


# ------------------------------------------------------------
# cambiar_cantidad
# ------------------------------------------------------------
def test_cambiar_cantidad_actualiza_la_linea_y_el_total(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    total_antes = factura.total_cents

    linea = service.cambiar_cantidad(factura, 0, Decimal(3))

    assert linea.quantity_milli == 3000
    assert factura.lines[0].quantity_milli == 3000
    assert factura.total_cents == total_antes * 3


def test_cambiar_cantidad_acepta_decimales(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    service.cambiar_cantidad(factura, 0, Decimal("2.5"))

    assert factura.lines[0].quantity_milli == 2500


@pytest.mark.parametrize("cantidad", [Decimal(0), Decimal(-1)])
def test_cambiar_cantidad_no_positiva_lanza_valueerror(conn, service, cantidad):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    with pytest.raises(ValueError, match="cantidad"):
        service.cambiar_cantidad(factura, 0, cantidad)

    assert factura.lines[0].quantity_milli == 1000  # no se toco


def test_cambiar_cantidad_indice_invalido_lanza_valueerror(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    with pytest.raises(ValueError, match="posición"):
        service.cambiar_cantidad(factura, 5, Decimal(2))


def test_cambiar_cantidad_no_escribe_en_la_base(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    service.cambiar_cantidad(factura, 0, Decimal(4))

    assert conn.execute("SELECT COUNT(*) FROM invoices").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM inventory_movements").fetchone()[0] == 0


# ------------------------------------------------------------
# lines_below_stock
# ------------------------------------------------------------
def test_lines_below_stock_detecta_faltante(conn, service):
    pid = _crear_producto(conn, stock=2000)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(5))

    faltantes = service.lines_below_stock(factura)

    assert len(faltantes) == 1
    assert faltantes[0][1] == 2000  # stock disponible


def test_lines_below_stock_vacio_si_alcanza(conn, service):
    pid = _crear_producto(conn, stock=10000)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(5))

    assert service.lines_below_stock(factura) == []


# ------------------------------------------------------------
# Datos fiscales del comprador: cedula/RIF y direccion
# ------------------------------------------------------------
def test_venta_sin_ficha_guarda_cedula_y_direccion_en_el_borrador(service):
    factura = service.crear_borrador(
        customer_name="Mostrador",
        customer_tax_id=" V-87654321 ",
        customer_address="Av. Sur",
    )

    assert factura.customer_id is None
    assert factura.customer_tax_id == "V-87654321"
    assert factura.customer_address == "Av. Sur"


def test_venta_sin_ficha_y_sin_datos_los_deja_en_none(service):
    factura = service.crear_borrador(
        customer_name="Mostrador", customer_tax_id="  ", customer_address=""
    )

    assert factura.customer_tax_id is None
    assert factura.customer_address is None


def test_con_cliente_registrado_se_ignoran_los_datos_sueltos(conn, service):
    cid = _crear_cliente(conn)

    factura = service.crear_borrador(
        customer_id=cid, customer_tax_id="J-1", customer_address="Otra"
    )

    assert factura.customer_tax_id == "V-12345678"  # el de su ficha
    assert factura.customer_address == "Calle Falsa 123"


@pytest.mark.parametrize(
    ("tax_id", "address", "faltan"),
    [
        (None, None, ["cédula/RIF"]),
        ("   ", "Calle 1", ["cédula/RIF"]),
        # la direccion NO frena una venta: sale en el PDF si esta
        ("V-1", None, []),
        ("V-1", "Calle 1", []),
    ],
)
def test_missing_fiscal_data(service, tax_id, address, faltan):
    factura = service.crear_borrador(
        customer_name="Mostrador", customer_tax_id=tax_id, customer_address=address
    )

    assert service.missing_fiscal_data(factura) == faltan


def _factura_con_una_linea(conn, service, **cliente):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador", **cliente)
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    return factura


def test_confirmar_exigiendo_datos_fiscales_sin_ellos_no_escribe_nada(conn, service):
    factura = _factura_con_una_linea(conn, service)

    with pytest.raises(MissingCustomerData) as error:
        service.confirmar(factura, require_fiscal_customer=True)

    assert error.value.missing == ["cédula/RIF"]
    assert "cédula/RIF" in str(error.value)
    # ni factura ni movimiento de stock ni consumo de numeracion
    assert conn.execute("SELECT count(*) FROM invoices").fetchone()[0] == 0
    assert conn.execute("SELECT count(*) FROM inventory_movements").fetchone()[0] == 0
    assert conn.execute("SELECT current_stock FROM products").fetchone()[0] == 10000


def test_confirmar_exigiendo_datos_fiscales_con_ellos_confirma(conn, service):
    factura = _factura_con_una_linea(
        conn, service, customer_tax_id="V-12345678", customer_address="Av. Sur"
    )

    service.confirmar(factura, require_fiscal_customer=True)

    guardada = service.list_invoices()[0]
    assert guardada.customer_tax_id == "V-12345678"
    assert guardada.customer_address == "Av. Sur"


def test_confirmar_usa_los_datos_actuales_de_la_ficha_del_cliente(conn, service):
    cid = _crear_cliente(conn)
    factura = service.crear_borrador(customer_id=cid)
    pid = _crear_producto(conn)
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    # entre iniciar y confirmar alguien le borra la cedula a la ficha
    conn.execute("UPDATE customers SET tax_id = NULL WHERE id = ?", (cid,))

    with pytest.raises(MissingCustomerData) as error:
        service.confirmar(factura, require_fiscal_customer=True)

    assert error.value.missing == ["cédula/RIF"]


def test_por_defecto_confirmar_no_exige_datos_fiscales(conn, service):
    """El servicio en si no rompe el flujo de quien arma facturas por
    codigo (importadores, scripts): la pantalla es la que lo exige."""
    factura = _factura_con_una_linea(conn, service)

    service.confirmar(factura)

    assert len(service.list_invoices()) == 1


# ------------------------------------------------------------
# confirmar
# ------------------------------------------------------------
def test_confirmar_factura_vacia_lanza_empty_document(service):
    factura = service.crear_borrador(customer_name="Mostrador")

    with pytest.raises(EmptyDocument):
        service.confirmar(factura)


def test_confirmar_bloquea_si_no_se_permite_stock_negativo(conn, service):
    set_setting(conn, "allow_negative_stock", "0")
    pid = _crear_producto(conn, stock=1000)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(5))

    with pytest.raises(InsufficientStock):
        service.confirmar(factura)

    assert conn.execute("SELECT COUNT(*) c FROM invoices").fetchone()["c"] == 0
    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 1000  # nada se toco


def test_confirmar_permite_stock_negativo_por_defecto(conn, service):
    pid = _crear_producto(conn, stock=1000)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(5))

    service.confirmar(factura)

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == -4000


def test_confirmar_inserta_factura_lineas_y_movimientos(conn, service):
    pid = _crear_producto(conn, stock=10000, price_milli=1500)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(3))

    service.confirmar(factura)

    assert factura.id is not None
    assert factura.display_number == "FAC-000001"

    guardada = service.get_invoice(factura.id)
    assert guardada.subtotal_cents == 450
    assert guardada.tax_cents == 72
    assert guardada.total_cents == 522
    assert len(guardada.lines) == 1

    movimientos = conn.execute(
        "SELECT * FROM inventory_movements WHERE reference_id = ? "
        "AND reference_type = 'INVOICE'",
        (factura.id,),
    ).fetchall()
    assert len(movimientos) == 1
    assert movimientos[0]["type"] == "OUT"
    assert movimientos[0]["quantity_milli"] == -3000

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 7000


def test_confirmar_relee_tasa_de_iva_al_momento_de_emitir(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")  # lee 16.00
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    set_setting(conn, "default_tax_rate", "8.00")  # cambia antes de confirmar
    service.confirmar(factura)

    assert factura.tax_rate == Decimal("8.00")


def test_confirmar_relee_nombre_del_cliente_al_momento_de_emitir(conn, service):
    cid = _crear_cliente(conn, name="Juan Perez")
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_id=cid)
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    conn.execute(
        "UPDATE customers SET name = 'Juan P. (actualizado)' WHERE id = ?", (cid,)
    )
    service.confirmar(factura)

    assert factura.customer_name == "Juan P. (actualizado)"


def test_confirmar_relee_direccion_del_cliente_al_momento_de_emitir(conn, service):
    cid = _crear_cliente(conn, address="Dirección vieja")
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_id=cid)
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    conn.execute(
        "UPDATE customers SET address = 'Dirección nueva' WHERE id = ?", (cid,)
    )
    service.confirmar(factura)

    assert factura.customer_address == "Dirección nueva"
    assert service.get_invoice(factura.id).customer_address == "Dirección nueva"


# ------------------------------------------------------------
# cancelar
# ------------------------------------------------------------
def test_cancelar_marca_estado_y_revierte_stock(conn, service):
    pid = _crear_producto(conn, stock=10000)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(3))
    service.confirmar(factura)

    cancelada = service.cancelar(factura.id, "Cliente se arrepintio")

    assert cancelada.is_cancelled
    assert cancelada.cancel_reason == "Cliente se arrepintio"

    stock = conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]
    assert stock == 10000  # volvio a como estaba

    movimientos = conn.execute(
        "SELECT type FROM inventory_movements WHERE reference_id = ? ORDER BY id",
        (factura.id,),
    ).fetchall()
    assert [m["type"] for m in movimientos] == ["OUT", "IN"]


def test_cancelar_sin_motivo_lanza_reason_required(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    service.confirmar(factura)

    with pytest.raises(ReasonRequired):
        service.cancelar(factura.id, "")


def test_cancelar_factura_ya_cancelada_lanza_invoice_already_cancelled(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    service.confirmar(factura)
    service.cancelar(factura.id, "primer motivo")

    with pytest.raises(InvoiceAlreadyCancelled):
        service.cancelar(factura.id, "segundo motivo")


def test_cancelar_factura_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.cancelar(999, "motivo")


# ------------------------------------------------------------
# datos de empresa (settings)
# ------------------------------------------------------------
def test_currency_symbol_lee_settings(conn, service):
    assert service.currency_symbol() == "$"  # semilla del esquema

    set_setting(conn, "currency_symbol", "Bs")

    assert service.currency_symbol() == "Bs"


# ------------------------------------------------------------
# generate_pdf
# ------------------------------------------------------------
def test_generate_pdf_crea_archivo_con_el_numero_de_factura(conn, service, tmp_path):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    service.confirmar(factura, bs_rate=Decimal("185.40"))

    ruta = service.generate_pdf(factura.id, export_dir=tmp_path)

    assert ruta == tmp_path / f"{factura.display_number}.pdf"
    assert ruta.exists()
    assert ruta.read_bytes()[:4] == b"%PDF"


def test_generate_pdf_factura_inexistente_lanza_valueerror(service, tmp_path):
    with pytest.raises(ValueError, match="No existe"):
        service.generate_pdf(999, export_dir=tmp_path)


# ------------------------------------------------------------
# La tasa en Bs se guarda en la factura (snapshot, regla 5) y el PDF
# sale en bolívares con ESA tasa
# ------------------------------------------------------------
def _factura_de_un_producto(conn, service, **confirmar):
    pid = _crear_producto(conn, price_milli=10000)  # 10,00$
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    return service.confirmar(factura, **confirmar)


def test_confirmar_guarda_la_tasa_en_bs_en_la_factura(conn, service):
    factura = _factura_de_un_producto(conn, service, bs_rate=Decimal("848.5458"))

    guardada = service.get_invoice(factura.id)

    assert guardada.bs_rate == Decimal("848.5458")


def test_confirmar_sin_tasa_deja_la_factura_sin_tasa(conn, service):
    factura = _factura_de_un_producto(conn, service)

    assert service.get_invoice(factura.id).bs_rate is None


@pytest.mark.parametrize("malo", [Decimal(0), Decimal(-5)])
def test_confirmar_rechaza_una_tasa_que_no_es_positiva(conn, service, malo):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    with pytest.raises(ValueError, match="mayor que cero"):
        service.confirmar(factura, bs_rate=malo)

    assert service.list_invoices() == []  # no escribio nada


def test_confirmar_guarda_la_tasa_de_referencia(conn, service):
    """Solo para poder volver al precio del catalogo: ningun monto de la
    factura sale de ella (los $ ya estan a tasa BCV)."""
    factura = _factura_de_un_producto(
        conn, service, bs_rate=Decimal("848.55"), ref_rate=Decimal(1000)
    )

    guardada = service.get_invoice(factura.id)

    assert guardada.ref_rate == Decimal(1000)
    assert guardada.bs_rate == Decimal("848.55")


def test_confirmar_sin_tasa_de_referencia_la_deja_en_none(conn, service):
    factura = _factura_de_un_producto(conn, service, bs_rate=Decimal("848.55"))

    assert service.get_invoice(factura.id).ref_rate is None


@pytest.mark.parametrize("malo", [Decimal(0), Decimal(-5)])
def test_confirmar_rechaza_una_tasa_de_referencia_que_no_es_positiva(
    conn, service, malo
):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    with pytest.raises(ValueError, match="referencia"):
        service.confirmar(factura, ref_rate=malo)

    assert service.list_invoices() == []


def test_el_total_al_precio_del_catalogo_deshace_la_alteracion(conn, service):
    """20,00$ cobrados a la tasa de referencia se guardan como 23,57$ a
    BCV; la vuelta tiene que dar los 20,00$ del catalogo."""
    from flexstock.domain.bs_breakdown import catalog_total

    pid = _crear_producto(conn, price_milli=20000)  # 20,00$
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(
        factura,
        product_id=pid,
        quantity=Decimal(1),
        price_factor=Decimal(1000) / Decimal("848.55"),
    )
    service.confirmar(
        factura,
        tax_rate_override=Decimal(0),
        bs_rate=Decimal("848.55"),
        ref_rate=Decimal(1000),
    )

    guardada = service.get_invoice(factura.id)
    assert guardada.total_cents == 2357  # 23,57$ a tasa BCV
    assert catalog_total(guardada) == 2000  # 20,00$ del catalogo


def test_sin_tasa_de_referencia_el_total_del_catalogo_es_el_total(conn, service):
    from flexstock.domain.bs_breakdown import catalog_total

    factura = _factura_de_un_producto(conn, service, bs_rate=Decimal("848.55"))

    guardada = service.get_invoice(factura.id)
    assert catalog_total(guardada) == guardada.total_cents


def _breakdown_del_pdf(monkeypatch):
    """Intercepta lo que se le pasa al PDF, sin leer el archivo."""
    from flexstock.services import billing_service as billing_service_module

    recibido = {}

    def _falso(invoice, **kwargs):
        recibido["invoice"] = invoice
        recibido.update(kwargs)
        return kwargs["export_dir"] / "x.pdf"

    monkeypatch.setattr(billing_service_module, "render_invoice", _falso)
    return recibido


def test_el_pdf_usa_la_tasa_guardada_en_la_factura(
    conn, service, tmp_path, monkeypatch
):
    set_setting(conn, "bcv_rate", "999.00")  # la de hoy NO debe usarse
    factura = _factura_de_un_producto(conn, service, bs_rate=Decimal("100.00"))
    recibido = _breakdown_del_pdf(monkeypatch)

    service.generate_pdf(factura.id, export_dir=tmp_path)

    bs = recibido["bs"]
    assert bs.rate == Decimal("100.00")
    assert bs.subtotal == 100000  # 10,00$ x 100 = 1.000,00 Bs
    assert bs.total == 116000  # + 16% de IVA


def test_reimprimir_da_el_mismo_monto_aunque_cambie_la_tasa_de_hoy(
    conn, service, tmp_path, monkeypatch
):
    """Regla 5: una factura vieja nunca cambia de monto."""
    set_setting(conn, "bcv_rate", "100.00")
    factura = _factura_de_un_producto(conn, service, bs_rate=Decimal("100.00"))
    recibido = _breakdown_del_pdf(monkeypatch)
    service.generate_pdf(factura.id, export_dir=tmp_path)
    antes = recibido["bs"].total

    set_setting(conn, "bcv_rate", "250.00")  # el dolar sube
    service.generate_pdf(factura.id, export_dir=tmp_path)

    assert recibido["bs"].total == antes


def test_factura_sin_tasa_usa_la_bcv_vigente(conn, service, tmp_path, monkeypatch):
    factura = _factura_de_un_producto(conn, service)  # sin tasa guardada
    set_setting(conn, "bcv_rate", "200.00")
    recibido = _breakdown_del_pdf(monkeypatch)

    service.generate_pdf(factura.id, export_dir=tmp_path)

    assert recibido["bs"].rate == Decimal("200.00")


def test_factura_sin_tasa_y_sin_bcv_avisa_en_vez_de_imprimir_en_dolares(
    conn, service, tmp_path
):
    factura = _factura_de_un_producto(conn, service)

    with pytest.raises(ValueError, match="no tiene tasa guardada"):
        service.generate_pdf(factura.id, export_dir=tmp_path)


def test_una_tasa_bcv_guardada_ilegible_se_trata_como_faltante(conn, service, tmp_path):
    factura = _factura_de_un_producto(conn, service)
    set_setting(conn, "bcv_rate", "no es un número")

    with pytest.raises(ValueError, match="tasa"):
        service.generate_pdf(factura.id, export_dir=tmp_path)


# ------------------------------------------------------------
# confirmar: guarda la forma de pago
# ------------------------------------------------------------
def test_confirmar_guarda_la_forma_de_pago_elegida(conn, service):
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    factura.payment_method = PaymentMethod.ZELLE

    service.confirmar(factura)

    assert service.get_invoice(factura.id).payment_method is PaymentMethod.ZELLE


def test_confirmar_sin_forma_de_pago_queda_null(conn, service):
    """
    BillingService.confirmar() no exige forma de pago (regla del brief:
    quien lo exige es la UI, al confirmar una factura NUEVA desde
    InvoiceDialog) - asi una factura vieja o una llamada directa al
    service (ej. un import) no queda bloqueada por este campo.
    """
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    service.confirmar(factura)

    assert service.get_invoice(factura.id).payment_method is None


# ------------------------------------------------------------
# confirmar: tax_rate_override (Modo avanzado, IVA por documento)
# ------------------------------------------------------------
def test_confirmar_sin_override_usa_la_tasa_de_settings(conn, service):
    set_setting(conn, "default_tax_rate", "16.00")
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    service.confirmar(factura)

    assert service.get_invoice(factura.id).tax_rate == Decimal("16.00")


def test_confirmar_con_override_ignora_la_tasa_de_settings(conn, service):
    set_setting(conn, "default_tax_rate", "16.00")
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    service.confirmar(factura, tax_rate_override=Decimal(0))

    guardada = service.get_invoice(factura.id)
    assert guardada.tax_rate == Decimal(0)
    assert guardada.tax_cents == 0


def test_confirmar_con_override_sigue_congelando_el_monto_en_la_factura(conn, service):
    """Regla 5: lo que se ve en pantalla es lo que se cobra - el
    override no es distinto a la tasa normal en ese sentido, tambien
    queda snapshoteado en la factura, no vuelve a recalcularse despues."""
    set_setting(conn, "default_tax_rate", "16.00")
    pid = _crear_producto(conn, price_milli=1000)
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(2))

    service.confirmar(factura, tax_rate_override=Decimal(0))

    guardada = service.get_invoice(factura.id)
    assert guardada.subtotal_cents == 200
    assert guardada.total_cents == 200  # sin el 16% que hubiera puesto settings


# ------------------------------------------------------------
# cierre_diario / cierre_mensual
# ------------------------------------------------------------
def _confirmar_en(conn, service, *, issued_at, payment_method, price_milli=1500):
    """Confirma una factura de 1 unidad y le pisa issued_at a mano (mismo
    truco que test_invoice_repository.py): permite armar escenarios de
    rango de fechas deterministas sin esperar al reloj real."""
    pid = _crear_producto(
        conn, sku=f"FER-{issued_at}-{payment_method}", price_milli=price_milli
    )
    factura = service.crear_borrador(customer_name="Mostrador")
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    factura.payment_method = payment_method
    service.confirmar(factura)
    editar_factura_vieja(
        conn, "UPDATE invoices SET issued_at = ? WHERE id = ?", (issued_at, factura.id)
    )
    return factura


def test_cierre_diario_agrupa_por_forma_de_pago(conn, service):
    _confirmar_en(
        conn,
        service,
        issued_at="2026-03-05 09:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )
    _confirmar_en(
        conn,
        service,
        issued_at="2026-03-05 20:00:00",
        payment_method=PaymentMethod.TARJETA,
    )
    _confirmar_en(
        conn,
        service,
        issued_at="2026-03-06 09:00:00",  # otro dia: no debe contar
        payment_method=PaymentMethod.EFECTIVO_BS,
    )

    cierre = service.cierre_diario(date(2026, 3, 5))
    formas = {c.payment_method for c in cierre}

    assert formas == {PaymentMethod.EFECTIVO_BS, PaymentMethod.TARJETA}
    assert sum(c.count for c in cierre) == 2


def test_cierre_diario_incluye_facturas_sin_forma_de_pago_como_none(conn, service):
    _confirmar_en(conn, service, issued_at="2026-03-05 09:00:00", payment_method=None)

    cierre = service.cierre_diario(date(2026, 3, 5))

    assert len(cierre) == 1
    assert cierre[0].payment_method is None


def test_cierre_diario_sin_facturas_devuelve_lista_vacia(service):
    assert service.cierre_diario(date(2026, 3, 5)) == []


def test_cierre_diario_excluye_facturas_canceladas(conn, service):
    factura = _confirmar_en(
        conn,
        service,
        issued_at="2026-03-05 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    service.cancelar(factura.id, "Se arrepintio")

    assert service.cierre_diario(date(2026, 3, 5)) == []


def test_cierre_mensual_agrupa_todo_el_mes(conn, service):
    _confirmar_en(
        conn,
        service,
        issued_at="2026-03-01 00:00:00",
        payment_method=PaymentMethod.EFECTIVO_USD,
    )
    _confirmar_en(
        conn,
        service,
        issued_at="2026-03-31 23:59:59",
        payment_method=PaymentMethod.EFECTIVO_USD,
    )
    _confirmar_en(
        conn,
        service,
        issued_at="2026-02-28 12:00:00",  # mes anterior: no debe contar
        payment_method=PaymentMethod.EFECTIVO_USD,
    )
    _confirmar_en(
        conn,
        service,
        issued_at="2026-04-01 00:00:00",  # mes siguiente: no debe contar
        payment_method=PaymentMethod.EFECTIVO_USD,
    )

    cierre = service.cierre_mensual(2026, 3)

    assert len(cierre) == 1
    assert cierre[0].count == 2


def test_cierre_mensual_diciembre_cruza_de_anio_correctamente(conn, service):
    _confirmar_en(
        conn,
        service,
        issued_at="2026-12-31 23:59:59",
        payment_method=PaymentMethod.TRANSFERENCIA,
    )
    _confirmar_en(
        conn,
        service,
        issued_at="2027-01-01 00:00:00",  # ya es enero del anio siguiente
        payment_method=PaymentMethod.TRANSFERENCIA,
    )

    cierre = service.cierre_mensual(2026, 12)

    assert len(cierre) == 1
    assert cierre[0].count == 1


# ------------------------------------------------------------
# list_invoices_for_customer
# ------------------------------------------------------------
def test_list_invoices_for_customer_delega_al_repositorio(conn, service):
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn)
    factura = service.crear_borrador(customer_id=cid)
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    service.confirmar(factura)

    historial = service.list_invoices_for_customer(cid)

    assert [f.id for f in historial] == [factura.id]


def test_list_invoices_for_customer_sin_facturas_devuelve_lista_vacia(conn, service):
    cid = _crear_cliente(conn)

    assert service.list_invoices_for_customer(cid) == []


# ------------------------------------------------------------
# el chequeo de stock no puede ser un N+1
# ------------------------------------------------------------
def _contar_consultas(conn):
    """
    Anota cada sentencia que sqlite3 ejecuta de verdad, via
    set_trace_callback (la API que sqlite3 da para esto; envolver
    conn.execute no se puede, es de solo lectura). Devuelve la lista
    viva: sigue creciendo mientras el callback este puesto.

    Hace falta para poder afirmar "esto es UNA consulta" en vez de
    confiar en que se lea bien el codigo - que es como se colo el N+1
    original.
    """
    sentencias = []
    conn.set_trace_callback(sentencias.append)
    return sentencias


def test_el_chequeo_de_stock_es_una_consulta_sin_importar_las_lineas(conn, service):
    """
    Antes esto llamaba a products.get() por linea, y get() son TRES
    consultas: 20 lineas = 60 consultas para leer 20 enteros (el N+1
    que prohibe la Politica de carga). Ahora es una sola, cualquiera
    sea el largo de la factura.
    """
    cid = _crear_cliente(conn)
    factura = service.crear_borrador(customer_id=cid)
    for i in range(20):
        pid = _crear_producto(conn, sku=f"FER-{i:04d}", name=f"Producto {i}")
        service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))

    sentencias = _contar_consultas(conn)
    service.lines_below_stock(factura)
    conn.set_trace_callback(None)

    selects = [s for s in sentencias if s.lstrip().upper().startswith("SELECT")]
    assert len(selects) == 1, f"deberia ser 1 consulta, fueron {len(selects)}"
    assert "IN (" in selects[0]  # en lote, no una por producto


def test_el_chequeo_de_stock_sigue_detectando_el_faltante(conn, service):
    """La optimizacion no puede cambiar lo que responde."""
    cid = _crear_cliente(conn)
    alcanza = _crear_producto(conn, sku="FER-0001", name="Alcanza", stock=10000)
    no_alcanza = _crear_producto(conn, sku="FER-0002", name="No alcanza", stock=1000)
    factura = service.crear_borrador(customer_id=cid)
    service.agregar_linea(factura, product_id=alcanza, quantity=Decimal(2))
    service.agregar_linea(factura, product_id=no_alcanza, quantity=Decimal(5))

    faltantes = service.lines_below_stock(factura)

    assert len(faltantes) == 1
    linea, disponible = faltantes[0]
    assert linea.product_id == no_alcanza
    assert disponible == 1000


def test_el_chequeo_de_stock_avisa_si_el_producto_ya_no_existe(conn, service):
    """Un id que no vuelve de la consulta en lote tiene que seguir siendo
    un error, no un stock 0 silencioso."""
    cid = _crear_cliente(conn)
    pid = _crear_producto(conn, sku="FER-0001", name="Tornillo")
    factura = service.crear_borrador(customer_id=cid)
    service.agregar_linea(factura, product_id=pid, quantity=Decimal(1))
    conn.execute("DELETE FROM products WHERE id = ?", (pid,))

    with pytest.raises(ValueError, match="ya no existe"):
        service.lines_below_stock(factura)
