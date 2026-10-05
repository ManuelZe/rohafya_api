from flask_jwt_extended import jwt_required
from Rohafya.deco.decorators import roles_required, require_any_permission
from ..accounts.models import User, Role
from flask import Blueprint, request, jsonify
from Rohafya import db
from functools import wraps
from sqlalchemy import select
from ..accounts.models import Etiquettes_notifications
from flask_cors import CORS, cross_origin

etiquettes = Blueprint('etiquettes', __name__, url_prefix='/notifications/etiquettes/')

@etiquettes.route('/add', methods=["POST"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_etiquettes.add_notification_etiquette"])
def add_etiquette():
    if request.method == 'POST' :
        name = request.get_json()['name']
        description = request.get_json().get('description', '')

        existing_etiquette = db.session.execute(select(Etiquettes_notifications).filter_by(name=name)).scalar_one_or_none()
        if existing_etiquette :
            return {"Message ": f" L'étiquette {name} existe déjà. "}

        new_etiquette = Etiquettes_notifications(name=name, description=description)
        db.session.add(new_etiquette)
        db.session.commit()

        return new_etiquette.to_dict()


@etiquettes.route("/", methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_etiquettes.all_etiquettes_notifications"])
def all_etiquettes():

    etiquettes = db.session.execute(select(Etiquettes_notifications)).scalars().all()
    etiquettes_list = [etiquette.to_dict() for etiquette in etiquettes]

    return jsonify(etiquettes_list)


@etiquettes.route("/del/<int:etiquette_id>", methods=['DELETE'])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_etiquettes.delete_etiquette_notification"])
def del_etiquette(etiquette_id):
    
    if etiquette_id:
        etiquette = db.session.execute(select(Etiquettes_notifications).filter_by(id=etiquette_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID de l'étiquette doit être fourni."}

    if not etiquette :
        return {"Message" : " Aucune étiquette trouvée."}

    db.session.delete(etiquette)
    db.session.commit()

    return {"Message" : "Étiquette Supprimée avec succès."}


@etiquettes.route('/modify/<int:etiquette_id>', methods=["PUT"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_etiquettes.modify_etiquette_notification"])
def modify_etiquette(etiquette_id):

    """MODIFIER UNE ÉTIQUETTE DANS LA TABLE NOTIFICATIONS
    """
    etiquette = db.session.execute(select(Etiquettes_notifications).filter_by(id=etiquette_id)).scalar_one_or_none()
    if not etiquette:
        return {"Message": " Aucune étiquette trouvée."}

    if request.method == "PUT":
        name = request.get_json().get('name', etiquette.name)
        description = request.get_json().get('description', etiquette.description)

        etiquette.name = name
        etiquette.description = description
        db.session.commit()

        return etiquette.to_dict()
