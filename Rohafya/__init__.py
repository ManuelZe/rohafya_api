import os
import re
import json
from flask import jsonify, Flask, session, g, request, render_template_string
import datetime
from itsdangerous import URLSafeTimedSerializer
from flask_sqlalchemy import SQLAlchemy
from flask_tryton import Tryton
from flask_login import LoginManager
from flask_cors import CORS
from flask_mail import Mail
from flask_migrate import Migrate
from datetime import timedelta

from flask_jwt_extended import create_access_token
from flask_jwt_extended import get_jwt
from flask_jwt_extended import get_jwt_identity
from flask_jwt_extended import jwt_required
from flask_jwt_extended import JWTManager
from flask_jwt_extended import set_access_cookies
from flask_jwt_extended import unset_jwt_cookies, verify_jwt_in_request
from sqlalchemy.orm.attributes import flag_modified
from werkzeug.middleware.proxy_fix import ProxyFix
import json
import requests
import logging
from .env_prod import ConfigProdClass
# from .env_dev import ConfigEnvClass
from sqlalchemy import select, text

# Initialisation des extensions en dehors de create_app
db = SQLAlchemy()
migrate = Migrate()
login_manager = LoginManager()
tryton = Tryton()
jwt = JWTManager()


class _FusionSlashs:
    """« //patient/… » → « /patient/… » : une URL de base du front terminée par « / » produit des
    chemins avec des « / » répétés, qui ne correspondent à aucune route (404, et échec du
    pré-contrôle CORS)."""

    def __init__(self, wsgi_app):
        self.wsgi_app = wsgi_app

    def __call__(self, environ, start_response):
        chemin = environ.get("PATH_INFO", "")
        if "//" in chemin:
            environ["PATH_INFO"] = re.sub("/{2,}", "/", chemin)
        return self.wsgi_app(environ, start_response)


