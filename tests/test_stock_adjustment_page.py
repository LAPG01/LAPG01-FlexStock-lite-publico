"""
Tests de StockAdjustmentPage.

Mismo enfoque que test_customers_page.py: widgets de Tkinter reales en
una ventana oculta con withdraw(). La pagina en si NO usa messagebox: el
resultado de cada ajuste sale como texto al lado del boton (ver
_avisar), justamente para no frenar a quien esta contando producto tras
producto. HistorialProductoDialog (el boton "Historial") SI es modal
(grab_set()) - los tests que lo abren lo destruyen antes de seguir, como
cualquier otro dialogo de la app.

    pytest -q
"""

import sqlite3
import tkinter as tk
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.services.product_service import ProductService
from flexstock.services.stock_service import StockService
from flexstock.ui.stock_adjustment_page import (
    HistorialProductoDialog,
    StockAdjustmentPage,
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
def product_service(conn):
    return ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )


@pytest.fixture
def stock_service(conn):
    return StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _crear_producto(conn, product_service, *, name="Tornillo", stock=10000):
    producto = product_service.create_product(
        name=name,
        category_id=product_service.default_category_id(),
        prices={product_service.list_price_tiers()[0].id: Decimal("1.50")},
    )
    conn.execute(
        "UPDATE products SET current_stock = ? WHERE id = ?", (stock, producto.id)
    )
    return producto.id


def _filas(page):
    return page._tree.get_children()


def _elegir(page, product_id):
    page._tree.selection_set(str(product_id))
    # <<TreeviewSelect>> se despacha en el proximo ciclo de Tk: en un
    # test se llama al handler derecho, sin esperar al mainloop.
    page._on_seleccionar_producto()


def _elegir_primero(page):
    _elegir(page, _filas(page)[0])


def _stock_de(conn, pid):
    return conn.execute(
        "SELECT current_stock FROM products WHERE id = ?", (pid,)
    ).fetchone()["current_stock"]


# ------------------------------------------------------------
# carga y busqueda
# ------------------------------------------------------------
def test_pagina_vacia_no_falla(root, stock_service, product_service):
    page = StockAdjustmentPage(root, stock_service, product_service)

    assert _filas(page) == ()


def test_carga_productos_en_la_tabla(root, conn, stock_service, product_service):
    _crear_producto(conn, product_service, name="Tornillo")
    _crear_producto(conn, product_service, name="Martillo")

    page = StockAdjustmentPage(root, stock_service, product_service)

    assert len(_filas(page)) == 2


def test_la_tabla_muestra_el_stock_de_cada_producto(
    root, conn, stock_service, product_service
):
    """Lo que faltaba en la version vieja: ver las existencias sin tener
    que seleccionar producto por producto."""
    _crear_producto(conn, product_service, name="Tornillo", stock=7500)

    page = StockAdjustmentPage(root, stock_service, product_service)

    (fila,) = _filas(page)
    assert page._tree.item(fila, "values")[3] == "7,5"


def test_filtrar_reduce_la_tabla(root, conn, stock_service, product_service):
    _crear_producto(conn, product_service, name="Tornillo")
    _crear_producto(conn, product_service, name="Martillo")
    page = StockAdjustmentPage(root, stock_service, product_service)

    page._buscar_var.set("martillo")

    assert len(_filas(page)) == 1


# ------------------------------------------------------------
# seleccion
# ------------------------------------------------------------
def test_seleccionar_muestra_stock_actual(root, conn, stock_service, product_service):
    _crear_producto(conn, product_service, name="Tornillo", stock=7500)
    page = StockAdjustmentPage(root, stock_service, product_service)

    _elegir_primero(page)

    assert "7,5" in page._stock_actual_var.get()
    assert "Tornillo" in page._titulo_var.get()


