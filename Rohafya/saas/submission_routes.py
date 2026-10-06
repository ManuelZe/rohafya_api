"""Demandes reçues par un établissement : prescriptions, pré-enregistrements, requêtes.

    GET  /saas/public/establishments                                établissements actifs (formulaires, sans connexion)
    GET  /saas/admin/tenants/<id>/submissions?kind=&status=&q=&page= demandes de l'établissement
    GET  /saas/admin/tenants/<id>/submissions/<sid>/image            image jointe (prescription, pré-enregistrement)
    PUT  /saas/admin/tenants/<id>/submissions/<sid>                  {status, response, quote_amount} : réponse

Les routes /saas/admin/… sont réservées aux administrateurs de l'établissement (et au super-administrateur).
"""
from io import BytesIO

from flask import Blueprint, g, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import current_user

from Rohafya import db
from .constants import SUBMISSION_KINDS, SUBMISSION_STATUSES
from .decorators import register_error_handler, tenant_admin_required
from .models import Submission
from .services import SaasError
from . import submissions

submissions_admin = Blueprint("saas_submissions", __name__, url_prefix="/saas")
register_error_handler(submissions_admin)

MAX_PAGE_SIZE = 100


def _submission(tenant_id, submission_id):
    submission = db.session.get(Submission, submission_id)
    if submission is None or submission.tenant_id != tenant_id:
        raise SaasError("Demande introuvable.", 404)
    return submission


@submissions_admin.route("/public/establishments", methods=["GET"])
@cross_origin()
def public_establishments():
    """Établissements actifs, pour choisir le destinataire d'une prescription, d'un pré-enregistrement ou d'une requête."""
    return jsonify(submissions.active_establishments())


@submissions_admin.route("/admin/tenants/<int:tenant_id>/submissions", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_submissions(tenant_id):
    kind = request.args.get("kind") or None
    status = request.args.get("status") or None
    if kind and kind not in SUBMISSION_KINDS:
        raise SaasError(f"Type inconnu : {kind}.", 400)
    if status and status not in SUBMISSION_STATUSES:
        raise SaasError(f"Statut inconnu : {status}.", 400)
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(MAX_PAGE_SIZE, max(1, int(request.args.get("page_size", 25))))
    except ValueError:
        page, size = 1, 25
    return jsonify(submissions.tenant_submissions(g.tenant, kind, status, request.args.get("q"), page, size))


@submissions_admin.route("/admin/tenants/<int:tenant_id>/submissions/<int:submission_id>/image", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def submission_image(tenant_id, submission_id):
    submission = _submission(tenant_id, submission_id)
    item = db.session.get(submissions.ITEM_MODELS[submission.kind], submission.item_id)
    if item is None or not getattr(item, "image_data", None):
        raise SaasError("Aucune image jointe.", 404)
    return send_file(BytesIO(item.image_data), mimetype=item.image_mimetype or "image/jpeg", as_attachment=False)


@submissions_admin.route("/admin/tenants/<int:tenant_id>/submissions/<int:submission_id>", methods=["PUT"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def answer_submission(tenant_id, submission_id):
    submission = _submission(tenant_id, submission_id)
    data = request.get_json(silent=True) or {}
    if not any(key in data for key in ("status", "response", "quote_amount")):
        raise SaasError("Indiquez un statut, une réponse ou un montant.", 400)
    return jsonify(submissions.respond(
        submission,
        current_user,
        status=data.get("status"),
        response=data.get("response"),
        quote_amount=data.get("quote_amount"),
    ))
