#  LA FONCTION PERMETTANT LE CHARGEMENT DES DOCTEURS DANS BASE DE DONNÉES DOCTEUR
from flask import Blueprint, jsonify, request, current_app
from flask_jwt_extended import jwt_required
from flask_tryton import Tryton
from ..deco.decorators import roles_required, require_any_permission
from flask_cors import CORS, cross_origin
from ..deco.generators import generate_random_email, generate_unique_numbers
from datetime import datetime
from werkzeug.security import check_password_hash, generate_password_hash
# Assure-toi d'importer UserDeletedNotifications ici
from ..accounts.models import Etiquettes_notifications, Notifications, User, UserDeletedNotifications
from ..email.email import send_email
from Rohafya import db, tryton
from sqlalchemy import select, and_, exists, or_


general_notification = Blueprint('notifications', __name__, url_prefix='/notifications/')


@general_notification.route('add', methods=["POST"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_general.add_notification"])
def add_notifications():
    """AJOUTER DE NOUVELLES NOTIFICATIONS MANUELLEMENT DANS LA TABLE NOTIFICATIONS"""
    if request.method == "POST":
        data = request.get_json()
        types = data.get('types', None)
        title = data['title']
        message = data['message']
        created_at = datetime.now()
        all_users = data.get('all_users', None)
        user_id = data.get('user_id', None)

        notification = Notifications(
            types=types,
            title=title,
            message=message,
            created_at=created_at,
            all_users=all_users,
            user_id=user_id if not all_users else None
        )

        etiquettes = []
        if data.get('etiquettes'):
            for etiquette_name in data.get('etiquettes', []):
                etiquette = db.session.execute(select(Etiquettes_notifications).filter_by(name=etiquette_name)).scalar_one_or_none()
                if etiquette:
                    etiquettes.append(etiquette)
            
        notification.etiquettes = etiquettes

        db.session.add(notification)
        db.session.commit()

        return {"Notifications" : notification.to_dict()}


@general_notification.route('all', methods=["GET"])
@tryton.transaction()
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_general.all_notifications"])
def all_notifications():
    """RÉCUPÉRER TOUTES LES NOTIFICATIONS (Vue Admin : voit tout, même ce qui est supprimé par les users)"""
    notifications = db.session.execute(select(Notifications)).scalars().all()
    notifications_list = [notification.to_dict() for notification in notifications]

    return jsonify(notifications_list)


@general_notification.route('del/<int:notification_id>', methods=["DELETE"])
@cross_origin(supports_credentials=True)
# @roles_required("Admin")
@jwt_required()
@require_any_permission(["administration.notifications_general.delete_notification"])
def del_notifications(notification_id):
    """SUPPRIMER DÉFINITIVEMENT UNE NOTIFICATION (Action Admin)"""
    
    if notification_id:
        notification = db.session.execute(select(Notifications).filter_by(id=notification_id)).scalar_one_or_none()
    else:
        return {"Message": "L'ID de la notification doit être fourni."}

    if not notification:
        return {"Message": "Aucune Notification trouvée."}

    # Cette suppression retire la notification pour TOUT LE MONDE (DB hard delete)
    db.session.delete(notification)
    db.session.commit()

    return {"Message": "Notification supprimée définitivement avec succès."}


@general_notification.route('user/<int:user_id>/', methods=["GET"])
@cross_origin(supports_credentials=True)
# @roles_required(["Admin", "Doctor", "Patient"])
@jwt_required()
@require_any_permission(["administration.notifications_general.add_notification",
                         "patients.notifications_general.add_notification",
                         "doctors.notifications_general.add_notification"])
def user_notifications(user_id):
    """
    RÉCUPÉRER TOUTES LES NOTIFICATIONS D'UN UTILISATEUR
    (Exclut celles que l'utilisateur a supprimées localement)
    """
    
    # 1. Sélectionner les notifications qui sont soit pour l'utilisateur spécifique, soit pour tous (all_users)
    # 2. ET qui ne sont PAS dans la table UserDeletedNotifications pour cet user_id
    
    stmt = select(Notifications).where(
        or_(
            Notifications.user_id == user_id,
            Notifications.all_users == True
        )
    ).where(
        ~exists().where(
            and_(
                UserDeletedNotifications.notification_id == Notifications.id,
                UserDeletedNotifications.user_id == user_id
            )
        )
    )

    notifications = db.session.execute(stmt).scalars().all()
    notifications_list = [notification.to_dict() for notification in notifications]

    return jsonify(notifications_list)


@general_notification.route('user/del/<int:notification_id>', methods=["DELETE"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.delete_notification",
                         "patients.notifications_general.delete_notification",
                         "doctors.notifications_general.delete_notification"])
def delete_single_user_notification(notification_id):
    """
    L'UTILISATEUR SUPPRIME UNE NOTIFICATION (SOFT DELETE)
    Elle reste en base pour l'admin, mais disparait pour l'utilisateur.
    """
    # Récupérer l'ID utilisateur depuis le token ou la session (C'est plus sécurisé que de le passer en paramètre, 
    # mais ici j'adapte selon ton architecture actuelle. Supposons que tu récupères l'ID utilisateur courant)
    # Pour l'instant, comme ta route n'a pas user_id, je suppose que tu l'extrais du contexte ou que tu devrais l'ajouter dans l'URL.
    # ATTENTION: Ta route originale prenait `user_id` en argument de fonction mais `<int:notification_id>` dans l'url.
    # Je vais supposer que l'utilisateur est identifié via le token JWT (current_user) ou que tu dois passer l'user_id.
    
    # CORRECTION : Je vais modifier la route pour inclure user_id pour correspondre à ta logique précédente
    pass

# JE RÉÉCRIS LA ROUTE CORRECTEMENT AVEC user_id ET notification_id
@general_notification.route('user/<int:user_id>/del/<int:notification_id>', methods=["DELETE"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.delete_notification",
                         "patients.notifications_general.delete_notification",
                         "doctors.notifications_general.delete_notification"])
def user_delete_notification(user_id, notification_id):
    
    notification = db.session.execute(select(Notifications).filter_by(id=notification_id)).scalar_one_or_none()
    
    if not notification:
        return {"Message": "Notification introuvable."}

    # Vérifier si l'utilisateur a le droit de supprimer cette notif
    if notification.user_id != user_id and not notification.all_users:
         return {"Message": "Vous n'avez pas le droit de supprimer cette notification."}

    # Vérifier si déjà supprimée
    already_deleted = db.session.execute(
        select(UserDeletedNotifications).filter_by(user_id=user_id, notification_id=notification_id)
    ).scalar_one_or_none()

    if not already_deleted:
        # On ajoute une entrée dans la table de suppression
        soft_delete = UserDeletedNotifications(user_id=user_id, notification_id=notification_id)
        db.session.add(soft_delete)
        db.session.commit()

    return {"Message": "Notification supprimée de votre liste."}


@general_notification.route('user/<int:user_id>/del_all', methods=["DELETE"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.delete_all_user_notification",
                         "patients.notifications_general.delete_all_user_notification",
                         "doctors.notifications_general.delete_all_user_notification"])
def delete_all_user_notifications(user_id):
    """L'UTILISATEUR SUPPRIME TOUTES SES NOTIFICATIONS (SOFT DELETE)"""
    
    # On récupère toutes les notifs visibles pour cet user
    stmt = select(Notifications).where(
        or_(
            Notifications.user_id == user_id,
            Notifications.all_users == True
        )
    )
    notifications = db.session.execute(stmt).scalars().all()

    if not notifications:
        return {"Message": "Aucune notification à supprimer."}

    count = 0
    for notif in notifications:
        # Vérifier si pas déjà supprimée
        exists_check = db.session.execute(
            select(UserDeletedNotifications).filter_by(user_id=user_id, notification_id=notif.id)
        ).scalar_one_or_none()
        
        if not exists_check:
            soft_delete = UserDeletedNotifications(user_id=user_id, notification_id=notif.id)
            db.session.add(soft_delete)
            count += 1
    
    db.session.commit()

    return {"Message": f"{count} notifications supprimées avec succès."}


@general_notification.route('all_users', methods=["GET"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.all_user_notifications",
                         "patients.notifications_general.all_user_notifications",
                         "doctors.notifications_general.all_user_notifications"])
def all_users_notifications():
    """RÉCUPÉRER TOUTES LES NOTIFICATIONS DESTINÉES À TOUS LES UTILISATEURS"""
    notifications = db.session.execute(select(Notifications).filter_by(all_users=True)).scalars().all()
    notifications_list = [notification.to_dict() for notification in notifications]

    return jsonify(notifications_list)


@general_notification.route('send_email/<int:notification_id>/all', methods=["POST"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required("Admin")
@require_any_permission(["administration.notifications_general.send_email_notification"])
def send_email_all_users(notification_id):
    """ENVOYER UNE NOTIFICATION PAR EMAIL À TOUS LES UTILISATEURS"""
    notification = db.session.execute(select(Notifications).filter_by(id=notification_id)).scalar_one_or_none()
    if not notification:
        return {"Message": "Aucune Notification trouvée."}

    if not notification.all_users:
        return {"Message": "Cette notification n'est pas destinée à tous les utilisateurs."}

    users = db.session.execute(select(User).filter_by(active=True)).scalars().all()
    user_emails = [user.email for user in users if user.email]

    if not user_emails:
        return {"Message": "Aucun utilisateur avec email trouvé."}

    subject = notification.title
    body = notification.message

    send_email(subject, user_emails, body)

    return {"Message": "Notification envoyée par email à tous les utilisateurs."}


@general_notification.route('modify/<int:notification_id>', methods=["PUT"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required("Admin")
@require_any_permission(["administration.notifications_general.modify_notification"])
def modify_notification(notification_id):
    """MODIFIER UNE NOTIFICATION"""
    notification = db.session.execute(select(Notifications).filter_by(id=notification_id)).scalar_one_or_none()
    if not notification:
        return {"Message": "Aucune Notification trouvée."}

    data = request.get_json()

    if request.method == "PUT":
        types = data.get('types', notification.types)
        title = data.get('title', notification.title)
        message = data.get('message', notification.message)
        
        if 'all_users' in data:
            all_users = data['all_users']
            user_id = None if all_users else data.get('user_id', notification.user_id)
        else:
            all_users = notification.all_users
            user_id = notification.user_id
        
        if data.get('etiquettes') is not None:
            # On vide d'abord ou on ajoute ? Ici logique de remplacement/ajout selon besoin.
            # Pour faire simple, on garde la logique d'ajout
            for etiquette_name in data.get('etiquettes', []):
                etiquette = db.session.execute(select(Etiquettes_notifications).filter_by(name=etiquette_name)).scalar_one_or_none()
                if etiquette:
                    if etiquette not in notification.etiquettes:
                        notification.etiquettes.append(etiquette)

        notification.types = types
        notification.title = title
        notification.message = message
        notification.all_users = all_users
        notification.user_id = user_id
        notification.updated_at = datetime.now()

        db.session.commit()

    return {"Notifications" : notification.to_dict()}
    

@general_notification.route('mark_read/<int:notification_id>', methods=["PUT"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.mark_notification_as_read",
                         "patients.notifications_general.mark_notification_as_read",
                         "doctors.notifications_general.mark_notification_as_read"])
def mark_notification_as_read(notification_id):
    """MARQUER UNE NOTIFICATION COMME LUE"""
    notification = db.session.execute(select(Notifications).filter_by(id=notification_id)).scalar_one_or_none()
    if not notification:
        return {"Message": "Aucune Notification trouvée."}

    # NOTE: Pour 'all_users', marquer comme lu ici marque comme lu pour tout le monde 
    # si on utilise le champ is_read de la table Notifications. 
    # Idéalement, il faudrait aussi une table UserReadNotifications, 
    # mais pour respecter ta demande, je laisse tel quel.
    
    if request.method == "PUT":
        notification.read_at = datetime.now()
        notification.is_read = True
        db.session.commit()

        return notification.to_dict()
    

# AVOIR TOUTES LES NOTIFICATIONS D'UN TYPE SPÉCIFIQUE
@general_notification.route('type/<int:type_id>', methods=["GET"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.notification_by_type",
                         "patients.notifications_general.notification_by_type",
                         "doctors.notifications_general.notification_by_type"])
def notifications_by_type(type_id):
    notifications = db.session.execute(select(Notifications).filter_by(types=type_id)).scalars().all()
    notifications_list = [notification.to_dict() for notification in notifications]

    return jsonify(notifications_list)


# AVOIR TOUTES LES NOTIFICATIONS D'UNE ÉTIQUETTE SPÉCIFIQUE
@general_notification.route('etiquette/<int:etiquette_id>', methods=["GET"])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(["Admin", "Doctor", "Patient"])
@require_any_permission(["administration.notifications_general.notification_by_etiquette",
                         "patients.notifications_general.notification_by_etiquette",
                         "doctors.notifications_general.notification_by_etiquette"])
def notifications_by_etiquette(etiquette_id):
    # Optimisation de la requête avec un JOIN direct
    stmt = select(Notifications).join(Notifications.etiquettes).filter(Etiquettes_notifications.id == etiquette_id)
    notifications = db.session.execute(stmt).scalars().all()
    
    result_list = [n.to_dict() for n in notifications]
    return jsonify(result_list)

