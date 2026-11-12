"""
Tests de CashClosingDialog: que arme las 2 pestanas (Diario/Mensual),
que muestre los totales por forma de pago correctos apenas se abre (sin
esperar un primer click en "Calcular"), y que una fecha invalida avise
en vez de reventar.

Mismo enfoque que test_backup_dialog.py: Tkinter real sobre una ventana
oculta (root.withdraw()), messagebox parchado porque bloquearia la
corrida esperando un click.

    pytest -q
"""

import sqlite3
import tkinter as tk
from datetime import date
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.domain.enums import PaymentMethod
from flexstock.services.billing_service import BillingService
from flexstock.services.product_service import ProductService
from flexstock.services.stock_service import StockService
from flexstock.ui import cash_closing_dialog as cash_closing_dialog_module
from flexstock.ui.cash_closing_dialog import _MESES, CashClosingDialog
from tests._datos_viejos import editar_factura_vieja


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    monkeypatch.setattr(
        cash_closing_dialog_module.messagebox, "showerror", lambda *a, **k: None
    )


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
def product_service(conn):
    return ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )


@pytest.fixture
def billing_service(conn):
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


def _confirmar_en(conn, billing_service, product_service, *, issued_at, payment_method):
    tier_id = next(t.id for t in product_service.list_price_tiers() if t.is_default)
    producto = product_service.create_product(
        name=f"Producto {issued_at} {payment_method}",
        category_id=product_service.default_category_id(),
        prices={tier_id: Decimal("1.50")},
    )
    conn.execute(
        "UPDATE products SET current_stock = 10000 WHERE id = ?", (producto.id,)
    )
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=producto.id, quantity=Decimal(2))
    factura.payment_method = payment_method
    billing_service.confirmar(factura)
    editar_factura_vieja(
        conn, "UPDATE invoices SET issued_at = ? WHERE id = ?", (issued_at, factura.id)
    )
    return factura


# ------------------------------------------------------------
# construccion
# ------------------------------------------------------------
def test_arma_las_dos_pestanas(root, billing_service):
    dialogo = CashClosingDialog(root, billing_service)

    assert dialogo.title() == "Cierre de caja"
    dialogo.destroy()


def test_arranca_con_la_fecha_y_el_mes_de_hoy(root, billing_service):
    hoy = date.today()  # noqa: DTZ011 - hora local, igual que el resto de la app

    dialogo = CashClosingDialog(root, billing_service)

    assert dialogo._dia_anio_var.get() == str(hoy.year)
    assert dialogo._dia_mes_var.get() == _MESES[hoy.month - 1]
    assert dialogo._dia_dia_var.get() == str(hoy.day)
    assert dialogo._mes_anio_var.get() == str(hoy.year)
    assert dialogo._mes_mes_var.get() == _MESES[hoy.month - 1]
    dialogo.destroy()


# ------------------------------------------------------------
# diario
# ------------------------------------------------------------
def test_diario_muestra_el_total_agrupado_por_forma_de_pago(
    root, conn, billing_service, product_service
):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 10:00:00",
        payment_method=PaymentMethod.ZELLE,
    )

    dialogo = CashClosingDialog(root, billing_service)

    filas = [
        dialogo._tree_diario.item(i, "values")
        for i in dialogo._tree_diario.get_children()
    ]
    formas = {fila[0] for fila in filas}
    assert formas == {"Efectivo Bs", "Zelle"}
    # 2 facturas de (1,50*2)*1,16 = 3,48 cada una -> 6,96 en total
    assert "6,96$" in dialogo._total_diario_var.get()
    dialogo.destroy()


def test_diario_excluye_facturas_de_otro_dia(
    root, conn, billing_service, product_service
):
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at="2020-01-01 09:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )

    dialogo = CashClosingDialog(root, billing_service)

    assert dialogo._tree_diario.get_children() == ()
    assert "0,00$" in dialogo._total_diario_var.get()
    dialogo.destroy()


