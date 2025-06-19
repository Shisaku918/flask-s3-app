from flask import Blueprint, request, render_template, redirect, url_for, flash
from app.s3_utils import *

bp = Blueprint('main', __name__)

@bp.route('/')
def index():
    prefix = request.args.get('prefix', '')  # récupère le dossier actuel
    key = S3Directory(prefix)
    folders, files = list_s3_objects(prefix)

    # # Pour la navigation "retour"
    # if prefix:
    #     parts = prefix.strip('/').split('/') #enlève les / de début et de fin et permet de sectionner les différents éléments en une liste d'éléments (chemin)
    #     parent_prefix = '/'.join(parts[:-1]) + '/' if len(parts) > 1 else ''
    #
    #     # parts[:-1] prend tous les éléments sauf le dernier (le dossier courant)
    #
    #     # '/'.join(...) recrée un chemin
    #
    #     # On ajoute / à la fin pour que le préfixe reste correct pour S3
    #
    #     # Si on est tout en haut (prefix = 'dossier/'), on revient à la racine ('')
    #
    # else:
    #     parent_prefix = None

    return render_template('index.html', folders=folders, files=files, prefix=prefix, parent_prefix=str(key.parent))


@bp.route('/delete', methods=['POST'])
def delete():
    key = request.form.get('key')

    if not key:
        flash("Aucune clé spécifiée.", "error")
        return redirect(request.referrer or url_for('main.index'))

    success, message = remove(key)
    flash(message, "success" if success else "error")

    # Redirection vers le dossier parent
    parent_prefix = '/'.join(key.rstrip('/').split('/')[:-1])
    if parent_prefix:
        parent_prefix += '/'
    return redirect(url_for('main.index', prefix=parent_prefix))


@bp.route('/download')
def download():
    key = request.args.get('key')
    if not key:
        flash("Clé manquante pour téléchargement.", "error")
        return redirect(request.referrer or url_for('index'))

    try:
        url = s3_client.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': bucket_name,
                'Key': key,
                'ResponseContentDisposition': f'attachment; filename="{key.split("/")[-1]}"'
            },
            ExpiresIn=3600
        )
        print("URL pré-signée:", url)
        return redirect(url)
    except Exception as e:
        flash(f"Erreur lors de la génération du lien de téléchargement : {e}", "error")
        return redirect(request.referrer or url_for('index'))

@bp.route('/create-folder', methods=['POST'])
def create_folder():
    prefix = request.form.get('prefix', '').strip()
    folder_name = request.form.get('folder_name', '').strip()

    if not folder_name:
        flash("Nom de dossier vide.", "error")
        return redirect(request.referrer or url_for('main.index'))

    # Forme finale : prefix + folder_name + /
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

    print(f"Prefix reçu : {prefix}")
    print(f"Nombre de fichiers reçus : {len(files)}")

    if not files:
        return "Aucun fichier reçu", 400

    for file in files:
        relative_path = file.filename  # contient le chemin relatif (ex: "monDossier/image.png")
        s3_key = prefix + relative_path

        print(f"Uploading: {s3_key}")
        try:
            s3_client.upload_fileobj(file, bucket_name, s3_key)
        except Exception as e:
            print(f"Erreur upload {file.filename} : {e}")
            return f"Erreur lors de l'upload de {file.filename} : {e}", 500

    return "Upload terminé", 200



@bp.route('/rename', methods=['POST'])
def rename_route():
    old_key = request.form.get('old_key')
    new_key = request.form.get('new_key')

    if not old_key or not new_key:
        flash("Clé source et destination requises.", "error")
        return redirect(request.referrer or url_for('main.index'))

    success, msg = rename(old_key, new_key)
    flash(msg, "success" if success else "error")

    # Rediriger vers le dossier parent du nouveau chemin
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

    success, message = move(old_key, new_key)
    flash(message, "success" if success else "error")
    return redirect(request.referrer or url_for('main.index'))