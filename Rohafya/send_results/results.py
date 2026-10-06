from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, jwt_required, current_user
from Rohafya import db, tryton
from sqlalchemy import select
from ..accounts.models import Patients, User, Send_Results, Doctors
from Rohafya.saas.constants import EXAM_TYPE_TO_KIND
from Rohafya.saas.services import audit, find_patient_record, gnuhealth_ref, record_details_response, serialize_record


from Rohafya.deco.decorators import require_any_permission, roles_required

results = Blueprint('results', __name__, url_prefix='/result')


def prescriptor(exam_type, request_order):
    """Récupérer le prescripteur d'un ordre de laboratoire

    Args:
        request_order (object): L'ordre de laboratoire

    Returns:
        str: Le nom complet du prescripteur
    """

    if request_order :
        if exam_type == "Laboratoire" :
            request = tryton.pool.get("gnuhealth.patient.lab.test").search([('request', '=', request_order)])
       
        if exam_type == "Imagerie" :
            request = tryton.pool.get("gnuhealth.imaging.test.request").search([('request', '=', request_order )])
        
        if exam_type == "Exploration" :
            request = tryton.pool.get("gnuhealth.patient.exp.test").search([('request', '=', request_order )])

        if request and request[0].service:
            prescriptor = request[0].service.requestor.name.name+" "+request[0].service.requestor.name.lastname
            
            return prescriptor
        
        return {"Message" : "Aucun Service attaché à ce résultat"}
    return {"Message" : "L'Ordre doit être fourni. "}



def _shared_patient(patient_federation_id):
    return db.session.execute(select(Patients).filter_by(PatientFederationID=patient_federation_id)).scalar_one_or_none()


def _can_read_shared(exam_code, patient_federation_id, patient):
    """Le patient lui-même, un médecin destinataire du partage, ou l'administration."""
    if current_user.has_permission("administration.send_results_results.get_results"):
        return True
    if patient and patient.user_id == current_user.id:
        return True
    doctor = db.session.execute(select(Doctors).filter_by(user_id=current_user.id)).scalar_one_or_none()
    if doctor:
        shared = db.session.execute(
            select(Send_Results.id).filter_by(doctor_id=doctor.id, exam_code=exam_code, patient_federation_id=patient_federation_id)
        ).first()
        return shared is not None
    return False


def _external_exam(exam_type, exam_code, patient):
    kind = EXAM_TYPE_TO_KIND.get(exam_type)
    if not kind or not patient:
        return None, None
    return find_patient_record(patient, kind, exam_code)


def _tryton_patient(patient_federation_id, patient):
    """Patient GNU Health correspondant (matricule du lien GNU Health si le compte est un compte ROHAFYA)."""
    reference = gnuhealth_ref(patient) if patient else patient_federation_id
    if not reference:
        return []
    party = tryton.pool.get("party.party").search([("federation_account", "=", reference)])
    if not party:
        return []
    return tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)])


@results.route('/<string:exam_type>/<path:exam_code>/<string:patient_federation_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Doctor', 'Patient'])
@require_any_permission(["patients.send_results_results.get_results",
                         "administration.send_results_results.get_results",
                         "doctors.send_results_results.get_results"])
