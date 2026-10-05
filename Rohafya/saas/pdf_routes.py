"""Comptes rendus PDF déposés par un établissement : extraction, relecture humaine, publication.

Un compte rendu n'est JAMAIS publié sans l'action d'un administrateur de l'établissement.
"""
import datetime
import io

from flask import Blueprint, current_app, g, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import current_user
from sqlalchemy import func, select

from Rohafya import db
from .constants import KIND_LAB, SOURCE_GNUHEALTH
from .decorators import register_error_handler, tenant_admin_required
from .ingestion import notify_new_results, upsert_record
from .models import AuditLog, PdfImport
from .pdf_extraction import (
    MAX_PDF_BYTES,
    STATUS_ERROR,
    STATUS_PUBLISHED,
    STATUS_READY,
    STATUS_REJECTED,
    STATUS_REVIEW,
    analyser_pdf,
    ia_disponible,
    recalculer_hors_norme,
)
from .services import SaasError, audit, now, parse_datetime

pdf_imports = Blueprint("saas_pdf_imports", __name__, url_prefix="/saas/admin/tenants/<int:tenant_id>/pdf-imports")
register_error_handler(pdf_imports)

STATUSES = (STATUS_READY, STATUS_REVIEW, STATUS_PUBLISHED, STATUS_REJECTED, STATUS_ERROR)


def exiger_import_pdf_ouvert():
    """Refuse toute utilisation de l'import PDF tant que la fonctionnalité n'est pas ouverte."""
    if not current_app.config.get("ROHAFYA_PDF_IMPORT_ENABLED", False):
        raise SaasError(
            "L'import de résultats médicaux par PDF arrive prochainement : cette fonctionnalité n'est pas encore ouverte.",
            403,
            feature="pdf_import",
            available=False,
        )


@pdf_imports.before_request
def _import_pdf_ouvert():
    if request.method != "OPTIONS":
        exiger_import_pdf_ouvert()


CHAMPS_INTERNES = ("valeur_source", "unite_source", "ligne_source", "confiance")


def _texte(value, limit):
    value = (value or "").strip() if isinstance(value, str) else ""
    return value[:limit] or None


def _bool(value):
    return str(value).lower() in ("1", "true", "oui", "on", "yes")


def lire_fichier():
    fichier = request.files.get("file")
    if fichier is None or not fichier.filename:
        raise SaasError("Joignez le compte rendu PDF (champ « file »).", 400)
    data = fichier.read()
    if not data:
        raise SaasError("Le fichier est vide.", 400)
    if len(data) > MAX_PDF_BYTES:
        raise SaasError("Fichier trop volumineux (10 Mo au maximum).", 413)
    if not data.startswith(b"%PDF"):
        raise SaasError("Le fichier n'est pas un PDF.", 400)
    return fichier.filename[:255], data


def quota_pdf(tenant):
    """Imports PDF consommés sur la période en cours (N derniers jours calendaires, aujourd'hui compris).

    Le décompte s'appuie sur le journal d'audit : supprimer un compte rendu ne rend pas de crédit.
    """
    limite = int(tenant.setting("pdf_quota_files"))
    jours = int(tenant.setting("pdf_quota_days"))
    debut = datetime.datetime.combine(now().date() - datetime.timedelta(days=jours - 1), datetime.time.min)
    dates = db.session.execute(
        select(AuditLog.created_at)
        .filter(AuditLog.tenant_id == tenant.id, AuditLog.action == "pdf.imported", AuditLog.created_at >= debut)
        .order_by(AuditLog.created_at)
    ).scalars().all()
    utilises = len(dates)
    prochain = None
    if utilises >= limite:
        # Un crédit se libère quand l'import le plus ancien encore compté sort de la période.
        libere = dates[utilises - limite].date() + datetime.timedelta(days=jours)
        prochain = datetime.datetime.combine(libere, datetime.time.min).isoformat()
    return {
        "limit": limite,
        "days": jours,
        "used": utilises,
        "remaining": max(0, limite - utilises),
        "next_available_at": prochain,
    }


def _periode(jours):
    return "jour" if jours == 1 else f"période de {jours} jours"


