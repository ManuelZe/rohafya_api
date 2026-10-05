"""Enregistrement des données envoyées par les établissements (API ROHAFYA et FHIR).

Le format « ROHAFYA » est exactement celui des données de la démo du front-end
(src/app/demo/data/patient.json) : un examen de laboratoire a les champs d'ExamensLab,
une imagerie ceux d'ExamenImagerie, etc., plus `local_ref` (le dossier du patient dans
l'établissement) et, en option, `details` (valeurs mesurées) ou `products` (lignes de facture).
"""
from sqlalchemy import select

from Rohafya import db
from .constants import CODE_FIELD, KIND_INVOICE, LINK_ACTIVE, RECORD_KINDS
from .models import PatientLink, TenantPatient, TenantRecord
from .services import SaasError, normalize_email, notify_user, now, parse_datetime

PATIENT_FIELDS = ("first_name", "last_name", "email", "phone", "birth_date", "gender")
KIND_LABELS = {"laboratoire": "de laboratoire", "imagerie": "d'imagerie", "exploration": "d'exploration fonctionnelle"}


def _text(value, limit=200):
    if value is None:
        return None
    return str(value).strip()[:limit] or None


def upsert_patient(tenant, data, source):
    if not isinstance(data, dict):
        raise SaasError("Chaque patient doit être un objet JSON.", 400)
    local_ref = _text(data.get("local_ref"), 120)
    if not local_ref:
        raise SaasError("Le champ local_ref (dossier du patient) est obligatoire.", 400)

    patient = db.session.execute(select(TenantPatient).filter_by(tenant_id=tenant.id, local_ref=local_ref)).scalar_one_or_none()
    created = patient is None
    if created:
        patient = TenantPatient(tenant_id=tenant.id, local_ref=local_ref)
        db.session.add(patient)
    for field in PATIENT_FIELDS:
        if field in data:
            value = normalize_email(data[field]) if field == "email" else _text(data[field], 200)
            setattr(patient, field, value or None)
    patient.source = source
    patient.updated_at = now()
    return patient, created


def record_code(kind, data):
    field = CODE_FIELD[kind]
    code = data.get(field)
    if kind == KIND_INVOICE and not code:
        code = data.get("invoice_number")
    return _text(code, 120)


def upsert_record(tenant, kind, data, source):
    if kind not in RECORD_KINDS:
        raise SaasError(f"Type inconnu : {kind}. Types acceptés : {', '.join(RECORD_KINDS)}.", 400)
    if not isinstance(data, dict):
        raise SaasError("Chaque enregistrement doit être un objet JSON.", 400)

    local_ref = _text(data.get("local_ref"), 120)
    if not local_ref:
        raise SaasError("Le champ local_ref (dossier du patient) est obligatoire.", 400)
    code = record_code(kind, data)
    if not code:
        field = "reference (ou invoice_number)" if kind == KIND_INVOICE else CODE_FIELD[kind]
        raise SaasError(f"Le champ {field} est obligatoire pour le type {kind}.", 400)

    payload = {key: value for key, value in data.items() if key not in ("local_ref", "details", "products")}
    payload[CODE_FIELD[kind]] = code
    if kind == KIND_INVOICE:
        payload.setdefault("invoice_number", code)
        details = data.get("products") or data.get("details") or []
        validated_at = parse_datetime(payload.get("date"))
    else:
        details = data.get("details") or []
        validated_at = parse_datetime(payload.get("validation_date"))
    if not isinstance(details, list):
        raise SaasError("Les champs details / products doivent être des listes.", 400)

    record = db.session.execute(select(TenantRecord).filter_by(tenant_id=tenant.id, kind=kind, code=code)).scalar_one_or_none()
    created = record is None
    if created:
        record = TenantRecord(tenant_id=tenant.id, kind=kind, code=code)
        db.session.add(record)
    record.local_ref = local_ref
    record.payload = payload
    record.details = details
    record.validated_at = validated_at
    record.source = source
    record.updated_at = now()
    return record, created


def delete_record(tenant, kind, code):
    record = db.session.execute(select(TenantRecord).filter_by(tenant_id=tenant.id, kind=kind, code=code)).scalar_one_or_none()
    if not record:
        raise SaasError("Enregistrement introuvable.", 404)
    db.session.delete(record)


def notify_new_results(tenant, records):
    """Prévient chaque patient rattaché qu'un nouveau résultat est disponible."""
    for record in records:
        if record.kind == KIND_INVOICE:
            continue
        link = db.session.execute(
            select(PatientLink).filter_by(tenant_id=tenant.id, local_ref=record.local_ref, status=LINK_ACTIVE)
        ).scalar_one_or_none()
        if link and link.patient:
            notify_user(
                link.patient.user_id,
                "Nouveau résultat disponible",
                f"{tenant.display_name} a publié un résultat {KIND_LABELS.get(record.kind, '')} ({record.code}).",
            )


def ingest_batch(tenant, kind, items, source):
    """Enregistre une liste d'objets. Tout ou rien : une erreur annule le lot entier."""
    if isinstance(items, dict):
        items = items.get("patients") or items.get("records") or items.get("items") or [items]
    if not isinstance(items, list) or not items:
        raise SaasError("Le corps de la requête doit contenir une liste non vide.", 400)
    if len(items) > 500:
        raise SaasError("500 éléments au maximum par envoi.", 413)

    created = updated = 0
    new_records = []
    try:
        for index, item in enumerate(items):
            try:
                if kind == "patients":
                    _, is_new = upsert_patient(tenant, item, source)
                else:
                    record, is_new = upsert_record(tenant, kind, item, source)
                    if is_new:
                        new_records.append(record)
            except SaasError as err:
                raise SaasError(f"Élément n° {index + 1} : {err.message}", err.status)
            created += 1 if is_new else 0
            updated += 0 if is_new else 1
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    notify_new_results(tenant, new_records)
    return {"created": created, "updated": updated, "total": created + updated}
