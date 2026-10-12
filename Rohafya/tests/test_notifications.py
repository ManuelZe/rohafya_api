"""Notifications envoyées depuis les consoles (super-administrateur, administrateur d'établissement).

SQLite en mémoire. Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_notifications.py
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

    def patient(email, tenant=None, status="active", ref=None, federation=None):
        user = services.create_user(email, email.split("@")[0].title(), "Test", "Patient")
        profile = services.ensure_patient_profile(user)
        if federation:
            profile.PatientFederationID = federation
        if tenant is not None:
            db.session.add(PatientLink(patient_id=profile.id, tenant_id=tenant.id, local_ref=ref or email, status=status))
        db.session.commit()
        return user

    def doctor(email, tenant=None, matricule="ROHAFYA-D-1"):
        user = services.create_user(email, "Doc", email.split("@")[0], "Doctor")
        profile = Doctors(user_id=user.id, DoctorEmail=email, DoctorFederationID=matricule, DoctorPhone=null(), DoctorPhone2=null())
        db.session.add(profile)
        db.session.flush()
        if tenant is not None:
            db.session.add(PractitionerLink(tenant_id=tenant.id, doctor_id=profile.id))
        db.session.commit()
        return user

    root = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    labo = Tenant(slug="labo-a", name="Labo A", source_type="api", settings={})
    autre = Tenant(slug="labo-b", name="Labo B", source_type="api", settings={})
    db.session.add_all([labo, autre])
    db.session.commit()
    admin_a = services.create_user("admin@labo-a.test", "Ada", "Admin", "EstablishmentAdmin")
    admin_b = services.create_user("admin@labo-b.test", "Bob", "Admin", "EstablishmentAdmin")
    db.session.add_all([TenantMember(tenant_id=labo.id, user_id=admin_a.id), TenantMember(tenant_id=autre.id, user_id=admin_b.id)])
    db.session.commit()

    p1 = patient("aicha@example.com", labo)
    p2 = patient("brice@example.com", labo)
    patient("pending@example.com", labo, status="pending")
    inactive = patient("inactive@example.com", labo)
    inactive.active = False
    pb = patient("autre@example.com", autre)
    d1 = doctor("ekane@example.com", labo, "ROHAFYA-D-1")
    doctor("owona@example.com", autre, "ROHAFYA-D-2")
    gnu = services.default_gnuhealth_tenant()
    legacy = patient("legacy@example.com", federation="GNU-0042")
    db.session.commit()

    def mine(user):
        return client.get("/saas/me/notifications", headers=auth(user)).get_json()

    print("1. Super-administrateur")
    SENT.clear()
    r = client.post(f"/saas/super/tenants/{labo.id}/notifications", json={"audience": "admins", "user_ids": [admin_a.id], "title": "Maintenance", "message": "Coupure ce soir\nà 22 h."}, headers=auth(root))
    check(r.status_code == 201 and r.get_json()["recipients_count"] == 1, "un administrateur d'établissement précis")
    check(len(SENT) == 1 and SENT[0]["to"] == "admin@labo-a.test" and SENT[0]["template"] == "emails/notification.html", "… prévenu par e-mail")
    check(SENT[0]["open_url"] == "https://rohafya.test/admin/notifications", "… avec un lien vers la console")
    notes = mine(admin_a)
    check(len(notes) == 1 and notes[0]["title"] == "ROHAFYA · Maintenance" and notes[0]["message"] == "Coupure ce soir\nà 22 h.", "… et dans son interface")
    SENT.clear()
    r = client.post(f"/saas/super/tenants/{labo.id}/notifications", json={"audience": "patients", "title": "Nouveaux horaires", "message": "Ouvert le samedi."}, headers=auth(root))
    check(r.status_code == 201 and r.get_json()["recipients_count"] == 2, "tous les patients (lien actif et compte actif seulement)")
    check(sorted(m["to"] for m in SENT) == ["aicha@example.com", "brice@example.com"], "un e-mail par patient")
    check(not mine(pb) and len(mine(p1)) == 1, "les patients d'un autre établissement ne reçoivent rien")
    r = client.post(f"/saas/super/tenants/{labo.id}/notifications", json={"audience": "doctors", "title": "Réunion", "message": "Jeudi 18 h."}, headers=auth(root))
    check(r.status_code == 201 and r.get_json()["recipients_count"] == 1 and len(mine(d1)) == 1, "tous les médecins de l'établissement")
    r = client.post(f"/saas/super/tenants/{gnu.id}/notifications", json={"audience": "patients", "title": "GNU", "message": "Info"}, headers=auth(root))
    check(r.status_code == 201 and len(mine(legacy)) == 1, "établissement GNU Health : patients historiques inclus")
    check(client.post(f"/saas/admin/tenants/{labo.id}/notifications", json={"audience": "patients", "title": "x", "message": "y"}, headers=auth(pb)).status_code == 403, "un patient ne peut pas envoyer")

    print("2. Administrateur d'établissement")
    SENT.clear()
    url = f"/saas/admin/tenants/{labo.id}/notifications"
    r = client.post(url, json={"audience": "patients", "user_ids": [p1.id], "title": "Résultat prêt", "message": "Passez au laboratoire."}, headers=auth(admin_a))
    check(r.status_code == 201 and r.get_json()["selection"] == "selected" and [m["to"] for m in SENT] == ["aicha@example.com"], "un patient précis")
    check(mine(p1)[0]["title"] == "Labo A · Résultat prêt" and len(mine(p2)) == 1, "… signé du nom de l'établissement, les autres patients non concernés")
    r = client.post(url, json={"audience": "patients", "user_ids": [p1.id, p2.id], "title": "Rappel", "message": "Jeûne demain."}, headers=auth(admin_a))
    check(r.status_code == 201 and r.get_json()["recipients_count"] == 2, "un ensemble de patients choisis")
    r = client.post(url, json={"audience": "doctors", "user_ids": [d1.id], "title": "Compte rendu", "message": "Disponible."}, headers=auth(admin_a))
    check(r.status_code == 201 and r.get_json()["recipients_count"] == 1, "un médecin précis")
    r = client.post(url, json={"audience": "doctors", "title": "Tous", "message": "Message."}, headers=auth(admin_a))
    check(r.status_code == 201 and r.get_json()["selection"] == "all", "tous les médecins")
    r = client.post(url, json={"audience": "patients", "user_ids": [pb.id], "title": "x", "message": "y"}, headers=auth(admin_a))
    check(r.status_code == 400 and "ne font pas partie" in r.get_json()["message"], "patient d'un autre établissement refusé")
    r = client.post(url, json={"audience": "admins", "title": "x", "message": "y"}, headers=auth(admin_a))
    check(r.status_code == 400, "écrire aux administrateurs : réservé au super-administrateur")
    check(client.post(f"/saas/admin/tenants/{autre.id}/notifications", json={"audience": "patients", "title": "x", "message": "y"}, headers=auth(admin_a)).status_code == 403, "pas d'envoi pour un autre établissement")
    check(client.post(url, json={"audience": "patients", "title": " ", "message": "y"}, headers=auth(admin_a)).status_code == 400, "objet obligatoire")
    check(client.post(url, json={"audience": "patients", "title": "x", "message": "a" * 2001}, headers=auth(admin_a)).status_code == 400, "message limité à 2000 caractères")
    r = client.get(f"/saas/admin/tenants/{labo.id}/notification-recipients?audience=patients&q=brice", headers=auth(admin_a)).get_json()
    check(r["total"] == 1 and r["items"][0]["user_id"] == p2.id, "recherche d'un destinataire")

    print("3. Historique")
    history = client.get(url, headers=auth(admin_a)).get_json()
    check(history["total"] == 7 and history["items"][0]["title"] == "Tous", "historique de l'établissement (le plus récent d'abord)")
    check(history["items"][0]["emails_sent"] == 1 and history["items"][0]["sender_role"] == "tenant_admin", "e-mails envoyés comptés")
    check(client.get("/saas/super/notifications", headers=auth(root)).get_json()["total"] == 8, "historique global du super-administrateur")

    print("4. Mes notifications")
    first = mine(p1)[0]
    r = client.put(f"/saas/me/notifications/{first['id']}/read", headers=auth(p1))
    check(r.status_code == 200 and r.get_json()["is_read"], "marquer comme lue")
    check(client.put(f"/saas/me/notifications/{first['id']}/read", headers=auth(p2)).status_code == 404, "pas la notification d'un autre")
    client.put("/saas/me/notifications/read", headers=auth(p1))
    check(all(n["is_read"] for n in mine(p1)), "tout marquer comme lu")
    client.delete(f"/saas/me/notifications/{first['id']}", headers=auth(p1))
    check(all(n["id"] != first["id"] for n in mine(p1)), "retirer une notification de sa liste")
    client.delete("/saas/me/notifications", headers=auth(p1))
    check(mine(p1) == [], "tout retirer")
    check(len(mine(admin_a)) == 1, "l'administrateur d'établissement a aussi ses notifications")

    print("5. Anciennes routes : seulement ses propres notifications")
    check(client.get(f"/notifications/user/{p2.id}/", headers=auth(p1)).status_code == 403, "lecture des notifications d'un autre refusée")
    check(client.get(f"/notifications/user/{p2.id}/", headers=auth(p2)).status_code == 200, "lecture des siennes")
    other = db.session.execute(select(Notifications).filter_by(user_id=p2.id)).scalars().first()
    check(client.put(f"/notifications/mark_read/{other.id}", headers=auth(p1)).status_code == 403, "marquer comme lue celle d'un autre refusé")
    check(client.delete(f"/notifications/user/{p2.id}/del_all", headers=auth(p1)).status_code == 403, "suppression chez un autre refusée")
    check(db.session.execute(select(NotificationCampaign)).scalars().first() is not None, "envois conservés")

print(f"\n{ok_count} contrôles réussis.")
