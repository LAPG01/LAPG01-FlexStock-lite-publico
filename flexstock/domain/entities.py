"""
Entidades del negocio.

REGLA INNEGOCIABLE de esta carpeta: aqui no se importa sqlite3 ni tkinter.
Si algun dia ves uno de esos imports en domain/, el diseno se rompio.

Los campos numericos guardan enteros escalados (ver money.py). Las
propiedades exponen Decimal para que el resto del sistema no haga
aritmetica con centavos a mano.
"""

from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from .enums import (
    InvoiceStatus,
    MovementType,
    PaymentMethod,
    QuoteCurrency,
    ReferenceType,
)
from .money import convert_cents, from_cents, from_milli


# ------------------------------------------------------------------
# Catalogo
# ------------------------------------------------------------------
@dataclass(slots=True)
class Category:
    id: int | None
    code: str  # prefijo del SKU: 'FER'
    name: str
    is_active: bool = True


@dataclass(slots=True)
class Supplier:
    id: int | None
    sku: str  # 'PROV-0001', autogenerado por SupplierService
    name: str  # nombre canonico ya normalizado
    tax_id: str | None = None
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    contact_name: str | None = None  # contacto principal del proveedor;
    # distinto del contacto por producto en product_suppliers (SupplierLink),
    # que es de la RELACION, no de esta entidad
    notes: str | None = None
    is_active: bool = True
    created_at: datetime | None = None


@dataclass(slots=True)
class PriceTier:
    id: int | None
    name: str  # 'Detal', 'Mayor', 'Especial'
    position: int
    is_default: bool = False


@dataclass(slots=True)
class ProductPrice:
    tier_id: int
    price_milli: int  # SIN impuesto

    @property
    def price(self) -> Decimal:
        return from_milli(self.price_milli)


@dataclass(slots=True)
class SupplierLink:
    """Relacion producto-proveedor. Los datos son de la RELACION, no de las entidades."""

    supplier_id: int
    supplier_name: str = ""
    contact_name: str | None = None
    last_purchase: date | None = None
    last_cost_milli: int | None = None
    note: str | None = None


@dataclass(slots=True)
class SupplierEntry:
    """
    Una fila del historial de entregas (product_supplier_entries): quien
    trajo ESTE producto de ESTE proveedor, cuando y a cuanto. A
    diferencia de SupplierLink (el vinculo ACTUAL, uno por producto-
    proveedor) puede haber muchas por par, una por cada vez que se
    registro una entrega.

    supplier_id/supplier_name van aca (no solo en SupplierLink) porque
    la ficha del producto muestra TODAS las entregas de TODOS sus
    proveedores juntas en una sola tabla (ver ProductRepository.
    list_entries_for_product): sin esto no habria forma de saber de
    quien es cada fila ni de abrir su ficha.

    product_id/product_sku/product_name son 0/"" salvo en el historial
    DEL CATALOGO COMPLETO (ver ProductRepository.list_entries_for_export,
    para ExportService): ahi cada fila puede ser de un producto
    distinto, y sin esto no habria forma de saber de cual.
    """

    id: int
    supplier_id: int = 0
    supplier_name: str = ""
    product_id: int = 0
    product_sku: str = ""
    product_name: str = ""
    contact_name: str | None = None
    entry_date: date | None = None
    cost_milli: int | None = None
    note: str | None = None


@dataclass(slots=True)
class ProductLink:
    """
    Misma relacion producto-proveedor que SupplierLink, mirada desde el
    proveedor: que productos le compra la ferreteria (para la ficha de
    un proveedor, reverso de Product.suppliers).
    """

    product_id: int
    product_sku: str = ""
    product_name: str = ""
    current_stock: int = 0
    contact_name: str | None = None
    last_purchase: date | None = None
    last_cost_milli: int | None = None
    note: str | None = None

    @property
    def stock(self) -> Decimal:
        return from_milli(self.current_stock)


