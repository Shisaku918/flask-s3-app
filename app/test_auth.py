from app.auth_utils import create_user

username = "admin"
password = "password123"
success = create_user(username, password)
print("Créé avec succès" if success else "Utilisateur existe déjà")
