"""
Exportar el catalogo, los proveedores, los clientes y las facturas a
CSV o PDF.

Esto NO es un respaldo (ver BackupService/data.connection.backup, que
siguen siendo la unica fuente de verdad restaurable via la API backup()
de SQLite): un CSV/PDF es de solo consulta, para abrir en una planilla
o archivar, y no se puede "restaurar" de vuelta a la base. Por eso cada
export_*() recibe un directorio destino explicito en vez de una carpeta
fija propia - quien llama (BackupDialog) ya le pregunto a la persona
donde quiere guardarlo, igual que con "Respaldar en...".

Cada export_*() pide los datos a un service ya existente
(ProductService.list_products(), SupplierService.list_suppliers(),
CustomerService.list_customers(), BillingService.list_invoices()),
nunca a un repositorio directo: esos services ya resuelven la carga en
lote (precios de producto, lineas de factura) - una consulta, no un
bucle. Ver la Politica de carga en repositorios del CLAUDE.md.

Productos/Proveedores/Clientes ademas aceptan 'columns': que columnas
incluir y en que orden mostrarlas se decide UNA vez por export
(BackupDialog pregunta, no se guarda en settings - a diferencia de las
columnas de las tablas en pantalla, que si se guardan en
Configuracion > Personalizacion, ver ui/columns.py). None (el default)
exporta la ficha completa, mismo comportamiento que antes de que esto
fuera elegible.
"""

import csv
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..domain.enums import InvoiceStatus
from ..domain.money import from_cents, from_milli, to_cents
from ..reports.listing_pdf import render_listing
from .billing_service import BillingService
from .customer_service import CustomerService
from .product_service import ProductService
from .rate_display_service import BCV, RateDisplayService
from .supplier_service import SupplierService

# Mismo texto que BillingPage._ESTADO_DISPLAY (ui/ no se puede importar
# desde services/, asi que es una copia chica a proposito en vez de una
# dependencia cruzada solo por 2 strings).
_ESTADO_CSV = {
    InvoiceStatus.ISSUED: "Confirmada",
    InvoiceStatus.CANCELLED: "Cancelada",
}


@dataclass(frozen=True, slots=True)
class ExportColumn:
    """
    Una columna exportable de Productos/Proveedores/Clientes.

    'key' usa las MISMAS claves que ui/columns.py para las tablas en
    pantalla (sku, name, price1...) - asi "las columnas visibles ahora"
    (Configuracion > Personalizacion) se puede pasar tal cual como
    filtro de 'columns', sin traducir nada entre los dos sistemas.
    'header' es el titulo de la columna en el CSV/PDF (snake_case,
    igual estilo que el resto del proyecto). 'label' es el texto del
    checkbox en el dialogo "Elegir columnas..." (ui/backup_dialog.py).
    """

    key: str
    header: str
    label: str
    value: Callable[[object], object]


