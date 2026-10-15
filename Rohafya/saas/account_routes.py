"""Connexion par code e-mail, profil SaaS de l'utilisateur et rattachement de ses établissements."""
from flask import Blueprint, current_app, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import current_user
from sqlalchemy import select

from Rohafya import db
from .constants import LINK_REVOKED, ROLE_PATIENT
from .decorators import login_needed, register_error_handler
from .models import LinkToken, PatientLink, Tenant, TenantMember, UserPreference
from .rights import rights_for
from . import notifications as notif
from .trials import create_trial_request
from . import i18n, privacy, security
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
    send_mail,
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
    email = normalize_email(_json().get("email"))
    issue_email_otp(email, purpose="login")
    # Sans compte pour cette adresse, le code sert à en créer un : l'interface le signale d'emblée.
    exists = bool(active_users_by_email(email))
    message = (
        f"Un code de connexion a été envoyé à {email}."
        if exists
        else f"Aucun compte n'existe avec l'adresse {email}. Un code vous a été envoyé pour en créer un."
    )
    return {"message": message, "account_exists": exists}


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

LOCALES = ("fr", "en")


def _locale_of(user_id):
    pref = db.session.execute(select(UserPreference).filter_by(user_id=user_id)).scalar_one_or_none()
    return pref.locale if pref else "fr"


@saas_account.route("/me/preferences", methods=["PUT"])
@cross_origin(supports_credentials=True)
@login_needed
def update_preferences():
    """Langue de l'interface du compte : « fr » ou « en »."""
    locale = (_json().get("locale") or "").strip().lower()
    if locale not in LOCALES:
        raise SaasError("Langue inconnue : choisissez « fr » ou « en ».", 400)
    pref = db.session.execute(select(UserPreference).filter_by(user_id=current_user.id)).scalar_one_or_none()
    if pref is None:
        pref = UserPreference(user_id=current_user.id)
        db.session.add(pref)
    pref.locale = locale
    pref.updated_at = now()
    db.session.commit()
    return {"locale": locale}


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
            # La couleur principale habille la console de l'établissement.
            "admin_tenants": [{**tenant.to_dict(with_settings=False), "primary_color": tenant.setting("primary_color")} for tenant in tenants],
            "links": patient_link_views(patient),
            "features": features_for(user),
            # Impression et partage : tout est disponible sauf configuration du super-administrateur.
            "rights": rights_for(user.id),
            # Langue de l'interface choisie par le compte (« fr » par défaut).
            "locale": _locale_of(user.id),
            "patient_federation_id": patient.PatientFederationID if patient else None,
            # Double authentification : activée, obligatoire (administrateurs).
            "security": security.status(user),
            "deletion_requested": privacy.open_request(user.id) is not None,
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


# =====================================================================
# Notifications de l'utilisateur connecté (patients, médecins, administrateurs)
# =====================================================================

