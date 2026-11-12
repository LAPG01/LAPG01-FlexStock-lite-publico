"""
Tests de HomePage (Inicio). Con dobles chicos de los servicios: la
pagina solo LEE cuatro cosas (cierre del dia y del mes, conteo de
stock, ultimo respaldo, ultimas facturas), y lo que importa probar es
como las muestra, no las consultas, que ya tienen sus propios tests.
"""

import tkinter as tk
from datetime import datetime, timedelta

import pytest

from flexstock.domain.entities import Invoice, PaymentMethodTotal, StockOverview
from flexstock.domain.enums import InvoiceStatus, PaymentMethod
from flexstock.ui.home_page import DIAS_SIN_RESPALDO_ALERTA, HomePage
from flexstock.ui.theme import LIGHT


class _Billing:
    def __init__(self, hoy=(), mes=(), facturas=()):
        self.hoy, self.mes, self.facturas = list(hoy), list(mes), list(facturas)

    def currency_symbol(self):
        return "$"

    def cierre_diario(self, _dia):
        return self.hoy

    def cierre_mensual(self, _anio, _mes):
        return self.mes

    def list_invoices(self):
        return self.facturas


_RESUMEN = StockOverview(active=10, out_of_stock=3, negative=0)


class _Products:
    def __init__(self, resumen=_RESUMEN):
        self.resumen = resumen

    def stock_overview(self):
        return self.resumen


class _Backups:
    def __init__(self, ultimo=None):
        self.ultimo = ultimo

    def last_backup_at(self):
        return self.ultimo


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _pagina(root, *, billing=None, products=None, backups=None, **kw):
    return HomePage(
        root,
        billing_service=billing or _Billing(),
        product_service=products or _Products(),
        backup_service=backups or _Backups(),
        **kw,
    )


def _factura(numero, *, status=InvoiceStatus.ISSUED, total=1000):
    return Invoice(
        id=numero,
        display_number=f"FAC-{numero:06d}",
        customer_name="Cliente",
        tax_rate=0,
        status=status,
        # (subtotal, iva, total) congelados al emitir, ver Invoice.
        totals_snapshot=(total, 0, total),
        issued_at=datetime(2026, 10, numero),  # noqa: DTZ001
    )


def test_ventas_de_hoy_suma_todas_las_formas_de_pago(root):
    hoy = [
        PaymentMethodTotal(PaymentMethod.EFECTIVO_BS, 2, 1500, 1_300_000),
        PaymentMethodTotal(None, 1, 500, 0),
    ]
    pagina = _pagina(root, billing=_Billing(hoy=hoy))

    assert pagina.card_hoy.value_var.get() == "20,00$"
    assert pagina.card_hoy.detail_var.get().startswith("3 facturas")
    assert "13.000,00 Bs" in pagina.card_hoy.detail_var.get()


def test_sin_ventas_lo_dice(root):
    pagina = _pagina(root)

    assert pagina.card_hoy.value_var.get() == "0,00$"
    assert pagina.card_hoy.detail_var.get() == "Sin ventas hoy"


def test_stock_muestra_negativos_y_el_avance_del_conteo(root):
    pagina = _pagina(
        root,
        products=_Products(
            StockOverview(active=50, out_of_stock=7, negative=0, counted=12)
        ),
    )

    assert pagina.card_stock.value_var.get() == "0"
    assert pagina.card_stock.detail_var.get() == "12 de 50 contados"


def test_negativos_se_avisan_en_rojo(root):
    pagina = _pagina(
        root, products=_Products(StockOverview(active=50, out_of_stock=7, negative=2))
    )

    assert pagina.card_stock.value_var.get() == "2"
    assert pagina.card_stock.value_label.cget("text_color")[0] == LIGHT.danger_bg


def test_sin_respaldo_nunca_en_rojo(root):
    pagina = _pagina(root, backups=_Backups(None))

    assert pagina.card_respaldo.value_var.get() == "Nunca"
    assert pagina.card_respaldo.value_label.cget("text_color")[0] == LIGHT.danger_bg


