"""Abonnements des établissements : consommation mensuelle, alertes de volume, factures.

Règles (document de vente) :
- frais de mise en service payés une fois (offerts aux établissements fondateurs), puis un abonnement ;
- abonnement payé d'avance, au mois, au trimestre ou à l'année (dix mois payés pour douze) ;
- le volume est le nombre de résultats publiés dans le mois (laboratoire, imagerie, exploration) ;
- la publication n'est jamais bloquée : au-delà du volume de la formule, l'établissement et les
  super-administrateurs sont prévenus, et le changement de formule constaté sur trois mois
  consécutifs s'applique au trimestre suivant (décision du super-administrateur) ;
- un mois offert par établissement recommandé qui s'abonne (parrainage).
"""
import calendar
import datetime
import os

from sqlalchemy import func, select

from Rohafya import db
from Rohafya.accounts.models import Role, User, UserRoles
from . import i18n
from .constants import (
    BILLING_CYCLES,
    INVOICE_CANCELLED,
    INVOICE_DUE_DAYS,
    INVOICE_ISSUED,
    INVOICE_PAID,
    INVOICE_STATUSES,
    KIND_EXPLORATION,
    KIND_IMAGING,
    KIND_LAB,
    PLAN_CHANGE_MONTHS,
    PLAN_ORDER,
    PLAN_RESEAU,
    PLANS,
    SOURCE_GNUHEALTH,
    SUB_ACTIVE,
    SUB_STATUSES,
    SUB_TRIAL,
    SUPER_ADMIN_ROLES,
    TRIAL_DAYS,
    USAGE_ALERT_THRESHOLDS,
)
from .models import Tenant, TenantInvoice, TenantMember, TenantSubscription, UsageAlert, UsageEvent
from .services import SaasError, audit, front_url, notify_user, now, send_mail

RESULT_KINDS = (KIND_LAB, KIND_IMAGING, KIND_EXPLORATION)


# =====================================================================
# Dates
# =====================================================================

def today():
    return now().date()


def period_of(day):
    return f"{day.year:04d}-{day.month:02d}"


def add_months(day, months):
    month_index = day.month - 1 + months
    year, month = day.year + month_index // 12, month_index % 12 + 1
    return datetime.date(year, month, min(day.day, calendar.monthrange(year, month)[1]))


def last_periods(count, day=None):
    """Les `count` derniers mois, du plus ancien au mois en cours."""
    first = (day or today()).replace(day=1)
    return [period_of(add_months(first, -offset)) for offset in range(count - 1, -1, -1)]


