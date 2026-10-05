import functools
from flask import Flask, jsonify
from flask_jwt_extended import jwt_required
from flask_restful import Api
import ast
import psycopg2
from flask import (
    Blueprint, request, g, redirect, flash, render_template, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import login_required
from flask_tryton import Tryton
from Rohafya.accounts.models import Doctors, User, Role, UserActivity
from flask_cors import CORS, cross_origin
from Rohafya import db
from flask import Flask
from flask_tryton import Tryton
from Rohafya.deco.decorators import require_any_permission, roles_required
from Rohafya.deco.generators import generate_random_letters, generate_random_letters_and_digits
from Rohafya.email.email import send_email
from datetime import date, datetime, timedelta, time
from Rohafya import tryton
from collections import defaultdict, UserList
from sqlalchemy import select

activity = Blueprint('activity', __name__, url_prefix='/activity/')

@activity.route('all', methods=["GET"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.user_activity.all_user_activity"])
def all_activity():
    activities = db.session.execute(select(UserActivity)).scalars().all()
    activities_list = [activity.to_dict() for activity in activities]

    return jsonify(activities_list)


@activity.route('one/<int:activity_id>', methods=["GET"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.user_activity.view_user_activity"])
def one_activity(activity_id):
    ativity = db.session.execute(select(UserActivity).filter_by(id=activity_id)).scalar_one_or_none()
    if activity:
        return jsonify(activity.to_dict())
    else:
        return jsonify({"message": "Activité non trouvée"})


@activity.route('delete/', methods=["DELETE"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.user_activity.delete_all_user_activity"])
def delete_all_activity():
    try:
        num_rows_deleted = db.session.query(UserActivity).delete()
        db.session.commit()
        return jsonify({"message": f"{num_rows_deleted} activités supprimées."})
    except Exception as e:
        db.session.rollback()
        return jsonify({"message": "Erreur lors de la suppression des activités.", "error": str(e)})