from flask import Blueprint,g, render_template, redirect, url_for, flash, request, session
from flask_jwt_extended import jwt_required
from flask_login import current_user, login_user, logout_user
from .models import User, Role, Permissions
from flask import jsonify
from .models import db
from functools import wraps
from werkzeug.security import check_password_hash, generate_password_hash
from flask_cors import CORS, cross_origin
from Rohafya.deco.decorators import require_any_permission, roles_required
from sqlalchemy import select

from flask_mail import Message

roles = Blueprint('roles', __name__, url_prefix='/roles/')

@roles.route('/add', methods=["POST"])
@cross_origin(supports_credentials=True)
# @require_any_permission(["administration.roles.add_role"])
@roles_required(["Admin"])
@jwt_required()
def add_roles():
    data = request.get_json()
    name = data.get("name")
    permission_codes = data.get("permissions", [])

    if not name:
        return {"Message": "Le nom du rôle est obligatoire."}, 400

    role = db.session.execute(select(Role).filter_by(name=name)).scalar_one_or_none()
    if role:
        return {"Message": f"Le rôle {name} existe déjà."}, 400

    # Création du rôle
    role = Role(name=name)

    # Récupération des permissions demandées
    if permission_codes:
        permissions = db.session.execute(
            select(Permissions).filter(Permissions.code.in_(permission_codes))
        ).scalars().all()
        role.permissions = permissions

    db.session.add(role)
    db.session.commit()

    return {
        "id": role.id,
        "name": role.name,
        "permissions": [p.code for p in role.permissions]
    }, 201


@roles.route("/", methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @require_any_permission(["administration.roles.all_roles"])
@roles_required(["Admin"])
def all_roles():
    roles = db.session.execute(select(Role)).scalars().all()

    roles_list = []
    for role in roles:
        roles_list.append({
            "id": role.id,
            "name": role.name,
            "permissions": [p.code for p in role.permissions]
        })

    return jsonify(roles_list)


@roles.route("/del/<int:role_id>", methods=['DELETE'])
@cross_origin(supports_credentials=True)
# @require_any_permission(["administration.roles.delete_role"])
@roles_required(["Admin"])
@jwt_required()
def del_roles(role_id):
    role = db.session.execute(select(Role).filter_by(id=role_id)).scalar_one_or_none()

    if not role:
        return {"Message": "Aucun rôle trouvé."}, 404

    db.session.delete(role)
    db.session.commit()

    return {"Message": "Rôle supprimé avec succès."}


@roles.route('/edit/<int:role_id>', methods=["PUT"])
@cross_origin(supports_credentials=True)
@jwt_required()
@roles_required(["Admin"])
# @require_any_permission(["administration.roles.edit_role"])
def edit_role(role_id):
    data = request.get_json()

    permissions_to_add = data.get("permissions_to_add", [])
    permissions_to_remove = data.get("permissions_to_remove", [])

    role = db.session.execute(select(Role).filter_by(id=role_id)).scalar_one_or_none()
    if not role:
        return {"Message": "Rôle introuvable."}, 404

    # AJOUT
    if permissions_to_add:
        permissions = db.session.execute(
            select(Permissions).filter(Permissions.code.in_(permissions_to_add))
        ).scalars().all()

        for p in permissions:
            if p not in role.permissions:
                role.permissions.append(p)

    # SUPPRESSION
    if permissions_to_remove:
        permissions = db.session.execute(
            select(Permissions).filter(Permissions.code.in_(permissions_to_remove))
        ).scalars().all()

        for p in permissions:
            if p in role.permissions:
                role.permissions.remove(p)

    db.session.commit()

    return {
        "id": role.id,
        "name": role.name,
        "permissions": [p.code for p in role.permissions]
    }
