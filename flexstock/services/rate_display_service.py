"""
Tasas para MOSTRAR un monto en $ convertido a bolívares.

Existe para que ninguna pantalla haga aritmetica de tasas por su cuenta:
Productos (columnas de precio en Bs), Nueva factura (total convertido),
Exportar y Cotizaciones piden las conversiones aca.

Nada de esto entra en el calculo de una factura: la factura se guarda
en $ con el IVA de siempre. La unica tasa que la factura guarda es la
que se uso para pasarla a Bs al confirmarla (invoices.bs_rate, ver
BillingService.confirmar): es un snapshot para poder imprimirla en Bs
siempre igual. Las cotizaciones congelan las dos tasas en su propia
tabla - ver QuotationService.

Tres tasas, todas en "Bs por USD":
    bcv     oficial, la del encabezado (ExchangeRateService)
    propia  la que carga el negocio (tarjeta "Tasa propia", Modo avanzado)
    usdt    Binance P2P, tambien del encabezado

y una conversion derivada, la misma de las cotizaciones:
    usd_at_bcv = $ x tasa propia / tasa BCV
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from ..data.connection import get_setting, set_setting
from ..domain.bs_breakdown import BsBreakdown, breakdown
from ..domain.entities import Invoice
from ..domain.money import convert_cents
from .exchange_rate_service import ExchangeRateService

PERSONAL = "propia"
BCV = "bcv"
USDT = "usdt"

CLAVES = (BCV, PERSONAL, USDT)
ETIQUETAS = {BCV: "Tasa BCV", PERSONAL: "Tasa propia", USDT: "Tasa USDT"}

_TIPO_POR_CLAVE = {BCV: "usd_oficial", USDT: "usdt"}


@dataclass(frozen=True, slots=True)
class RateSnapshot:
    """
    Las tres tasas leidas UNA vez. Para convertir una tabla entera: sacar
    la foto con RateDisplayService.snapshot() antes del bucle y convertir
    cada fila con esto, que no toca la base. Si no, cada celda convertida
    releeria settings - con 1.936 productos y 2 columnas en Bs eran
    ~5.800 consultas por tecla del buscador (el N+1 del CLAUDE.md).
    """

    personal: Decimal | None
    bcv: Decimal | None
    usdt: Decimal | None

    def rate(self, clave: str) -> Decimal | None:
        return {PERSONAL: self.personal, BCV: self.bcv, USDT: self.usdt}[clave]

    def to_bs(self, cents: int, clave: str = PERSONAL) -> int | None:
        rate = self.rate(clave)
        return None if rate is None else convert_cents(cents, rate)

    def usd_at_bcv(self, cents: int) -> int | None:
        if self.personal is None or self.bcv is None:
            return None
        return convert_cents(cents, self.personal / self.bcv)

    def invoice_in_bs(self, invoice: Invoice, clave: str = BCV) -> BsBreakdown | None:
        """
        Desglose en Bs de una factura, como en una factura fiscal: por
        linea el monto sin IVA, su IVA y el total; abajo las tres sumas.
        None si falta esa tasa. Solo para mostrar: no cambia la factura.

        Cada total es la suma de las lineas YA convertidas, asi las
        columnas cuadran a simple vista (convertir solo el total en $
        podia diferir por un centavo de la suma de las lineas).
        """
        rate = self.rate(clave)
        return None if rate is None else breakdown(invoice, rate)

    def invoice_at_usd_bcv(self, invoice: Invoice) -> BsBreakdown | None:
        """
        El mismo desglose, pero en $ a tasa BCV: el monto en Bs (a la
        tasa de referencia) dividido por la tasa BCV. Es la cuenta de
        las cotizaciones -ver Quotation.factor()- para poder mostrar en
        dolares oficiales lo que se esta cobrando en bolívares. None si
        falta alguna de las dos tasas.

        El campo 'rate' del resultado es ese factor (referencia / BCV),
        no una tasa en Bs por dolar: no tiene sentido mostrarlo.
        """
        if self.personal is None or self.bcv is None:
            return None
        return breakdown(invoice, self.personal / self.bcv)


class RateDisplayService:
    def __init__(
        self, conn: sqlite3.Connection, exchange_rate_service: ExchangeRateService
    ) -> None:
        self._conn = conn
        self._rates = exchange_rate_service

    # ------------------------------------------------------------
    def personal_rate(self) -> Decimal | None:
        return _positive_decimal(get_setting(self._conn, "personal_bs_rate", ""))

    def set_personal_rate(self, rate: Decimal) -> None:
        if rate <= 0:
            raise ValueError("La tasa propia debe ser mayor que cero.")
        set_setting(self._conn, "personal_bs_rate", str(rate))

    def rate(self, clave: str) -> Decimal | None:
        """Bs por USD para esa tasa, o None si todavia no se cargo."""
        if clave == PERSONAL:
            return self.personal_rate()
        return _positive_decimal(self._rates.current_rate(_TIPO_POR_CLAVE[clave]))

    def snapshot(self) -> RateSnapshot:
        """Tres consultas a settings, una sola vez (ver RateSnapshot)."""
        return RateSnapshot(
            personal=self.personal_rate(),
            bcv=self.rate(BCV),
            usdt=self.rate(USDT),
        )

    def to_bs(self, cents: int, clave: str = PERSONAL) -> int | None:
        """Centavos de $ -> centavos de Bs. None si falta esa tasa.
        Para UN monto; para una tabla, usar snapshot()."""
        return self.snapshot().to_bs(cents, clave)

    def usd_at_bcv(self, cents: int) -> int | None:
        """
        El precio "a tasa BCV" de las cotizaciones: se lleva a Bs con la
        tasa propia y se vuelve a $ dividiendo por la BCV. None si falta
        alguna de las dos. Para UN monto; para una tabla, usar snapshot().
        """
        return self.snapshot().usd_at_bcv(cents)


def _positive_decimal(text: str) -> Decimal | None:
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    return value if value > 0 else None