def next_quarter_start(day=None):
    day = day or today()
    month = ((day.month - 1) // 3 + 1) * 3 + 1
    return datetime.date(day.year + (month - 1) // 12, (month - 1) % 12 + 1, 1)


def _parse_date(value, field):
    if value in (None, ""):
        return None
    try:
        return datetime.date.fromisoformat(str(value)[:10])
    except ValueError:
        raise SaasError(f"Date invalide pour {field} (format AAAA-MM-JJ).", 400)


# =====================================================================
# Abonnement
# =====================================================================

def subscription_of(tenant):
    """Abonnement de l'établissement, créé à la première lecture : essai de 60 jours, formule Standard.

    L'établissement GNU Health historique (PDMD Santé) n'est pas facturé.
    """
    sub = db.session.execute(select(TenantSubscription).filter_by(tenant_id=tenant.id)).scalar_one_or_none()
    if sub is None:
        start = (tenant.created_at or now()).date()
        gnuhealth = tenant.source_type == SOURCE_GNUHEALTH
        sub = TenantSubscription(
            tenant_id=tenant.id,
            plan="standard",
            status=SUB_ACTIVE if gnuhealth else SUB_TRIAL,
            billing_cycle="mensuel",
            billed=not gnuhealth,
            trial_ends_at=None if gnuhealth else start + datetime.timedelta(days=TRIAL_DAYS),
        )
        db.session.add(sub)
        db.session.flush()
    return sub


def plan_terms(sub):
    plan = PLANS.get(sub.plan, PLANS["standard"])
    return {
        "quota": sub.custom_quota if sub.custom_quota is not None else plan["quota"],
        "monthly": sub.custom_monthly_price if sub.custom_monthly_price is not None else plan["monthly"],
        "setup": sub.custom_setup_fee if sub.custom_setup_fee is not None else plan["setup"],
    }


def subscription_dict(sub):
    terms = plan_terms(sub)
    months, paid = BILLING_CYCLES.get(sub.billing_cycle, (1, 1))
    trial_left = (sub.trial_ends_at - today()).days if sub.trial_ends_at and sub.status == SUB_TRIAL else None
    return {
        "tenant_id": sub.tenant_id,
        "plan": sub.plan,
        "plan_label": PLANS.get(sub.plan, {}).get("label", sub.plan),
        "status": sub.status,
        "billing_cycle": sub.billing_cycle,
        "billed": sub.billed,
        "trial_ends_at": sub.trial_ends_at.isoformat() if sub.trial_ends_at else None,
        "trial_days_left": trial_left,
        "next_invoice_on": sub.next_invoice_on.isoformat() if sub.next_invoice_on else None,
        "quota": terms["quota"],
        "monthly_price": terms["monthly"],
        "setup_fee": terms["setup"],
        "cycle_months": months,
        "cycle_price": terms["monthly"] * paid,
        "custom_quota": sub.custom_quota,
        "custom_monthly_price": sub.custom_monthly_price,
        "custom_setup_fee": sub.custom_setup_fee,
        "setup_fee_waived": sub.setup_fee_waived,
        "setup_invoiced": sub.setup_invoiced,
        "free_months": sub.free_months,
        "note": sub.note,
    }


def _int_or_none(value, field):
    if value in (None, ""):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise SaasError(f"{field} doit être un nombre entier.", 400)
    if number < 0:
        raise SaasError(f"{field} ne peut pas être négatif.", 400)
    return number


def update_subscription(tenant, data, actor_id):
    sub = subscription_of(tenant)
    before = sub.status
    if "plan" in data:
        if data["plan"] not in PLANS:
            raise SaasError("Formule inconnue.", 400)
        sub.plan = data["plan"]
    if "status" in data:
        if data["status"] not in SUB_STATUSES:
            raise SaasError("Statut d'abonnement inconnu.", 400)
        sub.status = data["status"]
    if "billing_cycle" in data:
        if data["billing_cycle"] not in BILLING_CYCLES:
            raise SaasError("Rythme de paiement inconnu : mensuel, trimestriel ou annuel.", 400)
        sub.billing_cycle = data["billing_cycle"]
    if "billed" in data:
        sub.billed = bool(data["billed"])
    if "setup_fee_waived" in data:
        sub.setup_fee_waived = bool(data["setup_fee_waived"])
    for field, label in (("custom_quota", "Le volume"), ("custom_monthly_price", "Le prix mensuel"), ("custom_setup_fee", "Les frais de mise en service")):
        if field in data:
            setattr(sub, field, _int_or_none(data[field], label))
    if "free_months" in data:
        sub.free_months = _int_or_none(data["free_months"], "Le nombre de mois offerts") or 0
    if "trial_ends_at" in data:
        sub.trial_ends_at = _parse_date(data["trial_ends_at"], "la fin d'essai")
    if "next_invoice_on" in data:
        sub.next_invoice_on = _parse_date(data["next_invoice_on"], "la prochaine facture")
    if "note" in data:
        note = str(data.get("note") or "").strip()
        if len(note) > 2000:
            raise SaasError("La note ne doit pas dépasser 2000 caractères.", 400)
        sub.note = note or None
    if sub.plan == PLAN_RESEAU and sub.custom_quota is None:
        raise SaasError("Formule Réseau ou hôpital : indiquez le volume mensuel convenu.", 400)
    # Passage à l'abonnement : la première facture (mise en service + première période) part aujourd'hui.
    if sub.status == SUB_ACTIVE and before != SUB_ACTIVE and sub.next_invoice_on is None and sub.billed:
        sub.next_invoice_on = today()
    sub.updated_at = now()
    audit("subscription.updated", tenant_id=tenant.id, user_id=actor_id, target=PLANS[sub.plan]["label"],
          details={"status": sub.status, "plan": sub.plan, "cycle": sub.billing_cycle}, commit=False)
    db.session.commit()
    return sub


# =====================================================================
# Consommation
# =====================================================================

def record_usage(tenant_id, kind, code, when=None):
    """Compte un résultat publié (appelé à sa première réception, dans la transaction de l'envoi)."""
    if kind not in RESULT_KINDS or not code:
        return
    period = period_of(when or today())
    exists = db.session.execute(
        select(UsageEvent.id).filter_by(tenant_id=tenant_id, kind=kind, code=code, period=period)
    ).first()
    if not exists:
        db.session.add(UsageEvent(tenant_id=tenant_id, kind=kind, code=code, period=period))


def usage_counts(tenant_id, periods):
    rows = db.session.execute(
        select(UsageEvent.period, func.count(UsageEvent.id))
        .filter(UsageEvent.tenant_id == tenant_id, UsageEvent.period.in_(periods))
        .group_by(UsageEvent.period)
    ).all()
    counts = dict(rows)
    return {period: counts.get(period, 0) for period in periods}


def _percent(count, quota):
    return round(count * 100 / quota) if quota else None


def plan_recommendation(sub, history):
    """Formule adaptée si les trois derniers mois complets sortent tous de la formule actuelle."""
    if sub.plan == PLAN_RESEAU or not sub.billed:
        return None
    complete = history[-(PLAN_CHANGE_MONTHS + 1):-1]
    if len(complete) < PLAN_CHANGE_MONTHS:
        return None
    counts = [month["count"] for month in complete]
    quota = plan_terms(sub)["quota"]
    peak = max(counts)
    fitting = next((code for code in PLAN_ORDER if PLANS[code]["quota"] is None or PLANS[code]["quota"] >= peak), PLAN_RESEAU)
    current_rank, fitting_rank = PLAN_ORDER.index(sub.plan), PLAN_ORDER.index(fitting)
    if min(counts) > quota and fitting_rank > current_rank:
        direction = "up"
    elif fitting_rank < current_rank and all(count <= PLANS[fitting]["quota"] for count in counts):
        direction = "down"
    else:
        return None
    return {
        "plan": fitting,
        "plan_label": PLANS[fitting]["label"],
        "direction": direction,
        "months": complete,
        "applies_from": next_quarter_start().isoformat(),
    }


def usage_summary(tenant, months=12):
    sub = subscription_of(tenant)
    periods = last_periods(months)
    counts = usage_counts(tenant.id, periods)
    history = [{"period": period, "count": counts[period]} for period in periods]
    quota = plan_terms(sub)["quota"]
    current = history[-1]["count"]
    return {
        "subscription": subscription_dict(sub),
        "current": {"period": periods[-1], "count": current, "quota": quota, "percent": _percent(current, quota)},
        "history": history,
        "recommendation": plan_recommendation(sub, history),
    }


# =====================================================================
# Destinataires des alertes
# =====================================================================

def tenant_admins(tenant):
    return db.session.execute(
        select(User).join(TenantMember, TenantMember.user_id == User.id)
        .filter(TenantMember.tenant_id == tenant.id, User.active.is_(True))
    ).scalars().all()


def super_admins():
    return db.session.execute(
        select(User).join(UserRoles, UserRoles.user_id == User.id).join(Role, Role.id == UserRoles.role_id)
        .filter(Role.name.in_(SUPER_ADMIN_ROLES), User.active.is_(True))
    ).scalars().unique().all()


def check_usage_alerts(tenant):
    """Après un envoi : prévient une fois par mois à 80 % puis à 100 % du volume de la formule."""
    sub = subscription_of(tenant)
    quota = plan_terms(sub)["quota"]
    if not sub.billed or not quota:
        db.session.commit()
        return None
    period = period_of(today())
    count = usage_counts(tenant.id, [period])[period]
    reached = [threshold for threshold in USAGE_ALERT_THRESHOLDS if count * 100 >= threshold * quota]
    sent = set(db.session.execute(select(UsageAlert.threshold).filter_by(tenant_id=tenant.id, period=period)).scalars())
    new = [threshold for threshold in reached if threshold not in sent]
    if not new:
        db.session.commit()
        return None
    for threshold in new:
        db.session.add(UsageAlert(tenant_id=tenant.id, period=period, threshold=threshold))
    db.session.commit()
    percent = _percent(count, quota)
    params = {"establishment": tenant.display_name, "count": count, "quota": quota,
              "plan": PLANS[sub.plan]["label"], "percent": percent}
    for user in tenant_admins(tenant) + super_admins():
        notify_user(user.id, "usage.alert.title", "usage.alert.message", **params)
    for user in tenant_admins(tenant):
        if user.email:
            send_mail(user.email, "mail.usage_alert", "emails/alerte_volume.html", locale=i18n.user_locale(user.id),
                      open_url=front_url() + "/admin/abonnement", **params)
    return max(new)


# =====================================================================
# Factures
# =====================================================================

def issuer():
    """Mentions de l'émetteur des factures (variables d'environnement du conteneur de l'API)."""
    return {
        "name": os.environ.get("ROHAFYA_INVOICE_NAME", "ROHAFYA"),
        "address": os.environ.get("ROHAFYA_INVOICE_ADDRESS", ""),
        "tax_id": os.environ.get("ROHAFYA_INVOICE_TAX_ID", ""),
        "registry": os.environ.get("ROHAFYA_INVOICE_RCCM", ""),
        "contact": os.environ.get("ROHAFYA_INVOICE_CONTACT", ""),
        "payment": os.environ.get("ROHAFYA_INVOICE_PAYMENT", ""),
    }


def invoice_dict(invoice):
    data = invoice.to_dict()
    data["overdue"] = invoice.status == INVOICE_ISSUED and invoice.due_on < today()
    data["plan_label"] = PLANS.get(invoice.plan or "", {}).get("label", invoice.plan)
    return data


def _invoice_number(day):
    prefix = f"ROH-{day:%Y%m}-"
    count = db.session.execute(select(func.count(TenantInvoice.id)).filter(TenantInvoice.number.like(prefix + "%"))).scalar()
    return f"{prefix}{count + 1:04d}"


def build_lines(sub, start):
    """Lignes d'une facture : abonnement de la période, mois offerts, mise en service (une seule fois).

    Les libellés sont produits par le front, dans la langue du lecteur, à partir de `type`.
    """
    terms = plan_terms(sub)
    months, paid = BILLING_CYCLES[sub.billing_cycle]
    lines = [{"type": "subscription", "plan": sub.plan, "cycle": sub.billing_cycle, "months": months,
              "quantity": paid, "unit_price": terms["monthly"], "amount": paid * terms["monthly"]}]
    free = min(sub.free_months or 0, paid)
    if free:
        lines.append({"type": "free_month", "quantity": free, "unit_price": -terms["monthly"], "amount": -free * terms["monthly"]})
        sub.free_months -= free
    if not sub.setup_invoiced:
        if sub.setup_fee_waived:
            lines.append({"type": "setup_waived", "quantity": 1, "unit_price": 0, "amount": 0})
        else:
            lines.append({"type": "setup", "quantity": 1, "unit_price": terms["setup"], "amount": terms["setup"]})
        sub.setup_invoiced = True
    return lines


def issue_invoice(tenant, sub, start, issued=None):
    issued = issued or today()
    months, _ = BILLING_CYCLES[sub.billing_cycle]
    lines = build_lines(sub, start)
    invoice = TenantInvoice(
        number=_invoice_number(issued),
        tenant_id=tenant.id,
        establishment=tenant.display_name,
        plan=sub.plan,
        billing_cycle=sub.billing_cycle,
        period_start=start,
        period_end=add_months(start, months) - datetime.timedelta(days=1),
        lines=lines,
        total=sum(line["amount"] for line in lines),
        status=INVOICE_ISSUED,
        issued_on=issued,
        due_on=max(issued, start) + datetime.timedelta(days=INVOICE_DUE_DAYS),
    )
    db.session.add(invoice)
    db.session.flush()
    return invoice


def issue_due_invoices(day=None, tenant_id=None):
    """Émet les factures arrivées à échéance (abonnements actifs et facturés). Idempotent pour un même jour."""
    day = day or today()
    stmt = select(TenantSubscription).filter(
        TenantSubscription.status == SUB_ACTIVE,
        TenantSubscription.billed.is_(True),
        TenantSubscription.next_invoice_on.isnot(None),
        TenantSubscription.next_invoice_on <= day,
    )
    if tenant_id:
        stmt = stmt.filter(TenantSubscription.tenant_id == tenant_id)
    issued = []
    for sub in db.session.execute(stmt).scalars().all():
        tenant = db.session.get(Tenant, sub.tenant_id)
        if tenant is None:
            continue
        while sub.next_invoice_on and sub.next_invoice_on <= day:
            months, _ = BILLING_CYCLES[sub.billing_cycle]
            invoice = issue_invoice(tenant, sub, sub.next_invoice_on, issued=day)
            sub.next_invoice_on = add_months(sub.next_invoice_on, months)
            issued.append((tenant, invoice))
            audit("invoice.issued", tenant_id=tenant.id, target=invoice.number, details={"total": invoice.total}, commit=False)
    db.session.commit()
    for tenant, invoice in issued:
        _announce_invoice(tenant, invoice)
    return [invoice for _, invoice in issued]


def _announce_invoice(tenant, invoice):
    for user in tenant_admins(tenant):
        locale = i18n.user_locale(user.id)
        params = {
            "number": invoice.number,
            "establishment": tenant.display_name,
            "period": f"{invoice.period_start:%d/%m/%Y} – {invoice.period_end:%d/%m/%Y}",
            "total": i18n.format_amount(invoice.total, locale),
            "due": f"{invoice.due_on:%d/%m/%Y}",
        }
        notify_user(user.id, i18n.text("invoice.title", locale, **params), i18n.text("invoice.message", locale, **params))
        if user.email:
            send_mail(user.email, "mail.invoice", "emails/facture.html", locale=locale,
                      open_url=front_url() + "/admin/abonnement", **params)


def set_invoice_status(invoice, data, actor_id):
    status = data.get("status")
    if status not in INVOICE_STATUSES:
        raise SaasError("Statut de facture inconnu : emise, payee ou annulee.", 400)
    if invoice.status == INVOICE_CANCELLED and status != INVOICE_CANCELLED:
        raise SaasError("Une facture annulée ne peut pas être rouverte : émettez-en une nouvelle.", 400)
    invoice.status = status
    invoice.paid_on = (_parse_date(data.get("paid_on"), "la date de paiement") or today()) if status == INVOICE_PAID else None
    if "payment_reference" in data:
        invoice.payment_reference = (str(data.get("payment_reference") or "").strip()[:120]) or None
    if "note" in data:
        invoice.note = (str(data.get("note") or "").strip()[:2000]) or None
    invoice.updated_at = now()
    audit(f"invoice.{status}", tenant_id=invoice.tenant_id, user_id=actor_id, target=invoice.number, commit=False)
    db.session.commit()
    return invoice


def invoices_page(tenant_id=None, status=None, page=1, size=25):
    stmt = select(TenantInvoice)
    if tenant_id:
        stmt = stmt.filter(TenantInvoice.tenant_id == tenant_id)
    if status == "overdue":
        stmt = stmt.filter(TenantInvoice.status == INVOICE_ISSUED, TenantInvoice.due_on < today())
    elif status:
        stmt = stmt.filter(TenantInvoice.status == status)
    total = db.session.execute(select(func.count()).select_from(stmt.subquery())).scalar()
    rows = db.session.execute(
        stmt.order_by(TenantInvoice.issued_on.desc(), TenantInvoice.id.desc()).offset((page - 1) * size).limit(size)
    ).scalars().all()
    return {"items": [invoice_dict(row) for row in rows], "total": total, "page": page, "page_size": size}


def unpaid_by_tenant():
    rows = db.session.execute(
        select(TenantInvoice.tenant_id, func.count(TenantInvoice.id), func.coalesce(func.sum(TenantInvoice.total), 0))
        .filter(TenantInvoice.status == INVOICE_ISSUED).group_by(TenantInvoice.tenant_id)
    ).all()
    overdue = dict(db.session.execute(
        select(TenantInvoice.tenant_id, func.count(TenantInvoice.id))
        .filter(TenantInvoice.status == INVOICE_ISSUED, TenantInvoice.due_on < today()).group_by(TenantInvoice.tenant_id)
    ).all())
    return {tenant_id: {"count": count, "amount": int(amount), "overdue": overdue.get(tenant_id, 0)} for tenant_id, count, amount in rows}


def billing_overview():
    """Console super-administrateur : formule, volume du mois et impayés de chaque établissement."""
    tenants = db.session.execute(select(Tenant).order_by(Tenant.name)).scalars().all()
    period = period_of(today())
    unpaid = unpaid_by_tenant()
    rows, mrr, trials, ending = [], 0, 0, 0
    for tenant in tenants:
        sub = subscription_of(tenant)
        terms = plan_terms(sub)
        count = usage_counts(tenant.id, [period])[period]
        data = subscription_dict(sub)
        if sub.billed and sub.status == SUB_ACTIVE:
            mrr += terms["monthly"]
        if sub.status == SUB_TRIAL:
            trials += 1
            if data["trial_days_left"] is not None and data["trial_days_left"] <= 7:
                ending += 1
        rows.append({
            "tenant_id": tenant.id,
            "establishment": tenant.display_name,
            "is_active": tenant.is_active,
            "subscription": data,
            "usage": {"period": period, "count": count, "quota": terms["quota"], "percent": _percent(count, terms["quota"])},
            "unpaid": unpaid.get(tenant.id, {"count": 0, "amount": 0, "overdue": 0}),
        })
    db.session.commit()
    return {
        "tenants": rows,
        "totals": {
            "monthly_recurring": mrr,
            "trials": trials,
            "trials_ending": ending,
            "unpaid_amount": sum(item["amount"] for item in unpaid.values()),
            "overdue": sum(item["overdue"] for item in unpaid.values()),
        },
    }


# =====================================================================
# Installation et reprise de l'existant
# =====================================================================

def backfill():
    """`flask saas init` : un abonnement par établissement, et le volume des résultats déjà reçus."""
    from .models import TenantRecord

    created = 0
    for tenant in db.session.execute(select(Tenant)).scalars().all():
        if db.session.execute(select(TenantSubscription.id).filter_by(tenant_id=tenant.id)).first() is None:
            subscription_of(tenant)
            created += 1
    counted = 0
    if db.session.execute(select(func.count(UsageEvent.id))).scalar() == 0:
        records = db.session.execute(select(TenantRecord).filter(TenantRecord.kind.in_(RESULT_KINDS))).scalars().all()
        for record in records:
            db.session.add(UsageEvent(tenant_id=record.tenant_id, kind=record.kind, code=record.code,
                                      period=period_of((record.created_at or now()).date())))
            counted += 1
    db.session.commit()
    return created, counted


def trial_reminders(day=None):
    """Prévient les super-administrateurs 7 jours avant la fin d'un essai, puis le jour même (une seule fois)."""
    day = day or today()
    reminded = 0
    subs = db.session.execute(select(TenantSubscription).filter_by(status=SUB_TRIAL)).scalars().all()
    for sub in subs:
        if not sub.trial_ends_at:
            continue
        left = (sub.trial_ends_at - day).days
        marker = 7 if left == 7 else 0 if left <= 0 else None
        if marker is None:
            continue
        # Rappels rangés avec les alertes de volume : seuil -7 (J-7) ou -1 (fin), mois de fin d'essai.
        period, threshold = period_of(sub.trial_ends_at), -7 if marker else -1
        if db.session.execute(select(UsageAlert.id).filter_by(tenant_id=sub.tenant_id, period=period, threshold=threshold)).first():
            continue
        db.session.add(UsageAlert(tenant_id=sub.tenant_id, period=period, threshold=threshold))
        db.session.commit()
        tenant = db.session.get(Tenant, sub.tenant_id)
        for user in super_admins():
            locale = i18n.user_locale(user.id)
            if locale == "en":
                title = f"Trial {'ends in 7 days' if marker else 'ended'} · {tenant.display_name}"
                message = f"The trial of {tenant.display_name} {'ends on' if marker else 'ended on'} {sub.trial_ends_at:%d/%m/%Y}. Convert it to a subscription or extend it from the Billing page."
            else:
                title = f"Essai {'terminé dans 7 jours' if marker else 'terminé'} · {tenant.display_name}"
                message = f"L'essai de {tenant.display_name} {'se termine le' if marker else 's’est terminé le'} {sub.trial_ends_at:%d/%m/%Y}. Passez-le en abonnement ou prolongez-le depuis la page Facturation."
            notify_user(user.id, title, message)
        reminded += 1
    return reminded
