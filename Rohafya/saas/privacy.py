"""Données personnelles (loi n° 2024/017 du 23 décembre 2024) : historique des accès, export, suppression.

- Historique des accès : ce que les établissements et les médecins ont fait du dossier du patient
  (consultation du dossier, consultation d'un résultat partagé, rattachements, QR codes).
- Export : toutes les données du compte en JSON (droit d'accès et de portabilité).
- Suppression : demandée par l'utilisateur, traitée par un super-administrateur. Le compte est
  anonymisé et désactivé, ses liens et partages supprimés. Les résultats et factures restent chez
  l'établissement qui les a produits (il en est responsable et doit les conserver).
"""
import secrets

from sqlalchemy import or_, select

from Rohafya import db
from Rohafya.accounts.models import Doctors, Notifications, Patients, Send_Results, User
from . import i18n
from .constants import LINK_REVOKED
from .models import (
    AccountRights,
    AuditLog,
    DeletionRequest,
    EmailOtp,
    PatientLink,
    PractitionerLink,
    Submission,
    TenantMember,
    TenantRecord,
    UserMfa,
    UserPreference,
)
from .services import (
    SaasError,
    audit,
    doctor_of_user,
    notify_user,
    now,
    patient_link_views,
    patient_of_user,
    external_sources,
    send_mail,
)

# Actions du journal montrées au patient, et qui les a faites.
ACCESS_ACTIONS = {
    "patient.viewed": "establishment",
    "patient.saved": "establishment",
    "result.viewed": "doctor",
    "link_token.issued": "establishment",
    "link_token.revoked": "establishment",
    "link.active": "patient",
    "link.pending": "patient",
    "link.revoked": "establishment",
    "link.revoked_by_patient": "patient",
    "link.conflict": "other",
}
OWN_ACTIONS = ("account.exported", "account.deletion_requested", "account.deletion_cancelled")

DELETION_REQUESTED = "demandee"
DELETION_DONE = "traitee"
DELETION_REFUSED = "refusee"
DELETION_CANCELLED = "annulee"


# =====================================================================
# Historique des accès
# =====================================================================

def access_log(user, limit=200):
    patient = patient_of_user(user.id)
    refs = {}
    if patient is not None:
        for view in patient_link_views(patient):
            if view.get("tenant_id") and view.get("local_ref"):
                refs.setdefault(view["tenant_id"], set()).add(view["local_ref"])
    filters = [(AuditLog.user_id == user.id) & AuditLog.action.in_(OWN_ACTIONS)]
    if refs:
        all_refs = {ref for values in refs.values() for ref in values}
        filters.append(
            AuditLog.tenant_id.in_(list(refs)) & AuditLog.action.in_(list(ACCESS_ACTIONS))
            & (AuditLog.target.in_(list(all_refs)) | (AuditLog.action == "result.viewed"))
        )
    rows = db.session.execute(select(AuditLog).filter(or_(*filters)).order_by(AuditLog.id.desc()).limit(limit * 3)).scalars().all()
    items = []
    for entry in rows:
        if entry.action in ACCESS_ACTIONS:
            local_ref = (entry.details or {}).get("local_ref") if entry.action == "result.viewed" else entry.target
            if local_ref not in refs.get(entry.tenant_id, set()):
                continue
        actor = entry.user
        role = ACCESS_ACTIONS.get(entry.action, "patient")
        if actor is not None and actor.id == user.id:
            role = "patient"
        items.append({
            "id": entry.id,
            "action": entry.action,
            "created_at": entry.created_at,
            "establishment": entry.tenant.display_name if entry.tenant else None,
            "actor_role": role,
            # Le médecin est nommé (le patient a choisi de lui partager) ; le personnel de l'établissement ne l'est pas.
            "actor_name": f"{actor.first_name or ''} {actor.last_name or ''}".strip() if actor is not None and role in ("doctor", "patient") else None,
            "target": entry.target if entry.action == "result.viewed" else None,
        })
        if len(items) >= limit:
            break
    return items


# =====================================================================
# Export
# =====================================================================