def test_elegir_un_producto_no_cambia_el_ancho_del_panel(
    root, conn, stock_service, product_service
):
    """
    El titulo (hasta 40 caracteres de nombre, ver schema.sql) no tenia
    tope: el panel se ensanchaba o achicaba segun el producto elegido,
    el "recuadro que se ajusta al texto" que reporto el autor.
    """
    corto = _crear_producto(conn, product_service, name="Tornillo")
    largo = _crear_producto(
        conn,
        product_service,
        name="DESTORNILLADOR PHILLIPS PUNTA PLANA PROFESIONAL XL",
    )
    page = StockAdjustmentPage(root, stock_service, product_service)
    page.pack(fill="both", expand=True)
    root.update()

    _elegir(page, corto)
    root.update()
    antes = page._panel.winfo_reqwidth()

    _elegir(page, largo)
    root.update()

    assert page._panel.winfo_reqwidth() == antes


# ------------------------------------------------------------
# vista previa: que va a pasar ANTES de registrar
# ------------------------------------------------------------
def test_preview_avisa_cuanto_va_a_restar(root, conn, stock_service, product_service):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)

    page._contado_var.set("7")

    assert "restar" in page._preview_var.get()
    assert "3" in page._preview_var.get()


def test_preview_avisa_cuanto_va_a_sumar(root, conn, stock_service, product_service):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)

    page._contado_var.set("12")

    assert "sumar" in page._preview_var.get()


def test_preview_avisa_que_no_hay_nada_que_ajustar(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)

    page._contado_var.set("10")

    assert "nada que ajustar" in page._preview_var.get()


def test_preview_con_texto_que_no_es_numero_avisa_sin_romper(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)

    page._contado_var.set("no-es-un-numero")  # no debe lanzar

    assert "número" in page._preview_var.get()


# ------------------------------------------------------------
# registrar ajuste
# ------------------------------------------------------------
def test_registrar_sin_seleccion_avisa_y_no_falla(root, stock_service, product_service):
    page = StockAdjustmentPage(root, stock_service, product_service)

    page._on_registrar()  # nada seleccionado: no debe lanzar

    assert "producto" in page._estado_var.get()
    assert str(page._label_estado.cget("style")) == "Error.TLabel"


def test_registrar_ajuste_reduce_stock(root, conn, stock_service, product_service):
    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("7")

    page._on_registrar()

    assert _stock_de(conn, pid) == 7000
    assert str(page._label_estado.cget("style")) == "Ok.TLabel"


def test_registrar_ajuste_sin_motivo_no_rompe_y_no_escribe(
    root, conn, stock_service, product_service
):
    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("7")
    page._motivo_var.set("")

    page._on_registrar()

    assert _stock_de(conn, pid) == 10000  # ReasonRequired bloqueo el ajuste
    assert str(page._label_estado.cget("style")) == "Error.TLabel"


def test_registrar_ajuste_cantidad_invalida_no_falla(
    root, conn, stock_service, product_service
):
    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("no-es-un-numero")

    page._on_registrar()  # no debe lanzar

    assert _stock_de(conn, pid) == 10000
    assert str(page._label_estado.cget("style")) == "Error.TLabel"


def test_registrar_ajuste_cantidad_negativa_no_escribe(
    root, conn, stock_service, product_service
):
    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("-5")

    page._on_registrar()

    assert _stock_de(conn, pid) == 10000


# ------------------------------------------------------------
# lo que hace comodo contar seguido
# ------------------------------------------------------------
def test_el_motivo_se_queda_puesto_entre_ajustes(
    root, conn, stock_service, product_service
):
    """
    Cambio a proposito respecto de la version vieja, que lo borraba: al
    cargar el inventario producto por producto, volver a escribir el
    motivo cada vez es el peaje mas caro de todos.
    """
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._motivo_var.set("Carga inicial de inventario")
    page._contado_var.set("7")

    page._on_registrar()

    assert page._motivo_var.get() == "Carga inicial de inventario"
    assert page._contado_var.get() == ""  # la cantidad si se limpia


def test_contado_por_se_queda_puesto_y_la_nota_se_borra(
    root, conn, stock_service, product_service
):
    """
    Igual criterio que el motivo (contar un estante entero suele ser la
    misma persona), pero la nota es de ESTE ajuste puntual -"llegaron 4
    pero vienen 6 mas" no tiene sentido repetido en el siguiente.
    """
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_por_var.set("Juan")
    page._nota_var.set("Llegaron 4 pero pronto vendran 6")
    page._contado_var.set("7")

    page._on_registrar()

    assert page._contado_por_var.get() == "Juan"
    assert page._nota_var.get() == ""


