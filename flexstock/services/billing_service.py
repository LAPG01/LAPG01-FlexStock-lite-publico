"""
Reglas de negocio de facturacion.

El BORRADOR vive solo en memoria: crear_borrador()/agregar_linea() arman
un Invoice en Python, sin tocar la base. La UNICA escritura a la base
es confirmar(), que hace INSERT de factura + lineas + movimientos de
stock como una sola transaccion atomica (si algo falla a mitad de
camino, no queda nada escrito). cancelar() es la otra escritura: marca
la factura como CANCELLED y revierte el stock, tambien en una sola
transaccion.

DIFERENCIA con ProductService/CustomerService: este servicio recibe la
conexion cruda ademas de los repositorios. La necesita para dos cosas
que ningun otro servicio hizo hasta ahora: leer settings (tasa de IVA,
permitir stock negativo) y abrir con 'transaction(conn)' la transaccion
compuesta que comparten InvoiceRepository y StockService. Es la primera
vez que un service orquesta varios repos en una sola transaccion (ver
las notas en invoice_repository.py y product_repository.py al respecto).
"""

import sqlite3
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path

from ..config import EXPORT_DIR
from ..data.connection import get_setting, set_setting, transaction
from ..data.repositories.customer_repository import CustomerRepository
from ..data.repositories.invoice_repository import InvoiceRepository
from ..data.repositories.price_tier_repository import PriceTierRepository
from ..data.repositories.product_repository import ProductRepository
from ..domain.bs_breakdown import breakdown
from ..domain.entities import Invoice, InvoiceLine, PaymentMethodTotal
from ..domain.enums import MovementType, PaymentMethod, ReferenceType
from ..domain.errors import (
    EmptyDocument,
    InsufficientStock,
    InvoiceAlreadyCancelled,
    MissingCustomerData,
    ReasonRequired,
)
from ..domain.money import to_cents, to_milli
from ..reports.invoice_pdf import render_invoice
from .settings_service import SettingsService, store_info_for_pdf
from .stock_service import StockService


