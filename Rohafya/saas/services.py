"""Logique métier du module SaaS, utilisée par les endpoints existants et par les nouveaux."""
import datetime
import hashlib
import hmac
import logging
import os
import secrets
from email.utils import parsedate_to_datetime

from flask import current_app, render_template
from flask_mail import Mail, Message
from sqlalchemy import func, null, select
from sqlalchemy.exc import IntegrityError

from Rohafya import db
from Rohafya.accounts.models import (
    Doctors,
    Notifications,
    Notifications_Type,
    Patients,
    Role,
    User,
)
from .constants import (
    DEFAULT_GNUHEALTH_NAME,
    DEFAULT_GNUHEALTH_SLUG,
    DEFAULT_SETTINGS,
    KIND_INVOICE,
    LINK_ACTIVE,
    LINK_PENDING,
    LINK_REVOKED,
    OTP_LENGTH,
    OTP_MAX_ATTEMPTS,
    OTP_MAX_REQUESTS_PER_HOUR,
    OTP_TTL_MINUTES,
    PATIENT_ID_PREFIXES,
    ROHAFYA_ID_PREFIX,
    ROLE_PATIENT,
    ROLE_TENANT_ADMIN,
    SHORT_CODE_ALPHABET,
    SHORT_CODE_LENGTH,
    SOURCE_GNUHEALTH,
    SUPER_ADMIN_ROLES,
)
from .models import AuditLog, EmailOtp, LinkToken, PatientLink, PractitionerLink, Tenant, TenantMember, TenantRecord

log = logging.getLogger(__name__)

EXPIRED_MESSAGE = "La période d'accès aux détails de ce résultat est expirée."
UNPAID_MESSAGE = (
    "Vous avez des factures impayées. "
    "Veuillez les régler pour accéder à vos résultats. "
    "Consultez la section 'Factures' pour plus de détails."
)
RECORD_ID_OFFSET = 1_000_000_000


class SaasError(Exception):
    """Erreur métier renvoyée telle quelle au client ({"message": ...}, status)."""

    def __init__(self, message, status=400, **extra):
        super().__init__(message)
        self.message = message
        self.status = status
        self.extra = extra

    def response(self):
        body = {"message": self.message}
        body.update(self.extra)
        return body, self.status


# =====================================================================
# Outils
# =====================================================================

def now():
    return datetime.datetime.now()


