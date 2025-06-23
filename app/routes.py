import os
import tempfile
from pathlib import Path

from flask import Blueprint, request, render_template, redirect, url_for, flash, send_file

import config
from app.s3_utils import S3Directory, S3Key

# Création du blueprint Flask 'main'
bp = Blueprint('main', __name__)

# Chargement des infos AWS depuis variables d'environnement ou config
region = os.getenv("AWS_REGION", config.REGION)
bucket_name = os.getenv("AWS_BUCKET_NAME", config.BUCKET_NAME)


@bp.route('/')
def index():
    # Page principale, liste les fichiers/dossiers sous un prefix donné
    prefix = request.args.get('prefix', '')

    directory = S3Directory(bucket_name, prefix)
    folders, files = directory.list_content()

    # parent_prefix = ''
    # if dir_obj.parent:
    #     parent_prefix = dir_obj.parent.path

    # Nettoyage des noms (relatifs au prefix)
    prefix_slash = prefix + '/' if prefix and not prefix.endswith('/') else prefix
    # folders_clean = [(f, f[len(prefix_slash):]) for f in folders] if prefix else [(f, f) for f in folders]
    # files_clean = [f[len(prefix_slash):] for f in files] if prefix else files

    # Affiche la page index.html avec les listes de dossiers/fichiers
    return render_template(
        'index.html',
        directory=directory,
        folders=folders,
        files=files,
    )


@bp.route('/delete', methods=['POST'])
def delete():
    # Supprime un fichier ou dossier S3 (clé reçue via formulaire)
    key = request.form.get('key')
    obj = S3Key.get_from_key(bucket_name, key)
    parent_path = obj.parent.path
    try:
        obj.remove()
    except Exception as e:
        message = f"Erreur lors de la suppression de {key}: {e}"
        flash(message, "error")
    else:
        message = f"{key} supprimé avec succès."
        flash(message, "success")
    return redirect(url_for('main.index', prefix=parent_path))


# @bp.route("/download_zip/<path:s3_path>")
# def download_zip(s3_path):
#     try:
#         s3_dir = S3Directory(config.BUCKET_NAME, s3_path)
#         with tempfile.TemporaryDirectory() as tmpdirname:
#             zip_path = Path(tmpdirname) / f"{Path(s3_path).name or 'archive'}.zip"
#             s3_dir.download(zip_path)
#             return send_file(
#                 zip_path,
#                 as_attachment=True,
#                 download_name=zip_path.name,
#                 mimetype='application/zip'
#             )
#     except Exception as e:
#         flash(f"Erreur lors du téléchargement : {e}", "danger")
#         return redirect(url_for("index"))  # rediriger vers la page d'accueil ou listing


@bp.route('/download')
def download():
    # Télécharge un fichier ou dossier depuis S3
    key = request.args.get('key')
    if not key:
        flash("Clé manquante pour téléchargement.", "error")
        return redirect(request.referrer or url_for('main.index'))

    obj = S3Key.get_from_key(bucket_name, key)
    if obj.is_folder():
        # Si dossier, on crée un zip temporaire et on l'envoie
        try:
            with tempfile.TemporaryDirectory() as tmpdirname:
                zip_path = Path(tmpdirname) / f"{obj.name}.zip"
            obj.create_zip(zip_path)
            return send_file(
                zip_path,
                as_attachment=True,
                download_name=zip_path.name,
                mimetype='application/zip'
            )
        except Exception as e:
            flash(f"Erreur lors du téléchargement du dossier : {e}", "error")
            return redirect(request.referrer or url_for('main.index'))
    else:
        # Si fichier, on génère un lien pré-signé pour téléchargement direct
        try:
            return redirect(obj.get_download_url())
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
        directory = S3Directory.create(bucket_name, new_folder_key)
        flash(f"Dossier '{folder_name}' créé avec succès.")
    except Exception as e:
        flash(f"Erreur lors de la création du dossier : {e}", "error")
    else:
        return redirect(request.referrer or url_for('main.index', prefix=prefix))


@bp.route('/upload', methods=['POST'])
def upload():
    # Upload un ou plusieurs fichiers via formulaire sous un prefix donné
    prefix = request.form.get('prefix', '').strip('/')
    files = request.files.getlist('files')
    if not files:
        flash("Aucun fichier reçu.", "error")
        return redirect(request.referrer or url_for('main.index'))
    directory = S3Directory(bucket_name, prefix)
    directory.upload_from_storage(files)
    print(prefix)
    print(files)
    print(type(files[0]))
    print(dir(files[0]))

    flash("Upload terminé avec succès.")
    return redirect(url_for('main.index', prefix=prefix))

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
