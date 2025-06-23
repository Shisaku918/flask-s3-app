from dotenv import load_dotenv
from werkzeug.datastructures import FileStorage

load_dotenv()

import os
from abc import abstractmethod
from pathlib import Path
from typing import ClassVar
import boto3
from boto3.resources.base import ServiceResource

import config
import zipfile


class S3Key:
    # Ressources S3 partagées (client et resource) configurées avec la région depuis config
    S3_RESOURCE: ClassVar[ServiceResource] = boto3.resource('s3', region_name=config.REGION)
    S3_CLIENT: ClassVar = boto3.client('s3', region_name=config.REGION)

    def __init__(self, bucket_name: str, path: str) -> None:
        # Initialise un objet S3Key avec un nom de bucket et un chemin dans ce bucket
        self.bucket_name = bucket_name
        self.path = path.lstrip('/')  # Nettoyage des '/' en début et fin du chemin

    @classmethod
    def get_from_key(cls, bucket_name: str, key: str) -> 'S3Directory | S3File':
        # Retourne un objet S3Directory ou S3File selon si la clé est un dossier ou un fichier
        if key == '' or key.endswith('/'):
            return S3Directory(bucket_name, key)
        return S3File(bucket_name, key)

    @property
    def _parts(self) -> list[str]:
        # Retourne la liste des parties du chemin, séparées par '/'
        return [part for part in self.path.split('/') if part]

    @property
    def name(self) -> str:
        return self._parts[-1]

    @property
    def parent(self) -> 'S3Directory | None':
        # Retourne le dossier parent sous forme d'objet S3Directory, ou None si à la racine
        if self._parts:
            # Reconstruit le chemin du parent en joignant toutes les parties sauf la dernière
            return S3Directory(self.bucket_name, '/'.join(self._parts[:-1]) + '/' if len(self._parts) > 1 else '')
        return None

    def relative_to(self, ancestor: 'S3Directory') -> str:
        # Retourne le chemin relatif au dossier ancestor
        if self.bucket_name != ancestor.bucket_name or (
                not ancestor.is_root() and not self.path.startswith(ancestor.path)):
            raise ValueError(f"Le répertoire {ancestor.path} n'est pas un parent de {self.path}")
        return '/'.join(self._parts[len(ancestor._parts):])

    @abstractmethod
    def is_folder(self) -> bool:
        # Méthode abstraite à implémenter pour savoir si l'objet est un dossier
        raise NotImplementedError

    @abstractmethod
    def is_file(self) -> bool:
        # Méthode abstraite à implémenter pour savoir si l'objet est un fichier
        raise NotImplementedError

    def is_root(self) -> bool:
        return self.path == ''

    @abstractmethod
    def download(self, local_path: Path) -> Path:
        # Méthode abstraite à implémenter pour télécharger le contenu localement
        raise NotImplementedError

    def __str__(self) -> str:
        # Représentation en format URL S3 classique
        return f's3://{self.bucket_name}/{self.path}'

    def __repr__(self) -> str:
        # Représentation en format URL S3 classique
        return str(self)


