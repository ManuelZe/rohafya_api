"""Conversion des ressources HL7 FHIR R4 vers le format ROHAFYA (celui de l'API et de la démo).

Ressources prises en charge :
- Patient            → dossier du patient dans l'établissement (local_ref = identifier[0].value, sinon id)
- DiagnosticReport   → résultat d'examen ; catégorie LAB → laboratoire, RAD/IMG → imagerie, autre → exploration
- Observation        → valeurs mesurées d'un DiagnosticReport (référencées par `result`)
- Invoice            → facture et ses lignes (`lineItem`)
"""
from .constants import KIND_EXPLORATION, KIND_IMAGING, KIND_INVOICE, KIND_LAB
from .ingestion import notify_new_results, upsert_patient, upsert_record
from .services import SaasError
from Rohafya import db

LAB_CATEGORIES = {"LAB", "laboratory", "HM", "CH", "MB", "SP"}
IMAGING_CATEGORIES = {"RAD", "IMG", "imaging", "RX", "US", "CT", "MR", "NMR"}
ABNORMAL_FLAGS = {"H", "HH", "L", "LL", "A", "AA", "HU", "LU", "POS"}
INVOICE_STATES = {
    "draft": "draft",
    "issued": "posted",
    "balanced": "paid",
    "cancelled": "cancel",
    "entered-in-error": "cancel",
}


def _first(items):
    return items[0] if isinstance(items, list) and items else {}


def _concept_text(concept):
    if not isinstance(concept, dict):
        return None
    if concept.get("text"):
        return concept["text"]
    coding = _first(concept.get("coding"))
    return coding.get("display") or coding.get("code")


def _codes(concepts):
    codes = set()
    for concept in concepts or []:
        for coding in concept.get("coding", []) if isinstance(concept, dict) else []:
            if coding.get("code"):
                codes.add(coding["code"])
    return codes


def _reference_id(reference):
    """'Patient/123' ou 'urn:uuid:abc' → '123' / 'urn:uuid:abc'."""
    value = (reference or {}).get("reference") if isinstance(reference, dict) else reference
    if not value:
        return None
    return value.split("/")[-1] if "/" in value and not value.startswith("urn:") else value


def _display(reference):
    return reference.get("display") if isinstance(reference, dict) else None


def _identifier(resource):
    ident = _first(resource.get("identifier"))
    return ident.get("value") or resource.get("id")


def _money(value):
    return (value or {}).get("value") if isinstance(value, dict) else None


class BundleIndex:
    """Permet de résoudre les références entre ressources d'un même Bundle."""

    def __init__(self, entries):
        self.by_key = {}
        for entry in entries:
            resource = entry.get("resource") or {}
            kind, rid = resource.get("resourceType"), resource.get("id")
            if entry.get("fullUrl"):
                self.by_key[entry["fullUrl"]] = resource
            if kind and rid:
                self.by_key[f"{kind}/{rid}"] = resource
                self.by_key[rid] = resource

    def resolve(self, reference):
        value = (reference or {}).get("reference") if isinstance(reference, dict) else reference
        if not value:
            return None
        return self.by_key.get(value) or self.by_key.get(value.split("/")[-1])


def _patient_ref(reference, index):
    patient = index.resolve(reference)
    if patient and patient.get("resourceType") == "Patient":
        return _identifier(patient)
    if isinstance(reference, dict) and isinstance(reference.get("identifier"), dict):
        return reference["identifier"].get("value")
    return _reference_id(reference)


def map_patient(resource):
    name = _first(resource.get("name"))
    email = next((t.get("value") for t in resource.get("telecom", []) if t.get("system") == "email"), None)
    phone = next((t.get("value") for t in resource.get("telecom", []) if t.get("system") == "phone"), None)
    return {
        "local_ref": _identifier(resource),
        "first_name": " ".join(name.get("given", [])) or None,
        "last_name": name.get("family"),
        "email": email,
        "phone": phone,
        "birth_date": resource.get("birthDate"),
        "gender": {"female": "F", "male": "M"}.get(resource.get("gender"), resource.get("gender")),
    }


def _observation_detail(observation):
    quantity = observation.get("valueQuantity") or {}
    ref_range = _first(observation.get("referenceRange"))
    low, high = (ref_range.get("low") or {}).get("value"), (ref_range.get("high") or {}).get("value")
    flags = _codes(observation.get("interpretation"))
    value = quantity.get("value")
    if value is None:
        value = observation.get("valueString") or _concept_text(observation.get("valueCodeableConcept"))
    return {
        "name": _concept_text(observation.get("code")),
        "result": value if isinstance(value, (int, float)) else None,
        "result_text": value if not isinstance(value, (int, float)) else "",
        "units": quantity.get("unit") or quantity.get("code"),
        "lower_limit": low,
        "upper_limit": high,
        "normal_range": ref_range.get("text") or (f"{low} – {high}" if low is not None and high is not None else ""),
        "remarks": _concept_text(_first(observation.get("interpretation"))) or "",
        "warning": bool(flags & ABNORMAL_FLAGS),
    }