@dataclass(slots=True)
class Product:
    id: int | None
    sku: str
    name: str
    legacy_code: str | None = None  # puente con el sistema viejo
    description: str = ""
    category_id: int | None = None
    unit: str = "UNI"
    cost_milli: int = 0
    current_stock: int = 0  # CACHE de inventory_movements
    min_stock: int = 0
    is_active: bool = True
    prices: list[ProductPrice] = field(default_factory=list)
    suppliers: list[SupplierLink] = field(default_factory=list)

    @property
    def cost(self) -> Decimal:
        return from_milli(self.cost_milli)

    @property
    def stock(self) -> Decimal:
        return from_milli(self.current_stock)

    @property
    def is_low_stock(self) -> bool:
        return self.min_stock > 0 and self.current_stock <= self.min_stock

    def price_for(self, tier_id: int) -> Decimal | None:
        for p in self.prices:
            if p.tier_id == tier_id:
                return p.price
        return None

    def price_milli_for(self, tier_id: int) -> int | None:
        """Como price_for(), pero sin pasar por from_milli(): para formatear
        con format_money_milli() sin reconvertir Decimal -> milli a mano."""
        for p in self.prices:
            if p.tier_id == tier_id:
                return p.price_milli
        return None

    def margin_pct(self, tier_id: int) -> Decimal | None:
        """Regla pura de negocio: no necesita base de datos ni ventana."""
        price = self.price_for(tier_id)
        if price is None or self.cost_milli == 0:
            return None
        return ((price - self.cost) / self.cost * 100).quantize(Decimal("0.01"))


@dataclass(slots=True)
class Customer:
    id: int | None
    sku: str  # 'CLI-0001', autogenerado por CustomerService
    name: str
    tax_id: str | None = None  # cedula / RIF: 'V-12345678'
    phone: str | None = None
    email: str | None = None
    address: str | None = None
    notes: str | None = None
    is_wholesale: bool = False
    is_active: bool = True
    created_at: datetime | None = None


# ------------------------------------------------------------------
# Documentos
# ------------------------------------------------------------------
@dataclass(slots=True)
class InvoiceLine:
    """
    Los campos product_sku/product_name/unit_price son SNAPSHOTS: se copian
    al emitir y no cambian nunca. Si el tornillo sube de 2,50 a 3,00, la
    factura de marzo sigue diciendo 2,50.
    """

    id: int | None
    product_id: int | None
    product_sku: str
    product_name: str
    unit: str
    quantity_milli: int
    unit_price_cents: int
    tier_id: int | None = None

    @property
    def quantity(self) -> Decimal:
        return from_milli(self.quantity_milli)

    @property
    def unit_price(self) -> Decimal:
        return from_cents(self.unit_price_cents)

    @property
    def line_total_cents(self) -> int:
        """Se calcula, no se guarda a ciegas. El servicio verifica que cuadre."""
        return round(self.unit_price_cents * self.quantity_milli / 1000)

    @property
    def line_total(self) -> Decimal:
        return from_cents(self.line_total_cents)


@dataclass(slots=True)
class Invoice:
    id: int | None
    display_number: str  # 'FAC-000531'
    customer_name: str  # SNAPSHOT
    tax_rate: Decimal  # SNAPSHOT, ej Decimal('16.00')
    number: int = 0
    series_id: int | None = None
    customer_id: int | None = None
    customer_tax_id: str | None = None
    customer_address: str | None = None
    status: InvoiceStatus = InvoiceStatus.ISSUED
    # None = sin forma de pago (factura emitida antes de este campo).
    # La UI exige elegir una al confirmar una factura NUEVA (ver
    # InvoiceDialog/BillingService); a nivel de entidad/esquema queda
    # opcional para no romper facturas viejas.
    payment_method: PaymentMethod | None = None
    # Bs por $ con que se paso a bolivares al confirmar (snapshot, ver
    # schema.sql). None = factura anterior a este campo.
    bs_rate: Decimal | None = None
    # Tasa de referencia con que se alteraron los precios al facturar
    # (Modo avanzado). None = se cobro el precio del catalogo tal cual.
    ref_rate: Decimal | None = None
    notes: str | None = None
    issued_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    lines: list[InvoiceLine] = field(default_factory=list)
    # Snapshot de subtotal_cents/tax_cents/total_cents ya calculados,
    # SOLO para cuando InvoiceRepository.list_summary() arma un Invoice
    # sin cargar 'lines' (evitar el N+1 de traer las lineas de cada
    # factura nomas para sumarlas - Politica de carga del CLAUDE.md).
    # None = todavia no se persistio (el borrador en memoria de
    # BillingService antes de confirmar()): ahi las propiedades de abajo
    # siguen sumando 'lines' en vivo, como siempre.
    totals_snapshot: tuple[int, int, int] | None = None

    @property
    def subtotal_cents(self) -> int:
        if self.totals_snapshot is not None:
            return self.totals_snapshot[0]
        return sum(line.line_total_cents for line in self.lines)

    @property
    def tax_cents(self) -> int:
        if self.totals_snapshot is not None:
            return self.totals_snapshot[1]
        return round(self.subtotal_cents * self.tax_rate / 100)

    @property
    def total_cents(self) -> int:
        if self.totals_snapshot is not None:
            return self.totals_snapshot[2]
        return self.subtotal_cents + self.tax_cents

    @property
    def subtotal(self) -> Decimal:
        return from_cents(self.subtotal_cents)

    @property
    def tax(self) -> Decimal:
        return from_cents(self.tax_cents)

    @property
    def total(self) -> Decimal:
        return from_cents(self.total_cents)

    @property
    def is_cancelled(self) -> bool:
        return self.status is InvoiceStatus.CANCELLED

    @property
    def bs_rate_is_bcv(self) -> bool:
        """
        Si la tasa con que se paso a bolivares es la BCV, para poder
        llamarla asi en el PDF. Sin bs_rate, el PDF usa la BCV vigente; con
        ref_rate, bs_rate es la BCV por construccion; si no, depende de si
        se confirmo antes o despues de BS_RATE_ALWAYS_BCV_SINCE.
        """
        if self.bs_rate is None or self.ref_rate is not None:
            return True
        return self.issued_at is None or self.issued_at >= BS_RATE_ALWAYS_BCV_SINCE