class S3Directory(S3Key):

    def is_folder(self) -> bool:
        return True

    def is_file(self) -> bool:
        return False

    @classmethod
    def create(cls, bucket_name: str, path: str) -> 'S3Directory':
        cls.S3_CLIENT.put_object(Bucket=bucket_name, Key=path)

    def download(self, local_path: Path) -> Path:
        z = self.create_zip(local_path)
        print(f"📥 Archive téléchargée dans : {local_path}")
        return Path(z.filename)

    def create_zip(self, zip_path: Path) -> zipfile.ZipFile:
        import tempfile

        files = self.list_all_files_recursively()
        if not files:
            raise ValueError(f"Aucun fichier à zipper dans {self.path}")

        temp_dir = Path(tempfile.mkdtemp())

        for s3_key in files:
            relative_path = Path(s3_key).relative_to(self.path)
            local_file_path = temp_dir / relative_path
            local_file_path.parent.mkdir(parents=True, exist_ok=True)

            self.S3_RESOURCE.Bucket(self.bucket_name).download_file(s3_key, str(local_file_path))

        zip_path.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for file_path in temp_dir.rglob('*'):
                if file_path.is_file():
                    arcname = file_path.relative_to(temp_dir)
                    zipf.write(file_path, arcname)

        print(f"📦 Archive créée : {zip_path}")
        return zipfile.ZipFile(zip_path)

    def list_all_files_recursively(self) -> list[str]:
        """
        Liste récursivement tous les fichiers sous le préfixe self.path
        """
        prefix = self.path
        if prefix and not prefix.endswith('/'):
            prefix += '/'

        paginator = self.S3_CLIENT.get_paginator('list_objects_v2')
        page_iterator = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)

        all_files = []
        for page in page_iterator:
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('/'):  # Ignore les "pseudo-dossiers"
                    all_files.append(key)

        return all_files

    def list_content(self) -> tuple[list['S3Directory'], list['S3File']]:
        # Liste les sous-dossiers et fichiers dans ce dossier S3
        prefix = self.path
        if prefix and not prefix.endswith('/'):
            prefix += '/'

        # Appelle l'API S3 pour lister objets et préfixes (dossiers)
        response = self.S3_CLIENT.list_objects_v2(
            Bucket=self.bucket_name,
            Prefix=prefix,
            Delimiter='/'
        )
        folders = []
        files = []

        # Extraire les dossiers (préfixes communs)
        for cp in response.get('CommonPrefixes', []):
            folders.append(S3Directory(self.bucket_name, cp['Prefix']))
        # Extraire les fichiers (clés qui ne finissent pas par '/')
        for obj in response.get('Contents', []):
            if not obj['Key'].endswith('/'):
                files.append(S3File(self.bucket_name, obj['Key']))

        return folders, files

    def remove(self) -> None:
        folders, files = self.list_content()
        for folder in folders:
            folder.remove()
        for file in files:
            file.remove()
        try:
            self.S3_CLIENT.delete_object(Bucket=self.bucket_name, Key=self.path)
        except self.S3_CLIENT.exceptions.NoSuchKey:
            pass

    def upload_from_storage(self, files: list[FileStorage]) -> None:
        for file in files:
            s3_key = os.path.join(self.path, file.filename)
            self.S3_CLIENT.upload_fileobj(file.stream, self.bucket_name, s3_key)

    @classmethod
    def upload(cls, local_path: Path, s3_path: str) -> 'S3Directory':
        # Upload un fichier ou dossier local vers S3 sous le chemin s3_path
        bucket_name = config.BUCKET_NAME
        s3_path = s3_path.strip('/')
        s3_dir = cls(bucket_name, s3_path)

        if local_path.is_file():
            # Upload simple pour un fichier unique
            s3_key = f"{s3_path}/{local_path.name}" if s3_path else local_path.name
            print(f"📤 Upload fichier unique : {local_path} → clé S3 '{s3_key}'")
            cls.S3_RESOURCE.Bucket(bucket_name).upload_file(str(local_path), s3_key)
        else:
            # Upload récursif pour un dossier et ses fichiers
            for root, _, files in os.walk(local_path):
                for file in files:
                    local_file = Path(root) / file
                    relative_path = local_file.relative_to(local_path).as_posix()
                    s3_key = f"{s3_path}/{relative_path}" if s3_path else relative_path
                    print(f"📤 Upload : {local_file} → clé S3 '{s3_key}'")
                    cls.S3_RESOURCE.Bucket(bucket_name).upload_file(str(local_file), s3_key)

        # Liste les objets uploadés et affiche
        folders, files = s3_dir.list_content()
        print(f"Upload effectué dans : s3://{bucket_name}/{s3_path}")
        print("Dossiers présents :")
        for d in folders:
            print(f"  - {d}")
        print("Fichiers présents :")
        for f in files:
            print(f"  - {f}")

        return s3_dir

    def copy(self, source_key, dest_key):
        # Copie tous les objets sous source_key vers dest_key dans ce bucket
        try:
            objects_to_copy = list(self.S3_RESOURCE.Bucket(self.bucket_name).objects.filter(Prefix=source_key))
            if not objects_to_copy:
                return "Aucun objet trouvé à ce chemin source."

            for obj in objects_to_copy:
                # Nouveau chemin construit en remplaçant le préfixe source par destination
                new_key = dest_key + obj.key[len(source_key):]
                copy_source = {'Bucket': self.bucket_name, 'Key': obj.key}
                self.S3_RESOURCE.Object(self.bucket_name, new_key).copy(copy_source)
            return f"{len(objects_to_copy)} objets copiés avec succès."
        except Exception as e:
            return f"Erreur lors de la copie : {e}"

    def rename(self, key, renamed_key):
        # Renomme un dossier ou un objet dans S3 en copiant puis supprimant l'original
        if key.endswith('/') and not renamed_key.endswith('/'):
            renamed_key += '/'
        copy_result = self.copy(key, renamed_key)
        if isinstance(copy_result, str) and "Erreur" in copy_result:
            return False, f"Erreur pendant la copie : {copy_result}"

        success, msg = self.remove()
        if not success:
            return False, f"Copié mais erreur pendant la suppression : {msg}"

        return True, f"L'élément {key} a été renommé {renamed_key} avec succès !"

    def move(self, source_key, dest_key):
        # Déplace un dossier ou objet en le copiant puis supprimant l'original
        # Gestion des slashs pour garder cohérence chemins
        if source_key.endswith('/') and not dest_key.endswith('/'):
            dest_key = dest_key.rstrip('/') + '/'
        if not source_key.endswith('/') and dest_key.endswith('/') and not dest_key.endswith(
                source_key.split('/')[-1] + '/'):
            dest_key += source_key.split('/')[-1]

        copy_res = self.copy(source_key, dest_key)
        if isinstance(copy_res, str) and "Erreur" in copy_res:
            return False, copy_res

        success, msg = self.remove()
        if not success:
            return False, msg

        return True, f"{source_key} déplacé vers {dest_key}"


