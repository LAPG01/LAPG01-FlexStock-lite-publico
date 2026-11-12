"""
Tests de BackupService. Redirige data.connection.BACKUP_DIR a un
directorio temporal (mismo patron que EXPORT_DIR en test_billing_page.py)
para no tocar la carpeta real del usuario.

    pytest -q
"""

import sqlite3

import pytest

from flexstock.data import connection as connection_module
from flexstock.data.connection import init_schema
from flexstock.services.backup_service import BackupService


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def service(conn, tmp_path, monkeypatch):
    monkeypatch.setattr(connection_module, "BACKUP_DIR", tmp_path)
    return BackupService(conn)


def test_backup_now_crea_un_archivo(service, tmp_path):
    ruta = service.backup_now()

    assert ruta.exists()
    assert ruta.parent == tmp_path
    assert ruta.name.startswith("flexstock_")
    assert ruta.suffix == ".db"


def test_backup_now_dos_veces_deja_dos_archivos(service, tmp_path):
    service.backup_now()
    service.backup_now()

    assert len(list(tmp_path.glob("flexstock_*.db"))) == 2


def test_backup_now_mantiene_solo_los_ultimos_5(service, tmp_path):
    for _ in range(7):
        service.backup_now()

    assert len(list(tmp_path.glob("flexstock_*.db"))) == 5


def test_backup_now_borra_los_mas_viejos_no_los_mas_nuevos(service, tmp_path):
    rutas = [service.backup_now() for _ in range(7)]

    restantes = {p.name for p in tmp_path.glob("flexstock_*.db")}
    assert {p.name for p in rutas[-5:]} == restantes


def test_list_backups_mas_reciente_primero(service, tmp_path):
    primero = service.backup_now()
    segundo = service.backup_now()

    listado = service.list_backups(tmp_path)

    assert listado == [segundo, primero]


def test_list_backups_vacio_si_no_hay_respaldos(service, tmp_path):
    assert service.list_backups(tmp_path) == []


def test_backup_now_con_dest_dir_va_a_esa_carpeta_no_a_backup_dir(service, tmp_path):
    otra_carpeta = tmp_path / "pendrive"

    ruta = service.backup_now(otra_carpeta)

    assert ruta.parent == otra_carpeta
    assert ruta.exists()
    assert list(tmp_path.glob("flexstock_*.db")) == []  # nada en BACKUP_DIR


def test_backup_now_con_dest_dir_crea_la_carpeta_si_no_existe(service, tmp_path):
    otra_carpeta = tmp_path / "no_existe_todavia" / "respaldos"

    ruta = service.backup_now(otra_carpeta)

    assert ruta.exists()


def test_backup_now_con_dest_dir_no_poda_respaldos_viejos(service, tmp_path):
    otra_carpeta = tmp_path / "pendrive"
    for _ in range(7):
        service.backup_now(otra_carpeta)

    assert len(list(otra_carpeta.glob("flexstock_*.db"))) == 7


def test_last_backup_at_sin_respaldos_es_none(service, tmp_path):
    assert service.last_backup_at(tmp_path) is None


def test_last_backup_at_es_la_fecha_del_mas_reciente(service, tmp_path):
    from datetime import datetime, timedelta

    service.backup_now()

    ultimo = service.last_backup_at(tmp_path)

    assert ultimo is not None
    assert datetime.now() - ultimo < timedelta(minutes=1)  # noqa: DTZ005
