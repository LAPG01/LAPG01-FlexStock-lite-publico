"""
Repositorio de categorias. Unico archivo que escribe SQL para
'categories'.

    list_all(only_active=True) -> listado completo, para combos/tablas.
    get(id)                    -> una categoria.
    create(category)           -> valida codigo unico y formato, inserta.
    update(category)           -> mismo cuidado con el codigo.

'code' es el prefijo del SKU ('FER', 'ELE'): siempre 3 letras
mayusculas. Igual que en product_repository.py, create()/update() abren
su propia transaccion y por eso no se pueden anidar dentro de otra ya
abierta.
"""

import re
import sqlite3

from ...domain.entities import Category
from ...domain.errors import DuplicateCode
from ..connection import transaction

_CODE_PATTERN = re.compile(r"^[A-Z]{3}$")


class CategoryRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    # ------------------------------------------------------------
    # Lectura
    # ------------------------------------------------------------
    def list_all(self, only_active: bool = True) -> list[Category]:
        sql = "SELECT id, code, name, is_active FROM categories"
        if only_active:
            sql += " WHERE is_active = 1"
        sql += " ORDER BY name"

        rows = self._conn.execute(sql).fetchall()
        return [self._row_to_category(row) for row in rows]

    def get(self, category_id: int) -> Category | None:
        row = self._conn.execute(
            "SELECT id, code, name, is_active FROM categories WHERE id = ?",
            (category_id,),
        ).fetchone()
        return self._row_to_category(row) if row else None

    def get_by_code(self, code: str) -> Category | None:
        row = self._conn.execute(
            "SELECT id, code, name, is_active FROM categories WHERE code = ?",
            (code,),
        ).fetchone()
        return self._row_to_category(row) if row else None

    # ------------------------------------------------------------
    # Escritura
    # ------------------------------------------------------------
    def create(self, category: Category) -> int:
        self._validate_code(category.code)

        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "INSERT INTO categories (code, name, is_active) VALUES (?, ?, ?)",
                    (category.code, category.name, int(category.is_active)),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate(e, category)
                if duplicate is None:
                    raise
                raise duplicate from e

            category_id = cur.lastrowid

        category.id = category_id
        return category_id

    def update(self, category: Category) -> None:
        if category.id is None:
            raise ValueError("No se puede actualizar una categoria sin id.")
        self._validate_code(category.code)

        with transaction(self._conn):
            try:
                cur = self._conn.execute(
                    "UPDATE categories SET code = ?, name = ?, is_active = ? "
                    "WHERE id = ?",
                    (
                        category.code,
                        category.name,
                        int(category.is_active),
                        category.id,
                    ),
                )
            except sqlite3.IntegrityError as e:
                duplicate = self._as_duplicate(e, category)
                if duplicate is None:
                    raise
                raise duplicate from e

            if cur.rowcount == 0:
                raise ValueError(f"No existe una categoria con id={category.id}.")

    # ------------------------------------------------------------
    # Helpers privados
    # ------------------------------------------------------------
    @staticmethod
    def _validate_code(code: str) -> None:
        """
        Se valida en Python, antes de tocar la base: es un chequeo de
        formato, no de unicidad, y no tiene sentido pagar un viaje a
        SQLite para algo que una regex resuelve gratis.
        """
        if not _CODE_PATTERN.match(code):
            raise ValueError(
                f"El codigo de categoria debe ser 3 letras mayusculas "
                f"(ej. 'FER'), recibido: '{code}'."
            )

    @staticmethod
    def _row_to_category(row: sqlite3.Row) -> Category:
        return Category(
            id=row["id"],
            code=row["code"],
            name=row["name"],
            is_active=bool(row["is_active"]),
        )

    @staticmethod
    def _as_duplicate(
        error: sqlite3.IntegrityError, category: Category
    ) -> DuplicateCode | None:
        message = str(error)
        if "categories.code" in message:
            return DuplicateCode(
                f"Ya existe una categoria con el codigo '{category.code}'."
            )
        if "categories.name" in message:
            return DuplicateCode(f"Ya existe una categoria llamada '{category.name}'.")
        return None
