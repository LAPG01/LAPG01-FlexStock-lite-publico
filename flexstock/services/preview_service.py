"""
Vista previa de un borrador (factura o cotizacion) como COTIZACION en PDF.

Existe para poder ver, o mandarle a un cliente que solo pregunta
precios, el documento ANTES de crearlo. No escribe nada: no numera, no
mueve stock, no toca ni 'invoices' ni 'quotations' (regla 4 del
CLAUDE.md: la factura solo nace al confirmarla). Lo unico que hace es
convertir los montos del borrador y pasarselos a reports/preview_pdf.py.

Que se muestra en cada columna ($ y Bs) es lo unico "delicado" de este
modulo, y sigue la misma regla en los dos documentos:

    Bs = $ del catalogo x tasa elegida
    $  = el $ del catalogo con la tasa BCV; con la tasa de referencia,
         esos Bs divididos entre la BCV (la cuenta de las cotizaciones)

Con la tasa de referencia el $ del catalogo NO se imprime: si saliera
junto a los Bs, el cliente podria dividir y ver la tasa que se uso.
"""

import sqlite3
from datetime import datetime
from pathlib import Path

from ..config import EXPORT_DIR
from ..data.connection import get_setting
from ..domain.entities import Invoice, Quotation
from ..domain.enums import QuoteCurrency
from ..domain.money import format_rate
from ..reports.preview_pdf import (
    PreviewDocument,
    PreviewLine,
    render_preview,
)
from .rate_display_service import BCV, PERSONAL, RateDisplayService
from .settings_service import SettingsService, store_info_for_pdf

_ETIQUETAS = {BCV: "la tasa BCV", PERSONAL: "la tasa de referencia"}


