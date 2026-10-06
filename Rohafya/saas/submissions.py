"""Prescriptions, pré-enregistrements et requêtes adressés à un établissement.

Chaque élément créé par un patient, un médecin ou un visiteur (requête anonyme) est envoyé à un
établissement actif. Ses administrateurs le voient dans leur console (/admin/demandes), en changent
le statut et y répondent ; l'auteur voit la réponse dans son espace et en est prévenu.

Le contenu reste dans sa table d'origine (prescriptions, save_patients, requests). La table
saas_submissions porte l'adressage : établissement, auteur, statut, réponse.
"""
import logging

from flask_jwt_extended import current_user
from sqlalchemy import func, select, text

from Rohafya import db
from Rohafya.accounts.models import Doctors, Prescriptions, Requests, Role, SavePatients, User, UserRoles
from .constants import (
    AUTHOR_ANONYMOUS,
    AUTHOR_DOCTOR,
    AUTHOR_PATIENT,
    SUBMISSION_DONE,
    SUBMISSION_IN_PROGRESS,
    SUBMISSION_KIND_LABELS,
    SUBMISSION_PRE_REGISTRATION,
    SUBMISSION_PRESCRIPTION,
    SUBMISSION_RECEIVED,
    SUBMISSION_REFUSED,
    SUBMISSION_REQUEST,
    SUBMISSION_STATUS_LABELS,
    SUBMISSION_STATUSES,
    SUPER_ADMIN_ROLES,
)
from .models import Submission, Tenant, TenantMember
from .services import SaasError, audit, can_admin_tenant, doctor_of_user, notify_user, now, patient_of_user, send_mail

log = logging.getLogger(__name__)

ITEM_MODELS = {
    SUBMISSION_PRESCRIPTION: Prescriptions,
    SUBMISSION_PRE_REGISTRATION: SavePatients,
    SUBMISSION_REQUEST: Requests,
}

# Permissions des anciens administrateurs globaux (rôle Admin) : ils gardent la vue d'ensemble.
GLOBAL_ADMIN_PERMISSIONS = {
    SUBMISSION_PRESCRIPTION: "administration.patients_prescriptions.all_prescriptions",
    SUBMISSION_PRE_REGISTRATION: "administration.saved_patients.all_saved_patients",
    SUBMISSION_REQUEST: "administration.requete.all_requests",
}


# =====================================================================
# Établissements et auteur
# =====================================================================

def active_establishments():
    tenants = db.session.execute(select(Tenant).filter_by(is_active=True).order_by(Tenant.name)).scalars().all()
    return [{"id": tenant.id, "name": tenant.display_name} for tenant in tenants]


def target_tenant(value):
    """Établissement destinataire choisi dans le formulaire (tenant_id) ; doit être actif."""
    try:
        tenant_id = int(value)
    except (TypeError, ValueError):
        raise SaasError("Choisissez l'établissement destinataire.", 400)
    tenant = db.session.get(Tenant, tenant_id)
    if tenant is None or not tenant.is_active:
        raise SaasError("Cet établissement n'existe pas ou n'est plus actif.", 400)
    return tenant


def author_role(user, requested=None):
    """Rôle sous lequel l'utilisateur envoie : celui demandé par l'espace (patient / doctor) s'il le possède."""
    has_patient = patient_of_user(user.id) is not None
    has_doctor = doctor_of_user(user.id) is not None
    if requested == AUTHOR_DOCTOR and has_doctor:
        return AUTHOR_DOCTOR
    if requested == AUTHOR_PATIENT and has_patient:
        return AUTHOR_PATIENT
    if requested in (AUTHOR_DOCTOR, AUTHOR_PATIENT):
        raise SaasError("Ce compte n'a pas de profil " + ("médecin." if requested == AUTHOR_DOCTOR else "patient."), 403)
    if has_patient:
        return AUTHOR_PATIENT
    if has_doctor:
        return AUTHOR_DOCTOR
    raise SaasError("Ce compte n'a ni profil patient ni profil médecin.", 403)


def doctor_identity(user):
    """(nom complet, numéro d'ordre) du médecin connecté."""
    doctor = doctor_of_user(user.id)
    if doctor is None:
        return None, None
    name = " ".join(part for part in (doctor.DoctorName, doctor.DoctorLastname) if part) or None
    return (f"Dr {name}" if name else None), (doctor.DoctorNO or None)


# =====================================================================
# Création et notification de l'établissement
# =====================================================================

