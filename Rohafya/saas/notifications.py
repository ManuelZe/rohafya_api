"""Notifications envoyées depuis les consoles (super-administrateur, administrateur d'établissement).

- Le super-administrateur écrit aux administrateurs d'un établissement, à tous ses patients ou à tous ses médecins.
- L'administrateur d'un établissement écrit à un patient ou un médecin précis, à une sélection, ou à tout le groupe.

Chaque destinataire reçoit sa propre notification dans l'interface (état « lu » individuel) et un e-mail.
Les e-mails partent en tâche de fond : un envoi à plusieurs centaines de patients ne bloque pas la requête.
"""
import logging
import threading

from flask import current_app
from sqlalchemy import and_, exists, func, or_, select, text

from Rohafya import db
from Rohafya.accounts.models import Doctors, Notifications, Notifications_Type, Patients, User, UserDeletedNotifications
from . import services
from .constants import LINK_ACTIVE, SOURCE_GNUHEALTH
from .models import NotificationCampaign, PatientLink, PractitionerLink, TenantMember
from .services import SaasError, front_url, now

log = logging.getLogger(__name__)

AUDIENCES = ("admins", "patients", "doctors")
AUDIENCE_LABELS = {"admins": "administrateurs", "patients": "patients", "doctors": "médecins"}
# Page « notifications » ouverte par le lien de l'e-mail, selon le destinataire.
OPEN_PATHS = {"admins": "/admin/notifications", "patients": "/notifications", "doctors": "/doctors/notifications"}
TITLE_MAX = 150
MESSAGE_MAX = 2000


# =====================================================================
# Destinataires d'un établissement (comptes ROHAFYA actifs)
# =====================================================================

def _person(user, first_name, last_name, ref):
    name = f"{first_name or ''} {last_name or ''}".strip() or f"{user.first_name or ''} {user.last_name or ''}".strip()
    return {"user_id": user.id, "name": name or user.email, "email": user.email, "ref": ref}


def tenant_patients(tenant):
    """Patients ayant un compte et un rattachement actif à l'établissement.

    Pour l'établissement GNU Health, les patients importés avant le SaaS (matricule GNU Health) en font
    partie implicitement, sauf lien explicite contraire (même règle que `services.patient_sources`).
    """
    rows = db.session.execute(
        select(User, Patients, PatientLink.local_ref)
        .select_from(PatientLink)
        .join(Patients, Patients.id == PatientLink.patient_id)
        .join(User, User.id == Patients.user_id)
        .filter(PatientLink.tenant_id == tenant.id, PatientLink.status == LINK_ACTIVE, User.active.is_(True))
    ).all()
    people = {user.id: _person(user, patient.PatientName, patient.PatientLastname, ref) for user, patient, ref in rows}

    if tenant.source_type == SOURCE_GNUHEALTH:
        explicit = set(db.session.execute(select(PatientLink.local_ref).filter_by(tenant_id=tenant.id)).scalars().all())
        legacy = db.session.execute(
            select(User, Patients)
            .join(Patients, Patients.user_id == User.id)
            .filter(Patients.PatientFederationID.isnot(None), Patients.PatientFederationID != "", User.active.is_(True))
        ).all()
        for user, patient in legacy:
            ref = patient.PatientFederationID
            if services.is_legacy_matricule(ref) and ref not in explicit and user.id not in people:
                people[user.id] = _person(user, patient.PatientName, patient.PatientLastname, ref)
    return sorted(people.values(), key=lambda p: (p["name"] or "").lower())


def tenant_doctors(tenant):
    """Médecins rattachés à l'établissement (et, pour GNU Health, médecins à matricule GNU Health)."""
    rows = db.session.execute(
        select(User, Doctors)
        .select_from(PractitionerLink)
        .join(Doctors, Doctors.id == PractitionerLink.doctor_id)
        .join(User, User.id == Doctors.user_id)
        .filter(PractitionerLink.tenant_id == tenant.id, User.active.is_(True))
    ).all()
    people = {user.id: _person(user, doctor.DoctorName, doctor.DoctorLastname, doctor.DoctorFederationID) for user, doctor in rows}

    if tenant.source_type == SOURCE_GNUHEALTH:
        legacy = db.session.execute(
            select(User, Doctors).join(Doctors, Doctors.user_id == User.id).filter(User.active.is_(True))
        ).all()
        for user, doctor in legacy:
            if services.is_legacy_matricule(doctor.DoctorFederationID) and user.id not in people:
                people[user.id] = _person(user, doctor.DoctorName, doctor.DoctorLastname, doctor.DoctorFederationID)
    return sorted(people.values(), key=lambda p: (p["name"] or "").lower())


