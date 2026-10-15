"""Double authentification des comptes (code TOTP d'une application d'authentification).

- Obligatoire pour les super-administrateurs et les administrateurs d'établissement quand
  ROHAFYA_ADMIN_MFA_REQUIRED vaut vrai (valeur par défaut en production) : sans elle, les
  consoles répondent 403 « mfa_setup_required » et l'interface ouvre la page Sécurité.
- Facultative pour les autres comptes.
- Connexion : après le mot de passe ou le code e-mail, l'API renvoie {"mfa_required": true,
  "mfa_token": …} au lieu de la session ; POST /saas/auth/mfa/verify échange ce jeton (5 minutes)
  et un code à 6 chiffres (ou un code de secours) contre la session. Le jeton de session porte
  alors la marque « mfa » exigée par les consoles.
"""
import base64
import datetime
import hashlib
import hmac
import secrets
import struct
import time
from urllib.parse import quote

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app
from flask_jwt_extended import get_jwt
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer
from sqlalchemy import func, select

from Rohafya import db
from .models import SaasAttempt, TenantMember, UserMfa
from .services import SaasError, is_super_admin, keyed_hash, now

ISSUER = "ROHAFYA"
STEP_SECONDS = 30
DIGITS = 6
CHALLENGE_SECONDS = 300
MAX_FAILURES = 5
FAILURE_WINDOW = datetime.timedelta(minutes=15)
RECOVERY_CODES = 10


# =====================================================================
# TOTP (RFC 6238) et chiffrement du secret
# =====================================================================

def new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode("ascii").rstrip("=")


def _code(secret, step):
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    digest = hmac.new(key, struct.pack(">Q", step), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(value % 10 ** DIGITS).zfill(DIGITS)


def current_step(at=None):
    return int((at if at is not None else time.time()) // STEP_SECONDS)


def match_step(secret, code, last_step=None, at=None):
    """Pas de temps du code (fenêtre ±30 s), ou None. Un pas déjà utilisé est refusé."""
    code = "".join(ch for ch in str(code or "") if ch.isdigit())
    if len(code) != DIGITS:
        return None
    step = current_step(at)
    for candidate in (step, step - 1, step + 1):
        if last_step is not None and candidate <= last_step:
            continue
        if hmac.compare_digest(_code(secret, candidate), code):
            return candidate
    return None


def _fernet():
    secret = (current_app.config.get("SECRET_KEY") or "rohafya").encode("utf-8")
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"rohafya-mfa:" + secret).digest()))


def _encrypt(value):
    return _fernet().encrypt(value.encode("ascii")).decode("ascii")


def _decrypt(value):
    try:
        return _fernet().decrypt(value.encode("ascii")).decode("ascii")
    except InvalidToken:
        raise SaasError("Double authentification illisible : demandez sa réinitialisation au super-administrateur.", 500)


def otpauth_uri(secret, account):
    label = quote(f"{ISSUER}:{account}", safe=":")
    return f"otpauth://totp/{label}?secret={secret}&issuer={ISSUER}&algorithm=SHA1&digits={DIGITS}&period={STEP_SECONDS}"


# =====================================================================
# État d'un compte
# =====================================================================

def mfa_of(user_id):
    return db.session.execute(select(UserMfa).filter_by(user_id=user_id)).scalar_one_or_none()


def mfa_enabled(user):
    record = mfa_of(user.id)
    return bool(record and record.enabled_at)


def is_admin_account(user):
    if is_super_admin(user):
        return True
    return db.session.execute(select(TenantMember.id).filter_by(user_id=user.id)).first() is not None


def mfa_required(user):
    return bool(current_app.config.get("ROHAFYA_ADMIN_MFA_REQUIRED", False)) and is_admin_account(user)


def status(user):
    record = mfa_of(user.id)
    enabled = bool(record and record.enabled_at)
    return {
        "enabled": enabled,
        "required": mfa_required(user),
        "enabled_at": record.enabled_at if enabled else None,
        "recovery_codes_left": len(record.recovery_hashes or []) if enabled else 0,
    }


def console_access_problem(user):
    """Réponse d'erreur si le compte ne peut pas ouvrir une console d'administration, sinon None."""
    if mfa_enabled(user):
        try:
            verified = bool(get_jwt().get("mfa"))
        except Exception:
            verified = False
        if not verified:
            return {"message": "Reconnectez-vous avec votre code de double authentification.", "code": "mfa_required"}, 401
        return None
    if mfa_required(user):
        return {
            "message": "La double authentification est obligatoire pour les administrateurs : activez-la depuis la page Sécurité.",
            "code": "mfa_setup_required",
        }, 403
    return None


