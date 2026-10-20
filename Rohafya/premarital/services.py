"""Règles du bilan prénuptial : qui voit quoi, qui modifie quoi, et l'historique de chaque modification."""
import datetime
import re

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from Rohafya import db
from Rohafya.accounts.models import Doctors, Patients
from Rohafya.saas.services import SaasError, audit, notify_user, now
from .catalogue import DEFAULT_EXAMS, SEX_ALL, SEX_FEMALE, SEX_MALE, SEXES
from .models import PremaritalAuthorization, PremaritalExam, PremaritalExamHistory, PremaritalExamType

FLAGS = ("normal", "abnormal", "to_check")
CODE_PATTERN = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")
# Au plus une notification « bilan mis à jour » par médecin et par patient sur cette durée.
NOTIFY_EVERY = datetime.timedelta(hours=2)
TEXT_LIMITS = {
    "value": 255, "interpretation": 1000, "label_fr": 200, "label_en": 200, "purpose_fr": 300, "purpose_en": 300,
    "reference_fr": 500, "reference_en": 500, "unit": 40,
}


# =====================================================================
# Catalogue
# =====================================================================

def ensure_catalogue():
    """Insère le catalogue initial si la table est vide (premier démarrage)."""
    if db.session.execute(select(PremaritalExamType.id).limit(1)).first():
        return
    for index, exam in enumerate(DEFAULT_EXAMS):
        db.session.add(PremaritalExamType(sort_order=(index + 1) * 10, **exam))
    try:
        db.session.commit()
    except IntegrityError:
        # Un autre worker l'a inséré au même instant.
        db.session.rollback()


def _text(data, field, required=False):
    value = data.get(field)
    value = value.strip() if isinstance(value, str) else ("" if value is None else str(value).strip())
    if required and not value:
        raise SaasError("Ce champ est obligatoire.", 400, field=field)
    if len(value) > TEXT_LIMITS[field]:
        raise SaasError(f"Texte trop long ({TEXT_LIMITS[field]} caractères au plus).", 400, field=field)
    return value or None


def type_view(exam_type, with_usage=False):
    view = {
        "id": exam_type.id,
        "code": exam_type.code,
        "sex": exam_type.sex,
        "label": {"fr": exam_type.label_fr, "en": exam_type.label_en or exam_type.label_fr},
        "purpose": {"fr": exam_type.purpose_fr, "en": exam_type.purpose_en or exam_type.purpose_fr},
        "reference": {"fr": exam_type.reference_fr, "en": exam_type.reference_en or exam_type.reference_fr},
        "unit": exam_type.unit or "",
        "sort_order": exam_type.sort_order,
        "is_active": exam_type.is_active,
    }
    if with_usage:
        view["usage"] = db.session.execute(
            select(func.count(PremaritalExam.id)).filter_by(exam_type_id=exam_type.id)
        ).scalar_one()
        view["label_fr"], view["label_en"] = exam_type.label_fr, exam_type.label_en or ""
        view["purpose_fr"], view["purpose_en"] = exam_type.purpose_fr or "", exam_type.purpose_en or ""
        view["reference_fr"], view["reference_en"] = exam_type.reference_fr or "", exam_type.reference_en or ""
    return view


def catalogue_admin():
    rows = db.session.execute(select(PremaritalExamType).order_by(PremaritalExamType.sort_order, PremaritalExamType.id))
    return [type_view(row, with_usage=True) for row in rows.scalars()]


def _apply_type_fields(exam_type, data, creating):
    if creating or "label_fr" in data:
        exam_type.label_fr = _text(data, "label_fr", required=True)
    for field in ("label_en", "purpose_fr", "purpose_en", "reference_fr", "reference_en", "unit"):
        if creating or field in data:
            setattr(exam_type, field, _text(data, field))
    if creating or "sex" in data:
        sex = data.get("sex") or SEX_ALL
        if sex not in SEXES:
            raise SaasError("Sexe invalide : all, F ou M.", 400, field="sex")
        exam_type.sex = sex
    if "sort_order" in data and data["sort_order"] not in (None, ""):
        try:
            exam_type.sort_order = int(data["sort_order"])
        except (TypeError, ValueError):
            raise SaasError("L'ordre d'affichage doit être un nombre entier.", 400, field="sort_order")
    if "is_active" in data:
        exam_type.is_active = bool(data["is_active"])


