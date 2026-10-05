from flask import Blueprint, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select
from io import BytesIO
from werkzeug.utils import secure_filename
from Rohafya.deco.decorators import require_any_permission, roles_required
from Rohafya.patients.allow_dayx import get_days_after
from datetime import datetime, timedelta
from Rohafya.accounts.models import SavePatients, User


save_patients = Blueprint('enregistrement_patient', __name__, url_prefix='/save_patient')


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}

def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@save_patients.route('/all_save_patients/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.saved_patients.all_saved_patients",
                         "administration.saved_patients.all_saved_patients"])
def all_save_patients():

    save_patients = db.session.execute(select(SavePatients)).scalars().all()

    save_patients_list = [save_patient.to_dict() for save_patient in save_patients]

    return jsonify(save_patients_list)


def to_bool(value):
    """Convertit un champ formulaire en booléen Python."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ['true', '1', 'yes', 'on']
    return False


@save_patients.route('/add/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Patient'])
@require_any_permission(["patients.saved_patients.add_save_patients"])
def add_save_patients():

    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    patient = user.patients if user and user.patients else None

    if request.method == 'POST':
        description = request.form.get('description')
        nom = request.form.get('nom')
        prenom = request.form.get('prenom')

        if 'file' not in request.files:
            return jsonify({"msg": "No file part in the request"}), 400

        file = request.files.get('file')
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            image_data = file.read()
            image_mimetype = file.mimetype
        elif file:
            return {'error' : 'Type de fichier non autorisé.'}
        
 
        new_save_patient = SavePatients(
            nom=nom,
            prenom=prenom,
            description=description,
            image_data=image_data,
            image_mimetype=image_mimetype,
            patient_id=patient.id
        )

        db.session.add(new_save_patient)
        db.session.commit()

        return jsonify({"msg": "File successfully uploaded"}), 201
        

@save_patients.route('/get_image/<int:save_patient_id>/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.saved_patients.get_image",
                         "administration.saved_patients.get_image"])
def get_image(save_patient_id):
    save_patient = db.session.execute(select(SavePatients).filter_by(id=save_patient_id)).scalar_one_or_none()

    if not save_patient or not save_patient.image_data:
        return jsonify({"msg": "Image not found"}), 404

    return send_file(
        BytesIO(save_patient.image_data),
        mimetype=save_patient.image_mimetype,
        as_attachment=False,
        download_name=f"save_patient_{save_patient_id}"
    )


@save_patients.route('/delete/<int:save_patient_id>/', methods=['DELETE'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.saved_patients.delete_save_patient",
                         "administration.saved_patients.delete_save_patient"])
def delete_save_patient(save_patient_id):
    save_patient = db.session.execute(select(SavePatients).filter_by(id=save_patient_id)).scalar_one_or_none()

    if not save_patient:
        return jsonify({"msg": "SavePatient not found"}), 404

    db.session.delete(save_patient)
    db.session.commit()

    return jsonify({"msg": "SavePatient successfully deleted"}), 200


@save_patients.route('/validate/<int:save_patient_id>/', methods=['POST'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin'])
@jwt_required()
@require_any_permission(["administration.saved_patients.validate_saved_patient"])
def validate_save_patient(save_patient_id):
    current_user = get_jwt_identity()

    save_patient = db.session.execute(select(SavePatients).filter_by(id=save_patient_id)).scalar_one_or_none()

    if not save_patient:
        return jsonify({"msg": "SavePatient not found"}), 404

    save_patient.validated = True
    save_patient.validated_by = current_user
    save_patient.validated_at = datetime.now()

    db.session.commit()

    return jsonify({"msg": "SavePatient successfully validated"}), 200


@save_patients.route('/get/<int:save_patient_id>/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.saved_patients.get_validated_save_patient",
                         "administration.saved_patients.get_validated_save_patient"])
def get_validated_save_patient(save_patient_id):
    save_patient = db.session.execute(select(SavePatients).filter_by(id=save_patient_id)).scalar_one_or_none()

    if not save_patient:
        return jsonify({"msg": "SavePatient not found"}), 404

    return jsonify({
        "id": save_patient.id,
        "description": save_patient.description,
        "nom" : save_patient.nom,
        "prenom" : save_patient.prenom,
        "Create_date": save_patient.Create_date,
        "validated": save_patient.validated,
        "validated_by": save_patient.validated_by,
        "validated_at": save_patient.validated_at
    }), 200


@save_patients.route('/get_patient_saves/<int:patient_id>/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.saved_patients.get_patient_saves",
                         "administration.saved_patients.get_patient_saves"])
def get_patient_saves(patient_id):
    saves = db.session.execute(select(SavePatients).filter_by(patient_id=patient_id)).scalars().all()

    saves_list = [save.to_dict() for save in saves]

    return jsonify(saves_list), 200


