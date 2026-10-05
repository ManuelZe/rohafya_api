
from flask import Blueprint, jsonify, request
from flask_jwt_extended import jwt_required, current_user
from Rohafya.saas.constants import KIND_INVOICE
from Rohafya.saas.services import find_patient_record, gnuhealth_establishment_name, gnuhealth_ref, patient_records
from Rohafya.accounts.models import Patients, User, Role
from flask_cors import CORS, cross_origin
from Rohafya.deco.decorators import roles_required, require_any_permission
from Rohafya import db, tryton
from sqlalchemy import select
from flask_tryton import Tryton
from Rohafya.deco.generators import generate_random_letters, generate_random_letters_and_digits, generate_unique_numbers, generate_random_email
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash


charges_patients = Blueprint('charge_patients', __name__, url_prefix='/patient/')

@charges_patients.route('/extract/all', methods=['GET'])
@cross_origin(supports_credentials=True)
@tryton.transaction()
@roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.charge_patients.extract_verified_patients"])
def extract_all_verified_patients():
    """CHARGER TOUS LES PATIENTS VÉRIFIER SI LA CASE RESULTS_ONLINE EST COCHÉE

    Returns:
        lists: lists des patients
    """
    patients = tryton.pool.get('party.party').search([('is_patient', '=', True), ('result_online', '=', True)])

    for patient in patients:
        ContactMechanism = tryton.pool.get('party.contact_mechanism')

        emails = ContactMechanism.search([
            ('party', '=', patient.id),
            ('type', '=', 'email'),
        ])

        phones = ContactMechanism.search([
            ('party', '=', patient.id),
            ('type', '=', 'phone'),
        ])

        email_patients = emails[0].value if emails else generate_random_email()
        phone1 = phones[0].value if phones else generate_unique_numbers()
        phone2 = None

        if patient.active:
            new_patient = Patients(
                PatientFederationID = patient.federation_account,
                PatientName = patient.name,
                PatientLastname = patient.lastname,
                PatientDOB = patient.dob,
                PatientPOB = "Cameroun",
                PatientNat = "Camerounaise",
                PatientCNI = "",
                PatientGender = patient.gender,
                PatientPhone = phone1,
                PatientPhone2 = phones[1].value if phones and len(phones) > 1 else generate_unique_numbers(),
                PatientEmail = email_patients,
                CreatedAt = datetime.now(),
            )

            username = generate_random_letters()
            password = generate_random_letters_and_digits()

            user = User(
                username = username,
                email = new_patient.PatientEmail,
                password = generate_password_hash(password),
                is_patient = True,
                first_name = new_patient.PatientName,
                last_name = new_patient.PatientLastname,
                gender= new_patient.PatientGender,
            )

            try:
                user.roles.append(db.session.execute(select(Role).filter_by(name='Patient')).scalar_one())
            except Exception as e:
                return {"message" : "Le rôle Patient n'existe pas. Veuillez le créer avant d'ajouter un patient."}
            
            if db.session.execute(select(User).filter_by(email=new_patient.PatientEmail)).scalar_one_or_none():
                continue
                # return {"message" : f"L'email {new_patient.PatientEmail} existe déjà. Veuillez réessayer."}
            
            db.session.add(user)
            db.session.flush()

            user_id = user.id

            if db.session.execute(select(Patients).filter_by(PatientEmail=new_patient.PatientEmail)).scalar_one_or_none() or db.session.execute(select(Patients).filter_by(PatientPhone=new_patient.PatientPhone)).scalar_one_or_none() or db.session.execute(select(Patients).filter_by(PatientFederationID=new_patient.PatientFederationID)).scalar_one_or_none():
                continue
            
            new_patient.user_id = user_id
            db.session.add(new_patient)
            db.session.commit()

    return {"message" : "Tous les patients vérifiés ont été extraits avec succès."}


import logging
from flask import jsonify
from sqlalchemy import select
from werkzeug.security import generate_password_hash
from datetime import datetime

# S'assurer d'importer Doctors, Patients, User, Role
from ..accounts.models import Doctors, Patients, User, Role