def test_diario_muestra_sin_especificar_para_facturas_sin_forma_de_pago(
    root, conn, billing_service, product_service
):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=None,
    )

    dialogo = CashClosingDialog(root, billing_service)

    filas = [
        dialogo._tree_diario.item(i, "values")
        for i in dialogo._tree_diario.get_children()
    ]
    assert len(filas) == 1
    assert filas[0][0] == "Sin especificar (factura anterior a este cambio)"
    dialogo.destroy()


def test_calcular_diario_con_otra_fecha_recalcula(
    root, conn, billing_service, product_service
):
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at="2026-05-10 09:00:00",
        payment_method=PaymentMethod.TARJETA,
    )
    dialogo = CashClosingDialog(root, billing_service)
    dialogo._dia_anio_var.set("2026")
    dialogo._dia_mes_var.set(_MESES[4])  # Mayo
    dialogo._dia_dia_var.set("10")

    dialogo._on_calcular_diario()

    filas = [
        dialogo._tree_diario.item(i, "values")
        for i in dialogo._tree_diario.get_children()
    ]
    assert filas[0][0] == "Tarjeta (punto de venta)"
    dialogo.destroy()


def test_fecha_diaria_invalida_avisa_y_no_revienta(root, billing_service, monkeypatch):
    dialogo = CashClosingDialog(root, billing_service)
    errores = []
    monkeypatch.setattr(
        cash_closing_dialog_module.messagebox,
        "showerror",
        lambda *a, **k: errores.append(a),
    )
    dialogo._dia_mes_var.set("Febrero")
    dialogo._dia_dia_var.set("31")  # Febrero no tiene 31

    dialogo._on_calcular_diario()  # no debe lanzar

    assert errores
    dialogo.destroy()


# ------------------------------------------------------------
# mensual
# ------------------------------------------------------------
def test_mensual_agrupa_todo_el_mes(root, conn, billing_service, product_service):
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at="2026-06-01 00:00:00",
        payment_method=PaymentMethod.TRANSFERENCIA,
    )
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at="2026-06-30 23:59:59",
        payment_method=PaymentMethod.TRANSFERENCIA,
    )
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at="2026-07-01 00:00:00",  # mes siguiente: no debe contar
        payment_method=PaymentMethod.TRANSFERENCIA,
    )
    dialogo = CashClosingDialog(root, billing_service)
    dialogo._mes_anio_var.set("2026")
    dialogo._mes_mes_var.set(_MESES[5])  # Junio

    dialogo._on_calcular_mensual()

    filas = [
        dialogo._tree_mensual.item(i, "values")
        for i in dialogo._tree_mensual.get_children()
    ]
    assert len(filas) == 1
    assert filas[0][1] == "2"  # cantidad de facturas (Treeview siempre da texto)
    dialogo.destroy()


def test_mes_invalido_avisa_y_no_revienta(root, billing_service, monkeypatch):
    dialogo = CashClosingDialog(root, billing_service)
    errores = []
    monkeypatch.setattr(
        cash_closing_dialog_module.messagebox,
        "showerror",
        lambda *a, **k: errores.append(a),
    )
    dialogo._mes_anio_var.set("no-es-un-anio")

    dialogo._on_calcular_mensual()  # no debe lanzar

    assert errores
    dialogo.destroy()


# ------------------------------------------------------------
# moneda
# ------------------------------------------------------------
def test_usa_el_simbolo_de_moneda_pasado(root, conn, billing_service, product_service):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.EFECTIVO_USD,
    )

    dialogo = CashClosingDialog(root, billing_service, currency_symbol="Bs")

    # La parte en la moneda configurada es la PRIMERA del total general
    # (la segunda siempre va en Bs, ver _poblar), asi que se mira esa y
    # no el final del texto: si no, el " Bs" de la segunda haria pasar
    # este test aunque el simbolo pasado se ignorara.
    en_la_moneda = dialogo._total_diario_var.get().split("|")[0]
    assert en_la_moneda.rstrip().endswith("Bs")
    dialogo.destroy()


