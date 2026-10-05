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
from datetime import datetime, timedelta, time
from Rohafya import tryton
from sqlalchemy import select

doctor_gnu = Blueprint('gnu_doctors', __name__, url_prefix='/gnu_doctor/')

UserT = tryton.pool.get('res.user')

@tryton.default_context
def default_context():
    return UserT.get_preferences(context_only=True)


@doctor_gnu.route('/test', methods=['GET'])
@tryton.transaction()
def test_tryton():
    user, = UserT.search([('login', '=', 'admin')])
    return user.name


def calcul_commission(commissions):

    data_doctors = {}
    #listPatExam = {}
    patient_data = []
    for commission in commissions :
        listPatExam = {}
        exams_prix = []
        if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
            patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
        else:
            patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

        examen = commission.origin.product.name

        prix = float(commission.amount) - (float(commission.amount) * 0.11)  # Calcul de la commission après taxe
        prix = round(prix, 2)

        exams_prix.append(examen)
        exams_prix.append(prix)
        exams_prix.append(commission.create_date)
        listPatExam[patient] = exams_prix
        patient_data.append(listPatExam)

    data_doctors["data_patients"] = patient_data
    somme = 0.0
    for item in patient_data :
        for key, values in item.items() :
           montant = float(values[1])

           somme += montant
        
    data_doctors["commission"] = round(somme, 2)
    return data_doctors

def calcul_not_invoiced_commission(commissions):
    data_doctors = {}
    #listPatExam = {}
    patient_data = []
    prix = 0.0
    for commission in commissions :
        listPatExam = {}
        exams_prix = []

        if commission.invoice_state == "":
            if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
                patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
            else:
                patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

            examen = commission.origin.product.name

            prix = float(commission.amount) - (float(commission.amount) * 0.11)  # Calcul de la commission après taxe
            prix = round(prix, 2)

            exams_prix.append(examen)
            exams_prix.append(prix)
            exams_prix.append(commission.create_date)
            listPatExam[patient] = exams_prix
            patient_data.append(listPatExam)

    data_doctors["data_patients"] = patient_data
    somme = 0.0
    for item in patient_data :
        for key, values in item.items() :
           montant = float(values[1])

           somme += montant
        
    data_doctors["commission"] = round(somme, 2)

    return data_doctors

def calcul_invoiced_commission(commissions):
    data_doctors = {}
    #listPatExam = {}
    patient_data = []
    prix = 0.0
    for commission in commissions :
        listPatExam = {}
        exams_prix = []

        if commission.invoice_state != "":
            if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
                patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
            else:
                patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

            examen = commission.origin.product.name

            prix = float(commission.amount) - (float(commission.amount) * 0.11)  # Calcul de la commission après taxe
            prix = round(prix, 2)

            exams_prix.append(examen)
            exams_prix.append(prix)
            exams_prix.append(commission.create_date)
            listPatExam[patient] = exams_prix
            patient_data.append(listPatExam)

    data_doctors["data_patients"] = patient_data
    somme = 0.0
    for item in patient_data :
        for key, values in item.items() :
           montant = float(values[1])

           somme += montant
        
    data_doctors["commission"] = round(somme, 2)

    return data_doctors

def calcul_commission_prescription(commissions):
    data_doctors = {}
    prescription = float(0)
    realisation = float(0)
    patient_data = []
    prix = 0.0
    for commission in commissions :
        listPatExam = {}
        exams_prix = []

        if commission.invoice_state != "" and commission.product.code == "CP V01":
            if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
                patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
            else:
                patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

            examen = commission.origin.product.name

            prix = float(commission.amount) - (float(commission.amount) * 0.11)  # Calcul de la commission après taxe
            prix = round(prix, 2)

            exams_prix.append(examen)
            exams_prix.append(prix)
            exams_prix.append(commission.create_date)
            listPatExam[patient] = exams_prix
            patient_data.append(listPatExam)

    # data_doctors["data_patients"] = patient_data
    somme = 0.0
    for item in patient_data :
        for key, values in item.items() :
           montant = float(values[1])

           somme += montant
        
    data_doctors["commission"] = round(somme, 2)

    return data_doctors

def calcul_commission_realisation(commissions):
    data_doctors = {}
    prescription = float(0)
    realisation = float(0)
    patient_data = []
    prix = 0.0
    for commission in commissions :
        listPatExam = {}
        exams_prix = []

        if commission.invoice_state != "" and commission.product.code == "CP V02":
            if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
                patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
            else:
                patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

            examen = commission.origin.product.name

            prix = float(commission.amount) - (float(commission.amount) * 0.11)  # Calcul de la commission après taxe
            prix = round(prix, 2)

            exams_prix.append(examen)
            exams_prix.append(prix)
            exams_prix.append(commission.create_date)
            listPatExam[patient] = exams_prix
            patient_data.append(listPatExam)

    # data_doctors["data_patients"] = patient_data
    somme = 0.0
    for item in patient_data :
        for key, values in item.items() :
           montant = float(values[1])

           somme += montant
        
    data_doctors["commission"] = round(somme, 2)

    return data_doctors


