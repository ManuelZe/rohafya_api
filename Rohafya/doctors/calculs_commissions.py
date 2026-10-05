import functools
from flask import Flask, jsonify
from flask_jwt_extended import jwt_required
from flask_restful import Api
import ast
import psycopg2
from flask import (
    Blueprint, request, g, redirect, flash, render_template, session, url_for
)
from werkzeug.security import check_password_hash, generate_password_hash
from flask_login import login_required
from flask_tryton import Tryton
from Rohafya.accounts.models import Doctors, User, Role
from flask_cors import CORS, cross_origin
from Rohafya import db
from flask import Flask
from flask_tryton import Tryton
from Rohafya.deco.decorators import require_any_permission, roles_required
from Rohafya.deco.generators import generate_random_letters, generate_random_letters_and_digits
from Rohafya.email.email import send_email
from datetime import date, datetime, timedelta, time
from Rohafya import tryton
from collections import defaultdict, UserList
from .gnudoctors import calcul_not_invoiced_commission, calcul_invoiced_commission, calcul_commission_prescription, calcul_commission
from dateutil.relativedelta import relativedelta

doctor_com = Blueprint('doctor_com', __name__, url_prefix='/doctor_com/')

UserT = tryton.pool.get('res.user')

@tryton.default_context
def default_context():
    return UserT.get_preferences(context_only=True)


