"""
Que columnas puede mostrar cada listado, y cuales estan visibles.

El CATALOGO de abajo es el orden de acoplamiento: marcar y desmarcar
columnas en Configuracion > Personalizacion nunca cambia el orden en que
se ven, solo cuales aparecen. Asi, volver a prender una columna la
devuelve a su lugar de siempre en vez de mandarla al final.

Vive en ui/ y no en domain/ porque es presentacion pura: que titulo
lleva una columna y cuanto mide. Lo unico que se persiste (via
SettingsService, clave 'columns_<modulo>') son las CLAVES elegidas.

Las columnas de precio en Bs usan las tasas de RateDisplayService: si
la tasa que necesitan falta, la celda muestra '-' y nada mas - ver las
paginas que las arman. Las que dependen de la tasa propia llevan
advanced=True: solo existen con Modo avanzado activo (catalog() las
saca si no), igual que la pestana Cotizaciones y la tarjeta de la tasa
propia en el encabezado.
"""

from dataclasses import dataclass, replace

PRODUCTS = "products"
INVOICES = "invoices"
QUOTATIONS = "quotations"
CUSTOMERS = "customers"
SUPPLIERS = "suppliers"

MODULOS = (PRODUCTS, INVOICES, QUOTATIONS, CUSTOMERS, SUPPLIERS)
MODULO_LABELS = {
    PRODUCTS: "Productos",
    INVOICES: "Facturas",
    QUOTATIONS: "Cotizaciones",
    CUSTOMERS: "Clientes",
    SUPPLIERS: "Proveedores",
}


@dataclass(frozen=True, slots=True)
class ColumnDef:
    key: str
    heading: str
    width: int = 100
    anchor: str = "w"
    default: bool = True
    advanced: bool = False  # solo con Modo avanzado (usa la tasa propia)


_PRODUCTS: tuple[ColumnDef, ...] = (
    ColumnDef("sku", "SKU", 110),
    ColumnDef("legacy_code", "Cod. DOS", 110),
    ColumnDef("name", "Nombre", 320),
    ColumnDef("unit", "Unidad", 90),
    ColumnDef("cost", "Costo", 120, "e", default=False),
    ColumnDef("price1", "Precio 1", 150, "e", default=False),
    ColumnDef("price2", "Precio 2", 160, "e", default=False),
    ColumnDef("price3", "Detal $", 190, "e"),
    ColumnDef("price3_bs_bcv", "Detal Bs (BCV)", 190, "e", default=False),
    ColumnDef(
        "price3_bs", "Detal Bs (tasa propia)", 190, "e", default=False, advanced=True
    ),
    ColumnDef(
        "price3_bcv",
        "Detal $ equivalente BCV",
        230,
        "e",
        default=False,
        advanced=True,
    ),
    ColumnDef("stock", "Existencia", 130, "e"),
    ColumnDef("min_stock", "Existencia minima", 150, "e", default=False),
    ColumnDef("activo", "Activo", 70, "center"),
)

_INVOICES: tuple[ColumnDef, ...] = (
    ColumnDef("numero", "Número", 140),
    ColumnDef("fecha", "Fecha", 110),
    ColumnDef("cliente", "Cliente", 220),
    # Datos del comprador, tal como quedaron en la factura (snapshot):
    # la cedula/RIF es lo principal y se ve por defecto; la direccion
    # esta disponible pero apagada. Se prenden y apagan en Configuracion >
    # Personalizacion como cualquier otra columna.
    ColumnDef("cedula", "Cédula/RIF", 140),
    ColumnDef("direccion", "Dirección", 240, default=False),
    ColumnDef("estado", "Estado", 120, "center"),
    # Los tres montos de una factura, todos con las tasas que ELLA
    # guardo (nunca con las de hoy: recalcular daba montos que nunca se
    # cobraron), y en el orden en que se leen:
    #   "Total $ ref"  el precio de lista, en dolares de verdad (la clave
    #                  sigue siendo total_catalogo: renombrarla le
    #                  borraria la columna a quien ya la tenia prendida)
    #   "Total $ BCV"  lo cobrado, que siempre esta a tasa BCV
    #   "Total Bs"     eso mismo en bolívares
    # Los dos primeros solo se diferencian si se facturo a la tasa de
    # referencia (Modo avanzado); sin ese modo son el mismo numero.
    ColumnDef("total_catalogo", "Total $ ref", 150, "e", default=False, advanced=True),
    ColumnDef("total", "Total $ BCV", 130, "e"),
    ColumnDef("total_bs", "Total Bs", 170, "e", default=False),
)

