"""
Para armar en un test una factura "de antes": con otra fecha de emision,
sin bs_rate (anterior a ese campo), con otra tasa.

En la app eso no se puede hacer -y esta bien- porque la base no deja
editar una factura emitida (ver flexstock/data/triggers.sql). Un test que
necesita ese escenario lo dice explicitamente usando esto, que apaga SOLO
ese trigger para una sentencia y lo vuelve a crear enseguida.
"""

import sqlite3

from flexstock.data.connection import create_integrity_triggers


def editar_factura_vieja(
    conn: sqlite3.Connection, sql: str, params: tuple = ()
) -> None:
    conn.execute("DROP TRIGGER IF EXISTS trg_facturas_solo_se_anulan")
    try:
        conn.execute(sql, params)
    finally:
        create_integrity_triggers(conn)
