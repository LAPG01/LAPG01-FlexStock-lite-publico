"""
Repositorio de facturas. Unico archivo que escribe SQL para 'invoices',
'invoice_lines' y (para asignar el numero) 'document_series'.

    list_summary()   -> cabeceras sin lineas. Para el listado de facturas.
    get(id)          -> factura completa, con sus lineas.
    create(invoice)  -> asigna numero/display_number desde document_series
                         e inserta factura + lineas.
    mark_cancelled(id, reason) -> pasa una factura a CANCELLED.

DIFERENCIA con product_repository.py / customer_repository.py /
category_repository.py: create() y mark_cancelled() NO abren su propia
transaccion. La Fase 6 (facturacion) es la primera vez que el proyecto
necesita componer una escritura de facturas con otra cosa (los
movimientos de inventario y el ajuste de current_stock) en una sola
transaccion atomica - exactamente el escenario que ya anticipaba el
docstring de product_repository.py. Por eso estos metodos asumen que
quien los llama (BillingService.confirmar()/cancelar()) ya abrio la
transaccion con 'with transaction(conn):' y la cierra el solo.

Las facturas NUNCA se borran ni se editan (regla 4 del CLAUDE.md): no
hay update() de lineas ni delete() de ningun tipo en este archivo.
"""

import sqlite3
from datetime import datetime
from decimal import Decimal

from ...domain.entities import Invoice, InvoiceLine, PaymentMethodTotal
from ...domain.enums import InvoiceStatus, PaymentMethod
from ...domain.money import convert_cents

_INVOICE_COLUMNS = (
    "id, series_id, number, display_number, customer_id, customer_name, "
    "customer_tax_id, customer_address, status, payment_method, tax_rate, "
    "bs_rate, ref_rate, subtotal_cents, tax_cents, total_cents, notes, issued_at, "
    "cancelled_at, cancel_reason"
)

_LINE_COLUMNS = (
    "id, invoice_id, product_id, product_sku, product_name, unit, "
    "quantity_milli, unit_price_cents, line_total_cents, tier_id"
)


class InvoiceRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_summary(self) -> list[Invoice]:
        """
        Una consulta, sin lineas. Para la tabla que lista facturas:
        cargar las lineas de todas seria trabajo que esa grilla no usa
        (misma politica que ProductRepository.list_summary()).
        """
        rows = self._conn.execute(
            f"SELECT {_INVOICE_COLUMNS} FROM invoices ORDER BY id DESC"
        ).fetchall()
        return [self._row_to_invoice(row) for row in rows]

    def get(self, invoice_id: int) -> Invoice | None:
        """Dos consultas fijas (cabecera + lineas): N=1, no depende del total de facturas."""
        row = self._conn.execute(
            f"SELECT {_INVOICE_COLUMNS} FROM invoices WHERE id = ?",
            (invoice_id,),
        ).fetchone()
        if row is None:
            return None

        invoice = self._row_to_invoice(row)
        invoice.lines = self._load_lines(invoice_id)
        return invoice

    def list_by_customer(self, customer_id: int) -> list[Invoice]:
        """
        Historial de compras de UN cliente puntual (para su ficha en
        CustomerDialog). Sin lineas, igual criterio que list_summary():
        esta vista solo necesita numero/fecha/estado/total, el detalle
        linea por linea ya esta en "Ver detalle" de Facturas. Usa
        idx_invoices_customer (ver schema.sql) para el WHERE.
        """
        rows = self._conn.execute(
            f"SELECT {_INVOICE_COLUMNS} FROM invoices "
            "WHERE customer_id = ? ORDER BY id DESC",
            (customer_id,),
        ).fetchall()
        return [self._row_to_invoice(row) for row in rows]

    # ------------------------------------------------------------
    # Escritura (requiere transaccion ya abierta por el llamador)
    # ------------------------------------------------------------
    def create(self, invoice: Invoice, *, doc_type: str = "INVOICE") -> int:
        """
        Asigna numero/display_number leyendo y actualizando
        document_series, e inserta la factura y sus lineas. Deja
        invoice.id, invoice.series_id, invoice.number y
        invoice.display_number completos al volver.

        No valida reglas de negocio (factura vacia, cliente inactivo,
        etc.): eso ya lo resolvio BillingService antes de llamar aca.
        """
        series = self._conn.execute(
            "SELECT id, prefix, next_number, padding FROM document_series "
            "WHERE doc_type = ?",
            (doc_type,),
        ).fetchone()
        if series is None:
            raise ValueError(f"No existe una serie de documentos para '{doc_type}'.")

        invoice.series_id = series["id"]
        invoice.number = series["next_number"]
        invoice.display_number = (
            f"{series['prefix']}{invoice.number:0{series['padding']}d}"
        )

        self._conn.execute(
            "UPDATE document_series SET next_number = next_number + 1 WHERE id = ?",
            (series["id"],),
        )

        # invoice.issued_at: None (el caso de siempre) usa el reloj de
        # ahora mismo, igual que el DEFAULT que tenia esta columna en el
        # esquema antes de que esto fuera editable. Si la UI ya lo puso
        # (ver InvoiceDialog, "Fecha"/"Hora" al confirmar), se respeta
        # ese valor en vez de pisarlo con el de ahora.
        issued_at = invoice.issued_at or datetime.now()  # noqa: DTZ005
        invoice.issued_at = issued_at

        cur = self._conn.execute(
            "INSERT INTO invoices "
            "(series_id, number, display_number, customer_id, customer_name, "
            " customer_tax_id, customer_address, status, payment_method, "
            " tax_rate, bs_rate, ref_rate, subtotal_cents, tax_cents, total_cents, "
            " notes, issued_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                invoice.series_id,
                invoice.number,
                invoice.display_number,
                invoice.customer_id,
                invoice.customer_name,
                invoice.customer_tax_id,
                invoice.customer_address,
                invoice.status.value,
                invoice.payment_method.value if invoice.payment_method else None,
                str(invoice.tax_rate),
                str(invoice.bs_rate) if invoice.bs_rate is not None else None,
                str(invoice.ref_rate) if invoice.ref_rate is not None else None,
                invoice.subtotal_cents,
                invoice.tax_cents,
                invoice.total_cents,
                invoice.notes,
                issued_at.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        invoice.id = cur.lastrowid

        for line in invoice.lines:
            self._insert_line(invoice.id, line)

        return invoice.id

    def totals_by_payment_method(
        self, *, start: str, end: str
    ) -> list[PaymentMethodTotal]:
        """
        Cierre de caja: UNA consulta, nunca una por factura - eso seria
        el N+1 que prohibe la Politica de carga del CLAUDE.md.

        La suma se hace en Python y no con GROUP BY/SUM, por el total
        en bolivares: cada factura se cobro a SU tasa (bs_rate, regla
        5), asi que los Bs del grupo son la suma de total_cents x
        bs_rate factura por factura, no el total del grupo por una sola
        tasa. Y bs_rate se guarda como TEXT justamente para no perder
        precision, asi que multiplicarlo en SQL obligaria a CAST(... AS
        REAL) - dinero en punto flotante, lo que prohibe la regla 1.
        Con Decimal en Python el numero es exacto.

        El costo de traer filas sueltas en vez de agregadas es una fila
        de tres columnas chicas por factura del periodo: un mes movido
        de esta ferreteria son cientos de filas, decenas de KB. Si
        alguna vez fueran cientos de miles, el camino seria guardar el
        monto en Bs al confirmar, no volver a agregar en SQL.

        start/end: TEXT ISO 'YYYY-MM-DD HH:MM:SS', rango semiabierto
        [start, end) sobre issued_at - quien llama (BillingService)
        arma el rango (un dia o un mes calendario). La comparacion de
        strings funciona porque issued_at siempre se guarda en formato
        ISO, que ordena igual que ordenan las fechas.

        Solo status='ISSUED': una factura CANCELLED (regla 4 del
        CLAUDE.md, se anula en vez de borrarse) no es plata que entro
        de verdad, y contarla haria que la caja "sobre" contra lo que
        de verdad hay en el cajon.
        """
        rows = self._conn.execute(
            "SELECT payment_method, total_cents, bs_rate "
            "FROM invoices "
            "WHERE status = ? AND issued_at >= ? AND issued_at < ?",
            (InvoiceStatus.ISSUED.value, start, end),
        ).fetchall()

        # dict normal, no defaultdict: el orden de insercion es el orden
        # en que aparecen las formas de pago, y asi el cierre las lista
        # igual que antes (las mas viejas primero).
        acumulado: dict[str | None, list[int]] = {}
        for row in rows:
            grupo = acumulado.setdefault(row["payment_method"], [0, 0, 0])
            grupo[0] += 1
            grupo[1] += row["total_cents"]
            if row["bs_rate"] is not None:
                grupo[2] += convert_cents(row["total_cents"], Decimal(row["bs_rate"]))

        return [
            PaymentMethodTotal(
                payment_method=(PaymentMethod(metodo) if metodo is not None else None),
                count=cantidad,
                total_cents=total,
                total_bs_cents=total_bs,
            )
            for metodo, (cantidad, total, total_bs) in acumulado.items()
        ]

    def list_by_period(
        self, *, start: str, end: str, payment_method: PaymentMethod | None
    ) -> list[Invoice]:
        """
        Las facturas que hay detras de UNA fila del cierre de caja: el
        mismo rango y el mismo filtro de status que
        totals_by_payment_method, pero sin agrupar.

        payment_method=None NO significa "todas": significa el grupo
        "Sin especificar", las facturas que quedaron sin forma de pago
        (emitidas antes de que existiera la columna). Es la misma
        semantica que PaymentMethodTotal.payment_method, para que la
        fila que se toca y las facturas que salen sean exactamente el
        mismo conjunto. Por eso el WHERE cambia a 'IS NULL': en SQL
        'payment_method = NULL' no matchea nunca, ni siquiera con NULL.

        Sin lineas, igual criterio que list_summary(): esta lista
        muestra numero, fecha, cliente y totales. Las lineas las carga
        get(), de a una factura, recien al abrir su detalle.
        """
        if payment_method is None:
            condicion, extra = "payment_method IS NULL", ()
        else:
            condicion, extra = "payment_method = ?", (payment_method.value,)

        rows = self._conn.execute(
            f"SELECT {_INVOICE_COLUMNS} FROM invoices "
            f"WHERE status = ? AND issued_at >= ? AND issued_at < ? AND {condicion} "
            "ORDER BY issued_at, id",
            (InvoiceStatus.ISSUED.value, start, end, *extra),
        ).fetchall()
        return [self._row_to_invoice(row) for row in rows]

    def mark_cancelled(self, invoice_id: int, reason: str) -> None:
        """
        Pasa la factura a CANCELLED. No revierte stock: eso lo hace
        StockService, orquestado por BillingService.cancelar() dentro
        de la misma transaccion que abre este UPDATE.
        """
        cur = self._conn.execute(
            "UPDATE invoices SET status = ?, "
            "cancelled_at = datetime('now', 'localtime'), cancel_reason = ? "
            "WHERE id = ?",
            (InvoiceStatus.CANCELLED.value, reason, invoice_id),
        )
        if cur.rowcount == 0:
            raise ValueError(f"No existe una factura con id={invoice_id}.")

    # ------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------
    def _insert_line(self, invoice_id: int, line: InvoiceLine) -> None:
        cur = self._conn.execute(
            "INSERT INTO invoice_lines "
            "(invoice_id, product_id, product_sku, product_name, unit, "
            " quantity_milli, unit_price_cents, line_total_cents, tier_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                invoice_id,
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

    def _load_lines(self, invoice_id: int) -> list[InvoiceLine]:
        rows = self._conn.execute(
            f"SELECT {_LINE_COLUMNS} FROM invoice_lines "
            "WHERE invoice_id = ? ORDER BY id",
            (invoice_id,),
        ).fetchall()
        return [self._row_to_line(row) for row in rows]

    @staticmethod
    def _row_to_invoice(row: sqlite3.Row) -> Invoice:
        return Invoice(
            id=row["id"],
            series_id=row["series_id"],
            number=row["number"],
            display_number=row["display_number"],
            customer_id=row["customer_id"],
            customer_name=row["customer_name"],
            customer_tax_id=row["customer_tax_id"],
            customer_address=row["customer_address"],
            status=InvoiceStatus(row["status"]),
            payment_method=(
                PaymentMethod(row["payment_method"])
                if row["payment_method"] is not None
                else None
            ),
            tax_rate=Decimal(row["tax_rate"]),
            bs_rate=(Decimal(row["bs_rate"]) if row["bs_rate"] is not None else None),
            ref_rate=(
                Decimal(row["ref_rate"]) if row["ref_rate"] is not None else None
            ),
            notes=row["notes"],
            issued_at=datetime.fromisoformat(row["issued_at"]),
            cancelled_at=(
                datetime.fromisoformat(row["cancelled_at"])
                if row["cancelled_at"]
                else None
            ),
            cancel_reason=row["cancel_reason"],
            totals_snapshot=(
                row["subtotal_cents"],
                row["tax_cents"],
                row["total_cents"],
            ),
        )

    @staticmethod
    def _row_to_line(row: sqlite3.Row) -> InvoiceLine:
        return InvoiceLine(
            id=row["id"],
            product_id=row["product_id"],
            product_sku=row["product_sku"],
            product_name=row["product_name"],
            unit=row["unit"],
            quantity_milli=row["quantity_milli"],
            unit_price_cents=row["unit_price_cents"],
            tier_id=row["tier_id"],
        )