def create_type(data, actor):
    code = (data.get("code") or "").strip().upper()
    if not CODE_PATTERN.match(code):
        raise SaasError("Code invalide : lettres majuscules, chiffres et _, en commençant par une lettre (2 à 40 caractères).", 400, field="code")
    if db.session.execute(select(PremaritalExamType.id).filter_by(code=code)).first():
        raise SaasError("Ce code existe déjà dans le catalogue.", 409, field="code")
    exam_type = PremaritalExamType(code=code)
    _apply_type_fields(exam_type, data, creating=True)
    if "sort_order" not in data or data.get("sort_order") in (None, ""):
        last = db.session.execute(select(func.max(PremaritalExamType.sort_order))).scalar_one()
        exam_type.sort_order = (last or 0) + 10
    db.session.add(exam_type)
    audit("premarital.catalogue.created", user_id=actor.id, target=code, commit=False)
    db.session.commit()
    return exam_type


def update_type(type_id, data, actor):
    exam_type = db.session.get(PremaritalExamType, type_id)
    if exam_type is None:
        raise SaasError("Examen introuvable dans le catalogue.", 404)
    _apply_type_fields(exam_type, data, creating=False)
    exam_type.updated_at = now()
    audit("premarital.catalogue.updated", user_id=actor.id, target=exam_type.code, commit=False)
    db.session.commit()
    return exam_type


# =====================================================================
# Personnes
# =====================================================================

def doctor_name(doctor):
    if doctor is None:
        return None
    name = f"{doctor.DoctorName or ''} {doctor.DoctorLastname or ''}".strip()
    return name or doctor.DoctorEmail


def _patient_name(patient):
    return f"{patient.PatientName or ''} {patient.PatientLastname or ''}".strip() or patient.PatientEmail


def patient_view(patient):
    gender = (patient.PatientGender or "").strip().upper()
    return {
        "id": patient.id,
        "name": _patient_name(patient),
        "federation_id": patient.PatientFederationID or "",
        "gender": gender if gender in (SEX_FEMALE, SEX_MALE) else None,
    }


def doctor_view(doctor):
    return {
        "id": doctor.id,
        "name": doctor_name(doctor),
        "matricule": doctor.DoctorFederationID or "",
        "speciality": doctor.Speciality or "",
    }


# =====================================================================
# Autorisations données par le patient
# =====================================================================

def active_authorization(patient_id, doctor_id):
    return db.session.execute(
        select(PremaritalAuthorization).filter_by(patient_id=patient_id, doctor_id=doctor_id, revoked_at=None)
    ).scalars().first()


def authorization_view(auth):
    return {"id": auth.id, "doctor": doctor_view(auth.doctor), "created_at": auth.created_at.isoformat() if auth.created_at else None}


def patient_authorizations(patient):
    rows = db.session.execute(
        select(PremaritalAuthorization).filter_by(patient_id=patient.id, revoked_at=None).order_by(PremaritalAuthorization.created_at.desc())
    ).scalars()
    return [authorization_view(row) for row in rows]


def authorize_doctor(patient, matricule, actor):
    matricule = (matricule or "").strip()
    if not matricule:
        raise SaasError("Saisissez le matricule du médecin.", 400, field="matricule")
    doctor = db.session.execute(
        select(Doctors).where(func.upper(Doctors.DoctorFederationID) == matricule.upper())
    ).scalars().first()
    if doctor is None or doctor.user is None or not doctor.user.active:
        raise SaasError("Aucun médecin ne correspond à ce matricule.", 404, field="matricule")
    if not doctor.doctor_is_confirmed:
        raise SaasError("Ce médecin n'a pas encore confirmé son profil : il ne peut pas encore être autorisé.", 400, field="matricule")
    if active_authorization(patient.id, doctor.id):
        raise SaasError("Ce médecin est déjà autorisé.", 409, field="matricule")
    auth = PremaritalAuthorization(patient_id=patient.id, doctor_id=doctor.id)
    db.session.add(auth)
    audit("premarital.doctor_authorized", user_id=actor.id, target=f"médecin {doctor.id}", commit=False)
    db.session.commit()
    notify_user(doctor.user_id, "premarital.authorized.title", "premarital.authorized.message", patient=_patient_name(patient))
    return auth


