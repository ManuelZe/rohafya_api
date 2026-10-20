"""API du bilan prénuptial : /premarital/…

- Patient : lecture seule de son bilan, gestion des médecins autorisés.
- Médecin : lecture et modification du bilan des seuls patients qui l'ont autorisé.
- Super-administrateur : catalogue des examens.
"""
from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import current_user

from Rohafya.saas.decorators import login_needed, register_error_handler, super_admin_required
from Rohafya.saas.services import SaasError, doctor_of_user, patient_of_user
from . import services

premarital = Blueprint("premarital", __name__, url_prefix="/premarital")
register_error_handler(premarital)


def _json():
    return request.get_json(silent=True) or {}


def _patient():
    patient = patient_of_user(current_user.id)
    if patient is None:
        raise SaasError("Cet espace est réservé aux patients.", 403)
    return patient


def _doctor():
    doctor = doctor_of_user(current_user.id)
    if doctor is None:
        raise SaasError("Seul un médecin peut modifier un bilan prénuptial.", 403)
    return doctor


# =====================================================================
# Patient : son bilan (lecture seule) et les médecins qu'il autorise
# =====================================================================

@premarital.route("/me", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_bilan():
    patient = _patient()
    data = services.bilan(patient)
    data["doctors"] = services.patient_authorizations(patient)
    return jsonify(data)


@premarital.route("/me/history", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def my_history():
    return jsonify(services.history(_patient().id))


@premarital.route("/me/doctors", methods=["POST"])
@cross_origin(supports_credentials=True)
@login_needed
def authorize_doctor():
    patient = _patient()
    auth = services.authorize_doctor(patient, _json().get("matricule"), current_user)
    return {
        "message": "Ce médecin peut désormais remplir votre bilan prénuptial.",
        "doctor": services.authorization_view(auth),
    }, 201


@premarital.route("/me/doctors/<int:auth_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@login_needed
def revoke_doctor(auth_id):
    services.revoke_authorization(_patient(), auth_id, current_user)
    return {"message": "Ce médecin n'a plus accès à votre bilan prénuptial."}


# =====================================================================
# Médecin : patients qui l'ont autorisé
# =====================================================================

@premarital.route("/doctor/patients", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def doctor_patients():
    return jsonify(services.doctor_patients(_doctor()))


@premarital.route("/doctor/patients/<int:patient_id>", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def doctor_bilan(patient_id):
    patient = services.require_doctor_access(_doctor(), patient_id)
    return jsonify(services.bilan(patient))


@premarital.route("/doctor/patients/<int:patient_id>/history", methods=["GET"])
@cross_origin(supports_credentials=True)
@login_needed
def doctor_history(patient_id):
    services.require_doctor_access(_doctor(), patient_id)
    return jsonify(services.history(patient_id))


@premarital.route("/doctor/patients/<int:patient_id>/exams/<string:code>", methods=["PUT"])
@cross_origin(supports_credentials=True)
@login_needed
def doctor_update_exam(patient_id, code):
    doctor = _doctor()
    patient = services.require_doctor_access(doctor, patient_id)
    exam_type, row = services.update_exam(doctor, patient, code, _json(), current_user)
    return {"message": "Examen enregistré.", "exam": services.exam_view(exam_type, row)}


# =====================================================================
# Super-administrateur : catalogue des examens
# =====================================================================

@premarital.route("/catalogue", methods=["GET"])
@cross_origin(supports_credentials=True)
@super_admin_required
def catalogue():
    return jsonify(services.catalogue_admin())


@premarital.route("/catalogue", methods=["POST"])
@cross_origin(supports_credentials=True)
@super_admin_required
def create_catalogue_entry():
    exam_type = services.create_type(_json(), current_user)
    return {"message": "Examen ajouté au catalogue.", "exam": services.type_view(exam_type, with_usage=True)}, 201


@premarital.route("/catalogue/<int:type_id>", methods=["PUT"])
@cross_origin(supports_credentials=True)
@super_admin_required
def update_catalogue_entry(type_id):
    exam_type = services.update_type(type_id, _json(), current_user)
    return {"message": "Catalogue mis à jour.", "exam": services.type_view(exam_type, with_usage=True)}
