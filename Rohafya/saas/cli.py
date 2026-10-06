"""Commandes d'installation du mode SaaS.

    flask --app Rohafya saas init
    flask --app Rohafya saas create-superadmin admin@exemple.com --first-name Jean --last-name Dupont
"""
import click
from flask.cli import AppGroup

from Rohafya import db
from .constants import ROLE_SUPER_ADMIN, ROLE_TENANT_ADMIN
from .services import (
    active_users_by_email,
    add_role,
    create_user,
    default_gnuhealth_tenant,
    get_or_create_role,
    grant_default_permissions,
    sync_permissions,
)

saas_cli = AppGroup("saas", help="Installation et administration du mode SaaS.")


@saas_cli.command("init")
@click.option("--sans-permissions", is_flag=True, help="Ne pas toucher aux permissions des rôles Patient et Doctor.")
def init(sans_permissions):
    """Crée les rôles, les permissions et l'établissement GNU Health historique (idempotent).

    Les rôles Patient et Doctor reçoivent les permissions patients.* et doctors.* qui leur manquent.
    """
    db.create_all()
    get_or_create_role(ROLE_SUPER_ADMIN)
    get_or_create_role(ROLE_TENANT_ADMIN)
    created = sync_permissions()
    added = {} if sans_permissions else grant_default_permissions()
    db.session.commit()
    tenant = default_gnuhealth_tenant()
    click.echo(f"Rôles {ROLE_SUPER_ADMIN} et {ROLE_TENANT_ADMIN} prêts.")
    click.echo(f"Permissions : {created} créée(s).")
    for role_name, count in added.items():
        click.echo(f"Rôle {role_name} : {count} permission(s) par défaut ajoutée(s).")
    click.echo(f"Établissement GNU Health : {tenant.name} (id {tenant.id}, identifiant {tenant.slug}).")


@saas_cli.command("create-superadmin")
@click.argument("email")
@click.option("--first-name", default="", help="Prénom (compte nouveau uniquement).")
@click.option("--last-name", default="", help="Nom (compte nouveau uniquement).")
def create_superadmin(email, first_name, last_name):
    """Donne le rôle SuperAdmin au compte de cet e-mail (le crée s'il n'existe pas)."""
    users = active_users_by_email(email)
    if len(users) > 1:
        raise click.ClickException("Plusieurs comptes utilisent cet e-mail ; utilisez une adresse unique.")
    if users:
        user = users[0]
        add_role(user, ROLE_SUPER_ADMIN)
    else:
        if not first_name or not last_name:
            raise click.ClickException("Compte inexistant : précisez --first-name et --last-name pour le créer.")
        user = create_user(email, first_name, last_name, ROLE_SUPER_ADMIN)
    db.session.commit()
    click.echo(f"{user.email} est super-administrateur. Connexion : « Code par e-mail » sur la page de connexion.")
