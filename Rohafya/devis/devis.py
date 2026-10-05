import inspect

from flask import Blueprint, jsonify, request
from flask_cors import cross_origin
from flask_jwt_extended import jwt_required, get_jwt_identity
from Rohafya import db, tryton
from sqlalchemy import select
from Rohafya.accounts.models import User
from Rohafya.deco.decorators import roles_required, require_any_permission


devis = Blueprint('devis', __name__, url_prefix='/devis')


def get_party_from_current_user():
    """RÉCUPÉRER LE PARTY TRYTON (PATIENT OU DOCTEUR) DE L'UTILISATEUR CONNECTÉ

    Returns:
        object | None: Le party Tryton correspondant, ou None si introuvable.
    """
    current_user = get_jwt_identity()
    user = db.session.execute(select(User).filter_by(id=current_user)).scalar_one_or_none()

    if not user:
        return None

    federation_id = None
    if user.patients:
        federation_id = user.patients.PatientFederationID
    elif user.doctor:
        federation_id = user.doctor.DoctorFederationID

    if not federation_id:
        return None

    parties = tryton.pool.get('party.party').search([('federation_account', '=', federation_id)])

    return parties[0] if parties else None


def compute_unit_price(price_list, product, quantity, party=None):
    """APPELER PriceList.compute() EN S'ADAPTANT À LA SIGNATURE INSTALLÉE

    Selon la version de Tryton, product.price_list.compute() attend soit :
    - (product, quantity, uom, pattern=None)                       -> récent
    - (party, product, unit_price, quantity, uom, pattern=None)    -> ancien

    Le venv du serveur HIS (trytond 5.0.6) utilise la signature récente,
    alors que le venv de cette API (trytond 6.0.22) utilise encore
    l'ancienne. On introspecte donc la méthode réellement chargée pour
    construire l'appel qui lui correspond, au lieu de figer une hypothèse
    qui casse dès que l'un des deux environnements change de version.

    Args:
        price_list (object): La liste de prix Tryton (product.price_list)
        product (object): Le produit Tryton (product.product)
        quantity (float): La quantité souhaitée
        party (object, optionnel): Le party concerné (ancienne signature uniquement)

    Returns:
        Decimal | None: Le prix unitaire calculé
    """
    parametres = inspect.signature(price_list.compute).parameters

    print(f"le produit {product}, et le price list {product.list_price} et la quantité {quantity} et le party {party} et la fin {product.list_prices[0].list_price}")
    if 'unit_price' in parametres or 'party' in parametres:
        return price_list.compute(
            party, product, product.list_prices[0].list_price, quantity, product.default_uom)

    return price_list.compute(product, quantity, product.default_uom)


def calculer_ligne_devis(price_list, product, quantity, party=None):
    """CALCULER LE PRIX D'UN PRODUIT SELON UNE LISTE DE PRIX

    Args:
        price_list (object): La liste de prix Tryton (product.price_list)
        product (object): Le produit Tryton (product.product)
        quantity (float): La quantité souhaitée
        party (object, optionnel): Le party concerné (ancienne signature de Tryton uniquement)

    Returns:
        dict: Le détail de la ligne de devis
    """

    print("le party ", party ,"   Calcul du prix pour le produit", product.name, "quantité", quantity, "avec la liste de  ", price_list ,"prix", price_list.name)
    unit_price = compute_unit_price(price_list, product, quantity, party=party)
    if unit_price is None:
        unit_price = product.list_price

    print("Prix unitaire calculé : ", unit_price)
    unit_price = float(unit_price)

    return {
        "product_id": product.id,
        "code": product.code,
        "name": product.name,
        # "list_price": float(product.list_price),
        "unit_price": unit_price,
        "quantity": quantity,
        "amount": unit_price * quantity,
    }