# Desde el 21/09/2026 a las 02:34 (commit 71cbec3) confirmar una factura
# guarda SIEMPRE la tasa BCV en bs_rate, y la de referencia, si se uso, va
# aparte en ref_rate. Antes, cobrar a la tasa de referencia la guardaba en
# bs_rate (en la base real le paso a una sola: FAC-000007).
BS_RATE_ALWAYS_BCV_SINCE = datetime(2026, 9, 21, 2, 34)  # noqa: DTZ001 - hora local, como issued_at


@dataclass(slots=True)
class Quotation:
    """
    Cotizacion / nota de entrega: documento informal, separado de las
    facturas. No mueve stock ni cuenta en el cierre de caja.

    Los montos base estan en USD (precio 3 del catalogo). Las otras dos
    monedas salen de dos tasas que se congelan al crear el documento
    (regla 5), asi reimprimirlo en cualquier moneda da siempre lo mismo:
        Bs      = USD x personal_rate
        $ BCV   = USD x personal_rate / bcv_rate
    """

    id: int | None
    display_number: str  # 'NE-000001'
    customer_name: str  # SNAPSHOT
    tax_rate: Decimal  # SNAPSHOT; 0 si se desactivo el IVA
    personal_rate: Decimal  # SNAPSHOT, Bs por USD elegido por el negocio
    bcv_rate: Decimal  # SNAPSHOT, Bs por USD oficial
    number: int = 0
    customer_id: int | None = None
    customer_tax_id: str | None = None
    customer_address: str | None = None
    status: InvoiceStatus = InvoiceStatus.ISSUED
    notes: str | None = None
    issued_at: datetime | None = None
    cancelled_at: datetime | None = None
    cancel_reason: str | None = None
    lines: list[InvoiceLine] = field(default_factory=list)
    # Totales ya guardados (total por moneda), para el listado que no
    # carga lineas - mismo criterio que Invoice.totals_snapshot.
    totals_snapshot: dict[QuoteCurrency, int] | None = None

    def factor(self, currency: QuoteCurrency) -> Decimal:
        if currency is QuoteCurrency.BS:
            return self.personal_rate
        if currency is QuoteCurrency.USD_BCV:
            return self.personal_rate / self.bcv_rate
        return Decimal(1)

    def unit_price_in(self, line: InvoiceLine, currency: QuoteCurrency) -> int:
        return convert_cents(line.unit_price_cents, self.factor(currency))

    def line_total_in(self, line: InvoiceLine, currency: QuoteCurrency) -> int:
        return convert_cents(line.line_total_cents, self.factor(currency))

    def totals_in(self, currency: QuoteCurrency) -> tuple[int, int, int]:
        """
        (subtotal, iva, total) en centavos de esa moneda. El subtotal es
        la suma de las lineas YA convertidas, asi el papel cuadra linea
        por linea en cualquier moneda (convertir solo el total final
        podia diferir por un centavo de la suma de las lineas).
        """
        subtotal = sum(self.line_total_in(line, currency) for line in self.lines)
        tax = round(subtotal * self.tax_rate / 100)
        return subtotal, tax, subtotal + tax

    def total_in(self, currency: QuoteCurrency) -> int:
        if self.totals_snapshot is not None:
            return self.totals_snapshot[currency]
        return self.totals_in(currency)[2]

    @property
    def is_cancelled(self) -> bool:
        return self.status is InvoiceStatus.CANCELLED