def test_registrar_sin_contado_por_ni_nota_no_falla(
    root, conn, stock_service, product_service
):
    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("7")

    page._on_registrar()  # ambos en blanco: no debe lanzar

    assert _stock_de(conn, pid) == 7000


def test_registrar_salta_al_producto_siguiente(
    root, conn, stock_service, product_service
):
    primero = _crear_producto(conn, product_service, name="AAA Tornillo")
    segundo = _crear_producto(conn, product_service, name="BBB Martillo")
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir(page, primero)
    page._contado_var.set("7")

    page._on_registrar()

    assert page._table.selected_iid() == str(segundo)
    assert "Martillo" in page._titulo_var.get()


def test_en_el_ultimo_producto_no_salta_a_ningun_lado(
    root, conn, stock_service, product_service
):
    pid = _crear_producto(conn, product_service, name="Tornillo")
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir(page, pid)
    page._contado_var.set("7")

    page._on_registrar()  # era el unico: no debe lanzar

    assert page._table.selected_iid() == str(pid)


def test_la_fila_ajustada_muestra_el_stock_nuevo(
    root, conn, stock_service, product_service
):
    """
    Se refresca SOLO la fila tocada. Antes se llamaba a reload(), que
    relee el catalogo entero y de paso hace perder el lugar en la lista.
    """
    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir(page, pid)
    page._contado_var.set("7")

    page._on_registrar()

    assert page._tree.item(str(pid), "values")[3] == "7"
    assert "contado" in page._tree.item(str(pid), "tags")


