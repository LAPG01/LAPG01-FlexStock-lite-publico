"""
Tests de CustomerRepository. SQLite en memoria, igual que los demas.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.domain.entities import Customer
from flexstock.domain.errors import DuplicateCode


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
    return CustomerRepository(conn)


# ------------------------------------------------------------
# list_all / get / count
# ------------------------------------------------------------
def test_list_all_vacio_al_principio(repo):
    assert repo.list_all() == []


def test_list_all_filtra_inactivos_por_defecto(repo):
    repo.create(Customer(id=None, sku="CLI-0001", name="Juan"))
    inactivo = Customer(id=None, sku="CLI-0002", name="Pedro", is_active=False)
    repo.create(inactivo)

    assert [c.name for c in repo.list_all()] == ["Juan"]
    assert {c.name for c in repo.list_all(only_active=False)} == {"Juan", "Pedro"}


def test_get_cliente_inexistente_devuelve_none(repo):
    assert repo.get(999) is None


def test_get_trae_todos_los_campos(repo):
    creado = Customer(
        id=None,
        sku="CLI-0001",
        name="Juan Perez",
        tax_id="V-12345678",
        phone="0412-1234567",
        email="juan@correo.com",
        address="Calle Falsa 123",
        is_wholesale=True,
    )
    repo.create(creado)

    cliente = repo.get(creado.id)

    assert cliente.name == "Juan Perez"
    assert cliente.tax_id == "V-12345678"
    assert cliente.email == "juan@correo.com"
    assert cliente.is_wholesale is True
    assert cliente.created_at is not None


def test_count_cuenta_activos_e_inactivos(repo):
    repo.create(Customer(id=None, sku="CLI-0001", name="Uno"))
    repo.create(Customer(id=None, sku="CLI-0002", name="Dos", is_active=False))

    assert repo.count() == 2


# ------------------------------------------------------------
# create / update
# ------------------------------------------------------------
def test_create_inserta_cliente(repo):
    nuevo = Customer(id=None, sku="CLI-0001", name="Juan")

    nuevo_id = repo.create(nuevo)

    assert nuevo.id == nuevo_id
    assert repo.get(nuevo_id).name == "Juan"


def test_create_sku_duplicado_lanza_duplicate_code(repo):
    repo.create(Customer(id=None, sku="CLI-0001", name="Juan"))

    with pytest.raises(DuplicateCode, match="CLI-0001"):
        repo.create(Customer(id=None, sku="CLI-0001", name="Pedro"))


def test_create_cedula_duplicada_lanza_duplicate_code(repo):
    repo.create(Customer(id=None, sku="CLI-0001", name="Juan", tax_id="V-12345678"))

    with pytest.raises(DuplicateCode, match="V-12345678"):
        repo.create(
            Customer(id=None, sku="CLI-0002", name="Pedro", tax_id="V-12345678")
        )


def test_create_dos_clientes_sin_cedula_no_chocan(repo):
    """tax_id NULL no es UNIQUE-conflictivo: dos NULL son distintos para SQLite."""
    repo.create(Customer(id=None, sku="CLI-0001", name="Juan", tax_id=None))

    repo.create(
        Customer(id=None, sku="CLI-0002", name="Pedro", tax_id=None)
    )  # no debe lanzar


def test_update_modifica_campos(repo):
    cliente = Customer(id=None, sku="CLI-0001", name="Juan")
    repo.create(cliente)

    cliente.phone = "0412-9999999"
    cliente.is_wholesale = True
    cliente.is_active = False
    repo.update(cliente)

    actualizado = repo.get(cliente.id)
    assert actualizado.phone == "0412-9999999"
    assert actualizado.is_wholesale is True
    assert actualizado.is_active is False


def test_update_cliente_inexistente_lanza_valueerror(repo):
    with pytest.raises(ValueError, match="No existe"):
        repo.update(Customer(id=999, sku="CLI-0001", name="X"))


def test_update_cedula_duplicada_lanza_duplicate_code(repo):
    repo.create(Customer(id=None, sku="CLI-0001", name="Juan", tax_id="V-12345678"))
    pedro = Customer(id=None, sku="CLI-0002", name="Pedro", tax_id="V-87654321")
    repo.create(pedro)

    pedro.tax_id = "V-12345678"
    with pytest.raises(DuplicateCode, match="V-12345678"):
        repo.update(pedro)