@doctor_gnu.route('commissions_doctors/<string:list_doctor>/<string:start_date>/<string:end_date>', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.gnudoctors.all_commissions"])
def all_commission(list_doctor, start_date=None, end_date=None):
    """CALCULER L'ENSEMBLE DES COMMISSIONS DE TOUS LES DOCTEURS

    Args:
        list_doctor (_str_): La liste des docteurs sous le format string
        start_date (_date_, optional): La Date de début sous le format sting. Defaults to None.
        end_date (_date_, optional):  La Date de fin sous le format sting. Defaults to None.

    Returns:
        _json_: Un JSON contenant le nom du docteur pour clé et la somme des commissions de celui-ci pour valeur
    """
    try:
        start_date = datetime.strptime(start_date, "%Y-%m-%d %H:%M:%S.%f")
        end_date = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S.%f")
    except ValueError as e :
        print(e)

    commissions = tryton.pool.get('commission')

    dict_commission = {}
    
    list_doctors = ast.literal_eval(ast.literal_eval(list_doctor))

    if list_doctors :
        for id in list_doctors :
            DocSys = db.session.execute(select(Doctors).filter_by(id=id)).scalar_one_or_none()
            if DocSys != None :
                results_commission = commissions.search([('agent.party.federation_account', '=', DocSys.DoctorFederationID), ("create_date", ">=" , start_date), ("create_date", "<=", end_date)])

                if results_commission != []:

                    doctor = results_commission[0]

                    if doctor.agent.party.name and doctor.agent.party.lastname :
                        doctor_name = doctor.agent.party.name + " " + doctor.agent.party.lastname
                    else :
                        doctor_name = doctor.agent.party.name or doctor.agent.party.lastname

                    results = calcul_commission(results_commission)

                    arrondi = round(results["commission"], 3)

                    dict_commission[doctor_name] = arrondi

        return dict_commission
    
    return {"Message " : "Vérifiez la liste des Docteurs donnée."}


@doctor_gnu.route('/<int:id>/exams-patients', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.gnudoctors.exams_patients"])
def exams_patients(id):
    """LA TOTALITÉ DES COMMISSIONS D'UN DOCTEUR

    Args:
        id (_int_): L'identifiant du docteur

    Returns:
        _json_: Un JSON qui donne la totalité des commission ainsi que les patients associées à ces commissions
    """
    DocSys = db.session.execute(select(Doctors).filter_by(id=id)).scalar_one_or_none()
    if DocSys :
        Commission = tryton.pool.get('commission')
        start_date = datetime(2025, 10, 21, 0, 0, 0)
        end_date = datetime.today()
        
        results_commission = Commission.search([('agent.party.federation_account' , '=', DocSys.DoctorFederationID), ("create_date", ">=" , start_date),("create_date", "<=", end_date)])


        results = calcul_commission(results_commission)

        return results
    return {"message" : "Le Docteur n'a pas été trouvé."}



@doctor_gnu.route('<int:id>/nbr_patients', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Doctor'])
@jwt_required()
@require_any_permission(["doctors.gnudoctors.nbr_list_exams"])
def nbr_list_exams(id):
    data = exams_patients(id)

    data_patients = data["data_patients"]
    patients = {}

    # Total de clés
    total_cles = sum(len(d) for d in data_patients)

    # Clés uniques
    cles_uniques = set()
    for d in data_patients:
        cles_uniques.update(d.keys())

    patients["nombres"] = len(cles_uniques)
    patients["listes"] = list(cles_uniques)

    return len(cles_uniques)


@doctor_gnu.route('<int:id>/research/<string:start_date>/<string:end_date>')
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.gnudoctors.simple_research"])
def simple_research(id, start_date=None, end_date=None):

    """Calcul des différentes commissions d'un docteur sous une date précise

    Args:
        id : Id du docteur dans la table 
        start_date : date de début
        end_date : date de fin

    Returns:
        json: Un JSON comme suit commissions : somme
    """

    DocSys = db.session.execute(select(Doctors).filter_by(id=id)).scalar_one_or_none()
    if DocSys :
        start_date = datetime.strptime(start_date, "%Y-%m-%d %H:%M:%S.%f")
        end_date = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S.%f")
        commissions = tryton.pool.get('commission')
        results_commission = commissions.search([('agent.party.federation_account', '=', DocSys.DoctorFederationID), ("create_date", ">=" , start_date), ("create_date", "<=", end_date)])

        results = calcul_commission(results_commission)
        return results

    return {"message":"Aucun Docteur Trouvé avec votre id."}


@doctor_gnu.route('<int:id>/research/', methods=['GET'])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.gnudoctors.today_transaction"])
def today_transaction(id):

    DocSys = db.session.execute(select(Doctors).filter_by(id=id)).scalar_one_or_none()
    if DocSys :
        aujourdhui = datetime.now()
        debut_journee = datetime.combine(aujourdhui, time.min)  # Minuit aujourd'hui
        fin_journee = datetime.combine(aujourdhui, time.max)

        commissions = tryton.pool.get('commission')
        results_commission = commissions.search([('agent.party.federation_account' , '=', DocSys.DoctorFederationID), ("create_date", ">=" , debut_journee),("create_date", "<=", fin_journee)])
        
        dict_info = {}
        number = 0
        if results_commission :
            number += len(results_commission)
            results = calcul_commission(results_commission)

            dict_info['Data'] = results
            dict_info['number'] = number

            return dict_info
        return {"message":"Aucune Commission générée ce jour."}
    return {"message":"Aucun Docteur Trouvé."}


@doctor_gnu.route('<int:id>/commissions/')
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Doctor")
@jwt_required()
@require_any_permission(["doctors.gnudoctors.lists_commissions"])
def lists_commissions(id):
    """lISTE DES ÉTATS DE COMMISSION D'UN DOCTEUR

    Args:
        id (int): L'identifiant ID du docteur
        start_date (str(date)): La date de déut sous le format str(date)
        end_date (str(date)): La date de fin sous le format str(date)

    Returns:
        json: Un JSON avec pour clé le docteur et la liste de ses commissions

    """

    today = datetime.today()
    annee = today.year
    mois = today.month
    day = today.day
    if day < 20:
        # Période = 21 du mois précédent → 20 du mois actuel
        if mois == 1:
            prev_month = 12
            prev_month_year = annee - 1
        else:
            prev_month = mois - 1
            prev_month_year = annee
        
        month = mois
    else:
        # Période = 21 du mois actuel → 20 du mois suivant

        if mois == 12:
            next_month = 1
            next_year = today.year + 1
        else:
            next_month = mois + 1
            next_year = annee
        
        month = next_month

    commissions = tryton.pool.get('commission')
    DocSys = db.session.execute(select(Doctors).filter_by(id=id)).scalar_one_or_none()
    if not DocSys:
        return {"message": "Aucun Docteur trouvé avec cet ID."}, 404

    
    solde_elt = {}
    data_prescription = {}
    data_realisation = {}
    
    from_date = datetime(2025, 10, 21, 0, 0, 0)
    to_date = datetime(today.year, month, 20, 23, 59, 59)

    commissions = tryton.pool.get('commission')
    DocSys = Doctors.query.get(id)
    if not DocSys:
        return {"message": "Aucun Docteur trouvé avec cet ID."}


    results_prescription = commissions.search([
        ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
        ("create_date", ">=", from_date),
        ("create_date", "<=", to_date),
        ("product.code", "=", "CP V01"),
        # ("invoice_state", operateur, "")
    ])

    list_commission = []
    for commission in results_prescription:
        if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
            patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
        else:
            patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

        if not patient:
            patient = "Inconnu"
    
        commission_data_prescription = {
            "Date"    : commission.date,
            "Produit" : commission.product.rec_name,
            "Montant": commission.amount,
            "Patient" : patient,
            "Examen"  : commission.origin.product.rec_name,
            "Validé"  : commission.is_validate,
            "Facturé" : commission.invoice_state
        }
        list_commission.append(commission_data_prescription)
    montant_prescription = calcul_commission(results_prescription)['commission'] if results_prescription else float(0)

    data_prescription["data_prescription"] = list_commission
    data_prescription["montant_prescription"] = montant_prescription

    results_realisation = commissions.search([
        ('agent.party.federation_account', '=', DocSys.DoctorFederationID),
        ("create_date", ">=", from_date),
        ("create_date", "<=", to_date),
        ("product.code", "=", "CP V02"),
        # ("invoice_state", operateur, "")
    ])
    
    list_commission = []
    for commission in results_realisation:
        if commission.origin.invoice.party.name and commission.origin.invoice.party.lastname :
            patient = commission.origin.invoice.party.name+" "+commission.origin.invoice.party.lastname
        else:
            patient = commission.origin.invoice.party.name or commission.origin.invoice.party.lastname

        if not patient:
            patient = "Inconnu"
    
        commission_data_realisation = {
            "Date"    : commission.date,
            "Produit" : commission.product.rec_name,
            "Montant": commission.amount,
            "Patient" : patient,
            "Examen"  : commission.origin.product.rec_name,
            "Validé"  : commission.is_validate,
            "Facturé" : commission.invoice_state
        }
        list_commission.append(commission_data_realisation)

    montant_realisation = calcul_commission(results_realisation)['commission'] if results_prescription else float(0)

    data_realisation["data_realisation"] = list_commission
    data_realisation["montant_realisation"] = montant_realisation

    montant_total = float(montant_prescription) + float(montant_realisation)

    solde_elt["prescription"] = data_prescription
    solde_elt["realisation"] = data_realisation
    solde_elt["montant_total"] = montant_total

    return solde_elt

    