def tenant_admins(tenant):
    rows = db.session.execute(
        select(User).join(TenantMember, TenantMember.user_id == User.id).filter(TenantMember.tenant_id == tenant.id, User.active.is_(True))
    ).scalars().all()
    return sorted((_person(user, None, None, None) for user in rows), key=lambda p: (p["name"] or "").lower())


GROUPS = {"admins": tenant_admins, "patients": tenant_patients, "doctors": tenant_doctors}


def recipient_options(tenant, audience, query="", limit=20):
    """Destinataires proposés à la sélection (recherche par nom, e-mail ou identifiant)."""
    if audience not in GROUPS:
        raise SaasError("Destinataires inconnus.", 400)
    people = GROUPS[audience](tenant)
    needle = (query or "").strip().lower()
    if needle:
        people = [p for p in people if any(needle in (p.get(k) or "").lower() for k in ("name", "email", "ref"))]
    return {"items": people[:limit], "total": len(people)}


# =====================================================================
# Envoi
# =====================================================================

def _info_type():
    kind = db.session.execute(select(Notifications_Type).filter_by(name="INFOS")).scalar_one_or_none()
    if kind is None:
        kind = Notifications_Type(name="INFOS", description="Informations Type Of All Notifications.")
        db.session.add(kind)
        db.session.flush()
    return kind.id


def _clean(data):
    title = (data.get("title") or "").strip()
    message = (data.get("message") or "").strip()
    if not title:
        raise SaasError("Indiquez l'objet de la notification.", 400)
    if not message:
        raise SaasError("Écrivez le message de la notification.", 400)
    if len(title) > TITLE_MAX:
        raise SaasError(f"L'objet ne doit pas dépasser {TITLE_MAX} caractères.", 400)
    if len(message) > MESSAGE_MAX:
        raise SaasError(f"Le message ne doit pas dépasser {MESSAGE_MAX} caractères.", 400)
    return title, message


def send_campaign(tenant, data, sender, sender_role, allowed_audiences):
    """Crée une notification par destinataire, programme les e-mails et renvoie l'historique de l'envoi.

    data : {"audience": "admins" | "patients" | "doctors", "user_ids": [..] (vide : tout le groupe), "title", "message"}
    """
    audience = data.get("audience")
    if audience not in allowed_audiences:
        labels = ", ".join(AUDIENCE_LABELS[a] for a in allowed_audiences)
        raise SaasError(f"Destinataires possibles : {labels}.", 400)
    title, message = _clean(data)
    if not tenant.is_active:
        raise SaasError("Cet établissement est désactivé.", 400)

    group = GROUPS[audience](tenant)
    raw_ids = data.get("user_ids") or []
    try:
        selected = {int(i) for i in raw_ids}
    except (TypeError, ValueError):
        raise SaasError("Sélection de destinataires invalide.", 400)
    if selected:
        members = {p["user_id"] for p in group}
        unknown = selected - members
        if unknown:
            raise SaasError(f"{len(unknown)} destinataire(s) ne font pas partie des {AUDIENCE_LABELS[audience]} de cet établissement.", 400)
        group = [p for p in group if p["user_id"] in selected]
    if not group:
        raise SaasError(f"Aucun {AUDIENCE_LABELS[audience][:-1]} de cet établissement n'a de compte ROHAFYA actif.", 400)

    sender_label = "l'équipe ROHAFYA" if sender_role == "super_admin" else tenant.display_name
    source = "ROHAFYA" if sender_role == "super_admin" else tenant.display_name
    kind = _info_type()
    created = now()
    db.session.add_all(
        Notifications(user_id=p["user_id"], title=f"{source} · {title}"[:255], message=message, types=kind, created_at=created, all_users=False)
        for p in group
    )
    with_email = [p for p in group if p.get("email")]
    campaign = NotificationCampaign(
        tenant_id=tenant.id,
        sender_id=sender.id,
        sender_role=sender_role,
        audience=audience,
        selection="selected" if selected else "all",
        title=title,
        message=message,
        recipients_count=len(group),
        emails_count=len(with_email),
        created_at=created,
    )
    db.session.add(campaign)
    services.audit(
        "notification.sent",
        tenant_id=tenant.id,
        user_id=sender.id,
        target=AUDIENCE_LABELS[audience],
        details={"recipients": len(group), "selection": campaign.selection, "title": title},
        commit=False,
    )
    db.session.commit()

    open_url = front_url() + OPEN_PATHS[audience]
    mails = [
        (p["email"], f"{source} — {title}", {"title": title, "message": message, "name": p["name"], "sender": sender_label,
                                             "establishment": tenant.display_name, "open_url": open_url})
        for p in with_email
    ]
    dispatch_mails(campaign.id, mails)
    return campaign


