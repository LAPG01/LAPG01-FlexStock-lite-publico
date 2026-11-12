"""
Repositorio de niveles de precio (price_tiers).

INCOMPLETO A PROPOSITO: por ahora solo trae el listado completo. Es lo
que necesita legacy_importer.py para mapear 'Precio 1/2/3' del txt
viejo a su tier_id real: la posicion (1, 2, 3) no garantiza que el id
en la base sea 1, 2, 3 (eso depende del autoincrement), asi que hay
que resolverlo por 'position', no asumirlo.
"""

import sqlite3

from ...domain.entities import PriceTier


class PriceTierRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def list_all(self) -> list[PriceTier]:
        rows = self._conn.execute(
            "SELECT id, name, position, is_default FROM price_tiers ORDER BY position"
        ).fetchall()
        return [
            PriceTier(
                id=row["id"],
                name=row["name"],
                position=row["position"],
                is_default=bool(row["is_default"]),
            )
            for row in rows
        ]