@doctor_com.route('solde/<int:doc_id>', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.calcul_commissions.solde"])
def solde(doc_id):
    """CALCULER LE SOLDE DU DOCTEUR / SES COMMISSIONS DU MOIS NON PAYÉS
    """

    today = datetime.today()
    day = today.day

    if day < 20:
        # Période = 21 du mois précédent → 20 du mois actuel
        if today.month == 1:
            prev_month = 12
            prev_year = today.year - 1
        else:
            prev_month = today.month - 1
            prev_year = today.year

        start_date = datetime(prev_year, prev_month, 21, 0, 0, 0)
        end_date = datetime(today.year, today.month, 20, 23, 59, 59)

    else:
        # Période = 21 du mois actuel → 20 du mois suivant
        start_date = datetime(today.year, today.month, 21, 0, 0, 0)

        if today.month == 12:
            next_month = 1
            next_year = today.year + 1
        else:
            next_month = today.month + 1
            next_year = today.year

        end_date = datetime(next_year, next_month, 20, 23, 59, 59)

    dict_commission = {}

    commissions = tryton.pool.get('commission')
    DocSys = db.session.get(Doctors, doc_id)
    if not DocSys:
        return {"message" : "Aucun Docteur trouvé avec cet ID."}
    
    commissions = tryton.pool.get('commission')
    results_commission = commissions.search([('agent.party.federation_account' , '=', DocSys.DoctorFederationID), ("create_date", ">=" , start_date), ("create_date", "<=", end_date)])
    
    count_number_invoiced = 0
    count_number_not_invoiced = 0
    number_of_registered_patients = 0
    results = None
    commission_product = {}
    exam_product = {}
    compte = {}

    top_three = {}

    if results_commission != []:

        results = calcul_not_invoiced_commission(results_commission)
        for commission in results_commission :

            product_commission = commission.product.rec_name
            exam = commission.origin.product.rec_name
            try : 
                patient = (
                    commission.origin.invoice.patient.name.name + ' ' +
                    commission.origin.invoice.patient.name.lastname
                )
            except AttributeError:
                patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

            if commission.invoice_state != "":
                count_number_invoiced += 1
            else :
                count_number_not_invoiced += 1
             
            if patient in compte:
                compte[patient] += 1
            else:
                compte[patient] = 1

            if product_commission in commission_product:
                commission_product[product_commission] += 1
            else:
                commission_product[product_commission] = 1

            if exam in exam_product:
                exam_product[exam] += 1
            else:
                exam_product[exam] = 1

        number_of_registered_patients = sum(compte.values())

        sorted_top_3 = sorted(exam_product.items(), key=lambda x: x[1], reverse=True)
        top_three = dict(sorted_top_3[:3])



    dict_commission["Solde"] = results
    dict_commission["Factured"] = count_number_invoiced
    dict_commission["Not_Factured"] = count_number_not_invoiced
    dict_commission["number_of_registered_patients"] = number_of_registered_patients
    dict_commission["Patient_nbr_examen"] = compte
    dict_commission["All_Exam"] = exam_product
    dict_commission["Top_3"] = top_three
    dict_commission["Commission_product"] = commission_product

    return dict_commission


@doctor_com.route('general/<int:doc_id>', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.calcul_commissions.general_solde"])
def general_solde(doc_id):
    """CALCULER LE GENERAL ET LES SOLDES DU DOCTEUR À UN INSTANT T DONNÉ

    Args:
        doc_id (_INT_): Identifiant du docteur
    """

    commissions = tryton.pool.get('commission')
    DocSys = db.session.get(Doctors, doc_id)
    if not DocSys:
        return {"message" : "Aucun Docteur trouvé avec cet ID."}
    

    start_date = datetime(2025, 10, 21, 0, 0, 0)
    end_date = datetime.today()
    
    commissions = tryton.pool.get('commission')
    results_commission = commissions.search([('agent.party.federation_account' , '=', DocSys.DoctorFederationID), ("create_date", ">=" , start_date), ("create_date", "<=", end_date)])
    
    dict_commission = {}
    count_number_invoiced = 0
    count_number_not_invoiced = 0
    number_of_registered_patients = 0
    results = None
    commission_product = {}
    exam_product = {}
    compte = {}

    if results_commission != []:

        results = calcul_invoiced_commission(results_commission)
        for commission in results_commission :

            product_commission = commission.product.rec_name
            exam = commission.origin.product.rec_name
            try : 
                patient = (
                    commission.origin.invoice.patient.name.name + ' ' +
                    commission.origin.invoice.patient.name.lastname
                )
            except AttributeError:
                patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

            if commission.invoice_state != "":
                count_number_invoiced += 1
            else :
                count_number_not_invoiced += 1
             
            if patient in compte:
                compte[patient] += 1
            else:
                compte[patient] = 1

            if product_commission in commission_product:
                commission_product[product_commission] += 1
            else:
                commission_product[product_commission] = 1

            if exam in exam_product:
                exam_product[exam] += 1
            else:
                exam_product[exam] = 1

        number_of_registered_patients = sum(compte.values())

        sorted_top_3 = sorted(exam_product.items(), key=lambda x: x[1], reverse=True)
        top_three = dict(sorted_top_3[:3])



    dict_commission["Solde"] = results
    dict_commission["Nombre_Commissions"] = len(results["data_patients"])
    dict_commission["Factured"] = count_number_invoiced
    dict_commission["Not_Factured"] = count_number_not_invoiced
    dict_commission["number_of_registered_patients"] = number_of_registered_patients
    dict_commission["Patient_nbr_examen"] = compte
    dict_commission["All_Exam"] = exam_product
    dict_commission["Top_3"] = top_three
    dict_commission["Commission_product"] = commission_product

    return dict_commission


######################### PRESCRIPTION ET REALISATION FACTURÉE ET NON FACTURÉE DU MOIS #########################
@doctor_com.route('invoiced_by_mounth/<int:doc_id>/<int:mois>/<string:type>', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.calcul_commissions.invoiced_solde_by_mounth"])
def invoiced_solde_by_mounth(doc_id, mois, type=None):
    """CALCULER LES COMMISSIONS D'UN DOCTEUR PAR MOIS
    NOTONS QUE DES ÉLÉMENTS ONT ÉTÉ AJOUTÉ ICI POUR PLUS DE TRANSPARENCE
    1. CETTE REQUÊTE S'EXÉCUTE EN FONCTION DE DEUX ÉLÉMENTS
        - LES COMMISSIONS FACTURÉES
        - LES COMMISSIONS NON FACTURÉES
    2. EN FONCTION DE SES DEUX TYPES DE COMMISSIONS NOUS AFFICHONS
        - MONTANT PRESCRIPTION
        - MONTANT RÉALISATION
        - MONTANT TOTAL
        - NOMBRE TOTAL DES COMMISSIONS
        - NOMBRE TOTAL DES PATIENTS
        - LISTE DES PATIENTS SUR LE MOIS

    Args:
        doc_id (INT): L'ID DU DOCTEUR
        mois (int): Mois ciblé (1=janvier, 2=février, ..., 12=décembre)

    Returns:
        _dict_: Un dictionnaire contenant les commissions d'un docteur sur un mois donné
    """

    from_date = None
    to_date = None

    # Sécuriser le mois
    
    if not 1 <= mois <= 12:
        return {"message": "Mois invalide. Doit être entre 1 et 12."}

    # Calculer l’année courante
    today = datetime.today()
    year = today.year

    if year == int(2025):
        if mois < int(10):
            return {
                "montant_prescription": float(0),
                "montant_realisation": float(0),
                "montant_total": float(0),
                "nb_total_commission": float(0),
                "nb_total_patient" : float(0),
                "list_patient_name" : [],
                "element_prescription" : [],
                "element_realisation" : []
            }
            # return {"message" : "Aucune Donnée disponible."} 

    # Calculer les dates de début et de fin
    prev_month = 12 if mois == 1 else mois - 1
    prev_month_year = year - 1 if mois == 1 else year

    if year == int(2025) and mois == 10 :
        from_date = datetime(prev_month_year, 10, 21, 0, 0, 0)
        to_date = datetime(year, today.month, today.day, 23, 59, 59)
    else:
        from_date = datetime(prev_month_year, prev_month, 21, 0, 0, 0)
        to_date = datetime(year, mois, 20, 23, 59, 59)

    commissions = tryton.pool.get('commission')
    DocSys = db.session.get(Doctors, doc_id)
    if not DocSys:
        return {"message": "Aucun Docteur trouvé avec cet ID."}


    results_prescription = commissions.search([
        ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
        ("create_date", ">=", from_date),
        ("create_date", "<=", to_date),
        ("product.code", "=", "CP V01"),
        # ("invoice_state", operateur, "")
    ])

    if type == "invoiced" or type == None:
        results_prescription = [
            c for c in results_prescription if c.invoice_state != ""
        ] 
    else :
        results_prescription = [
            c for c in results_prescription if c.invoice_state == ""
        ]

    elements_prescription = calcul_commission(results_prescription)
    
    montant_prescription = elements_prescription['commission'] if results_prescription else float(0)

    results_realisation = commissions.search([
        ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
        ("create_date", ">=", from_date),
        ("create_date", "<=", to_date),
        ("product.code", "=", "CP V02"),
        # ("invoice_state", operateur, "")
    ])

    if type == "invoiced" or type == None:
        results_realisation = [
            c for c in results_realisation if c.invoice_state != ""
        ]
    else :
        results_realisation = [
            c for c in results_realisation if c.invoice_state == ""
        ]

    elements_realisation = calcul_commission(results_realisation)

    montant_realisation = elements_realisation['commission'] if results_realisation else float(0)

    montant_total = float(montant_prescription) + float(montant_realisation)

    commission_non_facturee = len(results_prescription) + len(results_realisation)

    all_commission = results_prescription + results_realisation

    cpt_patient = int(0)
    list_patient_id = []
    list_patient_name = []
    for c in all_commission:
        try:
            patient = c.origin.invoice.patient
        except AttributeError:
            patient = c.origin.invoice.party
        
        if patient not in list_patient_id:
            list_patient_id.append(patient)
            cpt_patient += 1
        
    for p in all_commission:
        try:
            patient = p.origin.invoice.patient.name.name + ' ' + p.origin.invoice.patient.name.lastname
        except AttributeError:
            patient = p.origin.invoice.party.name or p.origin.invoice.party.lastname
        
        if patient not in list_patient_name:
            list_patient_name.append(patient)

    return {
        "montant_prescription": montant_prescription,
        "montant_realisation": montant_realisation,
        "montant_total": montant_total,
        "nb_total_commission": commission_non_facturee,
        "nb_total_patient" : cpt_patient,
        "list_patient_name" : list_patient_name,
        "element_prescription" : elements_prescription,
        "element_realisation" : elements_realisation
    }


######################### PRESCRIPTION ET REALISATION FACTURÉE ET NON FACTURÉE SUR L'ANNÉE #########################
@doctor_com.route('invoiced_by_year/<int:doc_id>/<int:annee>/<string:type>', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.calcul_commissions.invoiced_solde_by_year"])
def invoiced_solde_by_year(doc_id, annee, type=None):
    """CALCULER LES COMMISSIONS D'UN DOCTEUR SUR L'ANNÉE

    Args:
        doc_id (INT): L'ID DU DOCTEUR
        annee (int): Année ciblé (2024, 2025)

    Returns:
        _dict_: Un dictionnaire contenant les commissions par mois d'un docteur 
    """

    solde_par_mois = {}
    total_solde_par_mois = {}
    if annee == int(2025):
        mois_fr = {
            9: "SEPTEMBRE", 10: "OCTOBRE", 11: "NOVEMBRE", 12: "DECEMBRE"
        }
    else:
        mois_fr = {
            1: "JANVIER", 2: "FEVRIER", 3: "MARS", 4: "AVRIL",
            5: "MAI", 6: "JUIN", 7: "JUILLET", 8: "AOUT",
            9: "SEPTEMBRE", 10: "OCTOBRE", 11: "NOVEMBRE", 12: "DECEMBRE"
        }

    commissions = tryton.pool.get('commission')
    DocSys = db.session.get(Doctors, doc_id)
    if not DocSys:
        return {"message": "Aucun Docteur trouvé avec cet ID."}, 404

    
    solde_elt = {}
    for mois in range(1, 13):
        elt_mois = {}
        # Mois précédent pour le 21
        prev_month = 12 if mois == 1 else mois - 1
        year_start = annee - 1 if mois == 1 else annee
        year_end = annee

        from_date = datetime(year_start, prev_month, 21, 0, 0, 0)
        to_date = datetime(year_end, mois, 20, 23, 59, 59)

        commissions = tryton.pool.get('commission')
        DocSys = Doctors.query.get(doc_id)
        if not DocSys:
            return {"message": "Aucun Docteur trouvé avec cet ID."}


        results_prescription = commissions.search([
            ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
            ("create_date", ">=", from_date),
            ("create_date", "<=", to_date),
            ("product.code", "=", "CP V01"),
            # ("invoice_state", operateur, "")
        ])

        if type == "invoiced" or type == None:
            results_prescription = [
                c for c in results_prescription if c.invoice_state != ""
            ]
        else :
            results_prescription = [
                c for c in results_prescription if c.invoice_state == ""
            ]
        
        elements_prescription = calcul_commission(results_prescription)
        montant_prescription = elements_prescription['commission'] if results_prescription else float(0)

        results_realisation = commissions.search([
            ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
            ("create_date", ">=", from_date),
            ("create_date", "<=", to_date),
            ("product.code", "=", "CP V02"),
            # ("invoice_state", operateur, "")
        ])

        if type == "invoiced" or type == None:
            results_realisation = [
                c for c in results_realisation if c.invoice_state != ""
            ]
        else :
            results_realisation = [
                c for c in results_realisation if c.invoice_state == ""
            ]

        elements_realisation = calcul_commission(results_realisation)
        montant_realisation = elements_realisation['commission'] if results_realisation else float(0)

        montant_total = float(montant_prescription) + float(montant_realisation)

        try :
            solde_elt[mois_fr[mois]] = montant_total
            elt_mois[mois_fr[mois]] = montant_total
            elt_mois["elements_prescription"] = elements_prescription
            elt_mois["elements_realisation"] =elements_realisation
        except KeyError:
            continue

        solde_par_mois[mois_fr[mois]] = elt_mois
    
    total_solde_par_mois = sum(solde_elt.values())
    solde_par_mois["Total"] = total_solde_par_mois

    return solde_par_mois


######################### PRESCRIPTION ET REALISATION FACTURÉE ET NON FACTURÉE SUR L'ANNÉE #########################
@doctor_com.route('actual_solde/<int:doc_id>', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.calcul_commissions.actual_solde"])
def actual_solde(doc_id):
    """CALCULER LE SOLDE ACTUEL D'UN DOCTEUR
        LE SOLDE ACTUEL D'UN DOCTEUR ACTUEL.
        C'EST-À DIRE TOUT CE QUI N'A PAS ÉTÉ PAYÉ AU COURS DU MOIS EN COURS.
        CECI SE BASE UNIQUEMENT SUR LES PRESCRIPTIONS ACTUELS AINSI
        QUE LES RÉALISATIONS QUI ONT ÉTÉ RENSEIGNÉS
        NOUS AVONS AUSSI AFFICHÉ :
          - LE NOMBRE DE PATIENTS QUI ONT ÉTÉ ENREGISTRÉS
          - LE MONTANT TOTAL DES COMMISSIONS NON FACTURÉES
          - LA LISTE DES PATIENTS QUI ONT ÉTÉ ENREGISTRÉS

    Args:
        doc_id (INT): L'ID DU DOCTEUR

    Returns:
        _dict_: Un dictionnaire contenant le solde actuel du docteur
    """

    today = datetime.today()
    mois = today.month
    year = today.year
    day = today.day

    from_date = None
    to_date = None

    if day < 20:
        # Période = 21 du mois précédent → 20 du mois actuel
        if today.month == 1:
            prev_month = 12
            prev_month_year = year - 1
        else:
            prev_month = mois - 1
            prev_month_year = year

        from_date = datetime(prev_month_year, prev_month, 21, 0, 0, 0)
        to_date = datetime(prev_month_year, mois, 20, 23, 59, 59)

    else:
        # Période = 21 du mois actuel → 20 du mois suivant
        from_date = datetime(year, mois, 21, 0, 0, 0)

        if today.month == 12:
            next_month = 1
            next_year = today.year + 1
        else:
            next_month = today.month + 1
            next_year = today.year

        to_date = datetime(next_year, next_month, 20, 23, 59, 59)

    commissions = tryton.pool.get('commission')
    DocSys = db.session.get(Doctors, doc_id)
    if not DocSys:
        return {"message": "Aucun Docteur trouvé avec cet ID."}

    results_prescription = commissions.search([
        ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
        ("create_date", ">=", from_date),
        ("create_date", "<=", to_date),
        ("product.code", "=", "CP V01"),
        # ("invoice_state", operateur, "")
    ])

    results_prescription = [
        c for c in results_prescription if c.invoice_state == ""
    ]

    montant_prescription = calcul_commission(results_prescription)['commission'] if results_prescription else float(0)

    results_realisation = commissions.search([
        ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
        ("create_date", ">=", from_date),
        ("create_date", "<=", to_date),
        ("product.code", "=", "CP V02"),
        # ("invoice_state", operateur, "")
    ])

    results_realisation = [
        c for c in results_realisation if c.invoice_state == ""
    ]

    montant_realisation = calcul_commission(results_realisation)['commission'] if results_realisation else float(0)

    montant_total = float(montant_prescription) + float(montant_realisation)

    commission_non_facturee = len(results_prescription) + len(results_realisation)

    all_commission = results_prescription + results_realisation

    cpt_patient = int(0)
    list_patient_id = []
    list_patient_name = []
    for c in all_commission:
        try:
            patient = c.origin.invoice.patient
        except AttributeError:
            patient = c.origin.invoice.party
        
        if patient not in list_patient_id:
            list_patient_id.append(patient)
            cpt_patient += 1
        
    for p in all_commission:
        try:
            patient = p.origin.invoice.patient.name.name + ' ' + p.origin.invoice.patient.name.lastname
        except AttributeError:
            patient = p.origin.invoice.party.name or p.origin.invoice.party.lastname
        
        if patient not in list_patient_name:
            list_patient_name.append(patient)


    return {
        "montant_prescription": montant_prescription,
        "montant_realisation": montant_realisation,
        "montant_total": montant_total,
        "commission_non_facturee": commission_non_facturee,
        "nombre_patient" : cpt_patient,
        "list_patient_name" : list_patient_name
    }


