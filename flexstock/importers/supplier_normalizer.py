"""
Normalizacion de nombres de proveedor.

PRODR.TXT trae ~700 variantes de escritura para 80-150 proveedores
reales, bajo el formato libre 'fecha-PROVEEDOR/CONTACTO' (ver
CLAUDE.md, seccion "Datos de origen"). Este modulo NO decide cual es
el nombre final de nadie: agrupa lo que puede agrupar sin ambiguedad
(paso A, deterministico) y sugiere posibles parecidos entre grupos
que quedaron separados (paso B, una pista, nunca una fusion
automatica). El resto lo resuelve una persona en un CSV.
"""

import csv
import re
from dataclasses import dataclass, field
from datetime import date
from difflib import get_close_matches
from pathlib import Path

_DATE_PREFIX = re.compile(r"^(\d{1,2}/\d{1,2}/\d{2,4})-?(.*)$")
_SPLIT_CHARS = re.compile(r"[/,-]")
_FUZZY_CUTOFF = 0.75

CSV_FIELDS = [
    "normalized_key",
    "occurrences",
    "sample_raw_lines",
    "suggested_contact",
    "similar_to",
    "canonical_name",
    "is_supplier",
]


@dataclass(slots=True)
class ParsedSupplierLine:
    raw: str
    last_purchase: date | None
    normalized_key: str  # "" si no se pudo aislar nada util: queda visible en el CSV
    candidate_contact: str


@dataclass(slots=True)
class SupplierGroup:
    normalized_key: str
    raw_lines: list[ParsedSupplierLine] = field(default_factory=list)
    similar_to: str = ""

    @property
    def occurrences(self) -> int:
        return len(self.raw_lines)

    @property
    def suggested_contact(self) -> str:
        """El contacto mas repetido dentro del grupo, o '' si ninguno."""
        contacts = [p.candidate_contact for p in self.raw_lines if p.candidate_contact]
        if not contacts:
            return ""
        return max(set(contacts), key=contacts.count)


@dataclass(slots=True)
class ReviewedSupplier:
    canonical_name: str
    is_supplier: bool
    suggested_contact: str


def parse_raw_line(raw: str) -> ParsedSupplierLine:
    """
    'DD/MM/AA-PROVEEDOR/CONTACTO' -> fecha + candidato a proveedor +
    candidato a contacto. No lanza nunca: una linea rara (sin fecha,
    sin separador reconocible, o puro ruido) devuelve normalized_key
    vacio y queda a la vista en el CSV en vez de romper la importacion.
    """
    match = _DATE_PREFIX.match(raw)
    if match:
        last_purchase = _parse_legacy_date(match.group(1))
        resto = match.group(2)
    else:
        last_purchase = None
        resto = raw

    parts = _SPLIT_CHARS.split(resto.strip(), maxsplit=1)
    # rstrip(".") va DESPUES de partir, no antes: si el separador
    # aparece despues de un punto (ej. "PEDRO SAN.-C/MT."), aplicarlo
    # a toda la cadena de una nunca llega a tocar ese punto y
    # "PEDRO SAN" / "PEDRO SAN." quedan como grupos distintos.
    candidate_supplier = parts[0].rstrip(".").strip()
    candidate_contact = parts[1].strip() if len(parts) > 1 else ""

    return ParsedSupplierLine(
        raw=raw,
        last_purchase=last_purchase,
        normalized_key=candidate_supplier.upper(),
        candidate_contact=candidate_contact,
    )


def _parse_legacy_date(text: str) -> date | None:
    day, month, year = (int(part) for part in text.split("/"))
    if year < 100:
        year += 2000  # el txt viejo siempre trunca el anio a 2 digitos
    try:
        return date(year, month, day)
    except ValueError:
        return None  # fecha invalida en el dato de origen: se ignora, no rompe nada


def group_lines(raw_lines: list[str]) -> list[SupplierGroup]:
    """
    Paso A: agrupa por normalized_key exacto (ver parse_raw_line).
    Paso B: para cada grupo, sugiere el grupo mas parecido por texto
    entre los que quedaron SEPARADOS (nunca se fusiona solo).
    """
    groups: dict[str, SupplierGroup] = {}
    for raw in raw_lines:
        parsed = parse_raw_line(raw)
        groups.setdefault(
            parsed.normalized_key, SupplierGroup(parsed.normalized_key)
        ).raw_lines.append(parsed)

    ordered = sorted(groups.values(), key=lambda g: (-g.occurrences, g.normalized_key))
    keys = [g.normalized_key for g in ordered if g.normalized_key]

    for group in ordered:
        if not group.normalized_key:
            continue
        others = [k for k in keys if k != group.normalized_key]
        match = get_close_matches(
            group.normalized_key, others, n=1, cutoff=_FUZZY_CUTOFF
        )
        group.similar_to = match[0] if match else ""

    return ordered


def write_review_csv(groups: list[SupplierGroup], path: Path) -> None:
    """
    Una fila por grupo (no por linea cruda): son ~150-300 filas
    revisables, no las 2.093 lineas originales. 'canonical_name' e
    'is_supplier' quedan vacias a proposito: las llena una persona.
    """
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for group in groups:
            writer.writerow(
                {
                    "normalized_key": group.normalized_key,
                    "occurrences": group.occurrences,
                    "sample_raw_lines": " | ".join(p.raw for p in group.raw_lines[:3]),
                    "suggested_contact": group.suggested_contact,
                    "similar_to": group.similar_to,
                    "canonical_name": "",
                    "is_supplier": "",
                }
            )


def load_reviewed(path: Path) -> dict[str, ReviewedSupplier]:
    """
    Lee el CSV ya revisado a mano. La clave es 'normalized_key': quien
    llama (legacy_importer.py) recalcula esa misma clave con
    parse_raw_line() para cada linea cruda y busca aca. Las filas sin
    'canonical_name' se ignoran: todavia no fueron revisadas.
    """
    reviewed: dict[str, ReviewedSupplier] = {}
    with path.open(encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f):
            canonical_name = row["canonical_name"].strip()
            if not canonical_name:
                continue
            reviewed[row["normalized_key"]] = ReviewedSupplier(
                canonical_name=canonical_name,
                is_supplier=row["is_supplier"].strip().upper() != "NO",
                suggested_contact=row["suggested_contact"].strip(),
            )
    return reviewed
