"""
Reglas de negocio de clientes: genera el SKU y valida los datos antes de
que lleguen al repositorio. No abre conexiones ni escribe SQL: eso es
trabajo de data/.
"""

import re

from ..data.repositories.customer_repository import CustomerRepository
from ..domain.contact import clean_email, format_phone
from ..domain.entities import Customer

# 'CLI-0001': prefijo fijo, no depende de ninguna categoria (a diferencia
# del SKU de productos).
_SKU_PREFIX = "CLI"
_SKU_SEQUENCE_DIGITS = 4

# Tipos de identificacion venezolanos, en el orden en que se ofrecen en
# el formulario (ver CustomerDialog): el primero es el default.
TAX_ID_TYPES = (
    ("V", "V - Venezolano"),
    ("E", "E - Extranjero"),
    ("J", "J - Jurídico (empresa)"),
    ("G", "G - Gubernamental"),
    ("P", "P - Pasaporte"),
)

# Cedula (V/E/P) o RIF (J/G): letra, guion, 6 a 9 digitos. No es una
# validacion de RIF completa (esa lleva digito verificador aparte),
# alcanza para atajar errores de tipeo en el formulario.
_TAX_ID_PATTERN = re.compile(rf"^[{''.join(t for t, _e in TAX_ID_TYPES)}]-\d{{6,9}}$")


# update_customer(notes=...) distingue "no me pasaron notas" (se conservan las
# que tenia) de "borralas" (None o vacio): con un None por defecto, un
# llamador que no conoce las notas se las borraria sin querer.
_SIN_CAMBIOS = object()


class CustomerService:
    def __init__(self, customer_repo: CustomerRepository) -> None:
        self._customers = customer_repo

    def list_customers(self) -> list[Customer]:
        """Devuelve todos los clientes activos."""
        return self._customers.list_all()

    def get_customer(self, customer_id: int) -> Customer | None:
        return self._customers.get(customer_id)

    def create_customer(
        self,
        *,
        name: str,
        tax_id: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None = None,
        is_wholesale: bool = False,
    ) -> Customer:
        """Genera el SKU y crea el cliente. Solo 'name' es obligatorio."""
        name = self._validate_name(name)
        tax_id = self._validate_tax_id(tax_id)

        customer = Customer(
            id=None,
            sku=self._next_sku(),
            name=name,
            tax_id=tax_id,
            phone=format_phone(phone),
            email=clean_email(email),
            address=_blank_to_none(address),
            notes=_blank_to_none(notes),
            is_wholesale=is_wholesale,
        )
        self._customers.create(customer)
        return customer

    def update_customer(
        self,
        customer_id: int,
        *,
        name: str,
        tax_id: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        notes: str | None | object = _SIN_CAMBIOS,
        is_wholesale: bool = False,
        is_active: bool = True,
    ) -> Customer:
        """
        El SKU no se toca: no es editable, se genero una sola vez al
        alta. is_active=True es tambien como se reactiva un cliente
        desactivado (ver deactivate_customer()). Sin 'notes' se conservan
        las que ya tenia; con '' o None se borran.
        """
        name = self._validate_name(name)
        tax_id = self._validate_tax_id(tax_id)

        customer = self._customers.get(customer_id)
        if customer is None:
            raise ValueError(f"No existe un cliente con id={customer_id}.")

        customer.name = name
        customer.tax_id = tax_id
        customer.phone = format_phone(phone)
        customer.email = clean_email(email, actual=customer.email)
        customer.address = _blank_to_none(address)
        if notes is not _SIN_CAMBIOS:
            customer.notes = _blank_to_none(notes)  # type: ignore[arg-type]
        customer.is_wholesale = is_wholesale
        customer.is_active = is_active

        self._customers.update(customer)
        return customer

    def deactivate_customer(self, customer_id: int) -> None:
        """'Elimina' sin borrar la fila: reactivar es un update_customer normal."""
        customer = self._customers.get(customer_id)
        if customer is None:
            raise ValueError(f"No existe un cliente con id={customer_id}.")

        customer.is_active = False
        self._customers.update(customer)

    @staticmethod
    def _validate_name(name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("El nombre del cliente no puede estar vacío.")
        return name

    @staticmethod
    def _validate_tax_id(tax_id: str | None) -> str | None:
        """Opcional: vacio se guarda como NULL, no como ''.

        Dos clientes sin cedula no pueden chocar contra el UNIQUE de la
        columna si ambos guardan NULL (SQLite trata cada NULL como
        distinto); si guardaran '' en cambio, el segundo alta fallaria
        con un DuplicateCode falso.
        """
        tax_id = (tax_id or "").strip().upper()
        if not tax_id:
            return None
        if not _TAX_ID_PATTERN.match(tax_id):
            tipos = ", ".join(f"{t}-" for t, _e in TAX_ID_TYPES)
            raise ValueError(
                f"La cédula/RIF debe tener el formato V-12345678 (tipos: {tipos})."
            )
        return tax_id

    def _next_sku(self) -> str:
        sequence = self._customers.count() + 1
        return f"{_SKU_PREFIX}-{sequence:0{_SKU_SEQUENCE_DIGITS}d}"


def _blank_to_none(value: str | None) -> str | None:
    """'' -> None. Igual que con tax_id: no guardar vacios como texto vacio."""
    value = (value or "").strip()
    return value or None
