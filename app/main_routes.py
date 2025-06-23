import os
import tempfile
from pathlib import Path
from functools import wraps

from flask import (
    Blueprint, request, render_template, redirect, url_for,
    flash, session, send_file, abort, current_app
)
from werkzeug.utils import secure_filename

from app.s3_utils import S3Key, S3Directory, S3File
from app.auth_utils import (
    verify_user, create_session, get_username_from_session,
    delete_session, get_user_role, create_user
)
import config

bp = Blueprint('main', __name__)

bucket_name = os.getenv("AWS_BUCKET_NAME", config.BUCKET_NAME)
region = os.getenv("AWS_REGION", config.REGION)


def get_s3_object(key: str) -> S3Key:
    return S3Key.get_from_key(bucket_name, key or '')


def get_current_user():
    token = session.get('session_token')
    return get_username_from_session(token) if token else None


def current_user_role():
    token = session.get('session_token')
    if not token:
        return 'guest'
    username = get_username_from_session(token)
    return get_user_role(username) or 'guest'


def role_required(allowed_roles):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            if current_user_role() not in allowed_roles:
                flash("Vous n'avez pas les droits pour effectuer cette action.", "error")
                return redirect(url_for('main.index'))
            return func(*args, **kwargs)
        return wrapper
    return decorator


@bp.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if verify_user(username, password):
            session['session_token'] = create_session(username)
            return redirect(url_for('main.index'))
        flash("Identifiants invalides", "error")
        return render_template('login.html'), 401
    return render_template('login.html')


@bp.route('/logout')
def logout():
    token = session.get('session_token')
    if token:
        delete_session(token)
    session.clear()
    return redirect(url_for('main.login'))


@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role', 'user')
        if create_user(username, password, role):
            flash("Utilisateur créé avec succès", "success")
            return redirect(url_for('main.login'))
        flash("Nom d'utilisateur déjà utilisé", "danger")
    return render_template('register.html')


@bp.route('/')
def index():
    user = get_current_user()
    role = current_user_role()
    if role == 'guest':
        flash("Vous êtes en mode visiteur - accès en lecture seule.", "info")

    prefix = request.args.get('prefix', '') or ''
    dir_obj = get_s3_object(prefix)

    try:
        if dir_obj.is_folder():
            dirs, files = dir_obj.list_content()

            # Transformer en tuples (full_key, relative_key) pour Jinja
            folders_for_template = [(d.path, d.name) for d in dirs]
            files_for_template = [(f.path, f.name) for f in files]
        else:
            # Si dir_obj est un fichier seul, pas de dossiers ni fichiers enfants
            folders_for_template = []
            files_for_template = [(dir_obj.path, dir_obj.name)]

    except Exception as e:
        flash(f"Erreur de lecture S3 : {e}", "error")
        folders_for_template = []
        files_for_template = []

    parent_prefix = dir_obj.parent.path if dir_obj.parent else ''

    # Passe bien les tuples (path, name) au template
    return render_template(
        'index.html',
        user=user,
        role=role,
        prefix=prefix,
        parent_prefix=parent_prefix,
        folders=folders_for_template,
        files=files_for_template
    )


    return render_template(
        'index.html',
        content=content,
        user=user,
        role=role,
        folders=folders_for_template,
        files=files_for_template,
        prefix=prefix,
        parent_prefix=parent_prefix
    )





@bp.route('/upload', methods=['POST'])
@role_required(['admin', 'user'])
def upload():
    prefix = request.form.get('prefix', '').strip('/')
    files = request.files.getlist('files')

    if not files:
        flash("Aucun fichier reçu.", "error")
        return redirect(request.referrer or url_for('main.index'))

    s3dir = S3Directory(bucket_name, prefix)
    safe_files = []
    for f in files:
        f.filename = secure_filename(f.filename)
        if f.filename:
            safe_files.append(f)

    try:
        s3dir.upload_from_storage(safe_files)
        flash("Upload terminé avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de l'upload : {e}", "error")

    return redirect(url_for('main.index', prefix=prefix + '/' if prefix else ''))


@bp.route('/create-folder', methods=['POST'])
@role_required(['admin', 'user'])
def create_folder():
    prefix = request.form.get('prefix', '').strip('/')
    folder_name = request.form.get('folder_name', '').strip('/')

    if not folder_name:
        flash("Nom de dossier vide.", "error")
        return redirect(request.referrer or url_for('main.index'))

    new_key = f"{prefix}/{folder_name}/" if prefix else f"{folder_name}/"
    try:
        S3Directory.create(bucket_name, new_key)
        flash(f"Dossier '{folder_name}' créé avec succès.", "success")
    except Exception as e:
        flash(f"Erreur création dossier : {e}", "error")

    return redirect(url_for('main.index', prefix=prefix + '/' if prefix else ''))


@bp.route('/delete', methods=['POST'])
@role_required(['admin', 'user'])
def delete():
    key = request.form.get('key')
    if not key:
        flash("Clé manquante pour suppression.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(key)
    try:
        obj.remove()
        flash("Suppression réussie", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression : {e}", "error")

    parent = obj.parent.path if obj.parent else ''
    return redirect(url_for('main.index', prefix=parent))


@bp.route('/download')
def download():
    key = request.args.get('key')
    if not key.endswith('/'):
        key += '/'
    if not key:
        flash("Clé manquante pour téléchargement.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(key)
    try:
        if obj.is_folder():
            tmp_dir = tempfile.TemporaryDirectory()
            zip_path = (Path(tmp_dir.name) / (obj.name or "archive")).with_suffix(".zip")
            obj.download(zip_path)
            response = send_file(zip_path, as_attachment=True)
            # TemporaryDirectory will be cleaned up automatically on close
            return response
        else:
            url = obj.get_download_url()
            return redirect(url)
    except Exception as e:
        flash(f"Erreur lors du téléchargement : {e}", "error")
        return redirect(request.referrer or url_for('main.index'))


@bp.route('/rename', methods=['POST'])
@role_required(['admin', 'user'])
def rename_route():
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')

    if not old_key or not new_key:
        flash("Clé source et destination requises.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(old_key)
    try:
        obj.rename(new_key)
        flash("Renommage réussi.", "success")
    except Exception as e:
        flash(f"Erreur renommage : {e}", "error")

    parent = '/'.join(new_key.rstrip('/').split('/')[:-1])
    return redirect(url_for('main.index', prefix=parent + '/' if parent else ''))


@bp.route('/move', methods=['POST'])
@role_required(['admin', 'user'])
def move_route():
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')

    if not old_key or not new_key:
        flash("Clé source ou destination manquante.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(old_key)
    try:
        obj.move(new_key)
        flash("Déplacement réussi.", "success")
    except Exception as e:
        flash(f"Erreur déplacement : {e}", "error")

    parent = '/'.join(new_key.rstrip('/').split('/')[:-1])
    return redirect(url_for('main.index', prefix=parent + '/' if parent else ''))
