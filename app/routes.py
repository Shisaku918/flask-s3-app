from flask import Blueprint, request, render_template, redirect, url_for, flash, send_file
from app.s3_utils import S3File, S3Directory
import boto3
import os
from pathlib import Path
import config

# Création du blueprint Flask 'main'
bp = Blueprint('main', __name__)

# Chargement des infos AWS depuis variables d'environnement ou config
region = os.getenv("AWS_REGION", config.REGION)
bucket_name = os.getenv("AWS_BUCKET_NAME", config.BUCKET_NAME)

# Initialisation des clients S3 boto3
s3 = boto3.resource('s3', region_name=region)
s3_client = boto3.client('s3', region_name=region)


def is_folder_key(key: str) -> bool:
    # Détecte si la clé S3 représente un dossier (finissant par '/')
    return key.endswith('/')


def get_s3_object(key: str):
    # Renvoie un objet S3File ou S3Directory selon la clé
    if is_folder_key(key):
        return S3Directory(bucket_name, key)
    return S3File(bucket_name, key)


@bp.route('/')
def index():
    # Page principale, liste les fichiers/dossiers sous un prefix donné
    prefix = request.args.get('prefix', '') or ''

    dir_obj = S3Directory(bucket_name, prefix)
    folders, files = dir_obj.list()

    parent_prefix = ''
    if dir_obj.parent:
        parent_prefix = dir_obj.parent.path

    # Nettoyage des noms (relatifs au prefix)
    prefix_slash = prefix + '/' if prefix and not prefix.endswith('/') else prefix
    folders_clean = [(f, f[len(prefix_slash):]) for f in folders] if prefix else [(f, f) for f in folders]
    files_clean = [f[len(prefix_slash):] for f in files] if prefix else files

    # Affiche la page index.html avec les listes de dossiers/fichiers
    return render_template(
        'index.html',
        folders=folders_clean,
        files=files_clean,
        prefix=prefix,
        parent_prefix=parent_prefix
    )


@bp.route('/delete', methods=['POST'])
def delete():
    # Supprime un fichier ou dossier S3 (clé reçue via formulaire)
    key = request.form.get('key')
    obj = get_s3_object(key)
    success, message = obj.remove()
    flash(message, "success" if success else "error")

    # Redirection vers le dossier parent
    parent = '/'.join(key.rstrip('/').split('/')[:-1])
    if parent:
        parent = parent.rstrip('/') + '/'
    return redirect(url_for('main.index', prefix=parent))


@bp.route('/download')
def download():
    # Télécharge un fichier ou dossier depuis S3

    key = request.args.get('key')
    if not key:
        flash("Clé manquante pour téléchargement.", "error")
        return redirect(request.referrer or url_for('main.index'))

    if is_folder_key(key):
        # Si dossier, on crée un zip temporaire et on l'envoie
        dir_obj = S3Directory(bucket_name, key)
        try:
            local_zip = dir_obj.download(Path('/tmp') / key.strip('/'))  # dossier temporaire local
            return send_file(local_zip, as_attachment=True)
        except Exception as e:
            flash(f"Erreur lors du téléchargement du dossier : {e}", "error")
            return redirect(request.referrer or url_for('main.index'))
    else:
        # Si fichier, on génère un lien pré-signé pour téléchargement direct
        try:
            presigned_url = s3_client.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': bucket_name,
                    'Key': key,
                    'ResponseContentDisposition': f'attachment; filename="{os.path.basename(key)}"'
                },
                ExpiresIn=3600
            )
            return redirect(presigned_url)
        except Exception as e:
            flash(f"Erreur lors de la génération du lien de téléchargement : {e}", "error")
            return redirect(request.referrer or url_for('main.index'))


@bp.route('/create-folder', methods=['POST'])
def create_folder():
    # Crée un dossier S3 vide (objet clé finissant par '/')
    prefix = request.form.get('prefix', '').strip()
    folder_name = request.form.get('folder_name', '').strip()

    if not folder_name:
        flash("Nom de dossier vide.", "error")
        return redirect(request.referrer or url_for('main.index'))

    prefix = prefix.strip('/')
    folder_name = folder_name.strip('/')

    new_folder_key = f"{prefix}/{folder_name}/" if prefix else f"{folder_name}/"

    try:
        s3_client.put_object(Bucket=bucket_name, Key=new_folder_key)
        flash(f"Dossier '{folder_name}' créé avec succès.")
    except Exception as e:
        flash(f"Erreur lors de la création du dossier : {e}", "error")

    return redirect(request.referrer or url_for('main.index', prefix=(prefix + '/') if prefix else ''))


@bp.route('/upload', methods=['POST'])
def upload():
    # Upload un ou plusieurs fichiers via formulaire sous un prefix donné
    prefix = request.form.get('prefix', '').strip('/')
    files = request.files.getlist('files')

    if not files:
        flash("Aucun fichier reçu.", "error")
        return redirect(request.referrer or url_for('main.index'))

    for file in files:
        s3_key = f"{prefix}/{file.filename}" if prefix else file.filename
        try:
            s3_client.upload_fileobj(file, bucket_name, s3_key)
            print(f"Upload OK: {s3_key}")
        except Exception as e:
            print(f"Erreur upload {s3_key} : {e}")

    flash("Upload terminé avec succès.")
    return redirect(url_for('main.index', prefix=prefix + '/' if prefix else ''))


@bp.route('/rename', methods=['POST'])
def rename_route():
    # Renomme un fichier ou dossier dans S3 (copie + suppression)
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')
    if not old_key or not new_key:
        flash("Clé source et destination requises.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(old_key)
    success, msg = obj.rename(old_key, new_key)
    flash(msg, "success" if success else "error")

    parent_prefix = '/'.join(new_key.rstrip('/').split('/')[:-1])
    if parent_prefix:
        parent_prefix += '/'
    return redirect(url_for('main.index', prefix=parent_prefix))


@bp.route('/move', methods=['POST'])
def move_route():
    # Déplace un fichier ou dossier dans S3 (copie + suppression)
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')
    if not old_key or not new_key:
        flash("Clé source ou destination manquante", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(old_key)
    success, message = obj.move(old_key, new_key)
    flash(message, "success" if success else "error")

    parent_prefix = '/'.join(new_key.rstrip('/').split('/')[:-1])
    if parent_prefix:
        parent_prefix += '/'
    return redirect(url_for('main.index', prefix=parent_prefix))