def test_el_ajuste_queda_en_el_historial_de_la_sesion(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("7")

    page._on_registrar()

    assert page._lista_historial.size() == 1
    assert "Tornillo" in page._lista_historial.get(0)


def test_el_historial_de_la_sesion_lleva_la_hora_y_quien_conto(
    root, conn, stock_service, product_service
):
    """Para saber cuando -y, si se cargo, quien- se hizo cada conteo."""
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_por_var.set("Juan")
    page._contado_var.set("7")

    page._on_registrar()

    import re

    linea = page._lista_historial.get(0)
    assert re.match(r"^\d{2}:\d{2}  ", linea)
    assert linea.endswith("— Juan")


def test_el_historial_sin_quien_conto_no_agrega_el_guion(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("7")

    page._on_registrar()

    assert "—" not in page._lista_historial.get(0)


def test_un_ajuste_rechazado_no_entra_al_historial(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)
    page._contado_var.set("7")
    page._motivo_var.set("")  # sin motivo: lo rechaza el dominio

    page._on_registrar()

    assert page._lista_historial.size() == 0


# ------------------------------------------------------------
# boton "Historial": historial COMPLETO del producto (no solo esta sesion)
# ------------------------------------------------------------
def test_boton_historial_arranca_apagado_y_se_prende_al_elegir(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo")
    page = StockAdjustmentPage(root, stock_service, product_service)

    assert str(page._btn_historial.cget("state")) == "disabled"

    _elegir_primero(page)

    assert str(page._btn_historial.cget("state")) == "normal"


def test_boton_historial_pide_el_historial_del_producto_elegido(
    root, conn, stock_service, product_service, monkeypatch
):
    """
    Lo que abre el boton: StockService.history_for(ese producto), no
    'Ajustes de esta sesion' (que se pierde al cerrar la pantalla).
    """
    import flexstock.ui.stock_adjustment_page as stock_adjustment_page_module

    pid = _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    stock_service.ajustar(product_id=pid, counted_milli=7000, reason="Conteo físico")
    llamadas = []

    class _DialogoFalso(tk.Toplevel):
        def __init__(self, master, producto, movimientos):
            super().__init__(master)
            llamadas.append((producto, movimientos))
            self.after(0, self.destroy)

    monkeypatch.setattr(
        stock_adjustment_page_module, "HistorialProductoDialog", _DialogoFalso
    )
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)

    page._on_ver_historial()

    ((producto, movimientos),) = llamadas
    assert producto.id == pid
    assert len(movimientos) == 1
    assert movimientos[0].reason == "Conteo físico"


def test_boton_historial_sin_producto_elegido_no_hace_nada(
    root, conn, stock_service, product_service
):
    """El boton arranca apagado (ver test de arriba), pero por las dudas
    de que algo lo dispare igual -ej. un atajo de teclado futuro."""
    page = StockAdjustmentPage(root, stock_service, product_service)

    page._on_ver_historial()  # no debe lanzar


# ------------------------------------------------------------
# HistorialProductoDialog: como se ve cada fila (probado directo, sin
# pasar por el boton - mismo criterio que RegistrarProveedorDialog)
# ------------------------------------------------------------
def _producto(**kwargs):
    from flexstock.domain.entities import Product

    datos = {"id": 1, "sku": "FER-0001", "name": "Tornillo", "current_stock": 7000}
    datos.update(kwargs)
    return Product(**datos)


def _movimiento(**kwargs):
    from datetime import datetime

    from flexstock.domain.entities import InventoryMovement
    from flexstock.domain.enums import MovementType

    datos = {
        "id": 1,
        "product_id": 1,
        "type": MovementType.ADJUSTMENT,
        "quantity_milli": -3000,
        "created_at": datetime(2026, 3, 1, 14, 30),  # noqa: DTZ001 - hora local
    }
    datos.update(kwargs)
    return InventoryMovement(**datos)


def test_dialogo_sin_movimientos_avisa_en_vez_de_mostrar_una_tabla_vacia(root):
    """
    El caso mas comun hoy: los 1.936 productos migrados entraron en 0
    (sin importador de stock inicial, ver CLAUDE.md) y todavia nadie los
    conto.
    """
    dialogo = HistorialProductoDialog(root, _producto(), [])

    assert not hasattr(dialogo, "_tree_historial")
    dialogo.destroy()


def test_dialogo_muestra_fecha_tipo_cantidad_motivo_quien_y_nota(root):
    dialogo = HistorialProductoDialog(
        root,
        _producto(),
        [
            _movimiento(
                reason="Conteo físico",
                counted_by="Juan",
                note="Llegaron 4 pero pronto vendran 6",
            )
        ],
    )

    (fila,) = dialogo._tree_historial.get_children()
    valores = dialogo._tree_historial.item(fila, "values")
    assert valores == (
        "01/03/2026 14:30",
        "Ajuste",
        "-3",
        "-",  # sin referencia: no vino de una factura ni de una compra
        "Conteo físico",
        "Juan",
        "Llegaron 4 pero pronto vendran 6",
    )
    dialogo.destroy()


def test_dialogo_ordena_del_mas_reciente_al_mas_viejo(root):
    """No reordena: inserta en el orden que le llega (StockService.
    history_for ya lo trae mas reciente primero)."""
    dialogo = HistorialProductoDialog(
        root,
        _producto(),
        [_movimiento(id=2, reason="Más nuevo"), _movimiento(id=1, reason="Más viejo")],
    )

    filas = dialogo._tree_historial.get_children()
    assert dialogo._tree_historial.item(filas[0], "values")[4] == "Más nuevo"
    assert dialogo._tree_historial.item(filas[1], "values")[4] == "Más viejo"
    dialogo.destroy()


def test_dialogo_una_venta_muestra_el_origen_sin_motivo_ni_quien_ni_nota(root):
    from flexstock.domain.enums import MovementType, ReferenceType

    dialogo = HistorialProductoDialog(
        root,
        _producto(),
        [
            _movimiento(
                type=MovementType.OUT,
                quantity_milli=-3000,
                reference_type=ReferenceType.INVOICE,
                reference_id=12,
            )
        ],
    )

    (fila,) = dialogo._tree_historial.get_children()
    valores = dialogo._tree_historial.item(fila, "values")
    assert valores[1] == "Salida"
    assert valores[3] == "Factura"
    assert valores[4:] == ("-", "-", "-")
    dialogo.destroy()


def test_dialogo_una_entrada_suma_en_vez_de_restar(root):
    from flexstock.domain.enums import MovementType

    dialogo = HistorialProductoDialog(
        root, _producto(), [_movimiento(type=MovementType.IN, quantity_milli=2000)]
    )

    (fila,) = dialogo._tree_historial.get_children()
    assert dialogo._tree_historial.item(fila, "values")[2] == "+2"
    dialogo.destroy()


def test_a_150_por_ciento_ajustes_de_esta_sesion_sigue_siendo_usable(
    root, conn, stock_service, product_service, esperar
):
    """
    Dentro de MainWindow (menu + pestanas), a 150 % el panel solo recibe
    ~730 px de alto, no toda la ventana: los dos carteles de ayuda que
    tenia este panel ("Motivo y Por quedan puestos.", "Un ajuste no se
    borra...") envolvian a dos renglones cada uno con ese tema, y eso le
    dejaba el listbox de "Ajustes de esta sesion" en 12 px -una franja,
    no una lista. Se mide con el tema real (fuentes y alto de fila) y la
    misma altura ajustada que da MainWindow con el menu y las pestanas.
    """
    from flexstock.ui.theme import ThemeManager

    ThemeManager(root, font_scale=150)
    _crear_producto(conn, product_service, name="Tornillo", stock=10000)
    # root esta withdraw()n (ver fixture): el gestor de ventanas nunca le
    # aplica una geometria a una ventana que ni siquiera esta mapeada.
    # Un Toplevel (como cualquier dialogo real de la app) si se mapea.
    ventana = tk.Toplevel(root)
    page = StockAdjustmentPage(ventana, stock_service, product_service)
    page.pack(fill="both", expand=True)
    ventana.geometry("1200x730")
    esperar(ventana, lambda: ventana.winfo_height() == 730, que="se aplique 1200x730")
    _elegir_primero(page)
    page._contado_var.set("7")
    page._on_registrar()

    alto_una_fila = (
        page._lista_historial.winfo_reqheight()
    )  # height=1 (ver _build_panel)
    filas_visibles = page._lista_historial.winfo_height() // max(1, alto_una_fila)
    assert filas_visibles >= 3
    assert page._lista_historial.get(0) != ""  # el ajuste recien hecho se ve
    ventana.destroy()


# ------------------------------------------------------------
# select_product: "Ajustar..." desde la ficha de un producto
# ------------------------------------------------------------
def test_select_product_lo_deja_elegido_aunque_hubiera_otra_busqueda(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, name="Tornillo")
    martillo_id = _crear_producto(conn, product_service, name="Martillo")
    page = StockAdjustmentPage(root, stock_service, product_service)
    page._buscar_var.set("Tornillo")  # el Martillo no esta en la tabla

    page.select_product(martillo_id)

    assert page._buscar_var.get() == ""
    assert page._tree.selection() == (str(martillo_id),)
    assert page._producto.id == martillo_id
    assert "Martillo" in page._titulo_var.get()


def test_select_product_de_uno_que_no_existe_no_hace_nada(
    root, conn, stock_service, product_service
):
    page = StockAdjustmentPage(root, stock_service, product_service)

    page.select_product(999)

    assert page._tree.selection() == ()


# ------------------------------------------------------------
# la vista previa con color: verde suma, rojo resta o error
# ------------------------------------------------------------
@pytest.mark.parametrize(
    ("contado", "estilo"),
    [
        ("12", "Ok.TLabel"),  # stock 10: suma
        ("7", "Error.TLabel"),  # resta
        ("10", "Muted.TLabel"),  # igual: nada que ajustar
        ("muchos", "Error.TLabel"),  # no es numero
    ],
)
def test_la_vista_previa_se_pinta_segun_lo_que_va_a_pasar(
    root, conn, stock_service, product_service, contado, estilo
):
    _crear_producto(conn, product_service, stock=10000)
    page = StockAdjustmentPage(root, stock_service, product_service)
    _elegir_primero(page)

    page._contado_var.set(contado)

    assert page._label_preview.cget("style") == estilo


def test_el_stock_se_ve_en_grande_con_su_unidad(
    root, conn, stock_service, product_service
):
    _crear_producto(conn, product_service, stock=7500)
    page = StockAdjustmentPage(root, stock_service, product_service)

    _elegir_primero(page)

    assert page._stock_actual_var.get() == "7,5 UNI"
    assert page._label_stock.cget("style") == "CardValue.TLabel"
