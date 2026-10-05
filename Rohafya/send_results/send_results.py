from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select, exc
from ..accounts.models import Patients, User, Send_Results, Notifications_Type, Notifications, Doctors
import datetime

from Rohafya.deco.decorators import require_any_permission, roles_required

send_result = Blueprint('send_result', __name__, url_prefix='/send_result')


@send_result.route('/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Doctor', 'Patient'])
@require_any_permission(["patients.send_results.send",
                         "administration.send_results.send",
                         "doctors.send_results.send"])
@tryton.transaction()
def send():
    
    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    patient = db.session.execute(select(Patients).filter_by(user_id=current_user)).scalar_one_or_none()

    patient_federation_id = patient.PatientFederationID if patient else None

    if not patient:
        return {"Message" : "Seul un patient peut envoyer des résultats."}
    
    
    if request.method == "POST" :
        doctor_id = request.get_json().get('doctor_id')
        exam_type = request.get_json().get('exam_type')
        exam_code = request.get_json().get('exam_code')
        envoi_email = request.get_json().get('envoi_email', False)
        patient_federation_id = patient_federation_id
        patient_id = patient.id if patient else None

        send_result = Send_Results(
            doctor_id = doctor_id,
            exam_type = exam_type,
            exam_code = exam_code,
            envoi_email = envoi_email,
            patient_federation_id = patient_federation_id,
            patient_id = patient_id
        )

        exists = db.session.execute(select(Send_Results).filter_by(
            doctor_id=doctor_id,
            exam_type=exam_type,
            exam_code=exam_code)).scalar_one_or_none()
        if exists:
            return {"Message": "Ce résultat a déjà été envoyé."}
        
        try :
            types =  db.session.execute(select(Notifications_Type).filter_by(name='INFOS')).scalar_one_or_none()
            if not types:
                types = Notifications_Type(name='INFOS', description='Informations Type Of All Notifications.')
                db.session.add(types)
                db.session.commit()

            patient_name = f"{patient.PatientName} {patient.PatientLastname}" if patient else "Inconnu"

            # USER_ID DU Docteur

            doctor = db.session.execute(select(Doctors).filter_by(id=doctor_id)).scalar_one_or_none()
            if doctor :
                doctor_user_id = doctor.user_id
    
            if types :
                notification = Notifications(
                    types=types.id,
                    title="Nouveau Résultat Envoyé.",
                    message=f"Un nouveau résultat d'examen de type '{exam_type}' avec le code '{exam_code}' a été envoyé par le patient {patient_name}. Matricule du patient : {patient_federation_id if patient else 'Inconnu'}.",
                    created_at=datetime.datetime.now(),
                    all_users=False,
                    user_id=doctor_user_id if doctor else None
                )
                db.session.add(notification)
                db.session.commit() 
            db.session.add(send_result)
            db.session.commit()
        except exc.DataError as e:
            return {"Message" : "Vérifier Le type de l'examen. Il doit être 'Laboratoire', 'Imagerie' ou 'Exploration'. "}

        return {"Résultat Envoyé" : send_result.to_dict()}

 
# Tous les résultats envoyés
@send_result.route('/all', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin'])
@jwt_required()
@require_any_permission(["administration.send_results.all_send_results"])
@tryton.transaction()
def all_send_results():

    send_results = db.session.execute(select(Send_Results)).scalars().all()

    send_results_list = [result.to_dict() for result in send_results]
    return jsonify(send_results_list)


#  Tous les résultats envoyés par un patient spécifique
@send_result.route('/patient/<int:patient_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient'])
@require_any_permission(["patients.send_results.results_by_patient",
                         "administration.send_results.results_by_patient"])
@tryton.transaction()
def results_by_patient(patient_id):

    patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()
    if not patient:
        return {"Message" : "Aucun patient trouvé."}

    send_results = db.session.execute(select(Send_Results).filter_by(patient_id=patient_id)).scalars().all()
    send_results_list = [result.to_dict() for result in send_results]

    return jsonify(send_results_list)


# Tous les résultats récus par un docteur spécifique
@send_result.route('/doctor/<int:doctor_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Doctor'])
@require_any_permission(["administration.send_results.results_by_doctor",
                         "doctors.send_results.results_by_doctor"])
@tryton.transaction()
def results_by_doctor(doctor_id):

    send_results = db.session.execute(select(Send_Results).filter_by(doctor_id=doctor_id)).scalars().all()
    send_results_list = [result.to_dict() for result in send_results]

    return jsonify(send_results_list)


# Supprimer un résultat envoyé
@send_result.route('/del/<int:result_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient'])
@require_any_permission(["patients.send_results.del_send_result",
                         "administration.send_results.del_send_result"])
@tryton.transaction()
def del_send_result(result_id):
    
    if result_id:
        result = db.session.execute(select(Send_Results).filter_by(id=result_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID du résultat doit être fourni."}

    if not result :
        return {"Message" : " Aucun résultat trouvé."}

    db.session.delete(result)
    db.session.commit()

    types = db.session.execute(select(Notifications_Type).filter_by(name="ALERT")).scalar_one_or_none()
    if not types:
        types = Notifications_Type(name="ALERT", description="ALERT Type Of All Notifications.")
        db.session.add(types)
        db.session.commit()

    # Informations sur le patients 
    patient = db.session.execute(select(Patients).filter_by(id=result.patient_id)).scalar_one_or_none()
    patient_name = f"{patient.PatientName} {patient.PatientLastname}" if patient else "Inconnu"

    # Informations sur le docteur
    doctor = db.session.execute(select(Doctors).filter_by(id=result.doctor_id)).scalar_one_or_none()
    doctor_user_id = doctor.user.id if doctor else None

    notification = Notifications(
        types = types.id,
        title = "Résultat Supprimé.",
        message = f"Le Patient {patient_name} vient de Supprimer le partage de résultat. Le résultat d'examen de type '{result.exam_type}' avec le code '{result.exam_code}' a été supprimé.",
        created_at = datetime.datetime.now(),
        all_users = False,
        user_id = doctor_user_id if doctor else None
    )

    try:
        db.session.add(notification)
        db.session.commit()
    except exc.IntegrityError as e:
        db.session.rollback()
        return {"Message": "Erreur lors de la création de la notification."}

    return {"Message" : "Résultat Envoyé Supprimé avec succès."}


# Modifier un résultat envoyé
@send_result.route('/modify/<int:result_id>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])

@jwt_required()
@require_any_permission(["patients.send_results.modify_send_result",
                         "administration.send_results.modify_send_result"])
@tryton.transaction()
def modify_send_result(result_id):

    if result_id:
        result = db.session.execute(select(Send_Results).filter_by(id=result_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID du résultat doit être fourni."}

    if not result :
        return {"Message" : " Aucun résultat trouvé."}

    if request.method == "PUT" :
        doctor_id = request.get_json().get('doctor_id', result.doctor_id)
        exam_type = request.get_json().get('exam_type', result.exam_type)
        exam_code = request.get_json().get('exam_code', result.exam_code)
        patient_federation_id = request.get_json().get('patient_federation_id', result.patient_federation_id)

        result.doctor_id = doctor_id
        result.exam_type = exam_type
        result.exam_code = exam_code
        result.patient_federation_id = patient_federation_id

        db.session.commit()

        return result.to_dict()
    

