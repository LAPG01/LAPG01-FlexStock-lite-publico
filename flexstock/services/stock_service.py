"""
Unico servicio autorizado a modificar products.current_stock (regla 2
del CLAUDE.md), y siempre junto con su InventoryMovement, en la misma
transaccion.

Dos formas de usarlo, segun quien orquesta la transaccion:

    apply_movement()  -> NO abre transaccion propia. Para cuando otra
                          escritura ya abrio una (BillingService.
                          confirmar()/cancelar()) y el movimiento de
                          stock tiene que quedar adentro de esa misma
                          transaccion atomica.
    ajustar()          -> SI abre su propia transaccion (Fase 8, ajuste
                          manual desde StockAdjustmentPage): no hay
                          ninguna otra escritura con la que componerse,
                          asi que StockService abre la suya. Por eso
                          recibe 'conn' en el constructor, igual que
                          BillingService y por la misma razon.
    atomic()           -> la transaccion, para quien NO tiene conexion
                          propia y necesita componer su escritura con un
                          movimiento: ProductService al dar de alta un
                          producto con existencia inicial.
"""

import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime

from ..data.connection import transaction
from ..data.repositories.inventory_movement_repository import (
    InventoryMovementRepository,
)
from ..data.repositories.product_repository import ProductRepository
from ..domain.entities import InventoryMovement
from ..domain.enums import MovementType, ReferenceType
from ..domain.errors import ReasonRequired


class StockService:
    def __init__(
        self,
        conn: sqlite3.Connection,
        product_repo: ProductRepository,
        movement_repo: InventoryMovementRepository,
    ) -> None:
        self._conn = conn
        self._products = product_repo
        self._movements = movement_repo

    @contextmanager
    def atomic(self) -> Iterator[None]:
        """
        Todo o nada, para componer un movimiento con otra escritura:

            with stock_service.atomic():
                product_repo.insert(producto)
                stock_service.apply_movement(...)

        Es data.connection.transaction() sobre la conexion de este
        servicio: quien llama no necesita (ni deberia) tener la conexion.
        """
        with transaction(self._conn):
            yield

    def apply_movement(
        self,
        *,
        product_id: int,
        type: MovementType,
        quantity_milli: int,
        unit_cost_milli: int | None = None,
        reason: str | None = None,
        reference_type: ReferenceType | None = None,
        reference_id: int | None = None,
        counted_by: str | None = None,
        note: str | None = None,
    ) -> InventoryMovement:
        """
        Registra el movimiento y ajusta el cache en un solo paso.

        quantity_milli es el delta CON SIGNO, igual que en el dominio:
        una venta se pasa negativa (-3000 = 3 unidades menos), una
        devolucion o compra positiva. No decide de donde viene ese
        signo: eso lo arma quien llama (BillingService sabe si es una
        salida por factura o una reversion por cancelacion).

        No valida stock disponible: esa es una regla de facturacion
        (¿se permite vender en negativo?, ver settings.allow_negative_stock),
        no una regla general de movimientos - un ADJUSTMENT por conteo
        fisico, por ejemplo, puede bajar el stock sin que haya "venta"
        de por medio. Quien quiera bloquear por stock insuficiente lo
        valida antes de llamar aca.

        counted_by/note son libres y opcionales (no hay login en la
        app): quien conto y una nota tipo bitacora ("llegaron 4, vienen
        6 mas"), utiles sobre todo en ADJUSTMENT. created_at se fija ACA
        en vez de dejarselo al DEFAULT de schema.sql: asi el objeto que
        se devuelve ya trae la fecha real, sin una segunda consulta para
        mostrarla (ver StockAdjustmentPage, "Ajustes de esta sesion").
        """
        if quantity_milli == 0:
            raise ValueError("Un movimiento de stock no puede tener cantidad cero.")
        if type is MovementType.ADJUSTMENT and not (reason or "").strip():
            raise ReasonRequired("Un ajuste de inventario necesita un motivo.")

        movement = InventoryMovement(
            id=None,
            product_id=product_id,
            type=type,
            quantity_milli=quantity_milli,
            unit_cost_milli=unit_cost_milli,
            reason=reason,
            reference_type=reference_type,
            reference_id=reference_id,
            counted_by=counted_by,
            note=note,
            created_at=datetime.now().replace(microsecond=0),  # noqa: DTZ005
        )
        self._movements.record(movement)
        self._products.adjust_stock(product_id, quantity_milli)
        return movement

    def ajustar(
        self,
        *,
        product_id: int,
        counted_milli: int,
        reason: str,
        counted_by: str | None = None,
        note: str | None = None,
    ) -> InventoryMovement:
        """
        Ajuste manual (conteo fisico, merma, error de sistema). Abre su
        propia transaccion (ver docstring del modulo).

        Recibe la cantidad CONTADA, no el delta con signo: pedirle a
        alguien que calcule "-3" a mano es facil de invertir por error;
        pedirle "cuanto contaste" y calcular el delta aca adentro no.
        Es el mismo ejemplo que ya usa el docstring de InventoryMovement
        ("conte 47 y decia 50 -> -3000").
        """
        product = self._products.get(product_id)
        if product is None:
            raise ValueError(f"No existe un producto con id={product_id}.")

        delta = counted_milli - product.current_stock
        if delta == 0:
            raise ValueError(
                "La cantidad contada es igual al stock actual: no hay nada que ajustar."
            )

        with transaction(self._conn):
            return self.apply_movement(
                product_id=product_id,
                type=MovementType.ADJUSTMENT,
                quantity_milli=delta,
                reason=reason,
                counted_by=counted_by,
                note=note,
            )

    def history_for(self, product_id: int) -> list[InventoryMovement]:
        """
        Historial COMPLETO de este producto -ventas, ajustes, carga
        inicial, reversiones por cancelacion-, el mas reciente primero.
        No solo lo contado en esta sesion (ver StockAdjustmentPage,
        "Ajustes de esta sesion"): esto es la Regla 2 del CLAUDE.md en
        persona, products.current_stock es la suma de estas filas.
        """
        return self._movements.list_for_product(product_id)
