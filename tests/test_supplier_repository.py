"""
Tests de SupplierRepository. SQLite en memoria, igual que los demas.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.domain.entities import Supplier
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
    return SupplierRepository(conn)


# ------------------------------------------------------------
# list_all / get / count
# ------------------------------------------------------------
def test_list_all_vacio_al_principio(repo):
    assert repo.list_all() == []


def test_list_all_filtra_inactivos_por_defecto(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR"))
    inactivo = Supplier(id=None, sku="PROV-0002", name="MEDINAS", is_active=False)
    repo.create(inactivo)

    assert [s.name for s in repo.list_all()] == ["LIMAR"]
    assert {s.name for s in repo.list_all(only_active=False)} == {"LIMAR", "MEDINAS"}


def test_get_proveedor_inexistente_devuelve_none(repo):
    assert repo.get(999) is None


def test_get_trae_todos_los_campos(repo):
    creado = Supplier(
        id=None,
        sku="PROV-0001",
        name="LIMAR",
        tax_id="J-12345678-9",
        phone="0212-1234567",
        email="ventas@limar.com",
        address="Av. Principal",
        contact_name="Ramon",
        notes="Solo precio",
    )
    repo.create(creado)

    proveedor = repo.get(creado.id)

    assert proveedor.sku == "PROV-0001"
    assert proveedor.name == "LIMAR"
    assert proveedor.tax_id == "J-12345678-9"
    assert proveedor.email == "ventas@limar.com"
    assert proveedor.contact_name == "Ramon"
    assert proveedor.created_at is not None


def test_count_cuenta_activos_e_inactivos(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="Uno"))
    repo.create(Supplier(id=None, sku="PROV-0002", name="Dos", is_active=False))

    assert repo.count() == 2


# ------------------------------------------------------------
# create / update
# ------------------------------------------------------------
def test_create_inserta_proveedor(repo):
    nuevo = Supplier(id=None, sku="PROV-0001", name="LIMAR")

    nuevo_id = repo.create(nuevo)

    assert nuevo.id == nuevo_id
    assert repo.get(nuevo_id).name == "LIMAR"


def test_create_nombre_duplicado_lanza_duplicate_code(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR"))

    with pytest.raises(DuplicateCode, match="LIMAR"):
        repo.create(Supplier(id=None, sku="PROV-0002", name="LIMAR"))


def test_create_sku_duplicado_lanza_duplicate_code(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR"))

    with pytest.raises(DuplicateCode, match="PROV-0001"):
        repo.create(Supplier(id=None, sku="PROV-0001", name="MEDINAS"))


def test_update_modifica_campos(repo):
    proveedor = Supplier(id=None, sku="PROV-0001", name="LIMAR")
    repo.create(proveedor)

    proveedor.phone = "0212-9999999"
    proveedor.contact_name = "Ramon"
    proveedor.is_active = False
    repo.update(proveedor)

    actualizado = repo.get(proveedor.id)
    assert actualizado.phone == "0212-9999999"
    assert actualizado.contact_name == "Ramon"
    assert actualizado.is_active is False


def test_update_proveedor_inexistente_lanza_valueerror(repo):
    with pytest.raises(ValueError, match="No existe"):
        repo.update(Supplier(id=999, sku="PROV-0001", name="X"))


def test_update_nombre_duplicado_lanza_duplicate_code(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR"))
    medinas = Supplier(id=None, sku="PROV-0002", name="MEDINAS")
    repo.create(medinas)

    medinas.name = "LIMAR"
    with pytest.raises(DuplicateCode, match="LIMAR"):
        repo.update(medinas)


# ------------------------------------------------------------
# find_by_name_fuzzy: ayuda para normalizar variantes (Fase 3)
# ------------------------------------------------------------
def test_find_by_name_fuzzy_encuentra_variantes(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR C.A."))
    repo.create(Supplier(id=None, sku="PROV-0002", name="LIMAR DISTRIBUIDORA"))
    repo.create(Supplier(id=None, sku="PROV-0003", name="MEDINAS"))

    encontrados = repo.find_by_name_fuzzy("LIMAR")

    assert {s.name for s in encontrados} == {"LIMAR C.A.", "LIMAR DISTRIBUIDORA"}


def test_find_by_name_fuzzy_es_case_insensitive(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR C.A."))

    assert len(repo.find_by_name_fuzzy("limar")) == 1


def test_find_by_name_fuzzy_sin_coincidencias_devuelve_lista_vacia(repo):
    repo.create(Supplier(id=None, sku="PROV-0001", name="LIMAR"))

    assert repo.find_by_name_fuzzy("INEXISTENTE") == []


# ------------------------------------------------------------
# upgrade de esquema: sku/contact_name agregados en la Fase 8
# ------------------------------------------------------------
def test_init_schema_rellena_sku_de_proveedores_preexistentes(conn):
    """
    A diferencia de 'customers' (nunca tenia filas cuando se le agrego
    el sku), 'suppliers' si puede tener filas del importador de la
    Fase 3 antes de esta columna: init_schema() debe rellenarlas, no
    solo agregar la columna vacia.
    """
    conn.execute("ALTER TABLE suppliers RENAME TO suppliers_vieja")
    conn.execute(
        "CREATE TABLE suppliers (id INTEGER PRIMARY KEY AUTOINCREMENT, "
        "name TEXT NOT NULL UNIQUE, is_active INTEGER NOT NULL DEFAULT 1, "
        "created_at TEXT NOT NULL DEFAULT (datetime('now', 'localtime')))"
    )
    conn.execute("INSERT INTO suppliers (id, name) VALUES (5, 'LIMAR')")
    # 'suppliers_vieja' NO se borra a proposito: el RENAME de arriba hizo
    # que SQLite reescribiera las FK de product_suppliers/product_
    # supplier_entries (que apuntaban a 'suppliers') para que digan
    # 'suppliers_vieja' - borrarla las deja apuntando a una tabla
    # inexistente, y el backfill de entregas (ver connection.py), que
    # SI escribe en esas tablas durante init_schema(), fallaria al
    # validarlas. Dejarla como una tabla de mas, sin usar, no rompe nada.
    init_schema(conn)

    fila = conn.execute("SELECT sku FROM suppliers WHERE id = 5").fetchone()
    assert fila["sku"] == "PROV-0005"
