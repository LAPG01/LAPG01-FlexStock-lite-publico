"""
Tests de legacy_importer.py: el flujo completo de DOS corridas.

    1ra corrida: no existe el CSV -> se genera, no se toca la base.
    2da corrida: CSV ya revisado -> se cargan productos y proveedores.

Usa el mismo fixture de PRODR.TXT que test_txt_parser.py (lineas
reales, verificadas byte a byte) mas SQLite en memoria para la base.

    pytest -q
"""

import csv
import sqlite3
from decimal import Decimal

import pytest

from flexstock.data.connection import init_schema
from flexstock.data.repositories.category_repository import CategoryRepository
from flexstock.data.repositories.price_tier_repository import PriceTierRepository
from flexstock.data.repositories.product_repository import ProductRepository
from flexstock.data.repositories.supplier_repository import SupplierRepository
from flexstock.domain.money import to_milli
from flexstock.importers.legacy_importer import run_import
from flexstock.importers.supplier_normalizer import CSV_FIELDS
from flexstock.services.product_service import ProductService
from flexstock.services.supplier_service import SupplierService

_HEADER = "Código Descripción                              Referencia        Costo unitario Existencia   Unidades  Costo existenci         Precio 1         Precio 2         Precio 3"
_SEPARATOR = "------ ---------------------------------------- --------------- ---------------- ---------- ---------- ---------------- ---------------- ---------------- ----------------"
_ROW_TORNILLO = "TOR123 TORNILLO HEX 1/2X3 UNC-A                                             0,56         10          0             5,60             1,50             1,65             1,80"
_SUPPLIER_LINE_1 = "       09/10/24-SURTEC/MARIANA                                                                                                                                           "
_ROW_VALV34 = "VALV34 VALVULA 3/4 BRONCE PASO RECTO C/VOLANTE.                           445,00          4          0         1.780,00             3,00             4,00             5,00"
_SUPPLIER_LINE_2 = "       20/09/24-SURTEC-MARIANA SOTO                                                                                                                                    "
_TOTALS_ROW = "                                                                                      14           0         2225,60                                                        "


@pytest.fixture
def prodr_path(tmp_path):
    path = tmp_path / "PRODR.TXT"
    lines = [
        " " * 60 + "REPORTE DE PRODUCTOS",
        "Tipo    : General",
        _HEADER,
        _SEPARATOR,
        _ROW_TORNILLO,
        _SUPPLIER_LINE_1,
        _ROW_VALV34,
        _SUPPLIER_LINE_2,
        _SEPARATOR,
        _TOTALS_ROW,
        "",
    ]
    path.write_bytes("\r\n".join(lines).encode("cp850"))
    return path


@pytest.fixture
def review_csv_path(tmp_path):
    return tmp_path / "suppliers_to_review.csv"


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    init_schema(connection)
    yield connection
    connection.close()


@pytest.fixture
def repos(conn):
    product_repo = ProductRepository(conn)
    category_repo = CategoryRepository(conn)
    tier_repo = PriceTierRepository(conn)
    supplier_repo = SupplierRepository(conn)
    return {
        "product_service": ProductService(product_repo, category_repo, tier_repo),
        "product_repo": product_repo,
        "category_repo": category_repo,
        "supplier_repo": supplier_repo,
        "supplier_service": SupplierService(supplier_repo),
        "tier_repo": tier_repo,
    }


def _run(prodr_path, review_csv_path, repos):
    return run_import(
        prodr_path,
        review_csv_path,
        repos["product_service"],
        repos["product_repo"],
        repos["supplier_repo"],
        repos["supplier_service"],
        repos["tier_repo"],
    )


def _marcar_revisado(review_csv_path, canonical_by_key: dict[str, str]):
    with review_csv_path.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))
    for fila in filas:
        if fila["normalized_key"] in canonical_by_key:
            fila["canonical_name"] = canonical_by_key[fila["normalized_key"]]
            fila["is_supplier"] = "SI"
    with review_csv_path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        writer.writerows(filas)


