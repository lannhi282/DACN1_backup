import mysql.connector

db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': '',
    'database': 'face_attendance'
}

def fix_missing_columns():
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # 1. Add face_image if missing
        try:
            cursor.execute("ALTER TABLE students ADD COLUMN face_image LONGTEXT")
            print("✅ Added 'face_image' column.")
        except mysql.connector.Error as err:
            if err.errno == 1060: # Column already exists
                print("ℹ️ 'face_image' already exists.")
            else:
                print(f"❌ Error face_image: {err}")

        # 2. Add password if missing
        try:
            cursor.execute("ALTER TABLE students ADD COLUMN password VARCHAR(255)")
            print("✅ Added 'password' column.")
        except mysql.connector.Error as err:
            if err.errno == 1060:
                print("ℹ️ 'password' already exists.")
            else:
                print(f"❌ Error password: {err}")

        conn.commit()
        cursor.close()
        conn.close()
        print("\n--- Database Sync Complete! ---")
        
    except Exception as e:
        print(f"Critical Error: {e}")

if __name__ == "__main__":
    fix_missing_columns()