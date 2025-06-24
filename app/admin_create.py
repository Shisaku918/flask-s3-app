"""
Ce fichier est présent afin d'initialiser le premier compte administrateur ou en recréer un si par erreur vous n'en avez plus.
"""



import json
from app.auth_utils import hash_password
from app.auth_utils import redis_client  # ou directement from redis import Redis si tu utilises Redis natif

# --- Remplace ces valeurs ---
username = "admin"
password = "admin"

# --- Hachage du mot de passe ---
hashed_pw, salt = hash_password(password)

user_data = {
    "password": f"{hashed_pw}:{salt}",
    "role": "admin"
}

# --- Enregistrement dans Redis ---
if redis_client.hexists("users", username):
    print("⚠️ Utilisateur existe déjà.")
else:
    redis_client.hset("users", username, json.dumps(user_data))
    print("✅ Admin créé avec succès.")
