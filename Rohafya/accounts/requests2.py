import logging
from datetime import datetime

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import current_user, get_jwt_identity, jwt_required, verify_jwt_in_request
from sqlalchemy import delete, select

from Rohafya import db
from Rohafya.deco.decorators import require_any_permission
from Rohafya.saas import submissions
from Rohafya.saas.constants import (
    AUTHOR_ANONYMOUS,
    AUTHOR_DOCTOR,
    AUTHOR_PATIENT,
    SUBMISSION_DONE,
    SUBMISSION_REFUSED,
    SUBMISSION_REQUEST,
)
from Rohafya.saas.decorators import register_error_handler
from Rohafya.saas.models import Submission
from Rohafya.saas.services import SaasError
from .models import Requests

requete = Blueprint('requete', __name__, url_prefix='/requete')
register_error_handler(requete)

KIND = SUBMISSION_REQUEST
CATEGORY_FIELDS = (
    'administration', 'commission', 'suggestion', 'error', 'revendication_examen', 'etat_patient', 'connection',
)
PATIENT_CATEGORY_FIELDS = (
    'patient_request_examen_out', 'patient_request_prix_examen', 'patient_request_connexion',
    'patient_request_other_administration',
)


def _create(data, author=None, role=AUTHOR_ANONYMOUS):
    """Enregistre une requête et l'adresse à l'établissement choisi (data["tenant_id"])."""
    tenant = submissions.target_tenant(data.get('tenant_id'))
    first_name = (data.get('first_name') or '').strip()
    last_name = (data.get('last_name') or '').strip()
    email = (data.get('email') or '').strip() or (author.email if author else '')
    message = (data.get('message') or '').strip()
    if not first_name or not last_name:
        raise SaasError('Vous devez entrer un Nom et un Prénom', 400)
    if not message:
        raise SaasError('Un message doit être soumis.', 400)
    if role == AUTHOR_ANONYMOUS and '@' not in email:
        raise SaasError("Indiquez une adresse e-mail : la réponse de l'établissement y sera envoyée.", 400)

    fields = {name: bool(data.get(name)) for name in CATEGORY_FIELDS}
    if role == AUTHOR_PATIENT:
        fields.update({name: bool(data.get(name)) for name in PATIENT_CATEGORY_FIELDS})

    requete_obj = Requests(
        first_name=first_name[:100],
        last_name=last_name[:100],
        email=email[:100],
        message=message[:500],
        CreatedAt=datetime.now(),
        CreatedBy=author.id if author else None,
        **fields,
    )
    db.session.add(requete_obj)
    db.session.flush()
    submission = submissions.create_submission(KIND, requete_obj, tenant, author, role)
    data = requete_obj.to_dict()
    data['submission'] = submission.to_dict()
    return data


@requete.route('/add', methods=["POST"])
@cross_origin(supports_credentials=True)
def add_requests():
    """Requête d'un utilisateur connecté (ou anonyme, sans jeton) à un établissement : tenant_id obligatoire.

    audience = "patient" ou "doctor" : espace d'où la requête est envoyée.
    """
    verify_jwt_in_request(optional=True)
    data = request.get_json(silent=True) or {}
    author = current_user if get_jwt_identity() else None
    role = submissions.author_role(author, (data.get('audience') or '').lower() or None) if author else AUTHOR_ANONYMOUS
    return jsonify(_create(data, author, role)), 201


@requete.route('/anonym/add', methods=["POST"])
@cross_origin(supports_credentials=True)
def add_anonym_requests():
    """Requête d'un visiteur sans compte : tenant_id et e-mail obligatoires (la réponse y est envoyée)."""
    return jsonify(_create(request.get_json(silent=True) or {})), 201


@requete.route('/del/<int:req_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.delete_request",
                         "patients.requete.delete_request",
                         "doctors.requete.delete_request"])
def del_Requests(req_id=None):
    item, _ = submissions.get_item(KIND, req_id, write=True)
    submissions.delete_item(KIND, item)
    return {"Message": "Requête Supprimée avec succès. "}


@requete.route('/delete-all', methods=['DELETE', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.delete_request"])
def delete_all_requests():
    """Supprime l'ensemble des requêtes de la base de données (administrateur global)."""
    try:
        db.session.execute(delete(Submission).where(Submission.kind == KIND))
        result = db.session.execute(delete(Requests))
        db.session.commit()
        return {"Message": "Toutes les requêtes ont été supprimées avec succès.", "count": result.rowcount}, 200
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la suppression de toutes les requêtes : {e}")
        return {"Message": "Impossible de supprimer les requêtes. Des dépendances existent peut-être."}, 500


@requete.route('/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.all_requests"])
def all_requests():
    """Toutes les requêtes (administrateur global). Les établissements utilisent /saas/admin/tenants/<id>/submissions."""
    items = db.session.execute(select(Requests).order_by(Requests.id.desc())).scalars().all()
    return jsonify(submissions.with_submission(KIND, items))


@requete.route('/nbr', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.nbr_requests"])
def nbr_requete():
    return {"Nbr": len(db.session.execute(select(Requests.id)).all())}


def _set_status(int_req, status):
    item, submission = submissions.get_item(KIND, int_req)
    if submission is not None:
        submissions.respond(submission, current_user, status=status)
    else:
        item.valide = status == SUBMISSION_DONE
        item.rejected = status == SUBMISSION_REFUSED
        item.UpdatedAt = datetime.now()
        item.UpdatedBy = current_user.id
        db.session.commit()
    data = item.to_dict()
    data['submission'] = submission.to_dict() if submission else None
    return jsonify(data)


@requete.route('/validate/<int:int_req>', methods=['PUT'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.validate_request"])
def validate_request(int_req):
    """Ancienne validation globale ; les établissements répondent depuis leur console (/admin/demandes)."""
    return _set_status(int_req, SUBMISSION_DONE)


@requete.route('/get_requests/<int:user_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.get_requests_by_id",
                         "patients.requete.get_requests_by_id",
                         "doctors.requete.get_requests_by_id"])
def get_requests_by_id(user_id):
    """Requêtes d'un utilisateur : soi-même (?audience=doctor pour l'espace médecin), ou tout le monde pour un administrateur global."""
    if user_id == current_user.id:
        role = AUTHOR_DOCTOR if (request.args.get('audience') or '').lower() == AUTHOR_DOCTOR else AUTHOR_PATIENT
        items = submissions.own_items(KIND, current_user, role)
    elif submissions.is_global_admin(current_user, KIND):
        items = db.session.execute(select(Requests).filter_by(CreatedBy=user_id)).scalars().all()
    else:
        return {"message": "Accès refusé."}, 403
    return jsonify(submissions.with_submission(KIND, items))


@requete.route('/reject_requests/<int:int_req>', methods=['PUT'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.rejet_request"])
def rejected_request(int_req):
    return _set_status(int_req, SUBMISSION_REFUSED)