def creer_import(tenant, filename, data, source, user_id=None):
    """Enregistre le PDF et son extraction (statut « pret » ou « a_relire »)."""
    if tenant.source_type == SOURCE_GNUHEALTH:
        raise SaasError(
            "Les résultats de cet établissement sont lus directement dans GNU Health : l'import PDF n'y est pas disponible.", 400
        )
    quota = quota_pdf(tenant)
    if quota["remaining"] <= 0:
        reprise = datetime.datetime.fromisoformat(quota["next_available_at"])
        raise SaasError(
            f"Limite atteinte : {quota['limit']} fichier(s) par {_periode(quota['days'])}. "
            f"Prochain import possible le {reprise:%d/%m/%Y} à partir de {reprise:%H:%M}.",
            429,
            quota=quota,
        )
    local_ref = _texte(request.form.get("local_ref"), 120)
    autoriser_ia = bool(tenant.setting("pdf_ai_enabled")) and ia_disponible()
    resultat = analyser_pdf(data, autoriser_ia=autoriser_ia, local_ref=local_ref)
    entete = resultat["entete"]
    pdf_import = PdfImport(
        tenant_id=tenant.id,
        filename=filename,
        file_size=len(data),
        file_data=data,
        status=resultat["statut"],
        method=resultat["methode"],
        local_ref=entete.get("local_ref"),
        validation_date=entete.get("validation_date"),
        exam_code=_texte(request.form.get("exam_code"), 120),
        title=_texte(request.form.get("title"), 200),
        extraction=resultat,
        source=source,
        created_by=user_id,
    )
    db.session.add(pdf_import)
    db.session.flush()
    audit("pdf.imported", tenant_id=tenant.id, user_id=user_id, target=filename,
          details={"import_id": pdf_import.id, "statut": pdf_import.status, "methode": pdf_import.method}, commit=False)
    db.session.commit()
    return pdf_import


def _import(import_id):
    pdf_import = db.session.get(PdfImport, import_id)
    if not pdf_import or pdf_import.tenant_id != g.tenant.id:
        raise SaasError("Compte rendu introuvable.", 404)
    return pdf_import


def _modifiable(pdf_import):
    if pdf_import.status == STATUS_PUBLISHED:
        raise SaasError("Ce compte rendu est déjà publié : il ne peut plus être modifié.", 409)


def _verifier_publication(pdf_import):
    """(bloquants, avertissements) avant publication."""
    extraction = pdf_import.extraction or {}
    details = extraction.get("details") or []
    bloquants = []
    if not pdf_import.local_ref:
        bloquants.append("numéro de dossier manquant")
    if not pdf_import.validation_date or parse_datetime(pdf_import.validation_date) is None:
        bloquants.append("date de validation manquante ou invalide")
    if not details:
        bloquants.append("aucune valeur à publier")
    for d in details:
        if not (d.get("name") or "").strip() or not isinstance(d.get("result"), (int, float)):
            bloquants.append("chaque ligne doit avoir un nom et une valeur numérique")
            break
    avertissements = [a["probleme"] + (f" ({a['name']})" if a.get("name") else "") for a in extraction.get("anomalies") or []
                      if a.get("code") is not None or a.get("name")]
    avertissements += [f"ligne non reconnue : {ligne}" for ligne in extraction.get("a_relire") or []]
    return bloquants, avertissements


# =====================================================================
# Routes
# =====================================================================

