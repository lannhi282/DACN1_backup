import mysql.connector
from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

# 1. DATABASE CONFIGURATION
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'face_attendance'
}

# 2. SQLALCHEMY MODELS
db = SQLAlchemy()

class Student(db.Model):
    __tablename__ = 'students'
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), nullable=False)
    class_name = db.Column(db.String(50), nullable=False)
    face_data = db.Column(db.LargeBinary)  # Stores face data
    password = db.Column(db.String(255))

class ClassResources(db.Model):
    __tablename__ = 'class_resources'
    id = db.Column(db.Integer, primary_key=True)
    class_name = db.Column(db.String(100), nullable=False)
    file_name = db.Column(db.String(255), nullable=False)
    file_type = db.Column(db.String(50))
    file_path = db.Column(db.String(255), nullable=False)
    description = db.Column(db.Text)
    upload_date = db.Column(db.DateTime, default=db.func.current_timestamp())

class ClassFees(db.Model):
    __tablename__ = 'class_fees'
    id = db.Column(db.Integer, primary_key=True)
    class_name = db.Column(db.String(50), nullable=False, unique=True)
    monthly_fee = db.Column(db.Float, default=0.0)
    admission_fee = db.Column(db.Float, default=0.0)
    exam_fee = db.Column(db.Float, default=0.0)
    other_charges = db.Column(db.Float, default=0.0)
    description = db.Column(db.Text)
    last_updated = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

# 3. TABLE CREATION SCRIPTS (Raw MySQL)

# MISSING FUNCTION ADDED HERE:
def create_student_table():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        sql = """
        CREATE TABLE IF NOT EXISTS students (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(100) NOT NULL,
            class_name VARCHAR(50) NOT NULL,
            face_data LONGBLOB,     
            password VARCHAR(255)
        )
        """
        cursor.execute(sql)
        conn.commit()
        print("--- Success: MySQL 'students' table is ready! ---")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"--- Error creating students table: {e} ---")

def create_resource_table():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        sql = """
        CREATE TABLE IF NOT EXISTS class_resources (
            id INT AUTO_INCREMENT PRIMARY KEY,
            class_name VARCHAR(100),
            file_name VARCHAR(255),
            file_type VARCHAR(50), 
            file_path VARCHAR(255),
            description TEXT,
            upload_date DATETIME DEFAULT CURRENT_TIMESTAMP
        )
        """
        cursor.execute(sql)
        conn.commit()
        print("--- Success: MySQL 'class_resources' table synchronized! ---")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"--- Error creating table: {e} ---")

def create_class_fees_table():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        sql = """
        CREATE TABLE IF NOT EXISTS class_fees (
            id INT AUTO_INCREMENT PRIMARY KEY,
            class_name VARCHAR(50) UNIQUE NOT NULL,
            monthly_fee DECIMAL(10,2) DEFAULT 0.00,
            admission_fee DECIMAL(10,2) DEFAULT 0.00,
            exam_fee DECIMAL(10,2) DEFAULT 0.00,
            other_charges DECIMAL(10,2) DEFAULT 0.00,
            description TEXT,
            last_updated TIMESTAMP DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP
        )
        """
        cursor.execute(sql)
        conn.commit()
        print("--- Success: MySQL 'class_fees' table created! ---")
        cursor.close()
        conn.close()
    except Exception as e:
        print(f"--- Error creating class_fees table: {e} ---")

# EXECUTION BLOCK UPDATED
if __name__ == "__main__":
    create_student_table()  # Run the new table creator
    create_resource_table()
    create_class_fees_table()