@tryton.transaction()
def get_results(exam_type, exam_code, patient_federation_id):

    rohafya_patient = _shared_patient(patient_federation_id)
    if not _can_read_shared(exam_code, patient_federation_id, rohafya_patient):
        return {"message": "Ce résultat ne vous a pas été partagé."}, 403

    record, record_tenant = _external_exam(exam_type, exam_code, rohafya_patient)
    if record is not None:
        audit("result.viewed", tenant_id=record_tenant.id, user_id=current_user.id, target=exam_code)
        return jsonify([serialize_record(record, record_tenant, rohafya_patient)])

    patient = _tryton_patient(patient_federation_id, rohafya_patient)
    if not patient:
        return {"Message ": "Aucun résultat trouvé pour ce patient."}

    if exam_type == "Laboratoire" :
        results = tryton.pool.get("gnuhealth.lab").search([('patient', '=', patient[0].id), ('name', '=', exam_code)])

        if results:
            results_list = []
            for result in results:
                results_list.append({
                    "id": result.id,
                    "analytes_summary" : result.analytes_summary,
                    "date_analysis": result.date_analysis,
                    "date_requested": result.date_requested,
                    "diagnosis": result.diagnosis,
                    "done_by": result.done_by.name.name+" "+result.done_by.name.lastname  if result.done_by else None,
                    "done_date": result.done_date,
                    "historize" : result.historize,
                    "macroscopie": result.macroscopie,
                    "microscopie": result.microscopie,
                    "name" : result.name,
                    "patient" : result.patient.name.name +" "+ result.patient.name.lastname if result.patient else None,
                    "matricule_patient" : patient_federation_id,
                    "qr" : str(result.qr),
                    "rec_name" : result.rec_name,
                    "renseignements" : result.renseignements,
                    "request_order" : result.request_order,
                    "requestor" : prescriptor(exam_type, result.request_order),
                    "results" : result.results,
                    "serializer" : result.serializer,
                    "serializer_current" : result.serializer_current,
                    "state" : result.state,
                    "test" : result.test.name,
                    "validated_by" : result.validated_by.name.name+" "+result.validated_by.name.lastname if result.validated_by else None,
                    "validation_date" : result.validation_date,
                })
            return jsonify(results_list)
        else:
            return {"Message ": "Aucun Résultat Laboratoire Trouvé pour ce patient."}
    
    elif exam_type == "Imagerie" :
        results = tryton.pool.get("gnuhealth.imaging.test.result").search([('patient', '=', patient[0].id), ('number', '=', exam_code)])

        if results:
            results_list = []
            for result in results:
                results_list.append({
                    "id": result.id,
                    "date": result.date,
                    "request_date": result.request_date,
                    "create_date": result.create_date,
                    "computed_age": result.computed_age,
                    "create_uid": result.create_uid.name,
                    "done_by": result.done_by.name.name+" "+result.done_by.name.lastname  if result.done_by else None,
                    "done_date": result.done_date,
                    "doctor": result.doctor.name.name+" "+result.doctor.name.lastname  if result.doctor else None,
                    "conclusion": result.conclusion,
                    "indication" : result.indication,
                    "merge_id" : result.merge_id,
                    "number" : result.number,
                    "order" : result.order,
                    "rec_name" : result.rec_name,
                    "patient" : result.patient.name.name +" "+ result.patient.name.lastname if result.patient else None,
                    "realisateur" : result.realisateur.name.name +" "+ result.realisateur.name.lastname if result.realisateur else None,
                    "request_order" : result.request.request if result.request else None,
                    "service_cot" : result.request.service.name if result.request and result.request.service else None,
                    "requested_test" : result.requested_test.name if result.requested_test else None,
                    "requestor" : prescriptor(exam_type, result.order),
                    "resultat" : result.resultat,
                    "serializer" : result.serializer,
                    "serializer_current" : result.serializer_current,
                    "state" : result.state,
                    "technique" : result.technique,
                    "validated_by" : result.validated_by.name.name+" "+result.validated_by.name.lastname if result.validated_by else None,
                    "validation_date" : result.validation_date,
                })
            return jsonify(results_list)
        else:
            return {"Message ": "Aucun Résultat Exploration Trouvé pour ce patient."}
    
    elif exam_type == "Exploration" :
        results = tryton.pool.get("gnuhealth.exp").search([('patient', '=', patient[0].id), ('name', '=', exam_code)])
    
        if results:
            results_list = []
            for result in results:
                results_list.append({
                    "id": result.id,
                    "analytes_summary" : result.analytes_summary,
                    "commentaire" : result.commentaire,
                    "date_analysis": result.date_analysis,
                    "date_requested": result.date_requested,
                    "diagnosis": result.diagnosis,
                    "done_by": result.done_by.name.name+" "+result.done_by.name.lastname  if result.done_by else None,
                    "done_date": result.done_date,
                    "indication" : result.indication,
                    "historize" : result.historize,
                    "name" : result.name,
                    "pathologist": result.pathologist.name.name+" "+result.pathologist.name.lastname if result.pathologist else None,
                    "patient" : result.patient.name.name +" "+ result.patient.name.lastname if result.patient else None,
                    "matricule_patient" : patient_federation_id,
                    "rec_name" : result.rec_name,
                    "request_order" : result.request_order,
                    "requestor" : prescriptor(exam_type, result.request_order),
                    "realisateur" : result.realisateur.name.name+" "+result.realisateur.name.lastname if result.realisateur else None,
                    "results" : result.results,
                    "resultat" : result.resultat,
                    "serializer" : result.serializer,
                    "state" : result.state,
                    "test" : result.test.name,
                    "technique" : result.technique,
                    "traitement" : result.traitement,
                    "validated_by" : result.validated_by.name.name+" "+result.validated_by.name.lastname if result.validated_by else None,
                    "validation_date" : result.validation_date,
                })
            return jsonify(results_list)
        else:
            return {"Message ": "Aucun Résultat Exploration Trouvé pour ce patient."}
    
    
    