@saas_account.route("/me/notifications", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_notifications():
    return jsonify(notif.my_notifications(current_user.id))


@saas_account.route("/me/notifications/<int:notification_id>/read", methods=["PUT"])
@cross_origin(supports_credentials=True)
@login_needed
def read_notification(notification_id):
    return jsonify(notif.mark_read(current_user.id, notification_id))


@saas_account.route("/me/notifications/read", methods=["PUT"])
@cross_origin(supports_credentials=True)
@login_needed
def read_all_notifications():
    count = notif.mark_all_read(current_user.id)
    return {"message": f"{count} notification(s) marquée(s) comme lue(s).", "count": count}


@saas_account.route("/me/notifications/<int:notification_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@login_needed
def hide_notification(notification_id):
    notif.hide(current_user.id, notification_id)
    return {"message": "Notification supprimée de votre liste."}


@saas_account.route("/me/notifications", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@login_needed
def hide_all_notifications():
    count = notif.hide_all(current_user.id)
    return {"message": f"{count} notification(s) supprimée(s) de votre liste.", "count": count}


# =====================================================================
# Demande d'essai d'un établissement (site de présentation, sans compte)
# =====================================================================

@saas_account.route("/public/trial-requests", methods=["POST"])
@cross_origin(supports_credentials=True)
def trial_request():
    create_trial_request(_json())
    return {"message": "Merci ! Votre demande est bien reçue : nous vous recontactons sous 24 heures ouvrées pour fixer la démonstration."}, 201


# =====================================================================
# Double authentification
# =====================================================================

@saas_account.route("/auth/mfa/verify", methods=["POST"])
@cross_origin(supports_credentials=True)
def mfa_verify():
    """Deuxième étape de la connexion : jeton de la première étape + code à 6 chiffres (ou code de secours)."""
    from Rohafya.accounts.models import User

    data = _json()
    user_id, remember = security.read_challenge(data.get("mfa_token"))
    user = db.session.get(User, user_id)
    if user is None or not user.active:
        raise SaasError("Compte introuvable ou désactivé.", 401)
    method = security.verify_user_code(user, data.get("code"))
    if method == "recovery":
        audit("mfa.recovery_used", user_id=user.id, target=user.email, commit=False)
    db.session.commit()
    return current_app.user_manager._do_login_user(user, remember_me=remember, mfa_verified=True)


@saas_account.route("/me/security", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def security_status():
    return security.status(current_user)


@saas_account.route("/me/mfa/setup", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def mfa_setup():
    from .services import qr_png_base64

    secret, uri = security.start_setup(current_user)
    return {"secret": secret, "otpauth_uri": uri, "qr_png": qr_png_base64(uri)}


@saas_account.route("/me/mfa/enable", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def mfa_enable():
    """Confirme l'activation : renvoie les codes de secours (affichés une seule fois) et une session marquée « mfa »."""
    from flask_jwt_extended import create_access_token, get_jwt

    codes = security.confirm_setup(current_user, _json().get("code"))
    audit("mfa.enabled", user_id=current_user.id, target=current_user.email)
    if current_user.email:
        send_mail(current_user.email, "mail.mfa_enabled", "emails/securite.html", locale=i18n.user_locale(current_user.id),
                  enabled=True, name=current_user.first_name)
    claims = {key: value for key, value in get_jwt().items() if key in ("roles", "login_time")}
    token = create_access_token(identity=str(current_user.id), additional_claims={**claims, "mfa": True})
    return {"recovery_codes": codes, "access_token": token, "security": security.status(current_user)}


@saas_account.route("/me/mfa/recovery-codes", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def mfa_recovery_codes():
    security.verify_user_code(current_user, _json().get("code"))
    codes = security.new_recovery_codes(security.mfa_of(current_user.id))
    audit("mfa.recovery_renewed", user_id=current_user.id, target=current_user.email)
    return {"recovery_codes": codes, "security": security.status(current_user)}


@saas_account.route("/me/mfa/disable", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def mfa_disable():
    if security.mfa_required(current_user):
        raise SaasError("La double authentification est obligatoire pour les administrateurs : elle ne peut pas être désactivée.", 403)
    security.verify_user_code(current_user, _json().get("code"))
    security.disable(current_user)
    audit("mfa.disabled", user_id=current_user.id, target=current_user.email)
    if current_user.email:
        send_mail(current_user.email, "mail.mfa_disabled", "emails/securite.html", locale=i18n.user_locale(current_user.id),
                  enabled=False, name=current_user.first_name)
    return {"security": security.status(current_user)}


# =====================================================================
# Données personnelles : historique des accès, export, suppression
# =====================================================================

@saas_account.route("/me/access-log", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_access_log():
    return jsonify({"items": privacy.access_log(current_user)})


@saas_account.route("/me/export", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_export():
    return jsonify(privacy.export_data(current_user))


@saas_account.route("/me/deletion-request", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_deletion_request():
    request_row = privacy.open_request(current_user.id)
    return jsonify(request_row.to_dict() if request_row else None)


@saas_account.route("/me/deletion-request", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def ask_deletion():
    return jsonify(privacy.request_deletion(current_user, _json().get("reason")).to_dict()), 201


@saas_account.route("/me/deletion-request", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@login_needed
def cancel_deletion():
    return jsonify(privacy.cancel_deletion(current_user).to_dict())