def map_diagnostic_report(resource, index):
    categories = _codes(resource.get("category"))
    if categories & IMAGING_CATEGORIES:
        kind = KIND_IMAGING
    elif categories & LAB_CATEGORIES:
        kind = KIND_LAB
    else:
        kind = KIND_EXPLORATION

    code = _identifier(resource)
    title = _concept_text(resource.get("code"))
    performer = _display(_first(resource.get("performer")))
    interpreter = _display(_first(resource.get("resultsInterpreter"))) or performer
    requestor = _display(_first(resource.get("basedOn")))
    effective = resource.get("effectiveDateTime") or (resource.get("effectivePeriod") or {}).get("start")
    conclusion = resource.get("conclusion")

    details = []
    for reference in resource.get("result", []):
        observation = index.resolve(reference)
        if observation and observation.get("resourceType") == "Observation":
            details.append(_observation_detail(observation))

    data = {"local_ref": _patient_ref(resource.get("subject"), index), "state": resource.get("status"), "details": details}
    if kind == KIND_IMAGING:
        data.update(
            {
                "number": code,
                "requested_test": title,
                "request_date": effective,
                "date": effective,
                "validation_date": resource.get("issued") or effective,
                "realisateur": performer,
                "validated_by": interpreter,
                "requestor": requestor,
                "resultat": conclusion,
                "conclusion": conclusion,
            }
        )
    else:
        data.update(
            {
                "name": code,
                "test": title,
                "date_requested": effective,
                "date_analysis": effective,
                "validation_date": resource.get("issued") or effective,
                "done_by": performer,
                "validated_by": interpreter,
                "requestor": requestor,
                "diagnosis": conclusion,
                "resultat": conclusion if kind == KIND_EXPLORATION else None,
            }
        )
    return kind, data


def map_invoice(resource, index):
    reference = _identifier(resource)
    total = _money(resource.get("totalGross")) or _money(resource.get("totalNet")) or 0
    state = INVOICE_STATES.get(resource.get("status"), "posted")
    due = next(
        (_money(ext.get("valueMoney")) for ext in resource.get("extension", []) if str(ext.get("url", "")).endswith("amount-due")),
        None,
    )
    if due is None:
        due = 0 if state in ("paid", "cancel") else total
    products = [
        {
            "product_name": _concept_text(item.get("chargeItemCodeableConcept")) or "Prestation",
            "quantity": str(item.get("quantity", {}).get("value", 1) if isinstance(item.get("quantity"), dict) else 1),
        }
        for item in resource.get("lineItem", [])
    ]
    return {
        "local_ref": _patient_ref(resource.get("subject") or resource.get("recipient"), index),
        "reference": reference,
        "invoice_number": reference,
        "date": resource.get("date"),
        "state": state,
        "total_amount2": total,
        "untaxed_amount": _money(resource.get("totalNet")) or total,
        "amount_to_pay": str(due),
        "amount_to_pay_today": due,
        "montant_assurance": "0",
        "montant_patient": total,
        "products": products,
    }


def process_resources(tenant, entries):
    """Enregistre les entrées d'un Bundle ({fullUrl?, resource}). Tout ou rien."""
    index = BundleIndex(entries)
    resources = [entry["resource"] for entry in entries]
    summary = {"patients": 0, "laboratoire": 0, "imagerie": 0, "exploration": 0, "facture": 0, "ignored": 0}
    new_records = []
    try:
        for position, resource in enumerate(resources):
            kind = resource.get("resourceType")
            try:
                if kind == "Patient":
                    upsert_patient(tenant, map_patient(resource), "fhir")
                    summary["patients"] += 1
                elif kind == "DiagnosticReport":
                    record_kind, data = map_diagnostic_report(resource, index)
                    record, created = upsert_record(tenant, record_kind, data, "fhir")
                    summary[record_kind] += 1
                    if created:
                        new_records.append(record)
                elif kind == "Invoice":
                    upsert_record(tenant, KIND_INVOICE, map_invoice(resource, index), "fhir")
                    summary["facture"] += 1
                else:
                    summary["ignored"] += 1  # Observation (lue via son DiagnosticReport), Encounter…
            except SaasError as err:
                raise SaasError(f"Ressource n° {position + 1} ({kind}) : {err.message}", err.status)
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
    notify_new_results(tenant, new_records)
    return summary


def resources_from_body(body):
    if not isinstance(body, dict) or not body.get("resourceType"):
        raise SaasError("Le corps doit être une ressource FHIR ou un Bundle (resourceType manquant).", 400)
    if body["resourceType"] == "Bundle":
        entries = [entry for entry in body.get("entry") or [] if isinstance(entry, dict) and isinstance(entry.get("resource"), dict)]
        if not entries:
            raise SaasError("Le Bundle ne contient aucune ressource.", 400)
        if len(entries) > 1000:
            raise SaasError("1000 ressources au maximum par Bundle.", 413)
        return entries
    return [{"resource": body}]


def operation_outcome(summary):
    parts = [f"{count} {name}" for name, count in summary.items() if count]
    return {
        "resourceType": "OperationOutcome",
        "issue": [{"severity": "information", "code": "informational", "diagnostics": "Reçu : " + (", ".join(parts) or "rien")}],
        "summary": summary,
    }
