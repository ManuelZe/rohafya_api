"""Connexion par code e-mail, profil SaaS de l'utilisateur et rattachement de ses établissements."""
from flask import Blueprint, current_app, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import current_user
from sqlalchemy import select

from Rohafya import db
from .constants import LINK_REVOKED, ROLE_PATIENT
from .decorators import login_needed, register_error_handler
from .models import LinkToken, PatientLink, Tenant, TenantMember
from .services import (
    SaasError,
    active_users_by_email,
    audit,
    create_user,
    default_gnuhealth_tenant,
    ensure_patient_profile,
    features_for,
    is_legacy_matricule,
    is_super_admin,
    issue_email_otp,
    normalize_email,
    now,
    patient_link_views,
    patient_of_user,
    redeem_link_token,
    sha256,
    verify_email_otp,
)

saas_account = Blueprint("saas_account", __name__, url_prefix="/saas")
register_error_handler(saas_account)


def _json():
    return request.get_json(silent=True) or {}


# =====================================================================
# Connexion par code envoyé par e-mail
# =====================================================================

@saas_account.route("/auth/otp/request", methods=["POST"])
@cross_origin(supports_credentials=True)
def otp_request():
    issue_email_otp(_json().get("email"), purpose="login")
    return {"message": "Si l'adresse est valide, un code de connexion vient d'y être envoyé."}


@saas_account.route("/auth/otp/verify", methods=["POST"])
@cross_origin(supports_credentials=True)
def otp_verify():
    data = _json()
    email = normalize_email(data.get("email"))
    code = data.get("code")
    first_name = (data.get("first_name") or "").strip()
    last_name = (data.get("last_name") or "").strip()

    users = active_users_by_email(email)
    if len(users) > 1:
        raise SaasError(
            "Plusieurs comptes utilisent cette adresse. Connectez-vous avec votre nom d'utilisateur et votre mot de passe.",
            409,
        )

    if not users and not (first_name and last_name):
        # Le code est bon mais aucun compte n'existe : on le garde valable le temps de saisir nom et prénom.
        verify_email_otp(email, code, consume=False)
        return {"needs_registration": True}

    verify_email_otp(email, code)

    if users:
        user = users[0]
    else:
        user = create_user(email, first_name, last_name, ROLE_PATIENT)
        ensure_patient_profile(user)
        audit("account.created", user_id=user.id, target=email, commit=False)
    if not user.email_confirmed_at:
        user.email_confirmed_at = now()
    db.session.commit()
    return current_app.user_manager._do_login_user(user, remember_me=True)


# =====================================================================
# Profil SaaS de l'utilisateur connecté
# =====================================================================

@saas_account.route("/me", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def me():
    user = current_user
    if is_super_admin(user):
        tenants = db.session.execute(select(Tenant).order_by(Tenant.name)).scalars().all()
    else:
        tenants = db.session.execute(
            select(Tenant).join(TenantMember, TenantMember.tenant_id == Tenant.id).filter(TenantMember.user_id == user.id).order_by(Tenant.name)
        ).scalars().all()
    patient = patient_of_user(user.id)
    return jsonify(
        {
            "is_super_admin": is_super_admin(user),
            "admin_tenants": [tenant.to_dict(with_settings=False) for tenant in tenants],
            "links": patient_link_views(patient),
            "features": features_for(user),
            "patient_federation_id": patient.PatientFederationID if patient else None,
        }
    )


@saas_account.route("/me/links", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_links():
    return jsonify(patient_link_views(patient_of_user(current_user.id)))


@saas_account.route("/me/links/redeem", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def redeem():
    value = _json().get("code") or _json().get("token")
    if not value:
        raise SaasError("Saisissez le code imprimé sur votre facture.", 400)
    link = redeem_link_token(current_user, value)
    message = (
        f"{link.tenant.display_name} a été ajouté à votre compte."
        if link.status == "active"
        else f"Votre demande de rattachement à {link.tenant.display_name} est en attente de validation par l'établissement."
    )
    return {"message": message, "link": link.to_dict()}


@saas_account.route("/me/links/<int:link_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@login_needed
def revoke_my_link(link_id):
    patient = patient_of_user(current_user.id)
    link = db.session.get(PatientLink, link_id)
    if not patient or not link or link.patient_id != patient.id:
        raise SaasError("Rattachement introuvable.", 404)
    link.status = LINK_REVOKED
    link.updated_at = now()
    audit("link.revoked_by_patient", tenant_id=link.tenant_id, user_id=current_user.id, target=link.local_ref, commit=False)
    db.session.commit()
    return {"message": f"{link.tenant.display_name} a été retiré de votre compte."}


@saas_account.route("/me/links/legacy", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@login_needed
def revoke_legacy_link():
    """Retire l'établissement GNU Health historique (lien implicite par matricule)."""
    patient = patient_of_user(current_user.id)
    if not patient or not is_legacy_matricule(patient.PatientFederationID):
        raise SaasError("Rattachement introuvable.", 404)
    tenant = default_gnuhealth_tenant()
    link = db.session.execute(
        select(PatientLink).filter_by(tenant_id=tenant.id, local_ref=patient.PatientFederationID)
    ).scalar_one_or_none()
    if link is None:
        link = PatientLink(patient_id=patient.id, tenant_id=tenant.id, local_ref=patient.PatientFederationID, method="legacy")
        db.session.add(link)
    link.status = LINK_REVOKED
    link.updated_at = now()
    audit("link.revoked_by_patient", tenant_id=tenant.id, user_id=current_user.id, target=link.local_ref, commit=False)
    db.session.commit()
    return {"message": f"{tenant.display_name} a été retiré de votre compte."}


# =====================================================================
# Informations publiques d'un QR code (avant connexion)
# =====================================================================

@saas_account.route("/public/link-tokens/<string:token>", methods=["GET"])
@cross_origin(supports_credentials=True)
def public_token_info(token):
    link_token = db.session.execute(select(LinkToken).filter_by(token_hash=sha256(token.strip()))).scalar_one_or_none()
    if not link_token or not link_token.tenant:
        raise SaasError("Ce QR code n'est pas reconnu.", 404)
    return {
        "establishment": link_token.tenant.display_name,
        "status": link_token.status,
        "expires_at": link_token.expires_at,
    }
