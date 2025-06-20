import zipfile
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar

from boto3.resources.base import ServiceResource
from dotenv import load_dotenv

import config

load_dotenv()
import boto3
import botocore
import os
from botocore import exceptions


class S3Key:
    S3_RESOURCE: ClassVar[ServiceResource] = boto3.resource('s3', region_name=config.REGION)
    S3_CLIENT: ClassVar['X'] = boto3.client('s3', region_name=config.REGION)

    def __init__(self, bucket_name: str, path: str) -> None:
        self.bucket_name = bucket_name
        self.path = path.strip('/')

    @property
    def _parts(self) -> list[str]:
        return self.path.split('/')

    @property
    def parent(self) -> 'S3Directory | None':
        if self._parts:
            return S3Directory(self.bucket_name, '/'.join(self._parts[:-1]) + '/' if len(self._parts) > 1 else '')
        return None

    @abstractmethod
    def download(self, local_path: Path) -> Path:
        raise NotImplementedError

    def __str__(self) -> str:
        return f's3://{self.bucket_name}/{self.path}'


class S3Directory(S3Key):
    def download(self, local_path: Path) -> Path:
        z = self.create_zip()
        return Path(z.filename)

    def create_zip(self) -> zipfile.ZipFile:
        pass

    def list(self) -> tuple[list['S3Directory'], list['S3File']]:
        response = self.S3_CLIENT.list_objects_v2(Bucket=self.bucket_name, Prefix=self.path, Delimiter='/')
        folders = []
        files = []
        for cp in response.get('CommonPrefixes', []):
            folders.append(S3Directory(self.bucket_name, cp['Prefix']))

        # TODO: files

        return folders, files

    def remove(self) -> None:
        folders, files = self.list()
        # Supprimer tous les fichiers contenus dans le répertoire
        for file in files:
            file.remove()

        # Supprimer tous les sous-dossiers
        for folder in folders:
            folder.remove()

        # Maintenant que le répertoire est vide, on peut le supprimer
        # Supprimer le répertoire
        self.s3_client.delete_object(Bucket=self.bucket_name, Key=self.path)

    def list_folders(self, prefix='') -> list[S3Key]:
        response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix, Delimiter='/')
        folders = []

        for cp in response.get('CommonPrefixes', []):
            folders.append(cp['Prefix'])

        return folders

    @classmethod
    def upload(cls, local_path: Path, s3_path: str) -> 'S3Directory':
        if local_path.is_file():
            pass
        else:  # local_path est un dossier
            pass
        s3_dir = S3Directory(s3_path)
        return s3_dir

    def upload_folder(self, local_folder, s3_prefix):
        # On parcourt tout le dossier local (récursivement).
        for root, _, files in os.walk(
                local_folder):  # Ça parcourt tout local_folder et ses sous-dossiers. pour chaque dossier, il donne la liste des fichiers à l’intérieur.
            # root représente le dossier en cours et files les fichiers contenus dedans
            for file in files:
                # Chemin complet local du fichier
                local_file = os.path.join(root, file)  # tout jusqu'au dossier courant + le nom du fichier

                # Chemin relatif du fichier par rapport au dossier de base
                relative_path = os.path.relpath(local_file, local_folder)

                # On remplace les séparateurs Windows '\' par '/' pour S3
                relative_path = relative_path.replace(os.sep, '/')

                # Clé S3 finale = préfixe + chemin relatif du fichier
                s3_key = f"{s3_prefix.rstrip('/')}/{relative_path}"

                # On upload le fichier local vers cette clé dans le bucket S3
                self.bucket.upload_file(local_file, s3_key)

                # On upload chaque fichier individuellement, car S3 ne gère pas les dossiers physiques,
                # donc on simule la structure du dossier en créant des clés S3 qui reflètent les chemins relatifs.

    def remove(self, key):
        try:
            # Si la clé se termine par '/', on considère que c'est un "dossier".
            if key.endswith('/'):
                # On récupère la liste de tous les objets S3 dont la clé commence par ce préfixe
                objects_to_delete = list(self.bucket.objects.filter(Prefix=key))

                # Si aucun objet n'a été trouvé avec ce préfixe
                if not objects_to_delete:  # POUR LES DOSSIERS VIDES
                    try:
                        # On tente de charger l'objet correspondant au "dossier" vide (clé avec '/')
                        obj = self.s3.Object(self.bucket_name, key)
                        obj.load()  # Cette ligne vérifie si l'objet existe

                        # Si l'objet existe, on le supprime
                        obj.delete()
                        return True, f"Dossier vide supprimé avec succès : {key}"

                    # Si l'objet n'existe pas, boto3 lèvera une exception ClientError
                    except botocore.exceptions.ClientError as e:
                        # Si l'erreur est une "404 Not Found", on retourne que le dossier n'existe pas
                        if e.response['Error']['Code'] == '404':
                            return False, "Ce dossier est déjà vide et n'existe pas en tant qu'objet."
                        else:
                            # Si c'est une autre erreur, on la remonte
                            raise

                else:
                    # Si on a trouvé des objets à supprimer, on prépare une liste avec leur clé
                    delete_keys = [{'Key': obj.key} for obj in objects_to_delete]

                    # On supprime tous ces objets en une seule requête
                    response = self.bucket.delete_objects(Delete={'Objects': delete_keys})

                    # On récupère la liste des objets effectivement supprimés
                    deleted = response.get('Deleted', [])

                    # On récupère la liste des erreurs éventuelles
                    errors = response.get('Errors', [])

                    # S'il y a des erreurs, on les retourne en message d'échec
                    if errors:
                        return False, f"Erreurs lors de la suppression : {errors}"
                    else:
                        # Sinon, on indique le nombre d'objets supprimés avec succès
                        return True, f"{len(deleted)} objets supprimés avec succès sous le préfixe {key}"

        # En cas d'erreur non gérée, on capture l'exception et on retourne un message d'erreur
        except Exception as e:
            return False, f"Erreur lors de la suppression : {e}"

    def copy(self, source_key, dest_key):
        try:
            if source_key.endswith('/'):  # parce que les dossiers finissent en /
                objects_to_copy = list(self.bucket.objects.filter(Prefix=source_key))  # sourceKey + /
                if not objects_to_copy:
                    return "Aucun objet trouvé à ce chemin source."

                for obj in objects_to_copy:
                    new_key = dest_key + obj.key[len(source_key):]
                    # permet de récupérer ce qu'il y a après la source key en gros :
                    # len(source_key) sert à enlever la partie commune du chemin de source et le obj.key[len(source_key):]
                    # sert donc simplement à donner ce qu'il y a après le préfixe source afin de copier coller directement sous la nouvelle destination.
                    copy_source = {'Bucket': self.bucket_name,
                                   'Key': obj.key}  # dico qui sert à indiquer quelle est la source à copier (quel bucket et quelle clé (chemin)
                    self.s3.Object(self.bucket_name, new_key).copy(
                        copy_source)  # effectue la copie en se servant de la nouvelle clé créée depuis la source
                return f"{len(objects_to_copy)} objets copiés avec succès."
        except Exception as e:
            return f"Erreur lors de la copie : {e}"

    def rename(self, key, renamed_key):
        if key.endswith('/') and not renamed_key.endswith('/'):
            renamed_key += '/'

        copy_result = self.copy(key, renamed_key)
        if isinstance(copy_result, str) and "Erreur" in copy_result:
            return False, f"Erreur pendant la copie : {copy_result}"

        remove_result, msg = self.remove(key)
        if not remove_result:
            return False, f"Copié mais erreur pendant la suppression : {msg}"

        return True, f"L'élément {key} a été renommé {renamed_key} avec succès !"

    def move(self, source_key, dest_key):
        try:
            # Appelle ta fonction copy
            copy_result = self.copy(source_key, dest_key)
            if isinstance(copy_result, str) and "Erreur" in copy_result:
                return False, f"Erreur pendant la copie : {copy_result}"

            # Supprime la source (fichier ou dossier)
            remove_result, msg = self.remove(source_key)
            if not remove_result:
                return False, f"Copié mais erreur lors de la suppression : {msg}"

            return True, f"{source_key} déplacé vers {dest_key} avec succès !"

        except Exception as e:
            return False, f"Erreur pendant le déplacement : {e}"