def create_app():
    app = Flask(__name__)

    # Development environment
    # app.config.from_object(ConfigEnvClass)
    # CORS(app, origins=["http://172.16.200.35:5174", "http://172.16.200.35:5173", "http://172.16.200.37:3000", "http://localhost:3000", "http://172.30.50.52:5173"], supports_credentials=True, allow_headers=["Content-Type", "Authorization"])

    # Production environment
    app.config.from_object(ConfigProdClass)
    
    # Origines sans slash final ; les fronts autorisés sont listés dans saas/constants.py.
    from .saas.constants import front_origins
    CORS(
        app, 
        origins=[*front_origins(), "http://localhost:8081"], 
        supports_credentials=True, 
        allow_headers=["Content-Type", "Authorization"]
    )

    app.config['TRYTON_CONFIG'] = os.environ.get('TRYTON_CONFIG', '/home/gnuhealth/gnuhealth/tryton/server/config/trytond.conf')
    app.config['TRYTON_DATABASE'] = os.environ.get('TRYTON_DATABASE', 'pdmd_sante')
    app.config['TRYTON_USER'] = int(os.environ.get('TRYTON_USER', '0'))
    # Lecture directe de GNU Health : active par défaut. ROHAFYA_GNUHEALTH=false pour un déploiement
    # sans GNU Health (conteneur sur un VPS) : les données arrivent alors par le connecteur.
    app.config['ROHAFYA_GNUHEALTH'] = os.environ.get('ROHAFYA_GNUHEALTH', 'true').strip().lower() in ('1', 'true', 'oui', 'yes')
    app.config['CORS_HEADERS'] = 'Content-Type'
    app.config['MAX_CONTENT_LENGTH'] = 16 * 1000 * 1000
    app.config['UPLOAD_FOLDER'] = os.environ.get('ROHAFYA_UPLOAD_FOLDER', 'files/')

    manquants = [nom for nom, cle in (("ROHAFYA_SECRET_KEY", "SECRET_KEY"), ("ROHAFYA_JWT_SECRET_KEY", "JWT_SECRET_KEY"),
                                      ("ROHAFYA_DATABASE_URL", "SQLALCHEMY_DATABASE_URI"), ("ROHAFYA_PASSWORD_SALT", "SECURITY_PASSWORD_SALT"))
                 if not app.config.get(cle)]
    if manquants:
        raise RuntimeError(f"Variables d'environnement manquantes : {', '.join(manquants)} (voir docs/DEPLOIEMENT_DOCKER.md).")

    # Derrière un reverse proxy (Nginx Proxy Manager…) : ROHAFYA_PROXIES = nombre de proxys de confiance,
    # pour retrouver l'adresse IP et le schéma (https) du client.
    proxies = int(os.environ.get('ROHAFYA_PROXIES', '0'))
    if proxies:
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=proxies, x_proto=proxies, x_host=proxies)
    app.wsgi_app = _FusionSlashs(app.wsgi_app)

    # Initialisation des extensions avec l'application Flask
    db.init_app(app)
    migrate.init_app(app, db)
    login_manager.init_app(app)
    if app.config['ROHAFYA_GNUHEALTH']:
        tryton.init_app(app)
    else:
        from .tryton_absent import desactiver_tryton
        desactiver_tryton(tryton)
    jwt.init_app(app)

    # Charger les modèles et l'instance de User
    from .accounts.models import User
    from .custom.customization import CustomUserManager
    user_manager = CustomUserManager(app, db, User)

    # Configurer le gestionnaire de connexion
    login_manager.login_view = "user.login"
    login_manager.logout_view = "user.logout"

    @login_manager.user_loader
    def load_user(user_id):
        try:
            return User.query.get(user_id)
        except ValueError:
            return None
    
    @jwt.user_identity_loader
    def user_identity_lookup(user):
        return user
    
    @jwt.user_lookup_loader
    def user_lookup_callback(_jwt_header, jwt_data):
        identity = jwt_data["sub"]
        return User.query.filter_by(id=identity).one_or_none()

    # Tables du mode SaaS (nouvelles tables uniquement : créées ci-dessous par create_all).
    from .saas import models as saas_models  # noqa: F401

    # Les anciens noms EDEN_* sont encore lus pour ne pas casser les conteneurs existants.
    app.config['ROHAFYA_FRONT_URL'] = os.environ.get('ROHAFYA_FRONT_URL') or os.environ.get('EDEN_FRONT_URL') or "https://rohafya.com"
    # Import des résultats par PDF : fermé tant que ROHAFYA_PDF_IMPORT_ENABLED ne vaut pas « true » (disponible prochainement).
    app.config['ROHAFYA_PDF_IMPORT_ENABLED'] = (os.environ.get('ROHAFYA_PDF_IMPORT_ENABLED') or os.environ.get('EDEN_PDF_IMPORT_ENABLED', '')).strip().lower() in ('1', 'true', 'oui', 'yes')

    with app.app_context():
        db.create_all()
        # Prescriptions et pré-enregistrements de médecins : patient_id facultatif sur les bases existantes.
        from .saas.submissions import relax_patient_columns
        relax_patient_columns()

    # Enregistrement des blueprints
    from .accounts.users import userd
    from .accounts.roles import roles
    from .doctors.gnudoctors import doctor_gnu
    from .accounts.requests2 import requete
    from .doctors.charge_doctors import gnu_doctors
    from .doctors.calculs_commissions import doctor_com
    from .blog.blog import article
    from .accounts.user_activity import activity
    from .patients.charge_patients import charges_patients
    from .patients.exploration.exploration import exploration
    from .patients.laboratoire.laboratoire import laboratoire
    from .patients.imagerie.imagerie import imagerie
    from .patients.prescriptions import prescriptions
    from .notifications.general import general_notification
    from .notifications.etiquettes import etiquettes
    from .notifications.types import not_type
    from .send_results.send_results import send_result
    from .send_results.results import results
    from .configurations.configs import configuration
    from .patients.suggestions import suggestions_bp
    from .patients.save_patients import save_patients
    from .permissions.permissions_api import permissions
    from .devis.devis import devis
    from .saas.account_routes import saas_account
    from .saas.tenant_admin_routes import tenant_admin
    from .saas.super_admin_routes import super_admin
    from .saas.ingest_routes import ingest as saas_ingest, fhir as saas_fhir
    from .saas.cli import saas_cli
    from .saas.pdf_routes import pdf_imports
    from .saas.submission_routes import submissions_admin

    app.register_blueprint(requete)
    app.register_blueprint(doctor_gnu)
    app.register_blueprint(roles)
    app.register_blueprint(userd)
    app.register_blueprint(gnu_doctors)
    app.register_blueprint(doctor_com)
    app.register_blueprint(article)
    app.register_blueprint(activity)
    app.register_blueprint(charges_patients)
    app.register_blueprint(exploration)
    app.register_blueprint(laboratoire)
    app.register_blueprint(imagerie)
    app.register_blueprint(general_notification)
    app.register_blueprint(etiquettes)
    app.register_blueprint(not_type)
    app.register_blueprint(send_result)
    app.register_blueprint(results)
    app.register_blueprint(prescriptions)
    app.register_blueprint(configuration)
    app.register_blueprint(suggestions_bp)
    app.register_blueprint(save_patients)
    app.register_blueprint(permissions)
    app.register_blueprint(devis)
    app.register_blueprint(saas_account)
    app.register_blueprint(tenant_admin)
    app.register_blueprint(super_admin)
    app.register_blueprint(saas_ingest)
    app.register_blueprint(saas_fhir)
    app.register_blueprint(pdf_imports)
    app.register_blueprint(submissions_admin)
    app.cli.add_command(saas_cli)

    from .accounts.models import UserActivity
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    @app.before_request
    def log_user_activity():
        """
        Logue l'activité de l'utilisateur avant chaque requête en utilisant
        l'adresse IP et les informations du navigateur.
        """
        # Ne pas intercepter OPTIONS afin d'autoriser la génération des entêtes CORS
        if request.method == "OPTIONS" or request.path in ("/heartbeat", "/sante"):
            return
        # Les envois machine à machine des établissements ne sont pas des visites d'utilisateurs.
        if request.path.startswith(("/ingest/", "/fhir/")):
            return

        try:
            ip = request.headers.get("X-Forwarded-For", request.remote_addr)
            ua = request.user_agent

            g.user_info = {}
            
            if ip not in ('127.0.0.1', 'localhost'):
                try:
                    response = requests.get(f"http://ip-api.com/json/{ip}", timeout=2)
                    if response.status_code == 200:
                        response_json = response.json()
                        g.user_info = {
                            "country": response_json.get("country", "Unknown"),
                            "city": response_json.get("city", "Unknown"),
                        }
                except Exception as e:
                    logging.error(f"Erreur de géolocalisation pour l'IP {ip}: {e}")
                    g.user_info = {
                        "country": "Unknown",
                        "city": "Unknown",
                    }
            else:
                logging.info(f"IP locale détectée: {ip}")
                g.user_info = {
                    "country": "Local",
                    "city": "Local",
                }

            last_activity = UserActivity.query.filter_by(ip=ip).first()

            if last_activity:
                route = last_activity.route or []
                route.append({
                    "access_date": datetime.datetime.now().isoformat(),
                    "route": request.path,
                    "method": request.method
                })
                last_activity.modified_at = datetime.datetime.now()
                flag_modified(last_activity, "route")
                db.session.commit()
            else:
                route = [{
                    "access_date": datetime.datetime.now().isoformat(),
                    "route": request.path,
                    "method": request.method
                }]

                activity = {
                    "ip": ip,
                    "country": g.user_info.get("country", "Unknown"),
                    "city": g.user_info.get("city", "Unknown"),
                    "browser": ua.browser,
                    "platform": ua.platform,
                    "user_agent": ua.string,
                    "route": route,
                    "method": request.method,
                }
            
                db.session.add(UserActivity(**activity))
                db.session.commit()
        except Exception as e:
            db.session.rollback()
            logging.error(f"Erreur lors de l'enregistrement de l'activité utilisateur : {e}")

    @app.route("/heartbeat", methods=["POST"])
    def heartbeat():
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        last_activity = UserActivity.query.filter_by(
            ip=ip
        ).order_by(UserActivity.id.desc()).first()

        if last_activity:
            last_activity.last_seen = datetime.datetime.now().isoformat()
            db.session.commit()
        return {"status": "ok"}

    @app.route("/device-info", methods=["POST"])
    def device_info():
        data = request.json
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        last_activity = UserActivity.query.filter_by(
            ip=ip
        ).order_by(UserActivity.id.desc()).first()
        if last_activity:
            last_activity.device_info = data
            db.session.commit()

        return {"status": "saved"}

    @app.route('/hello')
    def hello():
        return 'Hello, World!'

    @app.route('/sante')
    def sante():
        """État de l'API pour les sondes (healthcheck Docker, supervision) ; non journalisé."""
        try:
            db.session.execute(text("SELECT 1"))
            base = "ok"
        except Exception as exc:
            logging.error(f"Base de données injoignable : {exc}")
            db.session.rollback()
            base = "injoignable"
        etat = {
            "statut": "ok" if base == "ok" else "degrade",
            "base": base,
            "gnuhealth": "actif" if app.config['ROHAFYA_GNUHEALTH'] else "desactive",
        }
        return jsonify(etat), 200 if base == "ok" else 503

    @app.route('/routes')
    def list_routes():
        output = [f"{rule.endpoint}: {rule} [{', '.join(rule.methods)}]" for rule in app.url_map.iter_rules()]
        return jsonify(output)

    @app.route("/protected", methods=["GET"])
    @jwt_required()
    def protected():
        current_user = get_jwt_identity()
        return jsonify({"logged_in_as": current_user})

    @app.route("/whoami", methods=["GET"])
    @jwt_required()
    def who_am_i():
        current_user = get_jwt_identity()
        user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()
        if user:
            return jsonify(user.to_dict())
        else:
            return jsonify({"messsage": "User not found"})

    return app