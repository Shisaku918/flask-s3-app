from flask import Blueprint, request, render_template, redirect, url_for, flash
from app.s3_utils import S3File, S3Directory
import boto3
import os

bp = Blueprint('main', __name__)
region = os.getenv("AWS_REGION")
bucket_name = os.getenv("AWS_BUCKET_NAME")

s3 = boto3.resource('s3', region_name=region)
s3_client = boto3.client('s3', region_name=region)
bucket = s3.Bucket(bucket_name)


def get_s3_object(key: str) -> S3File | S3Directory:
    if key.endswith('/'):
        return S3Directory(key)
    return S3File(key)


@bp.route('/')
def index():
    prefix = request.args.get('prefix', '')  # dossier actuel

    dir_obj = S3Directory(prefix)
    file_obj = S3File(prefix)

    folders = dir_obj.list_folders(prefix=prefix)
    files = file_obj.list_files(prefix=prefix)

    parent_prefix = str(dir_obj.parent) if dir_obj.parent else ''

    return render_template('index.html', folders=folders, files=files, prefix=prefix, parent_prefix=parent_prefix)



@bp.route('/delete', methods=['POST'])
def delete():
    key = request.form.get('key')

    if not key:
        flash("Aucune clé spécifiée.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(key)
    success, message = obj.remove(key)
    flash(message, "success" if success else "error")

    parent_prefix = '/'.join(key.rstrip('/').split('/')[:-1])
    if parent_prefix:
        parent_prefix += '/'
    return redirect(url_for('main.index', prefix=parent_prefix))


@bp.route('/download')
def download():
    key = request.args.get('key')
    if not key:
        flash("Clé manquante pour téléchargement.", "error")
        return redirect(request.referrer or url_for('main.index'))

    try:
        # Génération de l'URL pré-signée via boto3 client
        presigned_url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name,
                'Key': key,
                'ResponseContentDisposition': f'attachment; filename="{key.split("/")[-1]}"'
            },
            ExpiresIn=3600
        )
        return redirect(presigned_url)
    except Exception as e:
        flash(f"Erreur lors de la génération du lien de téléchargement : {e}", "error")
        return redirect(request.referrer or url_for('main.index'))


@bp.route('/create-folder', methods=['POST'])
def create_folder():
    prefix = request.form.get('prefix', '').strip()
    folder_name = request.form.get('folder_name', '').strip()

    if not folder_name:
        flash("Nom de dossier vide.", "error")
        return redirect(request.referrer or url_for('main.index'))

    if prefix and not prefix.endswith('/'):
        prefix += '/'
    new_folder_key = prefix + folder_name.strip('/') + '/'

    try:
        s3_client.put_object(Bucket=bucket_name, Key=new_folder_key)
        flash(f"Dossier '{folder_name}' créé avec succès.")
    except Exception as e:
        flash(f"Erreur lors de la création du dossier : {e}", "error")

    return redirect(request.referrer or url_for('main.index', prefix=prefix))


@bp.route('/upload', methods=['POST'])
def upload():
    prefix = request.form.get('prefix', '') or ''
    files = request.files.getlist('files')

    if not files:
        return "Aucun fichier reçu", 400

    for file in files:
        relative_path = file.filename
        s3_key = prefix + relative_path

        try:
            s3_client.upload_fileobj(file, bucket_name, s3_key)
        except Exception as e:
            return f"Erreur lors de l'upload de {file.filename} : {e}", 500

    return "Upload terminé", 200


@bp.route('/rename', methods=['POST'])
def rename_route():
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')

    if not old_key or not new_key:
        flash("Clé source et destination requises.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(old_key)
    success, msg = obj.rename(old_key, new_key)  # Passe les clés complètes
    flash(msg, "success" if success else "error")

    parent_prefix = '/'.join(new_key.rstrip('/').split('/')[:-1])
    if parent_prefix:
        parent_prefix += '/'
    return redirect(url_for('main.index', prefix=parent_prefix))


@bp.route('/move', methods=['POST'])
def move_route():
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')

    if not old_key or not new_key:
        flash("Clé source ou destination manquante", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = get_s3_object(old_key)
    success, message = obj.move(old_key, new_key)  # Passe les clés complètes
    flash(message, "success" if success else "error")

    return redirect(request.referrer or url_for('main.index'))