# ------------------------------------------------------------
# 1ra corrida: genera el CSV, no toca la base
# ------------------------------------------------------------
def test_primera_corrida_genera_csv_y_no_escribe_productos(
    prodr_path, review_csv_path, repos
):
    report = _run(prodr_path, review_csv_path, repos)

    assert report.generated_review_csv is True
    assert report.products_imported == 0
    assert review_csv_path.exists()
    assert repos["product_repo"].list_summary(only_active=False) == []


def test_csv_generado_tiene_un_grupo_para_surtec(prodr_path, review_csv_path, repos):
    _run(prodr_path, review_csv_path, repos)

    with review_csv_path.open(encoding="utf-8", newline="") as f:
        filas = list(csv.DictReader(f))

    assert [f["normalized_key"] for f in filas] == ["SURTEC"]
    assert filas[0]["occurrences"] == "2"


# ------------------------------------------------------------
# 2da corrida: CSV revisado, carga a la base
# ------------------------------------------------------------
def test_segunda_corrida_importa_productos_y_precios(
    prodr_path, review_csv_path, repos
):
    _run(prodr_path, review_csv_path, repos)  # genera el csv
    _marcar_revisado(review_csv_path, {"SURTEC": "SURTEC C.A."})

    report = _run(prodr_path, review_csv_path, repos)

    assert report.products_imported == 2
    productos = repos["product_repo"].list_summary(only_active=False)
    assert {p.legacy_code for p in productos} == {"TOR123", "VALV34"}

    tornillo = next(p for p in productos if p.legacy_code == "TOR123")
    completo = repos["product_repo"].get(tornillo.id)
    assert completo.cost_milli == to_milli(Decimal("0.56"))
    assert {p.price_milli for p in completo.prices} == {
        to_milli(Decimal("1.50")),
        to_milli(Decimal("1.65")),
        to_milli(Decimal("1.80")),
    }


def test_segunda_corrida_vincula_proveedor_revisado(prodr_path, review_csv_path, repos):
    _run(prodr_path, review_csv_path, repos)
    _marcar_revisado(review_csv_path, {"SURTEC": "SURTEC C.A."})

    report = _run(prodr_path, review_csv_path, repos)

    assert report.supplier_links == 2
    proveedores = repos["supplier_repo"].list_all()
    assert [s.name for s in proveedores] == ["SURTEC C.A."]

    tornillo = next(
        p
        for p in repos["product_repo"].list_summary(only_active=False)
        if p.legacy_code == "TOR123"
    )
    completo = repos["product_repo"].get(tornillo.id)
    assert completo.suppliers[0].supplier_name == "SURTEC C.A."
    assert completo.suppliers[0].last_purchase.isoformat() == "2024-10-09"


def test_segunda_corrida_no_revisado_queda_pendiente_y_sin_vinculo(
    prodr_path, review_csv_path, repos
):
    _run(prodr_path, review_csv_path, repos)
    # no se marca nada como revisado: SURTEC sigue con canonical_name vacio

    report = _run(prodr_path, review_csv_path, repos)

    assert report.products_imported == 2
    assert report.supplier_links == 0
    assert "SURTEC" in report.pending_suppliers
    assert repos["supplier_repo"].list_all() == []


def test_segunda_corrida_producto_queda_con_stock_en_cero(
    prodr_path, review_csv_path, repos
):
    """
    'existencia' del txt (10 unidades para TOR123) NO se carga
    todavia: el stock solo lo toca StockService (Regla 2), que llega
    en una fase posterior.
    """
    _run(prodr_path, review_csv_path, repos)
    _marcar_revisado(review_csv_path, {"SURTEC": "SURTEC C.A."})
    _run(prodr_path, review_csv_path, repos)

    productos = repos["product_repo"].list_summary(only_active=False)
    assert all(p.current_stock == 0 for p in productos)
