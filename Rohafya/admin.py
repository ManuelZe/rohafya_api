import functools
from flask import Flask
from flask_restful import Api
import psycopg2
from flask import (
    Blueprint, request, g, redirect, flash, render_template, session, url_for
)
from .auth import login_required
from werkzeug.security import check_password_hash, generate_password_hash
from Rohafya.db import get_db
from flask_tryton import Tryton

admin = Blueprint('admin', __name__, url_prefix='/admin')

