"""Configuracion de rutas. Unico lugar del proyecto que sabe donde viven los archivos."""

from pathlib import Path

APP_NAME = "flexstock-lite"

# Path.home() resuelve solo en Windows y en Linux. Nunca escribas rutas absolutas.
DATA_DIR: Path = Path.home() / APP_NAME / "data"
BACKUP_DIR: Path = Path.home() / APP_NAME / "backups"
EXPORT_DIR: Path = Path.home() / APP_NAME / "exports"

DB_PATH: Path = DATA_DIR / "flexstock.db"
SCHEMA_PATH: Path = Path(__file__).parent / "data" / "schema.sql"
TRIGGERS_PATH: Path = Path(__file__).parent / "data" / "triggers.sql"


def ensure_dirs() -> None:
    """Crea las carpetas de trabajo si no existen. Idempotente."""
    for d in (DATA_DIR, BACKUP_DIR, EXPORT_DIR):
        d.mkdir(parents=True, exist_ok=True)
