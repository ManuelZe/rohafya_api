"""Back-office d'un établissement : ses patients, rattachements, QR codes, médecins, données et réglages.

Accessible aux administrateurs de l'établissement (TenantMember) et au super-administrateur.
"""
from flask import Blueprint, g, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import current_user
from sqlalchemy import func, or_, select

from Rohafya import db
from Rohafya.accounts.models import Doctors, Patients
from .constants import (
    LINK_ACTIVE,
    LINK_PENDING,
    LINK_REVOKED,
    LINK_STATUSES,
    PATIENT_ID_PREFIXES,
    RECORD_KINDS,
    SOURCE_GNUHEALTH,
    TENANT_EDITABLE_SETTINGS,
)
from .decorators import register_error_handler, tenant_admin_required
from .models import AuditLog, LinkToken, PatientLink, PractitionerLink, TenantMember, TenantPatient, TenantRecord
from .services import (
    SaasError,
    audit,
    generate_api_key,
    issue_link_token,
    now,
    qr_png_base64,
    set_link_status,
)

tenant_admin = Blueprint("tenant_admin", __name__, url_prefix="/saas/admin/tenants")
register_error_handler(tenant_admin)

MAX_PAGE_SIZE = 100


def _json():
    return request.get_json(silent=True) or {}


def _page():
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(MAX_PAGE_SIZE, max(1, int(request.args.get("page_size", 25))))
    except ValueError:
        page, size = 1, 25
    return page, size


def _like(query):
    return f"%{query.strip().lower()}%"


def _legacy_patients_filter():
    """Patients importés de GNU Health avant le SaaS (matricule réel)."""
    return Patients.PatientFederationID.isnot(None), Patients.PatientFederationID != "", *(~Patients.PatientFederationID.startswith(prefix) for prefix in PATIENT_ID_PREFIXES)


def tenant_stats(tenant):
    links = dict(
        db.session.execute(
            select(PatientLink.status, func.count(PatientLink.id)).filter_by(tenant_id=tenant.id).group_by(PatientLink.status)
        ).all()
    )
    records = dict(
        db.session.execute(
            select(TenantRecord.kind, func.count(TenantRecord.id)).filter_by(tenant_id=tenant.id).group_by(TenantRecord.kind)
        ).all()
    )
    known = db.session.execute(select(func.count(TenantPatient.id)).filter_by(tenant_id=tenant.id)).scalar()
    active_links = links.get(LINK_ACTIVE, 0)
    if tenant.source_type == SOURCE_GNUHEALTH:
        legacy = db.session.execute(select(func.count(Patients.id)).filter(*_legacy_patients_filter())).scalar()
        known += legacy
        active_links += legacy
    last_record = db.session.execute(select(func.max(TenantRecord.updated_at)).filter_by(tenant_id=tenant.id)).scalar()
    tokens_active = db.session.execute(
        select(func.count(LinkToken.id)).filter(
            LinkToken.tenant_id == tenant.id, LinkToken.used_at.is_(None), LinkToken.revoked_at.is_(None), LinkToken.expires_at > now()
        )
    ).scalar()
    doctors = db.session.execute(select(func.count(PractitionerLink.id)).filter_by(tenant_id=tenant.id)).scalar()
    admins = db.session.execute(select(func.count(TenantMember.id)).filter_by(tenant_id=tenant.id)).scalar()
    return {
        "patients_known": known,
        "links_active": active_links,
        "links_pending": links.get(LINK_PENDING, 0),
        "links_revoked": links.get(LINK_REVOKED, 0),
        "tokens_active": tokens_active,
        "records": {kind: records.get(kind, 0) for kind in RECORD_KINDS},
        "records_total": sum(records.values()),
        "last_record_at": last_record,
        "doctors": doctors,
        "admins": admins,
    }


# =====================================================================
# Établissement et réglages
# =====================================================================

