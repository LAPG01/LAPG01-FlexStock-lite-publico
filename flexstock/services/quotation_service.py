"""
Cotizaciones / notas de entrega (Modo avanzado).

Mismo ciclo que BillingService - borrador en memoria, una sola escritura
al crear, anular en vez de borrar - pero con dos diferencias a proposito:

1. NUNCA mueve stock ni toca invoices/invoice_lines: es un documento
   informal, no una venta registrada. No hay InventoryMovement ni entra
   al cierre de caja.
2. Cada linea toma el PRECIO 3 del catalogo (tier de position 3) y se
   muestra en tres monedas a la vez, con dos tasas:
       Bs     = USD x tasa propia (settings.personal_bs_rate)
       $ BCV  = Bs / tasa BCV     (ExchangeRateService, la del encabezado)
   Las dos se vuelven a leer y se congelan al crear (regla 5).
"""

import sqlite3
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from ..config import EXPORT_DIR
from ..data.connection import get_setting, transaction
from ..data.repositories.customer_repository import CustomerRepository
from ..data.repositories.price_tier_repository import PriceTierRepository
from ..data.repositories.product_repository import ProductRepository
from ..data.repositories.quotation_repository import QuotationRepository
from ..domain.entities import InvoiceLine, Product, Quotation
from ..domain.enums import QuoteCurrency
from ..domain.errors import EmptyDocument, InvoiceAlreadyCancelled, ReasonRequired
from ..domain.money import convert_cents, from_milli, to_cents, to_milli
from ..reports.quotation_pdf import render_quotation
from .exchange_rate_service import ExchangeRateService
from .rate_display_service import BCV, RateDisplayService
from .settings_service import SettingsService, store_info_for_pdf

_PRICE_POSITION = 3  # "Precio 3" del sistema viejo


class MissingRate(ValueError):
    """Falta la tasa propia o la BCV: sin ellas no hay precio en Bs ni en $ BCV."""


