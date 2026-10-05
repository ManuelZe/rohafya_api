from flask import (
    Blueprint, request, g, redirect, flash, render_template, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import login_required
from flask_tryton import Tryton
from Rohafya.accounts.models import Configuration, User
from flask_cors import CORS, cross_origin
from Rohafya import db
from flask import Flask
from Rohafya.deco.decorators import roles_required, require_any_permission
from flask_tryton import Tryton
from flask_jwt_extended import jwt_required, verify_jwt_in_request, get_jwt_identity
from Rohafya.email.email import send_email
from datetime import date, datetime, timedelta, time
from Rohafya import tryton

configuration = Blueprint('conig', __name__, url_prefix='/config/')

@configuration.route('add/', methods=["POST"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.configuration.add_config"])
def add_config():
    """
        AJOUTER UNE NOUVELLE CONFIGURATION
    """

    if request.method == "POST" :
        exp_result = request.get_json()["exp_result"]
        start_date_facture_verification = request.get_json()["start_date_facture_verification"]
        name = request.get_json()["name"]
        CreatedAt = datetime.now()

        verify_jwt_in_request()
        try:
            identity = get_jwt_identity()
            CreatedBy = User.query.get(identity)
        except : 
            CreatedBy = None

        config = Configuration(
            exp_result = exp_result,
            start_date_facture_verification = start_date_facture_verification,
            CreatedBy = CreatedBy,
            CreatedAt = CreatedAt,
            name = name
        )

        db.session.add(config)
        db.session.commit()

        return config.to_dict()


@configuration.route('mod/<int:conf_id>', methods=["POST"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.configuration.mod_config"])
def mod_config(conf_id):
    """MODIFIER UNE CONFIGURATION DE BASE

    Args:
        conf_id (int): L'ID de la configuration
    """

    if request.method == "PUT":
        config = db.session.select(Configuration).filter_by(id=conf_id).first()

        if config :
            config.exp_result = request.get_json()["exp_result"]
            config.start_date_facture_verification = request.get_json()["start_date_facture_verification"]
            config.name = request.get_json()["name"]
            config.UpdatedAt =  datetime.now()
            verify_jwt_in_request()
            try:
                identity = get_jwt_identity()
                config.UpdatedBy = User.query.get(identity)
            except : 
                config.UpdatedBy = None

            db.session.commit()

            return config.to_dict()
        

@configuration.route('get/<int:conf_id>', methods=["GET"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.configuration.get_config"])
def get_config(config_ig):
    """Récupérer les données d'une configuration

    Args:
        config_ig (int): L'ID de la configuration à récupérer
    """

    if request.method == "GET":
        config = db.session.select(Configuration).filter_by(id=config_ig).first()

        if config :
            return config.to_dict()
        return {"message": "Configuration non trouvée"}
    

@configuration.route('delete/<int:conf_id>', methods=["DELETE"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.configuration.delete_config"])
def delete_conf(conf_id):
    """SUPPRIMER UNE CONFIGURATION DE BASE

    Args:
        conf_id (int): L'ID de la configuration à supprimer
    """

    if request.method == "DELETE":
        config = db.session.select(Configuration).filter_by(id=conf_id).first()

        if config :
            db.session.delete(config)
            db.session.commit()
            return {"message": "Configuration supprimée avec succès"}
        return {"message": "Configuration non trouvée"}
    