class S3File(S3Key):

    def is_folder(self) -> bool:
        return False

    def is_file(self) -> bool:
        return True

    def get_download_url(self):
        return self.S3_CLIENT.generate_presigned_url(
            'get_object',
            Params={
                'Bucket': self.bucket_name,
                'Key': self.path,
                'ResponseContentDisposition': f'attachment; filename="{self.name}"'
            },
            ExpiresIn=3600
        )

    def download(self, local_path: Path) -> Path:
        # Télécharge le fichier S3 vers le chemin local
        try:
            self.S3_RESOURCE.Bucket(self.bucket_name).download_file(self.path, str(local_path))
            return local_path
        except Exception as e:
            raise RuntimeError(f"Erreur lors du téléchargement : {e}")

    def remove(self):
        # Supprime le fichier S3
        self.S3_CLIENT.delete_object(Bucket=self.bucket_name, Key=self.path)

    def copy(self, source_key, dest_key):
        # Copie un fichier d'un chemin source à un chemin destination dans le bucket
        try:
            copy_source = {'Bucket': self.bucket_name, 'Key': source_key}
            self.S3_RESOURCE.Object(self.bucket_name, dest_key).copy(copy_source)
            return "Fichier copié avec succès."
        except Exception as e:
            return f"Erreur lors de la copie : {e}"

    def rename(self, key, renamed_key):
        # Renomme un fichier via copie puis suppression
        if key.endswith('/') and not renamed_key.endswith('/'):
            renamed_key += '/'
        copy_result = self.copy(key, renamed_key)
        if isinstance(copy_result, str) and "Erreur" in copy_result:
            return False, f"Erreur pendant la copie : {copy_result}"

        return self.remove()

    def move(self, source_key, dest_key):
        # Déplace un fichier via copie puis suppression
        copy_result = self.copy(source_key, dest_key)
        if isinstance(copy_result, str) and "Erreur" in copy_result:
            return False, f"Erreur pendant la copie : {copy_result}"

        return self.remove()
