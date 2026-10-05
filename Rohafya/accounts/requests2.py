from flask import Flask, jsonify
from flask import (
    Blueprint, request, g, url_for
)
from sqlalchemy import delete
from .models import Requests
from Rohafya.deco.decorators import roles_required, require_any_permission
from datetime import datetime
from flask_cors import CORS, cross_origin
from flask_tryton import Tryton
from Rohafya import db
from flask_jwt_extended import jwt_required, verify_jwt_in_request, get_jwt_identity, get_jwt
from .models import User
from sqlalchemy import select

requete = Blueprint('requete', __name__, url_prefix='/requete')

import logging
from datetime import datetime
from flask import jsonify, request
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, get_jwt
from flask_cors import cross_origin

from ..email.email import send_email  # S'assurer du bon chemin d'import

@requete.route('/add', methods=["POST"])
@cross_origin(supports_credentials=True)
def add_requests():
    if request.method == 'POST':
        # Gestion optionnelle de l'identité via JWT
        CreatedBy = None
        try:
            verify_jwt_in_request(optional=True)
            identity = get_jwt_identity()
            if identity:
                CreatedBy = User.query.get(identity)
        except Exception:
            CreatedBy = None

        # Récupération des rôles JWT
        try:
            jwt_data = get_jwt()
            roles = jwt_data.get("roles", []) if jwt_data else []
        except Exception:
            roles = []

        data = request.get_json() or {}

        # Initialisation par défaut
        patient_request_examen_out = False
        patient_request_prix_examen = False
        patient_request_connexion = False
        patient_request_other_administration = False

        if "Patient" in roles:
            patient_request_examen_out = data.get('patient_request_examen_out', False)
            patient_request_prix_examen = data.get('patient_request_prix_examen', False)
            patient_request_connexion = data.get('patient_request_connexion', False)
            patient_request_other_administration = data.get('patient_request_other_administration', False)

        first_name = data.get('first_name')
        last_name = data.get('last_name')
        email = data.get('email')
        message = data.get('message')
        administration = data.get('administration')
        commission = data.get('commission')
        suggestion = data.get('suggestion')
        etat_patient = data.get('etat_patient')
        connection = data.get('connection')
        error = data.get('error')
        revendication_examen = data.get('revendication_examen')
        CreatedAt = datetime.now()

        if not first_name or not last_name:
            return {'message': 'Vous devez entrer un Nom et un Prénom'}, 400
        if not message:
            return {'message': 'Un message doit être soumis.'}, 400

        created_by_id = CreatedBy.id if CreatedBy else None

        requete_obj = Requests(
            first_name=first_name,
            last_name=last_name,
            email=email,
            message=message,
            administration=administration,
            commission=commission,
            suggestion=suggestion,
            error=error,
            revendication_examen=revendication_examen,
            etat_patient=etat_patient,
            connection=connection,
            patient_request_examen_out=patient_request_examen_out,
            patient_request_prix_examen=patient_request_prix_examen,
            patient_request_connexion=patient_request_connexion,
            patient_request_other_administration=patient_request_other_administration,
            CreatedAt=CreatedAt,
            CreatedBy=created_by_id
        )

        try:
            db.session.add(requete_obj)
            db.session.commit()

            # --- ENVOI DE LA NOTIFICATION A L'ADMINISTRATION ---
            admin_emails = ["ze.lilian@myiuc.com", "nkwepo.aristide@pdmdsante.com"]
            # admin_email = "ze.lilian@myiuc.com"
            subject = "NOUVELLE REQUETE SOUMISE SUR L'APPLICATION"
            template_html = "emails/nouvelle_requete_email.html"

            context = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email or "Non renseigné",
                "message": message,
                "administration": administration,
                "commission": commission,
                "suggestion": suggestion,
                "error": error,
                "revendication_examen": revendication_examen,
                "etat_patient": etat_patient,
                "connection": connection,
                "Date": CreatedAt.strftime("%d/%m/%Y %H:%M:%S")
            }

            try:
                send_email(
                    to=admin_emails,
                    subject=subject,
                    body=f"Une nouvelle requête a été créée par {first_name} {last_name}.",
                    template_html=template_html,
                    **context
                )
            except Exception as e:
                logging.error(f"Erreur lors de l'envoi de l'e-mail de notification d'administration : {e}")

            return jsonify(requete_obj.to_dict()), 201

        except Exception as e:
            db.session.rollback()
            logging.error(f"Erreur lors de l'enregistrement de la requête : {e}")
            return {'message': "Une erreur est survenue lors de l'enregistrement de la requête."}, 500
    