def _row(model):
    data = {}
    for column in model.__table__.columns:
        value = getattr(model, column.key, None)
        if isinstance(value, (bytes, bytearray)):
            continue
        data[column.key] = value.isoformat() if hasattr(value, "isoformat") else value
    data.pop("password", None)
    return data


def export_data(user):
    patient = patient_of_user(user.id)
    doctor = doctor_of_user(user.id)
    pref = db.session.execute(select(UserPreference).filter_by(user_id=user.id)).scalar_one_or_none()
    data = {
        "export": {"generated_at": now().isoformat(), "law": "Loi n° 2024/017 du 23 décembre 2024 (Cameroun)"},
        "account": {
            "id": user.id,
            "email": user.email,
            "username": user.username,
            "first_name": user.first_name,
            "last_name": user.last_name,
            "roles": [role.name for role in user.roles],
            "email_confirmed_at": user.email_confirmed_at.isoformat() if user.email_confirmed_at else None,
            "locale": pref.locale if pref else "fr",
            "two_step_verification": db.session.execute(select(UserMfa.id).filter_by(user_id=user.id)).first() is not None,
        },
        "patient_profile": _row(patient) if patient else None,
        "doctor_profile": _row(doctor) if doctor else None,
        "establishments": patient_link_views(patient) if patient else [],
        "results": [],
        "shared_results": [],
        "requests": [],
        "notifications": [],
        "access_log": access_log(user, limit=500),
    }
    if patient is not None:
        for tenant, local_ref in external_sources(patient):
            records = db.session.execute(select(TenantRecord).filter_by(tenant_id=tenant.id, local_ref=local_ref)).scalars()
            for record in records:
                summary = record.to_summary()
                data["results"].append({
                    "kind": record.kind, "code": record.code, "title": summary["title"], "establishment": tenant.display_name,
                    "validated_at": record.validated_at.isoformat() if record.validated_at else None,
                })
        shares = db.session.execute(select(Send_Results).filter_by(patient_id=patient.id)).scalars().all()
        data["shared_results"] = [_row(share) for share in shares]
    for submission in db.session.execute(select(Submission).filter_by(author_id=user.id)).scalars():
        data["requests"].append({
            "kind": submission.kind, "status": submission.status, "establishment": submission.tenant.display_name if submission.tenant else None,
            "created_at": submission.created_at.isoformat() if submission.created_at else None, "response": submission.response,
        })
    for note in db.session.execute(select(Notifications).filter_by(user_id=user.id).order_by(Notifications.id.desc()).limit(500)).scalars():
        data["notifications"].append({"title": note.title, "message": note.message, "created_at": note.created_at.isoformat() if note.created_at else None})
    audit("account.exported", user_id=user.id, target=user.email)
    return data


# =====================================================================
# Demandes de suppression
# =====================================================================

def open_request(user_id):
    return db.session.execute(
        select(DeletionRequest).filter_by(user_id=user_id, status=DELETION_REQUESTED)
    ).scalar_one_or_none()


def request_deletion(user, reason=None):
    if open_request(user.id):
        raise SaasError("Une demande de suppression est déjà en cours pour ce compte.", 409)
    reason = (str(reason or "").strip()[:2000]) or None
    name = f"{user.first_name or ''} {user.last_name or ''}".strip()
    request = DeletionRequest(user_id=user.id, email=user.email, name=name, reason=reason, status=DELETION_REQUESTED)
    db.session.add(request)
    audit("account.deletion_requested", user_id=user.id, target=user.email, commit=False)
    db.session.commit()
    from .billing import super_admins

    for admin in super_admins():
        notify_user(admin.id, "deletion.title", "deletion.message", name=name or user.email, email=user.email)
    if user.email:
        send_mail(user.email, "mail.deletion_received", "emails/suppression_compte.html", locale=i18n.user_locale(user.id),
                  name=name, email=user.email)
    return request


def cancel_deletion(user):
    request = open_request(user.id)
    if request is None:
        raise SaasError("Aucune demande de suppression en cours.", 404)
    request.status = DELETION_CANCELLED
    request.processed_at = now()
    audit("account.deletion_cancelled", user_id=user.id, target=user.email, commit=False)
    db.session.commit()
    return request


