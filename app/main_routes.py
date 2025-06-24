import json
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
    delete_session, get_user_role, create_user, create_admin, delete_user, redis_client
)
import config
from app.logs_utils import log_action


bp = Blueprint('main', __name__)

bucket_name = os.getenv("AWS_BUCKET_NAME", config.BUCKET_NAME)
region = os.getenv("AWS_REGION", config.REGION)

@bp.before_app_request
def require_login():
    allowed_routes = ['main.login', 'static']  # autorise login et fichiers statiques
    if 'session_token' not in session and request.endpoint not in allowed_routes:
        return redirect(url_for('main.login'))


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
            log_action(username, "Connexion réussie")
            return redirect(url_for('main.index'))
        flash("Identifiants invalides", "error")
        return render_template('login.html'), 401
    return render_template('login.html')


@bp.route('/logout')
def logout():
    token = session.get('session_token')
    username = get_username_from_session(token) if token else None
    if token:
        delete_session(token)
    session.clear()
    if username:
        log_action(username, "Déconnexion")
    return redirect(url_for('main.login'))



@bp.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        role = request.form.get('role', 'user')
        if create_user(username, password, role):
            log_action(username, f"Création de compte avec rôle {role}")
            flash("Utilisateur créé avec succès", "success")
            return redirect(url_for('main.login'))
        flash("Nom d'utilisateur déjà utilisé", "danger")
    return render_template('register.html')



@bp.route('/register-admin', methods=['GET', 'POST'])
@role_required(['admin'])
def register_admin():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')

        if create_admin(username, password):
            log_action(get_current_user(), f"Création d'un administrateur : {username}")
            flash("Administrateur créé avec succès", "success")
            return redirect(url_for('main.index'))

        flash("Nom d'utilisateur déjà utilisé", "danger")
    return render_template('register-admin.html', registering_admin=True)




@bp.route('/manage-users')
@role_required(['admin'])
def manage_users():
    # Récupère tous les utilisateurs (stockés en hash sous la clé 'users')
    raw = redis_client.hgetall('users') or {}
    users = []
    for username_bytes, data_bytes in raw.items():
        # 1) Décoder la clé (username)
        username = username_bytes.decode('utf-8') if isinstance(username_bytes, (bytes, bytearray)) else username_bytes
        # 2) Décoder la valeur et parser le JSON
        try:
            data_str = data_bytes.decode('utf-8') if isinstance(data_bytes, (bytes, bytearray)) else data_bytes
            data = json.loads(data_str)
        except Exception:
            data = {'role': 'user'}
        users.append({
            'username': username,
            'role': data.get('role', 'user')
        })
        # Trier : admins en premier
        users.sort(key=lambda u: 0 if u['role'] == 'admin' else 1)

    return render_template('manage_users.html', users=users)


@bp.route('/delete-user', methods=['POST'])
@role_required(['admin'])
def delete_user():
    username = request.form.get('username')
    if username:
        redis_client.hdel('users', username)
        log_action(get_current_user(), f"Suppression de l'utilisateur {username}")
        flash(f"Utilisateur {username} supprimé.", "success")
    return redirect(url_for('main.manage_users'))


@bp.route('/promote-user', methods=['POST'])
@role_required(['admin'])
def promote_user():
    username = request.form.get('username')
    data = redis_client.hget('users', username)
    if data:
        user_data = json.loads(data)
        user_data['role'] = 'admin'
        redis_client.hset('users', username, json.dumps(user_data))
        log_action(get_current_user(), f"Promotion en admin de {username}")
        flash(f"Utilisateur {username} promu admin.", "success")
    return redirect(url_for('main.manage_users'))





@bp.route('/delete-account', methods=['GET', 'POST'])
def delete_account():
    token = session.get('session_token')
    if not token:
        return redirect(url_for('main.login'))

    username = get_username_from_session(token)
    if not username:
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        redis_client.hdel('users', username)
        delete_session(token)
        session.clear()
        log_action(username, "Suppression de son propre compte")
        flash("Votre compte a été supprimé.", "success")
        return redirect(url_for('main.login'))

    return render_template('confirm_delete_account.html', username=username)







@bp.route('/change-password', methods=['GET', 'POST'])
def change_password():
    token = session.get('session_token')
    if not token:
        return redirect(url_for('main.login'))

    username = get_username_from_session(token)
    if not username:
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        old_password = request.form.get('old_password')
        new_password = request.form.get('new_password')
        confirm_password = request.form.get('confirm_password')

        if not verify_user(username, old_password):
            flash("Ancien mot de passe incorrect.", "error")
        elif new_password != confirm_password:
            flash("Les nouveaux mots de passe ne correspondent pas.", "error")
        else:
            from app.auth_utils import hash_password
            hashed_pw, salt = hash_password(new_password)
            user_data_json = redis_client.hget('users', username)
            if not user_data_json:
                abort(404)

            user_data = json.loads(user_data_json.decode('utf-8') if isinstance(user_data_json, bytes) else user_data_json)
            user_data['password'] = f"{hashed_pw}:{salt}"
            redis_client.hset('users', username, json.dumps(user_data))
            log_action(username, "Changement de mot de passe")
            flash("Mot de passe mis à jour avec succès.", "success")
            return redirect(url_for('main.index'))

    return render_template('change_password.html', username=username)










