from flask import Blueprint, jsonify, request, send_file
from flask_cors import cross_origin
from flask_jwt_extended import get_jwt_identity, jwt_required
from Rohafya import db, tryton
from sqlalchemy import select
from io import BytesIO
from werkzeug.utils import secure_filename
from Rohafya.deco.decorators import require_any_permission, roles_required
from Rohafya.patients.allow_dayx import get_days_after
from datetime import datetime, timedelta
from Rohafya.accounts.models import Prescriptions, User, SuggestionBox

# Création du Blueprint
suggestions_bp = Blueprint('suggestions', __name__, url_prefix='/suggestions')


@suggestions_bp.route('/add', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@require_any_permission(["patients.suggestions.create_suggestion",
                         "administration.suggestions.create_suggestion",
                         "doctors.suggestions.create_suggestion"])
def create_suggestion():
    if request.method == "POST":
        data = request.get_json()
        if not data or 'content' not in data:
            return jsonify({"error": "Champs manquants Content"}), 400

        current_user = get_jwt_identity()

        user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

        suggestion = SuggestionBox(
            content=data['content'],
            note=data['note'] if 'note' in data else None,
            CreatedBy=user.id if user else None,
            CreatedAt=datetime.now()
        )
        db.session.add(suggestion)
        db.session.commit()

        return jsonify({"message": "Suggestion créée avec succès", "suggestion": suggestion.to_dict()}), 201


@suggestions_bp.route('/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["patients.suggestions.get_suggestions",
                         "administration.suggestions.get_suggestions",
                         "administration.suggestions.get_suggestions"])
def get_suggestions():

    suggestions = db.session.execute(select(SuggestionBox)).scalars().all()

    suggestions_list = [suggestion.to_dict() for suggestion in suggestions]

    return jsonify(suggestions_list)


@suggestions_bp.route('/<int:sug_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@require_any_permission(["patients.suggestions.get_suggestion",
                         "administration.suggestions.get_suggestion",
                         "doctors.suggestions.get_suggestion"])
def get_suggestion(sug_id):

    suggestion = None
    if sug_id:
        suggestion = db.session.execute(select(SuggestionBox).filter_by(id=sug_id)).scalar_one_or_none()
    else:
        return {"Message": "L'Id de la Suggestion doit être fourni. "}
    
    if not suggestion:
        return {"message" : "Suggestion Non Trouvée."}
    
    return {"Suggestion" : suggestion.to_dict()}


@suggestions_bp.route('update/<int:sug_id>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["patients.suggestions.update_suggestion",
                         "administration.suggestions.update_suggestion",
                         "doctors.suggestions.update_suggestion"])
def update_suggestion(sug_id):
    if request.method == 'PUT':

        if sug_id :
            suggestion = db.session.execute(select(SuggestionBox).filter_by(id=sug_id)).scalar_one_or_none() 

            if suggestion :
                data = request.get_json()
                suggestion.content = data.get('content', suggestion.content)
                suggestion.note = data.get('note', suggestion.note)
                suggestion.UpdatedAt = datetime.now()
                try:
                    identity = get_jwt_identity()
                    user = User.query.get(identity)
                    suggestion.UpdatedBy = user.id if user else None
                except : 
                    suggestion.UpdatedBy = None

                db.session.commit()

                return suggestion.to_dict()
            else : 
                return {"Message" : "Suggestion Non Trouvée."}
        
        else :
            return {"Message" : "L'Id de la Suggestion doit être fourni. "}


@suggestions_bp.route('delete/<int:sug_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["patients.suggestions.delete_suggestion",
                         "administration.suggestions.delete_suggestion",
                         "doctors.suggestions.delete_suggestion"])
def delete_suggestion(sug_id):

    if request.method == 'DELETE' :
        if sug_id :
            suggestion = db.session.execute(select(SuggestionBox).filter_by(id=sug_id)).scalar_one_or_none()

            if suggestion :
                db.session.delete(suggestion)
                db.session.commit()
                return {"Message" : "Suggestion Supprimée Avec Succès."}
            else : 
                return {"Message" : "Suggestion Non Trouvée."}
        else :
            return {"Message" : "L'Id de la Suggestion doit être fourni. "}


@suggestions_bp.route('by_user/<int:user_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin'])
@require_any_permission(["administration.suggestions.suggestions_by_user"])
def suggestions_by_user(user_id):

    if request.method == 'GET' :
        if user_id :
            suggestions = db.session.execute(select(SuggestionBox).filter_by(CreatedBy=user_id)).scalars().all()

            suggestions_list = [suggestion.to_dict() for suggestion in suggestions]

            return jsonify(suggestions_list)
        else :
            return {"Message" : "L'Id de l'Utilisateur doit être fourni. "} 


@suggestions_bp.route('for_user/', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin', 'Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["patients.suggestions.suggestions_for_user",
                         "administration.suggestions.suggestions_for_user",
                         "doctors.suggestions.suggestions_for_user"])
def suggestions_for_user():

    if request.method == 'GET' :

        current_user = get_jwt_identity()

        user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

        if user : 
            suggestions = db.session.execute(select(SuggestionBox).filter_by(CreatedBy=user.id)).scalars().all()

            suggestions_list = [suggestion.to_dict() for suggestion in suggestions]

            return jsonify(suggestions_list)
        else :
            return {"Message" : "Vous n'êtes pas connectés. "}

