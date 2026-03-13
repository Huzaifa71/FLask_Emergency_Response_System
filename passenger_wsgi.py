import sys, os

# Add the project directory to sys.path
sys.path.insert(0, os.path.dirname(__file__))

# Import the FlaskAPP
from app import app as application  # 'app' is your Flask instance in app.py