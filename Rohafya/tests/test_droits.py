"""Droits des comptes personnels (impression, partage des résultats) et e-mail inconnu à la connexion.

SQLite en mémoire. Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_droits.py
"""
import datetime
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from flask_jwt_extended import create_access_token
from sqlalchemy import JSON, null

import Rohafya
from Rohafya import db, jwt, login_manager

app = Flask("Rohafya", root_path=os.path.dirname(Rohafya.__file__))
app.config.update(
    SQLALCHEMY_DATABASE_URI="sqlite://",
    SECRET_KEY="test-secret-test-secret-test-secret",
    JWT_SECRET_KEY="test-jwt-test-jwt-test-jwt-test-jwt",
    JWT_TOKEN_LOCATION=["headers"],
    USER_APP_NAME="ROHAFYA",
    USER_ENABLE_EMAIL=True,
    USER_ENABLE_USERNAME=True,
    USER_EMAIL_SENDER_EMAIL="noreply@test",
    MAIL_SUPPRESS_SEND=True,
    TESTING=True,
    USER_LOGIN_URL="/user/login",
)
db.init_app(app)
jwt.init_app(app)
login_manager.init_app(app)

from Rohafya.accounts.models import Doctors, Patients, User, UserActivity  # noqa: E402
from Rohafya.custom.customization import CustomUserManager  # noqa: E402

CustomUserManager(app, db, User)


@jwt.user_identity_loader
def _identity(user):
    return user


@jwt.user_lookup_loader
def _lookup(_header, data):
    return db.session.get(User, int(data["sub"]))


from Rohafya import tryton  # noqa: E402
from Rohafya.tryton_absent import desactiver_tryton  # noqa: E402

desactiver_tryton(tryton)

from Rohafya.saas import services  # noqa: E402
from Rohafya.saas.account_routes import saas_account  # noqa: E402
from Rohafya.saas.super_admin_routes import super_admin  # noqa: E402
from Rohafya.send_results.send_results import send_result  # noqa: E402

for bp in (saas_account, super_admin, send_result):
    app.register_blueprint(bp)

User.has_permission = lambda self, code: True
UserActivity.__table__.c.route.type = JSON()
services.send_mail = lambda *args, **kwargs: True

ok_count = 0


def check(condition, label):
    global ok_count
    if not condition:
        raise AssertionError(label)
    ok_count += 1
    print("  ✔", label)


