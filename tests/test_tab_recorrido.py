"""
Tab recorre todos los campos de los formularios, en orden y sin trampas
(la caja de descripcion antes insertaba una sangria y el cursor se quedaba
atrapado ahi). Teclas de verdad: event_generate("<Tab>") sobre el campo
que tiene el foco, igual que el teclado.
"""

import sqlite3
import tkinter as tk
from decimal import Decimal
from types import SimpleNamespace

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.customer_service import CustomerService
from flexstock.services.product_service import ProductService
from flexstock.services.settings_service import SettingsService
from flexstock.services.stock_service import StockService
from flexstock.services.supplier_service import SupplierService
from flexstock.ui import kit
from flexstock.ui.customers_page import CustomerDialog
from flexstock.ui.products_page import (
    ProductDialog,
    RegistrarProveedorDialog,
)
from flexstock.ui.stock_adjustment_page import AjustarExistenciaDialog
from flexstock.ui.suppliers_page import SupplierDialog


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _internos(dialogo):
    """Los widgets que reciben el teclado: Entry/Combobox por dentro, y Text."""
    campos = []

    def recorrer(w):
        for h in w.winfo_children():
            if isinstance(h, (kit.Entry, kit.Combobox)):
                campos.append(h._entry)
            elif isinstance(h, tk.Text):
                campos.append(h)
            recorrer(h)

    recorrer(dialogo)
    return campos


def _recorrer_con_tab(dialogo, root, esperar, *, desde, pasos=40):
    """El foco que va dejando Tab, desde 'desde', hasta que vuelve a empezar."""
    esperar(root, lambda: dialogo.winfo_viewable(), que="se muestre la ventana")
    desde.focus_force()
    esperar(root, lambda: dialogo.focus_get() is desde, que="el foco llegue al inicio")
    vistos = []
    actual = desde
    for _ in range(pasos):
        if actual in vistos:
            break
        vistos.append(actual)
        actual.event_generate("<Tab>")
        dialogo.update()
        actual = dialogo.focus_get()
        assert actual is not None
        assert actual.winfo_toplevel() is dialogo, "Tab saco el foco de la ventana"
    return vistos


def _alcanza_todos(dialogo, vistos):
    visibles = [w for w in _internos(dialogo) if w.winfo_viewable()]
    faltan = [str(w) for w in visibles if w not in vistos]
    assert not faltan, f"Tab no alcanza: {faltan}"


def test_cliente_nuevo_tab_recorre_todos_los_campos_incluida_la_nota(
    root, conn, esperar
):
    dialogo = CustomerDialog(
        root, CustomerService(CustomerRepository(conn)), titulo="Nuevo cliente"
    )

    vistos = _recorrer_con_tab(dialogo, root, esperar, desde=dialogo._name_entry._entry)

    assert dialogo._notes_text in vistos
    _alcanza_todos(dialogo, vistos)
    dialogo.destroy()


def test_proveedor_nuevo_tab_recorre_todos_los_campos(root, conn, esperar):
    dialogo = SupplierDialog(
        root, SupplierService(SupplierRepository(conn)), titulo="Nuevo proveedor"
    )

    vistos = _recorrer_con_tab(dialogo, root, esperar, desde=dialogo._name_entry._entry)

    _alcanza_todos(dialogo, vistos)
    dialogo.destroy()


def test_producto_nuevo_tab_pasa_por_la_descripcion_y_entra_a_lo_opcional(
    root, conn, esperar
):
    servicio = ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )
    ajustes = SettingsService(conn)
    ajustes.set_allow_initial_stock(True)
    dialogo = ProductDialog(
        root,
        servicio,
        titulo="Nuevo producto",
        supplier_service=SupplierService(SupplierRepository(conn)),
        settings_service=ajustes,
    )
    assert dialogo._opcionales_abiertos is False

    vistos = _recorrer_con_tab(dialogo, root, esperar, desde=dialogo._name_entry._entry)

    assert dialogo._description_text in vistos  # no se queda atrapado en la caja
    assert dialogo._opcionales_abiertos is True  # Tab desde el ultimo precio lo abrio
    assert dialogo._existencia_entry._entry in vistos
    assert dialogo._picker_alta.entry._entry in vistos
    # Despues de la descripcion, el campo que sigue es Unidad (no la sangria).
    assert vistos.index(dialogo._description_text) == 1
    dialogo.destroy()


def test_ajustar_existencia_tab_recorre_cantidad_motivo_quien_y_nota(
    root, conn, esperar
):
    stock = StockService(
        conn, ProductRepository(conn), InventoryMovementRepository(conn)
    )
    servicio = ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )
    tier = PriceTierRepository(conn).list_all()[0].id
    producto = servicio.create_product(
        name="Clavo",
        category_id=servicio.default_category_id(),
        prices={tier: Decimal(1)},
    )
    dialogo = AjustarExistenciaDialog(
        root,
        stock,
        product_id=producto.id,
        sku=producto.sku,
        nombre=producto.name,
        unidad="UNI",
        existencia_milli=3000,
    )

    vistos = _recorrer_con_tab(
        dialogo, root, esperar, desde=dialogo._entry_contado._entry
    )

    # Los cuatro campos y despues los dos botones: Tab llega hasta Registrar.
    assert len(vistos) == 6
    _alcanza_todos(dialogo, vistos)
    dialogo.destroy()


