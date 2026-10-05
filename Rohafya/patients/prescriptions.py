from flask import Blueprint, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select
from io import BytesIO
from werkzeug.utils import secure_filename
from Rohafya.deco.decorators import roles_required, require_any_permission
from Rohafya.patients.allow_dayx import get_days_after
from datetime import datetime, timedelta
from Rohafya.accounts.models import Prescriptions, User


prescriptions = Blueprint('prescription', __name__, url_prefix='/prescription')


ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS


@prescriptions.route('/all_prescriptions/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.all_prescriptions",
                         "administration.patients_prescriptions.all_prescriptions"])
def all_prescriptions():

    prescriptions = db.session.execute(select(Prescriptions)).scalars().all()

    prescriptions_list = [prescription.to_dict() for prescription in prescriptions]

    return jsonify(prescriptions_list)



def to_bool(value):
    """Convertit un champ formulaire en booléen Python."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ['true', '1', 'yes', 'on']
    return False


@prescriptions.route('/add/', methods=['POST'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.add_prescriptions",
                         "administration.patients_prescriptions.add_prescriptions"])
def add_prescriptions():

    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    patient = user.patients if user and user.patients else None

    if request.method == 'POST':
        NameDoctor = request.form.get('NameDoctor')
        OrdreDoctor = request.form.get('OrdreDoctor')
        Demande_devis = to_bool(request.form.get('demande_devis'))
        Description = request.form.get('Description')
        file = request.files.get('file')

        image_data = None
        image_mimetype = None

        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            image_data = file.read()
            image_mimetype = file.mimetype
        elif file:
            return {'message' : 'Type de fichier non autorisé.'}
        
        new_prescription = Prescriptions(
            NameDoctor = NameDoctor,
            OrdreDoctor = OrdreDoctor,
            Demande_devis = Demande_devis,
            Description = Description,
            image_data = image_data,
            image_mimetype = image_mimetype,
            patient_id = patient.id if patient else None
        )

        db.session.add(new_prescription)
        db.session.commit()
        
        try:
            db.session.add(new_prescription)
            db.session.commit()
            return {"Prescription" : new_prescription.to_dict()}
        except Exception as e:
            db.session.rollback()
            return {'message' : 'Erreur lors de l\'ajout de la prescription.'}


@prescriptions.route('/del/<int:presc_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.delete_prescription",
                         "administration.patients_prescriptions.delete_prescription"])
def delete_prescription(presc_id):

    prescription = None
    if presc_id:
        prescription = db.session.execute(select(Prescriptions).filter_by(id=presc_id)).scalar_one_or_none()
    else:
        return {"Message": "L'Id de la prescription doit être fourni. "}
    
    if not prescription:
        return {"message" : "Prescription Non Trouvée."}
    
    db.session.delete(prescription)
    db.session.commit()

    return {"Message" : "Prescription Supprimée Avec Succès."}
        

@prescriptions.route('/<int:presc_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.get_prescription",
                         "administration.patients_prescriptions.get_prescription"])
def get_prescription(presc_id):
    
    prescription = None
    if presc_id:
        prescription = db.session.execute(select(Prescriptions).filter_by(id=presc_id)).scalar_one_or_none()
    else:
        return {"Message": "L'Id de la prescription doit être fourni. "}
    
    if not prescription:
        return {"message" : "Prescription Non Trouvée."}
    
    return {"Prescription" : prescription.to_dict()}


@prescriptions.route('/devis/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.get_devis_prescriptions",
                         "administration.patients_prescriptions.get_devis_prescriptions"])
def get_devis_prescriptions():

    devis_prescriptions = db.session.execute(select(Prescriptions).filter_by(Demande_devis=True)).scalars().all()

    devis_list = [prescription.to_dict() for prescription in devis_prescriptions]

    return jsonify(devis_list)  


@prescriptions.route('/image/<int:presc_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.recuperer_image_prescription",
                         "administration.patients_prescriptions.recuperer_image_prescription"])
def recuperer_image(presc_id):
    """RECUPÉRER L'IMAGE D'UNE PRESCRIPTION"""

    prescription = db.session.execute(select(Prescriptions).filter_by(id=presc_id)).scalar_one_or_none()
    if not prescription.image_data:
        return {"message": "Aucune image trouvée pour cette prescription."}, 404
    
    return send_file(BytesIO(prescription.image_data), mimetype=prescription.image_mimetype, as_attachment=False)


@prescriptions.route('/patient/<int:pat_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["patients.patients_prescriptions.all_user_prescription",
                         "administration.patients_prescriptions.all_user_prescription"])
def all_user_presc(pat_id):

    patient = db.session.execute(select(Prescriptions).filter_by(patient_id=pat_id)).scalars().all()

    presc_list = [prescription.to_dict() for prescription in patient]

    return jsonify(presc_list)