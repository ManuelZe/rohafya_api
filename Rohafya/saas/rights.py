"""Droits des comptes personnels : impression des comptes rendus et partage des résultats.

Règle générale : sans configuration pour un compte, tout est disponible. Une configuration ne restreint
que l'action qu'elle concerne ; le front rend alors le bouton inactif et en donne la raison.
"""
import datetime

from sqlalchemy import func, select

from Rohafya import db
from Rohafya.accounts.models import Patients, Send_Results
from .models import AccountRights
from .services import SaasError, now

PRINT_MODES = ("window", "blocked")
SHARE_LIMIT_MAX = 10000


def _fmt(value):
    return value.strftime("%d/%m/%Y à %H:%M") if value else None


def rights_row(user_id):
    return db.session.execute(select(AccountRights).filter_by(user_id=user_id)).scalar_one_or_none()


def shares_used(user_id, since):
    patient = db.session.execute(select(Patients.id).filter_by(user_id=user_id)).scalar_one_or_none()
    if patient is None:
        return 0
    stmt = select(func.count(Send_Results.id)).filter(Send_Results.patient_id == patient)
    if since is not None:
        stmt = stmt.filter(Send_Results.sended_at >= since)
    return db.session.execute(stmt).scalar() or 0


def print_right(row, at=None):
    at = at or now()
    if row is None or not row.print_mode:
        return {"configured": False, "allowed": True, "from": None, "until": None, "message": None}
    view = {"configured": True, "mode": row.print_mode, "from": row.print_from, "until": row.print_until}
    if row.print_mode == "blocked":
        return {**view, "allowed": False, "message": "L'impression des comptes rendus n'est pas activée pour votre compte. Contactez l'assistance ROHAFYA."}
    if row.print_from and at < row.print_from:
        return {**view, "allowed": False, "message": f"L'impression sera disponible à partir du {_fmt(row.print_from)}."}
    if row.print_until and at > row.print_until:
        return {**view, "allowed": False, "message": f"L'accès à l'impression a pris fin le {_fmt(row.print_until)}."}
    message = f"Impression autorisée jusqu'au {_fmt(row.print_until)}." if row.print_until else None
    return {**view, "allowed": True, "message": message}


def share_right(row, user_id):
    if row is None or row.share_limit is None:
        return {"configured": False, "allowed": True, "limit": None, "used": None, "remaining": None, "message": None}
    used = shares_used(user_id, row.share_since)
    remaining = max(0, row.share_limit - used)
    if remaining == 0:
        message = (
            "Le partage de résultats n'est pas activé pour votre compte. Contactez l'assistance ROHAFYA."
            if row.share_limit == 0
            else f"Vous avez utilisé vos {row.share_limit} partage{'s' if row.share_limit > 1 else ''} de résultats autorisé{'s' if row.share_limit > 1 else ''}."
        )
    else:
        message = f"Il vous reste {remaining} partage{'s' if remaining > 1 else ''} de résultats sur {row.share_limit}."
    return {
        "configured": True,
        "allowed": remaining > 0,
        "limit": row.share_limit,
        "used": used,
        "remaining": remaining,
        "since": row.share_since,
        "message": message,
    }


def rights_for(user_id):
    """Droits effectifs, tels que le front les affiche (GET /saas/me)."""
    row = rights_row(user_id)
    return {"print": print_right(row), "share": share_right(row, user_id)}


def ensure_can_share(user_id):
    """Refuse un partage au-delà de la limite fixée par le super-administrateur."""
    right = share_right(rights_row(user_id), user_id)
    if not right["allowed"]:
        raise SaasError(right["message"], 403)


def _parse_datetime(value, label):
    if value in (None, ""):
        return None
    try:
        return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        raise SaasError(f"{label} : date invalide.", 400)


def update_rights(user_id, data, admin_id):
    """Enregistre la configuration envoyée par le super-administrateur ; renvoie la ligne (ou None si tout est libre)."""
    row = rights_row(user_id) or AccountRights(user_id=user_id)

    if "print_mode" in data:
        mode = data.get("print_mode") or None
        if mode is not None and mode not in PRINT_MODES:
            raise SaasError("Mode d'impression inconnu.", 400)
        row.print_mode = mode
        if mode == "window":
            start = _parse_datetime(data.get("print_from"), "Début de l'accès") or now()
            end = _parse_datetime(data.get("print_until"), "Fin de l'accès")
            if end is None:
                raise SaasError("Indiquez la date de fin de l'accès à l'impression.", 400)
            if end <= start:
                raise SaasError("La fin de l'accès à l'impression doit être postérieure à son début.", 400)
            row.print_from, row.print_until = start, end
        else:
            row.print_from = row.print_until = None

    if "share_limit" in data:
        limit = data.get("share_limit")
        if limit in (None, ""):
            row.share_limit = None
            row.share_since = None
        else:
            try:
                limit = int(limit)
            except (TypeError, ValueError):
                raise SaasError("Le nombre de partages doit être un nombre entier.", 400)
            if not 0 <= limit <= SHARE_LIMIT_MAX:
                raise SaasError(f"Le nombre de partages doit être compris entre 0 et {SHARE_LIMIT_MAX}.", 400)
            if row.share_limit is None or data.get("reset_share_count"):
                row.share_since = now()
            row.share_limit = limit
    elif data.get("reset_share_count") and row.share_limit is not None:
        row.share_since = now()

    row.updated_at = now()
    row.updated_by = admin_id
    if row.print_mode is None and row.share_limit is None:
        if row.id is not None:
            db.session.delete(row)
        return None
    if row.id is None:
        db.session.add(row)
    return row


def admin_rights_view(user_id):
    """Configuration et effet, pour la console du super-administrateur."""
    row = rights_row(user_id)
    return {
        "user_id": user_id,
        "configured": row is not None,
        "print_mode": row.print_mode if row else None,
        "print_from": row.print_from if row else None,
        "print_until": row.print_until if row else None,
        "share_limit": row.share_limit if row else None,
        "share_since": row.share_since if row else None,
        "updated_at": row.updated_at if row else None,
        "effective": {"print": print_right(row), "share": share_right(row, user_id)},
    }


def rights_summaries(user_ids):
    """Résumé court par compte (liste des comptes) : seulement pour les comptes configurés."""
    if not user_ids:
        return {}
    rows = db.session.execute(select(AccountRights).filter(AccountRights.user_id.in_(user_ids))).scalars().all()
    summaries = {}
    for row in rows:
        parts = []
        right = print_right(row)
        if right["configured"]:
            if row.print_mode == "blocked":
                parts.append("Impression bloquée")
            else:
                parts.append(f"Impression jusqu'au {row.print_until.strftime('%d/%m/%Y')}" if right["allowed"] else "Impression hors période")
        if row.share_limit is not None:
            share = share_right(row, row.user_id)
            parts.append(f"Partages {share['used']}/{row.share_limit}")
        summaries[row.user_id] = parts
    return summaries