def sha256(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def keyed_hash(value):
    """Empreinte d'un code court (OTP) liée à la clé secrète de l'application."""
    secret = (current_app.config.get("SECRET_KEY") or "rohafya").encode("utf-8")
    return hmac.new(secret, value.encode("utf-8"), hashlib.sha256).hexdigest()


def normalize_email(email):
    return (email or "").strip().lower()


def parse_datetime(value):
    """Accepte ISO 8601, AAAA-MM-JJ, RFC 1123 (format Flask) ou un datetime."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime.datetime):
        return value.replace(tzinfo=None)
    if isinstance(value, datetime.date):
        return datetime.datetime(value.year, value.month, value.day)
    text = str(value).strip()
    try:
        parsed = datetime.datetime.fromisoformat(text.replace("Z", "+00:00"))
        return parsed.astimezone().replace(tzinfo=None) if parsed.tzinfo else parsed
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(text).astimezone().replace(tzinfo=None)
    except (TypeError, ValueError):
        return None


def to_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def front_url():
    """URL publique du front-end ROHAFYA (utilisée dans les QR codes et les e-mails)."""
    url = os.environ.get("ROHAFYA_FRONT_URL") or current_app.config.get("ROHAFYA_FRONT_URL") or "https://rohafya.com"
    return url.rstrip("/")


# =====================================================================
# Rôles et droits
# =====================================================================

def get_or_create_role(name):
    role = db.session.execute(select(Role).filter_by(name=name)).scalar_one_or_none()
    if role:
        return role
    role = Role(name=name)
    db.session.add(role)
    db.session.flush()
    return role


def add_role(user, name):
    role = get_or_create_role(name)
    if role not in user.roles:
        user.roles.append(role)


def role_names(user):
    return {role.name for role in (user.roles or [])} if user else set()


def is_super_admin(user):
    return bool(role_names(user) & SUPER_ADMIN_ROLES)


def admin_tenant_ids(user):
    if not user:
        return []
    rows = db.session.execute(select(TenantMember.tenant_id).filter_by(user_id=user.id)).scalars().all()
    return list(rows)


def can_admin_tenant(user, tenant_id):
    return is_super_admin(user) or tenant_id in admin_tenant_ids(user)


# =====================================================================
# Établissements
# =====================================================================

def default_gnuhealth_tenant():
    """Établissement GNU Health historique (PDMD Santé), créé au premier besoin."""
    tenant = db.session.execute(
        select(Tenant).filter_by(source_type=SOURCE_GNUHEALTH).order_by(Tenant.id).limit(1)
    ).scalar_one_or_none()
    if tenant:
        return tenant
    settings = dict(DEFAULT_SETTINGS)
    settings.update({"display_name": DEFAULT_GNUHEALTH_NAME, "commissions_enabled": True})
    tenant = Tenant(slug=DEFAULT_GNUHEALTH_SLUG, name=DEFAULT_GNUHEALTH_NAME, source_type=SOURCE_GNUHEALTH, settings=settings)
    db.session.add(tenant)
    try:
        db.session.commit()
    except IntegrityError:
        # Un autre worker l'a créé au même instant.
        db.session.rollback()
        tenant = db.session.execute(select(Tenant).filter_by(slug=DEFAULT_GNUHEALTH_SLUG)).scalar_one()
    return tenant


def generate_api_key():
    from .constants import API_KEY_PREFIX

    key = API_KEY_PREFIX + secrets.token_urlsafe(32)
    return key, sha256(key), key[-6:]


def tenant_from_api_key(key):
    if not key:
        return None
    tenant = db.session.execute(select(Tenant).filter_by(api_key_hash=sha256(key.strip()))).scalar_one_or_none()
    return tenant if tenant and tenant.is_active else None


# =====================================================================
# Sources de données d'un patient (liens)
# =====================================================================

def is_legacy_matricule(federation_id):
    """Matricule GNU Health d'un patient importé avant le passage en SaaS.

    Les identifiants ROHAFYA- (et EDEN-, attribués avant le changement de nom) ne sont pas des matricules.
    """
    return bool(federation_id) and not str(federation_id).startswith(PATIENT_ID_PREFIXES)


def patient_sources(patient):
    """Établissements dont le patient peut voir les données : [(tenant, local_ref)].

    Un patient importé de GNU Health avant le SaaS est implicitement relié à l'établissement
    GNU Health par son matricule, tant qu'aucun lien explicite ne dit le contraire.
    """
    if not patient:
        return []
    links = db.session.execute(select(PatientLink).filter_by(patient_id=patient.id)).scalars().all()
    sources = [(link.tenant, link.local_ref) for link in links if link.status == LINK_ACTIVE and link.tenant and link.tenant.is_active]

    if is_legacy_matricule(patient.PatientFederationID):
        gnu = default_gnuhealth_tenant()
        overridden = any(link.tenant_id == gnu.id and link.local_ref == patient.PatientFederationID for link in links)
        if not overridden and gnu.is_active:
            sources.append((gnu, patient.PatientFederationID))
    return sources


def gnuhealth_ref(patient):
    """Matricule GNU Health (federation_account) du patient, s'il est relié à GNU Health."""
    for tenant, local_ref in patient_sources(patient):
        if tenant.source_type == SOURCE_GNUHEALTH:
            return local_ref
    return None


def external_sources(patient):
    return [(tenant, ref) for tenant, ref in patient_sources(patient) if tenant.source_type != SOURCE_GNUHEALTH]


def gnuhealth_establishment_name():
    return default_gnuhealth_tenant().display_name


def patient_of_user(user_id):
    return db.session.execute(select(Patients).filter_by(user_id=user_id)).scalar_one_or_none()


def doctor_of_user(user_id):
    return db.session.execute(select(Doctors).filter_by(user_id=user_id)).scalar_one_or_none()


def generate_rohafya_id():
    while True:
        candidate = ROHAFYA_ID_PREFIX + "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(8))
        exists = db.session.execute(select(Patients.id).filter_by(PatientFederationID=candidate)).first()
        if not exists:
            return candidate


def ensure_patient_profile(user):
    """Profil patient du compte ; créé (avec un identifiant ROHAFYA) s'il n'existe pas encore."""
    patient = patient_of_user(user.id)
    if patient:
        return patient
    patient = Patients(
        PatientFederationID=generate_rohafya_id(),
        PatientName=user.first_name,
        PatientLastname=user.last_name,
        PatientEmail=user.email,
        # NULL explicite : ces colonnes sont uniques avec une valeur par défaut '' (deux '' seraient en conflit).
        PatientPhone=null(),
        PatientPhone2=null(),
        CreatedAt=now(),
        patient_is_confirmed=False,
        user_id=user.id,
    )
    add_role(user, ROLE_PATIENT)
    db.session.add(patient)
    db.session.flush()
    return patient


# =====================================================================
# Données reçues (API / FHIR)
# =====================================================================

def result_expiration(tenant, validated_at):
    if not validated_at:
        return None
    return validated_at + datetime.timedelta(days=int(tenant.setting("result_access_days") or 90))


def serialize_record(record, tenant, patient):
    """Enregistrement au format attendu par le front (identique aux réponses GNU Health)."""
    data = dict(record.payload or {})
    data["id"] = RECORD_ID_OFFSET + record.id
    data["establishment"] = tenant.display_name
    data["establishment_id"] = tenant.id
    if patient is not None:
        data.setdefault("matricule_patient", patient.PatientFederationID)
        data.setdefault("patient", f"{patient.PatientName or ''} {patient.PatientLastname or ''}".strip())
    if record.kind != KIND_INVOICE:
        expiration = result_expiration(tenant, record.validated_at)
        days = int(tenant.setting("result_access_days") or 90)
        data["validation_date"] = record.validated_at or data.get("validation_date")
        data["expiration_date"] = expiration
        data["nbr_days_before_expiration"] = days if expiration else None
        data["statut_expiration"] = bool(expiration and now() >= expiration)
    return data


def patient_records(patient, kind):
    """Enregistrements d'un type pour tous les établissements non GNU Health du patient."""
    items = []
    for tenant, local_ref in external_sources(patient):
        records = db.session.execute(
            select(TenantRecord).filter_by(tenant_id=tenant.id, local_ref=local_ref, kind=kind)
        ).scalars().all()
        items.extend(serialize_record(record, tenant, patient) for record in records)
    return items


def find_patient_record(patient, kind, code):
    for tenant, local_ref in external_sources(patient):
        record = db.session.execute(
            select(TenantRecord).filter_by(tenant_id=tenant.id, local_ref=local_ref, kind=kind, code=code)
        ).scalar_one_or_none()
        if record:
            return record, tenant
    return None, None


def external_record_requested(patient):
    """La requête en cours (détail d'un examen) vise-t-elle un résultat reçu d'un autre établissement ?"""
    from flask import request

    from .constants import KIND_EXPLORATION, KIND_IMAGING, KIND_LAB

    kind = {"laboratoire": KIND_LAB, "imagerie": KIND_IMAGING, "exploration": KIND_EXPLORATION}.get(request.blueprint)
    values = list((request.view_args or {}).values())
    if not kind or not values:
        return False
    record, _ = find_patient_record(patient, kind, str(values[0]))
    return record is not None


def has_unpaid_invoices(tenant_id, local_ref):
    """Même règle que GNU Health : reste à payer > 10 % du montant hors taxes."""
    invoices = db.session.execute(
        select(TenantRecord).filter_by(tenant_id=tenant_id, local_ref=local_ref, kind=KIND_INVOICE)
    ).scalars().all()
    for invoice in invoices:
        payload = invoice.payload or {}
        if payload.get("state") in ("posted", "open", "partial"):
            if to_float(payload.get("amount_to_pay_today")) > 0.1 * to_float(payload.get("untaxed_amount")):
                return True
    return False


def record_details_response(record, tenant, check_unpaid=True):
    """Détail d'un résultat reçu, en appliquant les règles de l'établissement."""
    expiration = result_expiration(tenant, record.validated_at)
    if expiration and now() >= expiration:
        return {"Message": EXPIRED_MESSAGE}, 200
    if check_unpaid and tenant.setting("block_unpaid_results") and has_unpaid_invoices(tenant.id, record.local_ref):
        return {"message": UNPAID_MESSAGE}, 403
    return list(record.details or []), 200


# =====================================================================
# Jetons de rattachement (QR code)
# =====================================================================

def link_is_taken(tenant, local_ref):
    """Le dossier local est-il déjà rattaché à un compte ROHAFYA ?"""
    existing = db.session.execute(
        select(PatientLink).filter(
            PatientLink.tenant_id == tenant.id,
            PatientLink.local_ref == local_ref,
            PatientLink.status.in_([LINK_ACTIVE, LINK_PENDING]),
        )
    ).scalar_one_or_none()
    if existing:
        return existing
    if tenant.source_type == SOURCE_GNUHEALTH:
        legacy = db.session.execute(select(Patients).filter_by(PatientFederationID=local_ref)).scalar_one_or_none()
        if legacy:
            return legacy
    return None


def format_short_code(code):
    return f"{code[:3]}-{code[3:]}"


def issue_link_token(tenant, local_ref, email_hint=None, created_by=None):
    """Crée le jeton du QR code d'un dossier local. Une réimpression annule le jeton précédent."""
    local_ref = (local_ref or "").strip()
    if not local_ref:
        raise SaasError("Le numéro de dossier local (local_ref) est obligatoire.", 400)

    if link_is_taken(tenant, local_ref):
        return {"already_linked": True, "local_ref": local_ref}

    for previous in db.session.execute(
        select(LinkToken).filter_by(tenant_id=tenant.id, local_ref=local_ref, used_at=None, revoked_at=None)
    ).scalars():
        previous.revoked_at = now()

    token = secrets.token_urlsafe(16)
    while True:
        short = "".join(secrets.choice(SHORT_CODE_ALPHABET) for _ in range(SHORT_CODE_LENGTH))
        if not db.session.execute(select(LinkToken.id).filter_by(short_hash=sha256(short))).first():
            break

    days = int(tenant.setting("link_token_days") or 30)
    link_token = LinkToken(
        token_hash=sha256(token),
        short_hash=sha256(short),
        tenant_id=tenant.id,
        local_ref=local_ref,
        email_hint=normalize_email(email_hint) or None,
        expires_at=now() + datetime.timedelta(days=days),
        created_by=created_by,
    )
    db.session.add(link_token)
    db.session.flush()
    audit("link_token.issued", tenant_id=tenant.id, user_id=created_by, target=local_ref, commit=False)
    db.session.commit()

    return {
        "already_linked": False,
        "token_id": link_token.id,
        "local_ref": local_ref,
        "url": f"{front_url()}/l/{token}",
        "short_code": format_short_code(short),
        "expires_at": link_token.expires_at,
    }


def normalize_short_code(value):
    compact = "".join(ch for ch in (value or "").upper() if ch.isalnum())
    if len(compact) == SHORT_CODE_LENGTH and all(ch in SHORT_CODE_ALPHABET for ch in compact):
        return compact
    return None


def redeem_link_token(user, value):
    """Rattache le dossier désigné par un jeton (QR) ou un code court au compte connecté."""
    from .constants import LINK_METHOD_CODE, LINK_METHOD_QR, REDEEM_MAX_ATTEMPTS_PER_HOUR
    from .models import SaasAttempt

    since = now() - datetime.timedelta(hours=1)
    failures = db.session.execute(
        select(func.count(SaasAttempt.id)).filter(
            SaasAttempt.user_id == user.id,
            SaasAttempt.action == "redeem",
            SaasAttempt.success.is_(False),
            SaasAttempt.created_at >= since,
        )
    ).scalar()
    if failures >= REDEEM_MAX_ATTEMPTS_PER_HOUR:
        raise SaasError("Trop d'essais. Réessayez dans une heure.", 429)

    short = normalize_short_code(value)
    method = LINK_METHOD_CODE if short else LINK_METHOD_QR
    lookup = sha256(short) if short else sha256((value or "").strip())
    column = LinkToken.short_hash if short else LinkToken.token_hash
    link_token = db.session.execute(select(LinkToken).filter(column == lookup)).scalar_one_or_none()

    if not link_token or link_token.status != "active":
        db.session.add(SaasAttempt(user_id=user.id, action="redeem", success=False))
        db.session.commit()
        if link_token and link_token.status == "used":
            raise SaasError("Ce code a déjà été utilisé.", 410)
        if link_token and link_token.status == "expired":
            raise SaasError("Ce code a expiré. Demandez-en un nouveau à l'accueil de l'établissement.", 410)
        raise SaasError("Code invalide. Vérifiez-le ou demandez-en un nouveau à l'accueil.", 404)

    tenant = link_token.tenant
    if not tenant or not tenant.is_active:
        raise SaasError("Cet établissement n'est plus actif sur ROHAFYA.", 410)

    patient = ensure_patient_profile(user)
    taken = link_is_taken(tenant, link_token.local_ref)
    if isinstance(taken, PatientLink) and taken.patient_id == patient.id:
        link = taken
    elif taken is not None and not (isinstance(taken, Patients) and taken.id == patient.id):
        audit("link.conflict", tenant_id=tenant.id, user_id=user.id, target=link_token.local_ref, commit=False)
        db.session.add(SaasAttempt(user_id=user.id, action="redeem", success=False))
        db.session.commit()
        raise SaasError("Ce dossier est déjà rattaché à un autre compte. Contactez l'établissement.", 409)
    else:
        link = db.session.execute(
            select(PatientLink).filter_by(tenant_id=tenant.id, local_ref=link_token.local_ref)
        ).scalar_one_or_none()
        if link is None:
            link = PatientLink(patient_id=patient.id, tenant_id=tenant.id, local_ref=link_token.local_ref)
            db.session.add(link)
        link.patient_id = patient.id
        emails = {normalize_email(user.email), normalize_email(patient.PatientEmail)}
        link.status = LINK_ACTIVE if link_token.email_hint and link_token.email_hint in emails else LINK_PENDING
        link.method = method
        link.verified_at = now() if link.status == LINK_ACTIVE else None
        link.updated_at = now()

    link_token.used_at = now()
    link_token.used_by = user.id
    db.session.add(SaasAttempt(user_id=user.id, action="redeem", success=True))
    db.session.flush()
    audit(f"link.{link.status}", tenant_id=tenant.id, user_id=user.id, target=link.local_ref, details={"method": method}, commit=False)
    db.session.commit()
    return link


def set_link_status(link, status, actor_id):
    link.status = status
    link.updated_at = now()
    if status == LINK_ACTIVE:
        link.verified_at = now()
        link.verified_by = actor_id
    audit(f"link.{status}", tenant_id=link.tenant_id, user_id=actor_id, target=link.local_ref, commit=False)
    db.session.commit()
    if status == LINK_ACTIVE and link.patient:
        notify_user(
            link.patient.user_id,
            "Établissement rattaché",
            f"Votre dossier de {link.tenant.display_name} est désormais accessible dans ROHAFYA.",
        )


# =====================================================================
# Codes à usage unique par e-mail
# =====================================================================

def active_users_by_email(email):
    email = normalize_email(email)
    if not email:
        return []
    users = db.session.execute(select(User).filter(func.lower(User.email) == email)).scalars().all()
    return [user for user in users if user.active]


def issue_email_otp(email, purpose="login"):
    email = normalize_email(email)
    if not email or "@" not in email:
        raise SaasError("Adresse e-mail invalide.", 400)

    since = now() - datetime.timedelta(hours=1)
    recent = db.session.execute(
        select(func.count(EmailOtp.id)).filter(EmailOtp.email == email, EmailOtp.created_at >= since)
    ).scalar()
    if recent >= OTP_MAX_REQUESTS_PER_HOUR:
        raise SaasError("Trop de codes demandés. Réessayez dans une heure.", 429)

    code = "".join(str(secrets.randbelow(10)) for _ in range(OTP_LENGTH))
    db.session.add(
        EmailOtp(
            email=email,
            code_hash=keyed_hash(f"{email}:{code}"),
            purpose=purpose,
            expires_at=now() + datetime.timedelta(minutes=OTP_TTL_MINUTES),
        )
    )
    db.session.commit()

    sent = send_mail(
        email,
        "Votre code de connexion ROHAFYA",
        "emails/otp_code.html",
        code=code,
        minutes=OTP_TTL_MINUTES,
        email=email,
    )
    if not sent:
        raise SaasError("Impossible d'envoyer l'e-mail. Vérifiez l'adresse et réessayez.", 502)
    return True


def verify_email_otp(email, code, consume=True):
    """Vérifie le dernier code envoyé. `consume=False` le laisse valable (étape « créer mon compte »)."""
    email = normalize_email(email)
    otp = db.session.execute(
        select(EmailOtp)
        .filter(EmailOtp.email == email, EmailOtp.used_at.is_(None), EmailOtp.expires_at > now())
        .order_by(EmailOtp.id.desc())
        .limit(1)
    ).scalar_one_or_none()
    if not otp:
        raise SaasError("Code expiré ou inexistant. Demandez un nouveau code.", 400)
    if otp.attempts >= OTP_MAX_ATTEMPTS:
        raise SaasError("Trop d'essais. Demandez un nouveau code.", 429)

    otp.attempts += 1
    expected = keyed_hash(f"{email}:{(code or '').strip()}")
    if not hmac.compare_digest(expected, otp.code_hash):
        db.session.commit()
        raise SaasError("Code incorrect.", 400)

    if consume:
        otp.used_at = now()
    db.session.commit()
    return True


# =====================================================================
# Notifications, e-mails, audit
# =====================================================================

def notify_user(user_id, title, message):
    if not user_id:
        return
    try:
        types = db.session.execute(select(Notifications_Type).filter_by(name="INFOS")).scalar_one_or_none()
        if not types:
            types = Notifications_Type(name="INFOS", description="Informations Type Of All Notifications.")
            db.session.add(types)
            db.session.flush()
        db.session.add(
            Notifications(types=types.id, title=title, message=message[:255], created_at=now(), all_users=False, user_id=user_id)
        )
        db.session.commit()
    except Exception as exc:  # une notification ne doit jamais faire échouer l'action principale
        db.session.rollback()
        log.error("Notification impossible pour l'utilisateur %s : %s", user_id, exc)


def send_mail(to, subject, template, **context):
    try:
        mail = Mail(current_app)
        message = Message(subject, recipients=[to], sender=current_app.config.get("MAIL_DEFAULT_SENDER"))
        message.html = render_template(template, front_url=front_url(), **context)
        mail.send(message)
        return True
    except Exception as exc:
        log.error("Envoi d'e-mail impossible à %s : %s", to, exc)
        return False


def audit(action, tenant_id=None, user_id=None, target=None, details=None, commit=True):
    db.session.add(AuditLog(action=action, tenant_id=tenant_id, user_id=user_id, target=target, details=details))
    if commit:
        db.session.commit()


def doctor_links(doctor):
    return db.session.execute(select(PractitionerLink).filter_by(doctor_id=doctor.id)).scalars().all()


def features_for(user):
    """Fonctions visibles côté front selon les établissements de l'utilisateur."""
    doctor = doctor_of_user(user.id)
    commissions = False
    if doctor:
        if is_legacy_matricule(doctor.DoctorFederationID):
            commissions = bool(default_gnuhealth_tenant().setting("commissions_enabled"))
        for link in doctor_links(doctor):
            if link.tenant and link.tenant.source_type == SOURCE_GNUHEALTH and link.tenant.setting("commissions_enabled"):
                commissions = True
    return {"commissions": commissions}


def ensure_tenant_admin(user, tenant):
    add_role(user, ROLE_TENANT_ADMIN)
    member = db.session.execute(select(TenantMember).filter_by(tenant_id=tenant.id, user_id=user.id)).scalar_one_or_none()
    if not member:
        db.session.add(TenantMember(tenant_id=tenant.id, user_id=user.id))


def revoke_link(link, actor_id):
    set_link_status(link, LINK_REVOKED, actor_id)


def create_user(email, first_name, last_name, role_name):
    """Crée un compte identifié par son e-mail (connexion par code) ; mot de passe aléatoire inutilisé."""
    import string

    alphabet = string.ascii_letters
    while True:
        username = "".join(secrets.choice(alphabet) for _ in range(10))
        if not db.session.execute(select(User.id).filter_by(username=username)).first():
            break
    user = User(
        username=username,
        email=normalize_email(email),
        first_name=(first_name or "").strip()[:100],
        last_name=(last_name or "").strip()[:100],
        password=current_app.user_manager.hash_password(secrets.token_urlsafe(24)),
        email_confirmed_at=now(),
    )
    db.session.add(user)
    add_role(user, role_name)
    db.session.flush()
    return user


def qr_png_base64(data):
    """QR code en PNG encodé base64 (affichable directement dans une balise <img>)."""
    import base64
    import io

    import qrcode

    image = qrcode.make(data, box_size=8, border=2)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


def link_view(tenant, local_ref, link=None):
    """Représentation d'un lien, y compris le lien implicite GNU Health (sans ligne en base)."""
    if link is not None:
        return link.to_dict()
    return {
        "id": None,
        "patient_id": None,
        "tenant_id": tenant.id,
        "establishment": tenant.display_name,
        "local_ref": local_ref,
        "status": LINK_ACTIVE,
        "method": "legacy",
        "verified_at": None,
        "created_at": None,
        "updated_at": None,
    }


def patient_link_views(patient):
    """Liens d'un patient pour son écran « Mes établissements » (actifs, en attente et implicites)."""
    if not patient:
        return []
    links = db.session.execute(
        select(PatientLink).filter(PatientLink.patient_id == patient.id, PatientLink.status != LINK_REVOKED)
    ).scalars().all()
    views = [link.to_dict() for link in links]
    if is_legacy_matricule(patient.PatientFederationID):
        gnu = default_gnuhealth_tenant()
        explicit = any(link.tenant_id == gnu.id and link.local_ref == patient.PatientFederationID for link in links)
        revoked = db.session.execute(
            select(PatientLink.id).filter_by(tenant_id=gnu.id, local_ref=patient.PatientFederationID, status=LINK_REVOKED)
        ).first()
        if not explicit and not revoked:
            views.insert(0, link_view(gnu, patient.PatientFederationID))
    return views