class S3File(S3Key):
    def list_files(self, prefix='') -> list[S3Key]:
        response = self.s3_client.list_objects_v2(Bucket=self.bucket_name, Prefix=prefix)
        files = []

        for obj in response.get('Contents', []):
            key = obj[
                'Key']  # obj est un dict donc Key renvoie la valeur associé à la clé 'Key', Key correspond au chemin, on l'extrait complètement
            if key == prefix:
                continue  # on ignore la "racine" elle-même, car si c'est la même pas besoin
            relative_path = key[
                            len(prefix):]  # on enlève tout ce qu'il y a avant le nom du fichier pour ne garder que ce nom
            if '/' not in relative_path:
                files.append(relative_path)

        return files

    def upload_file(self, local_file, s3_prefix) -> None:
        self.bucket.upload_file(local_file, s3_prefix)

    def download_file(self, key, filename):
        try:
            print(f"Téléchargement de s3://{self.bucket_name}/{key} vers {filename}")
            self.bucket.download_file(key, filename)
            print("Téléchargement réussi.")
            return True, "Le fichier a été téléchargé avec succès !"
        except Exception as e:
            print(f"Erreur: {e}")
            return False, f"Erreur lors du téléchargement : {e}"

    def remove(self, key):
        try:
            self.s3.Object(self.bucket_name, key).delete()
            return True, f"Fichier supprimé avec succès : {key}"

        except Exception as e:
            return False, f"Erreur lors de la suppression : {e}"

    def copy(self, source_key, dest_key):
        try:
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            self.s3.Object(self.bucket_name, dest_key).copy(copy_source)
            return "Fichier copié avec succès."
        except Exception as e:
            return f"Erreur lors de la copie : {e}"

    def move(self, source_key, dest_key):
        try:
            # Appelle ta fonction copy
            copy_result = self.copy(source_key, dest_key)
            if isinstance(copy_result, str) and "Erreur" in copy_result:
                return False, f"Erreur pendant la copie : {copy_result}"

            # Supprime la source (fichier ou dossier)
            remove_result, msg = self.remove(source_key)
            if not remove_result:
                return False, f"Copié mais erreur lors de la suppression : {msg}"

            return True, f"{source_key} déplacé vers {dest_key} avec succès !"

        except Exception as e:
            return False, f"Erreur pendant le déplacement : {e}"

    def rename(self, key, renamed_key):
        if key.endswith('/') and not renamed_key.endswith('/'):
            renamed_key += '/'

        copy_result = self.copy(key, renamed_key)
        if isinstance(copy_result, str) and "Erreur" in copy_result:
            return False, f"Erreur pendant la copie : {copy_result}"

        remove_result, msg = self.remove(key)
        if not remove_result:
            return False, f"Copié mais erreur pendant la suppression : {msg}"

        return True, f"L'élément {key} a été renommé {renamed_key} avec succès !"

#