# =====================================================================
# Tentatives
# =====================================================================

def _check_attempts(user_id):
    since = now() - FAILURE_WINDOW
    failures = db.session.execute(
        select(func.count(SaasAttempt.id)).filter(
            SaasAttempt.user_id == user_id, SaasAttempt.action == "mfa", SaasAttempt.success.is_(False), SaasAttempt.created_at >= since
        )
    ).scalar()
    if failures >= MAX_FAILURES:
        raise SaasError("Trop de codes incorrects. Réessayez dans 15 minutes.", 429)


def _record_attempt(user_id, success):
    db.session.add(SaasAttempt(user_id=user_id, action="mfa", success=success))
    db.session.commit()


def verify_user_code(user, code):
    """Vérifie un code TOTP ou un code de secours du compte (double authentification activée)."""
    record = mfa_of(user.id)
    if not record or not record.enabled_at:
        raise SaasError("La double authentification n'est pas activée sur ce compte.", 400)
    _check_attempts(user.id)
    secret = _decrypt(record.secret)
    step = match_step(secret, code, record.last_step)
    if step is not None:
        record.last_step = step
        _record_attempt(user.id, True)
        return "totp"
    recovery = _normalize_recovery(code)
    if len(recovery) == 8:
        digest = keyed_hash(f"mfa-recovery:{recovery}")
        hashes = list(record.recovery_hashes or [])
        if digest in hashes:
            hashes.remove(digest)
            record.recovery_hashes = hashes
            _record_attempt(user.id, True)
            return "recovery"
    _record_attempt(user.id, False)
    # 400 et non 401 : un code faux ne doit pas fermer la session en cours (réglages de sécurité).
    raise SaasError("Code incorrect ou expiré. Vérifiez l'heure de votre téléphone et réessayez.", 400)


# =====================================================================
# Activation
# =====================================================================

def _normalize_recovery(code):
    return "".join(ch for ch in str(code or "").lower() if ch.isalnum())


def _hash_codes(codes):
    return [keyed_hash(f"mfa-recovery:{_normalize_recovery(code)}") for code in codes]


def start_setup(user):
    record = mfa_of(user.id)
    if record and record.enabled_at:
        raise SaasError("La double authentification est déjà activée.", 409)
    secret = new_secret()
    if record is None:
        record = UserMfa(user_id=user.id, secret=_encrypt(secret), recovery_hashes=[])
        db.session.add(record)
    else:
        record.secret = _encrypt(secret)
        record.last_step = None
    db.session.commit()
    return secret, otpauth_uri(secret, user.email or user.username)


def confirm_setup(user, code):
    record = mfa_of(user.id)
    if record is None or record.enabled_at:
        raise SaasError("Commencez par afficher le QR code de configuration.", 400)
    _check_attempts(user.id)
    step = match_step(_decrypt(record.secret), code)
    if step is None:
        _record_attempt(user.id, False)
        raise SaasError("Code incorrect. Saisissez le code à 6 chiffres affiché par l'application.", 400)
    codes = new_recovery_codes(record)
    record.enabled_at = now()
    record.last_step = step
    _record_attempt(user.id, True)
    return codes


def new_recovery_codes(record):
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(RECOVERY_CODES)]
    record.recovery_hashes = _hash_codes(codes)
    db.session.commit()
    return codes


def disable(user):
    record = mfa_of(user.id)
    if record is not None:
        db.session.delete(record)
        db.session.commit()


# =====================================================================
# Étape de connexion
# =====================================================================

def _serializer():
    return URLSafeTimedSerializer(current_app.config.get("SECRET_KEY") or "rohafya", salt="rohafya-mfa-login")


def login_challenge(user, remember_me):
    """Jeton de l'étape « code de double authentification », ou None si le compte n'en a pas."""
    if not mfa_enabled(user):
        return None
    token = _serializer().dumps({"uid": user.id, "remember": bool(remember_me)})
    return {
        "mfa_required": True,
        "mfa_token": token,
        "message": "Saisissez le code à 6 chiffres de votre application d'authentification.",
    }


def read_challenge(token):
    try:
        data = _serializer().loads(token or "", max_age=CHALLENGE_SECONDS)
    except SignatureExpired:
        raise SaasError("Étape de connexion expirée : reconnectez-vous.", 401)
    except BadSignature:
        raise SaasError("Étape de connexion invalide : reconnectez-vous.", 401)
    return int(data["uid"]), bool(data.get("remember"))
