import redis
import hashlib
import os
import uuid
import json

# Connexion Redis (configure selon ton environnement)
redis_client = redis.Redis(host='localhost', port=6379, db=0, decode_responses=True)


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hash un mot de passe avec SHA256 et un sel."""
    if salt is None:
        salt = uuid.uuid4().hex
    hashed = hashlib.sha256((salt + password).encode()).hexdigest()
    return hashed, salt


def create_user(username: str, password: str, role: str = 'user') -> bool:
    if redis_client.hexists('users', username):
        return False

    hashed_pw, salt = hash_password(password)
    user_data = {
        'password': f"{hashed_pw}:{salt}",
        'role': role
    }
    # Stocke en JSON valide (avec guillemets doubles)
    redis_client.hset('users', username, json.dumps(user_data))
    return True


def create_admin(username: str, password: str) -> bool:
    """Crée un compte administrateur. Échoue si l'utilisateur existe déjà."""
    if redis_client.hexists('users', username):
        return False

    hashed_pw, salt = hash_password(password)
    user_data = {
        'password': f"{hashed_pw}:{salt}",
        'role': 'admin'  # rôle forcé
    }
    redis_client.hset('users', username, json.dumps(user_data))
    return True



def verify_user(username: str, password: str) -> bool:
    data = redis_client.hget('users', username)
    if not data:
        return False

    try:
        # Essaie de parser en JSON (nouveau format)
        user_data = json.loads(data)
        stored_hash, salt = user_data['password'].split(':', 1)
    except json.JSONDecodeError:
        # Ancien format simple "hash:salt"
        stored_hash, salt = data.split(':', 1)

    hashed_pw, _ = hash_password(password, salt)
    return hashed_pw == stored_hash





def create_session(username: str) -> str:
    """Crée une session et retourne un token."""
    session_token = uuid.uuid4().hex
    redis_client.set(f"session:{session_token}", username, ex=3600)  # Expire dans 1h
    return session_token


def get_username_from_session(session_token: str) -> str | None:
    """Récupère le username depuis un token de session."""
    return redis_client.get(f"session:{session_token}")


def delete_session(session_token: str):
    """Supprime une session."""
    redis_client.delete(f"session:{session_token}")


def get_user_role(username: str) -> str | None:
    import json
    data = redis_client.hget('users', username)
    if not data:
        return None
    user_data = json.loads(data)
    return user_data.get('role')


def current_user_role():
    session_token = session.get('session_token')
    if not session_token:
        return 'guest'  # Pas connecté = invité
    username = get_username_from_session(session_token)
    if not username:
        return 'guest'
    role = get_user_role(username)
    return role or 'guest'

def delete_user(username: str) -> bool:
    if redis_client.hexists('users', username):
        redis_client.hdel('users', username)
        return True
    return False