@bp.route('/change-username', methods=['GET', 'POST'])
def change_username():
    token = session.get('session_token')
    if not token:
        return redirect(url_for('main.login'))

    current_username = get_username_from_session(token)
    if not current_username:
        return redirect(url_for('main.login'))

    if request.method == 'POST':
        new_username = request.form.get('new_username')

        if not new_username:
            flash("Veuillez entrer un nouveau nom d'utilisateur.", "error")
        elif redis_client.hexists('users', new_username):
            flash("Ce nom d'utilisateur est déjà pris.", "error")
        else:
            user_data_json = redis_client.hget('users', current_username)
            if not user_data_json:
                abort(404)

            redis_client.hset('users', new_username, user_data_json)
            redis_client.hdel('users', current_username)

            delete_session(token)
            new_token = create_session(new_username)
            session['session_token'] = new_token
            log_action(new_username, f"Changement de nom d'utilisateur de {current_username} à {new_username}")
            flash(f"Nom d'utilisateur changé avec succès : {new_username}", "success")
            return redirect(url_for('main.index'))

    return render_template('change_username.html', current_username=current_username)











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
        log_action(get_current_user(), f"Upload de fichiers dans {prefix or '/'} : {[f.filename for f in safe_files]}")
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
        log_action(get_current_user(), f"Création du dossier {new_key}")
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
        log_action(get_current_user(), f"Suppression de {obj}")
        flash("Suppression réussie", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression : {e}", "error")

    parent = obj.parent.path if obj.parent else ''
    return redirect(url_for('main.index', prefix=parent))


@bp.route('/download')
def download():
    key = request.args.get('key')
    if not key:
        flash("Clé manquante pour téléchargement.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(key)

    try:
        if obj.is_folder():
            # Dossier => zip et download
            tmp_dir = tempfile.TemporaryDirectory()
            zip_path = (Path(tmp_dir.name) / (obj.name or "archive")).with_suffix(".zip")
            obj.download(zip_path)
            log_action(get_current_user(), f"Téléchargement du dossier {obj}")
            return send_file(zip_path, as_attachment=True)

        elif obj.is_file():
            # Fichier => lien S3 direct
            log_action(get_current_user(), f"Téléchargement du fichier {obj}")
            return redirect(obj.get_download_url())

        else:
            # Cas ambigus
            flash("Objet non reconnu comme fichier ou dossier.", "error")
            return redirect(request.referrer or url_for('main.index'))

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
        log_action(get_current_user(), f"Renommage de {old_key} en {new_key}")
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
        log_action(get_current_user(), f"Déplacement de {old_key} vers {new_key}")
        flash("Déplacement réussi.", "success")
    except Exception as e:
        flash(f"Erreur déplacement : {e}", "error")

    parent = '/'.join(new_key.rstrip('/').split('/')[:-1])
    return redirect(url_for('main.index', prefix=parent + '/' if parent else ''))




@bp.route('/logs')
def view_logs():
    user = get_current_user()
    role = current_user_role()

    if not user:
        flash("Veuillez vous connecter", "error")
        return redirect(url_for('main.login'))

    if role == 'admin' and request.args.get('user'):
        target_user = request.args.get('user')
    else:
        target_user = user

    log_key = f"logs:{target_user}"
    logs_raw = redis_client.lrange(log_key, 0, -1)

    logs = []
    for entry in logs_raw:
        log = json.loads(entry.encode('utf-8'))
        ts = log.get('timestamp')
        log['formatted_ts'] = ts.replace('T', ' ')[:19] if ts else '-'
        logs.append(log)

    viewing_own_logs = (user == target_user)

    return render_template(
        "logs.html",
        logs=logs,
        username=target_user,
        is_admin=(role == 'admin'),
        viewing_own_logs=viewing_own_logs
    )



from flask_login import current_user
from flask import make_response

@bp.route('/logs-global')
@role_required(['admin'])
def view_global_logs():
    raw_users = redis_client.hkeys('users') or []
    all_logs = []

    for username_bytes in raw_users:
        username = username_bytes.decode('utf-8') if isinstance(username_bytes, bytes) else username_bytes
        log_key = f"logs:{username}"
        logs_raw = redis_client.lrange(log_key, 0, -1)
        for entry in logs_raw:
            log = json.loads(entry.decode('utf-8') if isinstance(entry, bytes) else entry)
            ts = log.get('timestamp')
            log['formatted_ts'] = ts.replace('T', ' ')[:19] if ts else '-'
            log['username'] = username
            user_data_json = redis_client.hget('users', username)
            user_data = {}
            if user_data_json:
                try:
                    user_data = json.loads(user_data_json.decode('utf-8') if isinstance(user_data_json, bytes) else user_data_json)
                except:
                    user_data = {}
            log['role'] = user_data.get('role', 'user')
            all_logs.append(log)

    all_logs.sort(key=lambda x: x.get('timestamp', ''), reverse=True)

    response = make_response(render_template(
        'logs_global.html',
        logs=all_logs,
        is_admin=True,
        viewing_own_logs=False
    ))
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response








@bp.route('/clear-logs', methods=['POST'])
@role_required(['admin'])
def clear_logs():
    target_user = request.form.get('username')

    if not target_user:
        flash("Nom d'utilisateur manquant pour la suppression des logs.", "error")
        return redirect(request.referrer or url_for('main.view_logs'))

    try:
        redis_client.delete(f"logs:{target_user}")
        flash(f"Logs de l'utilisateur {target_user} supprimés avec succès.", "success")
    except Exception as e:
        flash(f"Erreur lors de la suppression des logs : {e}", "error")

    return redirect(url_for('main.view_logs', user=target_user))




@bp.route('/clear_logs_global', methods=['POST'])
@role_required(['admin'])
def clear_logs_global():
    raw_users = redis_client.hkeys('users') or []
    for username_bytes in raw_users:
        username = username_bytes.decode('utf-8') if isinstance(username_bytes, bytes) else username_bytes
        log_key = f"logs:{username}"
        redis_client.delete(log_key)
    flash("Tous les logs globaux ont été supprimés.", "success")
    return redirect(url_for('main.view_global_logs'))
