from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from app.routes import bp


app = Flask(__name__, template_folder='templates', static_folder='static')

app.register_blueprint(bp)  # enregistre les routes

if __name__ == "__main__":
    app.run(debug=True)  # lance le serveur en mode debug