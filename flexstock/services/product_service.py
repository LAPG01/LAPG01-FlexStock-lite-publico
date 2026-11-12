"""
Reglas de negocio de productos.

Esta capa traduce entre Decimal (como piensa un humano: '1,50') y los
enteros escalados que guardan los repositorios, y decide COMO se arma
un SKU nuevo. No abre conexiones ni escribe SQL: eso es trabajo de
data/.
"""

import sqlite3
from datetime import date
from decimal import Decimal

from ..data.repositories.category_repository import CategoryRepository
from ..data.repositories.price_tier_repository import PriceTierRepository
from ..data.repositories.product_repository import ProductRepository
from ..domain.entities import (
    Category,
    PriceTier,
    Product,
    ProductLink,
    ProductPrice,
    StockOverview,
    SupplierEntry,
)
from ..domain.enums import MovementType
from ..domain.money import to_milli
from .stock_service import StockService

# 'FER-0001': 4 digitos de secuencia dentro de la categoria.
_SKU_SEQUENCE_DIGITS = 4

# Categoria semilla (ver schema.sql) usada como default para altas
# rapidas sin selector de categoria: importador y UI basica.
_DEFAULT_CATEGORY_CODE = "GEN"


class ProductService:
    def __init__(
        self,
        product_repo: ProductRepository,
        category_repo: CategoryRepository,
        tier_repo: PriceTierRepository,
        *,
        stock_service: StockService | None = None,
    ) -> None:
        self._products = product_repo
        self._categories = category_repo
        self._tiers = tier_repo
        # Opcional: solo hace falta para dar de alta con existencia
        # inicial (create_product(initial_stock=...)). Sin el, el alta
        # funciona igual que siempre, en cero.
        self._stock = stock_service

    def list_products(self) -> list[Product]:
        """
        Devuelve todos los productos activos, con sus precios cargados
        en lote (una sola consulta IN, ver load_prices_for): la tabla
        de listado necesita mostrar 'Precio 3' sin volver a pedirle un
        producto a la vez a la base.
        """
        products = self._products.list_summary()
        self._products.load_prices_for(products)
        return products

    def get_product(self, product_id: int) -> Product | None:
        """Producto completo (con precios y proveedores) para prellenar
        un formulario de edicion."""
        return self._products.get(product_id)

    def link_supplier(
        self,
        product_id: int,
        supplier_id: int,
        *,
        contact_name: str = "",
        last_purchase: date | None = None,
        last_cost: Decimal | None = None,
        note: str = "",
    ) -> None:
        """
        Registra que 'supplier_id' le trajo 'product_id' a la ferreteria:
        agrega una fila al historial de entregas (product_supplier_
        entries) y deja el vinculo ACTUAL (product_suppliers) apuntando
        a esta ultima entrega, sin pisar las anteriores -ver
        ProductRepository.link_supplier(). Muchos-a-muchos (regla 6): un
        producto puede tener varios proveedores y esto no toca a los
        otros.

        El costo entra como Decimal ('1,24') y se guarda en milesimas via
        to_milli (regla 1), igual que el costo del producto.
        """
        if last_cost is not None and last_cost < 0:
            raise ValueError("El costo no puede ser negativo.")
        if self._products.get(product_id) is None:
            raise ValueError(f"No existe un producto con id={product_id}.")

        try:
            self._products.link_supplier(
                product_id,
                supplier_id,
                contact_name=contact_name.strip() or None,
                last_purchase=last_purchase,
                last_cost_milli=to_milli(last_cost) if last_cost is not None else None,
                note=note.strip() or None,
            )
        except sqlite3.IntegrityError as error:
            # La FK de product_suppliers.supplier_id (PRAGMA foreign_keys,
            # regla 9) es la que detecta un proveedor inexistente: se
            # traduce a un mensaje en espanol en vez de dejar subir el
            # error crudo de SQLite hasta la pantalla.
            raise ValueError(f"No existe un proveedor con id={supplier_id}.") from error

    def unlink_supplier(self, product_id: int, supplier_id: int) -> None:
        """
        Saca el vinculo ACTUAL (un proveedor cargado por error, o que ya
        no trae este producto). No borra ni al producto ni al proveedor,
        y el historial de entregas queda intacto (ver
        ProductRepository.unlink_supplier).
        """
        self._products.unlink_supplier(product_id, supplier_id)

    def list_supplier_entries(
        self, product_id: int, supplier_id: int
    ) -> list[SupplierEntry]:
        """
        Historial de entregas de este producto con este proveedor, la
        mas reciente primero (para "Registrar proveedor...", que ahora
        muestra quien lo trajo y cuando ademas de anotar una entrega
        nueva).
        """
        return self._products.list_entries(product_id, supplier_id)

    def distinct_contacts_for_supplier(self, supplier_id: int) -> list[str]:
        """
        Contactos ya vistos en entregas de este proveedor (en cualquier
        producto), para ofrecerlos en vez de tipearlos de nuevo.
        """
        return self._products.distinct_contacts_for_supplier(supplier_id)

    def list_all_supplier_entries(self, product_id: int) -> list[SupplierEntry]:
        """
        TODAS las entregas de este producto, de cualquier proveedor, la
        mas reciente primero. Para la tabla "Entregas" de la ficha del
        producto: a diferencia de Product.suppliers (el vinculo ACTUAL,
        una fila por proveedor), esto es el historial completo.
        """
        return self._products.list_entries_for_product(product_id)

    def delete_supplier_entry(self, entry_id: int) -> None:
        """
        Borra una entrega cargada por error. El vinculo actual
        (product_suppliers) se recalcula solo a partir de lo que quede
        para ese par -o desaparece, si no quedo ninguna- (ver
        ProductRepository.delete_entry). Quien llama ya tiene que haber
        confirmado con el usuario: esto no pregunta nada.
        """
        self._products.delete_entry(entry_id)

    def list_supplier_entries_for_export(self) -> list[SupplierEntry]:
        """
        TODAS las entregas del catalogo completo -de cualquier producto y
        proveedor-, para ExportService (ver ProductRepository.
        list_entries_for_export). Distinto de list_all_supplier_entries:
        ese es el historial de UN producto puntual (para su ficha), este
        es el de TODO el catalogo (para exportar a Excel/PDF).
        """
        return self._products.list_entries_for_export()

    def list_needing_attention(self, limit: int = 50) -> list[Product]:
        """Negativos y bajo minimo, para "Requiere atencion" en Inicio."""
        return self._products.list_needing_attention(limit)

    def stock_overview(self) -> StockOverview:
        """Conteos de existencia para el tablero de Inicio (una consulta)."""
        return self._products.stock_overview()

    def list_products_for_supplier(self, supplier_id: int) -> list[ProductLink]:
        """Para la ficha de un proveedor (SupplierDialog): que productos le compra."""
        return self._products.list_summary_for_supplier(supplier_id)

    def list_price_tiers(self) -> list[PriceTier]:
        """
        Para que quien arme un formulario de precios sepa que tier_id
        le corresponde a 'Detal'/'Mayor'/'Especial' sin tocar SQL.
        """
        return self._tiers.list_all()

    def default_category_id(self) -> int:
        """
        La categoria semilla, para altas que todavia no tienen un
        selector de categoria propio (ver ProductsPage y
        legacy_importer.py). El dia que haya un combobox real, este
        metodo deja de usarse.
        """
        category = self._categories.get_by_code(_DEFAULT_CATEGORY_CODE)
        if category is None:
            raise ValueError(
                f"No existe la categoría semilla '{_DEFAULT_CATEGORY_CODE}'."
            )
        return category.id

    def create_product(
        self,
        *,
        name: str,
        category_id: int,
        prices: dict[int, Decimal],
        cost: Decimal = Decimal(0),
        unit: str = "UNI",
        min_stock: Decimal = Decimal(0),
        legacy_code: str | None = None,
        description: str = "",
        initial_stock: Decimal = Decimal(0),
        counted_by: str | None = None,
    ) -> Product:
        """
        Genera el SKU y crea el producto.

        Con initial_stock > 0, en la MISMA transaccion se registra un
        movimiento INITIAL por esa cantidad (via StockService, el unico
        que toca current_stock - Regla 2): o quedan los dos, o ninguno.
        Nunca un producto creado con la existencia a medio cargar. Son
        tres escrituras (producto + precios, movimiento, cache de stock)
        en un solo COMMIT.

        NOTA sobre escalas: cost_milli y price_milli, pese al nombre
        "money", se guardan multiplicados por 1000 (to_milli), NO por
        100 como los centavos de una factura (unit_price_cents usa
        to_cents). Es una decision ya tomada en el esquema/dominio:
        el catalogo permite 3 decimales de precio por unidad (para
        vender fraccionado), la factura solo necesita 2. Mezclar estas
        dos funciones es exactamente el tipo de bug que prohibe la
        Regla 1 del CLAUDE.md.
        """
        name = self._validate_name_and_prices(name, prices)

        category = self._categories.get(category_id)
        if category is None:
            raise ValueError(f"No existe la categoría con id={category_id}.")

        product = Product(
            id=None,
            sku=self._next_sku(category),
            name=name,
            legacy_code=legacy_code,
            description=description,
            category_id=category_id,
            unit=unit,
            cost_milli=to_milli(cost),
            min_stock=to_milli(min_stock),
        )
        product.prices = self._build_prices(prices)

        existencia_milli = to_milli(initial_stock)
        if existencia_milli < 0:
            raise ValueError("La existencia inicial no puede ser negativa.")
        if existencia_milli == 0:
            self._products.create(product)
            return product
        if self._stock is None:
            raise RuntimeError(
                "ProductService necesita stock_service para cargar existencia inicial."
            )

        with self._stock.atomic():
            self._products.insert(product)
            self._stock.apply_movement(
                product_id=product.id,
                type=MovementType.INITIAL,
                quantity_milli=existencia_milli,
                unit_cost_milli=product.cost_milli or None,
                reason="Existencia inicial al crear el producto",
                counted_by=(counted_by or "").strip() or None,
            )
        product.current_stock = existencia_milli
        return product

    def update_product(
        self,
        product_id: int,
        *,
        name: str,
        prices: dict[int, Decimal],
        cost: Decimal = Decimal(0),
        is_active: bool = True,
        unit: str | None = None,
        description: str | None = None,
    ) -> Product:
        """
        Actualiza nombre, costo, precios y el estado activo/inactivo de
        un producto existente (reactivar es esto, con is_active=True:
        ver deactivate_product()).

        unit y description son opcionales A PROPOSITO: None (el default)
        significa "no lo toques", asi que un caller que no los pasa deja
        el producto tal cual estaba (product ya viene con sus valores
        actuales via get()). Pasar '' es distinto de no pasar nada: SI
        borra la descripcion. Categoria y min_stock siguen sin
        formulario que los edite, quedan tal cual estaban.
        """
        name = self._validate_name_and_prices(name, prices)

        product = self._products.get(product_id)
        if product is None:
            raise ValueError(f"No existe un producto con id={product_id}.")

        product.name = name
        product.cost_milli = to_milli(cost)
        product.prices = self._build_prices(prices)
        product.is_active = is_active
        if unit is not None:
            product.unit = unit
        if description is not None:
            product.description = description

        self._products.update(product)
        return product

    def deactivate_product(self, product_id: int) -> None:
        """
        'Elimina' un producto sin borrar la fila (nunca se borra:
        es el anclaje de facturas y movimientos pasados). Reactivarlo
        es un update_product que vuelva a poner is_active en True.
        """
        product = self._products.get(product_id)
        if product is None:
            raise ValueError(f"No existe un producto con id={product_id}.")

        product.is_active = False
        self._products.update(product)

    @staticmethod
    def _validate_name_and_prices(name: str, prices: dict[int, Decimal]) -> str:
        name = name.strip()
        if not name:
            raise ValueError("El nombre del producto no puede estar vacío.")
        if not prices:
            raise ValueError("El producto necesita al menos un precio.")
        return name

    @staticmethod
    def _build_prices(prices: dict[int, Decimal]) -> list[ProductPrice]:
        return [
            ProductPrice(tier_id=tier_id, price_milli=to_milli(price))
            for tier_id, price in prices.items()
        ]

    def _next_sku(self, category: Category) -> str:
        """
        PREFIJO-0001, donde PREFIJO es el codigo de la categoria y el
        numero es 'cuantos productos tiene ya esa categoria, mas uno'.
        Nunca se reutiliza: las filas de products no se borran, asi
        que el conteo solo crece (ver ProductRepository.create()).
        """
        sequence = self._products.count_by_category(category.id) + 1
        return f"{category.code}-{sequence:0{_SKU_SEQUENCE_DIGITS}d}"
