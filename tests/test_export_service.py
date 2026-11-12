"""
Tests de ExportService: que cada export_*_csv()/export_*_pdf() escriba
un archivo legible, con las columnas esperadas (todas por default, o
solo las elegidas via 'columns') y sin volver a golpear la base por
fila (los service ya resuelven la carga en lote - ver
test_billing_service.py / test_product_service.py para eso, aca solo
se prueba el archivo en si).

    pytest -q
"""

import csv
import sqlite3
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.billing_service import BillingService
from flexstock.services.customer_service import CustomerService
from flexstock.services.export_service import ExportService
from flexstock.services.product_service import ProductService
from flexstock.services.stock_service import StockService
from flexstock.services.supplier_service import SupplierService


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def product_service(conn):
    return ProductService(
        ProductRepository(conn), CategoryRepository(conn), PriceTierRepository(conn)
    )


@pytest.fixture
def supplier_service(conn):
    return SupplierService(SupplierRepository(conn))


@pytest.fixture
def customer_service(conn):
    return CustomerService(CustomerRepository(conn))


@pytest.fixture
def billing_service(conn):
    product_repo = ProductRepository(conn)
    stock_service = StockService(conn, product_repo, InventoryMovementRepository(conn))
    return BillingService(
        conn,
        InvoiceRepository(conn),
        product_repo,
        CustomerRepository(conn),
        PriceTierRepository(conn),
        stock_service,
    )


@pytest.fixture
def export_service(
    product_service, supplier_service, billing_service, customer_service
):
    return ExportService(
        product_service, supplier_service, billing_service, customer_service
    )


def _tier_id_default(product_service):
    return next(t.id for t in product_service.list_price_tiers() if t.is_default)


def _leer_csv(ruta):
    with ruta.open(encoding="utf-8-sig", newline="") as archivo:
        return list(csv.reader(archivo))


# ------------------------------------------------------------
# productos
# ------------------------------------------------------------
def test_export_products_csv_incluye_precio_por_tier(
    product_service, export_service, tmp_path
):
    tier_id = _tier_id_default(product_service)
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={tier_id: Decimal("1.50")},
        cost=Decimal("0.80"),
    )

    ruta = export_service.export_products_csv(tmp_path)
    filas = _leer_csv(ruta)

    assert ruta.parent == tmp_path
    assert ruta.suffix == ".csv"
    encabezado, fila = filas[0], filas[1]
    assert "sku" in encabezado
    assert fila[encabezado.index("nombre")] == "Tornillo"
    assert fila[encabezado.index("costo")] == "0.800"  # from_milli(): 3 decimales fijos


def test_export_products_csv_columna_vacia_si_no_tiene_precio_en_ese_tier(
    product_service, export_service, tmp_path
):
    tiers = product_service.list_price_tiers()
    un_tier, otro_tier = tiers[0], tiers[1]
    product_service.create_product(
        name="Tuerca",
        category_id=product_service.default_category_id(),
        prices={un_tier.id: Decimal("0.10")},
    )

    ruta = export_service.export_products_csv(tmp_path)
    encabezado, fila = _leer_csv(ruta)[0], _leer_csv(ruta)[1]

    columna_vacia = f"precio_{otro_tier.name.lower()}"
    assert fila[encabezado.index(columna_vacia)] == ""


def test_product_export_columns_usa_el_nombre_real_del_tier(product_service):
    columnas = ExportService(product_service, None, None, None).product_export_columns()

    tiers = {t.position: t.name for t in product_service.list_price_tiers()}
    for posicion, nombre in tiers.items():
        columna = next(c for c in columnas if c.key == f"price{posicion}")
        assert columna.label == f"{nombre} $"
        assert columna.header == f"precio_{nombre.lower()}"