def create_submission(kind, item, tenant, author=None, role=AUTHOR_ANONYMOUS, patient_name=None):
    """Adresse un élément tout juste enregistré (flush fait) à un établissement et prévient ses administrateurs."""
    submission = Submission(
        kind=kind,
        item_id=item.id,
        tenant_id=tenant.id,
        author_id=author.id if author else None,
        author_role=role,
        patient_name=(patient_name or "").strip()[:200] or None,
        status=SUBMISSION_RECEIVED,
    )
    db.session.add(submission)
    db.session.flush()
    audit(f"submission.{kind}.created", tenant_id=tenant.id, user_id=author.id if author else None,
          target=str(submission.id), commit=False)
    db.session.commit()
    notify_tenant(submission, author_label(submission, item))
    return submission


def tenant_recipients(tenant):
    """Administrateurs de l'établissement ; à défaut, son e-mail de contact puis les super-administrateurs."""
    members = db.session.execute(select(User).join(TenantMember, TenantMember.user_id == User.id).filter(
        TenantMember.tenant_id == tenant.id, User.active.is_(True))).scalars().all()
    if members:
        return members, [user.email for user in members if user.email]
    contact = (tenant.setting("contact_email") or "").strip()
    supers = db.session.execute(select(User).join(UserRoles, UserRoles.user_id == User.id).join(Role, Role.id == UserRoles.role_id)
                                .filter(Role.name.in_(SUPER_ADMIN_ROLES), User.active.is_(True))).scalars().unique().all()
    emails = [contact] if contact else [user.email for user in supers if user.email]
    return supers, emails


def notify_tenant(submission, author_name):
    tenant = submission.tenant
    label = SUBMISSION_KIND_LABELS[submission.kind]
    users, emails = tenant_recipients(tenant)
    title = f"Nouvelle {label}" if submission.kind != SUBMISSION_PRE_REGISTRATION else "Nouveau pré-enregistrement"
    for user in users:
        notify_user(user.id, title, f"{author_name} a envoyé une {label} à {tenant.display_name}.")
    for email in dict.fromkeys(emails):
        send_mail(email, f"ROHAFYA · {title} pour {tenant.display_name}", "emails/nouvelle_demande.html",
                  establishment=tenant.display_name, kind_label=label, author=author_name, admin_path="/admin/demandes")


# =====================================================================
# Accès et listes
# =====================================================================

def submission_of(kind, item_id):
    return db.session.execute(select(Submission).filter_by(kind=kind, item_id=item_id)).scalar_one_or_none()


def submissions_by_item(kind, item_ids):
    if not item_ids:
        return {}
    rows = db.session.execute(select(Submission).filter(Submission.kind == kind, Submission.item_id.in_(list(item_ids)))).scalars()
    return {row.item_id: row for row in rows}


def is_global_admin(user, kind):
    return user is not None and user.has_permission(GLOBAL_ADMIN_PERMISSIONS[kind])


def is_author(user, kind, item, submission):
    if user is None:
        return False
    if submission is not None and submission.author_id == user.id:
        return True
    if kind in (SUBMISSION_PRESCRIPTION, SUBMISSION_PRE_REGISTRATION) and item.patient_id:
        patient = patient_of_user(user.id)
        return patient is not None and patient.id == item.patient_id
    return kind == SUBMISSION_REQUEST and item.CreatedBy == user.id


def can_read(user, kind, item, submission):
    """Auteur, administrateur de l'établissement destinataire, ou ancien administrateur global."""
    if is_author(user, kind, item, submission) or is_global_admin(user, kind):
        return True
    return submission is not None and can_admin_tenant(user, submission.tenant_id)


def get_item(kind, item_id, user=None, write=False):
    """Élément accessible à l'utilisateur connecté (auteur, ou administrateur pour la lecture)."""
    user = user or current_user
    item = db.session.get(ITEM_MODELS[kind], item_id)
    if item is None:
        raise SaasError(f"{SUBMISSION_KIND_LABELS[kind].capitalize()} introuvable.", 404)
    submission = submission_of(kind, item_id)
    allowed = (is_author(user, kind, item, submission) or is_global_admin(user, kind)) if write else can_read(user, kind, item, submission)
    if not allowed:
        raise SaasError("Accès refusé.", 403)
    return item, submission