def construire_devis(price_list, produits, party=None):
    """CONSTRUIRE LE DEVIS COMPLET (LIGNES + TOTAL) POUR UNE LISTE DE PRIX DONNÉE

    Args:
        price_list (object): La liste de prix Tryton (product.price_list)
        produits (list): Liste de dicts {"product_id": int, "quantity": float}
        party (object, optionnel): Le party concerné (ancienne signature de Tryton uniquement)

    Returns:
        dict: Le devis complet (liste de prix, lignes, montant total)
    """
    Product = tryton.pool.get('product.product')

    lignes = []
    montant_total = 0.0

    for item in produits:
        product_id = item.get('product_id')
        quantity = item.get('quantity') or 1

        if not product_id:
            continue

        products = Product.search([('id', '=', product_id)])

        if not products:
            lignes.append({"product_id": product_id, "Message": "Produit Non Trouvé."})
            continue

        ligne = calculer_ligne_devis(price_list, products[0], quantity, party=party)
        montant_total += ligne['amount']
        lignes.append(ligne)

    return {
        "price_list": {"id": price_list.id, "name": price_list.name},
        "lignes": lignes,
        "montant_total": montant_total,
    }


# LISTER TOUTES LES LISTES DE PRIX ACTIVES
@devis.route('/liste_prix/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@tryton.transaction()
def liste_prix():
    """LISTER TOUTES LES LISTES DE PRIX ACTIVES

    Returns:
        json: Liste des listes de prix (id, name)
    """
    price_lists = tryton.pool.get('product.price_list').search([('active', '=', True)])

    return jsonify([
        {"id": price_list.id, "name": price_list.name}
        for price_list in price_lists
    ])


# RÉCUPÉRER LA LISTE DE PRIX DE L'UTILISATEUR CONNECTÉ (PATIENT OU DOCTEUR)
@devis.route('/liste_prix/moi/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Patient', 'Doctor'])
@tryton.transaction()
def liste_prix_moi():
    """RÉCUPÉRER LA LISTE DE PRIX ASSOCIÉE À L'UTILISATEUR CONNECTÉ

    Returns:
        json: La liste de prix associée au party (ou message si aucune)
    """
    party = get_party_from_current_user()

    if not party:
        return {"Message": "Patient Ou Docteur Non Trouvé."}

    price_list = party.sale_price_list

    if not price_list:
        return {"Message": "Aucune liste de prix n'est associée à ce compte."}

    return {"id": price_list.id, "name": price_list.name}


