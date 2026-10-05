import os
import json
import datetime
from flask import Flask, request, render_template_string
#from flask_babelex import Babel
from flask_sqlalchemy import SQLAlchemy
from flask_user import login_required, roles_required, UserManager
from flask_login import UserMixin
from flask import current_app
from flask_user import UserMixin
from flask_tryton import Tryton
from Rohafya import db, login_manager
from sqlalchemy import JSON, ARRAY, event
from Rohafya.deco.generators import generate_sequence
import enum


class User(UserMixin, db.Model):
        __tablename__ = 'users'
        id = db.Column(db.Integer, primary_key=True)
        active = db.Column('is_active', db.Boolean(), nullable=False, server_default='1')

        # User authentication information. The collation='NOCASE' is required
        # to search case insensitively when USER_IFIND_MODE is 'nocase_collation'.
        username = db.Column(db.String(255), nullable=False, unique=True)
        email = db.Column(db.String(255), nullable=False, unique=False)
        email_confirmed_at = db.Column(db.DateTime())
        password = db.Column(db.String(255), nullable=False, server_default='')

        is_admin = db.Column(db.Boolean, nullable=False, default=False)
        is_doctor = db.Column(db.Boolean, nullable=False, default=False)
        is_patient = db.Column(db.Boolean, nullable=False, default=False)

        # Vérifier si le compte utilisateur a bien été validé et quand
        is_confirmed = db.Column(db.Boolean, nullable=False, default=False)
        confirmed_on = db.Column(db.DateTime, nullable=True)

        # User information
        first_name = db.Column(db.String(100), nullable=False, server_default='')
        last_name = db.Column(db.String(100), nullable=False, server_default='')
        gender = db.Column(db.String(2), server_default='')

        # Define the relationship to Role via UserRoles
        roles = db.relationship('Role', secondary='user_roles', passive_deletes=True)
        doctor = db.relationship('Doctors', back_populates='user', uselist=False,
                                 primaryjoin="User.id == Doctors.user_id")
        patients = db.relationship('Patients', back_populates='user', uselist=False,
                                   primaryjoin="User.id == Patients.user_id")


        def is_active(self):
            # Here you should write whatever the code is
            # that checks the database if your user is active
            return self.active

        def is_anonymous(self):
            return False

        def is_authenticated(self):
            return True

        def get_id(self):
            """Converts a User ID and parts of a User password hash to a token."""

            user_manager = current_app.user_manager

            user_id = self.id
            password_ends_with = '' if user_manager.USER_ENABLE_AUTH0 else self.password[-8:]
            user_token = user_manager.generate_token(
                user_id,               # User ID
                password_ends_with,    # Last 8 characters of user password
            )
            return user_token
        
        def has_permission(self, permission_code):
            for role in self.roles:
                for perm in role.permissions:
                    if perm.code == permission_code:
                        return True
            return False



        def to_dict(self):

            try:
                doctor_id  = self.doctor.id
            except AttributeError :
                doctor_id = None

            try:
                patient_id = self.patients.id
            except AttributeError:
                patient_id = None
            
            return {
                'id': self.id,
                'username' : self.username,
                'first_name': self.first_name,
                'last_name': self.last_name,
                'email' : self.email,
                'roles': [role.to_dict() for role in self.roles],
                'doctor_id' : doctor_id,
                'patient_id' : patient_id
            }

# Define the Role data-model
class Role(db.Model):
    __tablename__ = 'roles'
    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(50), unique=True)

    permissions = db.relationship(
        "Permissions",
        secondary="role_permissions",
        backref="roles",
    )

    def to_dict(self):
        return {
            'id'   : self.id,
            'name' : self.name
        }


class RolePermission(db.Model):
    __tablename__ = "role_permissions"
    role_id = db.Column(db.Integer, db.ForeignKey("roles.id"), primary_key=True)
    permission_id = db.Column(db.Integer, db.ForeignKey("permissions.id"), primary_key=True)