@tenant_admin.route("/<int:tenant_id>", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def get_tenant(tenant_id):
    return jsonify(g.tenant.to_dict())


@tenant_admin.route("/<int:tenant_id>/settings", methods=["PUT"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def update_settings(tenant_id):
    data = _json()
    settings = dict(g.tenant.settings or {})
    for key in TENANT_EDITABLE_SETTINGS:
        if key not in data:
            continue
        value = data[key]
        if key in ("result_access_days", "link_token_days"):
            try:
                value = int(value)
            except (TypeError, ValueError):
                raise SaasError(f"{key} doit être un nombre de jours.", 400)
            if not 1 <= value <= 3650:
                raise SaasError(f"{key} doit être compris entre 1 et 3650 jours.", 400)
        elif key == "block_unpaid_results":
            value = bool(value)
        else:
            value = str(value or "").strip()[:200]
        settings[key] = value
    g.tenant.settings = settings
    g.tenant.updated_at = now()
    audit("tenant.settings_updated", tenant_id=g.tenant.id, user_id=current_user.id, details={k: data[k] for k in data if k in TENANT_EDITABLE_SETTINGS}, commit=False)
    db.session.commit()
    return jsonify(g.tenant.to_dict())


@tenant_admin.route("/<int:tenant_id>/api-key", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def rotate_api_key(tenant_id):
    if g.tenant.source_type == SOURCE_GNUHEALTH:
        raise SaasError("Cet établissement est relié directement à GNU Health : aucune clé d'API n'est nécessaire.", 400)
    key, key_hash, hint = generate_api_key()
    g.tenant.api_key_hash = key_hash
    g.tenant.api_key_hint = hint
    g.tenant.updated_at = now()
    audit("tenant.api_key_rotated", tenant_id=g.tenant.id, user_id=current_user.id, commit=False)
    db.session.commit()
    return {"api_key": key, "message": "Copiez cette clé maintenant : elle ne sera plus jamais affichée."}


@tenant_admin.route("/<int:tenant_id>/dashboard", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def dashboard(tenant_id):
    stats = tenant_stats(g.tenant)
    recent = db.session.execute(
        select(AuditLog).filter_by(tenant_id=g.tenant.id).order_by(AuditLog.id.desc()).limit(8)
    ).scalars().all()
    stats["recent_activity"] = [entry.to_dict() for entry in recent]
    stats["tenant"] = g.tenant.to_dict(with_settings=False)
    return jsonify(stats)


# =====================================================================
# Patients de l'établissement
# =====================================================================

def _explicit_links(tenant_id, local_refs):
    if not local_refs:
        return {}
    rows = db.session.execute(
        select(PatientLink).filter(PatientLink.tenant_id == tenant_id, PatientLink.local_ref.in_(local_refs))
    ).scalars().all()
    return {link.local_ref: link for link in rows}


def _record_counts(tenant_id, local_refs):
    if not local_refs:
        return {}
    rows = db.session.execute(
        select(TenantRecord.local_ref, func.count(TenantRecord.id))
        .filter(TenantRecord.tenant_id == tenant_id, TenantRecord.local_ref.in_(local_refs))
        .group_by(TenantRecord.local_ref)
    ).all()
    return dict(rows)


def _patient_row(tenant, local_ref, first_name, last_name, email, birth_date, gender, link, implicit, records):
    if link is not None:
        status = link.status
        account = link.to_dict()
    elif implicit is not None:
        status = LINK_ACTIVE
        account = {"patient_id": implicit.id, "patient_email": implicit.PatientEmail, "method": "legacy"}
    else:
        status = "none"
        account = None
    return {
        "local_ref": local_ref,
        "first_name": first_name,
        "last_name": last_name,
        "email": email,
        "birth_date": birth_date,
        "gender": gender,
        "link_status": status,
        "link_id": link.id if link is not None else None,
        "account": account,
        "records": records,
    }


@tenant_admin.route("/<int:tenant_id>/patients", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_patients(tenant_id):
    tenant = g.tenant
    page, size = _page()
    query = (request.args.get("q") or "").strip()
    items = []

    if tenant.source_type == SOURCE_GNUHEALTH:
        stmt = select(Patients).filter(*_legacy_patients_filter())
        if query:
            stmt = stmt.filter(
                or_(
                    func.lower(Patients.PatientName).like(_like(query)),
                    func.lower(Patients.PatientLastname).like(_like(query)),
                    func.lower(Patients.PatientEmail).like(_like(query)),
                    func.lower(Patients.PatientFederationID).like(_like(query)),
                )
            )
        total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = db.session.execute(stmt.order_by(Patients.PatientLastname, Patients.PatientName).offset((page - 1) * size).limit(size)).scalars().all()
        links = _explicit_links(tenant.id, [p.PatientFederationID for p in rows])
        for patient in rows:
            link = links.get(patient.PatientFederationID)
            items.append(
                _patient_row(
                    tenant,
                    patient.PatientFederationID,
                    patient.PatientName,
                    patient.PatientLastname,
                    patient.PatientEmail,
                    patient.PatientDOB,
                    patient.PatientGender,
                    link,
                    None if link is not None else patient,
                    None,
                )
            )
    else:
        stmt = select(TenantPatient).filter_by(tenant_id=tenant.id)
        if query:
            stmt = stmt.filter(
                or_(
                    func.lower(TenantPatient.first_name).like(_like(query)),
                    func.lower(TenantPatient.last_name).like(_like(query)),
                    func.lower(TenantPatient.email).like(_like(query)),
                    func.lower(TenantPatient.local_ref).like(_like(query)),
                )
            )
        total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
        rows = db.session.execute(stmt.order_by(TenantPatient.last_name, TenantPatient.first_name).offset((page - 1) * size).limit(size)).scalars().all()
        refs = [p.local_ref for p in rows]
        links = _explicit_links(tenant.id, refs)
        counts = _record_counts(tenant.id, refs)
        for patient in rows:
            items.append(
                _patient_row(
                    tenant,
                    patient.local_ref,
                    patient.first_name,
                    patient.last_name,
                    patient.email,
                    patient.birth_date,
                    patient.gender,
                    links.get(patient.local_ref),
                    None,
                    counts.get(patient.local_ref, 0),
                )
            )
    return jsonify({"items": items, "total": total, "page": page, "page_size": size})


@tenant_admin.route("/<int:tenant_id>/patients", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def create_patient(tenant_id):
    """Saisie manuelle d'un dossier (établissement sans logiciel relié)."""
    from .ingestion import upsert_patient

    if g.tenant.source_type == SOURCE_GNUHEALTH:
        raise SaasError("Les patients de cet établissement proviennent de GNU Health.", 400)
    patient, created = upsert_patient(g.tenant, _json(), "admin")
    audit("patient.saved", tenant_id=g.tenant.id, user_id=current_user.id, target=patient.local_ref, commit=False)
    db.session.commit()
    return jsonify({"patient": patient.to_dict(), "created": created}), 201 if created else 200


@tenant_admin.route("/<int:tenant_id>/patients/<path:local_ref>", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def patient_detail(tenant_id, local_ref):
    tenant = g.tenant
    known = db.session.execute(select(TenantPatient).filter_by(tenant_id=tenant.id, local_ref=local_ref)).scalar_one_or_none()
    legacy = None
    if tenant.source_type == SOURCE_GNUHEALTH:
        legacy = db.session.execute(select(Patients).filter_by(PatientFederationID=local_ref)).scalar_one_or_none()
    if not known and not legacy:
        raise SaasError("Dossier introuvable dans cet établissement.", 404)

    link = db.session.execute(select(PatientLink).filter_by(tenant_id=tenant.id, local_ref=local_ref)).scalar_one_or_none()
    records = db.session.execute(
        select(TenantRecord).filter_by(tenant_id=tenant.id, local_ref=local_ref).order_by(TenantRecord.validated_at.desc())
    ).scalars().all()
    tokens = db.session.execute(
        select(LinkToken).filter_by(tenant_id=tenant.id, local_ref=local_ref).order_by(LinkToken.id.desc()).limit(10)
    ).scalars().all()
    source = known.to_dict() if known else {
        "local_ref": local_ref,
        "first_name": legacy.PatientName,
        "last_name": legacy.PatientLastname,
        "email": legacy.PatientEmail,
        "phone": legacy.PatientPhone,
        "birth_date": legacy.PatientDOB,
        "gender": legacy.PatientGender,
        "source": "gnuhealth",
    }
    row = _patient_row(
        tenant, local_ref, source["first_name"], source["last_name"], source["email"], source["birth_date"], source["gender"],
        link, legacy if link is None else None, len(records),
    )
    row.update({"phone": source.get("phone"), "source": source.get("source")})
    return jsonify(
        {
            "patient": row,
            "records": [record.to_summary() for record in records],
            "tokens": [token.to_dict() for token in tokens],
        }
    )


# =====================================================================
# Rattachements et QR codes
# =====================================================================

@tenant_admin.route("/<int:tenant_id>/links", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_links(tenant_id):
    status = request.args.get("status")
    page, size = _page()
    stmt = select(PatientLink).filter_by(tenant_id=g.tenant.id)
    if status in LINK_STATUSES:
        stmt = stmt.filter_by(status=status)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    links = db.session.execute(stmt.order_by(PatientLink.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return jsonify({"items": [link.to_dict() for link in links], "total": total, "page": page, "page_size": size})


def _tenant_link(link_id):
    link = db.session.get(PatientLink, link_id)
    if not link or link.tenant_id != g.tenant.id:
        raise SaasError("Rattachement introuvable.", 404)
    return link


@tenant_admin.route("/<int:tenant_id>/links/<int:link_id>/approve", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def approve_link(tenant_id, link_id):
    link = _tenant_link(link_id)
    set_link_status(link, LINK_ACTIVE, current_user.id)
    return jsonify(link.to_dict())


@tenant_admin.route("/<int:tenant_id>/links/<int:link_id>/revoke", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def revoke_link(tenant_id, link_id):
    link = _tenant_link(link_id)
    set_link_status(link, LINK_REVOKED, current_user.id)
    return jsonify(link.to_dict())


@tenant_admin.route("/<int:tenant_id>/link-tokens", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def create_link_token(tenant_id):
    data = _json()
    local_ref = (data.get("local_ref") or "").strip()
    email = data.get("email")
    if not email and local_ref:
        known = db.session.execute(select(TenantPatient).filter_by(tenant_id=g.tenant.id, local_ref=local_ref)).scalar_one_or_none()
        email = known.email if known else None
    result = issue_link_token(g.tenant, local_ref, email_hint=email, created_by=current_user.id)
    if not result.get("already_linked"):
        result["qr_png"] = qr_png_base64(result["url"])
        result["establishment"] = g.tenant.display_name
    return jsonify(result)


@tenant_admin.route("/<int:tenant_id>/link-tokens", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_link_tokens(tenant_id):
    page, size = _page()
    stmt = select(LinkToken).filter_by(tenant_id=g.tenant.id)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    tokens = db.session.execute(stmt.order_by(LinkToken.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return jsonify({"items": [token.to_dict() for token in tokens], "total": total, "page": page, "page_size": size})


@tenant_admin.route("/<int:tenant_id>/link-tokens/<int:token_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def revoke_link_token(tenant_id, token_id):
    token = db.session.get(LinkToken, token_id)
    if not token or token.tenant_id != g.tenant.id:
        raise SaasError("QR code introuvable.", 404)
    if token.status == "active":
        token.revoked_at = now()
        audit("link_token.revoked", tenant_id=g.tenant.id, user_id=current_user.id, target=token.local_ref, commit=False)
        db.session.commit()
    return jsonify(token.to_dict())


# =====================================================================
# Médecins de l'établissement
# =====================================================================

@tenant_admin.route("/<int:tenant_id>/doctors", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_doctors(tenant_id):
    links = db.session.execute(select(PractitionerLink).filter_by(tenant_id=g.tenant.id).order_by(PractitionerLink.id.desc())).scalars().all()
    return jsonify([link.to_dict() for link in links])


@tenant_admin.route("/<int:tenant_id>/doctors", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def add_doctor(tenant_id):
    data = _json()
    identifier = (data.get("matricule") or data.get("email") or "").strip()
    if not identifier:
        raise SaasError("Indiquez le matricule ou l'e-mail du médecin.", 400)
    doctor = db.session.execute(
        select(Doctors).filter(
            or_(func.lower(Doctors.DoctorFederationID) == identifier.lower(), func.lower(Doctors.DoctorEmail) == identifier.lower())
        ).limit(1)
    ).scalar_one_or_none()
    if not doctor:
        raise SaasError("Aucun médecin ROHAFYA ne correspond à ce matricule ou à cet e-mail.", 404)
    existing = db.session.execute(select(PractitionerLink).filter_by(tenant_id=g.tenant.id, doctor_id=doctor.id)).scalar_one_or_none()
    if existing:
        return jsonify(existing.to_dict())
    link = PractitionerLink(tenant_id=g.tenant.id, doctor_id=doctor.id, local_ref=(data.get("local_ref") or None))
    db.session.add(link)
    db.session.flush()
    audit("doctor.linked", tenant_id=g.tenant.id, user_id=current_user.id, target=doctor.DoctorFederationID, commit=False)
    db.session.commit()
    return jsonify(link.to_dict()), 201


@tenant_admin.route("/<int:tenant_id>/doctors/<int:link_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def remove_doctor(tenant_id, link_id):
    link = db.session.get(PractitionerLink, link_id)
    if not link or link.tenant_id != g.tenant.id:
        raise SaasError("Médecin introuvable dans cet établissement.", 404)
    target = link.doctor.DoctorFederationID if link.doctor else None
    db.session.delete(link)
    audit("doctor.unlinked", tenant_id=g.tenant.id, user_id=current_user.id, target=target, commit=False)
    db.session.commit()
    return {"message": "Médecin retiré de l'établissement."}


# =====================================================================
# Données reçues et journal
# =====================================================================

@tenant_admin.route("/<int:tenant_id>/records", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_records(tenant_id):
    page, size = _page()
    kind = request.args.get("kind")
    query = (request.args.get("q") or "").strip()
    stmt = select(TenantRecord).filter_by(tenant_id=g.tenant.id)
    if kind in RECORD_KINDS:
        stmt = stmt.filter_by(kind=kind)
    if query:
        stmt = stmt.filter(or_(func.lower(TenantRecord.code).like(_like(query)), func.lower(TenantRecord.local_ref).like(_like(query))))
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    records = db.session.execute(stmt.order_by(TenantRecord.updated_at.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return jsonify({"items": [record.to_summary() for record in records], "total": total, "page": page, "page_size": size})


@tenant_admin.route("/<int:tenant_id>/audit", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def tenant_audit(tenant_id):
    page, size = _page()
    stmt = select(AuditLog).filter_by(tenant_id=g.tenant.id)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    entries = db.session.execute(stmt.order_by(AuditLog.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    return jsonify({"items": [entry.to_dict() for entry in entries], "total": total, "page": page, "page_size": size})
