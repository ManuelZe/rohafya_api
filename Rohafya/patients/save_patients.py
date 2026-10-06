from flask import Blueprint, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import current_user, jwt_required
from Rohafya import db
from sqlalchemy import select
from io import BytesIO
from Rohafya.deco.decorators import require_any_permission
from Rohafya.accounts.models import SavePatients
from Rohafya.saas.constants import AUTHOR_DOCTOR, AUTHOR_PATIENT, SUBMISSION_DONE, SUBMISSION_PRE_REGISTRATION
from Rohafya.saas.decorators import register_error_handler
from Rohafya.saas.services import SaasError, patient_of_user
from Rohafya.saas import submissions


save_patients = Blueprint('enregistrement_patient', __name__, url_prefix='/save_patient')
register_error_handler(save_patients)

KIND = SUBMISSION_PRE_REGISTRATION
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg'}


def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def _audience():
    """Espace d'où vient l'appel : patient (défaut) ou doctor."""
    return AUTHOR_DOCTOR if (request.values.get('audience') or '').lower() == AUTHOR_DOCTOR else AUTHOR_PATIENT


@save_patients.route('/all_save_patients/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.saved_patients.all_saved_patients",
                         "doctors.saved_patients.all_saved_patients",
                         "administration.saved_patients.all_saved_patients"])
def all_save_patients():
    """Pré-enregistrements de l'utilisateur (?audience=doctor pour l'espace médecin) ; tous pour un administrateur global."""
    if submissions.is_global_admin(current_user, KIND) and request.args.get('audience') is None:
        items = db.session.execute(select(SavePatients).order_by(SavePatients.id.desc())).scalars().all()
    else:
        items = submissions.own_items(KIND, current_user, _audience())
    return jsonify(submissions.with_submission(KIND, items))


@save_patients.route('/add/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.saved_patients.add_save_patients",
                         "doctors.saved_patients.add_save_patients"])
def add_save_patients():
    """Pré-enregistrement envoyé à un établissement (multipart : tenant_id, nom, prenom, description, file)."""
    tenant = submissions.target_tenant(request.form.get('tenant_id'))
    role = submissions.author_role(current_user, _audience())

    nom = (request.form.get('nom') or '').strip()
    prenom = (request.form.get('prenom') or '').strip()
    if not nom or not prenom:
        raise SaasError('Le nom et le prénom de la personne à pré-enregistrer sont obligatoires.', 400)

    file = request.files.get('file')
    if file is None:
        raise SaasError("Joignez une image (pièce d'identité, carte d'assurance…).", 400)
    if not allowed_file(file.filename):
        raise SaasError('Type de fichier non autorisé (PNG ou JPG).', 400)

    patient = patient_of_user(current_user.id) if role == AUTHOR_PATIENT else None
    save_patient = SavePatients(
        nom=nom[:100],
        prenom=prenom[:100],
        description=(request.form.get('description') or '')[:500],
        image_data=file.read(),
        image_mimetype=file.mimetype,
        patient_id=patient.id if patient else None,
    )
    db.session.add(save_patient)
    db.session.flush()
    submission = submissions.create_submission(KIND, save_patient, tenant, current_user, role,
                                               patient_name=f"{prenom} {nom}" if role == AUTHOR_DOCTOR else None)
    data = save_patient.to_dict()
    data['submission'] = submission.to_dict()
    return jsonify(data), 201


@save_patients.route('/get_image/<int:save_patient_id>/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.saved_patients.get_image",
                         "doctors.saved_patients.get_image",
                         "administration.saved_patients.get_image"])
def get_image(save_patient_id):
    save_patient, _ = submissions.get_item(KIND, save_patient_id)
    if not save_patient.image_data:
        return jsonify({"msg": "Image not found"}), 404

    return send_file(
        BytesIO(save_patient.image_data),
        mimetype=save_patient.image_mimetype,
        as_attachment=False,
        download_name=f"save_patient_{save_patient_id}"
    )


@save_patients.route('/delete/<int:save_patient_id>/', methods=['DELETE'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.saved_patients.delete_save_patient",
                         "doctors.saved_patients.delete_save_patient",
                         "administration.saved_patients.delete_save_patient"])
def delete_save_patient(save_patient_id):
    save_patient, _ = submissions.get_item(KIND, save_patient_id, write=True)
    submissions.delete_item(KIND, save_patient)
    return jsonify({"msg": "SavePatient successfully deleted"}), 200


@save_patients.route('/validate/<int:save_patient_id>/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.saved_patients.validate_saved_patient"])
def validate_save_patient(save_patient_id):
    """Ancienne validation globale ; les établissements répondent depuis leur console (/admin/demandes)."""
    save_patient, submission = submissions.get_item(KIND, save_patient_id)
    if submission is not None:
        submissions.respond(submission, current_user, status=SUBMISSION_DONE)
    else:
        from datetime import datetime
        save_patient.validated = True
        save_patient.validated_by = current_user.id
        save_patient.validated_at = datetime.now()
        db.session.commit()
    return jsonify({"msg": "SavePatient successfully validated"}), 200


@save_patients.route('/get/<int:save_patient_id>/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.saved_patients.get_validated_save_patient",
                         "doctors.saved_patients.get_validated_save_patient",
                         "administration.saved_patients.get_validated_save_patient"])
def get_validated_save_patient(save_patient_id):
    save_patient, submission = submissions.get_item(KIND, save_patient_id)
    data = save_patient.to_dict()
    data['submission'] = submission.to_dict() if submission else None
    return jsonify(data), 200


@save_patients.route('/get_patient_saves/<int:patient_id>/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.saved_patients.get_patient_saves",
                         "administration.saved_patients.get_patient_saves"])
def get_patient_saves(patient_id):
    patient = patient_of_user(current_user.id)
    if not submissions.is_global_admin(current_user, KIND) and (patient is None or patient.id != patient_id):
        return jsonify({"msg": "Accès refusé."}), 403
    saves = db.session.execute(select(SavePatients).filter_by(patient_id=patient_id)).scalars().all()
    return jsonify(submissions.with_submission(KIND, saves)), 200
