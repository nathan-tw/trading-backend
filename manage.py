from flask.cli import FlaskGroup, with_appcontext
from sqlalchemy import text
from app import create_app
from models import db

cli = FlaskGroup(create_app=create_app)

@cli.command("check-db")
@with_appcontext
def check_db():
    """Checks the database connection."""
    try:
        db.session.execute(text('SELECT 1'))
        print("Database connection check from command: Successful!")
    except Exception as e:
        print(f"Database connection check from command: Failed: {e}")
        exit(1)

if __name__ == "__main__":
    cli()
