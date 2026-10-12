"""Numéros de téléphone des comptes : un numéro n'appartient qu'à un seul compte.

Les colonnes PatientPhone / PatientPhone2 (et DoctorPhone / DoctorPhone2) sont uniques en base.
Ce module :
- enregistre un numéro vide comme NULL (deux chaînes vides entreraient en conflit) ;
- compare les numéros sur leurs chiffres (« +237 695 99 58 42 » = « 695995842 ») ;
- signale, avant d'écrire, le numéro déjà utilisé par un autre compte, sur l'une ou l'autre colonne.
"""
import re

from sqlalchemy import select

from Rohafya import db

PHONE_IN_USE = "Le numéro {phone} est déjà utilisé par un autre compte. Indiquez un autre numéro ou contactez l'établissement."
PHONES_IDENTICAL = "Le téléphone principal et le téléphone secondaire doivent être différents."
DUPLICATE_DATA = "Ces informations sont déjà utilisées par un autre compte (numéro de téléphone). Vérifiez-les puis réessayez."


def clean_phone(value):
    """Numéro saisi, sans espaces superflus ; None s'il est vide."""
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def phone_key(value):
    """Clé de comparaison : chiffres seuls, sans indicatif du Cameroun (237 / 00237)."""
    digits = re.sub(r"\D", "", value or "")
    for prefix in ("00237", "237"):
        if digits.startswith(prefix) and len(digits) > len(prefix) + 8:
            return digits[len(prefix):]
    return digits


def phone_error(model, columns, phones, exclude_id):
    """Message d'erreur si les numéros sont identiques entre eux ou déjà pris par un autre compte, sinon None.

    model : Patients ou Doctors ; columns : noms des deux colonnes de téléphone ;
    phones : numéros nettoyés (None ignorés) ; exclude_id : le compte mis à jour.
    """
    keys = {}
    for phone in phones:
        if phone is None:
            continue
        key = phone_key(phone)
        if not key:
            continue
        if key in keys:
            return PHONES_IDENTICAL
        keys[key] = phone
    if not keys:
        return None
    rows = db.session.execute(
        select(*(getattr(model, column) for column in columns)).filter(model.id != exclude_id)
    ).all()
    for row in rows:
        for other in row:
            if other and phone_key(other) in keys:
                return PHONE_IN_USE.format(phone=keys[phone_key(other)])
    return None
