import shutil
from abc import ABC, abstractmethod
from pathlib import Path
from typing import ClassVar, Union, List
import os
import tempfile
import zipfile

import boto3
from boto3.resources.base import ServiceResource

import config
from werkzeug.datastructures import FileStorage


class S3Key(ABC):
    S3_RESOURCE: ClassVar[ServiceResource] = boto3.resource('s3', region_name=config.REGION)
    S3_CLIENT: ClassVar = boto3.client('s3', region_name=config.REGION)

    def __init__(self, bucket_name: str, path: str) -> None:
        self.bucket_name = bucket_name
        self.path = path.lstrip('/')

    @classmethod
    def get_from_key(cls, bucket_name: str, key: str) -> Union['S3Directory', 'S3File']:
        """Retourne un objet S3Directory ou S3File selon la clé."""
        if key == '' or key.endswith('/'):
            return S3Directory(bucket_name, key)
        return S3File(bucket_name, key)

    @property
    def _parts(self) -> List[str]:
        return [p for p in self.path.split('/') if p]

    @property
    def name(self) -> str:
        return self._parts[-1] if self._parts else ''

    @property
    def parent(self) -> Union['S3Directory', None]:
        if not self._parts:
            return None
        parent_path = '/'.join(self._parts[:-1])
        if parent_path:
            parent_path += '/'
        return S3Directory(self.bucket_name, parent_path)

    @abstractmethod
    def is_folder(self) -> bool:
        ...

    @abstractmethod
    def is_file(self) -> bool:
        ...

    def is_root(self) -> bool:
        return self.path == ''

    @abstractmethod
    def download(self, local_path: Path) -> Path:
        ...

    def __str__(self) -> str:
        return f's3://{self.bucket_name}/{self.path}'


class S3Directory(S3Key):

    def is_folder(self) -> bool:
        return True

    def is_file(self) -> bool:
        return False

    @classmethod
    def create(cls, bucket_name: str, path: str) -> None:
        """Crée un dossier (objet S3 avec clé finissant par /)."""
        if not path.endswith('/'):
            path += '/'
        try:
            cls.S3_CLIENT.put_object(Bucket=bucket_name, Key=path)
        except Exception as e:
            raise RuntimeError(f"Impossible de créer le dossier {path} : {e}")

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

    def list_all_files_recursively(self) -> List[str]:
        """Liste récursivement toutes les clés fichiers sous ce dossier."""
        prefix = self.path
        if prefix and not prefix.endswith('/'):
            prefix += '/'
        paginator = self.S3_CLIENT.get_paginator('list_objects_v2')
        try:
            pages = paginator.paginate(Bucket=self.bucket_name, Prefix=prefix)
        except Exception as e:
            raise RuntimeError(f"Erreur pagination pour {self.path} : {e}")

        all_files = []
        for page in pages:
            for obj in page.get('Contents', []):
                key = obj['Key']
                if not key.endswith('/'):
                    all_files.append(key)
        return all_files

    def download(self, local_path: Path) -> Path:
        """Télécharge le dossier sous forme d’archive zip locale."""
        zip_path = local_path.with_suffix('.zip') if not local_path.suffix else local_path
        files = self.list_all_files_recursively()
        if not files:
            raise RuntimeError(f"Aucun fichier dans {self.path} à zipper")

        temp_dir = Path(tempfile.mkdtemp())
        try:
            for key in files:
                rel = Path(key).relative_to(self.path)
                dst = temp_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                self.S3_RESOURCE.Bucket(self.bucket_name).download_file(key, str(dst))

            zip_path.parent.mkdir(parents=True, exist_ok=True)
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zf:
                for file in temp_dir.rglob('*'):
                    if file.is_file():
                        zf.write(file, file.relative_to(temp_dir))
        except Exception as e:
            raise RuntimeError(f"Erreur lors de la création du zip pour {self.path} : {e}")
        finally:
            shutil.rmtree(temp_dir, ignore_errors=True)

        return zip_path

    def remove(self) -> None:
        """Supprime récursivement le dossier et son contenu."""
        try:
            dirs, files = self.list_content()
            for f in files:
                f.remove()
            for d in dirs:
                d.remove()
            # Supprime l'objet dossier lui-même (clé finissant par /)
            self.S3_CLIENT.delete_object(Bucket=self.bucket_name, Key=self.path)
        except Exception as e:
            raise RuntimeError(f"Erreur suppression dossier {self.path} : {e}")

    def upload_from_storage(self, files: List[FileStorage]) -> None:
        """Upload une liste de fichiers reçus via Flask vers ce dossier."""
        for file in files:
            key = os.path.join(self.path, file.filename)
            try:
                self.S3_CLIENT.upload_fileobj(file.stream, self.bucket_name, key)
            except Exception as e:
                raise RuntimeError(f"Erreur upload {file.filename} → {key} : {e}")


class S3File(S3Key):

    def is_folder(self) -> bool:
        return False

    def is_file(self) -> bool:
        return True

    def get_download_url(self) -> str:
        try:
            return self.S3_CLIENT.generate_presigned_url(
                'get_object',
                Params={
                    'Bucket': self.bucket_name,
                    'Key': self.path,
                    'ResponseContentDisposition': f'attachment; filename="{self.name}"'
                },
                ExpiresIn=3600
            )
        except Exception as e:
            raise RuntimeError(f"Erreur création URL présignée pour {self.path} : {e}")

    def download(self, local_path: Path) -> Path:
        try:
            local_path.parent.mkdir(parents=True, exist_ok=True)
            self.S3_RESOURCE.Bucket(self.bucket_name).download_file(self.path, str(local_path))
            return local_path
        except Exception as e:
            raise RuntimeError(f"Erreur téléchargement {self.path} → {local_path} : {e}")

    def remove(self) -> None:
        try:
            self.S3_CLIENT.delete_object(Bucket=self.bucket_name, Key=self.path)
        except Exception as e:
            raise RuntimeError(f"Erreur suppression fichier {self.path} : {e}")

    def copy(self, dest_key: str) -> None:
        try:
            self.S3_RESOURCE.Object(self.bucket_name, dest_key).copy({
                'Bucket': self.bucket_name,
                'Key': self.path
            })
        except Exception as e:
            raise RuntimeError(f"Erreur copie {self.path} → {dest_key} : {e}")

    def rename(self, dest_key: str) -> None:
        self.copy(dest_key)
        self.remove()

    def move(self, dest_key: str) -> None:
        self.copy(dest_key)
        self.remove()