class Permissions(db.Model):
    __tablename__ = "permissions"
    id = db.Column(db.Integer, primary_key=True)
    code = db.Column(db.String(500), unique=True, nullable=False)
    description = db.Column(db.String(255))

    def to_dict(self):
        return {
            'code' : self.code,
            'description' : self.description
        }


# Define the UserRoles association table
class UserRoles(db.Model):
    __tablename__ = 'user_roles'
    id = db.Column(db.Integer(), primary_key=True)
    user_id = db.Column(db.Integer(), db.ForeignKey('users.id', ondelete='CASCADE'))
    role_id = db.Column(db.Integer(), db.ForeignKey('roles.id', ondelete='CASCADE'))

    def to_dict(self):
        return {
            'role_id' : self.role_id,
            'user_id' : self.user_id,
        }


class Requests(db.Model):
    __tablename__ = 'requests'
    id = db.Column(db.Integer(), primary_key=True)
    first_name = db.Column(db.String(100), nullable=False, unique=False, server_default='')
    last_name = db.Column(db.String(100), nullable=False, unique=False, server_default='')
    email = db.Column(db.String(100), nullable=False, unique=False, server_default='')
    message = db.Column(db.String(500), nullable=False, unique=False, server_default='')

    valide = db.Column(db.Boolean, nullable=True, default=False)
    rejected = db.Column(db.Boolean, nullable=True, default=False)
    administration = db.Column(db.Boolean, nullable=True, default=False)
    commission = db.Column(db.Boolean, nullable=True, default=False)
    revendication_examen = db.Column(db.Boolean, nullable=True, default=False)
    etat_patient = db.Column(db.Boolean, nullable=True, default=False)
    error = db.Column(db.Boolean, nullable=True, default=False)
    suggestion = db.Column(db.Boolean, nullable=True, default=False)
    connection = db.Column(db.Boolean, nullable=True, default=False)

    patient_request_examen_out = db.Column(db.Boolean, nullable=True, default=False)
    patient_request_prix_examen = db.Column(db.Boolean, nullable=True, default=False)
    patient_request_connexion = db.Column(db.Boolean, nullable=True, default=False)
    patient_request_other_administration = db.Column(db.Boolean, nullable=True, default=False)

    CreatedAt = db.Column(db.DateTime(), nullable=True)
    UpdatedAt = db.Column(db.DateTime(), nullable=True)
    CreatedBy = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'), nullable=True)
    UpdatedBy = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'))
    


    def to_dict(self):
        return {
        'id' : self.id,
        'first_name' : self.first_name,
        'last_name' : self.last_name,
        'email' : self.email,
        'message' : self.message,
        'valide' : self.valide,
        'administration' : self.administration,
        'commission' : self.commission,
        'revendication_examen' : self.revendication_examen,
        'error' : self.error,
        'suggestion' : self.suggestion,
        'etat_patient' : self.etat_patient,
        'patient_request_examen_out' : self.patient_request_examen_out,
        'patient_request_prix_examen' : self.patient_request_prix_examen,
        'patient_request_connexion' : self.patient_request_connexion,
        'patient_request_other_administration' : self.patient_request_other_administration,
        'connection' : self.connection,
        'UpdatedAt' : self.UpdatedAt,
        'CreatedAt' : self.CreatedAt,
        'UpdatedBy' : self.UpdatedBy,
        'CreatedBy' : self.CreatedBy
        }