@pdf_imports.route("", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def list_imports(tenant_id):
    try:
        page = max(1, int(request.args.get("page", 1)))
        size = min(100, max(1, int(request.args.get("page_size", 25))))
    except ValueError:
        page, size = 1, 25
    stmt = select(PdfImport).filter_by(tenant_id=g.tenant.id)
    status = request.args.get("status")
    if status in STATUSES:
        stmt = stmt.filter_by(status=status)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    items = db.session.execute(stmt.order_by(PdfImport.id.desc()).offset((page - 1) * size).limit(size)).scalars().all()
    counts = dict(
        db.session.execute(
            select(PdfImport.status, func.count(PdfImport.id)).filter_by(tenant_id=g.tenant.id).group_by(PdfImport.status)
        ).all()
    )
    return jsonify({
        "items": [item.to_dict() for item in items],
        "total": total,
        "page": page,
        "page_size": size,
        "counts": {s: counts.get(s, 0) for s in STATUSES},
        "ai_available": bool(g.tenant.setting("pdf_ai_enabled")) and ia_disponible(),
        "quota": quota_pdf(g.tenant),
        "available": g.tenant.source_type != SOURCE_GNUHEALTH,
    })


@pdf_imports.route("", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def upload(tenant_id):
    filename, data = lire_fichier()
    pdf_import = creer_import(g.tenant, filename, data, "admin", current_user.id)
    return jsonify(pdf_import.to_dict(full=True)), 201


@pdf_imports.route("/<int:import_id>", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def get_import(tenant_id, import_id):
    body = _import(import_id).to_dict(full=True)
    body["ai_available"] = bool(g.tenant.setting("pdf_ai_enabled")) and ia_disponible()
    return jsonify(body)


@pdf_imports.route("/<int:import_id>/fichier", methods=["GET"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def get_file(tenant_id, import_id):
    pdf_import = _import(import_id)
    return send_file(io.BytesIO(pdf_import.file_data), mimetype="application/pdf",
                     download_name=pdf_import.filename or f"compte-rendu-{pdf_import.id}.pdf")


@pdf_imports.route("/<int:import_id>", methods=["PUT"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def update_import(tenant_id, import_id):
    """Corrections de l'administrateur (en-tête et valeurs) après lecture du PDF."""
    pdf_import = _import(import_id)
    _modifiable(pdf_import)
    data = request.get_json(silent=True) or {}
    for champ, limite in (("local_ref", 120), ("exam_code", 120), ("title", 200), ("validation_date", 20)):
        if champ in data:
            setattr(pdf_import, champ, _texte(data[champ], limite))

    extraction = dict(pdf_import.extraction or {})
    if isinstance(data.get("details"), list):
        lignes = []
        for brute in data["details"]:
            if not isinstance(brute, dict):
                continue
            ligne = {
                "code": _texte(brute.get("code"), 20),
                "name": _texte(brute.get("name"), 120) or "",
                "result": _nombre(brute.get("result")),
                "result_text": _texte(brute.get("result_text"), 120) or "",
                "units": _texte(brute.get("units"), 30) or "",
                "lower_limit": _nombre(brute.get("lower_limit")),
                "upper_limit": _nombre(brute.get("upper_limit")),
                "remarks": _texte(brute.get("remarks"), 200) or "",
                "warning": bool(brute.get("warning")),
            }
            ligne["normal_range"] = _intervalle(ligne["lower_limit"], ligne["upper_limit"])
            ligne["warning"] = recalculer_hors_norme(ligne)
            lignes.append(ligne)
        extraction["details"] = lignes
        # Les valeurs ont été vérifiées à la main : les anomalies de lecture n'ont plus lieu d'être.
        extraction["anomalies"] = []
        extraction["a_relire"] = []
        extraction["corrige_par"] = current_user.id
    pdf_import.extraction = extraction

    bloquants, avertissements = _verifier_publication(pdf_import)
    if pdf_import.status != STATUS_REJECTED:
        pdf_import.status = STATUS_READY if not bloquants and not avertissements else STATUS_REVIEW
    pdf_import.updated_at = now()
    audit("pdf.corrected", tenant_id=g.tenant.id, user_id=current_user.id, target=pdf_import.filename,
          details={"import_id": pdf_import.id}, commit=False)
    db.session.commit()
    body = pdf_import.to_dict(full=True)
    body["blocking"] = bloquants
    return jsonify(body)


def _nombre(value):
    if value is None or value == "":
        return None
    try:
        return float(str(value).replace(",", ".").strip())
    except ValueError:
        return None


def _intervalle(bas, haut):
    if bas is not None and haut is not None:
        return f"{bas} – {haut}"
    if haut is not None:
        return f"< {haut}"
    if bas is not None:
        return f"> {bas}"
    return ""


@pdf_imports.route("/<int:import_id>/reanalyse", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def reanalyse(tenant_id, import_id):
    pdf_import = _import(import_id)
    _modifiable(pdf_import)
    utiliser_ia = _bool((request.get_json(silent=True) or {}).get("use_ai"))
    if utiliser_ia and not g.tenant.setting("pdf_ai_enabled"):
        raise SaasError("La lecture par IA n'est pas autorisée pour cet établissement (réglage du super-administrateur).", 403)
    resultat = analyser_pdf(pdf_import.file_data, autoriser_ia=False, forcer_ia=utiliser_ia, local_ref=pdf_import.local_ref)
    pdf_import.extraction = resultat
    pdf_import.method = resultat["methode"]
    pdf_import.validation_date = pdf_import.validation_date or resultat["entete"].get("validation_date")
    pdf_import.status = resultat["statut"]
    pdf_import.updated_at = now()
    audit("pdf.reanalysed", tenant_id=g.tenant.id, user_id=current_user.id, target=pdf_import.filename,
          details={"import_id": pdf_import.id, "methode": resultat["methode"]}, commit=False)
    db.session.commit()
    return jsonify(pdf_import.to_dict(full=True))


@pdf_imports.route("/<int:import_id>/publier", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def publish(tenant_id, import_id):
    pdf_import = _import(import_id)
    _modifiable(pdf_import)
    if pdf_import.status == STATUS_REJECTED:
        raise SaasError("Ce compte rendu a été rejeté.", 409)
    bloquants, avertissements = _verifier_publication(pdf_import)
    if bloquants:
        raise SaasError("Publication impossible : " + " ; ".join(bloquants) + ".", 400, blocking=bloquants)
    if avertissements and not _bool((request.get_json(silent=True) or {}).get("confirm_warnings")):
        raise SaasError("Des points restent à vérifier : confirmez-les avant de publier.", 409, warnings=avertissements)

    exam_code = pdf_import.exam_code or f"PDF-{pdf_import.id:06d}"
    details = [{k: v for k, v in d.items() if k not in CHAMPS_INTERNES} for d in pdf_import.extraction.get("details") or []]
    for d in details:
        d["warning"] = recalculer_hors_norme(d)
    publieur = f"{current_user.first_name or ''} {current_user.last_name or ''}".strip()
    record, created = upsert_record(g.tenant, KIND_LAB, {
        "local_ref": pdf_import.local_ref,
        "name": exam_code,
        "rec_name": exam_code,
        "test": pdf_import.title or "Compte rendu d'analyses",
        "validation_date": pdf_import.validation_date,
        "validated_by": publieur or None,
        "analytes_summary": f"{sum(1 for d in details if d['warning'])} valeur(s) hors norme sur {len(details)}.",
        "state": "validated",
        "pdf_import_id": pdf_import.id,
        "details": details,
    }, "pdf")
    pdf_import.exam_code = exam_code
    pdf_import.status = STATUS_PUBLISHED
    pdf_import.published_at = now()
    pdf_import.published_by = current_user.id
    db.session.flush()
    pdf_import.record_id = record.id
    audit("pdf.published", tenant_id=g.tenant.id, user_id=current_user.id, target=exam_code,
          details={"import_id": pdf_import.id, "local_ref": pdf_import.local_ref}, commit=False)
    db.session.commit()
    if created:
        notify_new_results(g.tenant, [record])
    return jsonify(pdf_import.to_dict(full=True))


@pdf_imports.route("/<int:import_id>/rejeter", methods=["POST"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def reject(tenant_id, import_id):
    pdf_import = _import(import_id)
    _modifiable(pdf_import)
    pdf_import.status = STATUS_REJECTED
    pdf_import.updated_at = now()
    audit("pdf.rejected", tenant_id=g.tenant.id, user_id=current_user.id, target=pdf_import.filename,
          details={"import_id": pdf_import.id}, commit=False)
    db.session.commit()
    return jsonify(pdf_import.to_dict(full=True))


@pdf_imports.route("/<int:import_id>", methods=["DELETE"])
@cross_origin(supports_credentials=True)
@tenant_admin_required
def delete_import(tenant_id, import_id):
    pdf_import = _import(import_id)
    _modifiable(pdf_import)
    filename = pdf_import.filename
    db.session.delete(pdf_import)
    audit("pdf.deleted", tenant_id=g.tenant.id, user_id=current_user.id, target=filename, commit=False)
    db.session.commit()
    return {"message": "Compte rendu supprimé."}
