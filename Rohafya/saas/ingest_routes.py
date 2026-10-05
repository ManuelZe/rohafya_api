"""Réception des données médicales envoyées par les établissements.

Deux méthodes, authentifiées par la clé d'API de l'établissement (en-tête X-ROHAFYA-API-Key ; l'ancien X-EDEN-API-Key reste accepté) :

1. API ROHAFYA (format des données de la démo) :
   POST   /ingest/v1/patients                 [{local_ref, first_name, last_name, email, birth_date, gender, phone}]
   POST   /ingest/v1/laboratoire              [{local_ref, name, test, validation_date, …, details: [...]}]
   POST   /ingest/v1/imagerie                 [{local_ref, number, requested_test, validation_date, …, details: [...]}]
   POST   /ingest/v1/exploration              [{local_ref, name, test, validation_date, …, details: [...]}]
   POST   /ingest/v1/factures                 [{local_ref, reference, date, state, total_amount2, …, products: [...]}]
   DELETE /ingest/v1/<type>/<code>            retire un enregistrement envoyé par erreur
   POST   /ingest/v1/link-tokens              {local_ref, email?} → URL et code court à imprimer sur la facture
   POST   /ingest/v1/pdf                      compte rendu PDF (multipart) → file de relecture de l'établissement
   GET    /ingest/v1/ping                     vérifie la clé

2. HL7 FHIR R4 :
   POST   /fhir/r4                            Bundle (Patient, DiagnosticReport + Observation, Invoice)
   POST   /fhir/r4/<ResourceType>             une ressource isolée
   GET    /fhir/r4/metadata                   ressources prises en charge
"""
from flask import Blueprint, g, jsonify, request
from flask_cors import cross_origin

from Rohafya import db
from .constants import KIND_INVOICE, RECORD_KINDS
from .decorators import api_key_required, register_error_handler
from .fhir import operation_outcome, process_resources, resources_from_body
from .ingestion import delete_record, ingest_batch
from .models import TenantPatient
from .services import SaasError, audit, issue_link_token, qr_png_base64
from sqlalchemy import select

ingest = Blueprint("saas_ingest", __name__, url_prefix="/ingest/v1")
fhir = Blueprint("saas_fhir", __name__, url_prefix="/fhir/r4")
register_error_handler(ingest)
register_error_handler(fhir)

# L'URL utilise le pluriel « factures » ; le type interne est « facture ».
URL_KINDS = {"patients": "patients", "factures": KIND_INVOICE, **{kind: kind for kind in RECORD_KINDS if kind != KIND_INVOICE}}


def _body():
    body = request.get_json(silent=True)
    if body is None:
        raise SaasError("Corps JSON attendu (Content-Type: application/json).", 400)
    return body


@ingest.route("/ping", methods=["GET"])
@api_key_required
def ping():
    return {"establishment": g.tenant.display_name, "slug": g.tenant.slug, "source_type": g.tenant.source_type}


@ingest.route("/<string:url_kind>", methods=["POST"])
@api_key_required
def ingest_items(url_kind):
    kind = URL_KINDS.get(url_kind)
    if kind is None:
        raise SaasError(f"Type inconnu : {url_kind}. Types acceptés : {', '.join(URL_KINDS)}.", 404)
    result = ingest_batch(g.tenant, kind, _body(), "api")
    audit("ingest.api", tenant_id=g.tenant.id, target=url_kind, details=result)
    return jsonify(result)


@ingest.route("/<string:url_kind>/<path:code>", methods=["DELETE"])
@api_key_required
def remove_item(url_kind, code):
    kind = URL_KINDS.get(url_kind)
    if kind in (None, "patients"):
        raise SaasError("Seuls les résultats et les factures peuvent être retirés.", 400)
    delete_record(g.tenant, kind, code)
    audit("ingest.deleted", tenant_id=g.tenant.id, target=f"{url_kind}/{code}", commit=False)
    db.session.commit()
    return {"message": "Enregistrement retiré."}


@ingest.route("/link-tokens", methods=["POST"])
@api_key_required
def create_link_token():
    data = _body()
    local_ref = (data.get("local_ref") or "").strip()
    email = data.get("email")
    if not email and local_ref:
        known = db.session.execute(select(TenantPatient).filter_by(tenant_id=g.tenant.id, local_ref=local_ref)).scalar_one_or_none()
        email = known.email if known else None
    result = issue_link_token(g.tenant, local_ref, email_hint=email)
    if not result.get("already_linked") and request.args.get("qr") in ("1", "true", "png"):
        result["qr_png"] = qr_png_base64(result["url"])
    return jsonify(result)


@ingest.route("/pdf", methods=["POST"])
@api_key_required
def ingest_pdf():
    """Dépôt d'un compte rendu PDF (multipart : file, local_ref?, exam_code?, title?).

    Le PDF rejoint la file « Imports PDF » de l'établissement : il n'est publié qu'après
    validation par un administrateur.
    """
    from .pdf_routes import creer_import, exiger_import_pdf_ouvert, lire_fichier

    exiger_import_pdf_ouvert()
    filename, data = lire_fichier()
    pdf_import = creer_import(g.tenant, filename, data, "api")
    return jsonify(pdf_import.to_dict(full=True)), 201


@fhir.route("/metadata", methods=["GET"])
@cross_origin()
def metadata():
    return {
        "resourceType": "CapabilityStatement",
        "status": "active",
        "fhirVersion": "4.0.1",
        "format": ["json"],
        "rest": [
            {
                "mode": "server",
                "resource": [
                    {"type": name, "interaction": [{"code": "create"}, {"code": "update"}]}
                    for name in ("Patient", "DiagnosticReport", "Observation", "Invoice")
                ],
            }
        ],
    }


@fhir.route("", methods=["POST"])
@fhir.route("/", methods=["POST"])
@api_key_required
def receive_bundle():
    summary = process_resources(g.tenant, resources_from_body(_body()))
    audit("ingest.fhir", tenant_id=g.tenant.id, target="Bundle", details=summary)
    return jsonify(operation_outcome(summary))


@fhir.route("/<string:resource_type>", methods=["POST", "PUT"])
@fhir.route("/<string:resource_type>/<string:resource_id>", methods=["PUT"])
@api_key_required
def receive_resource(resource_type, resource_id=None):
    body = _body()
    if body.get("resourceType") != resource_type:
        raise SaasError(f"resourceType attendu : {resource_type}.", 400)
    summary = process_resources(g.tenant, resources_from_body(body))
    audit("ingest.fhir", tenant_id=g.tenant.id, target=resource_type, details=summary)
    return jsonify(operation_outcome(summary))