class ExportService:
    def __init__(
        self,
        product_service: ProductService,
        supplier_service: SupplierService,
        billing_service: BillingService,
        customer_service: CustomerService,
        *,
        rate_display_service: RateDisplayService | None = None,
    ) -> None:
        self._products = product_service
        self._suppliers = supplier_service
        self._billing = billing_service
        self._customers = customer_service
        # Para "Detal Bs (BCV)". Sin el, esa columna sale vacia.
        self._rates = rate_display_service

    # ------------------------------------------------------------
    # que columnas se pueden exportar de cada entidad
    # ------------------------------------------------------------
    def product_export_columns(self) -> list[ExportColumn]:
        """
        Los 3 niveles de precio salen de price_tiers en vivo (nunca mas
        de 3 en este catalogo, ver schema.sql): 'price{position}' como
        key -para que calce 1 a 1 con ui/columns.py- y el nombre REAL
        del nivel ('Mayor $', 'Detal $') como label, igual criterio
        que ProductsPage/Configuracion > Personalizacion.

        'price3_bs_bcv' es Precio 3 x tasa BCV: la tasa se lee UNA vez
        aca (RateSnapshot), no una vez por producto exportado.
        """
        tasas = self._rates.snapshot() if self._rates is not None else None

        def _precio3_bs_bcv(producto, tier_id: int) -> str:
            milli = producto.price_milli_for(tier_id)
            if milli is None or tasas is None:
                return ""
            cents = tasas.to_bs(to_cents(from_milli(milli)), BCV)
            return "" if cents is None else str(from_cents(cents))

        columnas = [
            ExportColumn("sku", "sku", "SKU", lambda p: p.sku),
            ExportColumn(
                "legacy_code",
                "codigo_viejo",
                "Cod. DOS",
                lambda p: p.legacy_code or "",
            ),
            ExportColumn("name", "nombre", "Nombre", lambda p: p.name),
            ExportColumn("unit", "unidad", "Unidad", lambda p: p.unit),
            ExportColumn("cost", "costo", "Costo", lambda p: from_milli(p.cost_milli)),
        ]
        for tier in self._products.list_price_tiers():
            if tier.position not in (1, 2, 3):
                continue  # catalogo real: siempre 3 niveles, defensivo nomas
            es_precio3 = tier.position == 3
            columnas.append(
                ExportColumn(
                    f"price{tier.position}",
                    f"precio_{tier.name.lower()}",
                    f"{tier.name} $",
                    lambda p, t=tier: _precio_milli_str(p.price_milli_for(t.id)),
                )
            )
            if es_precio3:
                columnas.append(
                    ExportColumn(
                        "price3_bs_bcv",
                        f"precio_{tier.name.lower()}_bs_bcv",
                        f"{tier.name} Bs (BCV)",
                        lambda p, t=tier: _precio3_bs_bcv(p, t.id),
                    )
                )
        columnas += [
            ExportColumn(
                "stock", "stock", "Existencia", lambda p: from_milli(p.current_stock)
            ),
            ExportColumn(
                "min_stock",
                "stock_minimo",
                "Existencia minima",
                lambda p: from_milli(p.min_stock),
            ),
            ExportColumn(
                "activo", "activo", "Activo", lambda p: "Si" if p.is_active else "No"
            ),
        ]
        return columnas

    def supplier_export_columns(self) -> list[ExportColumn]:
        return [
            ExportColumn("sku", "sku", "SKU", lambda s: s.sku),
            ExportColumn("name", "nombre", "Nombre", lambda s: s.name),
            ExportColumn("tax_id", "rif_cedula", "RIF", lambda s: s.tax_id or ""),
            ExportColumn("phone", "telefono", "Teléfono", lambda s: s.phone or ""),
            ExportColumn("email", "email", "Email", lambda s: s.email or ""),
            ExportColumn(
                "address", "direccion", "Dirección", lambda s: s.address or ""
            ),
            ExportColumn(
                "contact_name",
                "contacto",
                "Contacto",
                lambda s: s.contact_name or "",
            ),
            ExportColumn(
                "activo", "activo", "Activo", lambda s: "Si" if s.is_active else "No"
            ),
        ]

    def supplier_entry_export_columns(self) -> list[ExportColumn]:
        """
        Una fila por entrega (product_supplier_entries): que proveedor
        trajo que producto, cuando, a cuanto y con que contacto -el
        historial completo del catalogo, no la ficha de un producto
        puntual (ver ProductService.list_supplier_entries_for_export).
        Sin 'columns' elegible como Productos/Proveedores/Clientes: no
        hay una tabla en pantalla con estas columnas para sincronizar
        (ver BackupPanel._visibles_hoy), asi que siempre se ofrecen
        todas en "Elegir columnas...".
        """
        return [
            ExportColumn(
                "product_sku", "sku_producto", "SKU producto", lambda e: e.product_sku
            ),
            ExportColumn(
                "product_name", "producto", "Producto", lambda e: e.product_name
            ),
            ExportColumn(
                "supplier_name", "proveedor", "Proveedor", lambda e: e.supplier_name
            ),
            ExportColumn(
                "contact_name",
                "contacto",
                "Contacto",
                lambda e: e.contact_name or "",
            ),
            ExportColumn(
                "entry_date",
                "fecha",
                "Fecha",
                lambda e: e.entry_date.strftime("%Y-%m-%d") if e.entry_date else "",
            ),
            ExportColumn(
                "cost",
                "costo",
                "Costo",
                lambda e: from_milli(e.cost_milli) if e.cost_milli is not None else "",
            ),
            ExportColumn("note", "nota", "Nota", lambda e: e.note or ""),
        ]

    def customer_export_columns(self) -> list[ExportColumn]:
        return [
            ExportColumn("sku", "sku", "SKU", lambda c: c.sku),
            ExportColumn("name", "nombre", "Nombre", lambda c: c.name),
            ExportColumn(
                "tax_id", "rif_cedula", "Cédula/RIF", lambda c: c.tax_id or ""
            ),
            ExportColumn("phone", "telefono", "Teléfono", lambda c: c.phone or ""),
            ExportColumn("email", "email", "Email", lambda c: c.email or ""),
            ExportColumn(
                "address", "direccion", "Dirección", lambda c: c.address or ""
            ),
            ExportColumn(
                "mayorista",
                "mayorista",
                "Mayorista",
                lambda c: "Si" if c.is_wholesale else "No",
            ),
            ExportColumn(
                "activo", "activo", "Activo", lambda c: "Si" if c.is_active else "No"
            ),
        ]

    # ------------------------------------------------------------
    # CSV
    # ------------------------------------------------------------
    def export_products_csv(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.product_export_columns(), columns)
        productos = self._products.list_products()
        return self._escribir_csv(dest_dir, "productos", elegidas, productos)

    def export_suppliers_csv(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.supplier_export_columns(), columns)
        proveedores = self._suppliers.list_suppliers()
        return self._escribir_csv(dest_dir, "proveedores", elegidas, proveedores)

    def export_customers_csv(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.customer_export_columns(), columns)
        clientes = self._customers.list_customers()
        return self._escribir_csv(dest_dir, "clientes", elegidas, clientes)

    def export_supplier_entries_csv(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.supplier_entry_export_columns(), columns)
        entregas = self._products.list_supplier_entries_for_export()
        return self._escribir_csv(dest_dir, "entregas", elegidas, entregas)

    def export_invoices_csv(self, dest_dir: Path) -> Path:
        """
        Una fila por factura (cabecera, sin explotar lineas): mismo
        grano que la tabla de Facturas. list_invoices() ya trae los
        totales sin N+1 (ver InvoiceRepository.list_summary() /
        Invoice.totals_snapshot). Sin 'columns': a diferencia de
        Productos/Proveedores/Clientes, este listado no se pidio
        configurable.
        """
        columnas = [
            "numero",
            "fecha",
            "cliente",
            "rif_cedula",
            "estado",
            "subtotal",
            "iva_pct",
            "iva",
            "total",
        ]

        def fila(f):
            return [
                f.display_number,
                f.issued_at.strftime("%Y-%m-%d %H:%M") if f.issued_at else "",
                f.customer_name,
                f.customer_tax_id or "",
                _ESTADO_CSV.get(f.status, str(f.status)),
                from_cents(f.subtotal_cents),
                f.tax_rate,
                from_cents(f.tax_cents),
                from_cents(f.total_cents),
            ]

        facturas = self._billing.list_invoices()
        return self._escribir_csv_crudo(dest_dir, "facturas", columnas, facturas, fila)

    # ------------------------------------------------------------
    # PDF
    # ------------------------------------------------------------
    def export_products_pdf(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.product_export_columns(), columns)
        productos = self._products.list_products()
        return self._escribir_pdf(
            dest_dir, "productos", "Catálogo de productos", elegidas, productos
        )

    def export_suppliers_pdf(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.supplier_export_columns(), columns)
        proveedores = self._suppliers.list_suppliers()
        return self._escribir_pdf(
            dest_dir, "proveedores", "Listado de proveedores", elegidas, proveedores
        )

    def export_customers_pdf(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.customer_export_columns(), columns)
        clientes = self._customers.list_customers()
        return self._escribir_pdf(
            dest_dir, "clientes", "Listado de clientes", elegidas, clientes
        )

    def export_supplier_entries_pdf(
        self, dest_dir: Path, *, columns: tuple[str, ...] | None = None
    ) -> Path:
        elegidas = _filtrar_columnas(self.supplier_entry_export_columns(), columns)
        entregas = self._products.list_supplier_entries_for_export()
        return self._escribir_pdf(
            dest_dir, "entregas", "Historial de entregas", elegidas, entregas
        )

    # ------------------------------------------------------------
    def _escribir_csv(
        self,
        dest_dir: Path,
        nombre: str,
        columnas: list[ExportColumn],
        filas: list,
    ) -> Path:
        return self._escribir_csv_crudo(
            dest_dir,
            nombre,
            [c.header for c in columnas],
            filas,
            lambda item: [c.value(item) for c in columnas],
        )

    def _escribir_csv_crudo(
        self, dest_dir: Path, nombre: str, columnas, filas, a_fila
    ) -> Path:
        dest_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")  # noqa: DTZ005
        destino = dest_dir / f"{nombre}_{stamp}.csv"
        # utf-8-sig: agrega el BOM que Excel necesita para mostrar bien
        # las tildes/enies de "nombre"/"direccion" - sin el BOM, Excel
        # (no LibreOffice) las muestra mal aunque el archivo sea UTF-8
        # valido.
        with destino.open("w", newline="", encoding="utf-8-sig") as archivo:
            writer = csv.writer(archivo)
            writer.writerow(columnas)
            for item in filas:
                writer.writerow(a_fila(item))
        return destino

    def _escribir_pdf(
        self,
        dest_dir: Path,
        nombre: str,
        titulo: str,
        columnas: list[ExportColumn],
        filas: list,
    ) -> Path:
        encabezados = [c.header for c in columnas]
        filas_texto = [[str(c.value(item)) for c in columnas] for item in filas]
        return render_listing(
            titulo,
            encabezados,
            filas_texto,
            export_dir=dest_dir,
            filename_prefix=nombre,
        )


def _filtrar_columnas(
    definidas: list[ExportColumn], keys: tuple[str, ...] | None
) -> list[ExportColumn]:
    """
    Las columnas elegidas, SIEMPRE en el orden de 'definidas' (mismo
    criterio que ui/columns.py::visible). None o vacio -> todas (ficha
    completa, el comportamiento de siempre). Una seleccion que no matchea
    ninguna clave conocida tambien cae a todas, para no devolver un
    archivo sin columnas.
    """
    if not keys:
        return definidas
    filtradas = [c for c in definidas if c.key in keys]
    return filtradas or definidas


def _precio_milli_str(precio_milli: int | None) -> str:
    """precio_milli_for() da None si el producto no tiene precio en ese
    tier: columna vacia en vez de forzar un '0,00' enganoso."""
    return "" if precio_milli is None else str(from_milli(precio_milli))
