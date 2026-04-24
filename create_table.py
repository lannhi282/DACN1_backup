import mysql.connector
import sqlalchemy
from flask_sqlalchemy import SQLAlchemy


db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Lam@180511',
    'database': 'face_attendance'
}

def create_resource_table():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Creating the table with all necessary columns
        sql = """
        CREATE TABLE IF NOT EXISTS class_resources (
            id INT AUTO_INCREMENT PRIMARY KEY,
            class_name VARCHAR(50),
            file_name VARCHAR(255),
            file_path VARCHAR(255),
            description TEXT,
            category VARCHAR(50),
            upload_date DATETIME
        )
        """
        cursor.execute(sql)
        conn.commit()
        
        print("--- Success: 'class_resources' table is ready! ---")
        
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"--- Error: {e} ---")
        
        from flask_sqlalchemy import SQLAlchemy

# We initialize db here, but we will bind it to the app in app.py
db = SQLAlchemy()

class ClassResources(db.Model):
    __tablename__ = 'class_resources'
    
    id = db.Column(db.Integer, primary_key=True)
    file_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50))
    file_path = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    class_name = db.Column(db.String(100), nullable=False)

if __name__ == "__main__":
    create_resource_table()