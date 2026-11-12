"""
Respaldo de la base de datos, con politica de retencion.

Envuelve data.connection.backup() (ya resuelto desde la Fase 0: usa la
API backup() de SQLite, no copia el archivo .db a mano, asi que un
respaldo tomado con WAL activo queda consistente). Lo que agrega esta
capa es la parte de negocio que faltaba: no dejar crecer la carpeta de
respaldos sin limite.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import BACKUP_DIR
from ..data.connection import backup as backup_db

_KEEP_LAST = 5


class BackupService:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def backup_now(self, dest_dir: Path | None = None) -> Path:
        """
        Crea un respaldo nuevo. Con dest_dir=None (el caso normal, "Respaldar
        ahora") va a BACKUP_DIR y se poda a los ultimos _KEEP_LAST -el
        nombre (flexstock_YYYYMMDD_HHMMSS.db) ya ordena cronologicamente
        como texto, no hace falta leer la fecha de modificacion del
        archivo para saber cual es el mas viejo.

        Con un dest_dir propio (carpeta elegida a mano en BackupDialog,
        ej. un pendrive) NO se poda nada: podar una carpeta que la
        persona eligio a proposito, que capaz comparte con otras cosas,
        podria borrar un respaldo viejo que queria conservar ahi sin
        avisarle. La politica de retencion es solo para la carpeta
        propia de la app.
        """
        nuevo = backup_db(self._conn, dest_dir)
        if dest_dir is None:
            self._podar_respaldos_viejos(nuevo.parent)
        return nuevo

    def list_backups(self, backup_dir: Path | None = None) -> list[Path]:
        """Mas reciente primero."""
        directorio = backup_dir or BACKUP_DIR
        return sorted(directorio.glob("flexstock_*.db"), reverse=True)

    def last_backup_at(self, backup_dir: Path | None = None) -> datetime | None:
        """
        Cuando se hizo el ultimo respaldo de la carpeta de la app, o None
        si nunca se hizo ninguno. Lo lee del archivo mismo (fecha de
        modificacion) y no de un dato guardado en settings: un respaldo
        borrado a mano deja de contar, y uno copiado a la carpeta, cuenta.
        Los de un pendrive (Respaldar en...) no se ven desde aca.
        """
        respaldos = self.list_backups(backup_dir)
        if not respaldos:
            return None
        return datetime.fromtimestamp(respaldos[0].stat().st_mtime)  # noqa: DTZ006

    def _podar_respaldos_viejos(self, backup_dir: Path) -> None:
        for viejo in self.list_backups(backup_dir)[_KEEP_LAST:]:
            viejo.unlink()
