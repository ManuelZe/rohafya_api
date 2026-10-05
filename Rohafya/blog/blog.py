from flask_jwt_extended import jwt_required
from ..accounts.models import Blog
from flask import Blueprint, request, jsonify, current_app, send_file
from io import BytesIO
from flask_cors import CORS, cross_origin
from Rohafya import db, create_app
from werkzeug.utils import secure_filename
from sqlalchemy import select
from flask import send_from_directory
import os
from Rohafya.deco.decorators import roles_required, require_any_permission
from datetime import datetime

article = Blueprint('articles', __name__, url_prefix='/blog/')

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
        filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

@article.route('/add_picture', methods=['POST'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')

def add_article_picture():
    """Fonction de chargement des images des blogs

    Returns:
        _json_: Message de Succès ou d'erreur
    """
    app = create_app()
    if request.method == 'POST':
        # check if the post request has the file part
        if 'file' not in request.files:
            return {'message' : 'Aucune image.'}
        file = request.files['file']
        # If the user does not select a file, the browser submits an
        # empty file without a filename.
        if file.filename == '':
            return {'message' : 'No selected file.'}
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            try:
                file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            except FileNotFoundError:
                os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
            return {'message' : 'Image ajoutée.'}

    return jsonify({"message": "Mauvaise Requête."}),


@article.route('/add2', methods=['POST'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.blog.add_article"])
def add_article2():
    """Ajout d'un Nouvel article

    Returns:
        json: Json de l'article ajouté
    """
    if request.method == 'POST' :
        titre = request.form['titre']
        date = request.form['date']
        description = request.form['description']
        url = request.form['url']

        image_data = None
        image_mimetype = None

        image = request.files["image_data"]
        image_data = image.read() 
        image_mimetype = image.mimetype

        titre.capitalize()
        
        article = db.session.execute(select(Blog).filter_by(titre=titre)).scalar_one_or_none()
        if article :
            return {"Message ": f" L'article avec le titre {titre} existe déjà. "}

        article = Blog(titre=titre, date=date, 
                       description=description, url=url,
                       image_data=image_data, image_mimetype=image_mimetype)
        
        db.session.add(article)
        db.session.commit()

        return {"article" : article.to_dict()}
    

@article.route('/add', methods=['POST'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.blog.add_article"])
def add_article():
    """Ajout d'un article avec image"""
    titre = request.form.get('titre')
    description = request.form.get('description')
    url = request.form.get('url')
    file = request.files.get('file')

    # Vérification de base
    if not titre or not description:
        return jsonify({'message': 'Titre et description requis.'}), 400

    image_data = None
    image_mimetype = None

    # Gestion de l’image
    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        image_data = file.read()
        image_mimetype = file.mimetype
    elif file:
        return jsonify({'message': 'Format d’image non supporté.'}), 400

    # Création de l'article
    article = Blog(
        titre=titre,
        description=description,
        url=url,
        date=datetime.utcnow(),
        image_data=image_data,
        image_mimetype=image_mimetype,
        is_visible=True
    )

    try:
        db.session.add(article)
        db.session.commit()
        return {"article" : article.to_dict()}
    except Exception as e:
        db.session.rollback()
        return jsonify({'message': f'Erreur lors de l’ajout : {e}'}), 500


@article.route('/del/<int:blog_id>', methods=['DELETE'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.blog.delete_article"])
def delete_article(blog_id):
    """Suppression d'un Article

    Args:
        blog_id (int): Suppression d'un article

    Returns:
        json: Message de Suppression
    """
    if blog_id:
        article = db.session.execute(select(Blog).filter_by(id=blog_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID de l'article doit être fourni."}

    if not article :
        return {"Message" : " Aucun Article trouvé."}

    db.session.delete(article)
    db.session.commit()

    return {"Message" : "Article Supprimé avec succès."}


@article.route('/mod/<int:blog_id>', methods=['PUT'])
@cross_origin(supports_credentials=True)
# @roles_required('Admin')
@jwt_required()
@require_any_permission(["administration.blog.modify_article"])
def modif_article(blog_id):
    """Modification d'un article

    Args:
        blog_id (int): id de l'article

    Returns:
        json: Le json de l'article modifié
    """
    if blog_id:
        article = db.session.execute(select(Blog).filter_by(id=blog_id)).scalar_one_or_none()
    else :
        return {"Message" : " L'ID de l'Article doit être fourni."}

    if article is None :
        return {"Message" : " Aucun Article Trouvé"}

    if request.method == "PUT" :
        data = request.json
        article.titre = data.get('titre', article.titre)
        article.date = data.get('date', article.date)
        article.description = data.get('description', article.description)
        article.url = data.get('url', article.url)
        article.is_visible = data.get('is_visible', article.is_visible)
        article.image = data.get('image', article.image)

        if not article.titre :
            message = "Vérifier que le titre ne soit pas vide. "
            return {"message": message}
        
        db.session.commit()

    return {"article" : article.to_dict()}


@article.route('/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin','Patient', 'Doctor'])
@require_any_permission(["administration.blog.all_article",
                         "patients.blog.all_article",
                         "doctors.blog.all_article"])
def all_articles():
    articles = Blog.query.all()
    articles = db.session.execute(select(Blog)).scalars().all()

    return jsonify([article.to_dict() for article in articles])


@article.route('/<int:article_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin','Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["administration.blog.get_article",
                         "patients.blog.get_article",
                         "doctors.blog.get_article"])
def get_article(article_id):
    """Récupère un article en particulier

    Args:
        article_id (int): L'identifiant de l'article

    Returns:
        json: Le json de l'article
    """
    if article_id:
        article = db.session.execute(select(Blog).filter_by(id=article_id)).scalar_one_or_none()
        if article :
            return jsonify(article.to_dict())
        else :
            return {"Message ": "Aucun Article Trouvé à cet id."}
    else :
        return {"Message ": "Fournir un ID pour Continuer Normalement."}


@article.route('/image/<int:article_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
# @roles_required(['Admin','Patient', 'Doctor'])
@jwt_required()
@require_any_permission(["administration.blog.retrieve_image",
                         "patients.blog.retrieve_image",
                         "doctors.blog.retrieve_image"])
def recuperer_image(article_id):
    """Récupère l'image d'un produit en tant que fichier."""
    article = db.session.execute(select(Blog).filter_by(id=article_id)).scalar_one_or_none()
    if not article.image_data:
        return {'message' : 'Aucune Image Trouvée.'}
    
    return send_file(BytesIO(article.image_data), mimetype=article.image_mimetype, as_attachment=False)