class Doctors(db.Model):
    __tablename__ = 'doctors'
    id = db.Column(db.Integer(), primary_key=True)
    DoctorNO = db.Column(db.String(100), nullable=True, server_default='')
    DoctorName = db.Column(db.String(100), nullable=True, server_default='')
    DoctorLastname = db.Column(db.String(100), nullable=True, server_default='')
    DoctorFederationID = db.Column(db.String(100), nullable=True, server_default='')
    DoctorDOB = db.Column(db.DateTime(), nullable=True)
    DoctorPOB = db.Column(db.String(100), nullable=True, server_default='')
    DoctorNat = db.Column(db.String(100), nullable=True, server_default='')
    DoctorCNI = db.Column(db.String(20), nullable=True, server_default='')
    DoctorGender = db.Column(db.String(1), nullable=True, server_default='')
    DoctorPhone = db.Column(db.String(100), nullable=True, unique=True, server_default='')
    DoctorPhone2 = db.Column(db.String(100), nullable=True, unique=True, server_default='')
    DoctorEmail = db.Column(db.String(100), nullable=False, server_default='')
    CreatedAt = db.Column(db.DateTime(), nullable=True)
    ModifiedAt = db.Column(db.DateTime(), nullable=True)
    doctor_is_confirmed = db.Column(db.Boolean, default=False)
    Speciality = db.Column(db.String(100), nullable=True, server_default='')
    DoctorSignature = db.Column(db.LargeBinary, nullable=True)
    CodeIdentification = db.Column(db.String(100), nullable=True, server_default='')

    # Clé étrangère vers User
    user_id = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'), nullable=False)

    # Relation One-to-One avec User
    user = db.relationship('User', back_populates='doctor')

    # Relation One-to-Many avec Send Results
    send_results = db.relationship('Send_Results',  back_populates="doctor", cascade="all, delete")

    def to_dict(self):
        return {
            'id' : self.id,
            'DoctorNO' : self.DoctorNO,
            'DoctorName' : self.DoctorName,
            'DoctorLastname' : self.DoctorLastname,
            'DoctorFederationID' : self.DoctorFederationID,
            'user' : self.user.id,
            'DoctorPhone': self.DoctorPhone,
            'DoctorPhone2' : self.DoctorPhone2,
            'DoctorEmail' : self.DoctorEmail,
            'DoctorNat' : self.DoctorNat,
            'DoctorCNI' : self.DoctorCNI,
            'DoctorPOB' : self.DoctorPOB,
            'CreatedAt' : self.CreatedAt,
            'ModifiedAt': self.ModifiedAt,
            'DoctorDOB' : self.DoctorDOB,
            'Speciality' : self.Speciality,
            'doctor_is_confirmed' : self.doctor_is_confirmed,
            'CodeIdentification' : self.CodeIdentification,
            'DoctorGender' : self.DoctorGender
        }


class Blog(db.Model):
    __tablename__ = 'actualites'

    id = db.Column(db.Integer(), primary_key=True)
    titre = db.Column(db.String(250), nullable=True, server_default='')
    date = db.Column(db.DateTime(), nullable=True)
    description = db.Column(db.Text(), nullable=True)
    image_data = db.Column(db.LargeBinary, nullable=True)
    image_mimetype = db.Column(db.String(50), nullable=True)
    url = db.Column(db.String(250), nullable=True, server_default='')
    is_visible = db.Column(db.Boolean, nullable=True, default=True)

    def to_dict(self):
        return {
            'id' : self.id,
            'titre' : self.titre,
            'date' : self.date,
            'description' : self.description,
            'url' : self.url,
            'is_visible' : self.is_visible
        }


class UserActivity(db.Model):
    __tablename__ = "user_activity"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)

    # 🔹 Identifiant utilisateur (liens avec Doctor/User si tu veux)
    user_id = db.Column(db.Integer, nullable=True)

    # 🔹 Infos réseau
    ip = db.Column(db.String(45))  # IPv4 ou IPv6
    country = db.Column(db.String(100))
    city = db.Column(db.String(100))

    # 🔹 Infos navigateur / device
    browser = db.Column(db.String(50))
    platform = db.Column(db.String(50))
    user_agent = db.Column(db.Text)
    device_info = db.Column(JSON)  # stocke JSON envoyé par JS (screen, timezone, etc.)

    # 🔹 Infos session
    route = db.Column(ARRAY(JSON))
    method = db.Column(db.String(10))
    login_time = db.Column(db.DateTime, nullable=True)
    logout_time = db.Column(db.DateTime, nullable=True)
    last_seen = db.Column(db.DateTime, nullable=True)
    session_duration = db.Column(db.Float, default=0)  # en secondes
    active_time = db.Column(db.Float, default=0)  # en secondes

    # 🔹 Date de l’événement (utile si tu veux suivre navigation par page)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    modified_at = db.Column(db.DateTime, nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "ip": self.ip,
            "country": self.country,
            "city": self.city,
            "browser": self.browser,
            "platform": self.platform,
            "user_agent": self.user_agent,
            "device_info": self.device_info,
            "route": self.route,
            "method": self.method,
            "login_time": self.login_time,
            "logout_time": self.logout_time,
            "last_seen" : self.last_seen,
            "session_duration": self.session_duration,
            "active_time": self.active_time,
            "created_at": self.created_at,
            "modified_at": self.modified_at
        }
    
    def __repr__(self):
        return f"<UserActivity user_id={self.user_id} ip={self.ip} route={self.route}>"


