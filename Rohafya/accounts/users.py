from flask import Blueprint,g, render_template, redirect, url_for, flash, request, session, current_app
from flask_jwt_extended import jwt_required
from flask_login import current_user, login_user, logout_user, current_user
from .models import Notifications, User, Role, Doctors, Patients
from flask import jsonify
from flask_user import UserManager, UserMixin, PasswordManager
from .models import db
from functools import wraps
from Rohafya.deco.generators import generate_random_letters, generate_random_letters_and_digits
from werkzeug.security import check_password_hash, generate_password_hash
from flask_cors import CORS, cross_origin
from Rohafya.deco.decorators import require_any_permission, roles_required
from datetime import datetime
from Rohafya.email.email import send_email
from Rohafya import create_app
from sqlalchemy import or_, select
import logging

userd = Blueprint('userd', __name__, url_prefix='/users/')

@userd.route("/send_email/", methods=["POST"])
@cross_origin(supports_credentials=True)
def send_mail():
    # .get_json(silent=True) évite un crash si le header Content-Type de la requête n'est pas application/json
    data = request.get_json(silent=True) or {}
    
    # L'utilisation de .get() évite la KeyError. 
    # On vérifie vos clés spécifiques, avec 'federation_id' en solution de repli
    doctor_id = data.get("DoctorFederationID") or data.get("federation_id")
    patient_id = data.get("PatientFederationID") or data.get("federation_id")

    if not doctor_id and not patient_id:
        return {"message": "Aucun Email ou Matricule fourni dans la requête."}, 401

    # Utilisez current_app au lieu d'appeler create_app() à chaque requête
    passwordmanager = PasswordManager(current_app)
    hash_password = passwordmanager.hash_password
    
    username = generate_random_letters()
    password = generate_random_letters_and_digits()
    
    # Recherche de l'utilisateur (Docteur d'abord, puis Patient)
    user_d = None
    if doctor_id:
        user_d = db.session.execute(select(Doctors).filter_by(DoctorFederationID=doctor_id)).scalar_one_or_none()

    if not user_d and patient_id:
        user_d = db.session.execute(select(Patients).filter_by(PatientFederationID=patient_id)).scalar_one_or_none()
    
    if not user_d:
        return {"message": "Aucun Docteur ou Patient Trouvé avec cet Email / Matricule"}, 401
    
    # Récupération du compte global
    user = db.session.execute(select(User).filter_by(id=user_d.user_id)).scalar_one_or_none()
    
    if not user:
        return {"message": "Veuillez vérifier le matricule entré."}, 401

    # Mise à jour des identifiants
    user.username = username
    user.password = hash_password(password)

    # Récupération propre du matricule selon le type d'utilisateur
    matricule = getattr(user_d, 'DoctorFederationID', None) or getattr(user_d, 'PatientFederationID', None)

    body = "Paramètres de Connexion."
    context = {
        "first_name": f"{user.first_name} {user.last_name}",
        "matricule": matricule,
        "email": user.email,
        "Date": datetime.now(),
        "username": username,
        "password": password
    }

    subject = "PARAMETRES DE CONNEXION A L'APPLICATION"
    template_html = "emails/connexion_email.html"
    
    try:
        send_email(user.email, subject, body, template_html, **context)
        # On ne commit en base de données QUE si l'email a bien été envoyé
        db.session.commit()

        return {
            "user_email": user.email,
            "Message": "Identifiants Envoyés avec succès",
            "username": username
        }
    except Exception as e:
        # En cas d'échec de l'email, on annule les modifications du mot de passe
        db.session.rollback()
        return {"message": "Erreur lors de l'envoi de l'email (Email Inexistant)."}, 401
        