# RECHERCHER DES PRODUITS / EXAMENS PAR NOM (AUTOCOMPLÉTION)
@devis.route('/produits/recherche/', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@tryton.transaction()
def rechercher_produits():
    """RECHERCHER LES PRODUITS / EXAMENS DONT LE NOM CORRESPOND AU TERME SAISI

    Le patient ne choisit jamais un produit à l'aveugle : il tape le début
    (ou une partie) du nom et cette route renvoie les produits/examens qui
    correspondent, pour alimenter une liste de suggestions (autocomplétion).

    Query string :
        q (str): Terme recherché dans le nom du produit (2 caractères min).
        limit (int, optionnel): Nombre maximum de résultats (défaut 20).

    Returns:
        json: Liste des produits correspondants (id, code, name, list_price)
    """
    terme = (request.args.get('q') or '').strip()
    limit = request.args.get('limit', default=20, type=int)

    if len(terme) < 2:
        return jsonify([])

    products = tryton.pool.get('product.product').search([
        ('salable', '=', True),
        ['OR',
            ('name', 'ilike', '%' + terme + '%'),
            ('code', 'ilike', '%' + terme + '%'),
            ],
        ], limit=limit, order=[('name', 'ASC')])

    return jsonify([
        {
            "id": product.id,
            "code": product.code,
            "name": product.name,
            # "list_price": float(product.list_price),
        }
        for product in products
    ])


# CALCULER LE DEVIS DE PLUSIEURS PRODUITS SELON UNE LISTE DE PRIX
@devis.route('/calculer/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@tryton.transaction()
def calculer_devis():
    """CALCULER LE DEVIS DE PLUSIEURS PRODUITS EN FONCTION D'UNE LISTE DE PRIX

    Corps JSON attendu :
        {
            "price_list_id": int,      # Optionnel. Si absent, la liste de prix
                                        # de l'utilisateur connecté est utilisée.
            "produits": [
                {"product_id": int, "quantity": float},
                ...
            ]
        }

    Returns:
        json: Le devis complet (liste de prix, lignes, montant total)
    """
    data = request.get_json()

    if not data or not data.get('produits'):
        return {"Message": "La liste des produits doit être fournie."}, 400

    party = get_party_from_current_user()

    price_list = None
    price_list_id = data.get('price_list_id')

    if price_list_id:
        price_lists = tryton.pool.get('product.price_list').search([('id', '=', price_list_id)])
        price_list = price_lists[0] if price_lists else None
    elif party:
        price_list = party.sale_price_list

    if not price_list:
        return {"Message": "Aucune liste de prix trouvée. Veuillez en préciser une."}, 400

    return jsonify(construire_devis(price_list, data['produits'], party=party))


# CALCULER LE DEVIS DE PLUSIEURS PRODUITS SELON UNE LISTE DE PRIX CHOISIE AU PRÉALABLE
@devis.route('/calculer/<int:price_list_id>/', methods=['POST'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@tryton.transaction()
def calculer_devis_pour_liste_prix(price_list_id):
    """CALCULER LE DEVIS DE PLUSIEURS PRODUITS POUR UNE LISTE DE PRIX SÉLECTIONNÉE AU PRÉALABLE

    La liste de prix est imposée par l'URL (ex: après sélection dans
    GET /devis/liste_prix/), contrairement à /devis/calculer/ qui la déduit
    par défaut de l'utilisateur connecté.

    Args:
        price_list_id (int): L'identifiant de la liste de prix choisie

    Corps JSON attendu :
        {
            "produits": [
                {"product_id": int, "quantity": float},
                ...
            ]
        }

    Returns:
        json: Le devis complet (liste de prix, lignes, montant total)
    """
    data = request.get_json()

    if not data or not data.get('produits'):
        return {"Message": "La liste des produits doit être fournie."}, 400

    price_lists = tryton.pool.get('product.price_list').search([('id', '=', price_list_id)])
    if not price_lists:
        return {"Message": "Liste De Prix Non Trouvée."}, 404

    party = get_party_from_current_user()

    return jsonify(construire_devis(price_lists[0], data['produits'], party=party))


# CALCULER LE DEVIS D'UN SEUL PRODUIT SELON UNE LISTE DE PRIX
@devis.route('/produit/<int:price_list_id>/<int:product_id>', methods=['GET'])
@cross_origin(supports_credentials=True)
@jwt_required()
# @roles_required(['Admin', 'Patient', 'Doctor'])
@tryton.transaction()
def devis_produit(price_list_id, product_id):
    """CALCULER LE DEVIS D'UN SEUL PRODUIT SELON UNE LISTE DE PRIX

    Args:
        price_list_id (int): L'identifiant de la liste de prix
        product_id (int): L'identifiant du produit

    Returns:
        json: Le détail de la ligne de devis
    """
    quantity = request.args.get('quantity', default=1, type=float)

    price_lists = tryton.pool.get('product.price_list').search([('id', '=', price_list_id)])
    if not price_lists:
        return {"Message": "Liste De Prix Non Trouvée."}, 404

    products = tryton.pool.get('product.product').search([('id', '=', product_id)])
    if not products:
        return {"Message": "Produit Non Trouvé."}, 404

    party = get_party_from_current_user()

    ligne = calculer_ligne_devis(price_lists[0], products[0], quantity, party=party)

    return jsonify(ligne)