def revoke_authorization(patient, auth_id, actor):
    auth = db.session.get(PremaritalAuthorization, auth_id)
    if auth is None or auth.patient_id != patient.id or auth.revoked_at is not None:
        raise SaasError("Autorisation introuvable.", 404)
    auth.revoked_at = now()
    audit("premarital.doctor_revoked", user_id=actor.id, target=f"médecin {auth.doctor_id}", commit=False)
    db.session.commit()
    if auth.doctor is not None:
        notify_user(auth.doctor.user_id, "premarital.revoked.title", "premarital.revoked.message", patient=_patient_name(patient))
    return auth


def require_doctor_access(doctor, patient_id):
    """Le médecin doit avoir un profil confirmé et l'autorisation active du patient."""
    if not doctor.doctor_is_confirmed:
        raise SaasError("Confirmez d'abord votre profil de médecin dans Paramètres.", 403)
    patient = db.session.get(Patients, patient_id)
    if patient is None or active_authorization(patient_id, doctor.id) is None:
        raise SaasError("Ce patient ne vous a pas autorisé à accéder à son bilan prénuptial.", 403)
    return patient


def doctor_patients(doctor):
    """Patients qui ont autorisé le médecin, avec l'avancement de leur bilan."""
    rows = db.session.execute(
        select(PremaritalAuthorization).filter_by(doctor_id=doctor.id, revoked_at=None).order_by(PremaritalAuthorization.created_at.desc())
    ).scalars()
    items = []
    for auth in rows:
        patient = auth.patient
        if patient is None:
            continue
        exams = _patient_rows(patient.id)
        filled = [row for row in exams.values() if row.value or row.interpretation or row.exam_date]
        last = max((row.updated_at for row in exams.values()), default=None)
        items.append({
            "patient": patient_view(patient),
            "authorized_at": auth.created_at.isoformat() if auth.created_at else None,
            "filled": len(filled),
            "expected": len(_types_for(patient)),
            "last_update": last.isoformat() if last else None,
        })
    return items


# =====================================================================
# Bilan d'un patient
# =====================================================================

def _types_for(patient):
    """Examens actifs à présenter selon le sexe du patient (tous si le sexe n'est pas renseigné)."""
    gender = patient_view(patient)["gender"]
    sexes = [SEX_ALL, gender] if gender else list(SEXES)
    return db.session.execute(
        select(PremaritalExamType)
        .where(PremaritalExamType.is_active.is_(True), PremaritalExamType.sex.in_(sexes))
        .order_by(PremaritalExamType.sort_order, PremaritalExamType.id)
    ).scalars().all()


def _patient_rows(patient_id):
    rows = db.session.execute(select(PremaritalExam).filter_by(patient_id=patient_id)).scalars()
    return {row.exam_type_id: row for row in rows}


def _state(row):
    if row is None:
        return None
    return {
        "value": row.value,
        "flag": row.flag,
        "interpretation": row.interpretation,
        "exam_date": row.exam_date.isoformat() if row.exam_date else None,
    }


def exam_view(exam_type, row):
    view = type_view(exam_type)
    view.update(_state(row) or {"value": None, "flag": None, "interpretation": None, "exam_date": None})
    view["updated_at"] = row.updated_at.isoformat() if row and row.updated_at else None
    view["updated_by"] = doctor_view(row.updated_by) if row and row.updated_by else None
    return view


def bilan(patient):
    """Examens du patient : ceux du catalogue actif, plus les examens retirés du catalogue déjà renseignés."""
    rows = _patient_rows(patient.id)
    types = _types_for(patient)
    shown = {exam_type.id for exam_type in types}
    retired = [row.exam_type for row in rows.values() if row.exam_type_id not in shown and row.exam_type is not None]
    retired.sort(key=lambda t: (t.sort_order, t.id))
    return {
        "patient": patient_view(patient),
        "exams": [exam_view(exam_type, rows.get(exam_type.id)) for exam_type in [*types, *retired]],
    }


def _parse_date(value):
    if value in (None, ""):
        return None
    try:
        parsed = datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise SaasError("Date de l'examen invalide (format AAAA-MM-JJ).", 400, field="exam_date")
    if parsed > now().date():
        raise SaasError("La date de l'examen ne peut pas être dans le futur.", 400, field="exam_date")
    return parsed


