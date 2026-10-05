"""Contrôles d'accès du module SaaS (fondés sur les rôles et les appartenances, pas sur la table des permissions)."""
from functools import wraps

from flask import g, request
from flask_jwt_extended import current_user, verify_jwt_in_request

from Rohafya import db
from .constants import API_KEY_HEADER, LEGACY_API_KEY_HEADER
from .models import Tenant
from .services import SaasError, can_admin_tenant, is_super_admin, tenant_from_api_key


def login_needed(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        verify_jwt_in_request()
        if current_user is None or not current_user.active:
            return {"message": "Session invalide. Reconnectez-vous."}, 401
        return view(*args, **kwargs)

    return wrapper


def super_admin_required(view):
    @wraps(view)
    @login_needed
    def wrapper(*args, **kwargs):
        if not is_super_admin(current_user):
            return {"message": "Accès réservé au super-administrateur."}, 403
        return view(*args, **kwargs)

    return wrapper


def tenant_admin_required(view):
    """La route doit recevoir `tenant_id` ; place l'établissement dans g.tenant."""

    @wraps(view)
    @login_needed
    def wrapper(*args, **kwargs):
        tenant_id = kwargs.get("tenant_id")
        tenant = db.session.get(Tenant, tenant_id)
        if tenant is None:
            return {"message": "Établissement introuvable."}, 404
        if not can_admin_tenant(current_user, tenant.id):
            return {"message": "Vous n'administrez pas cet établissement."}, 403
        g.tenant = tenant
        return view(*args, **kwargs)

    return wrapper


def api_key_required(view):
    """Appels machine à machine des établissements : en-tête X-ROHAFYA-API-Key (l'ancien X-EDEN-API-Key reste accepté)."""

    @wraps(view)
    def wrapper(*args, **kwargs):
        key = request.headers.get(API_KEY_HEADER) or request.headers.get(LEGACY_API_KEY_HEADER)
        if not key:
            auth = request.headers.get("Authorization", "")
            key = auth[7:] if auth.lower().startswith("bearer ") else None
        tenant = tenant_from_api_key(key)
        if tenant is None:
            return {"message": f"Clé d'API absente ou invalide (en-tête {API_KEY_HEADER})."}, 401
        g.tenant = tenant
        return view(*args, **kwargs)

    return wrapper


def register_error_handler(blueprint):
    @blueprint.errorhandler(SaasError)
    def _handle(error):
        return error.response()
