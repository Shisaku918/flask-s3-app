# 🗂️ Flask S3 Explorer

Une interface web simple et élégante pour explorer, téléverser, renommer, déplacer et supprimer des fichiers et dossiers dans un bucket AWS S3 — développée avec Flask.

---

## 🚀 Fonctionnalités

- 🔍 Naviguer dans la structure du bucket S3 (via les préfixes)
- 📁 Créer des dossiers virtuels (simulés via les `prefix/`)
- ⬆️ Uploader plusieurs fichiers et dossiers par **glisser-déposer**
- 🔄 Renommer fichiers et dossiers (avec copie + suppression)
- 🚚 Déplacer fichiers/dossiers dans un autre répertoire
- 📥 Télécharger avec un **lien pré-signé** sécurisé
- 🗑️ Supprimer fichiers ou dossiers entiers

---

## 🧰 Dépendances

- Python 3.8+
- Flask
- boto3
- python-dotenv

---

## 📦 Installation

1. Clone ce dépôt :

```
git clone https://github.com/ton_pseudo/flask-s3-app.git
cd flask-s3-app
```

2. Crée un environnement virtuel :

```
python -m venv .venv
source .venv/bin/activate
```

3. Installe les dépendances :

```
pip install -r requirements.txt
```

⚙️ Configuration

Crée un fichier .env à la racine du projet avec tes identifiants AWS :

AWS_ACCESS_KEY_ID=ton_access_key

AWS_SECRET_ACCESS_KEY=ta_secret_key

AWS_REGION=eu-west-1

AWS_BUCKET_NAME=nom-de-ton-bucket

FLASK_SECRET_KEY=clé-de-ton-app-flask

🔐 Ne pas versionner ce fichier ! Il est ignoré via .gitignore.

🧪 Lancement

```
python app/run.py
```

📁 Arborescence du projet


```
flask-s3-app/
│
├── app/
│   ├── run.py
│   ├── routes.py
│   ├── s3_utils.py
│   ├── templates/
│   │   └── index.html
│   └── static/
│       └── style.css
├── .env
├── .gitignore
├── requirements.txt
└── README.md
```