_QUOTATIONS: tuple[ColumnDef, ...] = (
    ColumnDef("numero", "Número", 140),
    ColumnDef("fecha", "Fecha", 110),
    ColumnDef("cliente", "Cliente", 220),
    ColumnDef("estado", "Estado", 120, "center"),
    ColumnDef("total_usd", "Total $", 120, "e"),
    ColumnDef("total_bs", "Total Bs", 150, "e"),
    ColumnDef("total_bcv", "Total $ BCV", 140, "e"),
)

_CUSTOMERS: tuple[ColumnDef, ...] = (
    ColumnDef("sku", "SKU", 110),
    ColumnDef("name", "Nombre", 220),
    ColumnDef("tax_id", "Cédula/RIF", 140),
    ColumnDef("phone", "Teléfono", 130),
    ColumnDef("email", "Email", 180, default=False),
    ColumnDef("address", "Dirección", 220, default=False),
    ColumnDef("notes", "Notas", 240, default=False),
    ColumnDef("mayorista", "Mayorista", 100, "center"),
    ColumnDef("activo", "Activo", 70, "center"),
)

_SUPPLIERS: tuple[ColumnDef, ...] = (
    ColumnDef("sku", "SKU", 110),
    ColumnDef("name", "Nombre", 200),
    ColumnDef("contact_name", "Contacto", 150),
    ColumnDef("phone", "Teléfono", 130),
    ColumnDef("email", "Email", 180),
    ColumnDef("tax_id", "RIF", 140, default=False),
    ColumnDef("activo", "Activo", 70, "center"),
)

_CATALOGOS = {
    PRODUCTS: _PRODUCTS,
    INVOICES: _INVOICES,
    QUOTATIONS: _QUOTATIONS,
    CUSTOMERS: _CUSTOMERS,
    SUPPLIERS: _SUPPLIERS,
}


def catalog(
    modulo: str,
    *,
    tier_names: dict[int, str] | None = None,
    advanced: bool = True,
) -> tuple[ColumnDef, ...]:
    """
    El catalogo del modulo. advanced=False saca las columnas de Modo
    avanzado (las que usan la tasa propia). Para Productos, tier_names
    ({1: 'Mayor', ...}, por POSITION - ver PriceTierRepository) reemplaza
    'Precio 1/2/3' por el nombre real de cada nivel ('Mayor $',
    'Detal $', ...).
    """
    columnas = tuple(c for c in _CATALOGOS[modulo] if advanced or not c.advanced)
    if modulo != PRODUCTS or not tier_names:
        return columnas

    titulos = {f"price{pos}": f"{nombre} $" for pos, nombre in tier_names.items()}
    tier3 = tier_names.get(3)
    if tier3:
        titulos["price3"] = f"{tier3} $"
        titulos["price3_bs_bcv"] = f"{tier3} Bs (BCV)"
        titulos["price3_bs"] = f"{tier3} Bs (tasa propia)"
        titulos["price3_bcv"] = f"{tier3} $ equivalente BCV"
    return tuple(replace(c, heading=titulos.get(c.key, c.heading)) for c in columnas)


def default_keys(modulo: str) -> tuple[str, ...]:
    return tuple(c.key for c in _CATALOGOS[modulo] if c.default)


def visible(
    columnas: tuple[ColumnDef, ...], keys: tuple[str, ...] | None
) -> tuple[ColumnDef, ...]:
    """
    Las columnas elegidas, SIEMPRE en el orden del catalogo. keys=None
    (nunca se personalizo) y una seleccion vacia caen al default: una
    tabla sin ninguna columna no se puede leer ni arreglar desde la UI.
    """
    if keys is None:
        return tuple(c for c in columnas if c.default)
    elegidas = set(keys)
    visibles = tuple(c for c in columnas if c.key in elegidas)
    return visibles or tuple(c for c in columnas if c.default)
