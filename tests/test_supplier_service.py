"""
Tests de SupplierService: SKU automatico, validaciones de nombre.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.supplier_service import SupplierService


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


# ------------------------------------------------------------
# create_supplier: SKU
# ------------------------------------------------------------
def test_create_supplier_genera_primer_sku(service):
    proveedor = service.create_supplier(name="LIMAR")

    assert proveedor.sku == "PROV-0001"


def test_create_supplier_incrementa_secuencia(service):
    service.create_supplier(name="Uno")
    service.create_supplier(name="Dos")

    tres = service.create_supplier(name="Tres")

    assert tres.sku == "PROV-0003"


# ------------------------------------------------------------
# create_supplier: solo el nombre es obligatorio
# ------------------------------------------------------------
def test_create_supplier_solo_con_nombre(service):
    proveedor = service.create_supplier(name="LIMAR")

    assert proveedor.tax_id is None
    assert proveedor.phone is None
    assert proveedor.email is None
    assert proveedor.address is None
    assert proveedor.contact_name is None


def test_create_supplier_nombre_vacio_lanza_valueerror(service):
    with pytest.raises(ValueError, match="nombre"):
        service.create_supplier(name="   ")


def test_create_supplier_campos_opcionales_en_blanco_quedan_none(service):
    proveedor = service.create_supplier(name="LIMAR", tax_id="  ", contact_name="")

    assert proveedor.tax_id is None
    assert proveedor.contact_name is None


def test_create_supplier_con_contacto(service):
    proveedor = service.create_supplier(name="LIMAR", contact_name="Ramon")

    assert proveedor.contact_name == "Ramon"


# ------------------------------------------------------------
# update_supplier
# ------------------------------------------------------------
def test_update_supplier_no_cambia_el_sku(service):
    proveedor = service.create_supplier(name="LIMAR")

    actualizado = service.update_supplier(proveedor.id, name="LIMAR C.A.")

    assert actualizado.sku == proveedor.sku


def test_update_supplier_modifica_campos(service):
    proveedor = service.create_supplier(name="LIMAR")

    actualizado = service.update_supplier(
        proveedor.id, name="LIMAR C.A.", contact_name="Ramon", phone="0212-1234567"
    )

    assert actualizado.name == "LIMAR C.A."
    assert actualizado.contact_name == "Ramon"
    assert actualizado.phone == "0212-1234567"


def test_update_supplier_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.update_supplier(999, name="X")


def test_update_supplier_nombre_vacio_lanza_valueerror(service):
    proveedor = service.create_supplier(name="LIMAR")

    with pytest.raises(ValueError, match="nombre"):
        service.update_supplier(proveedor.id, name="  ")


def test_update_supplier_reactiva_con_is_active_true(service):
    proveedor = service.create_supplier(name="LIMAR")
    service.deactivate_supplier(proveedor.id)

    service.update_supplier(proveedor.id, name="LIMAR", is_active=True)

    assert service.get_supplier(proveedor.id).is_active is True
    assert [s.id for s in service.list_suppliers()] == [proveedor.id]


# ------------------------------------------------------------
# deactivate_supplier
# ------------------------------------------------------------
def test_deactivate_supplier_lo_saca_de_list_suppliers(service):
    proveedor = service.create_supplier(name="LIMAR")

    service.deactivate_supplier(proveedor.id)

    assert service.list_suppliers() == []
    assert service.get_supplier(proveedor.id).is_active is False


def test_deactivate_supplier_no_borra_la_fila(service):
    proveedor = service.create_supplier(name="LIMAR")

    service.deactivate_supplier(proveedor.id)

    assert service.get_supplier(proveedor.id) is not None


def test_deactivate_supplier_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.deactivate_supplier(999)


# ------------------------------------------------------------
# update_contact: corregir contacto/telefono sin tocar lo demas
# ------------------------------------------------------------
def test_update_contact_cambia_contacto_y_telefono(service):
    proveedor = service.create_supplier(name="SURTEC", contact_name="Ana")

    service.update_contact(proveedor.id, contact_name="Mariana", phone="0412-1112233")

    guardado = service.get_supplier(proveedor.id)
    assert guardado.contact_name == "Mariana"
    assert guardado.phone == "0412-1112233"


def test_update_contact_no_borra_el_resto_de_la_ficha(service):
    """Lo que haria update_supplier() si se lo usara para esto: blanquear
    todo lo que no se le pasa."""
    proveedor = service.create_supplier(
        name="SURTEC",
        tax_id="J-12345678-9",
        email="ventas@surtec.test",
        address="Zona industrial",
    )

    service.update_contact(proveedor.id, contact_name="Mariana", phone="0412")

    guardado = service.get_supplier(proveedor.id)
    assert guardado.name == "SURTEC"
    assert guardado.tax_id == "J-12345678-9"
    assert guardado.email == "ventas@surtec.test"
    assert guardado.address == "Zona industrial"
    assert guardado.is_active is True


def test_update_contact_vacio_queda_en_null(service):
    proveedor = service.create_supplier(name="SURTEC", contact_name="Ana", phone="1")

    service.update_contact(proveedor.id, contact_name="  ", phone="")

    guardado = service.get_supplier(proveedor.id)
    assert guardado.contact_name is None
    assert guardado.phone is None


def test_update_contact_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.update_contact(999, contact_name="x", phone="y")
