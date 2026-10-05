#  LA FONCTION PERMETTANT LE CHARGEMENT DES DOCTEURS DANS BASE DE DONNÉES DOCTEUR
from flask import Blueprint, jsonify, request, current_app, send_file
from flask_jwt_extended import jwt_required
from flask_tryton import Tryton
from ..deco.decorators import roles_required, require_any_permission
from flask_cors import CORS, cross_origin
from ..deco.generators import generate_random_email, generate_unique_numbers
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
from ..accounts.models import Doctors, User, Role
from ..deco.generators import generate_random_letters, generate_random_letters_and_digits
from ..email.email import send_email
from Rohafya import db, tryton
from io import BytesIO
from sqlalchemy import select
import logging

gnu_doctors = Blueprint('charge_doctors', __name__, url_prefix='/doctors/')

fichier = "username_password.txt"

#  Récupérer tous les docteurs de la plateforme de GNU avec cette méthode
@gnu_doctors.route('/extract', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@require_any_permission(["administration.doctors_charge_doctors.charge_doctors"])
@tryton.transaction()
@jwt_required()
def charge_doctors():
    """Recharge de tous les docteurs de GNU dans l'app.
        Le chargement des docteurs se fait à partir de gnuhealth.healthprofessional

    Returns:
        json: Json descriptif du dernier docteur ajouté.
    """
    try:
        doctors = tryton.pool.get('gnuhealth.healthprofessional')
        GNUDoctors = doctors.search([('code', '!=', None)], limit=50)

        for doctor in GNUDoctors:
            if doctor.active:
                DoctorNO = doctor.code
                DoctorName = doctor.name.name
                DoctorLastname = doctor.name.lastname
                DoctorFederationID = doctor.name.federation_account
                DoctorDOB = doctor.name.dob
                DoctorPOB = "Cameroun"
                DoctorNat = "Cameroun"
                DoctorCNI = ""
                DoctorGender = doctor.name.gender

                DoctorSignature = doctor.name.signature2 if hasattr(doctor, "signature2") else None
                
                DoctorPhone = generate_unique_numbers()
                DoctorPhone2 = generate_unique_numbers()
                DoctorEmail = generate_random_email()
                CreatedAt = datetime.now()
                if doctor.main_specialty and doctor.main_specialty.specialty:
                    Speciality = doctor.main_specialty.specialty.name
                else:
                    Speciality = "Nothing"

                username = generate_random_letters()
                password = generate_random_letters_and_digits()

                user = User(username=username, email=DoctorEmail, password=generate_password_hash(password),
                            first_name=DoctorName, last_name=DoctorLastname, gender=DoctorGender,
                            is_doctor=True)
                
                role_doc = db.session.execute(select(Role).filter_by(name='Doctor')).scalar_one_or_none()
                if role_doc:
                    user.roles.append(role_doc)
                else:
                    return {"message": "Role Doctor Non Trouvé"}, 400
                
                if db.session.execute(select(User).filter_by(email=DoctorEmail)).scalar_one_or_none() is not None or db.session.execute(select(Doctors).filter_by(DoctorNO=DoctorNO)).scalar_one_or_none():
                    continue
                
                db.session.add(user)
                db.session.commit()

                ligne = f"{DoctorName} {DoctorLastname} ----------------------------- {username} {password}\n"
                with open(fichier, 'a') as f:
                    f.write(ligne)

                user_id = user.id

                if (db.session.execute(select(Doctors).filter_by(DoctorNO=DoctorNO)).scalar_one_or_none() or 
                    db.session.execute(select(Doctors).filter_by(DoctorEmail=DoctorEmail)).scalar_one_or_none() or 
                    db.session.execute(select(Doctors).filter_by(DoctorPhone=DoctorPhone)).scalar_one_or_none() or 
                    db.session.execute(select(Doctors).filter_by(DoctorPhone2=DoctorPhone2)).scalar_one_or_none() or 
                    db.session.execute(select(Doctors).filter_by(DoctorFederationID=DoctorFederationID)).scalar_one_or_none()):
                    db.session.delete(user)
                    db.session.commit()
                    continue
                    
                doctor_obj = Doctors(CreatedAt=CreatedAt,
                                     DoctorFederationID=DoctorFederationID,
                                     DoctorPOB=DoctorPOB,
                                     DoctorNat=DoctorNat,
                                     DoctorCNI=DoctorCNI,
                                     Speciality=Speciality,
                                     DoctorPhone=DoctorPhone,
                                     DoctorPhone2=DoctorPhone2,
                                     DoctorNO=DoctorNO,
                                     DoctorGender=DoctorGender,
                                     DoctorName=DoctorName,
                                     DoctorLastname=DoctorLastname,
                                     DoctorDOB=DoctorDOB,
                                     DoctorEmail=DoctorEmail,
                                     DoctorSignature=DoctorSignature,
                                     user_id=user_id)
                
                db.session.add(doctor_obj)
                db.session.commit()

        return {"message": "Les docteurs ont bien été extraits de GNU."}
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de l'extraction des docteurs : {e}")
        return {"message": "Erreur serveur lors de l'extraction."}, 500


# Import de la classe Patients si non présent en haut de votre fichier
from ..accounts.models import Patients 

@gnu_doctors.route('/extract/<string:onmc>/', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@tryton.transaction()
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.charge_one_doctors"])
def charge_doctor(onmc=None):
    """Charger un docteur en particulier"""
    if not onmc:
        return {"message": "Aucun Numéro d'ordre renseigné."}, 400
    
    doctor = db.session.execute(select(Doctors).filter_by(DoctorNO=onmc)).scalar_one_or_none()
    if doctor:
        return {"message": f"Le docteur avec cet ordre {onmc} existe déjà."}, 400
    
    try:
        doctors = tryton.pool.get('gnuhealth.healthprofessional')
        GNUDoctors = doctors.search([('code', '=', onmc)])

        for doctor in GNUDoctors:
            if doctor.active:
                DoctorNO = doctor.code
                DoctorName = doctor.name.name
                DoctorLastname = doctor.name.lastname
                DoctorFederationID = doctor.name.federation_account
                DoctorDOB = doctor.name.dob
                DoctorPOB = "Cameroun"
                DoctorNat = "Camerounaise"
                DoctorCNI = ""
                DoctorGender = doctor.name.gender
                DoctorPhone = generate_unique_numbers()
                DoctorPhone2 = generate_unique_numbers()
                DoctorEmail = generate_random_email()
                CreatedAt = datetime.now()
                if doctor.main_specialty and doctor.main_specialty.specialty:
                    Speciality = doctor.main_specialty.specialty.name
                else:
                    Speciality = "Nothing"

                # Récupération du rôle Doctor
                role_doc = db.session.execute(select(Role).filter_by(name='Doctor')).scalar_one_or_none()
                if not role_doc:
                    return {"message": "Rôle Doctor Non Trouvé"}, 400

                # 1. Vérification de l'existence du Patient
                existing_patient = None
                if DoctorFederationID:
                    existing_patient = db.session.execute(
                        select(Patients).filter_by(PatientFederationID=DoctorFederationID)
                    ).scalar_one_or_none()

                user = None
                if existing_patient and existing_patient.user_id:
                    # 2. CAS PATIENT EXISTANT : Réutilisation de l'utilisateur lié
                    user = db.session.execute(
                        select(User).filter_by(id=existing_patient.user_id)
                    ).scalar_one_or_none()

                    if user:
                        user.is_doctor = True
                        if role_doc not in user.roles:
                            user.roles.append(role_doc)
                
                if not user:
                    # 3. CAS NOUVEAU DOCTEUR SEULEMENT : Création d'un nouvel utilisateur
                    username = generate_random_letters()
                    password = generate_random_letters_and_digits()

                    if db.session.execute(select(User).filter_by(email=DoctorEmail)).scalar_one_or_none() is not None:
                        return {"message": "Email Existe Déjà"}, 400

                    user = User(
                        username=username, 
                        email=DoctorEmail, 
                        password=generate_password_hash(password),
                        first_name=DoctorName, 
                        last_name=DoctorLastname, 
                        gender=DoctorGender,
                        is_doctor=True
                    )
                    user.roles.append(role_doc)
                    db.session.add(user)
                    db.session.flush()

                    # Écriture des identifiants générés uniquement s'il s'agit d'un nouvel utilisateur
                    ligne = f"{DoctorName} {DoctorLastname} ----------------------------- {username} {password}\n"
                    with open(fichier, 'a') as f:
                        f.write(ligne)

                user_id = user.id

                # Vérification des doublons sur la table Doctors
                if (db.session.execute(select(Doctors).filter_by(DoctorEmail=DoctorEmail)).scalar_one_or_none() or 
                    db.session.execute(select(Doctors).filter_by(DoctorPhone=DoctorPhone)).scalar_one_or_none() or 
                    db.session.execute(select(Doctors).filter_by(DoctorFederationID=DoctorFederationID)).scalar_one_or_none()):
                    db.session.rollback()
                    return {"message": "Le Docteur ne peut être enregistré à cause de ses informations dupliquées."}, 400
                
                # Instanciation et création du Docteur
                doctor_obj = Doctors(
                    CreatedAt=CreatedAt,
                    DoctorFederationID=DoctorFederationID,
                    DoctorPOB=DoctorPOB,
                    DoctorNat=DoctorNat,
                    DoctorCNI=DoctorCNI,
                    Speciality=Speciality,
                    DoctorPhone=DoctorPhone,
                    DoctorPhone2=DoctorPhone2,
                    DoctorNO=DoctorNO,
                    DoctorGender=DoctorGender,
                    DoctorName=DoctorName,
                    DoctorLastname=DoctorLastname,
                    DoctorDOB=DoctorDOB,
                    DoctorEmail=DoctorEmail,
                    user_id=user_id
                )
                
                db.session.add(doctor_obj)
                db.session.commit()

                return jsonify(doctor_obj.to_dict())

        return {"message": "Aucun docteur actif trouvé dans GNU avec ce numéro."}, 404

    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors du chargement du docteur {onmc}: {e}")
        return {"message": "Erreur serveur lors de l'extraction."}, 500


#  Avoir la liste de tous les docteurs
@gnu_doctors.route('/', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.all_doctors"])
def all_doctors():
    """Tous les docteurs"""
    doctors = db.session.execute(select(Doctors)).scalars().all()
    doctors_lists = [doctor.to_dict() for doctor in doctors]

    return jsonify(doctors_lists)


#  Supprimer un docteur, son profil patient éventuel et son compte utilisateur associé
@gnu_doctors.route('/del/<int:doc_id>', methods=['DELETE', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.delete_doctor"])
def del_doc(doc_id=None, email=None):
    """Suppression d'un docteur, du profil patient associé et de son compte utilisateur lié"""
    if doc_id:
        doc = db.session.execute(select(Doctors).filter_by(id=doc_id)).scalar_one_or_none()
    elif email:
        doc = db.session.execute(select(Doctors).filter_by(DoctorEmail=email)).scalar_one_or_none()
    else:
        return {"Message": "L'ID docteur ou l'Email doit être fourni."}, 400

    if not doc:
        return {"Message": "Aucun Docteur Trouvé."}, 404

    try:
        user_id = doc.user_id
        patient = None

        # 1. Chercher si un profil Patient est associé au même compte utilisateur
        if user_id:
            patient = db.session.execute(select(Patients).filter_by(user_id=user_id)).scalar_one_or_none()

        # 2. Supprimer l'enregistrement du Docteur
        db.session.delete(doc)

        # 3. Supprimer le profil Patient associé s'il existe
        if patient:
            db.session.delete(patient)

        # Libération des contraintes de clés étrangères en mémoire
        db.session.flush()

        # 4. Supprimer le compte Utilisateur lié
        if user_id:
            user = db.session.execute(select(User).filter_by(id=user_id)).scalar_one_or_none()
            if user:
                db.session.delete(user)

        # 5. Validation de la transaction
        db.session.commit()
        return {"Message": "Docteur, profil patient et compte utilisateur associés supprimés avec succès."}

    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la suppression du docteur, du patient et de l'utilisateur : {e}")
        return {"Message": "Impossible de supprimer le docteur. Dépendances de données existantes."}, 500


#  Avoir le nombre de docteur
@gnu_doctors.route('/nbr', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.nbr_doctor"])
def nbr_doc():
    """Nombre de docteurs"""
    doctors = db.session.execute(select(Doctors)).scalars().all()
    nbr = len(doctors)

    return {"Nombre": nbr}


# Confirmation d'un docteur
@gnu_doctors.route('/confirm/<int:doctor_id>', methods=['PUT', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["doctors.doctors_charge_doctors.confirm_doctor",
                         "administration.doctors_charge_doctors.confirm_doctor"])
def confirm_doctor(doctor_id):
    """Confirmer un docteur"""
    if not doctor_id:
        return {"Message": "Aucun Docteur Trouvé à cet id."}, 400

    doctor = db.session.execute(select(Doctors).filter_by(id=doctor_id)).scalar_one_or_none()

    if not doctor:
        return {"Message": "Aucun Docteur Trouvé à cet ID."}, 404

    try:
        doctor.doctor_is_confirmed = True
        db.session.commit()
        return {"Message": "Docteur Confirmé avec succès.", "Doctor": doctor.to_dict()}
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la confirmation du docteur : {e}")
        return {"Message": "Erreur serveur lors de la confirmation."}, 500

#  Mise à jour des informations d'un docteur
@gnu_doctors.route('/update/<int:doctor_id>', methods=["PUT", "OPTIONS"])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.update_doctor",
                         "doctors.doctors_charge_doctors.update_doctor"])
def update_doctor(doctor_id):
    """Mise à Jour d'un docteur"""
    if not doctor_id:
        return {"Message": "Aucun Docteur Trouvé à cet id."}, 400

    doctor = db.session.execute(select(Doctors).filter_by(id=doctor_id)).scalar_one_or_none()

    if not doctor:
        return {"Message": "Aucun Docteur Trouvé à cet ID."}, 404

    if request.method == "PUT":
        data = request.json or {}
        doctor.DoctorName = data.get("DoctorName", doctor.DoctorName)
        doctor.DoctorLastname = data.get("DoctorLastname", doctor.DoctorLastname)
        doctor.DoctorDOB = data.get("DoctorDOB", doctor.DoctorDOB)
        doctor.DoctorEmail = data.get("DoctorEmail", doctor.DoctorEmail)
        doctor.DoctorPhone = data.get("DoctorPhone", doctor.DoctorPhone)
        doctor.DoctorPhone2 = data.get("DoctorPhone2", doctor.DoctorPhone2)
        doctor.DoctorPOB = data.get("DoctorPOB", doctor.DoctorPOB)
        doctor.DoctorNat = data.get("DoctorPOB", doctor.DoctorPOB)
        doctor.DoctorCNI = data.get("DoctorCNI", doctor.DoctorCNI)
        doctor.Speciality = data.get("Speciality", doctor.Speciality)
        doctor.ModifiedAt = datetime.now()

        if not doctor.DoctorNO and not doctor.DoctorName and not doctor.DoctorLastname and not doctor.DoctorDOB:
            return {"message": "Vérifiez la totalité de vos informations avant de continuer"}, 400
        elif doctor.DoctorPhone == doctor.DoctorPhone2:
            return {"message": "Les Numéros ne Doivent pas être identiques"}, 400

        try:
            if doctor.user_id:
                user = db.session.execute(select(User).filter_by(id=doctor.user_id)).scalar_one_or_none()
                if user:
                    user.email = doctor.DoctorEmail
                    user.first_name = doctor.DoctorName
                    user.last_name = doctor.DoctorLastname

            db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.error(f"Erreur lors de la mise à jour du docteur : {e}")
            return {"message": "Erreur lors de la mise à jour des informations."}, 500

    return {"Doctor": doctor.to_dict()}


#  Avoir les informations d'un docteur précis avec son id
@gnu_doctors.route('/informations/<int:doc_id>', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.get_info_doctor",
                         "patients.doctors_charge_doctors.get_info_doctor",
                         "doctors.doctors_charge_doctors.get_info_doctor"])
def getDocInfos(doc_id):
    """Avoir les infos d'un docteur"""
    doctor = db.session.execute(select(Doctors).filter_by(id=doc_id)).scalar_one_or_none()
    if doctor:
        return jsonify(doctor.to_dict())
    else:
        return {"Message": "Aucun Docteur Trouvé à cet ID."}, 404


# Avoir les informations d'un docteur précis en fonction de son matricule
@gnu_doctors.route('/informations/matricule/<string:matricule>', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.getDocInfosByMatricule",
                         "patients.doctors_charge_doctors.getDocInfosByMatricule",
                         "doctors.doctors_charge_doctors.getDocInfosByMatricule"])
def getDocInfosByMatricule(matricule):
    """Avoir les infos d'un docteur en fonction de son matricule"""
    doctor = db.session.execute(select(Doctors).filter_by(DoctorFederationID=matricule)).scalar_one_or_none()
    if doctor:
        return jsonify(doctor.to_dict())
    else:
        return {"Message": "Aucun Docteur Trouvé à ce Matricule."}, 404


# Récupérer la signature du docteur
@gnu_doctors.route('/signature/matricule/<string:matricule>', methods=['GET', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.doctors_charge_doctors.getDocInfosByMatricule",
                         "patients.doctors_charge_doctors.getDocInfosByMatricule",
                         "doctors.doctors_charge_doctors.getDocInfosByMatricule"])
@tryton.transaction()
def getDocSignatureByMatricule(matricule):
    """Récupérer la signature d'un docteur en fonction de son matricule"""
    party = tryton.pool.get('party.party')
    GNUDoctors = party.search([('federation_account', '=', matricule)])

    if not GNUDoctors:
        return {"error": "Docteur introuvable"}, 404
    
    doctor = GNUDoctors[0]

    # signature binaire tryton
    signature_binary = getattr(doctor, "signature2", None)

    if not signature_binary:
        return {"error": "Signature inexistante"}, 404
    
    mimetype = detect_mimetype(signature_binary)
    file_obj = BytesIO(signature_binary)

    return send_file(
        file_obj,
        mimetype=mimetype,
        download_name=f"signature_{matricule}",
        as_attachment=False
    )


def detect_mimetype(b: bytes) -> str:
    if b.startswith(b'\x89PNG\r\n\x1a\n'):
        return "image/png"
    if b.startswith(b'\xff\xd8\xff'):
        return "image/jpeg"
    if b.startswith(b'GIF8'):
        return "image/gif"
    return "application/octet-stream"