def test_en_el_selector_de_proveedor_tab_elige_la_primera_coincidencia_y_sigue(
    root, conn, esperar
):
    proveedores = SupplierService(SupplierRepository(conn))
    sid = proveedores.create_supplier(name="AGROPECUARIA NORTE").id
    proveedores.create_supplier(name="AGROPECUARIA SUR")
    dialogo = RegistrarProveedorDialog(root, proveedores)
    dialogo._buscar_var.set("AGRO")  # dos coincidencias: ninguna elegida
    assert dialogo._elegido() is None

    vistos = _recorrer_con_tab(
        dialogo, root, esperar, desde=dialogo._picker.entry._entry, pasos=3
    )

    assert dialogo._elegido().id == sid  # Tab eligio la primera
    assert dialogo.resultado is None  # y NO registro nada
    assert vistos[1] is dialogo._contacto_combo._entry  # sigue al contacto
    dialogo.destroy()


def test_tab_en_el_selector_sin_nada_escrito_no_elige_nada(root, conn):
    proveedores = SupplierService(SupplierRepository(conn))
    proveedores.create_supplier(name="AGROPECUARIA NORTE")
    proveedores.create_supplier(name="ALFA SUMINISTROS")  # con uno solo se elige solo
    dialogo = RegistrarProveedorDialog(root, proveedores)

    dialogo._picker._on_entry_tab(SimpleNamespace())

    assert dialogo._elegido() is None
    dialogo.destroy()


def _con_foco(root, esperar, ventana, widget):
    ventana.deiconify()
    esperar(root, lambda: ventana.winfo_viewable(), que="se muestre la ventana")
    widget.focus_force()
    esperar(root, lambda: ventana.focus_get() is widget, que="el foco llegue")


def test_un_boton_recibe_foco_con_tab_y_se_activa_con_espacio_y_enter(root, esperar):
    ventana = tk.Toplevel(root)
    pulsaciones = []
    entrada = kit.Entry(ventana)
    entrada.pack()
    boton = kit.Button(ventana, text="Ir", command=lambda: pulsaciones.append(1))
    boton.pack()
    _con_foco(root, esperar, ventana, entrada._entry)

    entrada._entry.event_generate("<Tab>")
    esperar(
        root, lambda: ventana.focus_get() is boton._canvas, que="Tab llegue al boton"
    )
    boton._canvas.event_generate("<space>")
    boton._canvas.event_generate("<Return>")
    ventana.update()

    assert pulsaciones == [1, 1]
    ventana.destroy()


def test_un_boton_apagado_o_con_tab_false_no_es_parada_de_tab(root):
    ventana = tk.Toplevel(root)
    apagado = kit.Button(ventana, text="A", state="disabled")
    saltado = kit.Button(ventana, text="B", tab=False)
    normal = kit.Button(ventana, text="C")

    assert str(apagado._canvas.cget("takefocus")) == "0"
    assert str(saltado._canvas.cget("takefocus")) == "0"
    assert str(normal._canvas.cget("takefocus")) == "1"
    apagado.configure(state="normal")
    assert str(apagado._canvas.cget("takefocus")) == "1"
    ventana.destroy()


def test_una_casilla_se_alterna_con_espacio_y_enter_no_la_toca(root, esperar):
    ventana = tk.Toplevel(root)
    variable = tk.BooleanVar(value=False)
    casilla = kit.Checkbutton(ventana, text="Activo", variable=variable)
    casilla.pack()
    _con_foco(root, esperar, ventana, casilla._canvas)

    casilla._canvas.event_generate("<space>")
    ventana.update()
    assert variable.get() is True

    casilla._canvas.event_generate("<Return>")  # Enter es del formulario
    ventana.update()
    assert variable.get() is True
    ventana.destroy()


def test_un_radio_se_elige_con_espacio(root, esperar):
    ventana = tk.Toplevel(root)
    variable = tk.StringVar(value="a")
    kit.Radiobutton(ventana, text="A", variable=variable, value="a").pack()
    segundo = kit.Radiobutton(ventana, text="B", variable=variable, value="b")
    segundo.pack()
    _con_foco(root, esperar, ventana, segundo._canvas)

    segundo._canvas.event_generate("<space>")
    ventana.update()

    assert variable.get() == "b"
    ventana.destroy()


def test_enter_sobre_cancelar_cancela_y_no_guarda(root, conn, esperar):
    proveedores = SupplierService(SupplierRepository(conn))
    dialogo = SupplierDialog(root, proveedores, titulo="Nuevo proveedor")
    dialogo._name_var.set("LIMAR")
    esperar(root, lambda: dialogo.winfo_viewable(), que="se muestre")
    cancelar = next(b for b in _botones(dialogo) if str(b.cget("text")) == "Cancelar")
    cancelar._canvas.focus_force()
    esperar(
        root, lambda: dialogo.focus_get() is cancelar._canvas, que="foco en Cancelar"
    )

    cancelar._canvas.event_generate("<Return>")
    dialogo.update()

    assert not dialogo.winfo_exists()
    assert proveedores.list_suppliers() == []  # no guardo


def _botones(widget):
    encontrados = []

    def recorrer(w):
        for h in w.winfo_children():
            if isinstance(h, kit.Button):
                encontrados.append(h)
            recorrer(h)

    recorrer(widget)
    return encontrados


def test_un_interruptor_se_alterna_con_espacio(root, esperar):
    ventana = tk.Toplevel(root)
    variable = tk.BooleanVar(value=False)
    interruptor = kit.Switch(ventana, text="Modo", variable=variable)
    interruptor.pack()
    _con_foco(root, esperar, ventana, interruptor._canvas)

    interruptor._canvas.event_generate("<space>")
    ventana.update()

    assert variable.get() is True
    ventana.destroy()
