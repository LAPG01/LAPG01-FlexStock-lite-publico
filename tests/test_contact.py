"""domain/contact.py: correo, telefono y parecido entre nombres."""

import pytest

from flexstock.domain.contact import clean_email, format_phone, name_similarity


@pytest.mark.parametrize("valor", ["a@b.com", "  ventas@limar.com.ve "])
def test_clean_email_acepta_correos_normales(valor):
    assert clean_email(valor) == valor.strip()


@pytest.mark.parametrize("valor", ["limar", "a@b", "a b@c.com", "@x.com", "a@.com "])
def test_clean_email_rechaza_lo_que_no_parece_correo(valor):
    with pytest.raises(ValueError, match="correo"):
        clean_email(valor)


def test_clean_email_vacio_es_none():
    assert clean_email("") is None
    assert clean_email(None) is None
    assert clean_email("   ") is None


def test_clean_email_conserva_un_valor_viejo_que_no_se_toco():
    assert clean_email("raro", actual="raro") == "raro"
    with pytest.raises(ValueError):
        clean_email("otro-raro", actual="raro")


@pytest.mark.parametrize(
    ("escrito", "esperado"),
    [
        ("04121234567", "0412-1234567"),
        ("0412 123 4567", "0412-1234567"),
        ("(0212) 123-4567", "0212-1234567"),
        ("0412-1234567", "0412-1234567"),
        ("+58 412 1234567", "+58 412 1234567"),  # internacional: se deja
        ("555-1234", "555-1234"),
        ("", None),
        (None, None),
    ],
)
def test_format_phone(escrito, esperado):
    assert format_phone(escrito) == esperado


def test_name_similarity_ignora_mayusculas_tildes_y_puntuacion():
    assert name_similarity("Distribuidora Mök, C.A.", "DISTRIBUIDORA MOK CA") == 1.0


def test_name_similarity_un_nombre_contenido_en_el_otro():
    assert name_similarity("MOK", "MOK DISTRIBUIDORA") >= 0.9


def test_name_similarity_nombres_distintos_puntuan_bajo():
    assert name_similarity("SURTEC", "FERRETERIA LIMAR") < 0.5
    assert name_similarity("", "MOK") == 0.0
