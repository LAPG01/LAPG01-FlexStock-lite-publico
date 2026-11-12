"""Vocabulario cerrado del negocio. Espejo de los CHECK del esquema."""

from enum import StrEnum


class MovementType(StrEnum):
    IN = "IN"  # entrada: compra, devolucion que reingresa
    OUT = "OUT"  # salida: venta
    ADJUSTMENT = "ADJUSTMENT"  # conteo fisico, merma, rotura
    INITIAL = "INITIAL"  # carga inicial desde el sistema viejo


class ReferenceType(StrEnum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
    PURCHASE = "PURCHASE"
    COUNT = "COUNT"
    IMPORT = "IMPORT"


class InvoiceStatus(StrEnum):
    ISSUED = "ISSUED"
    CANCELLED = "CANCELLED"


class PaymentMethod(StrEnum):
    """Formas de pago que acepta la ferreteria (definidas con el dueno,
    no se agregan mas sin confirmar con el). El texto para mostrar en
    la UI vive en ui/billing_page.py (mismo criterio que InvoiceStatus:
    el enum es solo el vocabulario cerrado, no como se ve en pantalla)."""

    EFECTIVO_BS = "EFECTIVO_BS"
    EFECTIVO_USD = "EFECTIVO_USD"
    TRANSFERENCIA = "TRANSFERENCIA"
    TARJETA = "TARJETA"
    ZELLE = "ZELLE"


class QuoteCurrency(StrEnum):
    """En que moneda se muestran los montos de una cotizacion/nota de entrega."""

    USD = "USD"  # precio del catalogo, tal cual
    BS = "BS"  # USD x tasa propia
    USD_BCV = "USD_BCV"  # (USD x tasa propia) / tasa BCV


class DocumentType(StrEnum):
    INVOICE = "INVOICE"
    CREDIT_NOTE = "CREDIT_NOTE"
