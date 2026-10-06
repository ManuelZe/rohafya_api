from flask import Blueprint, jsonify
from flask_cors import cross_origin
from Rohafya import db, tryton
from sqlalchemy import select
from ...accounts.models import Patients, User
from flask_jwt_extended import jwt_required, get_jwt_identity

from Rohafya.saas.constants import KIND_LAB
from Rohafya.saas.services import find_patient_record, gnuhealth_establishment_name, gnuhealth_ref, patient_records, record_details_response
from Rohafya.deco.decorators import roles_required, all_facture_is_ok, require_any_permission
from Rohafya.patients.allow_dayx import get_days_after
from datetime import datetime, timedelta


laboratoire = Blueprint('laboratoire', __name__, url_prefix='/laboratoire')


def expiration_date(date_field=None, days=90):

    """AVOIR LA DATE D'EXPIRATION D'UN RÉSULTAT

    Returns:
        date: La date d'expiration
    """
    return date_field + timedelta(days=days)

def statut_expiration(expiration_date=None):

    """AVOIR LE STATUT D'EXPIRATION | SI LE RESULTAT EST EXPIRÉ OU NON

    Returns:
        boolean: True pour un Résultat Expiré, False pour un Résultat Non Expiré
    """
    today = datetime.now()
    if not expiration_date:
        return False
    if today >= expiration_date:
        return True
    else:
        return False


def prescriptor(request_order):
    """Récupérer le prescripteur d'un ordre de laboratoire

    Args:
        request_order (object): L'ordre de laboratoire

    Returns:
        str: Le nom complet du prescripteur
    """
    if request_order :
        request = tryton.pool.get("gnuhealth.patient.lab.test").search([('request', '=', request_order)])

        if request and request[0].service:
            prescriptor = request[0].service.requestor.name.name+" "+request[0].service.requestor.name.lastname
            
            return prescriptor
        
        return {"Message" : "Aucun Service attaché à ce résultat"}
    return {"Message" : "L'Ordre doit être fourni. "}


# Récupérer tous les résultats Laboratoire d'un patient
@laboratoire.route('/all_results/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin','Patient'])
@require_any_permission(["patients.patients_laboratoire.all_results",
                         "administration.patients_laboratoire.all_results",])
@tryton.transaction()
def all_results():
    """Récupérer tous les résultats Laboratoire d'un patient

    Args:
        patient_id (int): L'identifiant du patient

    Returns:
        json: Liste des résultats Laboratoire du patient
    """
    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    rohafya_patient = user.patients if user else None
    matricule_patient = gnuhealth_ref(rohafya_patient)

    party = tryton.pool.get("party.party").search([("federation_account", "=", matricule_patient)]) if matricule_patient else []

    patient = tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)]) if party else []

    results = tryton.pool.get("gnuhealth.lab").search([('patient', '=', patient[0].id)]) if patient else []
    establishment_name = gnuhealth_establishment_name() if results else None

    results_list = []
    if results:
        for result in results:
            expirat_date = expiration_date(result.validation_date) if result.validation_date else None
            results_list.append({
                "id": result.id,
                "establishment" : establishment_name,
                "analytes_summary" : result.analytes_summary,
                "date_analysis": result.date_analysis,
                "date_requested": result.date_requested,
                "diagnosis": result.diagnosis,
                "done_by": result.done_by.name.name+" "+result.done_by.name.lastname  if result.done_by else None,
                "done_date": result.done_date,
                "expiration_date" : expirat_date,
                "nbr_days_before_expiration" : (expirat_date - result.validation_date).days if expirat_date and result.validation_date else None,
                "statut_expiration" : statut_expiration(expirat_date),
                "historize" : result.historize,
                "macroscopie": result.macroscopie,
                "microscopie": result.microscopie,
                "name" : result.name,
                "patient" : result.patient.name.name +" "+ result.patient.name.lastname if result.patient else None,
                "matricule_patient" : matricule_patient,
                "qr" : str(result.qr),
                "rec_name" : result.rec_name,
                "renseignements" : result.renseignements,
                "request_order" : result.request_order,
                "requestor" : prescriptor(result.request_order),
                "results" : result.results,
                "serializer" : result.serializer,
                "serializer_current" : result.serializer_current,
                "state" : result.state,
                "test" : result.test.name,
                "validated_by" : result.validated_by.name.name+" "+result.validated_by.name.lastname if result.validated_by else None,
                "validated_fed_id" : result.validated_by.name.federation_account if result.validated_by else None,
                "validation_date" : result.validation_date,
            })
    # Résultats des autres établissements rattachés (reçus par l'API ROHAFYA ou FHIR).
    results_list.extend(patient_records(rohafya_patient, KIND_LAB))
    return jsonify(results_list)
    
    

# MORE INFORMATIONS ABOUT ONE RESULT
@laboratoire.route('/more_infos/<path:rec_name>/result/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin','Patient'])
@require_any_permission(["patients.patients_laboratoire.more_lab_informations",
                         "administration.patients_laboratoire.more_lab_informations",])
@tryton.transaction()
@all_facture_is_ok
def MoreLabInformations(rec_name):
    """LES CRITERAREA DE CHAQUE RESULTAT D'EXAMEN

    Args:
        rec_name (char): le rec_name du resultat en question. Il doit être 
        envoyé dans l'URL sans côtes ou double côtes.

    Returns:
        json: Liste des critearea Laboratoire du patient
    """
    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    rohafya_patient = user.patients if user else None

    # Résultat reçu d'un autre établissement : règles de cet établissement (durée d'accès, factures).
    record, record_tenant = find_patient_record(rohafya_patient, KIND_LAB, rec_name)
    if record is not None:
        body, status = record_details_response(record, record_tenant)
        return jsonify(body), status

    matricule_patient = gnuhealth_ref(rohafya_patient)

    party = tryton.pool.get("party.party").search([("federation_account", "=", matricule_patient)]) if matricule_patient else []

    patient = tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)]) if party else []

    lab_results = tryton.pool.get("gnuhealth.lab").search([("name", "=", rec_name), ("patient", "=", patient[0].id)]) if patient else []

    results_list = []
    if lab_results != []:
        for critearea in lab_results[0].critearea:
            delay = get_days_after(lab_results[0].validation_date)
            if delay == False:
                return {"Message" : "La période d'accès aux détails de ce résultat est expirée."}
            try:
                units = critearea.units.name
            except AttributeError:
                units = None
                
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
                "units" : units,
                "upper_limit" : critearea.upper_limit,
                "warning" : critearea.warning,
            })

    return jsonify(results_list)




    