def anonymize_account(user, actor_id):
    """Désactive et anonymise un compte. Les données médicales restent chez les établissements."""
    uid = user.id
    placeholder = f"supprime-{uid}@compte-supprime.invalid"
    patient = patient_of_user(uid)
    doctor = doctor_of_user(uid)
    if patient is not None:
        for share in db.session.execute(select(Send_Results).filter_by(patient_id=patient.id)).scalars():
            db.session.delete(share)
        for link in db.session.execute(select(PatientLink).filter_by(patient_id=patient.id)).scalars():
            link.status = LINK_REVOKED
            link.updated_at = now()
        patient.PatientName, patient.PatientLastname = "Compte", "supprimé"
        patient.PatientDOB, patient.PatientPOB, patient.PatientNat, patient.PatientCNI = None, "", "", ""
        patient.PatientPhone, patient.PatientPhone2 = None, None
        patient.PatientEmail, patient.PatientFederationID = placeholder, ""
    if doctor is not None:
        for share in db.session.execute(select(Send_Results).filter_by(doctor_id=doctor.id)).scalars():
            db.session.delete(share)
        for link in db.session.execute(select(PractitionerLink).filter_by(doctor_id=doctor.id)).scalars():
            db.session.delete(link)
        doctor.DoctorName, doctor.DoctorLastname = "Compte", "supprimé"
        doctor.DoctorDOB, doctor.DoctorPOB, doctor.DoctorNat, doctor.DoctorCNI = None, "", "", ""
        doctor.DoctorPhone, doctor.DoctorPhone2, doctor.DoctorSignature = None, None, None
        doctor.DoctorEmail, doctor.DoctorFederationID, doctor.CodeIdentification = placeholder, "", ""
    for model in (UserPreference, AccountRights, UserMfa, TenantMember):
        for row in db.session.execute(select(model).filter_by(user_id=uid)).scalars():
            db.session.delete(row)
    for note in db.session.execute(select(Notifications).filter_by(user_id=uid)).scalars():
        db.session.delete(note)
    if user.email:
        for otp in db.session.execute(select(EmailOtp).filter_by(email=user.email.lower())).scalars():
            db.session.delete(otp)
    user.email = placeholder
    user.username = f"supprime-{uid}-{secrets.token_hex(3)}"
    user.first_name, user.last_name, user.gender = "Compte", "supprimé", ""
    user.password = "!" + secrets.token_hex(16)  # aucun mot de passe ne correspond
    user.active = False
    audit("account.anonymized", user_id=actor_id, target=f"compte {uid}", commit=False)


def deletion_requests_page(status=None, page=1, size=25):
    stmt = select(DeletionRequest)
    if status:
        stmt = stmt.filter_by(status=status)
    from sqlalchemy import func

    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.order_by(DeletionRequest.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return {"items": [row.to_dict() for row in rows], "total": total, "page": page, "page_size": size}


def process_deletion(request_id, data, actor):
    request = db.session.get(DeletionRequest, request_id)
    if request is None:
        raise SaasError("Demande introuvable.", 404)
    if request.status != DELETION_REQUESTED:
        raise SaasError("Cette demande a déjà été traitée.", 409)
    decision = data.get("decision")
    note = (str(data.get("note") or "").strip()[:2000]) or None
    if decision == "refuse":
        if not note:
            raise SaasError("Indiquez le motif du refus : il est communiqué à l'utilisateur.", 400)
        request.status = DELETION_REFUSED
    elif decision == "anonymize":
        user = db.session.get(User, request.user_id) if request.user_id else None
        if user is not None:
            if user.id == actor.id:
                raise SaasError("Vous ne pouvez pas supprimer votre propre compte depuis la console.", 400)
            anonymize_account(user, actor.id)
        request.status = DELETION_DONE
    else:
        raise SaasError("Décision attendue : anonymize ou refuse.", 400)
    request.note = note
    request.processed_at = now()
    request.processed_by = actor.id
    audit(f"account.deletion_{request.status}", user_id=actor.id, target=request.email, commit=False)
    db.session.commit()
    if request.status == DELETION_REFUSED and request.user_id:
        notify_user(request.user_id, "deletion.refused.title", "deletion.refused.message", reason=note)
    return request