def _deliver(campaign_id, mails):
    sent = sum(1 for to, subject, context in mails if services.send_mail(to, subject, "emails/notification.html", **context))
    campaign = db.session.get(NotificationCampaign, campaign_id)
    if campaign is not None:
        campaign.emails_sent = sent
        db.session.commit()
    if sent < len(mails):
        log.warning("Notification %s : %s e-mail(s) sur %s envoyé(s).", campaign_id, sent, len(mails))
    return sent


def dispatch_mails(campaign_id, mails):
    """E-mails en tâche de fond (sauf en test) ; `emails_sent` est renseigné à la fin de l'envoi."""
    if not mails:
        _deliver(campaign_id, mails)
        return
    app = current_app._get_current_object()
    if app.config.get("TESTING") or app.config.get("ROHAFYA_MAIL_SYNC"):
        _deliver(campaign_id, mails)
        return

    def worker():
        with app.app_context():
            try:
                _deliver(campaign_id, mails)
            except Exception:  # noqa: BLE001 — l'envoi ne doit jamais faire tomber le serveur
                log.exception("Envoi des e-mails de la notification %s interrompu.", campaign_id)
            finally:
                db.session.remove()

    threading.Thread(target=worker, name=f"notification-{campaign_id}", daemon=True).start()


def campaigns_page(tenant_id=None, page=1, size=20):
    stmt = select(NotificationCampaign)
    if tenant_id is not None:
        stmt = stmt.filter_by(tenant_id=tenant_id)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.order_by(NotificationCampaign.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return {"items": [c.to_dict() for c in rows], "total": total, "page": page, "page_size": size}


# =====================================================================
# Notifications de l'utilisateur connecté (toutes les interfaces)
# =====================================================================

def _visible(user_id):
    return select(Notifications).where(
        or_(Notifications.user_id == user_id, Notifications.all_users.is_(True)),
        ~exists().where(and_(UserDeletedNotifications.notification_id == Notifications.id, UserDeletedNotifications.user_id == user_id)),
    )


def my_notifications(user_id):
    rows = db.session.execute(_visible(user_id).order_by(Notifications.created_at.desc(), Notifications.id.desc())).scalars().all()
    return [n.to_dict() for n in rows]


def _owned(user_id, notification_id):
    notification = db.session.execute(_visible(user_id).filter(Notifications.id == notification_id)).scalar_one_or_none()
    if notification is None:
        raise SaasError("Notification introuvable.", 404)
    return notification


def mark_read(user_id, notification_id):
    notification = _owned(user_id, notification_id)
    # Une notification « à tous » est partagée : son état « lu » ne peut pas être propre à chacun.
    if notification.user_id == user_id and not notification.is_read:
        notification.is_read = True
        notification.read_at = now()
        db.session.commit()
    return notification.to_dict()


def mark_all_read(user_id):
    count = (
        db.session.query(Notifications)
        .filter(Notifications.user_id == user_id, Notifications.is_read.is_(False))
        .update({"is_read": True, "read_at": now()}, synchronize_session=False)
    )
    db.session.commit()
    return count


def hide(user_id, notification_id):
    notification = _owned(user_id, notification_id)
    db.session.add(UserDeletedNotifications(user_id=user_id, notification_id=notification.id))
    db.session.commit()


def hide_all(user_id):
    ids = db.session.execute(_visible(user_id).with_only_columns(Notifications.id)).scalars().all()
    db.session.add_all(UserDeletedNotifications(user_id=user_id, notification_id=i) for i in ids)
    db.session.commit()
    return len(ids)


# =====================================================================
# Base existante
# =====================================================================

def widen_message_column():
    """notifications.message : VARCHAR(255) → TEXT sur une base PostgreSQL existante (idempotent)."""
    if db.engine.dialect.name != "postgresql":
        return False
    with db.engine.begin() as connection:
        kind = connection.execute(text(
            "SELECT data_type FROM information_schema.columns "
            "WHERE table_schema = current_schema() AND table_name = 'notifications' AND column_name = 'message'"
        )).scalar()
        if kind and kind != "text":
            connection.execute(text("ALTER TABLE notifications ALTER COLUMN message TYPE TEXT"))
            return True
    return False
