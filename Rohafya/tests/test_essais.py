"""Langue des comptes et demandes d'essai des établissements (site de présentation).

SQLite en mémoire. Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_essais.py
"""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from flask import Flask
from flask_jwt_extended import create_access_token
from sqlalchemy import JSON, null, select

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
    ROHAFYA_FRONT_URL="https://rohafya.test",
)
db.init_app(app)
jwt.init_app(app)
login_manager.init_app(app)

from Rohafya.accounts.models import Doctors, Notifications, Patients, User, UserActivity  # noqa: E402
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

from Rohafya.notifications.general import general_notification  # noqa: E402
from Rohafya.saas import services  # noqa: E402
from Rohafya.saas.account_routes import saas_account  # noqa: E402
from Rohafya.saas.models import NotificationCampaign, PatientLink, PractitionerLink, Tenant, TenantMember  # noqa: E402
from Rohafya.saas.super_admin_routes import super_admin  # noqa: E402
from Rohafya.saas.tenant_admin_routes import tenant_admin  # noqa: E402

for bp in (saas_account, super_admin, tenant_admin, general_notification):
    app.register_blueprint(bp)

User.has_permission = lambda self, code: True
UserActivity.__table__.c.route.type = JSON()
SENT = []


def fake_send(to, subject, template, **context):
    SENT.append({"to": to, "subject": subject, "template": template, **context})
    return True


services.send_mail = fake_send
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

    root = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    patient = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    services.ensure_patient_profile(patient)
    db.session.commit()

    print("1. Langue de l'interface, par compte")
    check(client.get("/saas/me", headers=auth(patient)).get_json()["locale"] == "fr", "français par défaut")
    r = client.put("/saas/me/preferences", json={"locale": "en"}, headers=auth(patient))
    check(r.status_code == 200 and r.get_json()["locale"] == "en", "anglais choisi")
    check(client.get("/saas/me", headers=auth(patient)).get_json()["locale"] == "en", "retrouvé sur un autre appareil (GET /saas/me)")
    check(client.put("/saas/me/preferences", json={"locale": "de"}, headers=auth(patient)).status_code == 400, "langue inconnue refusée")
    check(client.put("/saas/me/preferences", json={"locale": "en"}).status_code == 401, "connexion exigée")

    print("2. Demande d'essai depuis le site")
    form = {
        "contact_name": "Dr Ekané", "job_title": "Biologiste", "establishment": "Laboratoire Horizon", "city": "Bafoussam",
        "phone": "+237 690 00 00 00", "email": "Direction@Horizon.cm", "kind": "laboratoire", "monthly_volume": "500_2000",
        "software": "LabSoft", "pain_points": "Résultats non retirés\nAppels à l'accueil", "preferred_slot": "Mardi matin",
        "consent": True, "locale": "en",
    }
    SENT.clear()
    r = client.post("/saas/public/trial-requests", json=form)
    check(r.status_code == 201 and "24 heures" in r.get_json()["message"], "demande enregistrée, sans compte")
    check(len(SENT) == 1 and SENT[0]["to"] == "root@rohafya.test" and SENT[0]["template"] == "emails/demande_essai.html", "super-administrateur prévenu par e-mail")
    notes = client.get("/saas/me/notifications", headers=auth(root)).get_json()
    check(notes and notes[0]["title"] == "Demande d'essai · Laboratoire Horizon", "… et dans son interface")
    check(client.post("/saas/public/trial-requests", json={**form, "consent": False}).status_code == 400, "accord obligatoire")
    check(client.post("/saas/public/trial-requests", json={**form, "kind": "pharmacie"}).status_code == 400, "type inconnu refusé")
    r = client.post("/saas/public/trial-requests", json={**form, "email": "pas-un-email"})
    check(r.status_code == 400 and "e-mail" in r.get_json()["message"], "e-mail invalide refusé")
    r = client.post("/saas/public/trial-requests", json={**form, "establishment": " "})
    check(r.status_code == 400 and "établissement" in r.get_json()["message"], "champ obligatoire nommé dans le message")
    before = client.get("/saas/super/trial-requests", headers=auth(root)).get_json()["total"]
    r = client.post("/saas/public/trial-requests", json={**form, "website": "http://spam"})
    check(r.status_code == 201 and client.get("/saas/super/trial-requests", headers=auth(root)).get_json()["total"] == before, "robot (champ piège) : rien n'est enregistré")
    client.post("/saas/public/trial-requests", json=form)
    client.post("/saas/public/trial-requests", json=form)
    check(client.post("/saas/public/trial-requests", json=form).status_code == 429, "trois demandes par adresse et par jour")

    print("3. Console du super-administrateur")
    page = client.get("/saas/super/trial-requests", headers=auth(root)).get_json()
    first = page["items"][0]
    check(page["total"] == 3 and first["email"] == "direction@horizon.cm" and first["locale"] == "en", "liste des demandes")
    r = client.put(f"/saas/super/trial-requests/{first['id']}", json={"status": "contactee", "note": "Visio jeudi"}, headers=auth(root))
    check(r.status_code == 200 and r.get_json()["status"] == "contactee", "suivi de la demande")
    check(client.get("/saas/super/trial-requests?status=contactee", headers=auth(root)).get_json()["total"] == 1, "filtre par statut")
    r = client.put(f"/saas/super/trial-requests/{first['id']}", json={"note": "x" * 2001}, headers=auth(root))
    check(r.status_code == 400 and "2000" in r.get_json()["message"], "note limitée à 2000 caractères")
    check(client.get("/saas/super/trial-requests", headers=auth(patient)).status_code == 403, "réservé au super-administrateur")

print(f"\n{ok_count} contrôles réussis.")
