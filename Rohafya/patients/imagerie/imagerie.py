from flask import Blueprint, jsonify
from flask_cors import cross_origin
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, current_user, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select
from ...accounts.models import Patients, User, Doctors
from Rohafya.patients.allow_dayx import get_days_after
from datetime import datetime, timedelta
from Rohafya.saas.constants import KIND_IMAGING
from Rohafya.saas.services import find_patient_record, gnuhealth_establishment_name, gnuhealth_ref, patient_records, record_details_response
from Rohafya.deco.decorators import all_facture_is_ok, require_any_permission

from Rohafya.deco.decorators import roles_required

imagerie = Blueprint('imagerie', __name__, url_prefix='/imagerie')

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
    """Récupérer le prescripteur d'un ordre d'imagerie

    Args:
        request_order (object): L'ordre d'imagerie

    Returns:
        str: Le nom complet du prescripteur
    """
    if request_order :
        request = tryton.pool.get("gnuhealth.imaging.test.request").search([('request', '=', request_order)])

        if request and request[0].service:
            prescriptor = request[0].service.requestor.name.name+" "+request[0].service.requestor.name.lastname
            
            return prescriptor
        
        return {"Message" : "Aucun Service attaché à ce résultat"}
    return {"Message" : "L'Ordre doit être fourni. "}


# Récupérer tous les résultats Imagerie d'un patient
@imagerie.route('/all_results/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin','Patient'])
@jwt_required()
@require_any_permission(["patients.patients_imagerie.all_results",
                         "administration.patients_imagerie.all_results",])
@tryton.transaction()
def all_results():
    """Récupérer tous les résultats Imagerie d'un patient

    Args:
        patient_id (int): L'identifiant du patient

    Returns:
        json: Liste des résultats Imagerie du patient
    """
    # Implémenter la logique pour récupérer les résultats Imagerie du patient
    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    rohafya_patient = user.patients if user else None
    matricule_patient = gnuhealth_ref(rohafya_patient)

    party = tryton.pool.get("party.party").search([("federation_account", "=", matricule_patient)]) if matricule_patient else []

    patient = tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)]) if party else []

    results = tryton.pool.get("gnuhealth.imaging.test.result").search([('patient', '=', patient[0].id)]) if patient else []
    establishment_name = gnuhealth_establishment_name() if results else None

    results_list = []
    if results:
        for result in results:

            error = None
            facture_is_ok = all_facture_is_ok2(party[0].federation_account)
            if facture_is_ok == True:
                error = error
            else:
                error = facture_is_ok['message']

            expirat_date = expiration_date(result.validation_date) if result.validation_date else None
            results_list.append({
                "id": result.id,
                "establishment" : establishment_name,
                "date": result.date,
                "request_date": result.request_date,
                "create_date": result.create_date,
                "computed_age": result.computed_age,
                "create_uid": result.create_uid.name,
                "done_by": result.done_by.name.name+" "+result.done_by.name.lastname  if result.done_by else None,
                "done_date": result.done_date,
                "expiration_date" : expirat_date,
                "nbr_days_before_expiration" : (expirat_date - result.validation_date).days if expirat_date and result.validation_date else None,
                "error" : error,
                "statut_expiration" : statut_expiration(expirat_date),
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
                "requestor" : prescriptor(result.order),
                "resultat" : result.resultat,
                "serializer" : result.serializer,
                "serializer_current" : result.serializer_current,
                "state" : result.state,
                "technique" : result.technique,
                "validated_by" : result.validated_by.name.name+" "+result.validated_by.name.lastname if result.validated_by else None,
                "validated_fed_id" : result.validated_by.name.federation_account if result.validated_by else None,
                "validation_date" : result.validation_date,
            })
    # Résultats des autres établissements rattachés (reçus par l'API ROHAFYA ou FHIR).
    results_list.extend(patient_records(rohafya_patient, KIND_IMAGING))
    return jsonify(results_list)
    


# MORE INFORMATIONS ABOUT ONE RESULT
@imagerie.route('/more_infos/<string:number>/result/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin','Patient'])
@require_any_permission(["patients.patients_imagerie.more_ima_informations",
                         "administration.patients_imagerie.more_ima_informations",])
@tryton.transaction()
@all_facture_is_ok
def MoreImaInformations(number):
    """LES CRITERAREA DE CHAQUE RESULTAT D'EXAMEN

    Args:
        number (char): le rec_name du resultat en question. Il doit être 
        envoyé dans l'URL sans côtes ou double côtes.

    Returns:
        json: Liste des images - studies imagerie du patient
    """
    current_user = get_jwt_identity()

    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    rohafya_patient = user.patients if user else None

    # Résultat reçu d'un autre établissement : règles de cet établissement (durée d'accès, factures).
    record, record_tenant = find_patient_record(rohafya_patient, KIND_IMAGING, number)
    if record is not None:
        body, status = record_details_response(record, record_tenant)
        return jsonify(body), status

    matricule_patient = gnuhealth_ref(rohafya_patient)

    party = tryton.pool.get("party.party").search([("federation_account", "=", matricule_patient)]) if matricule_patient else []

    patient = tryton.pool.get("gnuhealth.patient").search([("name", "=", party[0].id)]) if party else []

    studies = tryton.pool.get("gnuhealth.imaging.test.result").search([("number", "=", number), ("patient", "=", patient[0].id)]) if patient else []

    results_list = []
    if studies != [] :
        for study in studies[0].studies:
            delay = get_days_after(studies[0].validation_date)
            if delay == False:
                return {"Message" : "La période d'accès aux détails de ce résultat est expirée."}
            
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

            })

    return jsonify(results_list)

