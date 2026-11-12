"""
Orquesta la migracion de PRODR.TXT: lee -> normaliza proveedores ->
carga a la base. Es el unico archivo de importers/ que conoce
domain/money.py, los repositorios y ProductService; txt_parser.py y
supplier_normalizer.py no saben nada del esquema nuevo.

Flujo de DOS corridas (Regla del CLAUDE.md: la normalizacion de
proveedores se revisa a mano, no se adivina):

    1ra corrida: 'review_csv_path' todavia no existe -> se genera y
                 NO se toca la base. Alguien completa 'canonical_name'
                 (y 'is_supplier') a mano en ese CSV.
    2da corrida: 'review_csv_path' ya existe -> se cargan productos y
                 los vinculos de proveedor ya revisados. Lo que sigue
                 sin revisar se reporta en 'pending_suppliers', nunca
                 se inventa.

PENDIENTE A PROPOSITO (fuera del alcance de esta fase):
  - 'existencia' (stock inicial) NO se carga todavia: la Regla 2 del
    CLAUDE.md exige que el stock solo lo toque StockService, siempre
    junto a su InventoryMovement, y ese servicio no existe aun
    (llega con la Fase 6, facturacion). Importar productos con stock
    en 0 y cargarlo despues es mas seguro que inventar un StockService
    de apuro solo para esto.
  - 'unit' queda en el default 'UNI': la unidad real esta en INV.TXT,
    que se deja para una pasada aparte.
"""

import argparse
import sys
from dataclasses import dataclass, field
from pathlib import Path

from ..data.connection import connect, init_schema
from ..data.repositories.category_repository import CategoryRepository
from ..data.repositories.price_tier_repository import PriceTierRepository
from ..data.repositories.product_repository import ProductRepository
from ..data.repositories.supplier_repository import SupplierRepository
from ..domain.errors import DuplicateCode
from ..domain.money import to_milli
from ..services.product_service import ProductService
from ..services.supplier_service import SupplierService
from . import supplier_normalizer as normalizer
from . import txt_parser

_DEFAULT_CSV_NAME = "suppliers_to_review.csv"


@dataclass(slots=True)
class ImportReport:
    generated_review_csv: bool = False
    products_imported: int = 0
    supplier_links: int = 0
    pending_suppliers: set[str] = field(default_factory=set)


def run_import(
    prodr_path: Path,
    review_csv_path: Path,
    product_service: ProductService,
    product_repo: ProductRepository,
    supplier_repo: SupplierRepository,
    supplier_service: SupplierService,
    tier_repo: PriceTierRepository,
) -> ImportReport:
    raw_products = txt_parser.parse_prodr(prodr_path)

    if not review_csv_path.exists():
        _write_review_csv(raw_products, review_csv_path)
        return ImportReport(generated_review_csv=True)

    reviewed = normalizer.load_reviewed(review_csv_path)
    category_id = product_service.default_category_id()
    tier_id_by_position = _tier_id_by_position(tier_repo)
    supplier_id_cache: dict[str, int] = {}
    report = ImportReport()

    for raw in raw_products:
        product = product_service.create_product(
            name=raw["descripcion"],
            category_id=category_id,
            prices={
                tier_id_by_position[1]: raw["precio1"],
                tier_id_by_position[2]: raw["precio2"],
                tier_id_by_position[3]: raw["precio3"],
            },
            cost=raw["costo_unitario"],
            legacy_code=raw["codigo"],
            description=_build_description(raw["referencia"]),
        )
        report.products_imported += 1

        for raw_line in raw["supplier_lines"]:
            parsed = normalizer.parse_raw_line(raw_line)
            review = reviewed.get(parsed.normalized_key)
            if review is None:
                report.pending_suppliers.add(parsed.normalized_key)
                continue
            if not review.is_supplier:
                continue

            supplier_id = _resolve_supplier(
                review.canonical_name,
                supplier_id_cache,
                supplier_repo,
                supplier_service,
            )
            product_repo.link_supplier(
                product.id,
                supplier_id,
                contact_name=review.suggested_contact
                or parsed.candidate_contact
                or None,
                last_purchase=parsed.last_purchase,
                last_cost_milli=to_milli(raw["costo_unitario"]),
                note=None,
            )
            report.supplier_links += 1

    return report