def update_exam(doctor, patient, code, data, actor):
    """Seul un médecin autorisé modifie un examen ; chaque modification est inscrite à l'historique."""
    exam_type = db.session.execute(select(PremaritalExamType).filter_by(code=(code or "").upper())).scalar_one_or_none()
    if exam_type is None:
        raise SaasError("Examen introuvable dans le catalogue.", 404)
    if not exam_type.is_active:
        raise SaasError("Cet examen a été retiré du catalogue : il ne peut plus être modifié.", 400)
    gender = patient_view(patient)["gender"]
    if gender and exam_type.sex not in (SEX_ALL, gender):
        raise SaasError("Cet examen ne concerne pas le sexe de ce patient.", 400)

    flag = data.get("flag") or None
    if flag is not None and flag not in FLAGS:
        raise SaasError("Appréciation invalide.", 400, field="flag")
    new_state = {
        "value": _text(data, "value"),
        "flag": flag,
        "interpretation": _text(data, "interpretation"),
        "exam_date": _parse_date(data.get("exam_date")),
    }

    row = db.session.execute(select(PremaritalExam).filter_by(patient_id=patient.id, exam_type_id=exam_type.id)).scalar_one_or_none()
    before = _state(row)
    after = {**new_state, "exam_date": new_state["exam_date"].isoformat() if new_state["exam_date"] else None}
    if before == after:
        return exam_type, row  # rien n'a changé : ni historique ni nouvelle date de mise à jour
    empty = not any(after.values())
    if row is None and empty:
        return exam_type, None
    action = "create" if row is None else ("clear" if empty else "update")
    if row is None:
        row = PremaritalExam(patient_id=patient.id, exam_type_id=exam_type.id)
        db.session.add(row)
    row.value, row.flag, row.interpretation, row.exam_date = (
        new_state["value"], new_state["flag"], new_state["interpretation"], new_state["exam_date"],
    )
    row.updated_at = now()
    row.updated_by_doctor_id = doctor.id
    db.session.add(PremaritalExamHistory(
        patient_id=patient.id, exam_type_id=exam_type.id, doctor_id=doctor.id, action=action, before=before, after=after, changed_at=row.updated_at,
    ))
    audit(f"premarital.exam.{action}", user_id=actor.id, target=f"patient {patient.id} · {exam_type.code}", commit=False)

    auth = active_authorization(patient.id, doctor.id)
    notify = auth is not None and (auth.last_notified_at is None or row.updated_at - auth.last_notified_at >= NOTIFY_EVERY)
    if notify:
        auth.last_notified_at = row.updated_at
    db.session.commit()
    if notify:
        notify_user(patient.user_id, "premarital.updated.title", "premarital.updated.message", doctor=doctor_name(doctor))
    db.session.refresh(row)
    return exam_type, row


def history(patient_id, limit=300):
    rows = db.session.execute(
        select(PremaritalExamHistory).filter_by(patient_id=patient_id)
        .order_by(PremaritalExamHistory.changed_at.desc(), PremaritalExamHistory.id.desc()).limit(limit)
    ).scalars()
    return [{
        "id": row.id,
        "code": row.exam_type.code if row.exam_type else None,
        "label": {"fr": row.exam_type.label_fr, "en": row.exam_type.label_en or row.exam_type.label_fr} if row.exam_type else None,
        "unit": (row.exam_type.unit or "") if row.exam_type else "",
        "action": row.action,
        "before": row.before,
        "after": row.after,
        "changed_at": row.changed_at.isoformat() if row.changed_at else None,
        "doctor": doctor_view(row.doctor) if row.doctor else None,
    } for row in rows]


# =====================================================================
# Données personnelles (export et suppression du compte)
# =====================================================================

def export_for_patient(patient):
    data = bilan(patient)
    return {"exams": [exam for exam in data["exams"] if exam["updated_at"]], "authorized_doctors": patient_authorizations(patient), "history": history(patient.id, limit=1000)}


def purge_patient(patient):
    """Suppression du compte : le bilan est conservé par ROHAFYA seul, il est donc effacé."""
    for model in (PremaritalExamHistory, PremaritalExam, PremaritalAuthorization):
        for row in db.session.execute(select(model).filter_by(patient_id=patient.id)).scalars():
            db.session.delete(row)


def revoke_for_doctor(doctor):
    """Suppression d'un compte médecin : ses autorisations prennent fin (l'historique garde son identifiant)."""
    for auth in db.session.execute(select(PremaritalAuthorization).filter_by(doctor_id=doctor.id, revoked_at=None)).scalars():
        auth.revoked_at = now()