@dataclass(frozen=True, slots=True)
class StockOverview:
    """
    Cuantos productos activos hay y como esta su existencia, para el
    tablero de Inicio. Son conteos, no la lista: quien quiera VER cuales
    va a Ajuste de stock o a Productos.

    'sin_existencia' incluye a los negativos (current_stock <= 0); se
    cuentan aparte porque un negativo es otra cosa: se vendio mas de lo
    que el sistema sabia que habia (allow_negative_stock lo permite), y
    es la pista de un conteo pendiente.
    """

    active: int
    out_of_stock: int
    negative: int
    # Cuantos ya se contaron alguna vez (tienen un ajuste o carga inicial):
    # el avance del conteo a mano del inventario, que se hace de a poco.
    counted: int = 0


@dataclass(slots=True)
class PaymentMethodTotal:
    """
    Una fila del cierre de caja: cuantas facturas CONFIRMADAS (status
    ISSUED - una CANCELLED no es plata que entro de verdad) y cuanto
    sumaron, para una forma de pago, en el rango de fechas que haya
    pedido BillingService.cierre_diario()/cierre_mensual().

    payment_method None agrupa las facturas sin ese dato (emitidas
    antes de que existiera la columna, o -en teoria- sin elegir una):
    el cierre las tiene que poder mostrar igual, nunca excluirlas en
    silencio (si no, la caja "no cuadra" y nadie sabe por que).

    total_bs_cents es lo mismo en bolivares, pero NO es total_cents por
    la tasa de hoy: cada factura aporta sus bolivares con el bs_rate
    que congelo al emitirse (regla 5). Esa es la unica cifra que sirve
    para contar el cajon, porque es la que de verdad se cobro. Las
    facturas anteriores a bs_rate no tienen con que convertirse y no
    suman nada aca (ver totals_by_payment_method): mejor que inventar
    bolivares con una tasa que no es la que se uso.
    """

    payment_method: PaymentMethod | None
    count: int
    total_cents: int
    total_bs_cents: int = 0

    @property
    def total(self) -> Decimal:
        return from_cents(self.total_cents)

    @property
    def total_bs(self) -> Decimal:
        return from_cents(self.total_bs_cents)


@dataclass(slots=True)
class CreditNoteLine:
    id: int | None
    invoice_line_id: int | None
    product_id: int | None
    product_sku: str
    product_name: str
    quantity_milli: int
    unit_price_cents: int
    restock: bool = True  # False = devuelto danado, no reingresa al inventario

    @property
    def line_total_cents(self) -> int:
        return round(self.unit_price_cents * self.quantity_milli / 1000)


@dataclass(slots=True)
class CreditNote:
    """Devolucion. No edita la factura original: la referencia."""

    id: int | None
    display_number: str  # 'NC-000012'
    invoice_id: int
    reason: str
    tax_rate: Decimal  # el de la factura original
    number: int = 0
    series_id: int | None = None
    issued_at: datetime | None = None
    lines: list[CreditNoteLine] = field(default_factory=list)

    @property
    def subtotal_cents(self) -> int:
        return sum(line.line_total_cents for line in self.lines)

    @property
    def tax_cents(self) -> int:
        return round(self.subtotal_cents * self.tax_rate / 100)

    @property
    def total_cents(self) -> int:
        return self.subtotal_cents + self.tax_cents


# ------------------------------------------------------------------
# Inventario
# ------------------------------------------------------------------
@dataclass(slots=True)
class InventoryMovement:
    """
    Append-only. Nunca se edita ni se borra.
    quantity_milli es SIEMPRE el delta con signo:
      vendi 3 unidades      -> -3000
      conte 47 y decia 50   -> -3000  (tipo ADJUSTMENT)
      devolvieron 2         -> +2000
    """

    id: int | None
    product_id: int
    type: MovementType
    quantity_milli: int
    unit_cost_milli: int | None = None
    reason: str | None = None
    reference_type: ReferenceType | None = None
    reference_id: int | None = None
    counted_by: str | None = None  # quien conto (ajuste manual), texto libre
    note: str | None = None  # bitacora opcional del ajuste
    created_at: datetime | None = None

    @property
    def quantity(self) -> Decimal:
        return from_milli(self.quantity_milli)

    @property
    def is_inbound(self) -> bool:
        return self.quantity_milli > 0
