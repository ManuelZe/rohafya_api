"""Console du super-administrateur : tous les établissements, leurs administrateurs, les comptes et le journal."""
import re

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import current_user
from sqlalchemy import func, or_, select

from Rohafya import db
from Rohafya.accounts.models import Doctors, Patients, Role, User, UserRoles
from .constants import (
    DEFAULT_SETTINGS,
    LINK_ACTIVE,
    LINK_PENDING,
    PDF_QUOTA_LIMITS,
    RECORD_KINDS,
    ROLE_LEGACY_ADMIN,
    ROLE_SUPER_ADMIN,
    ROLE_TENANT_ADMIN,
    SOURCE_GNUHEALTH,
    SOURCES,
)
from .decorators import register_error_handler, super_admin_required
from .models import AuditLog, PatientLink, Tenant, TenantMember, TenantRecord
from .services import (
    SaasError,
    active_users_by_email,
    add_role,
    audit,
    create_user,
    ensure_tenant_admin,
    generate_api_key,
    normalize_email,
    now,
    send_mail,
)
from .tenant_admin_routes import tenant_stats

super_admin = Blueprint("super_admin", __name__, url_prefix="/saas/super")
register_error_handler(super_admin)

SLUG_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{1,78}[a-z0-9]$")


def _json():
    return request.get_json(silent=True) or {}


def _page():
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(100, max(1, int(request.args.get("page_size", 25))))
    except ValueError:
        page, size = 1, 25
    return page, size


def _slugify(value):
    value = (value or "").strip().lower()
    for src, dst in (("àâä", "a"), ("éèêë", "e"), ("îï", "i"), ("ôö", "o"), ("ùûü", "u"), ("ç", "c")):
        for char in src:
            value = value.replace(char, dst)
    return re.sub(r"[^a-z0-9]+", "-", value).strip("-")[:80]


def _tenant(tenant_id):
    tenant = db.session.get(Tenant, tenant_id)
    if not tenant:
        raise SaasError("Établissement introuvable.", 404)
    return tenant


def _apply_tenant_fields(tenant, data, creating=False):
    if "name" in data or creating:
        name = (data.get("name") or "").strip()
        if not name:
            raise SaasError("Le nom de l'établissement est obligatoire.", 400)
        tenant.name = name[:200]
    if "slug" in data or creating:
        slug = _slugify(data.get("slug") or tenant.name)
        if not SLUG_PATTERN.match(slug):
            raise SaasError("Identifiant invalide : lettres minuscules, chiffres et tirets (3 caractères minimum).", 400)
        clash = db.session.execute(select(Tenant.id).filter(Tenant.slug == slug, Tenant.id != (tenant.id or 0))).first()
        if clash:
            raise SaasError("Cet identifiant est déjà utilisé par un autre établissement.", 409)
        tenant.slug = slug
    if "source_type" in data or creating:
        source = data.get("source_type") or "api"
        if source not in SOURCES:
            raise SaasError(f"Source inconnue. Valeurs possibles : {', '.join(SOURCES)}.", 400)
        tenant.source_type = source
    if "is_active" in data:
        tenant.is_active = bool(data["is_active"])
    if isinstance(data.get("settings"), dict):
        settings = dict(tenant.settings or {})
        for key, value in data["settings"].items():
            if key not in DEFAULT_SETTINGS:
                continue
            if key in PDF_QUOTA_LIMITS:
                low, high = PDF_QUOTA_LIMITS[key]
                try:
                    value = int(value)
                except (TypeError, ValueError):
                    raise SaasError(f"{key} doit être un nombre entier.", 400)
                if not low <= value <= high:
                    raise SaasError(f"{key} doit être compris entre {low} et {high}.", 400)
            elif isinstance(DEFAULT_SETTINGS[key], bool):
                value = bool(value)
            settings[key] = value
        tenant.settings = settings


def _tenant_row(tenant):
    row = tenant.to_dict()
    row["stats"] = tenant_stats(tenant)
    return row


# =====================================================================
# Vue d'ensemble
# =====================================================================

