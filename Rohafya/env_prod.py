import os
import json
from flask import jsonify, Flask, session, g, request, render_template_string
import datetime
from itsdangerous import URLSafeTimedSerializer
from flask_sqlalchemy import SQLAlchemy
from flask_tryton import Tryton
from flask_login import LoginManager
from flask_cors import CORS
from datetime import timedelta
from flask_jwt_extended import JWTManager
import json

class ConfigProdClass(object):
    # Configuration spécifique à l'environnement de production
    DEBUG = False
    TESTING = False
    """ Flask application config """


    # Flask settings
    SECRET_KEY = '21481decfe9c4cdfg61fdbe11b22b82gzerf5zefzef498ze7fze4f6ze48f1zc8z4fz98ef'
    JWT_SECRET_KEY = 'zgfezef49ze4f1ze4f6zrg4h4tkit9yjh4f64uis6rt84s64ryh198tu4se98yrikd6yfh84fg984kjd6jh84utth9r8e4h1s6frg4hjs6t4hjdry64js987j4sr984jhs'

    # Flask-SQLAlchemy settings
    SQLALCHEMY_DATABASE_URI = 'postgresql://gnuhealth:toor@localhost:5432/gnu_doctor2'    # File-based SQL database
    SQLALCHEMY_TRACK_MODIFICATIONS = True    # Avoids SQLAlchemy warning

    # Flask-Mail SMTP server settings
    MAIL_SERVER = 'smtp.office365.com'
    MAIL_PORT = 587
    MAIL_USE_SSL = False
    MAIL_USE_TLS = True
    MAIL_USERNAME = 'eden.no-reply@pdmdsante.com'
    MAIL_PASSWORD = '9C@}xeG79eYe!7'
    MAIL_DEFAULT_SENDER = '"PDMD - EDEN" <eden.no-reply@pdmdsante.com>'

    # Flask-User settings
    USER_APP_NAME = "Doctors And Patients App"      # Shown in and email templates and page footers
    USER_ENABLE_EMAIL = True        # Enable email authentication
    USER_ENABLE_USERNAME = True    # Disable username authentication
    USER_EMAIL_SENDER_NAME = USER_APP_NAME
    USER_EMAIL_SENDER_EMAIL = "verified@pdmdsante.com"
    SECURITY_PASSWORD_SALT = "bvKQv321v324fr56v46sf4v6"
    USER_LOGIN_URL = '/user/login'
    USER_LOGOUT_URL = '/user/logout'
    USER_ENABLE_CONFIRM_EMAIL = False
    USER_REGISTER_URL = '/signup'
    #USER_CONFIRM_EMAIL_URL = '/confirm/<token>'
    #USER_AUTO_LOGIN_AFTER_REGISTER = True
    USER_AUTO_LOGIN = True
    USER_AUTO_LOGIN_AT_LOGIN = True
    USER_RESET_PASSWORD_TEMPLATE = ''
    USER_CHANGE_PASSWORD_TEMPLATE = ''
    USER_CHANGE_USERNAME_TEMPLATE = ''
    USER_EDIT_USER_PROFILE_TEMPLATE = ''
    USER_FORGOT_PASSWORD_TEMPLATE = ''
    USER_INVITE_USER_TEMPLATE = ''
    USER_LOGIN_TEMPLATE = ''
    USER_LOGIN_AUTH0_TEMPLATE = ''
    USER_MANAGE_EMAILS_TEMPLATE = ''
    #USER_REGISTER_TEMPLATE = ''
    USER_RESEND_CONFIRM_EMAIL_TEMPLATE = ''

    #USER_CONFIRM_EMAIL_TEMPLATE = ''
    USER_INVITE_USER_EMAIL_TEMPLATE = ''
    USER_PASSWORD_CHANGED_EMAIL_TEMPLATE = ''
    #USER_REGISTERED_EMAIL_TEMPLATE = ''
    USER_RESET_PASSWORD_EMAIL_TEMPLATE = ''
    USER_UNAUTHENTICATED_ENDPOINT = 'user.login'

    USER_SEND_REGISTERED_EMAIL = False
    USER_AFTER_LOGIN_ENDPOINT = ''

    REMEMBER_COOKIE_SECURE = True
    SESSION_COOKIE_SAMESITE = 'None'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = True
    SESSION_PERMANENT = True
    SESSION_USE_SIGNER = True
    SESSION_REFRESH_EACH_REQUEST = True
    PERMANENT_SESSION_LIFETIME = timedelta(days=7)
    REMEMBER_COOKIE_DURATION = timedelta(days=5)

    JWT_COOKIE_SECURE = True
    JWT_COOKIE_CSRF_PROTECT = False
    JWT_TOKEN_LOCATION = ["headers"]
    JWT_ACCESS_TOKEN_EXPIRES = timedelta(days=7)
    JWT_COOKIE_SAMESITE = "None"
    JWT_ACCESS_COOKIE_PATH = '/'
    JWT_SESSION_COOKIE = True





