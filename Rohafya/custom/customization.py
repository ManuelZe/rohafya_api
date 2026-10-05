from flask import Flask, session, g, jsonify, request, render_template_string, redirect, url_for
from flask_login import current_user, login_user, logout_user
from flask_user import login_required, roles_required, UserManager, signals, UserMixin
from Rohafya import db
from Rohafya.accounts.models import Role, User, Doctors
import json
from Rohafya.deco.generators import generate_random_letters, generate_random_letters_and_digits
from werkzeug.security import check_password_hash, generate_password_hash
from Rohafya.deco.decorators import roles_required, login_required
from flask_mail import Message
from Rohafya.email.email import send_email
from datetime import timedelta, datetime
from flask import current_app

from flask_jwt_extended import create_access_token, verify_jwt_in_request, set_access_cookies, get_jwt_identity, unset_jwt_cookies, create_refresh_token, set_refresh_cookies, get_jwt
from flask_jwt_extended import current_user
from Rohafya.accounts.models import UserActivity
from sqlalchemy import select, desc

class CustomUserManager(UserManager):

    def login_view(self):
        """Prepare and process the login form."""

        if request.method == 'POST' :
            # Retrieve User
            user = None
            user_email = None

            if self.USER_ENABLE_USERNAME:
                # Find user record by username
                user = self.db_manager.find_user_by_username(request.get_json()['username'])
                # Find user record by email (with form.username)

                if not user and self.USER_ENABLE_EMAIL:
                    user, user_email = self.db_manager.get_user_and_user_email_by_email(request.get_json()['username'])
            else:
                # Find user by email (with form.email)
                user, user_email = self.db_manager.get_user_and_user_email_by_email(request.get_json()['email'])

            if user and self.verify_password(request.get_json()['password'], user.password):
                # Log user in
                return self._do_login_user(user, remember_me=request.get_json()['remember_me'])

        return {"message": " Vérifiez vos paramètres de connexion et réessayez. -------- "}, 401


    def _do_login_user(self, user, remember_me=False):
        # User must have been authenticated
        if not user: return self.unauthenticated()

        login_user(user, remember=remember_me, duration=timedelta(days=7))
        # Send user_logged_in signal
        signals.user_logged_in.send(current_app._get_current_object(), user=user)
        
        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        last_activity = db.session.execute(select(UserActivity).filter_by(
            ip=ip
        ).order_by(desc(UserActivity.id))  # Triez par date décroissante
        .limit(1)).scalar_one_or_none()

        if last_activity :
            last_activity.login_time = datetime.now().isoformat()
            last_activity.user_id = user.id
            db.session.commit()

        data = user.to_dict()
        access_token = create_access_token(identity=str(user.id), additional_claims={"roles": [r.name for r in user.roles], "login_time": datetime.now().isoformat()})
        refresh_token = create_refresh_token(identity=str(user.id))
        response = jsonify({"data" : data, "access_token": access_token})

        try:
            set_access_cookies(response, access_token)
            set_refresh_cookies(response, refresh_token)
        except Exception as e:
            print("ERREUR set_access_cookies:", e)

        return response


    def logout_view(self):
        """Process the logout link."""
        """ Sign the user out."""

        # Send user_logged_out signal

        verify_jwt_in_request()

        claims = get_jwt()

        ip = request.headers.get("X-Forwarded-For", request.remote_addr)

        login_time = claims.get("login_time", None)
        signals.user_logged_out.send(current_app._get_current_object(), user=current_user)
        logout_time = datetime.now()
        format_string = "%Y-%m-%dT%H:%M:%S.%f"

        if login_time != None:
            duration = (logout_time - datetime.strptime(login_time, format_string)).total_seconds()
        else:
            duration = 0

        identity = get_jwt_identity()
        user = db.session.get(User, identity)


        # Mettre à jour la dernière activité
        last_activity = db.session.execute(select(UserActivity).filter_by(
            ip=ip
        ).order_by(desc(UserActivity.id))  # Triez par date décroissante
        .limit(1)).scalar_one_or_none()

        if last_activity:
            last_activity.logout_time = logout_time
            last_activity.session_duration = duration
            db.session.commit()


        # Use Flask-Login to sign out user
        logout_user()
    
        messages = jsonify({"Messages ": "Déconnexion Réussie. "})
        unset_jwt_cookies(messages)
        return messages


    def register_view(self):
        """ Display registration form and create new User."""

        # Process valid POST
        if request.method == 'POST':

            first_name = request.get_json()['first_name']
            last_name = request.get_json()['last_name']
            roles_user = request.get_json()['roles']
            email = request.get_json()['email']

            error = None

            username = generate_random_letters()
            password = generate_random_letters_and_digits()

            while db.session.execute(select(User).filter_by(username=username)).scalar_one_or_none() is not None :
                username = generate_random_letters()

            if not email :
                error = "Email is required."
                return {"message" : error}

            # register_form.populate_obj(user_email)

            user = User(username=username, first_name=first_name, last_name=last_name, password=self.hash_password(password), email=email)
            if db.session.execute(select(User).filter_by(email=email)).scalar_one_or_none() is not None :
                error = "Email Existe Déjà"
                return {"message" : error}

            roles = []
            for role_name in request.get_json()['roles'] :
                role = db.session.execute(select(Role).filter_by(name=role_name)).scalar_one_or_none()
                if not role :
                    return {"message": f" Le role {role_name} n'existe pas"}
                roles.append(role)

                user.roles = roles

            db.session.add(user)
            db.session.commit()

            context = {
            "first_name" : user.first_name + " " + user.last_name,
            "matricule"  : user.username,
            "email" : user.email,
            "Date" : datetime.now(),
            "username" : username,
            "password" : password
            }

            #body = "Le doctor "+DoctorName+" "+DoctorLastname+ " a pour Username : >>  "+username+" /Password >>  "+password
            body = "Contextualisation"
            subject = "PARAMETRES DE CONNEXION"
            template_html = "emails/doctor_save.html"
            try :
                send_email(user.email, subject, body, template_html, **context)
            except :
                db.session.delete(user)
                db.session.commit()
                return {"message" : " L'email entré n'est pas correct"}

            # Send user_registered signal
            signals.user_registered.send(current_app._get_current_object(),
                                         user=user)

            # Auto-login after register or redirect to login page
            messages = json.dumps({"messages ": " Enregistrement effectué avec succès. "})
            return messages