@super_admin.route("/stats", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def stats():
    def count(model, *filters):
        return db.session.execute(select(func.count(model.id)).filter(*filters)).scalar()

    records = dict(db.session.execute(select(TenantRecord.kind, func.count(TenantRecord.id)).group_by(TenantRecord.kind)).all())
    recent = db.session.execute(select(AuditLog).order_by(AuditLog.id.desc()).limit(10)).scalars().all()
    return jsonify(
        {
            "tenants": count(Tenant),
            "tenants_active": count(Tenant, Tenant.is_active.is_(True)),
            "users": count(User),
            "patients": count(Patients),
            "doctors": count(Doctors),
            "admins": count(TenantMember),
            "links_active": count(PatientLink, PatientLink.status == LINK_ACTIVE),
            "links_pending": count(PatientLink, PatientLink.status == LINK_PENDING),
            "records": {kind: records.get(kind, 0) for kind in RECORD_KINDS},
            "records_total": sum(records.values()),
            "recent_activity": [entry.to_dict() for entry in recent],
        }
    )


# =====================================================================
# Établissements
# =====================================================================

@super_admin.route("/tenants", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def list_tenants():
    tenants = db.session.execute(select(Tenant).order_by(Tenant.name)).scalars().all()
    return jsonify([_tenant_row(tenant) for tenant in tenants])


@super_admin.route("/tenants", methods=["POST"])
@cross_origin(supports_credentials=True)
@super_admin_required
def create_tenant():
    data = _json()
    tenant = Tenant(settings=dict(DEFAULT_SETTINGS))
    _apply_tenant_fields(tenant, data, creating=True)
    if tenant.source_type == SOURCE_GNUHEALTH:
        existing = db.session.execute(select(Tenant.id).filter_by(source_type=SOURCE_GNUHEALTH)).first()
        if existing:
            raise SaasError("Un établissement relié directement à GNU Health existe déjà.", 409)
    api_key = None
    if tenant.source_type != SOURCE_GNUHEALTH:
        api_key, tenant.api_key_hash, tenant.api_key_hint = generate_api_key()
    db.session.add(tenant)
    db.session.flush()
    audit("tenant.created", tenant_id=tenant.id, user_id=current_user.id, target=tenant.slug, commit=False)
    db.session.commit()
    body = _tenant_row(tenant)
    body["api_key"] = api_key
    return jsonify(body), 201


@super_admin.route("/tenants/<int:tenant_id>", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def get_tenant(tenant_id):
    return jsonify(_tenant_row(_tenant(tenant_id)))


@super_admin.route("/tenants/<int:tenant_id>", methods=["PUT"])
@cross_origin(supports_credentials=True)
@super_admin_required
def update_tenant(tenant_id):
    tenant = _tenant(tenant_id)
    data = _json()
    if data.get("source_type") == SOURCE_GNUHEALTH and tenant.source_type != SOURCE_GNUHEALTH:
        raise SaasError("La source GNU Health est réservée à l'établissement historique.", 400)
    _apply_tenant_fields(tenant, data)
    tenant.updated_at = now()
    audit("tenant.updated", tenant_id=tenant.id, user_id=current_user.id, target=tenant.slug, commit=False)
    db.session.commit()
    return jsonify(_tenant_row(tenant))


@super_admin.route("/tenants/<int:tenant_id>/api-key", methods=["POST"])
@cross_origin(supports_credentials=True)
@super_admin_required
def rotate_api_key(tenant_id):
    tenant = _tenant(tenant_id)
    if tenant.source_type == SOURCE_GNUHEALTH:
        raise SaasError("Cet établissement est relié directement à GNU Health : aucune clé d'API n'est nécessaire.", 400)
    key, tenant.api_key_hash, tenant.api_key_hint = generate_api_key()
    tenant.updated_at = now()
    audit("tenant.api_key_rotated", tenant_id=tenant.id, user_id=current_user.id, commit=False)
    db.session.commit()
    return {"api_key": key, "message": "Copiez cette clé maintenant : elle ne sera plus jamais affichée."}


@super_admin.route("/tenants/<int:tenant_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@super_admin_required
def delete_tenant(tenant_id):
    """Suppression définitive d'un établissement et de toutes ses données dans ROHAFYA.

    Le corps doit rappeler l'identifiant (slug) de l'établissement : {"confirm_slug": "..."}.
    Les comptes des patients et des médecins sont conservés (ils peuvent avoir d'autres
    établissements) ; seuls leurs liens avec cet établissement disparaissent. Le journal
    d'audit est conservé.
    """
    from .models import LinkToken, PdfImport, PractitionerLink, TenantPatient

    tenant = _tenant(tenant_id)
    if tenant.source_type == SOURCE_GNUHEALTH:
        raise SaasError("L'établissement relié à GNU Health ne peut pas être supprimé (il peut être suspendu).", 400)
    if (_json().get("confirm_slug") or "").strip() != tenant.slug:
        raise SaasError(f"Confirmation incorrecte : saisissez exactement « {tenant.slug} ».", 400)

    counts = {}
    for label, model in (
        ("imports_pdf", PdfImport),
        ("donnees", TenantRecord),
        ("dossiers", TenantPatient),
        ("rattachements", PatientLink),
        ("medecins", PractitionerLink),
        ("qr_codes", LinkToken),
    ):
        rows = db.session.execute(select(model).filter_by(tenant_id=tenant.id)).scalars().all()
        counts[label] = len(rows)
        for row in rows:
            db.session.delete(row)
        db.session.flush()

    members = db.session.execute(select(TenantMember).filter_by(tenant_id=tenant.id)).scalars().all()
    counts["administrateurs"] = len(members)
    admin_role = db.session.execute(select(Role).filter_by(name=ROLE_TENANT_ADMIN)).scalar_one_or_none()
    for member in members:
        user_id = member.user_id
        db.session.delete(member)
        db.session.flush()
        still_admin = db.session.execute(select(TenantMember.id).filter_by(user_id=user_id)).first()
        user = db.session.get(User, user_id)
        if not still_admin and admin_role and user and admin_role in user.roles:
            user.roles.remove(admin_role)

    # Le journal reste lisible : on garde la trace de l'établissement dans les détails.
    for entry in db.session.execute(select(AuditLog).filter_by(tenant_id=tenant.id)).scalars():
        entry.details = {**(entry.details or {}), "etablissement_supprime": tenant.name}
        entry.tenant_id = None

    name, slug = tenant.name, tenant.slug
    db.session.delete(tenant)
    audit("tenant.deleted", user_id=current_user.id, target=slug, details={"name": name, **counts}, commit=False)
    db.session.commit()
    return {"message": f"L'établissement {name} a été supprimé.", "deleted": counts}


# =====================================================================
# Administrateurs d'établissement
# =====================================================================

def _member_view(member):
    user = member.user
    return {
        "user_id": member.user_id,
        "tenant_id": member.tenant_id,
        "email": user.email if user else None,
        "first_name": user.first_name if user else None,
        "last_name": user.last_name if user else None,
        "active": bool(user.active) if user else False,
        "created_at": member.created_at,
    }


@super_admin.route("/tenants/<int:tenant_id>/admins", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def list_admins(tenant_id):
    _tenant(tenant_id)
    members = db.session.execute(select(TenantMember).filter_by(tenant_id=tenant_id).order_by(TenantMember.id)).scalars().all()
    return jsonify([_member_view(member) for member in members])


@super_admin.route("/tenants/<int:tenant_id>/admins", methods=["POST"])
@cross_origin(supports_credentials=True)
@super_admin_required
def add_admin(tenant_id):
    tenant = _tenant(tenant_id)
    data = _json()
    email = normalize_email(data.get("email"))
    if not email or "@" not in email:
        raise SaasError("Adresse e-mail invalide.", 400)
    users = active_users_by_email(email)
    if len(users) > 1:
        raise SaasError("Plusieurs comptes utilisent cette adresse : choisissez une adresse unique pour l'administrateur.", 409)
    if users:
        user = users[0]
    else:
        first_name, last_name = (data.get("first_name") or "").strip(), (data.get("last_name") or "").strip()
        if not first_name or not last_name:
            raise SaasError("Nom et prénom obligatoires pour créer le compte de l'administrateur.", 400)
        user = create_user(email, first_name, last_name, ROLE_TENANT_ADMIN)
    ensure_tenant_admin(user, tenant)
    audit("tenant.admin_added", tenant_id=tenant.id, user_id=current_user.id, target=email, commit=False)
    db.session.commit()

    sent = send_mail(
        email,
        f"Vous administrez {tenant.display_name} sur ROHAFYA",
        "emails/admin_invite.html",
        first_name=user.first_name,
        establishment=tenant.display_name,
        email=email,
    )
    member = db.session.execute(select(TenantMember).filter_by(tenant_id=tenant.id, user_id=user.id)).scalar_one()
    body = _member_view(member)
    body["email_sent"] = sent
    return jsonify(body), 201


@super_admin.route("/tenants/<int:tenant_id>/admins/<int:user_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@super_admin_required
def remove_admin(tenant_id, user_id):
    member = db.session.execute(select(TenantMember).filter_by(tenant_id=tenant_id, user_id=user_id)).scalar_one_or_none()
    if not member:
        raise SaasError("Cet utilisateur n'administre pas cet établissement.", 404)
    email = member.user.email if member.user else None
    db.session.delete(member)
    db.session.flush()
    remaining = db.session.execute(select(TenantMember.id).filter_by(user_id=user_id)).first()
    if not remaining:
        role = db.session.execute(select(Role).filter_by(name=ROLE_TENANT_ADMIN)).scalar_one_or_none()
        user = db.session.get(User, user_id)
        if role and user and role in user.roles:
            user.roles.remove(role)
    audit("tenant.admin_removed", tenant_id=tenant_id, user_id=current_user.id, target=email, commit=False)
    db.session.commit()
    return {"message": "Administrateur retiré."}


# =====================================================================
# Comptes utilisateurs
# =====================================================================

def _user_view(user, memberships):
    roles = sorted(role.name for role in user.roles)
    return {
        "id": user.id,
        "username": user.username,
        "email": user.email,
        "first_name": user.first_name,
        "last_name": user.last_name,
        "active": bool(user.active),
        "roles": roles,
        "is_super_admin": bool({ROLE_SUPER_ADMIN, ROLE_LEGACY_ADMIN} & set(roles)),
        "admin_of": memberships.get(user.id, []),
        "patient_id": user.patients.id if user.patients else None,
        "doctor_id": user.doctor.id if user.doctor else None,
    }


@super_admin.route("/users", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def list_users():
    page, size = _page()
    query = (request.args.get("q") or "").strip().lower()
    role = request.args.get("role")
    stmt = select(User)
    if query:
        like = f"%{query}%"
        stmt = stmt.filter(
            or_(
                func.lower(User.email).like(like),
                func.lower(User.first_name).like(like),
                func.lower(User.last_name).like(like),
                func.lower(User.username).like(like),
            )
        )
    if role:
        stmt = stmt.join(UserRoles, UserRoles.user_id == User.id).join(Role, Role.id == UserRoles.role_id).filter(Role.name == role)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    users = db.session.execute(stmt.order_by(User.id.desc()).offset((page - 1) * size).limit(size)).scalars().unique().all()

    memberships = {}
    if users:
        rows = db.session.execute(
            select(TenantMember.user_id, Tenant.name).join(Tenant, Tenant.id == TenantMember.tenant_id).filter(TenantMember.user_id.in_([u.id for u in users]))
        ).all()
        for user_id, name in rows:
            memberships.setdefault(user_id, []).append(name)
    return jsonify({"items": [_user_view(user, memberships) for user in users], "total": total, "page": page, "page_size": size})


@super_admin.route("/users/<int:user_id>/active", methods=["PUT"])
@cross_origin(supports_credentials=True)
@super_admin_required
def set_user_active(user_id):
    user = db.session.get(User, user_id)
    if not user:
        raise SaasError("Utilisateur introuvable.", 404)
    if user.id == current_user.id:
        raise SaasError("Vous ne pouvez pas désactiver votre propre compte.", 400)
    user.active = bool(_json().get("active"))
    audit("user.activated" if user.active else "user.deactivated", user_id=current_user.id, target=user.email, commit=False)
    db.session.commit()
    return jsonify(_user_view(user, {}))


@super_admin.route("/users/<int:user_id>/super-admin", methods=["PUT"])
@cross_origin(supports_credentials=True)
@super_admin_required
def set_super_admin(user_id):
    user = db.session.get(User, user_id)
    if not user:
        raise SaasError("Utilisateur introuvable.", 404)
    enabled = bool(_json().get("enabled"))
    if not enabled and user.id == current_user.id:
        raise SaasError("Vous ne pouvez pas retirer vos propres droits de super-administrateur.", 400)
    if enabled:
        add_role(user, ROLE_SUPER_ADMIN)
    else:
        role = db.session.execute(select(Role).filter_by(name=ROLE_SUPER_ADMIN)).scalar_one_or_none()
        if role and role in user.roles:
            user.roles.remove(role)
    audit("user.super_admin_granted" if enabled else "user.super_admin_revoked", user_id=current_user.id, target=user.email, commit=False)
    db.session.commit()
    return jsonify(_user_view(user, {}))


# =====================================================================
# Journal global
# =====================================================================

@super_admin.route("/audit", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def global_audit():
    page, size = _page()
    stmt = select(AuditLog)
    tenant_id = request.args.get("tenant_id", type=int)
    if tenant_id:
        stmt = stmt.filter_by(tenant_id=tenant_id)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    entries = db.session.execute(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return jsonify({"items": [entry.to_dict() for entry in entries], "total": total, "page": page, "page_size": size})