def own_items(kind, user, role):
    """Éléments envoyés par l'utilisateur sous ce rôle (patient : y compris ceux d'avant les établissements)."""
    model = ITEM_MODELS[kind]
    ids = set(db.session.execute(select(Submission.item_id).filter_by(kind=kind, author_id=user.id, author_role=role)).scalars())
    if role == AUTHOR_PATIENT:
        patient = patient_of_user(user.id)
        if kind == SUBMISSION_REQUEST:
            legacy = select(model.id).filter(model.CreatedBy == user.id)
        elif patient is not None:
            legacy = select(model.id).filter(model.patient_id == patient.id)
        else:
            legacy = None
        if legacy is not None:
            attached = select(Submission.item_id).filter(Submission.kind == kind)
            ids.update(db.session.execute(legacy.filter(model.id.not_in(attached))).scalars())
    if not ids:
        return []
    return db.session.execute(select(model).filter(model.id.in_(ids)).order_by(model.id.desc())).scalars().all()


def with_submission(kind, items):
    """Sérialise des éléments avec leur adressage (clé « submission », None pour un élément non adressé)."""
    subs = submissions_by_item(kind, [item.id for item in items])
    result = []
    for item in items:
        data = item.to_dict()
        submission = subs.get(item.id)
        data["submission"] = submission.to_dict() if submission else None
        result.append(data)
    return result


def delete_item(kind, item):
    submission = submission_of(kind, item.id)
    if submission is not None:
        db.session.delete(submission)
    db.session.delete(item)
    db.session.commit()


# =====================================================================
# Console de l'établissement
# =====================================================================

def author_label(submission, item):
    if submission.author is not None:
        name = " ".join(part for part in (submission.author.first_name, submission.author.last_name) if part)
        if name:
            return ("Dr " + name) if submission.author_role == AUTHOR_DOCTOR else name
    if submission.kind == SUBMISSION_REQUEST:
        return " ".join(part for part in (item.first_name, item.last_name) if part) or "Visiteur"
    return "Un patient"


def author_email(submission, item):
    if submission.author is not None and submission.author.email:
        return submission.author.email
    if submission.kind == SUBMISSION_REQUEST:
        return item.email or None
    return None


def admin_view(submission, item):
    data = submission.to_dict()
    data["author"] = {
        "id": submission.author_id,
        "name": author_label(submission, item),
        "email": author_email(submission, item),
        "role": submission.author_role,
    }
    data["item"] = item.to_dict() if item is not None else None
    data["has_image"] = bool(getattr(item, "image_data", None)) if item is not None else False
    return data


def tenant_submissions(tenant, kind=None, status=None, query=None, page=1, size=25):
    stmt = select(Submission).filter(Submission.tenant_id == tenant.id)
    if kind:
        stmt = stmt.filter(Submission.kind == kind)
    if status:
        stmt = stmt.filter(Submission.status == status)
    if query:
        like = f"%{query.strip().lower()}%"
        full = func.coalesce(User.first_name, "") + " " + func.coalesce(User.last_name, "") + " " + func.coalesce(User.email, "")
        authors = select(User.id).filter(func.lower(full).like(like))
        stmt = stmt.filter((Submission.author_id.in_(authors)) | (func.lower(Submission.patient_name).like(like)))
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(stmt.order_by(Submission.created_at.desc(), Submission.id.desc())
                              .offset((page - 1) * size).limit(size)).scalars().all()
    items = []
    for submission in rows:
        item = db.session.get(ITEM_MODELS[submission.kind], submission.item_id)
        items.append(admin_view(submission, item))
    counts = {}
    for row_kind, row_status, count in db.session.execute(
        select(Submission.kind, Submission.status, func.count(Submission.id))
        .filter(Submission.tenant_id == tenant.id).group_by(Submission.kind, Submission.status)
    ).all():
        counts.setdefault(row_kind, {})[row_status] = count
    return {"items": items, "total": total, "page": page, "page_size": size, "counts": counts}


def respond(submission, actor, status=None, response=None, quote_amount=None):
    """Réponse de l'établissement : statut, texte, montant du devis. Prévient l'auteur."""
    item = db.session.get(ITEM_MODELS[submission.kind], submission.item_id)
    if status is not None:
        if status not in SUBMISSION_STATUSES:
            raise SaasError(f"Statut inconnu : {status}.", 400)
        submission.status = status
    if response is not None:
        submission.response = str(response).strip()[:5000] or None
    if quote_amount is not None:
        if quote_amount == "":
            submission.quote_amount = None
        else:
            try:
                submission.quote_amount = round(float(quote_amount), 2)
            except (TypeError, ValueError):
                raise SaasError("Montant du devis invalide.", 400)
            if submission.quote_amount < 0:
                raise SaasError("Montant du devis invalide.", 400)
    submission.responded_by = actor.id
    submission.responded_at = now()
    submission.updated_at = now()
    _sync_legacy_flags(submission, item, actor)
    audit(f"submission.{submission.kind}.{submission.status}", tenant_id=submission.tenant_id, user_id=actor.id,
          target=str(submission.id), commit=False)
    db.session.commit()
    _notify_author(submission, item)
    return admin_view(submission, item)


