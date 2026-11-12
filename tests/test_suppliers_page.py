"""
Tests de SuppliersPage y SupplierDialog.

Mismo enfoque que test_customers_page.py: widgets de Tkinter reales en una
ventana oculta con withdraw(); messagebox se parchea porque sus dialogos
son modales que bloquearian la corrida esperando un click.

    pytest -q
"""

import sqlite3
import tkinter as tk
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.product_service import ProductService
from flexstock.services.supplier_service import SupplierService
from flexstock.ui import kit
from flexstock.ui import suppliers_page as suppliers_page_module
from flexstock.ui.suppliers_page import SupplierDialog, SuppliersPage


@pytest.fixture(autouse=True)
def _sin_dialogos_modales(monkeypatch):
    monkeypatch.setattr(
        suppliers_page_module.messagebox, "showerror", lambda *a, **k: None
    )
    monkeypatch.setattr(
        suppliers_page_module.messagebox, "showinfo", lambda *a, **k: None
    )
    monkeypatch.setattr(
        suppliers_page_module.messagebox, "askyesno", lambda *a, **k: True
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
    return SupplierService(SupplierRepository(conn))


@pytest.fixture
def product_service(conn):
    return ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )


@pytest.fixture
def root():
    ventana = tk.Tk()
    ventana.withdraw()
    yield ventana
    ventana.destroy()


def _tiene_checkbox_activo(dialogo):
    for hijo in dialogo.winfo_children():
        for nieto in hijo.winfo_children():
            if isinstance(nieto, kit.Checkbutton) and nieto.cget("text") == "Activo":
                return True
    return False


# ------------------------------------------------------------
# carga inicial
# ------------------------------------------------------------
def test_reload_carga_todos_los_proveedores(root, service):
    service.create_supplier(name="LIMAR")
    service.create_supplier(name="MEDINAS")

    page = SuppliersPage(root, service)

    assert len(page._tree.get_children()) == 2
    assert page._status.cget("text") == "Mostrando 2 de 2 proveedores"


def test_pagina_vacia_no_falla(root, service):
    page = SuppliersPage(root, service)

    assert page._tree.get_children() == ()
    assert page._status.cget("text") == "Mostrando 0 de 0 proveedores"


# ------------------------------------------------------------
# busqueda: filtra en memoria por nombre o sku
# ------------------------------------------------------------
def test_buscar_por_nombre_filtra_la_tabla(root, service):
    service.create_supplier(name="LIMAR")
    service.create_supplier(name="MEDINAS")
    page = SuppliersPage(root, service)

    page._search_var.set("limar")

    assert len(page._tree.get_children()) == 1


def test_buscar_por_sku(root, service):
    proveedor = service.create_supplier(name="LIMAR")
    service.create_supplier(name="MEDINAS")
    page = SuppliersPage(root, service)

    page._search_var.set("PROV-0001")

    assert page._tree.get_children() == (str(proveedor.id),)


def test_borrar_busqueda_muestra_todo_de_nuevo(root, service):
    service.create_supplier(name="LIMAR")
    service.create_supplier(name="MEDINAS")
    page = SuppliersPage(root, service)
    page._search_var.set("limar")

    page._search_var.set("")

    assert len(page._tree.get_children()) == 2