def _resolve_supplier(
    canonical_name: str,
    cache: dict[str, int],
    supplier_repo: SupplierRepository,
    supplier_service: SupplierService,
) -> int:
    """
    Busca-o-crea con cache en memoria: muchas lineas crudas distintas
    terminan apuntando al mismo canonical_name (esa es justamente la
    idea de normalizar), y no tiene sentido volver a golpear la base
    cada vez que aparece.

    Crea via SupplierService (no supplier_repo.create() directo): es
    el service el que sabe generar el SKU 'PROV-0001' (regla del
    proyecto, igual que ProductService/CustomerService - los
    repositorios nunca inventan un identificador).
    """
    if canonical_name in cache:
        return cache[canonical_name]

    try:
        supplier_id = supplier_service.create_supplier(name=canonical_name).id
    except DuplicateCode:
        existente = next(
            s
            for s in supplier_repo.find_by_name_fuzzy(canonical_name)
            if s.name == canonical_name
        )
        supplier_id = existente.id

    cache[canonical_name] = supplier_id
    return supplier_id


def _tier_id_by_position(tier_repo: PriceTierRepository) -> dict[int, int]:
    return {tier.position: tier.id for tier in tier_repo.list_all()}


def _build_description(referencia: str) -> str:
    """
    Solo 23 de 1.937 productos traen 'referencia', y es texto libre
    sin estructura (telefonos, garantias, precios en dolares). No se
    puede mapear a un campo propio sin inventar significado que no
    esta: se preserva como anotacion en description en vez de tirarla.
    """
    return f"[REF] {referencia}" if referencia else ""


def _write_review_csv(raw_products: list[dict], path: Path) -> None:
    all_raw_lines = [line for p in raw_products for line in p["supplier_lines"]]
    groups = normalizer.group_lines(all_raw_lines)
    normalizer.write_review_csv(groups, path)


# ------------------------------------------------------------
# CLI: python -m flexstock.importers.legacy_importer PRODR_PATH [CSV_PATH]
# ------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Importa el catalogo desde PRODR.TXT (Fase 3)."
    )
    parser.add_argument("prodr_path", type=Path, help="Ruta a PRODR.TXT")
    parser.add_argument(
        "csv_path",
        type=Path,
        nargs="?",
        default=Path(_DEFAULT_CSV_NAME),
        help=f"CSV de revision de proveedores (default: {_DEFAULT_CSV_NAME})",
    )
    args = parser.parse_args()

    if not args.prodr_path.exists():
        print(f"No existe el archivo: {args.prodr_path}", file=sys.stderr)
        raise SystemExit(1)

    conn = connect()
    init_schema(conn)

    product_repo = ProductRepository(conn)
    category_repo = CategoryRepository(conn)
    supplier_repo = SupplierRepository(conn)
    tier_repo = PriceTierRepository(conn)
    product_service = ProductService(product_repo, category_repo, tier_repo)
    supplier_service = SupplierService(supplier_repo)

    try:
        report = run_import(
            args.prodr_path,
            args.csv_path,
            product_service,
            product_repo,
            supplier_repo,
            supplier_service,
            tier_repo,
        )
    finally:
        conn.close()

    if report.generated_review_csv:
        print(f"Se genero '{args.csv_path}'.")
        print(
            "Todavia no se importo nada: completa 'canonical_name' (y "
            "'is_supplier' donde haga falta) y volve a correr con el "
            "mismo CSV_PATH."
        )
        return

    print(f"Productos importados : {report.products_imported}")
    print(f"Vinculos de proveedor : {report.supplier_links}")
    if report.pending_suppliers:
        print(f"Proveedores sin revisar todavia ({len(report.pending_suppliers)}):")
        for key in sorted(report.pending_suppliers):
            print(f"  - {key!r}")


if __name__ == "__main__":
    main()
