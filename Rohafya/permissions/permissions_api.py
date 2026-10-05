from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select, exc
from ..accounts.models import Permissions, User, Send_Results, Notifications_Type, Notifications, Doctors
import datetime

from Rohafya.deco.decorators import require_any_permission, roles_required

permissions = Blueprint('permissions', __name__, url_prefix='/permissions/')

@permissions.route('/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
def all_permissions():

    permissions = db.session.execute(select(Permissions)).scalars().all()

    permissions_lists = [permission.to_dict() for permission in permissions]

    return jsonify(permissions_lists)

