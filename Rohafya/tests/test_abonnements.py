"""Abonnements des établissements (volume, alertes, factures) et messages dans la langue du compte.

SQLite en mémoire. Lancement, depuis la racine du dépôt :
    Rohafya/envDoc/bin/python Rohafya/tests/test_abonnements.py
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
from Rohafya.saas.ingest_routes import fhir, ingest  # noqa: E402
from Rohafya.saas import billing, i18n, ingestion  # noqa: E402
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

    def lab(code, ref="P-1"):
        return {"local_ref": ref, "name": code, "test": "NFS", "validation_date": "2026-10-01T09:00:00"}

    root = services.create_user("root@rohafya.test", "Root", "Admin", "SuperAdmin")
    admin = services.create_user("admin@horizon.cm", "Awa", "Bello", "EstablishmentAdmin")
    patient = services.create_user("aicha@example.com", "Aïcha", "Ngono", "Patient")
    services.ensure_patient_profile(patient)
    db.session.commit()

    print("1. Abonnement créé avec l'établissement")
    r = client.post("/saas/super/tenants", json={"name": "Laboratoire Horizon", "slug": "horizon", "source_type": "api"}, headers=auth(root))
    tenant_id = r.get_json()["id"]
    tenant = db.session.get(services.Tenant, tenant_id)
    services.ensure_tenant_admin(admin, tenant)
    db.session.commit()
    sub = client.get(f"/saas/super/tenants/{tenant_id}/subscription", headers=auth(root)).get_json()["subscription"]
    check(sub["status"] == "essai" and sub["plan"] == "standard" and sub["trial_days_left"] == 60 and sub["billed"], "essai de 60 jours, formule Standard")
    check(sub["quota"] == 2000 and sub["monthly_price"] == 50000 and sub["setup_fee"] == 150000, "conditions de la formule Standard")
    gnu = services.default_gnuhealth_tenant()
    gnu_sub = client.get(f"/saas/super/tenants/{gnu.id}/subscription", headers=auth(root)).get_json()["subscription"]
    check(not gnu_sub["billed"] and gnu_sub["status"] == "actif", "établissement GNU Health non facturé")

    print("2. Volume du mois")
    ingestion.ingest_batch(tenant, "laboratoire", [lab("LAB-1"), lab("LAB-2")], "api")
    ingestion.ingest_batch(tenant, "laboratoire", [lab("LAB-1")], "api")
    ingestion.ingest_batch(tenant, "facture", [{"local_ref": "P-1", "reference": "FAC-1"}], "api")
    usage = client.get(f"/saas/admin/tenants/{tenant_id}/subscription", headers=auth(admin)).get_json()
    check(usage["current"]["count"] == 2, "deux résultats comptés : la mise à jour et la facture ne comptent pas")
    check(len(usage["history"]) == 12 and usage["history"][-1]["count"] == 2, "historique des douze derniers mois")
    dash = client.get(f"/saas/admin/tenants/{tenant_id}/dashboard", headers=auth(admin)).get_json()
    check(dash["usage"]["count"] == 2 and dash["usage"]["quota"] == 2000, "volume affiché au tableau de bord")
    client.delete(f"/ingest/v1/laboratoire/LAB-2")
    record = db.session.execute(select(services.TenantRecord).filter_by(tenant_id=tenant_id, code="LAB-2")).scalar_one()
    db.session.delete(record)
    db.session.commit()
    ingestion.ingest_batch(tenant, "laboratoire", [lab("LAB-2")], "api")
    check(billing.usage_counts(tenant_id, [billing.period_of(billing.today())])[billing.period_of(billing.today())] == 2, "un résultat supprimé puis renvoyé dans le mois n'est compté qu'une fois")

    print("3. Alertes de volume (sans blocage)")
    db.session.add(UserPreference(user_id=admin.id, locale="en"))
    db.session.commit()
    r = client.put(f"/saas/super/tenants/{tenant_id}/subscription", json={"plan": "essentiel", "custom_quota": 10}, headers=auth(root))
    check(r.status_code == 200 and r.get_json()["subscription"]["quota"] == 10, "volume particulier convenu")
    SENT.clear()
    ingestion.ingest_batch(tenant, "laboratoire", [lab(f"LAB-{i}") for i in range(3, 9)], "api")
    notes = client.get("/saas/me/notifications", headers=auth(admin)).get_json()
    check(notes and notes[0]["title"] == "Results volume: 80% of your plan", "alerte à 80 % dans la langue de l'administrateur")
    check(any(m["template"] == "emails/alerte_volume.html" and m.get("locale") == "en" for m in SENT), "… et par e-mail en anglais")
    ingestion.ingest_batch(tenant, "laboratoire", [lab(f"LAB-{i}") for i in range(9, 13)], "api")
    notes = client.get("/saas/me/notifications", headers=auth(admin)).get_json()
    check(notes[0]["title"] == "Results volume: 120% of your plan", "alerte au-delà de 100 %")
    count_before = len(notes)
    ingestion.ingest_batch(tenant, "laboratoire", [lab("LAB-13")], "api")
    check(len(client.get("/saas/me/notifications", headers=auth(admin)).get_json()) == count_before, "une seule alerte par seuil et par mois")
    check(db.session.execute(select(services.TenantRecord).filter_by(tenant_id=tenant_id, code="LAB-13")).first() is not None, "la publication continue au-delà du volume")
    r = client.put(f"/saas/super/tenants/{tenant_id}/subscription", json={"plan": "reseau", "custom_quota": ""}, headers=auth(root))
    check(r.status_code == 400, "formule Réseau : volume convenu obligatoire")

    print("4. Recommandation de formule (trois mois consécutifs)")
    client.put(f"/saas/super/tenants/{tenant_id}/subscription", json={"plan": "essentiel", "custom_quota": ""}, headers=auth(root))
    for offset in (1, 2, 3):
        period = billing.period_of(billing.add_months(billing.today().replace(day=1), -offset))
        db.session.add_all(UsageEvent(tenant_id=tenant_id, kind="laboratoire", code=f"OLD-{offset}-{i}", period=period) for i in range(600 + offset))
    db.session.commit()
    reco = client.get(f"/saas/admin/tenants/{tenant_id}/subscription", headers=auth(admin)).get_json()["recommendation"]
    check(reco and reco["plan"] == "standard" and reco["direction"] == "up", "formule supérieure proposée après trois mois au-delà")
    check(reco["applies_from"] == billing.next_quarter_start().isoformat(), "… applicable au trimestre suivant")

    print("5. Facturation d'avance")
    r = client.put(f"/saas/super/tenants/{tenant_id}/subscription",
                   json={"status": "actif", "billing_cycle": "annuel", "free_months": 1}, headers=auth(root))
    check(r.get_json()["subscription"]["next_invoice_on"] == billing.today().isoformat(), "passage à l'abonnement : première facture aujourd'hui")
    SENT.clear()
    r = client.post("/saas/super/invoices/issue", json={}, headers=auth(root))
    invoice = r.get_json()["issued"][0]
    types = [line["type"] for line in invoice["lines"]]
    check(r.get_json()["count"] == 1 and types == ["subscription", "free_month", "setup"], "abonnement, mois offert et mise en service")
    check(invoice["lines"][0]["quantity"] == 10 and invoice["total"] == 9 * 25000 + 75000, "annuel : dix mois payés pour douze, moins le mois offert")
    check(invoice["number"].startswith("ROH-") and invoice["status"] == "emise", "facture numérotée et émise")
    check(any(m["template"] == "emails/facture.html" for m in SENT), "facture annoncée aux administrateurs")
    check(client.post("/saas/super/invoices/issue", json={}, headers=auth(root)).get_json()["count"] == 0, "pas de double facturation")
    sub = client.get(f"/saas/super/tenants/{tenant_id}/subscription", headers=auth(root)).get_json()["subscription"]
    check(sub["next_invoice_on"] == billing.add_months(billing.today(), 12).isoformat() and sub["setup_invoiced"] and sub["free_months"] == 0, "prochaine facture dans un an")
    later = billing.issue_due_invoices(day=billing.add_months(billing.today(), 12))
    check(len(later) == 1 and [l["type"] for l in later[0].lines] == ["subscription"], "renouvellement sans frais de mise en service")

    print("6. Suivi des paiements et accès")
    inv_id = invoice["id"]
    r = client.put(f"/saas/super/invoices/{inv_id}", json={"status": "payee", "payment_reference": "MOMO-123"}, headers=auth(root))
    check(r.status_code == 200 and r.get_json()["paid_on"] == billing.today().isoformat(), "facture marquée payée")
    check(client.get(f"/saas/super/invoices?status=payee", headers=auth(root)).get_json()["total"] == 1, "filtre des factures")
    detail = client.get(f"/saas/admin/tenants/{tenant_id}/invoices/{inv_id}", headers=auth(admin)).get_json()
    check(detail["number"] == invoice["number"] and "issuer" in detail, "l'établissement télécharge sa facture")
    check(client.get(f"/saas/admin/tenants/{gnu.id}/invoices/{inv_id}", headers=auth(root)).status_code == 404, "facture d'un autre établissement : introuvable")
    check(client.get(f"/saas/admin/tenants/{tenant_id}/subscription", headers=auth(patient)).status_code == 403, "réservé aux administrateurs")
    overview = client.get("/saas/super/billing", headers=auth(root)).get_json()
    row = next(t for t in overview["tenants"] if t["tenant_id"] == tenant_id)
    check(row["unpaid"]["count"] == 1 and overview["totals"]["monthly_recurring"] == 25000, "vue d'ensemble : impayés et revenu mensuel")
    old = db.session.get(TenantInvoice, later[0].id)
    old.due_on = billing.today().replace(year=billing.today().year - 1)
    db.session.commit()
    check(client.get("/saas/super/invoices?status=overdue", headers=auth(root)).get_json()["total"] == 1, "factures en retard")

    print("7. Messages dans la langue du compte")
    check(i18n.template_for("emails/otp_code.html", "en") == "emails/en/otp_code.html", "version anglaise d'un e-mail")
    check(i18n.template_for("emails/otp_code.html", "fr") == "emails/otp_code.html", "version française par défaut")
    with app.test_request_context(headers={"Accept-Language": "en"}):
        check(i18n.email_locale("inconnu@example.com") == "en", "sans compte : langue de l'interface (Accept-Language)")
        check(i18n.email_locale("admin@horizon.cm") == "en", "avec compte : préférence du compte")
    with app.test_request_context(headers={"Accept-Language": "en"}):
        db.session.add(UserPreference(user_id=root.id, locale="fr"))
        db.session.commit()
        check(i18n.email_locale("root@rohafya.test") == "fr", "la préférence du compte l'emporte sur le navigateur")
    from flask import render_template
    with app.test_request_context():
        html = render_template("emails/en/otp_code.html", code="123456", minutes=10, email="x@y.z", front_url="https://r", locale="en")
        check("sign-in code" in html and 'lang="en"' in html, "e-mail anglais rendu")
        html = render_template("emails/facture.html", number="ROH-1", establishment="Horizon", period="p", total="1", due="d", open_url="u", locale="fr")
        check("Facture ROH-1" in html, "e-mail de facture rendu")
    db.session.add(UserPreference(user_id=patient.id, locale="en"))
    db.session.commit()
    services.notify_user(patient.id, "result.new.title", "result.new.message", establishment="Horizon", kind=i18n.text("result.kind.laboratoire", "en"), code="LAB-9")
    note = client.get("/saas/me/notifications", headers=auth(patient)).get_json()[0]
    check(note["title"] == "New result available" and note["message"] == "Horizon published a laboratory result (LAB-9).", "notification système en anglais")
    check(i18n.text("Texte {libre}", "en") == "Texte {libre}", "un texte déjà rédigé reste tel quel")

print(f"\n{ok_count} contrôles réussis.")