class QuotationService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        quotation_repo: QuotationRepository,
        product_repo: ProductRepository,
        customer_repo: CustomerRepository,
        tier_repo: PriceTierRepository,
        exchange_rate_service: ExchangeRateService,
    ) -> None:
        self._conn = conn
        self._quotations = quotation_repo
        self._products = product_repo
        self._customers = customer_repo
        self._tiers = tier_repo
        # La MISMA tasa propia que la tarjeta del encabezado y el selector
        # de Nueva factura: un solo dueno (RateDisplayService), asi
        # cambiarla en cualquier lado la cambia en todos.
        self._rates = RateDisplayService(conn, exchange_rate_service)

    # ------------------------------------------------------------
    # Tasas
    # ------------------------------------------------------------
    def personal_rate(self) -> Decimal | None:
        return self._rates.personal_rate()

    def set_personal_rate(self, rate: Decimal) -> None:
        self._rates.set_personal_rate(rate)

    def bcv_rate(self) -> Decimal | None:
        return self._rates.rate(BCV)

    def default_tax_rate(self) -> Decimal:
        return Decimal(get_setting(self._conn, "default_tax_rate", "0"))

    def company_name(self) -> str:
        return get_setting(self._conn, "company_name", "Ferreteria")

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_quotations(self) -> list[Quotation]:
        return self._quotations.list_summary()

    def get_quotation(self, quotation_id: int) -> Quotation | None:
        return self._quotations.get(quotation_id)

    # ------------------------------------------------------------
    # Borrador en memoria
    # ------------------------------------------------------------
    def crear_borrador(
        self, *, customer_id: int | None = None, customer_name: str | None = None
    ) -> Quotation:
        personal, bcv = self._tasas_vigentes()

        if customer_id is not None:
            customer = self._customers.get(customer_id)
            if customer is None:
                raise ValueError(f"No existe un cliente con id={customer_id}.")
            name, tax_id, address = customer.name, customer.tax_id, customer.address
        else:
            name = (customer_name or "").strip()
            if not name:
                raise ValueError(
                    "Se necesita un cliente registrado o un nombre para la cotización."
                )
            tax_id = address = None

        return Quotation(
            id=None,
            display_number="",
            customer_name=name,
            tax_rate=self.default_tax_rate(),
            personal_rate=personal,
            bcv_rate=bcv,
            customer_id=customer_id,
            customer_tax_id=tax_id,
            customer_address=address,
            issued_at=datetime.now(),  # noqa: DTZ005
        )

    def agregar_linea(
        self, quotation: Quotation, *, product_id: int, quantity: Decimal
    ) -> InvoiceLine:
        """Una consulta de producto (get) por linea agregada a mano: N=1."""
        quantity_milli = to_milli(quantity)
        if quantity_milli <= 0:
            raise ValueError("La cantidad debe ser mayor que cero.")

        product = self._products.get(product_id)
        if product is None:
            raise ValueError(f"No existe un producto con id={product_id}.")
        if not product.is_active:
            raise ValueError(f"'{product.name}' esta inactivo.")

        tier_id = self.price_tier_id()
        price = product.price_for(tier_id)
        if price is None:
            raise ValueError(f"'{product.name}' no tiene Precio 3 definido.")

        line = InvoiceLine(
            id=None,
            product_id=product.id,
            product_sku=product.sku,
            product_name=product.name,
            unit=product.unit,
            quantity_milli=quantity_milli,
            unit_price_cents=to_cents(price),
            tier_id=tier_id,
        )
        quotation.lines.append(line)
        return line

    def quitar_linea(self, quotation: Quotation, index: int) -> None:
        if index < 0 or index >= len(quotation.lines):
            raise ValueError(f"No existe una línea en la posición {index}.")
        del quotation.lines[index]

    def set_cobrar_iva(self, quotation: Quotation, cobrar: bool) -> None:
        quotation.tax_rate = self.default_tax_rate() if cobrar else Decimal(0)

    def price_preview(self, product: Product) -> dict[QuoteCurrency, int | None] | None:
        """
        Precio 3 de un producto ya cargado (con precios, ver
        ProductService.list_products) en las 3 monedas, con las tasas
        vigentes - para mostrarlo al elegirlo en la lista, antes de
        agregarlo. Sin consultas a productos. None si no tiene Precio 3;
        una moneda en None si falta su tasa.
        """
        price_milli = product.price_milli_for(self.price_tier_id())
        if price_milli is None:
            return None
        usd = to_cents(from_milli(price_milli))
        personal, bcv = self.personal_rate(), self.bcv_rate()
        return {
            QuoteCurrency.USD: usd,
            QuoteCurrency.BS: convert_cents(usd, personal) if personal else None,
            QuoteCurrency.USD_BCV: (
                convert_cents(usd, personal / bcv) if personal and bcv else None
            ),
        }

    def price_tier_id(self) -> int:
        for tier in self._tiers.list_all():
            if tier.position == _PRICE_POSITION:
                return tier.id
        raise ValueError("No existe el nivel de precio 3 en price_tiers.")

    # ------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------
    def confirmar(self, quotation: Quotation, *, cobrar_iva: bool = True) -> Quotation:
        """
        Una transaccion: cabecera + lineas. Sin movimientos de stock.
        Vuelve a leer tasas, IVA y datos del cliente justo antes de
        insertar - son los snapshots "al emitir" (regla 5).
        """
        if not quotation.lines:
            raise EmptyDocument("Una cotización sin líneas no se puede crear.")

        quotation.personal_rate, quotation.bcv_rate = self._tasas_vigentes()
        self.set_cobrar_iva(quotation, cobrar_iva)
        if quotation.customer_id is not None:
            customer = self._customers.get(quotation.customer_id)
            if customer is None:
                raise ValueError(f"El cliente id={quotation.customer_id} ya no existe.")
            quotation.customer_name = customer.name
            quotation.customer_tax_id = customer.tax_id
            quotation.customer_address = customer.address

        with transaction(self._conn):
            self._quotations.create(quotation)
        return quotation

    def cancelar(self, quotation_id: int, reason: str) -> Quotation:
        reason = (reason or "").strip()
        if not reason:
            raise ReasonRequired("Anular una cotización necesita un motivo.")
        quotation = self._quotations.get(quotation_id)
        if quotation is None:
            raise ValueError(f"No existe una cotización con id={quotation_id}.")
        if quotation.is_cancelled:
            raise InvoiceAlreadyCancelled(
                f"La cotización {quotation.display_number} ya estaba anulada."
            )
        with transaction(self._conn):
            self._quotations.mark_cancelled(quotation_id, reason)
        return self._quotations.get(quotation_id)

    # ------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------
    def generate_pdf(
        self,
        quotation_id: int,
        currency: QuoteCurrency,
        *,
        export_dir: Path | None = None,
    ) -> Path:
        quotation = self._quotations.get(quotation_id)
        if quotation is None:
            raise ValueError(f"No existe una cotización con id={quotation_id}.")
        return render_quotation(
            quotation,
            currency,
            company_name=self.company_name(),
            company_tax_id=get_setting(self._conn, "company_tax_id", ""),
            company_phone=get_setting(self._conn, "company_phone", ""),
            # El mismo logo que la factura: antes la nota de entrega salia
            # con el hueco del logo vacio a la izquierda del encabezado.
            company_logo=SettingsService(self._conn).logo(),
            company_payment_qr=SettingsService(self._conn).payment_qr(),
            **store_info_for_pdf(self._conn),
            export_dir=export_dir or EXPORT_DIR,
        )

    # ------------------------------------------------------------
    def _tasas_vigentes(self) -> tuple[Decimal, Decimal]:
        personal = self.personal_rate()
        if personal is None:
            raise MissingRate(
                "Falta la tasa propia: cárgala tocando la tarjeta 'Tasa propia' "
                "del encabezado."
            )
        bcv = self.bcv_rate()
        if bcv is None:
            raise MissingRate(
                "Falta la tasa BCV: cárgala tocando la tarjeta 'BCV' del encabezado."
            )
        return personal, bcv