def test_export_products_csv_precio_detal_bs_a_bcv(
    conn, product_service, supplier_service, billing_service, customer_service, tmp_path
):
    from flexstock.data.connection import set_setting
    from flexstock.services.exchange_rate_service import ExchangeRateService
    from flexstock.services.rate_display_service import RateDisplayService

    set_setting(conn, "bcv_rate", "848.55")
    exportador = ExportService(
        product_service,
        supplier_service,
        billing_service,
        customer_service,
        rate_display_service=RateDisplayService(conn, ExchangeRateService(conn)),
    )
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("2.50")},
    )

    ruta = exportador.export_products_csv(tmp_path, columns=("name", "price3_bs_bcv"))
    encabezado, fila = _leer_csv(ruta)[0], _leer_csv(ruta)[1]

    assert encabezado == ["nombre", "precio_detal_bs_bcv"]
    assert fila == ["Tornillo", "2121.38"]  # 2,50 x 848,55


def test_export_products_sin_tasas_deja_vacio_el_precio_bs_a_bcv(
    product_service, export_service, tmp_path
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("2.50")},
    )

    ruta = export_service.export_products_csv(tmp_path, columns=("price3_bs_bcv",))

    assert _leer_csv(ruta)[1] == [""]


def test_export_products_csv_solo_las_columnas_elegidas(
    product_service, export_service, tmp_path
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )

    ruta = export_service.export_products_csv(tmp_path, columns=("sku", "name"))
    encabezado = _leer_csv(ruta)[0]

    assert encabezado == ["sku", "nombre"]


def test_export_products_csv_columnas_invalidas_cae_a_todas(
    product_service, export_service, tmp_path
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )

    ruta = export_service.export_products_csv(tmp_path, columns=("no_existe",))
    encabezado = _leer_csv(ruta)[0]

    assert "nombre" in encabezado
    assert len(encabezado) == len(export_service.product_export_columns())


def test_export_products_pdf_arranca_como_pdf(
    product_service, export_service, tmp_path
):
    product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    )

    ruta = export_service.export_products_pdf(tmp_path)

    assert ruta.suffix == ".pdf"
    assert ruta.read_bytes().startswith(b"%PDF")


# ------------------------------------------------------------
# proveedores
# ------------------------------------------------------------
def test_export_suppliers_csv(supplier_service, export_service, tmp_path):
    supplier_service.create_supplier(
        name="Ferretotal", phone="0412-1234567", address="Av. Principal"
    )

    ruta = export_service.export_suppliers_csv(tmp_path)
    encabezado, fila = _leer_csv(ruta)[0], _leer_csv(ruta)[1]

    assert fila[encabezado.index("nombre")] == "Ferretotal"
    assert fila[encabezado.index("telefono")] == "0412-1234567"
    assert fila[encabezado.index("direccion")] == "Av. Principal"


def test_export_suppliers_pdf_arranca_como_pdf(
    supplier_service, export_service, tmp_path
):
    supplier_service.create_supplier(name="Ferretotal", phone="0412-1234567")

    ruta = export_service.export_suppliers_pdf(tmp_path)

    assert ruta.suffix == ".pdf"
    assert ruta.read_bytes().startswith(b"%PDF")