@results.route('/more_infos/<string:exam_type>/<path:exam_code>/<string:patient_federation_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Doctor', 'Patient'])
@jwt_required()
@require_any_permission(["patients.send_results_results.more_informations",
                         "administration.send_results_results.more_informations",
                         "doctors.send_results_results.more_informations",
                         "patients.send_results_results.create_suggestion",
                         "administration.send_results_results.create_suggestion",
                         "doctors.send_results_results.create_suggestion"])
@tryton.transaction()
def more_informations(exam_type, exam_code, patient_federation_id):

    rohafya_patient = _shared_patient(patient_federation_id)
    if not _can_read_shared(exam_code, patient_federation_id, rohafya_patient):
        return {"message": "Ce résultat ne vous a pas été partagé."}, 403

    record, record_tenant = _external_exam(exam_type, exam_code, rohafya_patient)
    if record is not None:
        body, status = record_details_response(record, record_tenant, check_unpaid=False)
        return jsonify(body), status

    patient = _tryton_patient(patient_federation_id, rohafya_patient)
    if not patient:
        return jsonify([])

    if exam_type == "Laboratoire" :
        criteareas = tryton.pool.get("gnuhealth.lab").search([('patient', '=', patient[0].id), ('name', '=', exam_code)])

        results_list = []
        if criteareas :
            for critearea in criteareas[0].critearea:
                results_list.append({
                    "code" : critearea.code,
                    "create_date": critearea.create_date,
                    "gnuhealth_lab_id" : critearea.gnuhealth_lab_id.request_order,
                    "id" : critearea.id,
                    "lower_limit" : critearea.lower_limit,
                    "name" : critearea.name,
                    "normal_range" : critearea.normal_range,
                    "rec_name" : critearea.rec_name,
                    "remarks" : critearea.remarks,
                    "result" : critearea.result,
                    "result_text" : critearea.result_text,
                    "sequence" : critearea.sequence,
                    "test_type_id" : critearea.test_type_id.name if critearea.test_type_id else None,
                    "units" : critearea.units.name,
                    "upper_limit" : critearea.upper_limit,
                    "warning" : critearea.warning,
                    "header_id": criteareas[0].id,
                    "header_analytes_summary" : criteareas[0].analytes_summary,
                    "header_date_analysis": criteareas[0].date_analysis,
                    "header_date_requested": criteareas[0].date_requested,
                    "header_diagnosis": criteareas[0].diagnosis,
                    "header_done_by": criteareas[0].done_by.name.name+" "+criteareas[0].done_by.name.lastname  if criteareas[0].done_by else None,
                    "header_done_date": criteareas[0].done_date,
                    "header_historize" : criteareas[0].historize,
                    "header_macroscopie": criteareas[0].macroscopie,
                    "header_microscopie": criteareas[0].microscopie,
                    "header_name" : criteareas[0].name,
                    "header_patient" : criteareas[0].patient.name.name +" "+ criteareas[0].patient.name.lastname if criteareas[0].patient else None,
                    "header_matricule_patient" : patient_federation_id,
                    "header_qr" : str(criteareas[0].qr),
                    "header_rec_name" : criteareas[0].rec_name,
                    "header_renseignements" : criteareas[0].renseignements,
                    "header_request_order" : criteareas[0].request_order,
                    "header_requestor" : prescriptor(exam_type, criteareas[0].request_order),
                    "header_results" : criteareas[0].results,
                    "header_serializer" : criteareas[0].serializer,
                    "header_serializer_current" : criteareas[0].serializer_current,
                    "header_state" : criteareas[0].state,
                    "header_test" : criteareas[0].test.name,
                    "header_validated_by" : criteareas[0].validated_by.name.name+" "+criteareas[0].validated_by.name.lastname if criteareas[0].validated_by else None,
                    "header_validation_date" : criteareas[0].validation_date,
                })

        return jsonify(results_list)

    elif exam_type == "Imagerie" :
        studies = tryton.pool.get("gnuhealth.imaging.test.result").search([("number", "=", exam_code), ("patient", "=", patient[0].id)])

        results_list = []
        if studies != [] :
            for study in studies[0].studies:
                results_list.append({
                    "create_date" : study.create_date,
                    "create_uid" : study.create_uid.name,
                    "date" : study.date,
                    "description" : study.description,
                    "id" : study.id,
                    "ident" : study.ident,
                    "imaging_test_number" : study.imaging_test.number,
                    "imaging_test_order" : study.imaging_test.order,
                    "instance_uid" : study.instance_uid,
                    "institution" : study.institution,
                    "link" : study.link,
                    "merge_id" : study.merge_id,
                    "patient_orthanc" : study.patient.name,
                    "patient_orthanc_recname" : study.patient.rec_name,
                    "patient_orthanc_gnuhealth" : study.patient.patient.name.name+" "+study.patient.patient.name.lastname if study.patient and study.patient.patient else None,
                    "rec_name" : study.rec_name,
                    "ref_physician" : study.ref_phys,
                    "req_physician" : study.req_phys,
                    "requested_procedure_id" : study.requested_procedure_id,
                    "server" : study.server.rec_name if study.server else None,
                    "server_domain" : study.server.domain if study.server else None,
                    "server_username" : study.server.user if study.server else None,
                    "uuid" : study.uuid,
                    "write_date" : study.write_date,
                    "write_uid" : study.write_uid.name,
                    "header_id": studies[0].id,
                    "header_date": studies[0].date,
                    "header_request_date": studies[0].request_date,
                    "header_create_date": studies[0].create_date,
                    "header_computed_age": studies[0].computed_age,
                    "header_create_uid": studies[0].create_uid.name,
                    "header_done_by": studies[0].done_by.name.name+" "+studies[0].done_by.name.lastname  if studies[0].done_by else None,
                    "header_done_date": studies[0].done_date,
                    "header_doctor": studies[0].doctor.name.name+" "+studies[0].doctor.name.lastname  if studies[0].doctor else None,
                    "header_conclusion": studies[0].conclusion,
                    "header_indication" : studies[0].indication,
                    "header_merge_id" : studies[0].merge_id,
                    "header_number" : studies[0].number,
                    "header_order" : studies[0].order,
                    "header_rec_name" : studies[0].rec_name,
                    "header_patient" : studies[0].patient.name.name +" "+ studies[0].patient.name.lastname if studies[0].patient else None,
                    "header_realisateur" : studies[0].realisateur.name.name +" "+ studies[0].realisateur.name.lastname if studies[0].realisateur else None,
                    "header_request_order" : studies[0].request.request if studies[0].request else None,
                    "header_service_cot" : studies[0].request.service.name if studies[0].request and studies[0].request.service else None,
                    "header_requested_test" : studies[0].requested_test.name if studies[0].requested_test else None,
                    "header_requestor" : prescriptor(exam_type, studies[0].number),
                    "header_resultat" : studies[0].resultat,
                    "header_serializer" : studies[0].serializer,
                    "header_serializer_current" : studies[0].serializer_current,
                    "header_state" : studies[0].state,
                    "header_technique" : studies[0].technique,
                    "header_validated_by" : studies[0].validated_by.name.name+" "+studies[0].validated_by.name.lastname if studies[0].validated_by else None,
                    "header_validated_date" : studies[0].validation_date,

                })

        return jsonify(results_list)
    
    elif exam_type == "Exploration" :
        criteareas = tryton.pool.get("gnuhealth.exp").search([("name", "=", exam_code), ("patient", "=", patient[0].id)])

        results_list = []
        if criteareas :        
            for critearea in criteareas[0].critearea:
                results_list.append({
                    "code" : critearea.code,
                    "create_date": critearea.create_date,
                    "gnuhealth_exp_id" : critearea.gnuhealth_exp_id.request_order,
                    "id" : critearea.id,
                    "lower_limit" : critearea.lower_limit,
                    "name" : critearea.name,
                    "normal_range" : critearea.normal_range,
                    "rec_name" : critearea.rec_name,
                    "remarks" : critearea.remarks,
                    "result" : critearea.result,
                    "result_text" : critearea.result_text,
                    "sequence" : critearea.sequence,
                    "test_type_id" : critearea.test_type_id,
                    "units" : critearea.units,
                    "upper_limit" : critearea.upper_limit,
                    "warning" : critearea.warning,
                    "header_id": criteareas[0].id,
                    "header_analytes_summary" : criteareas[0].analytes_summary,
                    "header_commentaire" : criteareas[0].commentaire,
                    "header_date_analysis": criteareas[0].date_analysis,
                    "header_date_requested": criteareas[0].date_requested,
                    "header_diagnosis": criteareas[0].diagnosis,
                    "header_done_by": criteareas[0].done_by.name.name+" "+criteareas[0].done_by.name.lastname  if criteareas[0].done_by else None,
                    "header_done_date": criteareas[0].done_date,
                    "header_indication" : criteareas[0].indication,
                    "header_historize" : criteareas[0].historize,
                    "header_name" : criteareas[0].name,
                    "header_pathologist": criteareas[0].pathologist.name.name+" "+criteareas[0].pathologist.name.lastname if criteareas[0].pathologist else None,
                    "header_patient" : criteareas[0].patient.name.name +" "+ criteareas[0].patient.name.lastname if criteareas[0].patient else None,
                    "header_matricule_patient" : patient_federation_id,
                    "header_rec_name" : criteareas[0].rec_name,
                    "header_request_order" : criteareas[0].request_order,
                    "header_requestor" : prescriptor(exam_type, criteareas[0].request_order),
                    "header_realisateur" : criteareas[0].realisateur.name.name+" "+criteareas[0].realisateur.name.lastname if criteareas[0].realisateur else None,
                    "header_results" : criteareas[0].results,
                    "header_resultat" : criteareas[0].resultat,
                    "header_serializer" : criteareas[0].serializer,
                    "header_state" : criteareas[0].state,
                    "header_test" : criteareas[0].test.name,
                    "header_technique" : criteareas[0].technique,
                    "header_traitement" : criteareas[0].traitement,
                    "header_validated_by" : criteareas[0].validated_by.name.name+" "+criteareas[0].validated_by.name.lastname if criteareas[0].validated_by else None,
                    "header_validation_date" : criteareas[0].validation_date,
                })

        return jsonify(results_list)