def _sync_legacy_flags(submission, item, actor):
    """Garde cohérents les anciens champs (valide / rejected / validated) lus par l'existant."""
    if item is None:
        return
    if submission.kind == SUBMISSION_REQUEST:
        item.valide = submission.status == SUBMISSION_DONE
        item.rejected = submission.status == SUBMISSION_REFUSED
        item.UpdatedAt = now()
        item.UpdatedBy = actor.id
    elif submission.kind == SUBMISSION_PRE_REGISTRATION:
        done = submission.status == SUBMISSION_DONE
        item.validated = done
        item.validated_by = actor.id if done else None
        item.validated_at = now() if done else None


def _notify_author(submission, item):
    if submission.status not in (SUBMISSION_IN_PROGRESS, SUBMISSION_DONE, SUBMISSION_REFUSED) and not submission.response:
        return
    label = SUBMISSION_KIND_LABELS[submission.kind]
    status_label = SUBMISSION_STATUS_LABELS[submission.status].lower()
    establishment = submission.tenant.display_name
    message = f"Votre {label} envoyée à {establishment} est {status_label}."
    if submission.kind == SUBMISSION_PRE_REGISTRATION:
        message = f"Votre pré-enregistrement envoyé à {establishment} est {status_label}."
    if submission.author_id:
        notify_user(submission.author_id, f"Réponse de {establishment}", message)
    email = author_email(submission, item)
    if email:
        send_mail(email, f"ROHAFYA · Réponse de {establishment}", "emails/reponse_demande.html",
                  establishment=establishment, message=message, response=submission.response,
                  quote_amount=submission.quote_amount, has_account=bool(submission.author_id))


# =====================================================================
# Installation (flask saas init)
# =====================================================================

def relax_patient_columns():
    """patient_id devient facultatif (prescriptions et pré-enregistrements de médecins) sur une base existante.

    PostgreSQL seulement (une base neuve est créée ainsi par create_all). Appelé au démarrage et par
    flask saas init : l'ALTER n'est exécuté que si la colonne est encore obligatoire.
    """
    if db.engine.dialect.name != "postgresql":
        return []
    changed = []
    with db.engine.begin() as connection:
        for table in ("prescriptions", "save_patients"):
            nullable = connection.execute(text(
                "SELECT is_nullable FROM information_schema.columns "
                "WHERE table_schema = current_schema() AND table_name = :table AND column_name = 'patient_id'"
            ), {"table": table}).scalar()
            if nullable == "NO":
                connection.execute(text(f"ALTER TABLE {table} ALTER COLUMN patient_id DROP NOT NULL"))
                changed.append(table)
    return changed


def attach_orphans(tenant):
    """Adresse à `tenant` (PDMD Santé) les éléments créés avant les établissements. Idempotent."""
    created = {}
    for kind, model in ITEM_MODELS.items():
        attached = select(Submission.item_id).filter(Submission.kind == kind)
        orphans = db.session.execute(select(model).filter(model.id.not_in(attached))).scalars().all()
        for item in orphans:
            db.session.add(_legacy_submission(kind, item, tenant))
        created[kind] = len(orphans)
    db.session.commit()
    return created


def _legacy_submission(kind, item, tenant):
    author_id, role, status, created_at = None, AUTHOR_ANONYMOUS, SUBMISSION_RECEIVED, None
    if kind in (SUBMISSION_PRESCRIPTION, SUBMISSION_PRE_REGISTRATION):
        author_id = item.patient.user_id if item.patient is not None else None
        role = AUTHOR_PATIENT
        created_at = item.Create_date
        if kind == SUBMISSION_PRE_REGISTRATION and item.validated:
            status = SUBMISSION_DONE
    else:
        author_id = item.CreatedBy
        created_at = item.CreatedAt
        if author_id:
            role = AUTHOR_DOCTOR if db.session.execute(select(Doctors.id).filter_by(user_id=author_id)).first() and not \
                db.session.execute(select(User.id).filter_by(id=author_id, is_patient=True)).first() else AUTHOR_PATIENT
        if item.rejected:
            status = SUBMISSION_REFUSED
        elif item.valide:
            status = SUBMISSION_DONE
    return Submission(kind=kind, item_id=item.id, tenant_id=tenant.id, author_id=author_id, author_role=role,
                      status=status, created_at=created_at or now())