# ------------------------------------------------------------
# total en bolívares (lo que se cuenta en el cajon)
# ------------------------------------------------------------
def test_diario_muestra_el_total_en_bolívares_de_cada_factura(
    root, conn, billing_service, product_service
):
    """
    Dos facturas cobradas a tasas distintas: la columna Bs suma los
    bolívares de cada una a SU tasa, que es lo que de verdad entro al
    cajon.
    """
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    una = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )
    otra = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 10:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )
    # cada una vale 3,48$ (348 centavos)
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = '40.00' WHERE id = ?", (una.id,)
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = '50.00' WHERE id = ?", (otra.id,)
    )

    dialogo = CashClosingDialog(root, billing_service)

    (fila,) = [
        dialogo._tree_diario.item(i, "values")
        for i in dialogo._tree_diario.get_children()
    ]
    # 348*40 + 348*50 = 13.920 + 17.400 = 31.320 centavos = 313,20 Bs
    assert fila[3] == "313,20 Bs"
    assert "313,20 Bs" in dialogo._total_diario_var.get()
    dialogo.destroy()


def test_factura_sin_tasa_guardada_no_suma_bolívares(
    root, conn, billing_service, product_service
):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    vieja = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = NULL WHERE id = ?", (vieja.id,)
    )

    dialogo = CashClosingDialog(root, billing_service)

    (fila,) = [
        dialogo._tree_diario.item(i, "values")
        for i in dialogo._tree_diario.get_children()
    ]
    assert fila[2] == "3,48$"  # el dolar si se cuenta
    assert fila[3] == "0,00 Bs"  # los bolívares no se inventan
    dialogo.destroy()


# ------------------------------------------------------------
# doble clic en una fila: las facturas que hay detras
# ------------------------------------------------------------
def _espiar_detalle(monkeypatch):
    """
    Reemplaza InvoiceDetailDialog por un doble que anota con que lo
    llamaron y se cierra solo (el de verdad es modal y el wait_window()
    de _on_detalle colgaria la corrida). Se parchea en billing_page
    porque el import de _on_detalle es local: se resuelve al llamarla.
    """
    from flexstock.ui import billing_page as billing_page_module

    llamadas = []

    class DetalleFalso(tk.Toplevel):
        def __init__(self, master, invoice, **kwargs):
            super().__init__(master)
            llamadas.append((invoice, kwargs))
            self.after(1, self.destroy)

    monkeypatch.setattr(billing_page_module, "InvoiceDetailDialog", DetalleFalso)
    return llamadas


def _grupo_abierto(dialogo):
    """El _FacturasDelGrupoDialog que acaba de abrir el cierre."""
    from flexstock.ui.cash_closing_dialog import _FacturasDelGrupoDialog

    hijos = [
        w for w in dialogo.winfo_children() if isinstance(w, _FacturasDelGrupoDialog)
    ]
    return hijos[-1] if hijos else None


def test_doble_clic_en_una_fila_lista_sus_facturas(
    root, conn, billing_service, product_service
):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    una = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    otra = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 10:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    # de otra forma de pago: no tiene que aparecer
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 11:00:00",
        payment_method=PaymentMethod.EFECTIVO_BS,
    )
    dialogo = CashClosingDialog(root, billing_service)

    dialogo._tree_diario.selection_set(PaymentMethod.ZELLE.value)
    dialogo._on_ver_facturas(dialogo._tree_diario)

    grupo = _grupo_abierto(dialogo)
    assert grupo is not None
    numeros = [grupo._tree.item(i, "values")[0] for i in grupo._tree.get_children()]
    assert numeros == [una.display_number, otra.display_number]
    grupo.destroy()
    dialogo.destroy()


def test_la_fila_sin_forma_de_pago_tambien_abre_sus_facturas(
    root, conn, billing_service, product_service
):
    """El grupo 'Sin especificar' usa un iid centinela, no el valor de
    un PaymentMethod: tiene que funcionar igual."""
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    sin_metodo = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=None,
    )
    dialogo = CashClosingDialog(root, billing_service)

    dialogo._tree_diario.selection_set(cash_closing_dialog_module._SIN_METODO_IID)
    dialogo._on_ver_facturas(dialogo._tree_diario)

    grupo = _grupo_abierto(dialogo)
    numeros = [grupo._tree.item(i, "values")[0] for i in grupo._tree.get_children()]
    assert numeros == [sin_metodo.display_number]
    grupo.destroy()
    dialogo.destroy()