@userd.route("/all", methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.user_management.all_user"])
def all_user():
    users = db.session.execute(select(User)).scalars().all()
    users_list = [user.to_dict() for user in users]

    return jsonify(users_list)


@userd.route('nb', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.user_management.delete_user"])
def nbr_user():

    users = db.session.execute(select(User)).scalars().all()
    nbr = len(users)
    return {"Nombre Utilisateur " : nbr}


@userd.route('/del/<int:user_id>', methods=['DELETE', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.user_management.delete_user"])
def del_user(user_id):
    if not user_id:
        return {"message": "L'ID utilisateur doit être fourni."}, 400

    user = db.session.execute(select(User).filter_by(id=user_id)).scalar_one_or_none()
    if not user:
        return {"message": "Aucun utilisateur trouvé."}, 404

    try:
        doctor = db.session.execute(select(Doctors).filter_by(user_id=user_id)).scalar_one_or_none()
        patient = db.session.execute(select(Patients).filter_by(user_id=user_id)).scalar_one_or_none()
        
        if doctor:
            db.session.delete(doctor)

        if patient:
            db.session.delete(patient)

        db.session.delete(user)
        db.session.commit()
        return {"message": "Utilisateur Supprimé avec succès."}
    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la suppression de l'utilisateur {user_id}: {e}")
        return {"message": "Impossible de supprimer l'utilisateur. Vérifiez les dépendances de données."}, 500


@userd.route('/delete-unconfirmed', methods=['DELETE', 'OPTIONS'])
@cross_origin(supports_credentials=True)
@jwt_required()
@require_any_permission(["administration.user_management.delete_user"])
def delete_unconfirmed_users():
    """
    Supprime tous les utilisateurs non confirmés ainsi que leurs dépendances
    (Patients, Doctors, Notifications, Rôles).
    """
    try:
        # 1. Sélectionner tous les utilisateurs non confirmés
        unconfirmed_users = db.session.execute(
            select(User).where(or_(User.is_confirmed == False, User.is_confirmed == None))
        ).scalars().all()

        if not unconfirmed_users:
            return {"message": "Aucun utilisateur non confirmé trouvé."}, 200

        deleted_count = len(unconfirmed_users)
        user_ids = [user.id for user in unconfirmed_users]

        # 2. Supprimer les notifications liées à ces utilisateurs
        notifications_to_delete = db.session.execute(
            select(Notifications).where(Notifications.user_id.in_(user_ids))
        ).scalars().all()
        for notification in notifications_to_delete:
            db.session.delete(notification)

        # 3. Supprimer les entrées Patients associées
        patients_to_delete = db.session.execute(
            select(Patients).where(Patients.user_id.in_(user_ids))
        ).scalars().all()
        for patient in patients_to_delete:
            db.session.delete(patient)

        # 4. Supprimer les entrées Doctors associées
        doctors_to_delete = db.session.execute(
            select(Doctors).where(Doctors.user_id.in_(user_ids))
        ).scalars().all()
        for doctor in doctors_to_delete:
            db.session.delete(doctor)

        # 5. Vider les relations Many-to-Many (ex: rôles) avant la suppression
        for user in unconfirmed_users:
            user.roles.clear()

        # Libérer les dépendances en mémoire
        db.session.flush()

        # 6. Supprimer les utilisateurs
        for user in unconfirmed_users:
            db.session.delete(user)

        # 7. Commit global
        db.session.commit()

        logging.info(f"{deleted_count} utilisateurs non confirmés supprimés.")
        return {
            "message": f"Suppression réussie. {deleted_count} utilisateur(s) non confirmé(s) supprimé(s).",
            "count": deleted_count
        }, 200

    except Exception as e:
        db.session.rollback()
        logging.error(f"Erreur lors de la suppression des utilisateurs non confirmés : {e}")
        return {"message": "Erreur lors de la suppression en masse des utilisateurs."}, 500

@userd.route('/mod/<int:user_id>', methods=['PUT'])
# @roles_required("Admin")
@require_any_permission(["administration.user_management.mod_user"])
@jwt_required()
@cross_origin(supports_credentials=True)
def mod_user(user_id=None, email=None):
    if user_id:
        user = db.session.execute(select(User).filter_by(id=user_id)).scalar_one_or_none()
    else :
        return {"message" : " L'ID utilisateur doit être fourni."}

    if user is None :
        return {"message" : " Aucun Utilisateur Trouvé"}

    if request.method == "PUT" :
        data = request.json
        user.first_name = data.get('first_name', user.first_name)
        user.last_name = data.get('last_name', user.last_name)
        user.email = data.get('email', user.email)

        if not user.email :
            error = "Vérifier que l'email ne soit pas vide. "
            return {"message": error}
        else :
            if data["roles"] != [] :
                for item in data["roles"]:
                    role = db.session.execute(select(Role).filter_by(name=item)).scalar_one_or_none()
                    if role :
                        user.roles.append(role)
                    else :
                        return {"message" : f"Le Role {item} n'existe pas."}
            db.session.commit()

    return {"user" : user.to_dict()}


@userd.route('/get/<int:user_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.user_management.view_user"])
def get_user(user_id=None):
    if user_id:
        user = db.session.execute(select(User).filter_by(id=user_id)).scalar_one_or_none()
    else :
        return {"message" : " L'ID utilisateur doit être fourni."}

    if user is None :
        return {"message" : " Aucun Utilisateur Trouvé"}

    return {"user" : user.to_dict()}