@requete.route('/anonym/add', methods=["POST"])
@cross_origin(supports_credentials=True)
def add_anonym_requests():
    if request.method == 'POST':
        data = request.get_json() or {}

        first_name = data.get('first_name')
        last_name = data.get('last_name')
        email = data.get('email')
        message = data.get('message')
        administration = data.get('administration')
        commission = data.get('commission')
        suggestion = data.get('suggestion')
        etat_patient = data.get('etat_patient')
        connection = data.get('connection')
        error = data.get('error')
        revendication_examen = data.get('revendication_examen')
        CreatedAt = datetime.now()

        if not first_name or not last_name:
            return {'message': 'Vous devez entrer un Nom et un Prénom'}, 400
        if not message:
            return {'message': 'Un message doit être soumis.'}, 400

        requete = Requests(
            first_name=first_name,
            last_name=last_name,
            email=email,
            message=message,
            administration=administration,
            commission=commission,
            suggestion=suggestion,
            error=error,
            revendication_examen=revendication_examen,
            etat_patient=etat_patient,
            connection=connection,
            CreatedAt=CreatedAt,
            CreatedBy=None
        )

        try:
            db.session.add(requete)
            db.session.commit()

            # --- ENVOI DES NOTIFICATIONS A L'ADMINISTRATION (2 DESTINATAIRES) ---
            admin_emails = ["ze.lilian@myiuc.com", "nkwepo.aristide@pdmdsante.com"]  # Remplacez par le 2ème e-mail
            subject = "NOUVELLE REQUETE ANONYME SOUMISE SUR L'APPLICATION"
            template_html = "emails/nouvelle_requete_email.html"

            context = {
                "first_name": first_name,
                "last_name": last_name,
                "email": email or "Non renseigné (Anonyme)",
                "message": message,
                "administration": administration,
                "commission": commission,
                "suggestion": suggestion,
                "error": error,
                "revendication_examen": revendication_examen,
                "etat_patient": etat_patient,
                "connection": connection,
                "Date": CreatedAt.strftime("%d/%m/%Y %H:%M:%S")
            }

            for recipient in admin_emails:
                try:
                    send_email(
                        to=recipient,
                        subject=subject,
                        body=f"Une nouvelle requête anonyme a été créée par {first_name} {last_name}.",
                        template_html=template_html,
                        **context
                    )
                except Exception as e:
                    logging.error(f"Erreur lors de l'envoi de l'e-mail à {recipient} : {e}")

            return jsonify(requete.to_dict()), 201

        except Exception as e:
            db.session.rollback()
            logging.error(f"Erreur lors de l'enregistrement de la requête anonyme : {e}")
            return {'message': "Une erreur est survenue lors de l'enregistrement de la requête."}, 500


@requete.route('/del/<int:req_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
#@roles_required(['Admin', 'Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["administration.requete.delete_request",
                     "patients.requete.delete_request",
                     "doctors.requete.delete_request"])
def del_Requests(req_id=None):
    if req_id:
        requests = db.session.execute(select(Requests).filter_by(id=req_id)).scalar_one_or_none()
    else :
        return {"Message" : " l'ID De la requête doit être fourni. "}

    if not requests :
        return {"Message" : "Aucune Requête Trouvée. "}

    db.session.delete(requests)
    db.session.commit()

    return {"Message" : "Requête Supprimée avec succès. "}


@requete.route('/delete-all', methods=['DELETE', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.requete.delete_request"])
def delete_all_requests():
    """Supprime l'ensemble des requêtes de la base de données."""
    try:
        # Exécution d'un DELETE global pour une suppression optimisée
        result = db.session.execute(delete(Requests))
        db.session.commit()

        deleted_count = result.rowcount

        return {
            "Message": f"Toutes les requêtes ont été supprimées avec succès.",
            "count": deleted_count
        }, 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la suppression de toutes les requêtes : {e}")
        return {"Message": "Impossible de supprimer les requêtes. Des dépendances existent peut-être."}, 500


@requete.route('/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.requete.all_requests"])
def all_requests():
    requests = db.session.execute(select(Requests)).scalars().all()
    requests_lists = [request.to_dict() for request in requests]

    return jsonify(requests_lists)


@requete.route('/nbr', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.requete.nbr_requests"])
def nbr_requete():

    req = db.session.execute(select(Requests)).scalars().all()
    nbr = len(req)
    return {"Nbr":nbr}


@requete.route('/validate/<int:int_req>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["requete.nbr_requests"])
def validate_request(int_req):

    if request.method == "PUT":
        req = db.session.execute(select(Requests).filter_by(id=int_req)).scalar_one_or_none()
        if req :
            req.valide = True
            req.UpdatedAt = datetime.now()
            
            verify_jwt_in_request()
            try:
                identity = get_jwt_identity()
                UpdatedBy = User.query.get(identity)
                req.UpdatedBy = UpdatedBy.id
            except : 
                req.UpdatedByBy = None

            db.session.commit()

            return jsonify(req.to_dict())
        
        return {"Message" : f"Aucune Requête à cet ID {int_req}. "}


@requete.route('/get_requests/<int:user_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Doctor', 'Patient'])
@jwt_required()
@require_any_permission(["administration.requete.get_requests_by_id",
                         "patients.requete.get_requests_by_id",
                         "doctors.requete.get_requests_by_id"])
def get_requests_by_id(user_id):
    """Get all requests by user ID."""
    if request.method == "GET":
        req = db.session.execute(select(Requests).filter_by(CreatedBy=user_id)).scalars().all()
        if req:
            req_list = [request.to_dict() for request in req]
            return jsonify(req_list)
        
        return {"Message": f"Aucune Requête trouvée pour l'ID {user_id}."}
    
    return {"Message": "Méthode non autorisée."}


@requete.route('/reject_requests/<int:int_req>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin'])
@jwt_required()
@require_any_permission(["administration.requete.rejet_request"])
def rejected_request(int_req):
    if request.method == "PUT":
        req = db.session.execute(select(Requests).filter_by(id=int_req)).scalar_one_or_none()
        if req :
            req.rejected = True
            req.UpdatedAt = datetime.now()
            
            verify_jwt_in_request()
            try:
                identity = get_jwt_identity()
                UpdatedBy = User.query.get(identity)
                req.UpdatedBy = UpdatedBy.id
            except : 
                req.UpdatedByBy = None

            db.session.commit()

            return jsonify(req.to_dict())
        
        return {"Message" : f"Aucune Requête à cet ID {int_req}. "}