class BillingService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        invoice_repo: InvoiceRepository,
        product_repo: ProductRepository,
        customer_repo: CustomerRepository,
        tier_repo: PriceTierRepository,
        stock_service: StockService,
    ) -> None:
        self._conn = conn
        self._invoices = invoice_repo
        self._products = product_repo
        self._customers = customer_repo
        self._tiers = tier_repo
        self._stock = stock_service

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_invoices(self) -> list[Invoice]:
        return self._invoices.list_summary()

    def get_invoice(self, invoice_id: int) -> Invoice | None:
        return self._invoices.get(invoice_id)

    def list_invoices_for_customer(self, customer_id: int) -> list[Invoice]:
        """Historial de compras de un cliente, para su ficha en CustomerDialog."""
        return self._invoices.list_by_customer(customer_id)

    def company_name(self) -> str:
        """
        Para el encabezado de BillingPage y del PDF. Vive aca y no en la
        UI/reports porque 'settings' es una tabla de la base, y ninguna
        de las dos puede tocar data/ directo (regla de arquitectura).
        """
        return get_setting(self._conn, "company_name", "Ferreteria")

    def company_tax_id(self) -> str:
        return get_setting(self._conn, "company_tax_id", "")

    def company_phone(self) -> str:
        return get_setting(self._conn, "company_phone", "")

    def company_address(self) -> str:
        return get_setting(self._conn, "company_address", "")

    def company_email(self) -> str:
        return get_setting(self._conn, "company_email", "")

    def company_logo(self) -> bytes | None:
        """
        El logo de la tienda (PNG), o None si no cargaron ninguno.

        Se delega en SettingsService, que es quien lo guarda desde
        Configuracion > Tienda: asi la forma de guardarlo (base64
        adentro de settings) vive en UN solo lugar, y este modulo no
        tiene que saber nada de base64.
        """
        return SettingsService(self._conn).logo()

    def currency_symbol(self) -> str:
        """
        La UI (MainWindow) lo lee una sola vez al arrancar y lo reparte
        a las paginas que muestran plata, en vez de que cada format_money()
        vuelva a golpear settings. Antes de esto, todo el proyecto tenia
        '$' escrito a mano en cada llamada e ignoraba esta fila.
        """
        return get_setting(self._conn, "currency_symbol", "$")

    def default_tax_rate(self) -> Decimal:
        """
        Tasa de IVA vigente, para mostrarla en la UI de edicion (regla 7:
        se sigue aplicando solo al facturar, nunca sobre precios en $ -
        ver set_default_tax_rate y crear_borrador/confirmar mas abajo).
        """
        return Decimal(get_setting(self._conn, "default_tax_rate", "0"))

    def set_default_tax_rate(self, rate: Decimal) -> None:
        """
        Cambia la tasa que leeran crear_borrador()/confirmar() de ahora
        en adelante. Las facturas ya emitidas no se tocan: su tax_rate
        es un snapshot en la propia fila (regla 5), completamente
        independiente de este valor en settings.
        """
        if rate < 0 or rate > 100:
            raise ValueError("La tasa de IVA debe estar entre 0 y 100.")
        set_setting(self._conn, "default_tax_rate", str(rate))

    # ------------------------------------------------------------
    # BORRADOR: todo en memoria, nada se escribe todavia
    # ------------------------------------------------------------
    def crear_borrador(
        self,
        *,
        customer_id: int | None = None,
        customer_name: str | None = None,
        customer_tax_id: str | None = None,
        customer_address: str | None = None,
    ) -> Invoice:
        """
        Arma una factura vacia en memoria. Acepta un cliente registrado
        (customer_id) o, para una venta de mostrador sin ficha de
        cliente, un nombre suelto (customer_name) - el esquema permite
        customer_id NULL mientras customer_name siempre va (regla del
        propio schema.sql, ninguna de las dos es obligatoria por si sola).

        Una venta sin ficha tambien puede traer su cedula/RIF y direccion
        (customer_tax_id/customer_address): se guardan en la factura como
        snapshot, sin crear un cliente. Con un cliente registrado se
        ignoran: los datos salen de su ficha.

        La tasa de impuesto se lee aca para que la UI pueda mostrar
        totales en vivo mientras se arma la factura, pero NO es la que
        se congela: confirmar() la vuelve a leer al momento de emitir
        (regla 7 del CLAUDE.md: "se congela en la factura" es en ese
        instante, no cuando se abrio el formulario).
        """
        if customer_id is not None:
            customer = self._customers.get(customer_id)
            if customer is None:
                raise ValueError(f"No existe un cliente con id={customer_id}.")
            name = customer.name
            tax_id = customer.tax_id
            address = customer.address
        else:
            name = (customer_name or "").strip()
            if not name:
                raise ValueError(
                    "Se necesita un cliente registrado o un nombre para "
                    "una venta sin cliente."
                )
            tax_id = (customer_tax_id or "").strip() or None
            address = (customer_address or "").strip() or None

        tax_rate = Decimal(get_setting(self._conn, "default_tax_rate", "0"))

        return Invoice(
            id=None,
            display_number="",
            customer_name=name,
            tax_rate=tax_rate,
            customer_id=customer_id,
            customer_tax_id=tax_id,
            customer_address=address,
            lines=[],
            # Arranca en "ahora", editable en el formulario (ver
            # InvoiceDialog) antes de confirmar - confirmar() respeta lo
            # que haya aca en vez de pisarlo (ver InvoiceRepository.create).
            issued_at=datetime.now(),  # noqa: DTZ005
        )

    def cambiar_cliente(self, invoice: Invoice, customer_id: int) -> None:
        """
        Le pone otro cliente registrado a un borrador, sin perder sus
        lineas. Copia de la ficha lo mismo que crear_borrador (nombre,
        cedula/RIF y direccion son SNAPSHOT de la factura), asi el
        borrador queda igual que si se hubiera creado con este cliente.
        Solo borradores: una factura emitida no cambia (Regla 4).
        """
        if invoice.id is not None:
            raise ValueError("Una factura ya emitida no cambia de cliente.")
        customer = self._customers.get(customer_id)
        if customer is None:
            raise ValueError(f"No existe un cliente con id={customer_id}.")
        invoice.customer_id = customer_id
        invoice.customer_name = customer.name
        invoice.customer_tax_id = customer.tax_id
        invoice.customer_address = customer.address

    def agregar_linea(
        self,
        invoice: Invoice,
        *,
        product_id: int,
        quantity: Decimal,
        tier_id: int | None = None,
        price_factor: Decimal | None = None,
    ) -> InvoiceLine:
        """
        Snapshotea sku/nombre/unidad/precio del producto AHORA (no al
        confirmar): si el precio cambia mientras la factura sigue
        abierta en el formulario, la linea ya agregada no debe moverse
        sola - es la misma logica de "lo que se ve en pantalla es lo
        que se cobra" que despues aplica confirmar() sobre la factura
        entera.

        price_factor multiplica el precio del catalogo ANTES de
        guardarlo. Con Modo avanzado la UI manda tasa_referencia / BCV:
        el precio que queda en la linea es el del catalogo cobrado a esa
        tasa pero expresado en $ a la BCV, asi la factura entera es una
        venta normal a tasa BCV (ver InvoiceDialog). None o 1 = el
        precio del catalogo tal cual.

        No escribe en la base: solo agrega a invoice.lines en memoria.
        """
        quantity_milli = to_milli(quantity)
        if quantity_milli <= 0:
            raise ValueError("La cantidad debe ser mayor que cero.")

        product = self._products.get(product_id)
        if product is None:
            raise ValueError(f"No existe un producto con id={product_id}.")
        if not product.is_active:
            raise ValueError(f"'{product.name}' está inactivo, no se puede vender.")

        if tier_id is None:
            tier_id = self._default_tier_id()
        price = product.price_for(tier_id)
        if price is None:
            raise ValueError(
                f"'{product.name}' no tiene precio definido para ese nivel."
            )
        if price_factor is not None:
            if price_factor <= 0:
                raise ValueError("El factor de precio debe ser mayor que cero.")
            price = price * price_factor

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
        invoice.lines.append(line)
        return line

    def quitar_linea(self, invoice: Invoice, index: int) -> None:
        """
        Saca una linea del borrador por posicion (la 'x' de InvoiceDialog):
        simetrico a agregar_linea, mismo criterio de "nada toca la base
        todavia" - el indice es el que ve la UI en su Treeview, que se
        redibuja entero en cada cambio y siempre respeta el orden de
        invoice.lines.
        """
        if index < 0 or index >= len(invoice.lines):
            raise ValueError(f"No existe una línea en la posición {index}.")
        del invoice.lines[index]

    def cambiar_cantidad(
        self, invoice: Invoice, index: int, quantity: Decimal
    ) -> InvoiceLine:
        """
        Cambia la cantidad de una linea del borrador (el "+" y el "-" de la
        tabla de InvoiceDialog) sin tocar su precio: el precio unitario se
        congelo al agregarla (regla 5) y sigue siendo ese. Igual que
        quitar_linea, el indice es el que ve la UI y nada toca la base.
        """
        if index < 0 or index >= len(invoice.lines):
            raise ValueError(f"No existe una línea en la posición {index}.")
        quantity_milli = to_milli(quantity)
        if quantity_milli <= 0:
            raise ValueError("La cantidad debe ser mayor que cero.")
        linea = invoice.lines[index]
        linea.quantity_milli = quantity_milli
        return linea

    def lines_below_stock(self, invoice: Invoice) -> list[tuple[InvoiceLine, int]]:
        """
        Sin efectos: para que la UI pregunte "esto va a dejar stock
        negativo, ¿confirmo igual?" ANTES de llamar a confirmar(),
        cuando settings.allow_negative_stock lo permite. Devuelve
        (linea, stock_disponible_milli) solo para las lineas que
        dejarian el producto en negativo.
        """
        return self._stock_shortfalls(invoice)

    # ------------------------------------------------------------
    # Escritura: la unica transaccion que crea la factura de verdad
    # ------------------------------------------------------------
    @staticmethod
    def missing_fiscal_data(invoice: Invoice) -> list[str]:
        """
        Que le falta al comprador para una factura: su cedula/RIF (el
        nombre siempre va, ver crear_borrador). Lista vacia = completo.
        La usa la pantalla para avisar ANTES de armar las lineas, y
        confirmar() como ultima palabra.

        El telefono y la direccion NO son obligatorios: se muestran en la
        pantalla y salen en el PDF si el cliente los tiene.
        """
        return [] if (invoice.customer_tax_id or "").strip() else ["cédula/RIF"]

    def confirmar(
        self,
        invoice: Invoice,
        *,
        tax_rate_override: Decimal | None = None,
        require_fiscal_customer: bool = False,
        bs_rate: Decimal | None = None,
        ref_rate: Decimal | None = None,
    ) -> Invoice:
        """
        Transaccion atomica: INSERT factura + lineas + movimientos de
        stock. Si algo falla en el medio (incluido un stock insuficiente
        cuando no se permite negativo), no se escribe nada.

        Vuelve a leer la tasa de IVA y (si hay customer_id) el nombre/
        cedula del cliente justo antes de insertar: son los snapshots
        que la regla 5 dice que se copian "al emitir", no cuando se
        abrio el formulario.

        tax_rate_override: None (el caso de siempre) mantiene ese
        comportamiento tal cual. Con un valor puntual (ej. Decimal(0)),
        lo usa en vez de volver a leer settings - la UI lo manda solo
        cuando Modo avanzado esta activo y se eligio explicitamente no
        cobrar IVA en ESTE documento (ver InvoiceDialog), nunca a
        ciegas.

        require_fiscal_customer: con True exige cedula/RIF y direccion
        del comprador (MissingCustomerData, sin escribir nada). Las
        facturas de la app lo piden siempre (ver InvoiceDialog); queda
        en False por defecto igual que 'payment_method': a nivel de
        servicio no se rompe el flujo de quien arma facturas por codigo.

        bs_rate: los Bs por $ con que se paso esta factura a bolívares (la
        BCV o la de referencia que se eligio en pantalla). Se guarda en la
        factura como snapshot (regla 5): el PDF se imprime en Bs con ESTA
        tasa, y reimprimirla un mes despues da el mismo monto. None = sin
        tasa (queda NULL y el PDF usara la BCV vigente al generarlo).

        ref_rate: la tasa de referencia con que se alteraron los precios
        (Modo avanzado, ver agregar_linea(price_factor)). Se guarda solo
        para poder volver al precio del catalogo - ningun monto de la
        factura sale de ella. None = se cobro el catalogo tal cual.
        """
        if not invoice.lines:
            raise EmptyDocument("Una factura sin líneas no se puede confirmar.")

        shortfalls = self._stock_shortfalls(invoice)
        if shortfalls and get_setting(self._conn, "allow_negative_stock", "0") != "1":
            line, available = shortfalls[0]
            raise InsufficientStock(
                line.product_name, available=available, requested=line.quantity_milli
            )

        invoice.tax_rate = (
            tax_rate_override
            if tax_rate_override is not None
            else Decimal(get_setting(self._conn, "default_tax_rate", "0"))
        )
        if invoice.customer_id is not None:
            customer = self._customers.get(invoice.customer_id)
            if customer is None:
                raise ValueError(f"El cliente id={invoice.customer_id} ya no existe.")
            invoice.customer_name = customer.name
            invoice.customer_tax_id = customer.tax_id
            invoice.customer_address = customer.address

        if require_fiscal_customer:
            faltan = self.missing_fiscal_data(invoice)
            if faltan:
                raise MissingCustomerData(faltan)

        if bs_rate is not None and bs_rate <= 0:
            raise ValueError("La tasa en Bs de la factura debe ser mayor que cero.")
        if ref_rate is not None and ref_rate <= 0:
            raise ValueError("La tasa de referencia debe ser mayor que cero.")
        invoice.bs_rate = bs_rate
        invoice.ref_rate = ref_rate

        with transaction(self._conn):
            self._invoices.create(invoice)
            for line in invoice.lines:
                self._stock.apply_movement(
                    product_id=line.product_id,
                    type=MovementType.OUT,
                    quantity_milli=-line.quantity_milli,
                    reference_type=ReferenceType.INVOICE,
                    reference_id=invoice.id,
                )

        return invoice

    def cancelar(self, invoice_id: int, reason: str) -> Invoice:
        """
        Marca la factura CANCELLED y revierte el stock de cada linea
        con un movimiento IN (nunca se toca ni se borra el OUT
        original: regla 3, append-only). Todo en una sola transaccion.
        """
        reason = (reason or "").strip()
        if not reason:
            raise ReasonRequired("Cancelar una factura necesita un motivo.")

        invoice = self._invoices.get(invoice_id)
        if invoice is None:
            raise ValueError(f"No existe una factura con id={invoice_id}.")
        if invoice.is_cancelled:
            raise InvoiceAlreadyCancelled(
                f"La factura {invoice.display_number} ya estaba cancelada."
            )

        with transaction(self._conn):
            self._invoices.mark_cancelled(invoice_id, reason)
            for line in invoice.lines:
                self._stock.apply_movement(
                    product_id=line.product_id,
                    type=MovementType.IN,
                    quantity_milli=line.quantity_milli,
                    reference_type=ReferenceType.INVOICE,
                    reference_id=invoice_id,
                    reason=f"Reversión por cancelación de {invoice.display_number}",
                )

        return self._invoices.get(invoice_id)

    # ------------------------------------------------------------
    # PDF (Fase 7)
    # ------------------------------------------------------------
    def generate_pdf(self, invoice_id: int, *, export_dir: Path | None = None) -> Path:
        """
        Busca la factura y delega el dibujo a reports/invoice_pdf.py:
        este metodo no sabe nada de ReportLab (regla de arquitectura,
        'reports/' es el unico lugar que lo importa). export_dir existe
        para que los tests puedan apuntar a un directorio temporal; en
        uso real siempre es config.EXPORT_DIR.

        El PDF sale en BOLIVARES, con la tasa que la factura guardo al
        confirmarse (invoices.bs_rate): reimprimir una factura vieja
        siempre da el mismo monto. Una factura anterior a ese campo no la
        tiene y usa la tasa BCV vigente; si tampoco hay una BCV cargada,
        avisa en vez de imprimir en $ sin que nadie lo note.
        """
        invoice = self._invoices.get(invoice_id)
        if invoice is None:
            raise ValueError(f"No existe una factura con id={invoice_id}.")

        tasa = invoice.bs_rate or self._bcv_rate()
        if tasa is None:
            raise ValueError(
                f"La factura {invoice.display_number} no tiene tasa guardada y "
                "no hay una tasa BCV cargada: cárgala desde el encabezado para "
                "generar el PDF en bolívares."
            )

        return render_invoice(
            invoice,
            company_name=self.company_name(),
            company_tax_id=self.company_tax_id(),
            company_phone=self.company_phone(),
            company_logo=self.company_logo(),
            company_payment_qr=SettingsService(self._conn).payment_qr(),
            **store_info_for_pdf(self._conn),
            currency_symbol=self.currency_symbol(),
            bs=breakdown(invoice, tasa),
            export_dir=export_dir or EXPORT_DIR,
        )

    def _bcv_rate(self) -> Decimal | None:
        """La tasa BCV cacheada en settings (ver ExchangeRateService), o
        None si no hay una valida. Solo la usan las facturas sin tasa."""
        try:
            tasa = Decimal(get_setting(self._conn, "bcv_rate", ""))
        except InvalidOperation:
            return None
        return tasa if tasa > 0 else None

    # ------------------------------------------------------------
    # Cierre de caja
    # ------------------------------------------------------------
    def cierre_diario(self, day: date) -> list[PaymentMethodTotal]:
        """
        Totales por forma de pago para un dia calendario completo (de
        00:00:00 a las 00:00:00 del dia siguiente, ambos en hora local
        - igual criterio que issued_at, que se guarda con
        datetime('now','localtime')). Una sola consulta agregada
        (ver InvoiceRepository.totals_by_payment_method): nunca un
        bucle sumando list_summary() en Python.
        """
        inicio, fin = _rango_dia(day)
        return self._invoices.totals_by_payment_method(start=inicio, end=fin)

    def cierre_mensual(self, year: int, month: int) -> list[PaymentMethodTotal]:
        """
        Igual que cierre_diario(), pero para el mes calendario completo
        (del dia 1 al ultimo dia del mes, inclusive). No usa
        calendar.monthrange(): arrancar el mes siguiente en el dia 1 y
        restar nada evita tener que calcular cuantos dias tiene 'month'.
        """
        inicio, fin = _rango_mes(year, month)
        return self._invoices.totals_by_payment_method(start=inicio, end=fin)

    def facturas_del_cierre_diario(
        self, day: date, payment_method: PaymentMethod | None
    ) -> list[Invoice]:
        """
        Las facturas detras de una fila del cierre diario, para poder
        abrir cualquiera desde ahi. Mismo rango y mismo filtro que
        cierre_diario(): lo que se ve sumado y lo que se lista es el
        mismo conjunto. payment_method=None es el grupo "Sin
        especificar", no "todas" (ver list_by_period).
        """
        inicio, fin = _rango_dia(day)
        return self._invoices.list_by_period(
            start=inicio, end=fin, payment_method=payment_method
        )

    def facturas_del_cierre_mensual(
        self, year: int, month: int, payment_method: PaymentMethod | None
    ) -> list[Invoice]:
        """Igual que facturas_del_cierre_diario(), para el mes completo."""
        inicio, fin = _rango_mes(year, month)
        return self._invoices.list_by_period(
            start=inicio, end=fin, payment_method=payment_method
        )

    # ------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------
    def _default_tier_id(self) -> int:
        for tier in self._tiers.list_all():
            if tier.is_default:
                return tier.id
        raise ValueError("No hay un nivel de precio marcado como default.")

    def _stock_shortfalls(self, invoice: Invoice) -> list[tuple[InvoiceLine, int]]:
        """
        Que lineas dejarian el stock en negativo, con UNA consulta.

        Antes esto llamaba a self._products.get() adentro del for, y
        get() son tres consultas (producto + precios + proveedores) para
        leer un solo entero: una factura de 20 lineas gastaba 60
        consultas, y corre dos veces por factura (lines_below_stock()
        avisa antes de confirmar, confirmar() valida de nuevo). Es el
        N+1 que prohibe la Politica de carga del CLAUDE.md. stock_of()
        trae {id: current_stock} de todas las lineas de una.
        """
        stocks = self._products.stock_of([line.product_id for line in invoice.lines])

        shortfalls = []
        for line in invoice.lines:
            stock = stocks.get(line.product_id)
            if stock is None:
                raise ValueError(
                    f"El producto de una linea ya no existe (id={line.product_id})."
                )
            if stock - line.quantity_milli < 0:
                shortfalls.append((line, stock))
        return shortfalls


# ------------------------------------------------------------
# Rango de fechas para el cierre de caja (modulo, no metodo: no
# necesitan 'self' ni tocan la base - son aritmetica de fechas pura).
# Devuelven TEXT ISO 'YYYY-MM-DD HH:MM:SS', el mismo formato que
# issued_at, para comparar con >= / < directo en SQL sin parsear nada
# del lado de SQLite.
# ------------------------------------------------------------
def _rango_dia(day: date) -> tuple[str, str]:
    inicio = date(day.year, day.month, day.day)
    fin = inicio + timedelta(days=1)
    return f"{inicio.isoformat()} 00:00:00", f"{fin.isoformat()} 00:00:00"


def _rango_mes(year: int, month: int) -> tuple[str, str]:
    inicio = date(year, month, 1)
    fin = date(year + 1, 1, 1) if month == 12 else date(year, month + 1, 1)
    return f"{inicio.isoformat()} 00:00:00", f"{fin.isoformat()} 00:00:00"
