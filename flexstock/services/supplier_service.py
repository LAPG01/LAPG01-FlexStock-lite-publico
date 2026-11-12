"""
Reglas de negocio de proveedores: genera el SKU y valida el nombre antes
de que llegue al repositorio. No abre conexiones ni escribe SQL: eso es
trabajo de data/.
"""

from ..data.repositories.supplier_repository import SupplierRepository
from ..domain.contact import clean_email, format_phone, name_similarity
from ..domain.entities import Supplier

# 'PROV-0001': prefijo fijo, no depende de ninguna categoria (igual que
# el SKU de clientes, a diferencia del de productos).
_SKU_PREFIX = "PROV"
_SKU_SEQUENCE_DIGITS = 4

# Desde que parecido (0 a 1, ver domain.contact.name_similarity) dos nombres
# se consideran "el mismo proveedor, quiza".
_UMBRAL_PARECIDO = 0.86


class SupplierService:
    def __init__(self, supplier_repo: SupplierRepository) -> None:
        self._suppliers = supplier_repo

    def list_suppliers(self) -> list[Supplier]:
        """Devuelve todos los proveedores activos."""
        return self._suppliers.list_all()

    def get_supplier(self, supplier_id: int) -> Supplier | None:
        return self._suppliers.get(supplier_id)

    def create_supplier(
        self,
        *,
        name: str,
        tax_id: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        contact_name: str | None = None,
    ) -> Supplier:
        """Genera el SKU y crea el proveedor. Solo 'name' es obligatorio."""
        name = self._validate_name(name)

        supplier = Supplier(
            id=None,
            sku=self._next_sku(),
            name=name,
            tax_id=_blank_to_none(tax_id),
            phone=format_phone(phone),
            email=clean_email(email),
            address=_blank_to_none(address),
            contact_name=_blank_to_none(contact_name),
        )
        self._suppliers.create(supplier)
        return supplier

    def update_supplier(
        self,
        supplier_id: int,
        *,
        name: str,
        tax_id: str | None = None,
        phone: str | None = None,
        email: str | None = None,
        address: str | None = None,
        contact_name: str | None = None,
        is_active: bool = True,
    ) -> Supplier:
        """
        El SKU no se toca: no es editable, se genero una sola vez al
        alta. is_active=True es tambien como se reactiva un proveedor
        desactivado (ver deactivate_supplier()).
        """
        name = self._validate_name(name)

        supplier = self._suppliers.get(supplier_id)
        if supplier is None:
            raise ValueError(f"No existe un proveedor con id={supplier_id}.")

        supplier.name = name
        supplier.tax_id = _blank_to_none(tax_id)
        supplier.phone = format_phone(phone)
        supplier.email = clean_email(email, actual=supplier.email)
        supplier.address = _blank_to_none(address)
        supplier.contact_name = _blank_to_none(contact_name)
        supplier.is_active = is_active

        self._suppliers.update(supplier)
        return supplier

    def update_contact(
        self, supplier_id: int, *, contact_name: str | None, phone: str | None
    ) -> Supplier:
        """
        Cambia SOLO el contacto y el telefono de la ficha general del
        proveedor. Existe porque update_supplier() pide todos los campos
        y deja en blanco los que no recibe: usarlo para corregir un
        telefono desde "Registrar proveedor" borraria el RIF, el email y
        la direccion sin que nadie lo note.

        Es la ficha GENERAL: vale para todos los productos de este
        proveedor, no solo para el que se esta registrando. Quien llama
        tiene que haberlo confirmado con el usuario.
        """
        supplier = self._suppliers.get(supplier_id)
        if supplier is None:
            raise ValueError(f"No existe un proveedor con id={supplier_id}.")

        supplier.contact_name = _blank_to_none(contact_name)
        supplier.phone = format_phone(phone)
        self._suppliers.update(supplier)
        return supplier

    def find_similar(
        self, name: str, *, exclude_id: int | None = None, limit: int = 5
    ) -> list[Supplier]:
        """
        Proveedores activos con un nombre parecido a 'name' ("MOK" vs "MOK
        DISTRIBUIDORA"), los mas parecidos primero. Para avisar antes de
        crear un duplicado: el catalogo viejo tenia ~700 variantes de
        nombre para ~100 proveedores reales. Una consulta (los activos) y
        la comparacion en memoria: ~150 filas.
        """
        puntajes = []
        for proveedor in self._suppliers.list_all():
            if proveedor.id == exclude_id:
                continue
            puntaje = name_similarity(name, proveedor.name)
            if puntaje >= _UMBRAL_PARECIDO:
                puntajes.append((puntaje, proveedor))
        puntajes.sort(key=lambda par: -par[0])
        return [proveedor for _p, proveedor in puntajes[:limit]]

    def deactivate_supplier(self, supplier_id: int) -> None:
        """'Elimina' sin borrar la fila: reactivar es un update_supplier normal."""
        supplier = self._suppliers.get(supplier_id)
        if supplier is None:
            raise ValueError(f"No existe un proveedor con id={supplier_id}.")

        supplier.is_active = False
        self._suppliers.update(supplier)

    @staticmethod
    def _validate_name(name: str) -> str:
        name = name.strip()
        if not name:
            raise ValueError("El nombre del proveedor no puede estar vacío.")
        return name

    def _next_sku(self) -> str:
        sequence = self._suppliers.count() + 1
        return f"{_SKU_PREFIX}-{sequence:0{_SKU_SEQUENCE_DIGITS}d}"


def _blank_to_none(value: str | None) -> str | None:
    """'' -> None. Dos proveedores sin cedula/telefono no deben chocar con nada."""
    value = (value or "").strip()
    return value or None
