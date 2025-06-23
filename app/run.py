from dotenv import load_dotenv
load_dotenv()

from flask import Flask
from app.main_routes import bp as main_bp
import os

def create_app():
    app = Flask(__name__, template_folder='templates', static_folder='static')
    app.secret_key = os.getenv('FLASK_SECRET_KEY', 'changeme')

    # Enregistre le blueprint principal
    app.register_blueprint(main_bp)

    return app

if __name__ == "__main__":
    app = create_app()
    app.run(debug=True)