class Notifications_Type(db.Model):
    __tablename__ = "notifications_type"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), nullable=False, unique=True)  # info, warning, alert, etc.
    description = db.Column(db.String(255), nullable=True)


    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description
        }
    

class Notifications_Etiquettes(db.Model):
    __tablename__ = "notifications_etiquettes"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    notifications_id = db.Column(db.Integer, db.ForeignKey('notifications.id'))
    etiquettes_id = db.Column(db.Integer, db.ForeignKey('etiquettes_notifications.id'))  # info, warning, alert, etc.
    
    def to_dict(self):
        return {
            "id": self.id,
            "notifications_id": self.notifications_id,
            "etiquettes_id": self.etiquettes_id
        }


class Etiquettes_notifications(db.Model):
    __tablename__ = "etiquettes_notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    name = db.Column(db.String(50), nullable=False, unique=True)  # info, warning, alert, etc.
    description = db.Column(db.String(255), nullable=True)


    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description
        }


# Nouvelle table pour gérer les suppressions "soft" par utilisateur
class UserDeletedNotifications(db.Model):
    __tablename__ = "user_deleted_notifications"
    
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), primary_key=True)
    notification_id = db.Column(db.Integer, db.ForeignKey('notifications.id'), primary_key=True)
    deleted_at = db.Column(db.DateTime, default=datetime.datetime.now)


class Notifications(db.Model):
    __tablename__ = "notifications"

    id = db.Column(db.Integer, primary_key=True, autoincrement=True)
    user_id = db.Column(db.Integer, db.ForeignKey('users.id'), nullable=True)
    title = db.Column(db.String(255), nullable=True)
    message = db.Column(db.String(255), nullable=False)
    is_read = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.datetime.now)
    updated_at = db.Column(db.DateTime, nullable=True)
    read_at = db.Column(db.DateTime, nullable=True)
    all_users = db.Column(db.Boolean, default=False)  # Si True, notification pour tous les utilisateurs

    types = db.Column(db.Integer, db.ForeignKey('notifications_type.id'), nullable=True)
    etiquettes = db.relationship('Etiquettes_notifications', secondary='notifications_etiquettes', backref='notifications', cascade="all, delete",
        passive_deletes=True)
    
    # Relation pour savoir qui a supprimé (optionnel, utile pour des requêtes complexes)
    deleted_by_users = db.relationship('UserDeletedNotifications', backref='notification', cascade="all, delete-orphan")

    def to_dict(self):
        return {
            "id": self.id,
            "user_id": self.user_id,
            "message": self.message,
            "is_read": self.is_read,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            'read_at' : self.read_at,
            'types' : self.types,
            'title' : self.title,
            'all_users' : self.all_users
        }


