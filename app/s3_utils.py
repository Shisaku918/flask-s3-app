import boto3
import os
import botocore
from botocore import exceptions

region = os.getenv("AWS_REGION")
bucket_name = os.getenv("AWS_BUCKET_NAME")

s3 = boto3.resource('s3', region_name=region)
s3_client = boto3.client('s3', region_name=region)
bucket = s3.Bucket(bucket_name)

def list_s3_objects(prefix=''):
    response = s3_client.list_objects_v2(
        Bucket=bucket_name,
        Prefix=prefix,
        Delimiter='/'  # Important pour simuler une structure en dossier
    )

    folders = []
    files = []

    # Gestion des sous-dossiers immédiats
    for cp in response.get('CommonPrefixes', []):
        folders.append(cp['Prefix'])

    # Gestion des fichiers à ce niveau uniquement (pas dans sous-sous-dossier)
    for obj in response.get('Contents', []):
        key = obj['Key']
        if key == prefix:
            continue  # on ignore la "racine" elle-même
        relative_path = key[len(prefix):]
        if '/' not in relative_path:
            files.append(relative_path)

    return folders, files



def upload_file(local_path, stocking_folder): #local_path = là où se trouve le fichier à upload et stocking_folder = là où il faut le stocker
    filename = os.path.basename(local_path)
    stocking_path = f"{stocking_folder}/{filename}"

    try:
        bucket.upload_file(local_path, stocking_path)
        return True, "Fichier uploadé avec succès"
    except Exception as e:
        return False, f"Erreur : {e}"


def download_file(key, filename): #key = chemin du fichier à download et filename = où le stocker

    try:
            bucket.download_file(key, filename)
            return True, "Le fichier a été téléchargé avec succès !"
    except Exception as e:
        return False, f"Erreur lors du téléchargement : {e}"


def remove(key):
    try:
        if key.endswith('/'):
            # 🔍 Étape 1 : lister tous les objets dans ce "dossier"
            objects_to_delete = bucket.objects.filter(Prefix=key)
            keys = [{'Key': obj.key} for obj in objects_to_delete]

            # 🔍 Étape 2 : si aucun objet trouvé, tenter suppression du dossier vide
            if not keys:
                try:
                    # Peut-être qu’un objet vide 'prefix/' existe ?
                    s3.Object(bucket_name, key).load()
                    s3.Object(bucket_name, key).delete()
                    return True, f"Dossier vide supprimé avec succès : {key}"
                except botocore.exceptions.ClientError as e:
                    if e.response['Error']['Code'] == '404':
                        return False, "Ce dossier est déjà vide et n'existe pas en tant qu'objet."
                    else:
                        raise  # Autre erreur → on relance
            else:
                # ✂️ Étape 3 : supprimer tous les objets
                response = bucket.delete_objects(Delete={'Objects': keys})
                deleted = response.get('Deleted', [])
                return True, f"{len(deleted)} objets supprimés dans le dossier '{key}'."

        else:
            # 🗑️ Suppression d'un fichier
            s3.Object(bucket_name, key).delete()
            return True, f"Fichier supprimé avec succès : {key}"

    except Exception as e:
        return False, f"Erreur lors de la suppression : {e}"




def copy(source_key, dest_key):
    try:
        if source_key.endswith('/'):  # parce que les dossiers finissent en /
            objects_to_copy = list(bucket.objects.filter(Prefix=source_key))  # sourceKey + /
            if not objects_to_copy:
                return  "Aucun objet trouvé à ce chemin source."

            for obj in objects_to_copy:
                new_key = dest_key + obj.key[len(source_key):]
                # permet de récupérer ce qu'il y a après la source key en gros :
                # len(source_key) sert à enlever la partie commune du chemin de source et le obj.key[len(source_key):]
                # sert donc simplement à donner ce qu'il y a après le préfixe source afin de copier coller directement sous la nouvelle destination.
                copy_source = {'Bucket': bucket_name,
                               'Key': obj.key}  # dico qui sert à indiquer quelle est la source à copier (quel bucket et quelle clé (chemin)
                s3.Object(bucket_name, new_key).copy(
                    copy_source)  # effectue la copie en se servant de la nouvelle clé créée depuis la source
            return f"{len(objects_to_copy)} objets copiés avec succès."
        else:
            copy_source = {'Bucket': bucket_name, 'Key': source_key}
            s3.Object(bucket_name, dest_key).copy(copy_source)
            return "Fichier copié avec succès."
    except Exception as e:
        return f"Erreur lors de la copie : {e}"


def rename(key, renamed_key):
    if key.endswith('/') and not renamed_key.endswith('/'):
        renamed_key += '/'

    copy_result = copy(key, renamed_key)
    if isinstance(copy_result, str) and "Erreur" in copy_result:
        return False, f"Erreur pendant la copie : {copy_result}"

    remove_result, msg = remove(key)
    if not remove_result:
        return False, f"Copié mais erreur pendant la suppression : {msg}"

    return True, f"L'élément {key} a été renommé {renamed_key} avec succès !"


def move(source_key, dest_key):
    try:
        # Appelle ta fonction copy
        copy_result = copy(source_key, dest_key)
        if isinstance(copy_result, str) and "Erreur" in copy_result:
            return False, f"Erreur pendant la copie : {copy_result}"

        # Supprime la source (fichier ou dossier)
        remove_result, msg = remove(source_key)
        if not remove_result:
            return False, f"Copié mais erreur lors de la suppression : {msg}"

        return True, f"{source_key} déplacé vers {dest_key} avec succès !"

    except Exception as e:
        return False, f"Erreur pendant le déplacement : {e}"


def get_parent_prefix(prefix):
    if not prefix:
        return None
    parts = prefix.rstrip('/').split('/')
    if len(parts) <= 1:
        return ''
    return '/'.join(parts[:-1]) + '/'


def get_folders_and_files(prefix):
    paginator = s3_client.get_paginator('list_objects_v2')
    result = paginator.paginate(Bucket=bucket_name, Prefix=prefix, Delimiter='/')

    folders = []
    files = []

    for page in result:
        # Les dossiers "communs" sont dans CommonPrefixes
        if 'CommonPrefixes' in page:
            for cp in page['CommonPrefixes']:
                folders.append(cp['Prefix'])

        # Les fichiers dans Contents
        if 'Contents' in page:
            for obj in page['Contents']:
                # Exclure le dossier lui-même (clé égale au prefix)
                if obj['Key'] != prefix:
                    files.append(obj['Key'])

    # Pour ne garder que le nom relatif à ce prefix (et pas le chemin complet)
    folders = [f[len(prefix):] for f in folders]
    files = [f[len(prefix):] for f in files]

    return folders, files