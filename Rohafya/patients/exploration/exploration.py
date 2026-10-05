from flask import Blueprint, jsonify
from flask_cors import cross_origin
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, current_user, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select
from ...accounts.models import Patients, User, Doctors
from Rohafya.patients.allow_dayx import get_days_after
from datetime import datetime, timedelta

from Rohafya.saas.constants import KIND_EXPLORATION
from Rohafya.saas.services import find_patient_record, gnuhealth_establishment_name, gnuhealth_ref, patient_records, record_details_response
from Rohafya.deco.decorators import roles_required, require_any_permission, all_facture_is_ok

exploration = Blueprint('exploration', __name__, url_prefix='/exploration')

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


def all_facture_is_ok2(federationID):
    """S'ASSURER QUE LA TOTALITÉ DES FACTURES A ÉTÉ COMPLÈTEMENT PAYÉ

    Args:
        federationID (string): Le Féderation ID du patient

    Returns:
        boolean | response : True si toutes les factures ont été payées | Un Message disant de régler les factures sinon.
    """

    from_date = datetime(2025, 12, 1, 0, 0, 0)
   
    if  federationID:
        invoices = tryton.pool.get('account.invoice').search([('party.federation_account', '=', federationID),
                                                              ("create_date", ">=", from_date)])
    cond = True
    for invoice in invoices :
        if invoice.state == 'posted':
            if float(invoice.amount_to_pay_today) > float(0.1) * float(invoice.untaxed_amount):
                cond = False
                break
            else :
                cond =  True
        elif invoice.state == 'paid':
            cond = True
    
    if cond:
        return True
    else:
        return {
            "message": 
                "Ses éléments sont masqués en raison des Factures Impayées. "
                "Veuillez les régler pour accéder à vos résultats. "
            
        }


def prescriptor(request_order):
    """Récupérer le prescripteur d'un ordre de exploration

    Args:
        request_order (object): L'ordre de exploration

    Returns:
        str: Le nom complet du prescripteur
    """
    if request_order :
        request = tryton.pool.get("gnuhealth.patient.exp.test").search([('request', '=', request_order)])

        if request and request[0].service:
            prescriptor = request[0].service.requestor.name.name+" "+request[0].service.requestor.name.lastname
            
            return prescriptor
        
        return {"Message" : "Aucun Service attaché à ce résultat"}
    return {"Message" : "L'Ordre doit être fourni. "}


# Récupérer tous les résultats Exploration d'un patient
@exploration.route('/all_results/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin','Patient'])
@jwt_required()
@require_any_permission(["patients.patients_explorations.all_results",
                         "administration.patients_explorations.all_results",])
@tryton.transaction()
def all_results():
    """Récupérer tous les résultats Exploration d'un patient

    Args:
        patient_id (int): L'identifiant du patient

    Returns:
        json: Liste des résultats Exploration du patient
    """
    # Implémenter la logique pour récupérer les résultats exploration du patient
    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    rohafya_patient = user.patients if user else None
    matricule_patient = gnuhealth_ref(rohafya_patient)

    party = tryton.pool.get("party.party").search([("federation_account", "=", matricule_patient)]) if matricule_patient else []

    patient = tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)]) if party else []

    print(f"Patient ID: {patient}, Matricule: {matricule_patient}")

    results = tryton.pool.get("gnuhealth.exp").search([('patient', '=', patient[0].id)]) if patient else []
    establishment_name = gnuhealth_establishment_name() if results else None
    
    results_list = []
    if results:
        for result in results:
            
            error = None
            facture_is_ok = all_facture_is_ok2(party[0].federation_account)
            if facture_is_ok == True:
                error = None
            else:
                error = facture_is_ok['message']

            expirat_date = expiration_date(result.validation_date) if result.validation_date else None
            results_list.append({
                "id": result.id,
                "establishment" : establishment_name,
                "analytes_summary" : result.analytes_summary,
                "commentaire" : result.commentaire,
                "date_analysis": result.date_analysis,
                "date_requested": result.date_requested,
                "diagnosis": result.diagnosis,
                "done_by": result.done_by.name.name+" "+result.done_by.name.lastname  if result.done_by else None,
                "done_date": result.done_date,
                "error" : error,
                "expiration_date" : expirat_date,
                "nbr_days_before_expiration" : (expirat_date - result.validation_date).days if expirat_date and result.validation_date else None,
                "statut_expiration" : statut_expiration(expirat_date),
                "indication" : result.indication,
                "historize" : result.historize,
                "name" : result.name,
                "pathologist": result.pathologist.name.name+" "+result.pathologist.name.lastname if result.pathologist else None,
                "patient" : result.patient.name.name +" "+ result.patient.name.lastname if result.patient else None,
                "matricule_patient" : matricule_patient,
                "rec_name" : result.rec_name,
                "request_order" : result.request_order,
                "requestor" : prescriptor(result.request_order),
                "realisateur" : result.realisateur.name.name+" "+result.realisateur.name.lastname if result.realisateur else None,
                "results" : result.results,
                "resultat" : result.resultat,
                "serializer" : result.serializer,
                "state" : result.state,
                "test" : result.test.name,
                "technique" : result.technique,
                "traitement" : result.traitement,
                "validated_by" : result.validated_by.name.name+" "+result.validated_by.name.lastname if result.validated_by else None,
                "validated_fed_id" : result.validated_by.name.federation_account if result.validated_by else None,                
                "validation_date" : result.validation_date,
            })
    # Résultats des autres établissements rattachés (reçus par l'API ROHAFYA ou FHIR).
    results_list.extend(patient_records(rohafya_patient, KIND_EXPLORATION))
    return jsonify(results_list)
    

# MORE INFORMATIONS ABOUT ONE RESULT
@exploration.route('/more_infos/<string:rec_name>/result/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin','Patient'])
@require_any_permission(["patients.patients_explorations.more_exp_informations",
                         "administration.patients_explorations.more_exp_informations",])
@tryton.transaction()
@all_facture_is_ok
def MoreExpInformations(rec_name):
    """LES CRITERAREA DE CHAQUE RESULTAT D'EXAMEN

    Args:
        result_id (int): l'id du resultat en question

    Returns:
        json: Liste des critearea Exploration du patient
    """

    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    rohafya_patient = user.patients if user else None

    # Résultat reçu d'un autre établissement : règles de cet établissement (durée d'accès, factures).
    record, record_tenant = find_patient_record(rohafya_patient, KIND_EXPLORATION, rec_name)
    if record is not None:
        body, status = record_details_response(record, record_tenant)
        return jsonify(body), status

    matricule_patient = gnuhealth_ref(rohafya_patient)

    party = tryton.pool.get("party.party").search([("federation_account", "=", matricule_patient)]) if matricule_patient else []

    patient = tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)]) if party else []

    criteareas = tryton.pool.get("gnuhealth.exp").search([("name", "=", rec_name), ("patient", "=", patient[0].id)]) if patient else []
    if not criteareas:
        return jsonify([])

    results_list = []
    for critearea in criteareas[0].critearea:
        delay = get_days_after(criteareas[0].validation_date)
        if delay == False:
            return {"Message" : "La période d'accès aux détails de ce résultat est expirée."}
        try:
            units = critearea.units.name
        except AttributeError:
            units = None
             
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
            "units" : critearea.units.name,
            "upper_limit" : critearea.upper_limit,
            "warning" : critearea.warning,
        })

    return jsonify(results_list)