def test_sin_fila_elegida_no_abre_nada(root, conn, billing_service, product_service):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    dialogo = CashClosingDialog(root, billing_service)

    dialogo._on_ver_facturas(dialogo._tree_diario)  # nadie toco una fila

    assert _grupo_abierto(dialogo) is None
    dialogo.destroy()


def test_desde_el_grupo_se_abre_el_detalle_de_la_factura(
    root, conn, billing_service, product_service, monkeypatch
):
    """Lo que pidio el autor: el atajo llega hasta la misma ventana que
    "Ver detalle" de Facturas, la del boton de PDF."""
    llamadas = _espiar_detalle(monkeypatch)
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    factura = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    dialogo = CashClosingDialog(root, billing_service)
    dialogo._tree_diario.selection_set(PaymentMethod.ZELLE.value)
    dialogo._on_ver_facturas(dialogo._tree_diario)
    grupo = _grupo_abierto(dialogo)

    grupo._tree.selection_set(str(factura.id))
    grupo._on_detalle()

    assert len(llamadas) == 1
    abierta, kwargs = llamadas[0]
    assert abierta.id == factura.id
    assert len(abierta.lines) == 1  # completa, con lineas
    assert kwargs["billing_service"] is billing_service  # sin esto no hay PDF
    grupo.destroy()
    dialogo.destroy()


def test_el_grupo_muestra_los_bolívares_de_cada_factura(
    root, conn, billing_service, product_service
):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    factura = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = '40.00' WHERE id = ?", (factura.id,)
    )
    dialogo = CashClosingDialog(root, billing_service)
    dialogo._tree_diario.selection_set(PaymentMethod.ZELLE.value)
    dialogo._on_ver_facturas(dialogo._tree_diario)

    grupo = _grupo_abierto(dialogo)
    (fila,) = [grupo._tree.item(i, "values") for i in grupo._tree.get_children()]
    assert fila[3] == "3,48$"
    assert fila[4] == "139,20 Bs"  # 348 * 40
    grupo.destroy()
    dialogo.destroy()


def test_una_factura_sin_tasa_muestra_guion_en_el_grupo(
    root, conn, billing_service, product_service
):
    hoy = date.today().isoformat()  # noqa: DTZ011 - hora local
    factura = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{hoy} 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    editar_factura_vieja(
        conn, "UPDATE invoices SET bs_rate = NULL WHERE id = ?", (factura.id,)
    )
    dialogo = CashClosingDialog(root, billing_service)
    dialogo._tree_diario.selection_set(PaymentMethod.ZELLE.value)
    dialogo._on_ver_facturas(dialogo._tree_diario)

    grupo = _grupo_abierto(dialogo)
    (fila,) = [grupo._tree.item(i, "values") for i in grupo._tree.get_children()]
    assert fila[4] == "-"  # no se inventa un monto
    grupo.destroy()
    dialogo.destroy()


def test_la_pestana_mensual_tambien_abre_sus_facturas(
    root, conn, billing_service, product_service
):
    """El periodo sale de la pestana donde se hizo el doble clic."""
    hoy = date.today()  # noqa: DTZ011 - hora local
    primero = hoy.replace(day=1).isoformat()
    factura = _confirmar_en(
        conn,
        billing_service,
        product_service,
        issued_at=f"{primero} 09:00:00",
        payment_method=PaymentMethod.ZELLE,
    )
    dialogo = CashClosingDialog(root, billing_service)

    dialogo._tree_mensual.selection_set(PaymentMethod.ZELLE.value)
    dialogo._on_ver_facturas(dialogo._tree_mensual)

    grupo = _grupo_abierto(dialogo)
    numeros = [grupo._tree.item(i, "values")[0] for i in grupo._tree.get_children()]
    assert numeros == [factura.display_number]
    grupo.destroy()
    dialogo.destroy()
