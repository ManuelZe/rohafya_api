import random
import string
from datetime import datetime


def generate_random_letters(length=8):
    # Utiliser string.ascii_letters qui contient toutes les lettres majuscules et minuscules
    letters = string.ascii_letters  # 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ'
    username = ''.join(random.choice(letters) for _ in range(length))
    return username

def generate_random_letters_and_digits(length=8):
    # Utiliser string.ascii_letters pour les lettres et string.digits pour les chiffres
    letters_and_digits = string.ascii_letters + string.digits  # 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789'
    password = ''.join(random.choice(letters_and_digits) for _ in range(length))
    return password


def generate_unique_numbers(prefix="+1", length=10):
    """ Génère `n` numéros de téléphone uniques """
    number = f"{prefix}{random.randint(10**(length-1), 10**length - 1)}"
    return number

def generate_random_email():
    """ Génère un email aléatoire """
    domains = ["gmail.com", "yahoo.com", "outlook.com", "example.com"]
    name = "".join(random.choices(string.ascii_lowercase + string.digits, k=10))
    domain = random.choice(domains)
    return f"{name}@{domain}"


def generate_sequence(prefix, model, field_name="Sequence"):
    """Génère une séquence du type PREFIX YYYY/XXXX pour le modèle donné."""
    year = datetime.now().year
    # On compte les éléments existants pour cette année
    last_entry = (
        model.query
        .filter(getattr(model, field_name).like(f"{prefix} {year}/%"))
        .order_by(getattr(model, field_name).desc())
        .first()
    )

    if last_entry and getattr(last_entry, field_name):
        # Extraire la dernière partie numérique (ex: 4521)
        last_number = int(getattr(last_entry, field_name).split("/")[-1])
        next_number = last_number + 1
    else:
        next_number = 1

    return f"{prefix} {year}/{next_number:04d}"