@charges_patients.route('/extract/<string:fed>', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@tryton.transaction()
@jwt_required()
@require_any_permission(["administration.charge_patients.extract_patients"])
def extract_patients(fed=None):
    """Charger un patient en particulier"""
    if not fed:
        return {"message": "Le Federation Account doit être fourni."}, 400
    
    patient_exists = db.session.execute(select(Patients).filter_by(PatientFederationID=fed)).scalar_one_or_none()
    if patient_exists:
        return {"message": f"Le patient avec le federation ID {fed} existe déjà."}, 400
    
    try:
        patients = tryton.pool.get('party.party').search([
            ('federation_account', '=', fed), 
            ('is_patient', '=', True), 
            ('result_online', '=', True)
        ])

        if not patients:
            return {"message": "Aucun patient correspondant trouvé dans Tryton/GNU Health."}, 404

        ContactMechanism = tryton.pool.get('party.contact_mechanism')

        emails = ContactMechanism.search([
            ('party', '=', patients[0].id),
            ('type', '=', 'email'),
        ])

        phones = ContactMechanism.search([
            ('party', '=', patients[0].id),
            ('type', '=', 'phone'),
        ])

        email_patients = emails[0].value if emails else generate_random_email()
        phone1 = phones[0].value if phones else generate_unique_numbers()
        
        for patient in patients:
            if patient.active:
                # Récupération du rôle Patient
                role_patient = db.session.execute(select(Role).filter_by(name='Patient')).scalar_one_or_none()
                if not role_patient:
                    return {"message": "Le rôle Patient n'existe pas. Veuillez le créer avant d'ajouter un patient."}, 400

                PatientFederationID = patient.federation_account

                # 1. Vérification de l'existence d'un Docteur avec le même matricule
                existing_doctor = None
                if PatientFederationID:
                    existing_doctor = db.session.execute(
                        select(Doctors).filter_by(DoctorFederationID=PatientFederationID)
                    ).scalar_one_or_none()

                user = None
                if existing_doctor and existing_doctor.user_id:
                    # 2. CAS DOCTEUR EXISTANT : Réutilisation du compte Utilisateur lié
                    user = db.session.execute(
                        select(User).filter_by(id=existing_doctor.user_id)
                    ).scalar_one_or_none()

                    if user:
                        user.is_patient = True
                        if role_patient not in user.roles:
                            user.roles.append(role_patient)

                if not user:
                    # 3. CAS NOUVEAU PATIENT SEULEMENT : Création d'un utilisateur dédié
                    if db.session.execute(select(User).filter_by(email=email_patients)).scalar_one_or_none():
                        return {"message": f"L'email {email_patients} existe déjà. Veuillez réessayer."}, 400

                    username = generate_random_letters()
                    password = generate_random_letters_and_digits()

                    user = User(
                        username=username,
                        email=email_patients,
                        password=generate_password_hash(password),
                        is_patient=True,
                        first_name=patient.name,
                        last_name=patient.lastname,
                        gender=patient.gender,
                    )
                    user.roles.append(role_patient)
                    db.session.add(user)
                    db.session.flush()

                user_id = user.id

                # Vérification des doublons sur la table Patients
                if (db.session.execute(select(Patients).filter_by(PatientEmail=email_patients)).scalar_one_or_none() or 
                    db.session.execute(select(Patients).filter_by(PatientPhone=phone1)).scalar_one_or_none() or 
                    db.session.execute(select(Patients).filter_by(PatientFederationID=PatientFederationID)).scalar_one_or_none()):
                    db.session.rollback()
                    return {"message": "Le patient que vous essayez d'enregistrer existe déjà."}, 400

                # Instanciation du modèle Patients
                new_patient = Patients(
                    PatientFederationID=PatientFederationID,
                    PatientName=patient.name,
                    PatientLastname=patient.lastname,
                    PatientDOB=patient.dob,
                    PatientPOB="Cameroun",
                    PatientNat="Camerounaise",
                    PatientCNI="",
                    PatientGender=patient.gender,
                    PatientPhone=phone1,
                    PatientPhone2=phones[1].value if phones and len(phones) > 1 else generate_unique_numbers(),
                    PatientEmail=email_patients,
                    CreatedAt=datetime.now(),
                    user_id=user_id
                )

                db.session.add(new_patient)
                db.session.commit()

                return jsonify(new_patient.to_dict())

        return {"message": "Aucun patient actif trouvé dans Tryton avec ce numéro."}, 404

    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors du chargement du patient {fed}: {e}")
        return {"message": "Erreur serveur lors de l'extraction."}, 500


# AVOIR LA LISTE DE TOUS LES PATIENTS
@charges_patients.route('/all', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.charge_patients.all_patients"])
def get_all_patients():
    """Obtenir la liste de tous les patients

    Returns:
        json: liste des patients
    """
    patients = db.session.execute(select(Patients)).scalars().all()
    if patients:
        return jsonify([patient.to_dict() for patient in patients])
    else :
        return {"Message ": "Aucun Patient Trouvé."}


import logging
from sqlalchemy import select

# SUPPRIMER UN PATIENT EN PARTICULIER (AVEC CASCADE USER ET DOCTEUR)
@charges_patients.route('/delete/<int:patient_id>', methods=['DELETE', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.charge_patients.delete_patient"])
def delete_patient(patient_id=None):
    """Supprimer un patient en particulier ainsi que son compte utilisateur et son profil docteur associé s'il existe."""
    if not patient_id:
        return {"Message": "L'ID du patient doit être fourni."}, 400

    patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()
    if not patient:
        return {"Message": "Aucun Patient Trouvé à cet id."}, 404

    try:
        user_id = patient.user_id
        doctor = None

        # 1. Rechercher si un profil Docteur est associé au même compte Utilisateur
        if user_id:
            doctor = db.session.execute(select(Doctors).filter_by(user_id=user_id)).scalar_one_or_none()

        # 2. Supprimer l'enregistrement du Patient
        db.session.delete(patient)

        # 3. Supprimer le profil Docteur associé si existant
        if doctor:
            db.session.delete(doctor)

        # Effectuer un flush pour libérer les clés étrangères des entités dépendantes
        db.session.flush()

        # 4. Supprimer le compte Utilisateur sous-jacent
        if user_id:
            user = db.session.execute(select(User).filter_by(id=user_id)).scalar_one_or_none()
            if user:
                db.session.delete(user)

        # 5. Valider la transaction complète
        db.session.commit()
        return {"Message": f"Le Patient (ID: {patient_id}), l'utilisateur et le profil docteur associés ont été supprimés avec succès."}

    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la suppression en cascade du patient {patient_id}: {e}")
        return {"Message": "Impossible de supprimer le patient. Des dépendances de données empêchent la suppression."}, 500
    

# AVOIR LE NOMBRE DE PATIENTS
@charges_patients.route('/count', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.charge_patients.count_patients"])
def count_patients():
    """Obtenir le nombre total de patients

    Returns:
        json: json avec le nombre total de patients
    """
    count = db.session.execute(select(Patients)).scalars().all()

    return {"TotalPatients" : len(count)}


#  MISE À JOUR D'UN PATIENT EN PARTICULIER
@charges_patients.route('/update/<int:patient_id>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["administration.charge_patients.update_patients",
                         "patients.charge_patients.update_patients"])
def update_patient(patient_id=None):
    """Mettre à jour un patient en particulier

    Args:
        patient_id (int, optional): L'identifiant du patient. Defaults to None.

    Returns:
        json: json avec le message de succès ou d'erreur
    """

    if patient_id:
        patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()
        if patient:
            data = request.get_json()

            patient.PatientName = data.get('PatientName', patient.PatientName)
            patient.PatientLastname = data.get('PatientLastname', patient.PatientLastname)
            patient.PatientDOB = data.get('PatientDOB', patient.PatientDOB)
            patient.PatientPOB = data.get('PatientPOB', patient.PatientPOB)
            patient.PatientNat = data.get('PatientNat', patient.PatientNat)
            patient.PatientCNI = data.get('PatientCNI', patient.PatientCNI)
            patient.PatientGender = data.get('PatientGender', patient.PatientGender)
            patient.PatientPhone = data.get('PatientPhone', patient.PatientPhone)
            patient.PatientPhone2 = data.get('PatientPhone2', patient.PatientPhone2)
            patient.PatientEmail = data.get('PatientEmail', patient.PatientEmail)
            patient.ModifiedAt = datetime.now()
            
            # Mettre à jour patient_is_confirmed à True après la mise à jour
            patient.patient_is_confirmed = True

            db.session.commit()

            patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()

            user_id = patient.user_id
            user = db.session.execute(select(User).filter_by(id=user_id)).scalar_one_or_none()
            if user:
                user.first_name = patient.PatientName
                user.last_name = patient.PatientLastname
                user.email = patient.PatientEmail
                
                # Mettre à jour is_confirmed de l'utilisateur à True
                user.is_confirmed = True
                user.confirmed_on = datetime.now()

                db.session.commit()

                return {"Message ": f"Le Patient avec l'id {patient_id} a été mis à jour avec succès.", "data" : patient.to_dict()}
            else :
                return {"Message ": "Aucun Utilisateur Trouvé pour ce patient."}
            
        else :
            return {"Message ": "Aucun Patient Trouvé à cet id."}


# Confirmation d'un Patient
@charges_patients.route('/confirm/<int:patient_id>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["administration.charge_patients.confirm_patients",
                         "patients.charge_patients.confirm_patients"])
def confirm_patient(patient_id=None):
    """Confirmer un patient en particulier

    Args:
        patient_id (int, optional): L'identifiant du patient. Defaults to None.

    Returns:
        json: json avec le message de succès ou d'erreur
    """

    if patient_id:
        patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()
        if patient:
            patient.patient_is_confirmed = True
            db.session.commit()

            return {"Message ": f"Le Patient avec l'id {patient_id} a été confirmé avec succès."}
        else :
            return {"Message ": "Aucun Patient Trouvé à cet id."}
    else :
        return {"Message ": "Fournir un ID pour Continuer Normalement."}
    

# AVOIR UN PATIENT EN PARTICULIER
@charges_patients.route('/<int:patient_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient'])
@jwt_required()
@require_any_permission(["administration.charge_patients.get_info_patient",
                         "patients.charge_patients.get_info_patient"])
def get_patient(patient_id=None):
    """Obtenir un patient en particulier

    Args:
        patient_id (int, optional): L'identifiant du patient. Defaults to None.

    Returns:
        json: json avec les informations du patient
    """
    if patient_id:
        patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()
        if patient :
            return jsonify(patient.to_dict())
        else :
            return {"Message ": "Aucun Patient Trouvé à cet id."}
    else :
        patients = db.session.execute(select(Patients)).scalars().all()
        if patients:
            return jsonify([patient.to_dict() for patient in patients])
        

# LISTE DE TOUTES LES FACTURES D'UN PATIENT
@charges_patients.route('factures/<int:patient_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@tryton.transaction()
# @roles_required(['Admin', 'Patient'])
@require_any_permission(["administration.charge_patients.all_invoices",
                         "patients.charge_patients.all_invoices"])
def all_invoices(patient_id=None):

    if patient_id:
        patient = db.session.execute(select(Patients).filter_by(id=patient_id)).scalar_one_or_none()

        if not patient:
            return {"message" : "Patient Non Trouvé. "}

        # Un patient ne consulte que ses propres factures.
        if patient.user_id != current_user.id and not current_user.has_permission("administration.charge_patients.all_invoices"):
            return {"message" : "Accès refusé."}, 403

        federationID = gnuhealth_ref(patient)

        invoices = None
        if  federationID:
            invoices = tryton.pool.get('account.invoice').search([('party.federation_account', '=', federationID)])
        
        draft_invoices = []
        ok_invoices = []
        if invoices:
            for invoice in invoices:
                if invoice.number not in ok_invoices:
                    ok_invoices.append(invoice.number)

            for invoice in invoices:
                if invoice.reference in ok_invoices:
                    ok_invoices.remove(invoice.reference)
                    ok_invoices.remove(invoice.number)
        
        invoices_list = []
        establishment_name = gnuhealth_establishment_name() if ok_invoices else None
        for inv in ok_invoices:
            invoices2 = tryton.pool.get('account.invoice').search([('party.federation_account', '=', federationID), ('number', '=' , inv)])

            invoice = invoices2[0] if invoices2 else None
            if invoice :
                invoices_list.append({
                    'invoice_number': invoice.number,
                    'date': invoice.invoice_date,
                    'amount_to_pay' : invoice.amount_to_pay,
                    'montant_assurance' : invoice.montant_assurance,
                    'montant_patient' : invoice.montant_patient if float(invoice.montant_patient) > float(25) else float(0),
                    'reference' : invoice.reference,
                    'untaxed_amount': float(invoice.untaxed_amount),
                    'amount_to_pay_today': float(invoice.amount_to_pay_today),
                    'state': invoice.state,
                    'total_amount2' : float(invoice.total_amount2),
                    'establishment' : establishment_name,
                })

        # Factures des autres établissements rattachés (reçues par l'API ROHAFYA ou FHIR).
        invoices_list.extend(patient_records(patient, KIND_INVOICE))
        return jsonify(invoices_list)
    

# LISTE DES PRODUITS / EXAMENS D'UNE FACTURE D'UN PATIENT
@charges_patients.route('factures/products/<string:invoice_number>', methods=['GET'])
@cross_origin(supports_credentials=True)
@tryton.transaction()
@jwt_required()
# @roles_required(['Admin', 'Patient'])
@require_any_permission(["administration.charge_patients.all_products",
                         "patients.charge_patients.all_products"])
def all_product(invoice_number=None):

    if invoice_number:
        rohafya_patient = db.session.execute(select(Patients).filter_by(user_id=current_user.id)).scalar_one_or_none()
        record, _ = find_patient_record(rohafya_patient, KIND_INVOICE, invoice_number)
        if record is not None:
            return jsonify(list(record.details or []))

        invoice = tryton.pool.get('account.invoice').search([('number', '=', invoice_number)])
        if not invoice:
            return {"message" : "Facture Non Trouvée. "}
        
        invoice_lines = tryton.pool.get('account.invoice.line').search([('invoice', '=', invoice[0].id)])
        products_list = []
        for line in invoice_lines:
            products_list.append({
                'product_name' : line.product.name,
                'quantity' : line.quantity,
            })
        
        return jsonify(products_list)
    else :
        return {"message" : "Le Numéro de la Facture doit être fourni. "}
    








