from flask import Flask, session, g, request
from flask import current_app
from flask_jwt_extended import verify_jwt_in_request, get_jwt_identity, current_user
import jsonify
from sqlalchemy import select
from ..accounts.models import User, Role, Doctors, Patients
from Rohafya import db, tryton
from datetime import datetime
from sqlalchemy import func
from Rohafya.saas.services import default_gnuhealth_tenant, external_record_requested, gnuhealth_ref


from functools import wraps

EXEMPT_METHODS = {"OPTIONS"}

from datetime import timedelta
from flask import make_response, request, current_app
from functools import update_wrapper

def roles_required(*role_names) :
    def wrapper(view_function):

        @wraps(view_function)    # Tells debuggers that is is a function wrapper
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            try:
                identity = get_jwt_identity()
                user = User.query.get(identity)
            except : 
                return ({'message' : "Vérifiez vos accès."})

            if not user:
                return ({'message' : "User not found."})

            user_roles = [role.name for role in user.roles]
           
            role_names2 = []
            if len(role_names) == 1  and isinstance(role_names[0], list):
                role_names2 = role_names[0]
            
            if len(role_names) == 1  and isinstance(role_names, tuple):
                role_names2.append(role_names[0])

            if not any(role in user_roles for role in role_names2):
                return ({'message' : "Vous n'avez pas des droits dessus."})


            return view_function(*args, **kwargs)

        return decorator

    return wrapper


def all_facture_is_ok(func) :
        """S'ASSURER QUE LA TOTALITÉ DES FACTURES A ÉTÉ COMPLÈTEMENT PAYÉ

        Args:
            federationID (string): Le Féderation ID du patient

        Returns:
            boolean | response : True si toutes les factures ont été payées | Un Message disant de régler les factures sinon.
        """
        @wraps(func)    # Tells debuggers that is is a function wrapper
        def decorator(*args, **kwargs):
            verify_jwt_in_request()
            try:
                identity = get_jwt_identity()
                user = User.query.get(identity)
            except : 
                return ({'message' : "Vérifiez vos accès."})

            if not user:
                return ({'message' : "User not found."})
            
            patient = db.session.execute(select(Patients).filter_by(user_id=user.id)).scalar_one_or_none()
            doctor = db.session.execute(select(Doctors).filter_by(user_id=user.id)).scalar_one_or_none()
            
            federationID = None
            if patient :
                # Résultat reçu d'un autre établissement : la vue applique les règles de cet établissement.
                if external_record_requested(patient):
                    return func(*args, **kwargs)
                federationID = gnuhealth_ref(patient)
            elif doctor :
                federationID = doctor.DoctorFederationID

            from_date = datetime(2025, 12, 1, 0, 0, 0)

            invoices = []
            if  federationID and default_gnuhealth_tenant().setting("block_unpaid_results"):
                invoices = tryton.pool.get('account.invoice').search([('party.federation_account', '=', federationID),
                                                              ("create_date", ">=", from_date)])

            cond = True
            for invoice in invoices :
                if invoice.state == 'posted':
                    if float(invoice.amount_to_pay_today) > float(0.1) * float(invoice.untaxed_amount):
                        cond = False
                        break
                    else :
                        cond =  True
                elif invoice.state == 'paid':
                    cond = True
            
            if not cond:
                return {
                    "message": 
                        "Vous avez des factures impayées. "
                        "Veuillez les régler pour accéder à vos résultats. "
                        "Consultez la section 'Factures' pour plus de détails."
                    
                }, 403
            
            return func(*args, **kwargs)

        return decorator


def login_required(func):

    @wraps(func)
    def decorated_view(*args, **kwargs):
        if request.method in EXEMPT_METHODS or current_app.config.get("LOGIN_DISABLED"):
            pass
        elif not current_user.is_authenticated:
            return {"Message" : "Vous N'êtes pas connecté. "}
            #return current_app.login_manager.unauthorized()
        if callable(getattr(current_app, "ensure_sync", None)):
            return current_app.ensure_sync(func)(*args, **kwargs)
        return func(*args, **kwargs)

    return decorated_view


def require_permissions(required_perms):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user

            for perm in required_perms:
                if not user.has_permission(perm):
                    return {"error": "Permission denied"}, 403

            return f(*args, **kwargs)
        return wrapper
    return decorator

def require_any_permission(required_perms):
    def decorator(f):
        @wraps(f)
        def wrapper(*args, **kwargs):
            user = current_user

            if not any(user.has_permission(p) for p in required_perms):
                return {"error": "Permission denied"}, 403

            return f(*args, **kwargs)
        return wrapper
    return decorator