# ------------------------------------------------------------
# entregas (product_supplier_entries, el catalogo completo)
# ------------------------------------------------------------
def test_export_supplier_entries_csv_incluye_producto_y_proveedor(
    product_service, supplier_service, export_service, tmp_path
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    sid = supplier_service.create_supplier(name="LIMAR").id
    product_service.link_supplier(
        pid,
        sid,
        contact_name="Ramon",
        last_cost=Decimal("0.80"),
        note="caja de 10",
    )

    ruta = export_service.export_supplier_entries_csv(tmp_path)
    encabezado, fila = _leer_csv(ruta)[0], _leer_csv(ruta)[1]

    assert fila[encabezado.index("producto")] == "Tornillo"
    assert fila[encabezado.index("proveedor")] == "LIMAR"
    assert fila[encabezado.index("contacto")] == "Ramon"
    assert fila[encabezado.index("costo")] == "0.800"
    assert fila[encabezado.index("nota")] == "caja de 10"


def test_export_supplier_entries_csv_una_fila_por_entrega_no_por_proveedor(
    product_service, supplier_service, export_service, tmp_path
):
    """A diferencia de Product.suppliers (el vinculo ACTUAL), cada
    registro queda como su propia fila: nada se pisa."""
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    sid = supplier_service.create_supplier(name="LIMAR").id
    product_service.link_supplier(pid, sid, last_cost=Decimal("0.80"), note="primera")
    product_service.link_supplier(pid, sid, last_cost=Decimal("0.90"), note="segunda")

    ruta = export_service.export_supplier_entries_csv(tmp_path)
    filas = _leer_csv(ruta)[1:]

    assert len(filas) == 2


def test_export_supplier_entries_csv_sin_entregas_solo_trae_encabezado(
    export_service, tmp_path
):
    ruta = export_service.export_supplier_entries_csv(tmp_path)

    assert len(_leer_csv(ruta)) == 1


def test_export_supplier_entries_pdf_arranca_como_pdf(
    product_service, supplier_service, export_service, tmp_path
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    sid = supplier_service.create_supplier(name="LIMAR").id
    product_service.link_supplier(pid, sid)

    ruta = export_service.export_supplier_entries_pdf(tmp_path)

    assert ruta.suffix == ".pdf"
    assert ruta.read_bytes().startswith(b"%PDF")


def test_export_supplier_entries_respeta_columnas_elegidas(
    product_service, supplier_service, export_service, tmp_path
):
    pid = product_service.create_product(
        name="Tornillo",
        category_id=product_service.default_category_id(),
        prices={_tier_id_default(product_service): Decimal("1.50")},
    ).id
    sid = supplier_service.create_supplier(name="LIMAR").id
    product_service.link_supplier(pid, sid, contact_name="Ramon")

    ruta = export_service.export_supplier_entries_csv(
        tmp_path, columns=("product_name", "supplier_name")
    )
    encabezado = _leer_csv(ruta)[0]

    assert encabezado == ["producto", "proveedor"]


# ------------------------------------------------------------
# clientes
# ------------------------------------------------------------
def test_export_customers_csv(customer_service, export_service, tmp_path):
    customer_service.create_customer(
        name="Juan Perez", tax_id="V-12345678", is_wholesale=True
    )

    ruta = export_service.export_customers_csv(tmp_path)
    encabezado, fila = _leer_csv(ruta)[0], _leer_csv(ruta)[1]

    assert fila[encabezado.index("nombre")] == "Juan Perez"
    assert fila[encabezado.index("rif_cedula")] == "V-12345678"
    assert fila[encabezado.index("mayorista")] == "Si"


def test_export_customers_csv_solo_las_columnas_elegidas(
    customer_service, export_service, tmp_path
):
    customer_service.create_customer(name="Juan Perez")

    ruta = export_service.export_customers_csv(tmp_path, columns=("name", "phone"))
    encabezado = _leer_csv(ruta)[0]

    assert encabezado == ["nombre", "telefono"]


def test_export_customers_pdf_arranca_como_pdf(
    customer_service, export_service, tmp_path
):
    customer_service.create_customer(name="Juan Perez")

    ruta = export_service.export_customers_pdf(tmp_path)

    assert ruta.suffix == ".pdf"
    assert ruta.read_bytes().startswith(b"%PDF")


# ------------------------------------------------------------
# facturas
# ------------------------------------------------------------
def test_export_invoices_csv_trae_el_total_sin_cargar_lineas_por_factura(
    conn, product_service, billing_service, export_service, tmp_path
):
    tier_id = _tier_id_default(product_service)
    producto = product_service.create_product(
        name="Clavo",
        category_id=product_service.default_category_id(),
        prices={tier_id: Decimal("1.50")},
    )
    conn.execute(
        "UPDATE products SET current_stock = ? WHERE id = ?", (10000, producto.id)
    )
    factura = billing_service.crear_borrador(customer_name="Mostrador")
    billing_service.agregar_linea(factura, product_id=producto.id, quantity=Decimal(2))
    billing_service.confirmar(factura)

    ruta = export_service.export_invoices_csv(tmp_path)
    encabezado, fila = _leer_csv(ruta)[0], _leer_csv(ruta)[1]

    assert fila[encabezado.index("cliente")] == "Mostrador"
    assert fila[encabezado.index("total")] == "3.48"  # 3,00 + 16% IVA
    assert fila[encabezado.index("estado")] == "Confirmada"
