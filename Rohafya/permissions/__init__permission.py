from Rohafya import create_app, db
from Rohafya.accounts.models import Permissions
from ..permissions.permissions import PERMISSIONS

app = create_app()

def save_permissions_from_dict(permissions_dict, parent_key=""):
    """
    Parcours récursivement ton dictionnaire de permissions
    et sauvegarde toutes les permissions en base.
    """

    for key, value in permissions_dict.items():

        # Construction du préfixe hiérarchique :
        full_key = f"{parent_key}.{key}" if parent_key else key

        # Si la valeur est un dict → descendre dans l'arborescence
        if isinstance(value, dict):
            save_permissions_from_dict(value, full_key)
        else:
            # La valeur n'est pas un dict → c'est une PERMISSION FINALE
            perm_code = full_key
            perm_label = value

            # Vérifier si la permission existe déjà
            existing = Permissions.query.filter_by(code=perm_code).first()
            if not existing:
                new_perm = Permissions(code=perm_code, description=perm_label)
                db.session.add(new_perm)
                print(f"✔ Permission créée : {perm_code}")
            else:
                print(f"⏩ Permission déjà existante : {perm_code}")

    db.session.commit()
    print("🎉 Toutes les permissions ont été mises à jour !")


with app.app_context():

    save_permissions_from_dict(PERMISSIONS)



