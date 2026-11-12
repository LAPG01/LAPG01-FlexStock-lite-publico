"""
Tests de CustomerService: SKU automatico, validaciones de nombre/cedula.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.services.customer_service import CustomerService


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
    return CustomerService(CustomerRepository(conn))


# ------------------------------------------------------------
# create_customer: SKU
# ------------------------------------------------------------
def test_create_customer_genera_primer_sku(service):
    cliente = service.create_customer(name="Juan Perez")

    assert cliente.sku == "CLI-0001"


def test_create_customer_incrementa_secuencia(service):
    service.create_customer(name="Uno")
    service.create_customer(name="Dos")

    tres = service.create_customer(name="Tres")

    assert tres.sku == "CLI-0003"


# ------------------------------------------------------------
# create_customer: solo el nombre es obligatorio
# ------------------------------------------------------------
def test_create_customer_solo_con_nombre(service):
    cliente = service.create_customer(name="Juan Perez")

    assert cliente.tax_id is None
    assert cliente.phone is None
    assert cliente.email is None
    assert cliente.address is None
    assert cliente.is_wholesale is False


def test_create_customer_nombre_vacio_lanza_valueerror(service):
    with pytest.raises(ValueError, match="nombre"):
        service.create_customer(name="   ")


def test_create_customer_campos_opcionales_en_blanco_quedan_none(service):
    """'' no es un valor: se guarda como NULL, no como texto vacio."""
    cliente = service.create_customer(name="Juan", tax_id="  ", phone="", email="  ")

    assert cliente.tax_id is None
    assert cliente.phone is None
    assert cliente.email is None


def test_create_customer_dos_clientes_sin_cedula_no_chocan(service):
    service.create_customer(name="Juan")

    service.create_customer(name="Pedro")  # no debe lanzar DuplicateCode


# ------------------------------------------------------------
# create_customer: formato de cedula/RIF
# ------------------------------------------------------------
@pytest.mark.parametrize("tax_id", ["V-12345678", "E-1234567", "J-123456789"])
def test_create_customer_acepta_formatos_validos(service, tax_id):
    cliente = service.create_customer(name="Juan", tax_id=tax_id)

    assert cliente.tax_id == tax_id


def test_create_customer_normaliza_minusculas_a_mayusculas(service):
    cliente = service.create_customer(name="Juan", tax_id="v-12345678")

    assert cliente.tax_id == "V-12345678"


@pytest.mark.parametrize(
    "tax_id", ["12345678", "V12345678", "X-12345678", "V-123", "V-1234567890"]
)
def test_create_customer_rechaza_formatos_invalidos(service, tax_id):
    with pytest.raises(ValueError, match="cédula"):
        service.create_customer(name="Juan", tax_id=tax_id)


# ------------------------------------------------------------
# update_customer
# ------------------------------------------------------------
def test_update_customer_no_cambia_el_sku(service):
    cliente = service.create_customer(name="Juan")

    actualizado = service.update_customer(cliente.id, name="Juan Perez")

    assert actualizado.sku == cliente.sku


def test_update_customer_modifica_campos(service):
    cliente = service.create_customer(name="Juan")

    actualizado = service.update_customer(
        cliente.id, name="Juan Perez", tax_id="V-12345678", is_wholesale=True
    )

    assert actualizado.name == "Juan Perez"
    assert actualizado.tax_id == "V-12345678"
    assert actualizado.is_wholesale is True


def test_update_customer_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.update_customer(999, name="X")


def test_update_customer_nombre_vacio_lanza_valueerror(service):
    cliente = service.create_customer(name="Juan")

    with pytest.raises(ValueError, match="nombre"):
        service.update_customer(cliente.id, name="  ")


def test_update_customer_reactiva_con_is_active_true(service):
    cliente = service.create_customer(name="Juan")
    service.deactivate_customer(cliente.id)

    service.update_customer(cliente.id, name="Juan", is_active=True)

    assert service.get_customer(cliente.id).is_active is True
    assert [c.id for c in service.list_customers()] == [cliente.id]


# ------------------------------------------------------------
# deactivate_customer
# ------------------------------------------------------------
def test_deactivate_customer_lo_saca_de_list_customers(service):
    cliente = service.create_customer(name="Juan")

    service.deactivate_customer(cliente.id)

    assert service.list_customers() == []
    assert service.get_customer(cliente.id).is_active is False


def test_deactivate_customer_no_borra_la_fila(service):
    cliente = service.create_customer(name="Juan")

    service.deactivate_customer(cliente.id)

    assert service.get_customer(cliente.id) is not None


def test_deactivate_customer_inexistente_lanza_valueerror(service):
    with pytest.raises(ValueError, match="No existe"):
        service.deactivate_customer(999)


def test_create_customer_guarda_las_notas(service):
    cliente = service.create_customer(name="Juan", notes="  Paga a fin de mes  ")

    assert service.get_customer(cliente.id).notes == "Paga a fin de mes"


def test_create_customer_sin_notas_o_en_blanco_quedan_none(service):
    sin = service.create_customer(name="A")
    en_blanco = service.create_customer(name="B", notes="   ")

    assert service.get_customer(sin.id).notes is None
    assert service.get_customer(en_blanco.id).notes is None


def test_update_customer_sin_notas_conserva_las_que_tenia(service):
    """Un llamador que no conoce las notas no se las puede borrar sin querer."""
    cliente = service.create_customer(name="Juan", notes="Llamar antes")

    service.update_customer(cliente.id, name="Juan Perez")

    assert service.get_customer(cliente.id).notes == "Llamar antes"


def test_update_customer_con_notas_en_blanco_las_borra(service):
    cliente = service.create_customer(name="Juan", notes="Llamar antes")

    service.update_customer(cliente.id, name="Juan", notes="")

    assert service.get_customer(cliente.id).notes is None


def test_update_customer_cambia_las_notas(service):
    cliente = service.create_customer(name="Juan", notes="Llamar antes")

    service.update_customer(cliente.id, name="Juan", notes="Solo efectivo")

    assert service.get_customer(cliente.id).notes == "Solo efectivo"
