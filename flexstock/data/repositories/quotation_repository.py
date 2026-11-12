"""
Repositorio de cotizaciones / notas de entrega. Unico archivo que
escribe SQL para 'quotations' y 'quotation_lines'.

    list_summary()  -> cabeceras sin lineas, para el listado.
    get(id)         -> cotizacion completa, con sus lineas (2 consultas).
    create(q)       -> asigna numero e inserta cabecera + lineas.
    mark_cancelled  -> pasa una cotizacion a CANCELLED.

Igual que InvoiceRepository, create() y mark_cancelled() NO abren su
propia transaccion: las abre QuotationService. Eso importa aca mas que
en ningun lado: el numero sale de MAX(number) + 1, y solo es seguro
porque la transaccion es BEGIN IMMEDIATE (toma el lock de escritura
antes de leer el MAX, nadie mas puede insertar en el medio).

No hay update() ni delete(): igual que las facturas, se anulan.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal

from ...domain.entities import InvoiceLine, Quotation
from ...domain.enums import InvoiceStatus, QuoteCurrency

_PREFIX = "NE-"
_PADDING = 6

_COLUMNS = (
    "id, number, display_number, customer_id, customer_name, customer_tax_id, "
    "customer_address, status, tax_rate, personal_rate, bcv_rate, total_cents, "
    "total_bs_cents, total_bcv_cents, notes, issued_at, cancelled_at, cancel_reason"
)

_LINE_COLUMNS = (
    "id, product_id, product_sku, product_name, unit, quantity_milli, "
    "unit_price_cents, tier_id"
)


class QuotationRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_summary(self) -> list[Quotation]:
        rows = self._conn.execute(
            f"SELECT {_COLUMNS} FROM quotations ORDER BY id DESC"
        ).fetchall()
        return [self._row_to_quotation(row) for row in rows]

    def get(self, quotation_id: int) -> Quotation | None:
        row = self._conn.execute(
            f"SELECT {_COLUMNS} FROM quotations WHERE id = ?", (quotation_id,)
        ).fetchone()
        if row is None:
            return None
        quotation = self._row_to_quotation(row)
        quotation.lines = self._load_lines(quotation_id)
        quotation.totals_snapshot = None  # con lineas, se recalcula en vivo
        return quotation

    # ------------------------------------------------------------
    # Escritura (requiere transaccion ya abierta por el llamador)
    # ------------------------------------------------------------
    def create(self, quotation: Quotation) -> int:
        row = self._conn.execute(
            "SELECT COALESCE(MAX(number), 0) + 1 AS siguiente FROM quotations"
        ).fetchone()
        quotation.number = row["siguiente"]
        quotation.display_number = f"{_PREFIX}{quotation.number:0{_PADDING}d}"

        issued_at = quotation.issued_at or datetime.now()  # noqa: DTZ005
        quotation.issued_at = issued_at

        cur = self._conn.execute(
            "INSERT INTO quotations "
            "(number, display_number, customer_id, customer_name, customer_tax_id, "
            " customer_address, status, tax_rate, personal_rate, bcv_rate, "
            " total_cents, total_bs_cents, total_bcv_cents, notes, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quotation.number,
                quotation.display_number,
                quotation.customer_id,
                quotation.customer_name,
                quotation.customer_tax_id,
                quotation.customer_address,
                quotation.status.value,
                str(quotation.tax_rate),
                str(quotation.personal_rate),
                str(quotation.bcv_rate),
                quotation.total_in(QuoteCurrency.USD),
                quotation.total_in(QuoteCurrency.BS),
                quotation.total_in(QuoteCurrency.USD_BCV),
                quotation.notes,
                issued_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        quotation.id = cur.lastrowid

        for line in quotation.lines:
            self._insert_line(quotation.id, line)
        return quotation.id

    def mark_cancelled(self, quotation_id: int, reason: str) -> None:
        cur = self._conn.execute(
            "UPDATE quotations SET status = ?, "
            "cancelled_at = datetime('now', 'localtime'), cancel_reason = ? "
            "WHERE id = ?",
            (InvoiceStatus.CANCELLED.value, reason, quotation_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No existe una cotizacion con id={quotation_id}.")

    # ------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------
    def _insert_line(self, quotation_id: int, line: InvoiceLine) -> None:
        cur = self._conn.execute(
            "INSERT INTO quotation_lines "
            "(quotation_id, product_id, product_sku, product_name, unit, "
            " quantity_milli, unit_price_cents, line_total_cents, tier_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                quotation_id,
                line.product_id,
                line.product_sku,
                line.product_name,
                line.unit,
                line.quantity_milli,
                line.unit_price_cents,
                line.line_total_cents,
                line.tier_id,
            ),
        )
        line.id = cur.lastrowid

    def _load_lines(self, quotation_id: int) -> list[InvoiceLine]:
        rows = self._conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM quotation_lines "
            "WHERE quotation_id = ? ORDER BY id",
            (quotation_id,),
        ).fetchall()
        return [
            InvoiceLine(
                id=row["id"],
                product_id=row["product_id"],
                product_sku=row["product_sku"],
                product_name=row["product_name"],
                unit=row["unit"],
                quantity_milli=row["quantity_milli"],
                unit_price_cents=row["unit_price_cents"],
                tier_id=row["tier_id"],
            )
            for row in rows
        ]

    @staticmethod
    def _row_to_quotation(row: sqlite3.Row) -> Quotation:
        return Quotation(
            id=row["id"],
            number=row["number"],
            display_number=row["display_number"],
            customer_id=row["customer_id"],
            customer_name=row["customer_name"],
            customer_tax_id=row["customer_tax_id"],
            customer_address=row["customer_address"],
            status=InvoiceStatus(row["status"]),
            tax_rate=Decimal(row["tax_rate"]),
            personal_rate=Decimal(row["personal_rate"]),
            bcv_rate=Decimal(row["bcv_rate"]),
            notes=row["notes"],
            issued_at=datetime.fromisoformat(row["issued_at"]),
            cancelled_at=(
                datetime.fromisoformat(row["cancelled_at"])
                if row["cancelled_at"]
                else None
            ),
            cancel_reason=row["cancel_reason"],
            totals_snapshot={
                QuoteCurrency.USD: row["total_cents"],
                QuoteCurrency.BS: row["total_bs_cents"],
                QuoteCurrency.USD_BCV: row["total_bcv_cents"],
            },
        )