with app.app_context():
    db.create_all()
    client = app.test_client()

    def auth(user):
        return {"Authorization": "Bearer " + create_access_token(identity=str(user.id))}

    admin = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    patient_user = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    patient = services.ensure_patient_profile(patient_user)
    doctors = []
    for i in range(3):
        doctor_user = services.create_user(f"doc{i}@example.com", "Doc", str(i), "Doctor")
        doctor = Doctors(user_id=doctor_user.id, DoctorEmail=doctor_user.email, DoctorPhone=f"69000000{i}", DoctorPhone2=null())
        db.session.add(doctor)
        doctors.append(doctor)
    db.session.commit()

    def me():
        return client.get("/saas/me", headers=auth(patient_user)).get_json()["rights"]

    def share(doctor, code):
        return client.post("/send_result/", json={"doctor_id": doctor.id, "exam_type": "Laboratoire", "exam_code": code}, headers=auth(patient_user))

    rights_url = f"/saas/super/users/{patient_user.id}/rights"

    print("1. Sans configuration, tout est disponible")
    rights = me()
    check(rights["print"]["allowed"] and not rights["print"]["configured"], "impression disponible")
    check(rights["share"]["allowed"] and not rights["share"]["configured"], "partage disponible")
    check(share(doctors[0], "LAB-1").status_code == 200, "partage accepté")

    print("2. Partage limité à un nombre de fois")
    r = client.put(rights_url, json={"share_limit": 1}, headers=auth(admin))
    check(r.status_code == 200 and r.get_json()["effective"]["share"]["remaining"] == 1, "le compteur part de la configuration")
    check(share(doctors[1], "LAB-2").status_code == 200, "1er partage autorisé")
    r = share(doctors[2], "LAB-3")
    check(r.status_code == 403 and "partage" in r.get_json()["message"], "partage au-delà de la limite refusé (403, message)")
    rights = me()
    check(rights["share"]["configured"] and not rights["share"]["allowed"] and rights["share"]["used"] == 1, "le front reçoit le partage épuisé")
    r = client.put(rights_url, json={"reset_share_count": True}, headers=auth(admin))
    check(r.get_json()["effective"]["share"]["remaining"] == 1, "compteur remis à zéro")
    client.put(rights_url, json={"share_limit": 0}, headers=auth(admin))
    check(not me()["share"]["allowed"], "0 partage : partage bloqué")

    print("3. Impression pendant une période précise")
    soon = (services.now() + datetime.timedelta(days=2)).isoformat()
    later = (services.now() + datetime.timedelta(days=5)).isoformat()
    past = (services.now() - datetime.timedelta(days=1)).isoformat()
    r = client.put(rights_url, json={"print_mode": "window", "print_until": later}, headers=auth(admin))
    check(r.status_code == 200 and me()["print"]["allowed"], "impression autorisée jusqu'à la date de fin")
    client.put(rights_url, json={"print_mode": "window", "print_from": soon, "print_until": later}, headers=auth(admin))
    rights = me()
    check(not rights["print"]["allowed"] and "à partir du" in rights["print"]["message"], "pas encore ouverte : bouton inactif avec la date")
    r = client.put(rights_url, json={"print_mode": "window", "print_from": later, "print_until": soon}, headers=auth(admin))
    check(r.status_code == 400, "période incohérente refusée")
    r = client.put(rights_url, json={"print_mode": "window"}, headers=auth(admin))
    check(r.status_code == 400 and "fin" in r.get_json()["message"], "date de fin exigée")
    client.put(rights_url, json={"print_mode": "window", "print_from": past, "print_until": past}, headers=auth(admin))
    check(not me()["print"]["allowed"], "période terminée : impression indisponible")
    client.put(rights_url, json={"print_mode": "blocked"}, headers=auth(admin))
    check(not me()["print"]["allowed"], "impression bloquée")

    print("4. Console du super-administrateur")
    users = client.get("/saas/super/users", headers=auth(admin)).get_json()["items"]
    row = next(u for u in users if u["id"] == patient_user.id)
    check("Impression bloquée" in row["rights"] and "Partages 0/0" in row["rights"], "résumé des droits dans la liste des comptes")
    check(client.put(rights_url, json={"share_limit": 2}, headers=auth(patient_user)).status_code == 403, "réservé au super-administrateur")
    r = client.delete(rights_url, headers=auth(admin))
    check(r.status_code == 200 and not r.get_json()["configured"], "configuration supprimée")
    rights = me()
    check(rights["print"]["allowed"] and rights["share"]["allowed"], "tout redevient disponible")

    print("5. Connexion : adresse e-mail inconnue")
    r = client.post("/saas/auth/otp/request", json={"email": "inconnu@example.com"})
    check(r.get_json()["account_exists"] is False and "Aucun compte" in r.get_json()["message"], "code par e-mail : adresse inconnue signalée")
    r = client.post("/saas/auth/otp/request", json={"email": "aicha@example.com"})
    check(r.get_json()["account_exists"] is True, "code par e-mail : compte existant")
    r = client.post("/user/login", json={"username": "inconnu@example.com", "password": "secret123", "remember_me": True})
    check(r.status_code == 401 and "Aucun compte n'existe avec l'adresse e-mail inconnu@example.com" in r.get_json()["message"], "mot de passe : e-mail inconnu signalé")
    r = client.post("/user/login", json={"username": "aicha@example.com", "password": "mauvais", "remember_me": True})
    check(r.status_code == 401 and r.get_json()["message"].startswith("Mot de passe incorrect"), "mot de passe : compte existant, mot de passe erroné")

print(f"\n{ok_count} contrôles réussis.")