def test_respaldo_de_hoy_no_alarma(root):
    pagina = _pagina(root, backups=_Backups(datetime.now()))  # noqa: DTZ005

    assert pagina.card_respaldo.value_var.get() == "Hoy"
    assert pagina.card_respaldo.value_label.cget("text_color")[0] == LIGHT.fg


def test_respaldo_viejo_alarma(root):
    viejo = datetime.now() - timedelta(days=DIAS_SIN_RESPALDO_ALERTA + 1)  # noqa: DTZ005
    pagina = _pagina(root, backups=_Backups(viejo))

    assert pagina.card_respaldo.value_var.get().startswith("Hace")
    assert pagina.card_respaldo.value_label.cget("text_color")[0] == LIGHT.danger_bg


def test_ultimas_facturas_la_mas_nueva_primero_y_con_tope(root):
    facturas = [_factura(n) for n in range(1, 13)]
    pagina = _pagina(root, billing=_Billing(facturas=facturas))

    filas = pagina.tree.get_children()
    assert len(filas) == 8
    assert pagina.tree.item(filas[0], "values")[0] == "FAC-000012"


def test_una_anulada_se_ve_como_anulada(root):
    pagina = _pagina(
        root,
        billing=_Billing(facturas=[_factura(1, status=InvoiceStatus.CANCELLED)]),
    )

    (fila,) = pagina.tree.get_children()
    assert pagina.tree.item(fila, "values")[4] == "Anulada"
    assert "anulada" in pagina.tree.item(fila, "tags")


def test_atajos_llaman_a_la_ventana(root):
    llamadas = []
    pagina = _pagina(
        root,
        on_nueva_factura=lambda: llamadas.append("factura"),
        on_ir_a=llamadas.append,
        on_respaldar=lambda: llamadas.append("respaldo"),
    )

    pagina._nueva_factura()
    pagina._respaldar()
    pagina._on_ir_a("Ajuste de stock")

    assert llamadas == ["factura", "respaldo", "Ajuste de stock"]


def test_requiere_atencion_lista_negativos_en_rojo_y_bajos_en_naranja(root):
    from flexstock.domain.entities import Product

    def producto(pid, stock):
        return Product(
            id=pid, sku=f"S{pid}", name=f"P{pid}", unit="UNI",
            category_id=1, current_stock=stock, min_stock=5000,
        )  # fmt: skip

    class _ConAtencion(_Products):
        def list_needing_attention(self):
            return [producto(1, -1000), producto(2, 2000)]

    pagina = _pagina(root, products=_ConAtencion())
    tree = pagina.tree_atencion

    assert tree.get_children() == ("1", "2")
    assert tree.item("1", "values")[1] == "-1 UNI"
    assert tree.item("1", "tags") == ("negativo",)
    assert tree.item("2", "tags") == ("bajo",)
    assert pagina._atencion_vacio.winfo_manager() == ""


def test_requiere_atencion_vacio_lo_dice(root):
    pagina = _pagina(root)

    assert pagina.tree_atencion.get_children() == ()
    assert pagina._atencion_vacio.winfo_manager() == "pack"


def test_las_tarjetas_llevan_a_su_detalle(root):
    idas = []
    respaldos = []
    pagina = _pagina(
        root, on_ir_a=idas.append, on_ver_respaldos=lambda: respaldos.append(1)
    )

    # El clic esta atado a la tarjeta y a sus hijos (CTk dibuja con widgets
    # internos que, si no, se comerian el evento).
    valor = pagina.card_stock.value_label._label  # el tk.Label de adentro
    assert pagina.tk.call("bind", valor, "<Button-1>")
    pagina.card_stock._on_click()
    pagina.card_respaldo._on_click()

    assert idas == ["Productos"]
    assert respaldos == [1]
