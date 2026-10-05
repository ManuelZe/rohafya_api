from flask_cors import cross_origin
from flask_jwt_extended import jwt_required

from Rohafya.deco.decorators import roles_required, require_any_permission
from ..accounts.models import Notifications_Type
from flask import Blueprint, request, jsonify
from Rohafya import db
from sqlalchemy import select

not_type = Blueprint('notifications_types', __name__, url_prefix='/notifications/types/')

@not_type.route('add', methods=["POST"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required("Admin")
@require_any_permission(["administration.notifications_types.add_notification_type"])
def add_notification_type():
    if request.method == 'POST' :
        name = request.get_json()['name']
        description = request.get_json().get('description', '')

        existing_type = db.session.execute(select(Notifications_Type).filter_by(name=name)).scalar_one_or_none()
        if existing_type :
            return {"Message ": f" Le type de notification {name} existe déjà. "}

        new_type = Notifications_Type(name=name, description=description)
        db.session.add(new_type)
        db.session.commit()

        return new_type.to_dict()


@not_type.route("/", methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required("Admin")
@require_any_permission(["administration.notifications_types.all_notification_types"])
def all_notification_types():

    types = db.session.execute(select(Notifications_Type)).scalars().all()
    types_list = [ntype.to_dict() for ntype in types]

    return jsonify(types_list)


@not_type.route("/del/<int:type_id>", methods=['DELETE'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required("Admin")
@require_any_permission(["administration.notifications_types.delete_notification_type"])
def del_notification_type(type_id):

    if type_id:
        ntype = db.session.execute(select(Notifications_Type).filter_by(id=type_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID du type de notification doit être fourni."}

    if not ntype :
        return {"Message" : " Aucun type de notification trouvé."}

    db.session.delete(ntype)
    db.session.commit()

    return {"Message" : "Type de notification Supprimé avec succès."}


@not_type.route('/modify/<int:type_id>', methods=["PUT"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required("Admin")
@require_any_permission(["administration.notifications_types.modify_notification_type"])
def modify_notification_type(type_id):

    if type_id:
        ntype = db.session.execute(select(Notifications_Type).filter_by(id=type_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID du type de notification doit être fourni."}

    if not ntype :
        return {"Message" : " Aucun type de notification trouvé."}

    if request.method == "PUT":
        name = request.get_json().get('name', ntype.name)
        description = request.get_json().get('description', ntype.description)

        ntype.name = name
        ntype.description = description

        db.session.commit()

        return ntype.to_dict()



@not_type.route('/<int:type_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Docteur'])
@require_any_permission(["administration.notifications_types.get_notification_type",
                         "patients.notifications_types.get_notification_type",
                         "doctors.notifications_types.get_notification_type",])
def get_notification_type(type_id):

    if type_id:
        ntype = db.session.execute(select(Notifications_Type).filter_by(id=type_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID du type de notification doit être fourni."}

    if not ntype :
        return {"Message" : " Aucun type de notification trouvé."}

    return ntype.to_dict()