class PreviewService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        rate_display_service: RateDisplayService | None = None,
    ) -> None:
        self._conn = conn
        self._rates = rate_display_service

    # ------------------------------------------------------------
    # factura
    # ------------------------------------------------------------
    def invoice_pdf(
        self,
        invoice: Invoice,
        tasa: str = BCV,
        *,
        issued_at: datetime | None = None,
        export_dir: Path | None = None,
    ) -> Path:
        return self.render(
            self.invoice_preview(invoice, tasa, issued_at=issued_at),
            export_dir=export_dir,
        )

    def invoice_preview(
        self,
        invoice: Invoice,
        tasa: str = BCV,
        *,
        issued_at: datetime | None = None,
    ) -> PreviewDocument:
        """
        'tasa' es la que esta elegida en pantalla (BCV o PERSONAL).
        Sin servicio de tasas solo sale la columna en $. Lanza ValueError
        (mensaje en espanol, listo para un messagebox) si el borrador esta
        vacio o falta cargar una tasa.
        """
        if not invoice.lines:
            raise ValueError("Agrega al menos una línea para ver la vista previa.")
        symbol = self._currency_symbol()
        fecha = issued_at or invoice.issued_at or datetime.now()  # noqa: DTZ005 - hora local, igual que el resto del esquema

        if self._rates is None:
            lineas = tuple(
                PreviewLine(
                    line.product_name,
                    line.quantity_milli,
                    line.unit_price_cents,
                    None,
                    line.line_total_cents,
                    None,
                )
                for line in invoice.lines
            )
            usd = (invoice.subtotal_cents, invoice.tax_cents, invoice.total_cents)
            return self._documento(invoice, lineas, fecha, symbol, usd, None)

        tasas = self._rates.snapshot()  # UNA lectura de tasas, no una por linea
        en_bs = tasas.invoice_in_bs(invoice, tasa)
        # La BCV del dia, a la vista en el recuadro MONEDA (como en la factura
        # confirmada). Con la tasa de referencia no: delataria la cuenta.
        tasa_texto = (
            f"Tasa BCV: {format_rate(tasas.bcv)} Bs por dólar"
            if tasa == BCV and tasas.bcv is not None
            else None
        )
        if en_bs is None:
            raise ValueError(
                f"Falta cargar {_ETIQUETAS[tasa]}: cárgala desde el encabezado."
            )
        bs = (en_bs.subtotal, en_bs.tax, en_bs.total)

        if tasa == PERSONAL:
            en_usd = tasas.invoice_at_usd_bcv(invoice)
            if en_usd is None:
                raise ValueError(
                    f"Falta cargar {_ETIQUETAS[BCV]}: cárgala desde el encabezado."
                )
            usd = (en_usd.subtotal, en_usd.tax, en_usd.total)
            lineas = tuple(
                PreviewLine(
                    line.product_name,
                    line.quantity_milli,
                    en_usd.lines[i].unitario,
                    en_bs.lines[i].unitario,
                    en_usd.lines[i].sin_iva,
                    en_bs.lines[i].sin_iva,
                )
                for i, line in enumerate(invoice.lines)
            )
        else:
            usd = (invoice.subtotal_cents, invoice.tax_cents, invoice.total_cents)
            lineas = tuple(
                PreviewLine(
                    line.product_name,
                    line.quantity_milli,
                    line.unit_price_cents,
                    en_bs.lines[i].unitario,
                    line.line_total_cents,
                    en_bs.lines[i].sin_iva,
                )
                for i, line in enumerate(invoice.lines)
            )
        return self._documento(invoice, lineas, fecha, symbol, usd, bs, tasa_texto)

    # ------------------------------------------------------------
    # cotizacion
    # ------------------------------------------------------------
    def quotation_pdf(
        self,
        quotation: Quotation,
        *,
        issued_at: datetime | None = None,
        export_dir: Path | None = None,
    ) -> Path:
        return self.render(
            self.quotation_preview(quotation, issued_at=issued_at),
            export_dir=export_dir,
        )

    def quotation_preview(
        self, quotation: Quotation, *, issued_at: datetime | None = None
    ) -> PreviewDocument:
        """
        Lo que ve el cliente de una cotizacion: los Bs (precio x la tasa
        que se congelo en el borrador) y los $ a tasa BCV. Nunca el $ del
        catalogo. Las dos tasas ya vienen en el borrador (ver
        QuotationService.crear_borrador).
        """
        if not quotation.lines:
            raise ValueError("Agrega al menos una línea para ver la vista previa.")
        usd_bcv, bs = QuoteCurrency.USD_BCV, QuoteCurrency.BS
        lineas = tuple(
            PreviewLine(
                line.product_name,
                line.quantity_milli,
                quotation.unit_price_in(line, usd_bcv),
                quotation.unit_price_in(line, bs),
                quotation.line_total_in(line, usd_bcv),
                quotation.line_total_in(line, bs),
            )
            for line in quotation.lines
        )
        return PreviewDocument(
            customer_name=quotation.customer_name,
            tax_rate=quotation.tax_rate,
            lines=lineas,
            issued_at=issued_at or quotation.issued_at or datetime.now(),  # noqa: DTZ005 - hora local, igual que el resto del esquema
            totals_usd=quotation.totals_in(usd_bcv),
            totals_bs=quotation.totals_in(bs),
            customer_tax_id=quotation.customer_tax_id,
            customer_address=quotation.customer_address,
            currency_symbol=self._currency_symbol(),
            # El modulo Cotizaciones es en realidad de notas de entrega:
            # el mismo titulo del PDF que ya se genera al guardarlas.
            title="NOTA DE ENTREGA",
            legend="Documento no fiscal",
        )

    # ------------------------------------------------------------
    def render(
        self, document: PreviewDocument, *, export_dir: Path | None = None
    ) -> Path:
        """
        Dibuja el PDF. Va a una subcarpeta propia de EXPORT_DIR, para no
        mezclar estos borradores desechables con los PDF de documentos
        reales.
        """
        return render_preview(
            document,
            company_name=get_setting(self._conn, "company_name", "Ferreteria"),
            company_tax_id=get_setting(self._conn, "company_tax_id", ""),
            company_phone=get_setting(self._conn, "company_phone", ""),
            company_logo=SettingsService(self._conn).logo(),
            company_payment_qr=SettingsService(self._conn).payment_qr(),
            **store_info_for_pdf(self._conn),
            export_dir=export_dir or EXPORT_DIR / "vistas_previas",
        )

    def _documento(
        self,
        invoice: Invoice,
        lineas: tuple[PreviewLine, ...],
        fecha: datetime,
        symbol: str,
        usd: tuple[int, int, int] | None,
        bs: tuple[int, int, int] | None,
        rate_text: str | None = None,
    ) -> PreviewDocument:
        return PreviewDocument(
            customer_name=invoice.customer_name,
            tax_rate=invoice.tax_rate,
            lines=lineas,
            issued_at=fecha,
            totals_usd=usd,
            totals_bs=bs,
            customer_tax_id=invoice.customer_tax_id,
            customer_address=invoice.customer_address,
            currency_symbol=symbol,
            rate_text=rate_text,
        )

    def _currency_symbol(self) -> str:
        return get_setting(self._conn, "currency_symbol", "$")