class Patients(db.Model):
    __tablename__ = 'patients'

    id = db.Column(db.Integer(), primary_key=True)
    PatientNO = db.Column(db.String(100), nullable=True, server_default='')
    PatientName = db.Column(db.String(100), nullable=True, server_default='')
    PatientLastname = db.Column(db.String(100), nullable=True, server_default='')
    PatientFederationID = db.Column(db.String(100), nullable=True, server_default='')
    PatientDOB = db.Column(db.DateTime(), nullable=True)
    PatientPOB = db.Column(db.String(100), nullable=True, server_default='')
    PatientNat = db.Column(db.String(100), nullable=True, server_default='')
    PatientCNI = db.Column(db.String(20), nullable=True, server_default='')
    PatientGender = db.Column(db.String(1), nullable=True, server_default='')
    PatientPhone = db.Column(db.String(100), nullable=True, unique=True, server_default='')
    PatientPhone2 = db.Column(db.String(100), nullable=True, unique=True, server_default='')
    PatientEmail = db.Column(db.String(100), nullable=False, server_default='')
    CreatedAt = db.Column(db.DateTime(), nullable=True)
    ModifiedAt = db.Column(db.DateTime(), nullable=True)
    patient_is_confirmed = db.Column(db.Boolean, default=False)

    # Clé étrangère vers User
    user_id = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'), nullable=False)

    # Relation One-to-One avec User
    user = db.relationship('User', back_populates='patients')

    # Relation One-to-Many : un patient a plusieurs prescriptions
    prescriptions = db.relationship(
        'Prescriptions',
        back_populates='patient',
        cascade="all, delete-orphan"
    )

    save_patients = db.relationship(
        'SavePatients',
        back_populates='patient',
        cascade="all, delete-orphan"
    )

    # Relation One-to-Many avec Send Results
    send_results = db.relationship('Send_Results',  back_populates="patient", cascade="all, delete")

    def to_dict(self):
        return {
            'id' : self.id,
            'PatientFederationID' : self.PatientFederationID,
            'PatientNO' : self.PatientNO,
            'PatientName' : self.PatientName,
            'PatientLastname' : self.PatientLastname,
            'PatientPhone': self.PatientPhone,
            'PatientPhone2' : self.PatientPhone2,
            'PatientEmail' : self.PatientEmail,
            'PatientNat' : self.PatientNat,
            'PatientCNI' : self.PatientCNI,
            'PatientPOB' : self.PatientPOB,
            'CreatedAt' : self.CreatedAt,
            'ModifiedAt': self.ModifiedAt,
            'PatientDOB' : self.PatientDOB,
            'PatientGender' : self.PatientGender,
            "user_id" : self.user_id,
            'patient_is_confirmed' : self.patient_is_confirmed,
        }


class TypeExamenEnum(enum.Enum):
    Imagerie = "Imagerie"
    Laboratoire = "Laboratoire"
    Exploration = "Exploration"


class Send_Results(db.Model):
    __tablename__ = 'send_results'

    id = db.Column(db.Integer(), primary_key=True)


    doctor_id = db.Column(db.Integer, db.ForeignKey('doctors.id',  ondelete='CASCADE'), nullable=False)
    doctor = db.relationship('Doctors', back_populates='send_results')

    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id',  ondelete='CASCADE'), nullable=False)
    patient = db.relationship('Patients', back_populates='send_results')
    
    # Type d'examen (Labo, Exploration, Imagerie, etc.)
    exam_type = db.Column(db.Enum(TypeExamenEnum), nullable=False)

    # Le code de l'examen (TEST2514, TEST3652, etc.)
    exam_code = db.Column(db.String(100), nullable=False)

    patient_federation_id = db.Column(db.String(100), nullable=False)

    sended_at = db.Column(db.DateTime, default=datetime.datetime.now)

    envoi_email = db.Column(db.Boolean, default=False)

    def to_dict(self):
        return {
            'id' : self.id,
            'doctor_id' : self.doctor_id,
            'patient_id' : self.patient_id,
            'exam_type' : self.exam_type.value if self.exam_type else None,
            'exam_code' : self.exam_code,
            'patient_federation_id' : self.patient_federation_id,
            'envoi_email' : self.envoi_email,
            'sended_at' : self.sended_at,
        }


