from flask import Blueprint, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import current_user, jwt_required
from Rohafya import db
from sqlalchemy import select
from io import BytesIO
from Rohafya.deco.decorators import require_any_permission
from Rohafya.accounts.models import Prescriptions
from Rohafya.saas.constants import AUTHOR_DOCTOR, AUTHOR_PATIENT, SUBMISSION_PRESCRIPTION
from Rohafya.saas.decorators import register_error_handler
from Rohafya.saas.services import SaasError, patient_of_user
from Rohafya.saas import submissions


prescriptions = Blueprint('prescription', __name__, url_prefix='/prescription')
register_error_handler(prescriptions)

KIND = SUBMISSION_PRESCRIPTION
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}


def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


def to_bool(value):
    """Convertit un champ formulaire en booléen Python."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ['true', '1', 'yes', 'on']
    return False


def _audience():
    """Espace d'où vient l'appel : patient (défaut) ou doctor."""
    return AUTHOR_DOCTOR if (request.values.get('audience') or '').lower() == AUTHOR_DOCTOR else AUTHOR_PATIENT


@prescriptions.route('/all_prescriptions/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.all_prescriptions",
                         "doctors.prescriptions.all_prescriptions",
                         "administration.patients_prescriptions.all_prescriptions"])
def all_prescriptions():
    """Prescriptions de l'utilisateur connecté (son espace patient ou médecin, ?audience=doctor).

    Les anciens administrateurs globaux (permission administration.*) voient toutes les prescriptions.
    """
    if submissions.is_global_admin(current_user, KIND) and request.args.get('audience') is None:
        items = db.session.execute(select(Prescriptions).order_by(Prescriptions.id.desc())).scalars().all()
    else:
        items = submissions.own_items(KIND, current_user, _audience())
    return jsonify(submissions.with_submission(KIND, items))


@prescriptions.route('/add/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.add_prescriptions",
                         "doctors.prescriptions.add_prescriptions",
                         "administration.patients_prescriptions.add_prescriptions"])
def add_prescriptions():
    """Prescription envoyée à un établissement (multipart : tenant_id, Description, file, demande_devis…).

    Patient : NameDoctor / OrdreDoctor = médecin prescripteur. Médecin : son nom et son numéro
    d'ordre sont repris de son profil ; patient_name (patient concerné) est obligatoire.
    """
    tenant = submissions.target_tenant(request.form.get('tenant_id'))
    role = submissions.author_role(current_user, _audience())

    file = request.files.get('file')
    image_data = image_mimetype = None
    if file and allowed_file(file.filename):
        image_data = file.read()
        image_mimetype = file.mimetype
    elif file:
        raise SaasError('Type de fichier non autorisé (PNG, JPG ou GIF).', 400)

    patient_name = None
    if role == AUTHOR_DOCTOR:
        name_doctor, ordre_doctor = submissions.doctor_identity(current_user)
        patient_name = (request.form.get('patient_name') or '').strip()
        if not patient_name:
            raise SaasError('Indiquez le patient concerné par la prescription.', 400)
        patient = None
    else:
        name_doctor = (request.form.get('NameDoctor') or '').strip()
        ordre_doctor = (request.form.get('OrdreDoctor') or '').strip()
        if not name_doctor:
            raise SaasError('Le nom du médecin prescripteur est obligatoire.', 400)
        patient = patient_of_user(current_user.id)

    prescription = Prescriptions(
        NameDoctor=name_doctor,
        OrdreDoctor=ordre_doctor,
        Demande_devis=to_bool(request.form.get('demande_devis')),
        Description=(request.form.get('Description') or '')[:500],
        image_data=image_data,
        image_mimetype=image_mimetype,
        patient_id=patient.id if patient else None,
    )
    db.session.add(prescription)
    db.session.flush()
    submission = submissions.create_submission(KIND, prescription, tenant, current_user, role, patient_name)
    data = prescription.to_dict()
    data['submission'] = submission.to_dict()
    return {"Prescription": data}, 201


@prescriptions.route('/del/<int:presc_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.delete_prescription",
                         "doctors.prescriptions.delete_prescription",
                         "administration.patients_prescriptions.delete_prescription"])
def delete_prescription(presc_id):
    prescription, _ = submissions.get_item(KIND, presc_id, write=True)
    submissions.delete_item(KIND, prescription)
    return {"Message": "Prescription Supprimée Avec Succès."}


@prescriptions.route('/<int:presc_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.get_prescription",
                         "doctors.prescriptions.get_prescription",
                         "administration.patients_prescriptions.get_prescription"])
def get_prescription(presc_id):
    prescription, submission = submissions.get_item(KIND, presc_id)
    data = prescription.to_dict()
    data['submission'] = submission.to_dict() if submission else None
    return {"Prescription": data}


@prescriptions.route('/devis/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.get_devis_prescriptions",
                         "doctors.prescriptions.get_devis_prescriptions",
                         "administration.patients_prescriptions.get_devis_prescriptions"])
def get_devis_prescriptions():
    """Prescriptions avec demande de devis (de l'utilisateur ; toutes pour un administrateur global)."""
    if submissions.is_global_admin(current_user, KIND) and request.args.get('audience') is None:
        items = db.session.execute(select(Prescriptions).filter_by(Demande_devis=True)).scalars().all()
    else:
        items = [p for p in submissions.own_items(KIND, current_user, _audience()) if p.Demande_devis]
    return jsonify(submissions.with_submission(KIND, items))


@prescriptions.route('/image/<int:presc_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.recuperer_image_prescription",
                         "doctors.prescriptions.recuperer_image_prescription",
                         "administration.patients_prescriptions.recuperer_image_prescription"])
def recuperer_image(presc_id):
    """RECUPÉRER L'IMAGE D'UNE PRESCRIPTION"""
    prescription, _ = submissions.get_item(KIND, presc_id)
    if not prescription.image_data:
        return {"message": "Aucune image trouvée pour cette prescription."}, 404
    return send_file(BytesIO(prescription.image_data), mimetype=prescription.image_mimetype, as_attachment=False)


@prescriptions.route('/patient/<int:pat_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.all_user_prescription",
                         "administration.patients_prescriptions.all_user_prescription"])
def all_user_presc(pat_id):
    patient = patient_of_user(current_user.id)
    if not submissions.is_global_admin(current_user, KIND) and (patient is None or patient.id != pat_id):
        return {"message": "Accès refusé."}, 403
    items = db.session.execute(select(Prescriptions).filter_by(patient_id=pat_id)).scalars().all()
    return jsonify(submissions.with_submission(KIND, items))
