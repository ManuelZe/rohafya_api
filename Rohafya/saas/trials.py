"""Demandes d'essai des établissements (formulaire du site de présentation, sans compte).

Les questions sont celles de la qualification du document de vente (annexe D.7). Chaque demande
est enregistrée, signalée aux super-administrateurs (notification et e-mail) et suivie dans leur console.
"""
import datetime
import re

from sqlalchemy import func, select

from Rohafya import db
from Rohafya.accounts.models import Notifications, Role, User, UserRoles
from . import i18n, services
from .constants import SUPER_ADMIN_ROLES
from .models import TrialRequest
from .services import SaasError, front_url, now

KINDS = ("laboratoire", "imagerie", "centre_medical", "clinique", "hopital", "reseau", "autre")
KIND_LABELS = {
    "laboratoire": "Laboratoire d'analyses",
    "imagerie": "Centre d'imagerie",
    "centre_medical": "Centre médical",
    "clinique": "Clinique",
    "hopital": "Hôpital",
    "reseau": "Réseau multi-sites",
    "autre": "Autre",
}
KIND_LABELS_EN = {
    "laboratoire": "Medical laboratory",
    "imagerie": "Imaging centre",
    "centre_medical": "Medical centre",
    "clinique": "Clinic",
    "hopital": "Hospital",
    "reseau": "Multi-site network",
    "autre": "Other",
}
VOLUMES = ("moins_200", "200_500", "500_2000", "plus_2000")
VOLUME_LABELS = {"moins_200": "Moins de 200", "200_500": "200 à 500", "500_2000": "500 à 2 000", "plus_2000": "Plus de 2 000"}
VOLUME_LABELS_EN = {"moins_200": "Under 200", "200_500": "200 to 500", "500_2000": "500 to 2,000", "plus_2000": "Over 2,000"}


def _kind_label(kind, locale):
    return (KIND_LABELS_EN if locale == "en" else KIND_LABELS).get(kind, kind)


def _volume_label(volume, locale):
    labels = VOLUME_LABELS_EN if locale == "en" else VOLUME_LABELS
    return labels.get(volume or "", "Not specified" if locale == "en" else "Non précisé")
STATUSES = ("nouvelle", "contactee", "essai", "close")
EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
PER_EMAIL_PER_DAY = 3
PER_HOUR = 30

REQUIRED = {
    "contact_name": "votre nom",
    "establishment": "le nom de l'établissement",
    "city": "la ville",
    "phone": "un numéro WhatsApp ou de téléphone",
    "email": "une adresse e-mail",
    "kind": "le type d'établissement",
}
LIMITS = {
    "contact_name": 150, "job_title": 150, "establishment": 200, "city": 120, "phone": 40, "email": 200,
    "software": 200, "pain_points": 2000, "preferred_slot": 60,
}


def _text(data, key):
    value = str(data.get(key) or "").strip()
    return value[: LIMITS.get(key, 200)] or None


def create_trial_request(data):
    # Champ invisible (« website ») : rempli seulement par les robots. Réponse identique, rien n'est enregistré.
    if str(data.get("website") or "").strip():
        return None
    values = {key: _text(data, key) for key in LIMITS}
    values["kind"] = str(data.get("kind") or "").strip()
    missing = [label for key, label in REQUIRED.items() if not values.get(key)]
    if missing:
        raise SaasError("Indiquez " + ", ".join(missing) + ".", 400)
    if values["kind"] not in KINDS:
        raise SaasError("Type d'établissement inconnu.", 400)
    if not EMAIL.match(values["email"]):
        raise SaasError("Adresse e-mail invalide.", 400)
    volume = str(data.get("monthly_volume") or "").strip() or None
    if volume is not None and volume not in VOLUMES:
        raise SaasError("Volume de résultats inconnu.", 400)
    if not data.get("consent"):
        raise SaasError("Cochez la case d'accord pour être recontacté.", 400)
    locale = "en" if str(data.get("locale") or "").lower() == "en" else "fr"

    email = values["email"].lower()
    since_day = now() - datetime.timedelta(days=1)
    recent = db.session.execute(
        select(func.count(TrialRequest.id)).filter(func.lower(TrialRequest.email) == email, TrialRequest.created_at >= since_day)
    ).scalar()
    if recent >= PER_EMAIL_PER_DAY:
        raise SaasError("Votre demande a déjà été reçue. Nous vous recontactons rapidement.", 429)
    hourly = db.session.execute(select(func.count(TrialRequest.id)).filter(TrialRequest.created_at >= now() - datetime.timedelta(hours=1))).scalar()
    if hourly >= PER_HOUR:
        raise SaasError("Trop de demandes en ce moment. Réessayez dans une heure.", 429)

    request = TrialRequest(
        contact_name=values["contact_name"],
        job_title=values["job_title"],
        establishment=values["establishment"],
        city=values["city"],
        phone=values["phone"],
        email=email,
        kind=values["kind"],
        monthly_volume=volume,
        software=values["software"],
        pain_points=values["pain_points"],
        preferred_slot=values["preferred_slot"],
        locale=locale,
        created_at=now(),
    )
    db.session.add(request)
    db.session.flush()
    _notify_super_admins(request)
    services.audit("trial.requested", target=request.establishment, details={"kind": request.kind, "city": request.city}, commit=False)
    db.session.commit()
    return request


def _super_admins():
    return db.session.execute(
        select(User).join(UserRoles, UserRoles.user_id == User.id).join(Role, Role.id == UserRoles.role_id)
        .filter(Role.name.in_(SUPER_ADMIN_ROLES), User.active.is_(True))
    ).scalars().unique().all()


def _notify_super_admins(request):
    contact = f"{request.contact_name}{' (' + request.job_title + ')' if request.job_title else ''}"
    admins = _super_admins()
    for admin in admins:
        locale = i18n.user_locale(admin.id)
        title = i18n.text("trial.title", locale, establishment=request.establishment)[:255]
        message = i18n.text("trial.message", locale, contact=contact, kind=_kind_label(request.kind, locale),
                            city=request.city, phone=request.phone, email=request.email)
        db.session.add(Notifications(user_id=admin.id, title=title, message=message, created_at=now(), all_users=False))
    link = front_url() + "/super-admin/demandes-essai"
    for admin in admins:
        if admin.email:
            locale = i18n.user_locale(admin.id)
            services.send_mail(
                admin.email,
                "trial.title",
                "emails/demande_essai.html",
                locale=locale,
                establishment=request.establishment,
                request=request,
                kind_label=_kind_label(request.kind, locale),
                volume_label=_volume_label(request.monthly_volume, locale),
                open_url=link,
            )


def trial_requests_page(status=None, page=1, size=25):
    stmt = select(TrialRequest)
    if status:
        stmt = stmt.filter_by(status=status)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.order_by(TrialRequest.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    counts = dict(db.session.execute(select(TrialRequest.status, func.count(TrialRequest.id)).group_by(TrialRequest.status)).all())
    return {"items": [r.to_dict() for r in rows], "total": total, "page": page, "page_size": size, "counts": counts}


def update_trial_request(request_id, data):
    request = db.session.get(TrialRequest, request_id)
    if request is None:
        raise SaasError("Demande introuvable.", 404)
    if "status" in data:
        if data["status"] not in STATUSES:
            raise SaasError("Statut inconnu.", 400)
        request.status = data["status"]
    if "note" in data:
        note = str(data.get("note") or "").strip()
        if len(note) > 2000:
            raise SaasError("La note ne doit pas dépasser 2000 caractères.", 400)
        request.note = note or None
    request.updated_at = now()
    db.session.commit()
    return request
