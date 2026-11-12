"""
Punto de entrada de FlexStock-lite: arma la conexion, los repositorios,
los servicios, y abre la ventana principal.
"""

from flexstock.config import ensure_dirs
from flexstock.data.connection import connect, init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.customer_repository import CustomerRepository
from flexstock.data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from flexstock.data.repositories.invoice_repository import InvoiceRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.quotation_repository import QuotationRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.services.backup_service import BackupService
from flexstock.services.billing_service import BillingService
from flexstock.services.customer_service import CustomerService
from flexstock.services.exchange_rate_service import ExchangeRateService
from flexstock.services.export_service import ExportService
from flexstock.services.preview_service import PreviewService
from flexstock.services.product_service import ProductService
from flexstock.services.quotation_service import QuotationService
from flexstock.services.rate_display_service import RateDisplayService
from flexstock.services.settings_service import SettingsService
from flexstock.services.stock_service import StockService
from flexstock.services.supplier_service import SupplierService
from flexstock.ui.main_window import MainWindow


def main() -> None:
    ensure_dirs()
    conn = connect()
    init_schema(conn)

    category_repo = CategoryRepository(conn)
    tier_repo = PriceTierRepository(conn)
    product_repo = ProductRepository(conn)
    customer_repo = CustomerRepository(conn)
    supplier_repo = SupplierRepository(conn)
    invoice_repo = InvoiceRepository(conn)
    movement_repo = InventoryMovementRepository(conn)

    stock_service = StockService(conn, product_repo, movement_repo)
    product_service = ProductService(
        product_repo, category_repo, tier_repo, stock_service=stock_service
    )
    customer_service = CustomerService(customer_repo)
    supplier_service = SupplierService(supplier_repo)
    billing_service = BillingService(
        conn, invoice_repo, product_repo, customer_repo, tier_repo, stock_service
    )

    exchange_rate_service = ExchangeRateService(conn)
    quotation_service = QuotationService(
        conn,
        QuotationRepository(conn),
        product_repo,
        customer_repo,
        tier_repo,
        exchange_rate_service,
    )

    rate_display_service = RateDisplayService(conn, exchange_rate_service)

    window = MainWindow(
        product_service=product_service,
        customer_service=customer_service,
        supplier_service=supplier_service,
        billing_service=billing_service,
        stock_service=stock_service,
        backup_service=BackupService(conn),
        export_service=ExportService(
            product_service,
            supplier_service,
            billing_service,
            customer_service,
            rate_display_service=rate_display_service,
        ),
        exchange_rate_service=exchange_rate_service,
        settings_service=SettingsService(conn),
        quotation_service=quotation_service,
        rate_display_service=rate_display_service,
        preview_service=PreviewService(conn, rate_display_service),
    )

    try:
        window.mainloop()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