class Prescriptions(db.Model):
    __tablename__ = 'prescriptions'

    id = db.Column(db.Integer(), primary_key=True)

    NameDoctor = db.Column(db.String(100), nullable=True, server_default='')
    OrdreDoctor = db.Column(db.String(100), nullable=True, server_default='')
    Sequence = db.Column(db.String(50), unique=True, index=True)
    Create_date = db.Column(db.DateTime, default=datetime.datetime.now)
    Demande_devis = db.Column(db.Boolean, default=False)
    Description = db.Column(db.String(500), nullable=True, default='')

    # Clé étrangère vers Patient
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)

    # Relation Many-to-One : une prescription appartient à un patient
    patient = db.relationship('Patients', back_populates='prescriptions')

    image_data = db.Column(db.LargeBinary, nullable=True)
    image_mimetype = db.Column(db.String(50), nullable=True)

    def to_dict(self):
        return {
            'id' : self.id,
            'NameDoctor' : self.NameDoctor,
            'OrdreDoctor' : self.OrdreDoctor,
            'Sequence' : self.Sequence,
            'Create_date' : self.Create_date,
            'demande_devis' : self.Demande_devis,
            'Description': self.Description,
            'patient_id': self.patient_id
        }
    
@event.listens_for(Prescriptions, "before_insert")
def set_prescription_sequence(mapper, connect, target):
    if not target.Sequence:
        target.Sequence = generate_sequence("PRES", Prescriptions)


class Configuration(db.Model):
    __tablename__ = 'configurations'

    id = db.Column(db.Integer(), primary_key=True)
    name = db.Column(db.String(100), nullable=True)
    exp_result = db.Column(db.Integer(), nullable=True)
    start_date_facture_verification = db.Column(db.String(100), nullable=True)
    CreatedAt = db.Column(db.DateTime(), nullable=True)
    UpdatedAt = db.Column(db.DateTime(), nullable=True)
    CreatedBy = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'))
    UpdatedBy = db.Column(db.Integer, db.ForeignKey('users.id',  ondelete='CASCADE'))

    def to_dict(self):
        return {
            "id" : self.id,
            "name" :  self.name,
            "exp_result" : self.exp_result,
            "start_date_facture_verification" : self.start_date_facture_verification,
            "CreatedAt" : self.CreatedAt,
            "UpdateAt" : self.UpdatedAt,
            "CreatedBy" : self.CreatedBy,
            "UpdatedBy" : self.UpdatedBy
        }


class SuggestionBox(db.Model):
    __tablename__ = 'suggestion_box'

    id = db.Column(db.Integer, primary_key=True)
    content = db.Column(db.Text, nullable=True)
    note = db.Column(db.Float, nullable=True)
    CreatedAt = db.Column(db.DateTime(), nullable=True)
    UpdatedAt = db.Column(db.DateTime(), nullable=True)
    CreatedBy = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=True)
    UpdatedBy = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=True)

    def to_dict(self):
        return {
            "id": self.id,
            "content": self.content,
            "Note" : self.note,
            "CreatedAt": self.CreatedAt,
            "UpdatedAt": self.UpdatedAt,
            "CreatedBy": self.CreatedBy,
            "UpdatedBy": self.UpdatedBy
        }

    def __repr__(self):
        return f"<SuggestionBox {self.id}>"
    

class SavePatients(db.Model):
    __tablename__ = 'save_patients'

    id = db.Column(db.Integer, primary_key=True)
    nom = db.Column(db.String(100), nullable=True)
    prenom = db.Column(db.String(100), nullable=True)
    description = db.Column(db.String(500), nullable=True)
    # Clé étrangère vers Patient
    patient_id = db.Column(db.Integer, db.ForeignKey('patients.id', ondelete='CASCADE'), nullable=False)

    # Relation Many-to-One : une prescription appartient à un patient
    patient = db.relationship('Patients', back_populates='save_patients')

    image_data = db.Column(db.LargeBinary, nullable=True)
    image_mimetype = db.Column(db.String(50), nullable=True)
    Create_date = db.Column(db.DateTime, default=datetime.datetime.now)

    validated = db.Column(db.Boolean,  default=False)
    validated_by = db.Column(db.Integer, db.ForeignKey('users.id', ondelete='CASCADE'), nullable=True)
    validated_at = db.Column(db.DateTime, nullable=True)


    def to_dict(self):
        return {
            'id' : self.id,
            'nom' : self.nom,
            'prenom' : self.prenom,
            'description' : self.description,
            'Create_date' : self.Create_date,
            'patient_id': self.patient_id,
            'validated_by' : self.validated_by,
            'validated_at' : self.validated_at,
            'validated' : self.validated
        }