# ------------------------------------------------------------
# Alta
# ------------------------------------------------------------
def test_alta_crea_proveedor_solo_con_nombre(root, service):
    page = SuppliersPage(root, service)
    dialogo = SupplierDialog(page, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("LIMAR")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    proveedores = service.list_suppliers()
    assert len(proveedores) == 1
    assert proveedores[0].name == "LIMAR"
    assert proveedores[0].sku == "PROV-0001"


def test_dialogo_tiene_atajos_enter_y_escape(root, service):
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")

    assert dialogo.bind("<Return>") != ""
    assert dialogo.bind("<Escape>") != ""
    dialogo.destroy()


def test_alta_con_contacto_y_telefono(root, service):
    page = SuppliersPage(root, service)
    dialogo = SupplierDialog(page, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("LIMAR")
    dialogo._contact_var.set("Ramon")
    dialogo._phone_var.set("0212-1234567")

    dialogo._on_guardar()

    proveedor = service.list_suppliers()[0]
    assert proveedor.contact_name == "Ramon"
    assert proveedor.phone == "0212-1234567"


def test_alta_nombre_vacio_no_guarda_y_deja_el_dialogo_abierto(root, service):
    page = SuppliersPage(root, service)
    dialogo = SupplierDialog(page, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("   ")

    dialogo._on_guardar()

    assert dialogo.guardado is False
    assert dialogo.winfo_exists()
    assert service.list_suppliers() == []
    dialogo.destroy()


def test_on_alta_desde_la_pagina_refresca_la_tabla(root, service, monkeypatch):
    page = SuppliersPage(root, service)

    def _fake_dialog(master, svc, **kwargs):
        svc.create_supplier(name="Desde el boton")

        class _Falso:
            guardado = True

        return _Falso()

    monkeypatch.setattr(suppliers_page_module, "SupplierDialog", _fake_dialog)
    monkeypatch.setattr(page, "wait_window", lambda *_a: None)

    page._on_alta()

    assert len(page._tree.get_children()) == 1


# ------------------------------------------------------------
# Editar
# ------------------------------------------------------------
def test_editar_prellena_los_campos(root, service):
    proveedor = service.create_supplier(name="LIMAR", contact_name="Ramon")
    page = SuppliersPage(root, service)

    dialogo = SupplierDialog(
        page, service, titulo="Editar proveedor", supplier_id=proveedor.id
    )

    assert dialogo._name_var.get() == "LIMAR"
    assert dialogo._contact_var.get() == "Ramon"
    dialogo.destroy()


def test_editar_muestra_los_productos_que_le_compramos(
    root, service, product_service, conn
):
    proveedor = service.create_supplier(name="LIMAR")
    producto = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={product_service.list_price_tiers()[0].id: Decimal("1.00")},
    )
    conn.execute(
        "INSERT INTO product_suppliers (product_id, supplier_id, last_purchase) "
        "VALUES (?, ?, '2024-01-15')",
        (producto.id, proveedor.id),
    )
    page = SuppliersPage(root, service, product_service=product_service)

    dialogo = SupplierDialog(
        page,
        service,
        titulo="Editar proveedor",
        supplier_id=proveedor.id,
        product_service=product_service,
    )

    assert [p.product_sku for p in dialogo._productos_proveedor] == [producto.sku]
    fila = dialogo._tree_productos.get_children()[0]
    assert dialogo._tree_productos.item(fila, "values")[0:2] == (
        producto.sku,
        "Tornillo",
    )
    dialogo.destroy()


def test_editar_proveedor_sin_productos_no_muestra_tabla(
    root, service, product_service
):
    proveedor = service.create_supplier(name="LIMAR")

    dialogo = SupplierDialog(
        root,
        service,
        titulo="Editar proveedor",
        supplier_id=proveedor.id,
        product_service=product_service,
    )

    assert dialogo._productos_proveedor == []
    assert not hasattr(dialogo, "_tree_productos")
    dialogo.destroy()


def test_editar_sin_product_service_no_falla_ni_muestra_seccion(root, service):
    """product_service es opcional (ver __init__): sin el, el dialogo
    sigue funcionando, solo que sin la seccion de productos."""
    proveedor = service.create_supplier(name="LIMAR")

    dialogo = SupplierDialog(
        root, service, titulo="Editar proveedor", supplier_id=proveedor.id
    )

    assert dialogo._productos_proveedor == []
    assert not hasattr(dialogo, "_tree_productos")
    dialogo.destroy()


def test_alta_no_muestra_seccion_de_productos(root, service, product_service):
    """Un proveedor nuevo todavia no le vendio nada a la ferreteria."""
    dialogo = SupplierDialog(
        root, service, titulo="Nuevo proveedor", product_service=product_service
    )

    assert not hasattr(dialogo, "_tree_productos")
    dialogo.destroy()


def test_alta_no_muestra_checkbox_activo(root, service):
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")

    assert not _tiene_checkbox_activo(dialogo)
    dialogo.destroy()


def test_reactivar_proveedor_desde_el_dialogo(root, service):
    proveedor = service.create_supplier(name="LIMAR")
    service.deactivate_supplier(proveedor.id)
    page = SuppliersPage(root, service)

    dialogo = SupplierDialog(
        page, service, titulo="Editar proveedor", supplier_id=proveedor.id
    )
    assert dialogo._active_var.get() is False

    dialogo._active_var.set(True)
    dialogo._on_guardar()

    assert service.get_supplier(proveedor.id).is_active is True


def test_editar_guarda_cambios_y_no_toca_el_sku(root, service):
    proveedor = service.create_supplier(name="LIMAR")
    page = SuppliersPage(root, service)
    dialogo = SupplierDialog(
        page, service, titulo="Editar proveedor", supplier_id=proveedor.id
    )
    dialogo._name_var.set("LIMAR C.A.")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    actualizado = service.get_supplier(proveedor.id)
    assert actualizado.name == "LIMAR C.A."
    assert actualizado.sku == proveedor.sku


def test_doble_click_esta_conectado_a_editar(root, service):
    page = SuppliersPage(root, service)

    assert page._tree.bind("<Double-1>") != ""


def test_editar_sin_seleccion_no_abre_dialogo(root, service, monkeypatch):
    service.create_supplier(name="LIMAR")
    page = SuppliersPage(root, service)

    abierto = []
    monkeypatch.setattr(
        suppliers_page_module, "SupplierDialog", lambda *a, **k: abierto.append(1)
    )

    page._on_editar()

    assert abierto == []


# ------------------------------------------------------------
# Eliminar (desactivar)
# ------------------------------------------------------------
def test_eliminar_desactiva_y_no_borra_la_fila(root, service):
    proveedor = service.create_supplier(name="LIMAR")
    page = SuppliersPage(root, service)
    page._tree.selection_set(str(proveedor.id))

    page._on_eliminar()

    assert service.list_suppliers() == []
    assert service.get_supplier(proveedor.id) is not None
    assert service.get_supplier(proveedor.id).is_active is False


def test_eliminar_sin_seleccion_no_falla(root, service):
    service.create_supplier(name="LIMAR")
    page = SuppliersPage(root, service)

    page._on_eliminar()  # nada seleccionado: no debe lanzar

    assert len(service.list_suppliers()) == 1


def test_a_150_por_ciento_los_campos_de_ambas_secciones_arrancan_en_la_misma_x(
    root, service, mostrar_con_tamano
):
    """Igual que en CustomerDialog: Datos del proveedor y Contacto alinean sus campos."""
    from flexstock.ui.theme import ThemeManager

    ThemeManager(root, font_scale=150)
    dialogo = SupplierDialog(SuppliersPage(root, service), service, titulo="Nuevo")
    mostrar_con_tamano(root, dialogo, "900x640")

    assert (
        abs(dialogo._name_entry.winfo_rootx() - dialogo._phone_entry.winfo_rootx()) <= 1
    )
    dialogo.destroy()


def test_alta_arma_el_rif_con_el_tipo_elegido(root, service):
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("LIMAR")
    assert dialogo._tax_tipo_var.get() == "J"  # un proveedor es casi siempre empresa
    dialogo._tax_numero_var.set("12345678 9")

    dialogo._on_guardar()

    assert service.list_suppliers()[0].tax_id == "J-12345678-9"


def test_alta_sin_rif_no_guarda_nada_en_la_cedula(root, service):
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("LIMAR")

    dialogo._on_guardar()

    assert service.list_suppliers()[0].tax_id is None


def test_rif_con_letras_avisa_y_no_guarda(root, service, monkeypatch):
    avisos = []
    monkeypatch.setattr(
        "flexstock.ui.suppliers_page.messagebox.showerror",
        lambda *a, **k: avisos.append(a),
    )
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("LIMAR")
    dialogo._tax_numero_var.set("ABC")

    dialogo._on_guardar()

    assert avisos and dialogo.guardado is False
    assert service.list_suppliers() == []
    dialogo.destroy()


def test_editar_conserva_una_cedula_vieja_que_no_se_toco(root, service):
    proveedor = service.create_supplier(name="LIMAR", tax_id="RIF 12.345.678")
    dialogo = SupplierDialog(
        root, service, titulo="Editar proveedor", supplier_id=proveedor.id
    )
    dialogo._phone_var.set("0212-1234567")

    dialogo._on_guardar()  # solo cambio el telefono

    editado = service.get_supplier(proveedor.id)
    assert editado.tax_id == "RIF 12.345.678"
    assert editado.phone == "0212-1234567"


def test_editar_prellena_tipo_y_numero_del_rif(root, service):
    proveedor = service.create_supplier(name="LIMAR", tax_id="G-20000001-5")
    dialogo = SupplierDialog(
        root, service, titulo="Editar proveedor", supplier_id=proveedor.id
    )

    assert dialogo._tax_tipo_var.get() == "G"
    assert dialogo._tax_numero_var.get() == "20000001-5"
    dialogo.destroy()


def test_alta_puede_arrancar_con_el_nombre_ya_escrito(root, service):
    dialogo = SupplierDialog(
        root, service, titulo="Nuevo proveedor", nombre_inicial="  MOK  "
    )

    assert dialogo._name_var.get() == "MOK"
    dialogo.destroy()


def test_find_similar_encuentra_nombres_parecidos_y_no_a_si_mismo(service):
    mok = service.create_supplier(name="MOK DISTRIBUIDORA")
    service.create_supplier(name="SURTEC")

    assert [p.name for p in service.find_similar("MOK")] == ["MOK DISTRIBUIDORA"]
    assert service.find_similar("MOK DISTRIBUIDORA", exclude_id=mok.id) == []
    assert service.find_similar("LIMAR") == []


def test_alta_con_nombre_parecido_pregunta_y_si_se_dice_que_no_no_guarda(
    root, service, monkeypatch
):
    service.create_supplier(name="MOK DISTRIBUIDORA")
    preguntas = []
    monkeypatch.setattr(
        "flexstock.ui.suppliers_page.messagebox.askyesno",
        lambda titulo, mensaje, **k: preguntas.append(mensaje) or False,
    )
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("MOK")

    dialogo._on_guardar()

    assert "MOK DISTRIBUIDORA" in preguntas[0]
    assert dialogo.guardado is False
    assert len(service.list_suppliers()) == 1
    dialogo.destroy()


def test_alta_con_nombre_parecido_y_si_se_confirma_guarda(root, service, monkeypatch):
    service.create_supplier(name="MOK DISTRIBUIDORA")
    monkeypatch.setattr(
        "flexstock.ui.suppliers_page.messagebox.askyesno", lambda *a, **k: True
    )
    dialogo = SupplierDialog(root, service, titulo="Nuevo proveedor")
    dialogo._name_var.set("MOK")

    dialogo._on_guardar()

    assert dialogo.guardado is True
    assert len(service.list_suppliers()) == 2


def test_editar_sin_cambiar_el_nombre_no_pregunta_por_parecidos(
    root, service, monkeypatch
):
    service.create_supplier(name="MOK")
    otro = service.create_supplier(name="MOK DISTRIBUIDORA")
    monkeypatch.setattr(
        "flexstock.ui.suppliers_page.messagebox.askyesno",
        lambda *a, **k: pytest.fail("no debia preguntar"),
    )
    dialogo = SupplierDialog(
        root, service, titulo="Editar proveedor", supplier_id=otro.id
    )
    dialogo._phone_var.set("04121234567")

    dialogo._on_guardar()

    assert service.get_supplier(otro.id).phone == "0412-1234567"


def test_correo_invalido_en_proveedor_se_rechaza(service):
    with pytest.raises(ValueError, match="correo"):
        service.create_supplier(name="LIMAR", email="limar.com")
