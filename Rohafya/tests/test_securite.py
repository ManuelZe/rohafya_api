"""Double authentification des administrateurs et données personnelles (historique, export, suppression).

SQLite en mémoire. Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_securite.py
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
    ROHAFYA_ADMIN_MFA_REQUIRED=True,
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
from Rohafya.saas.ingest_routes import fhir, ingest  # noqa: E402
from Rohafya.saas import billing, i18n, ingestion, privacy, security  # noqa: E402
from Rohafya.saas.models import DeletionRequest, PatientLink, TenantPatient  # noqa: E402
from Rohafya.saas.models import TenantInvoice, UsageEvent, UserPreference  # noqa: E402

for bp in (saas_account, super_admin, tenant_admin, general_notification, ingest, fhir):
    app.register_blueprint(bp)

User.has_permission = lambda self, code: True
UserActivity.__table__.c.route.type = JSON()
SENT = []


def fake_send(to, subject, template, **context):
    SENT.append({"to": to, "subject": subject, "template": template, **context})
    return True


services.send_mail = fake_send
billing.send_mail = fake_send
privacy.send_mail = fake_send
import Rohafya.saas.account_routes as account_routes  # noqa: E402
account_routes.send_mail = fake_send
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

    def auth(user, mfa=False):
        return {"Authorization": "Bearer " + create_access_token(identity=str(user.id), additional_claims={"mfa": mfa})}

    def bearer(token):
        return {"Authorization": "Bearer " + token}

    def code_for(secret, offset=0):
        return security._code(secret, security.current_step() + offset)

    root = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    other_root = services.create_user("ops@rohafya.test", "Ops", "Admin", "SuperAdmin")
    admin = services.create_user("admin@horizon.cm", "Awa", "Bello", "EstablishmentAdmin")
    patient = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    profile = services.ensure_patient_profile(patient)
    db.session.commit()
    tenant = services.Tenant(slug="horizon", name="Laboratoire Horizon", source_type="api", settings={})
    db.session.add(tenant)
    db.session.flush()
    services.ensure_tenant_admin(admin, tenant)
    db.session.add(TenantPatient(tenant_id=tenant.id, local_ref="P-1", first_name="Aïcha", last_name="Ngono"))
    db.session.add(PatientLink(tenant_id=tenant.id, local_ref="P-1", patient_id=services.patient_of_user(patient.id).id, status="active"))
    db.session.commit()

    print("1. Double authentification obligatoire pour les administrateurs")
    r = client.get(f"/saas/admin/tenants/{tenant.id}/dashboard", headers=auth(admin))
    check(r.status_code == 403 and r.get_json()["code"] == "mfa_setup_required", "console fermée tant qu'elle n'est pas activée")
    me = client.get("/saas/me", headers=auth(admin)).get_json()
    check(me["security"] == {"enabled": False, "required": True, "enabled_at": None, "recovery_codes_left": 0}, "/saas/me signale l'obligation")
    check(client.get("/saas/me", headers=auth(patient)).get_json()["security"]["required"] is False, "facultative pour un patient")

    print("2. Activation")
    setup = client.post("/saas/me/mfa/setup", headers=auth(admin)).get_json()
    secret = setup["secret"]
    check(setup["otpauth_uri"].startswith("otpauth://totp/ROHAFYA:admin%40horizon.cm?secret=") and setup["qr_png"], "QR code de configuration")
    check(security.mfa_of(admin.id).secret != secret, "secret chiffré en base")
    check(client.post("/saas/me/mfa/enable", json={"code": "000000"}, headers=auth(admin)).status_code == 400, "mauvais code refusé (sans fermer la session)")
    SENT.clear()
    r = client.post("/saas/me/mfa/enable", json={"code": code_for(secret)}, headers=auth(admin))
    body = r.get_json()
    check(r.status_code == 200 and len(body["recovery_codes"]) == 10 and body["security"]["enabled"], "activée, dix codes de secours")
    check(any(m["template"] == "emails/securite.html" for m in SENT), "e-mail de sécurité envoyé")
    recovery = body["recovery_codes"]
    r = client.get(f"/saas/admin/tenants/{tenant.id}/dashboard", headers=bearer(body["access_token"]))
    check(r.status_code == 200, "la nouvelle session ouvre la console")
    r = client.get(f"/saas/admin/tenants/{tenant.id}/dashboard", headers=auth(admin))
    check(r.status_code == 401 and r.get_json()["code"] == "mfa_required", "une session sans code est refusée")

    print("3. Connexion en deux étapes")
    with app.test_request_context():
        step1 = app.user_manager._do_login_user(admin, remember_me=True).get_json()
    check(step1["mfa_required"] and "access_token" not in step1, "le mot de passe seul n'ouvre pas de session")
    security.mfa_of(admin.id).last_step = None
    db.session.commit()
    code = code_for(secret)
    r = client.post("/saas/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": code})
    check(r.status_code == 200 and r.get_json()["access_token"], "le code ouvre la session")
    check(client.get(f"/saas/admin/tenants/{tenant.id}/dashboard", headers=bearer(r.get_json()["access_token"])).status_code == 200, "… marquée « mfa »")
    check(client.post("/saas/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": code}).status_code == 400, "un code ne sert qu'une fois")
    r = client.post("/saas/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": recovery[0].upper()})
    check(r.status_code == 200, "code de secours accepté")
    check(client.post("/saas/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": recovery[0].replace("-", "")}).status_code == 400, "… une seule fois, avec ou sans tiret")
    check(client.post("/saas/auth/mfa/verify", json={"mfa_token": "faux", "code": "123456"}).status_code == 401, "jeton d'étape falsifié refusé")
    for _ in range(4):
        client.post("/saas/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": "111111"})
    check(client.post("/saas/auth/mfa/verify", json={"mfa_token": step1["mfa_token"], "code": code_for(secret, 1)}).status_code == 429, "cinq erreurs : blocage de 15 minutes")

    print("4. Désactivation et réinitialisation")
    check(client.post("/saas/me/mfa/disable", json={"code": "x"}, headers=auth(admin, True)).status_code == 403, "un administrateur ne peut pas la désactiver")
    s2 = client.post("/saas/me/mfa/setup", headers=auth(patient)).get_json()["secret"]
    client.post("/saas/me/mfa/enable", json={"code": code_for(s2)}, headers=auth(patient))
    check(security.mfa_enabled(patient), "un patient peut l'activer")
    security.mfa_of(patient.id).last_step = None
    db.session.commit()
    check(client.post("/saas/me/mfa/disable", json={"code": code_for(s2)}, headers=auth(patient, True)).status_code == 200 and not security.mfa_enabled(patient), "… et la désactiver avec un code")
    root_secret = client.post("/saas/me/mfa/setup", headers=auth(root)).get_json()["secret"]
    root_token = client.post("/saas/me/mfa/enable", json={"code": code_for(root_secret)}, headers=auth(root)).get_json()["access_token"]
    r = client.put(f"/saas/super/users/{admin.id}/mfa-reset", headers=bearer(root_token))
    check(r.status_code == 200 and not security.mfa_enabled(admin), "réinitialisée par un super-administrateur")
    check(client.put(f"/saas/super/users/{root.id}/mfa-reset", headers=bearer(root_token)).status_code == 400, "pas sur son propre compte")
    users = client.get("/saas/super/users?q=root", headers=bearer(root_token)).get_json()["items"]
    check(users and users[0]["mfa_enabled"] is True, "visible dans la liste des comptes")

    print("5. Historique des accès du patient")
    app.config["ROHAFYA_ADMIN_MFA_REQUIRED"] = False
    client.get(f"/saas/admin/tenants/{tenant.id}/patients/P-1", headers=auth(admin))
    services.audit("result.viewed", tenant_id=tenant.id, user_id=root.id, target="LAB-1", details={"local_ref": "P-1"})
    services.audit("result.viewed", tenant_id=tenant.id, user_id=root.id, target="LAB-9", details={"local_ref": "P-2"})
    services.audit("patient.viewed", tenant_id=tenant.id, user_id=admin.id, target="P-2")
    log = client.get("/saas/me/access-log", headers=auth(patient)).get_json()["items"]
    actions = [item["action"] for item in log]
    check(actions.count("patient.viewed") == 1 and actions.count("result.viewed") == 1, "seuls les accès à son dossier")
    viewed = next(item for item in log if item["action"] == "patient.viewed")
    check(viewed["establishment"] == "Laboratoire Horizon" and viewed["actor_role"] == "establishment" and viewed["actor_name"] is None, "établissement nommé, personnel anonyme")
    shared = next(item for item in log if item["action"] == "result.viewed")
    check(shared["actor_role"] == "doctor" and shared["actor_name"] == "Root Admin" and shared["target"] == "LAB-1", "médecin et résultat nommés")
    check(client.get("/saas/me/access-log", headers=auth(admin)).get_json()["items"] == [], "rien pour un compte sans dossier")

    print("6. Export des données")
    export = client.get("/saas/me/export", headers=auth(patient)).get_json()
    check(export["account"]["email"] == "aicha@example.com" and "password" not in str(export["patient_profile"]).lower().replace("passwordless", ""), "compte et profil, sans mot de passe")
    check(export["establishments"][0]["establishment"] == "Laboratoire Horizon" and len(export["access_log"]) == 2, "établissements et historique des accès")
    check(client.get("/saas/me/access-log", headers=auth(patient)).get_json()["items"][0]["action"] == "account.exported", "l'export est tracé")

    print("7. Suppression du compte")
    SENT.clear()
    r = client.post("/saas/me/deletion-request", json={"reason": "Je n'utilise plus ROHAFYA"}, headers=auth(patient))
    req_id = r.get_json()["id"]
    check(r.status_code == 201 and client.get("/saas/me", headers=auth(patient)).get_json()["deletion_requested"], "demande enregistrée")
    check(any(m["template"] == "emails/suppression_compte.html" for m in SENT), "accusé de réception par e-mail")
    check(client.post("/saas/me/deletion-request", json={}, headers=auth(patient)).status_code == 409, "une seule demande à la fois")
    notes = client.get("/saas/me/notifications", headers=auth(root)).get_json()
    check(notes and notes[0]["title"] == "Demande de suppression de compte", "super-administrateurs prévenus")
    check(client.delete("/saas/me/deletion-request", headers=auth(patient)).status_code == 200, "demande annulable")
    req_id = client.post("/saas/me/deletion-request", json={}, headers=auth(patient)).get_json()["id"]
    check(client.post(f"/saas/super/deletion-requests/{req_id}", json={"decision": "refuse"}, headers=bearer(root_token)).status_code == 400, "refus : motif obligatoire")
    r = client.post(f"/saas/super/deletion-requests/{req_id}", json={"decision": "anonymize"}, headers=bearer(root_token))
    check(r.status_code == 200 and r.get_json()["status"] == "traitee", "compte anonymisé")
    db.session.refresh(patient)
    profile = services.patient_of_user(patient.id)
    link = db.session.execute(select(PatientLink).filter_by(local_ref="P-1")).scalar_one()
    check(not patient.active and patient.email.endswith("@compte-supprime.invalid") and patient.first_name == "Compte", "compte désactivé et anonymisé")
    check(profile.PatientName == "Compte" and profile.PatientPhone is None and link.status == "revoked", "profil effacé, rattachement retiré")
    check(db.session.get(TenantPatient, 1).first_name == "Aïcha", "le dossier de l'établissement est conservé")
    check(client.post(f"/saas/super/deletion-requests/{req_id}", json={"decision": "anonymize"}, headers=bearer(root_token)).status_code == 409, "demande déjà traitée")
    listing = client.get("/saas/super/deletion-requests", headers=bearer(root_token)).get_json()
    check(listing["total"] == 2, "historique des demandes")

print(f"\n{ok_count} contrôles réussis.")
