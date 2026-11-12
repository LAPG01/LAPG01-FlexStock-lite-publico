"""
Excepciones del negocio.

Existen para que services/ pueda decir "esto no se puede hacer y esta es
la razon" sin saber nada de Tkinter, y para que ui/ pueda mostrar un
mensaje distinto por cada caso sin leer strings.
"""


class DomainError(Exception):
    """Raiz de todos los errores de negocio. ui/ captura esta."""


class InsufficientStock(DomainError):
    def __init__(self, product_name: str, available: int, requested: int):
        self.product_name = product_name
        self.available = available
        self.requested = requested
        super().__init__(
            f"Stock insuficiente de {product_name}: "
            f"hay {available / 1000:g}, se piden {requested / 1000:g}"
        )


class InvoiceAlreadyCancelled(DomainError):
    pass


class EmptyDocument(DomainError):
    """Una factura sin lineas no es una factura."""


class ReasonRequired(DomainError):
    """Un ajuste sin motivo no deja rastro util."""


class DuplicateCode(DomainError):
    pass


class MissingCustomerData(DomainError):
    """
    Una factura fiscal lleva nombre, cedula/RIF y direccion del
    comprador. Las notas de entrega (cotizaciones) NO: ahi el cliente
    puede ser solo un nombre, para vender rapido.
    """

    def __init__(self, missing: list[str]):
        self.missing = missing
        super().__init__(
            f"Falta {' y '.join(missing)} del cliente: una factura los necesita."
        )


class ExcessiveReturn(DomainError):
    """No se puede devolver mas de lo que se facturo."""
