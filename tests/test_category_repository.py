"""
Tests de CategoryRepository. SQLite en memoria, igual que los demas.

El schema.sql trae una categoria semilla ('GEN'/'General'), asi que
list_all() parte de 1 fila incluso en una base recien creada.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.domain.entities import Category
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
    return CategoryRepository(conn)


# ------------------------------------------------------------
# list_all / get
# ------------------------------------------------------------
def test_list_all_trae_la_semilla_gen(repo):
    categorias = repo.list_all()
    assert [c.code for c in categorias] == ["GEN"]


def test_list_all_filtra_inactivas_por_defecto(conn, repo):
    conn.execute(
        "INSERT INTO categories (code, name, is_active) VALUES ('FER', 'Ferreteria', 0)"
    )
    assert [c.code for c in repo.list_all()] == ["GEN"]
    assert {c.code for c in repo.list_all(only_active=False)} == {"GEN", "FER"}


def test_get_categoria_inexistente_devuelve_none(repo):
    assert repo.get(999) is None


def test_get_trae_la_categoria(repo):
    categoria = repo.get(repo.list_all()[0].id)
    assert categoria.code == "GEN"


# ------------------------------------------------------------
# create
# ------------------------------------------------------------
def test_create_inserta_categoria(repo):
    nueva = Category(id=None, code="FER", name="Ferreteria")

    nuevo_id = repo.create(nueva)

    assert nueva.id == nuevo_id
    assert repo.get(nuevo_id).code == "FER"


@pytest.mark.parametrize("code", ["fer", "FE", "FERR", "F3R", "", "FER "])
def test_create_rechaza_codigo_con_formato_invalido(repo, code):
    with pytest.raises(ValueError, match="3 letras mayusculas"):
        repo.create(Category(id=None, code=code, name="X"))


def test_create_code_duplicado_lanza_duplicate_code(repo):
    repo.create(Category(id=None, code="FER", name="Ferreteria"))

    with pytest.raises(DuplicateCode, match="FER"):
        repo.create(Category(id=None, code="FER", name="Otra"))


def test_create_name_duplicado_lanza_duplicate_code(repo):
    repo.create(Category(id=None, code="FER", name="Ferreteria"))

    with pytest.raises(DuplicateCode, match="Ferreteria"):
        repo.create(Category(id=None, code="ELE", name="Ferreteria"))


# ------------------------------------------------------------
# update
# ------------------------------------------------------------
def test_update_modifica_campos(repo):
    categoria = Category(id=None, code="FER", name="Ferreteria")
    repo.create(categoria)

    categoria.name = "Ferreteria General"
    categoria.is_active = False
    repo.update(categoria)

    actualizada = repo.get(categoria.id)
    assert actualizada.name == "Ferreteria General"
    assert actualizada.is_active is False


def test_update_valida_formato_de_codigo(repo):
    categoria = Category(id=None, code="FER", name="Ferreteria")
    repo.create(categoria)

    categoria.code = "fer"
    with pytest.raises(ValueError, match="3 letras mayusculas"):
        repo.update(categoria)


def test_update_categoria_inexistente_lanza_valueerror(repo):
    with pytest.raises(ValueError, match="No existe"):
        repo.update(Category(id=999, code="FER", name="X"))


def test_update_code_duplicado_lanza_duplicate_code(repo):
    repo.create(Category(id=None, code="FER", name="Ferreteria"))
    ele = Category(id=None, code="ELE", name="Electricidad")
    repo.create(ele)

    ele.code = "FER"
    with pytest.raises(DuplicateCode, match="FER"):
        repo.update(ele)
