import cv2
import mysql.connector
import os
import io
import time 
from datetime import datetime
import pandas as pd 
from fpdf import FPDF 
from camera import VideoCamera
from functools import wraps
from flask import Flask, current_app, render_template, Response, request, redirect, url_for, flash, send_file, session, jsonify, g
from werkzeug.utils import secure_filename
from models import db, ClassResources, ClassFees  # Add ClassFees here
import base64
# Change this in app.py
from models import db, Student, ClassResources, ClassFees

import calendar
from threading import Thread
import time
from datetime import datetime, timedelta

# --- AUTOMATIC INVOICE GENERATION SYSTEM ---

def get_class_fee(class_name):
    """Get monthly fee for a class"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT monthly_fee FROM class_fees WHERE class_name = %s", (class_name,))
        result = cursor.fetchone()
        cursor.close()
        conn.close()
        return result['monthly_fee'] if result else 0.0
    except Exception as e:
        print(f"Error getting class fee: {e}")
        return 0.0

def generate_monthly_invoices():
    """Generate invoices for all students at the start of each month"""
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # Get current month and year
        now = datetime.now()
        current_month = now.month
        current_year = now.year
        month_name = now.strftime("%B %Y")
        
        # Get all students with their class fees
        cursor.execute("""
            SELECT s.id, s.name, s.class_name, cf.monthly_fee 
            FROM students s
            LEFT JOIN class_fees cf ON s.class_name = cf.class_name
        """)
        students = cursor.fetchall()
        
        invoices_created = 0
        
        for student in students:
            student_id = student['id']
            class_name = student['class_name']
            monthly_fee = student['monthly_fee'] or 0.0
            
            # Skip if fee is 0
            if monthly_fee <= 0:
                continue
            
            # Check if invoice already exists for this month
            cursor.execute("""
                SELECT id FROM invoices 
                WHERE student_id = %s 
                AND description LIKE %s 
                AND MONTH(created_at) = %s 
                AND YEAR(created_at) = %s
            """, (student_id, f'%Monthly Fee%{month_name}%', current_month, current_year))
            
            existing = cursor.fetchone()
            
            if not existing:
                # Calculate due date (15th of next month)
                if current_month == 12:
                    due_date = datetime(current_year + 1, 1, 15)
                else:
                    due_date = datetime(current_year, current_month + 1, 15)
                
                # Create invoice
                description = f"Monthly Fee - {month_name} ({class_name})"
                
                cursor.execute("""
                    INSERT INTO invoices (student_id, description, amount, due_date, status, created_at) 
                    VALUES (%s, %s, %s, %s, 'Unpaid', %s)
                """, (student_id, description, monthly_fee, due_date.strftime('%Y-%m-%d'), now))
                
                invoices_created += 1
        
        conn.commit()
        cursor.close()
        conn.close()
        
        print(f"[{datetime.now()}] Auto-generated {invoices_created} monthly invoices")
        return invoices_created
        
    except Exception as e:
        print(f"Error generating monthly invoices: {e}")
        return 0

def invoice_scheduler():
    """Background thread that runs daily to check for invoice generation"""
    while True:
        try:
            now = datetime.now()
            # Run on 1st day of every month at 1:00 AM
            if now.day == 1 and now.hour == 1:
                print(f"[{now}] Running monthly invoice generation...")
                generate_monthly_invoices()
                # Sleep for 2 hours to avoid duplicate runs
                time.sleep(7200)
            else:
                # Check every hour
                time.sleep(3600)
        except Exception as e:
            print(f"Scheduler error: {e}")
            time.sleep(3600)

# Start scheduler in background (add this after app creation)
def start_invoice_scheduler():
    scheduler_thread = Thread(target=invoice_scheduler, daemon=True)
    scheduler_thread.start()
    print("Invoice scheduler started")

# Call this after app initialization
# start_invoice_scheduler()


# --- DATABASE & MODELS IMPORT ---
from models import db, ClassResources
from flask_sqlalchemy import SQLAlchemy
import pymysql
pymysql.install_as_MySQLdb() 

# 1. INITIALIZE APP ONLY ONCE
app = Flask(__name__)
app.secret_key = "eduscan_secret_key"

# 2. FOLDER CONFIGURATION
UPLOAD_FOLDER = 'static/enrollments'
RESOURCE_UPLOAD_FOLDER = 'static/uploads/resources'

# Ensure directories exist
for folder in [UPLOAD_FOLDER, RESOURCE_UPLOAD_FOLDER]:
    if not os.path.exists(folder):
        os.makedirs(folder)

# 3. CONFIGURE APP (All keys in one place)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['RESOURCE_UPLOAD_FOLDER'] = RESOURCE_UPLOAD_FOLDER

# Database Configuration
db_config = {
    'host': 'localhost',
    'user': 'root',
    'password': 'Lam@180511', 
    'database': 'face_attendance'
}

app.config['SQLALCHEMY_DATABASE_URI'] = f"mysql+pymysql://{db_config['user']}:{db_config['password']}@{db_config['host']}/{db_config['database']}"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# 4. INITIALIZE DATABASE
db.init_app(app) 

# 1. Define your allowed types at the top of your file
ALLOWED_EXTENSIONS = {'pdf', 'png', 'jpg', 'jpeg', 'doc', 'docx', 'xls', 'xlsx', 'ppt', 'pptx', 'txt'}

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# Global state
last_recognized_user = None
system_camera = VideoCamera(db_config)

# --- AUTH DECORATORS ---

def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'admin':
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def teacher_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'teacher':
            return redirect(url_for('teacher_login'))
        return f(*args, **kwargs)
    return decorated_function

def student_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('role') != 'student':
            return redirect(url_for('student_login'))
        return f(*args, **kwargs)
    return decorated_function

# --- HELPER FUNCTIONS ---

def log_attendance(name, subject="General"):
    global last_recognized_user
    if name == "Unknown" or not name:
        return

    last_recognized_user = name 
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        now = datetime.now()
        today = now.strftime("%Y-%m-%d")
        now_time = now.strftime("%H:%M:%S")

        cursor.execute("SELECT class_name FROM students WHERE name=%s", (name,))
        result = cursor.fetchone()
        
        if not result:
            cursor.execute("SELECT 'Staff' as class_name FROM teachers WHERE full_name=%s", (name,))
            result = cursor.fetchone()

        student_class = result['class_name'] if result else "General"

        cursor.execute("SELECT id FROM attendance WHERE student_name=%s AND log_date=%s AND subject=%s", (name, today, subject))
        if cursor.fetchone() is None:
            cursor.execute("""
                INSERT INTO attendance (student_name, class_name, log_date, log_time, status, subject) 
                VALUES (%s, %s, %s, %s, 'Present', %s)
            """, (name, student_class, today, now_time, subject))
            conn.commit()
    except Exception as e:
        print(f"Database Error: {e}")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
            
            
            # --- HELPER FUNCTIONS ---





# --- LANDING & AUTH ROUTES ---

@app.route('/')
def index():
    if session.get('role') == 'admin':
        return redirect(url_for('admin_dashboard'))
    elif session.get('role') == 'teacher':
        return redirect(url_for('teacher_dashboard'))
    elif session.get('role') == 'student':
        return redirect(url_for('student_portal', student_id=session.get('user_id')))
    
    return render_template('portal_selection.html')

@app.route('/portal_selection')
def portal_selection():
    return render_template('portal_selection.html')

# --- AJAX IDENTITY CHECK ---
@app.route('/check_identity')
def check_identity():
    global last_recognized_user
    if last_recognized_user:
        name = last_recognized_user
        last_recognized_user = None 
        return jsonify({"found": True, "name": name})
    return jsonify({"found": False})

# ADMIN LOGIN
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        if username == 'admin' and password == 'admin123':
            session.clear() 
            session['role'] = 'admin'
            return redirect(url_for('admin_dashboard'))
        flash("Invalid Admin Credentials", "danger")
    return render_template('admin_login.html')

import base64 # Make sure this is imported at the top of app.py

@app.route('/admin/dashboard')
@admin_required
def admin_dashboard():
    classes_list = []
    teachers_list = []
    students_list = []
    attendance_log = [] # New list for the logs
    unique_classes = [] # For the filter buttons
    
    conn = None
    cursor = None
    
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Fetch Classes for management
        cursor.execute("SELECT c.*, (SELECT COUNT(*) FROM students s WHERE s.class_name = c.class_name) as student_count FROM classes c ORDER BY class_name ASC")
        classes_list = cursor.fetchall()
            
        # 2. Fetch Teachers
        cursor.execute("SELECT * FROM teachers")
        teachers_list = cursor.fetchall()

        # 3. Fetch Students and base64 images
        cursor.execute("SELECT id, name, class_name, face_data FROM students")
        raw_students = cursor.fetchall()
        for student in raw_students:
            if student['face_data']:
                base64_image = base64.b64encode(student['face_data']).decode('utf-8')
                student['face_image'] = f"data:image/jpeg;base64,{base64_image}"
            else:
                student['face_image'] = None 
            students_list.append(student)

        # 4. NEW: Fetch Attendance Logs and Unique Class Names for Filtering
        cursor.execute("SELECT * FROM attendance ORDER BY log_date DESC, log_time DESC")
        attendance_log = cursor.fetchall()
        
        cursor.execute("SELECT DISTINCT class_name FROM attendance")
        unique_classes = [row['class_name'] for row in cursor.fetchall()]

    except Exception as e:
        print(f"Error: {e}")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()

    return render_template('index.html', 
                           classes=classes_list, 
                           teachers=teachers_list, 
                           students=students_list,
                           attendance=attendance_log,
                           filter_classes=unique_classes)

# --- ADDED: UPDATE CLASS SUBJECT ROUTE (FIXED BUILD ERROR) ---
@app.route('/update_class_subject/<string:class_name>', methods=['POST'])
@admin_required
def update_class_subject(class_name):
    # CHANGED: Match the 'name' attribute in your HTML form
    new_subjects = request.form.get('new_subject') 
    
    conn = None
    cursor = None
    try:
        # Debug print to see what is coming from the form
        print(f"DEBUG: Attempting to update {class_name} with value: {new_subjects}")
        
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # This SQL is correct, but only if the 'subjects' column exists in 'classes' table
        cursor.execute("UPDATE classes SET subjects=%s WHERE class_name=%s", (new_subjects, class_name))
        
        conn.commit()
        flash(f"Subjects for {class_name} updated to {new_subjects}!", "success")
        
    except Exception as e:
        print(f"❌ DATABASE ERROR: {e}")
        flash(f"Error updating subjects: {e}", "danger")
    finally:
        if cursor: cursor.close()
        if conn: conn.close()
        
    return redirect(url_for('index')) # Change to 'index' if you want to stay on the same page
# NEW PORTAL ROUTE
@app.route('/admin/view_portal')
@app.route('/admin/view_portal/<string:class_name>')
@admin_required
def view_portal(class_name=None):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT class_name FROM classes ORDER BY class_name ASC")
        all_classes = cursor.fetchall()
        
        students = []
        if class_name:
            cursor.execute("SELECT id, name, class_name FROM students WHERE class_name = %s", (class_name,))
            students = cursor.fetchall()
        else:
            cursor.execute("SELECT id, name, class_name FROM students ORDER BY class_name")
            students = cursor.fetchall()
            
        for student in students:
            img_name = f"{student['name']}.jpg"
            img_path = os.path.join(UPLOAD_FOLDER, img_name)
            if os.path.exists(img_path):
                student['image_url'] = url_for('static', filename=f'enrollments/{img_name}')
            else:
                student['image_url'] = f"https://ui-avatars.com/api/?name={student['name']}&background=2c3e50&color=fff"

        return render_template('enrolled_faces.html', 
                               all_classes=all_classes, 
                               students=students, 
                               active_class=class_name)
    except Exception as e:
        flash(f"Portal Error: {e}", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# TEACHER LOGIN
@app.route('/teacher/login', methods=['GET', 'POST'])
def teacher_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = None
        cursor = None
        try:
            conn = mysql.connector.connect(**db_config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM teachers WHERE username=%s AND password=%s", (username, password))
            teacher = cursor.fetchone()
            if teacher:
                session.clear()
                session['role'] = 'teacher'
                session['user_id'] = teacher['id']
                session['t_name'] = teacher['full_name']
                session['assigned_class'] = teacher.get('assigned_class')
                return redirect(url_for('teacher_dashboard'))
        except Exception as e:
            print(f"Login error: {e}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        flash("Invalid Teacher Credentials", "danger")
    return render_template('teacher_login.html')

# STUDENT LOGIN
@app.route('/student/login', methods=['GET', 'POST'])
def student_login():
    if request.method == 'POST':
        username = request.form.get('username')
        password = request.form.get('password')
        conn = None
        cursor = None
        try:
            conn = mysql.connector.connect(**db_config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT * FROM students WHERE name=%s AND password=%s", (username, password))
            student = cursor.fetchone()
            if student:
                session.clear()
                session['role'] = 'student'
                session['user_id'] = student['id']
                session['user_name'] = student['name']
                session['class_name'] = student['class_name']
                return redirect(url_for('student_portal', student_id=student['id']))
        except Exception as e:
            print(f"Login error: {e}")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
        flash("Invalid Student Credentials", "danger")
    return render_template('student_login.html')

# --- INVOICE & BILLING SYSTEM ---

@app.route('/admin/add_invoice', methods=['POST'])
@admin_required
def add_invoice():
    student_id = request.form.get('student_id')
    desc = request.form.get('description')
    amount = request.form.get('amount')
    due_date = request.form.get('due_date')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO invoices (student_id, description, amount, due_date, status) 
            VALUES (%s, %s, %s, %s, 'Unpaid')
        """, (student_id, desc, amount, due_date))
        conn.commit()
        flash("Invoice generated successfully!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/admin/mark_paid/<int:invoice_id>')
@admin_required
def mark_paid(invoice_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("UPDATE invoices SET status='Paid' WHERE id=%s", (invoice_id,))
        conn.commit()
        flash("Invoice marked as Paid!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(request.referrer or url_for('admin_dashboard'))

@app.route('/download_invoice/<int:invoice_id>')
def download_invoice(invoice_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT i.*, s.name, s.class_name FROM invoices i JOIN students s ON i.student_id = s.id WHERE i.id = %s", (invoice_id,))
        inv = cursor.fetchone()
        if not inv: 
            return "Invoice not found", 404

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 20)
        pdf.cell(0, 10, "EDUSCAN SCHOOL SYSTEM", ln=True, align='C')
        pdf.set_font("Arial", '', 12)
        pdf.cell(0, 10, f"Invoice ID: #INV-{inv['id']}", ln=True, align='C')
        pdf.ln(10)
        pdf.cell(0, 10, f"Student Name: {inv['name']}", ln=True)
        pdf.cell(0, 10, f"Class: {inv['class_name']}", ln=True)
        pdf.cell(0, 10, f"Description: {inv['description']}", ln=True)
        pdf.cell(0, 10, f"Amount: Rs. {inv['amount']}", ln=True)
        pdf.cell(0, 10, f"Due Date: {inv['due_date']}", ln=True)
        pdf.cell(0, 10, f"Status: {inv['status']}", ln=True)
        
        pdf_content = pdf.output(dest='S').encode('latin-1')
        return send_file(io.BytesIO(pdf_content), download_name=f"Invoice_{inv['id']}.pdf", as_attachment=True)
    except Exception as e: 
        return f"PDF Error: {e}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- CLASS & STUDENT MANAGEMENT ---

@app.route('/add_class', methods=['POST'])
@admin_required
def add_class():
    class_name = request.form.get('new_class_name')
    subjects = request.form.get('subjects')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO classes (class_name, subjects) VALUES (%s, %s)", (class_name, subjects))
        conn.commit()
        flash("Class added successfully!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/edit_class/<string:class_name>', methods=['POST'])
@admin_required
def edit_class(class_name):
    updated_name = request.form.get('updated_name')
    updated_subjects = request.form.get('updated_subjects')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("UPDATE classes SET class_name=%s, subjects=%s WHERE class_name=%s", (updated_name, updated_subjects, class_name))
        conn.commit()
        flash("Class updated!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/delete_class/<string:class_name>', methods=['POST'])
@admin_required
def delete_class(class_name):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM classes WHERE class_name = %s", (class_name,))
        conn.commit()
        flash("Class deleted!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/finalize_class/<string:class_name>')
@admin_required
def finalize_class(class_name):
    flash(f"Class {class_name} session finalized.", "success")
    return redirect(url_for('admin_dashboard'))

@app.route('/add_student', methods=['POST'])
@admin_required
def add_student():
    name = request.form.get('student_name').strip()
    s_class = request.form.get('student_class')
    password = request.form.get('student_password')
    
    if not os.path.exists(UPLOAD_FOLDER):
        os.makedirs(UPLOAD_FOLDER)

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO students (name, class_name, password) VALUES (%s, %s, %s)", (name, s_class, password))
        conn.commit()
        
        success = system_camera.enroll_face(name)
        
        if success: 
            time.sleep(0.5) 
            ret, frame = system_camera.video.read()
            if ret:
                img_path = os.path.join(UPLOAD_FOLDER, f"{name}.jpg")
                cv2.imwrite(img_path, frame)
            
            flash(f"Student {name} added and face image saved!", "success")
        else: 
            flash("Student added to DB, but face capture failed.", "warning")
            
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        
    return redirect(url_for('admin_dashboard'))

@app.route('/remove_student/<int:id>')
@admin_required
def remove_student(id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM students WHERE id = %s", (id,))
        conn.commit()
        flash("Student removed successfully!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

# --- LOGS ROUTE ---

@app.route('/logs')
@app.route('/logs/<string:class_name>')
def view_logs(class_name=None):
    if not session.get('role'): 
        return redirect(url_for('index'))
    if not class_name: 
        class_name = request.args.get('class_name')
    subject_filter = request.args.get('subject')

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        query = "SELECT * FROM attendance WHERE 1=1"
        params = []

        if class_name:
            query += " AND class_name = %s"
            params.append(class_name)
        if subject_filter:
            query += " AND subject = %s"
            params.append(subject_filter)
        
        query += " ORDER BY log_date DESC, log_time DESC"
        cursor.execute(query, tuple(params))
        raw_logs = cursor.fetchall()

        grouped_data = {}
        for log in raw_logs:
            d_key = str(log['log_date'])
            if d_key not in grouped_data: 
                grouped_data[d_key] = []
            grouped_data[d_key].append(log)
        return render_template('logs.html', grouped_logs=grouped_data, selected_class=class_name)
    except Exception as e: 
        return f"Database Error: {e}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- TEACHER PORTAL LOGIC ---

@app.route('/teacher')
@teacher_required
def teacher_dashboard():
    my_class = session.get('assigned_class')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM classes")
        all_classes = cursor.fetchall()
        cursor.execute("SELECT * FROM leave_applications WHERE class_name = %s", (my_class,))
        leaves = cursor.fetchall()
        cursor.execute("SELECT COUNT(*) as p_count FROM leave_applications WHERE class_name = %s AND status='Pending'", (my_class,))
        pending_count = cursor.fetchone()['p_count']
        
        cursor.execute("SELECT id, name FROM students WHERE class_name = %s", (my_class,))
        my_students = cursor.fetchall()
        
        return render_template('teacher_portal.html', classes=all_classes, leaves=leaves, pending_count=pending_count,
                               today_date=datetime.now().strftime('%Y-%m-%d'), teacher_id=session.get('user_id'),
                               teacher_name=session.get('t_name'), my_class=my_class, my_students=my_students)
    except Exception as e: 
        return f"Error: {e}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/teacher/update_leave/<int:leave_id>/<string:status>', methods=['POST'])
@teacher_required
def update_leave(leave_id, status):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("UPDATE leave_applications SET status=%s WHERE id=%s", (status, leave_id))
        conn.commit()
        flash(f"Leave {status}!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('teacher_dashboard'))

@app.route('/upload_grades', methods=['POST'])
@app.route('/teacher/add_grade', methods=['POST'])
def add_grade():
    student_id = request.form.get('student_id')
    subject = request.form.get('subject')
    marks = request.form.get('grade') if request.form.get('grade') else request.form.get('marks')
    class_name = request.form.get('class_name') or session.get('assigned_class')
    total_marks = request.form.get('total_marks', 100)
    exam_type = request.form.get('exam_type', 'General')

    if not student_id or student_id == "":
        flash("Error: Please select a valid student.", "danger")
        return redirect(request.referrer or url_for('teacher_dashboard'))

    try:
        obs_marks = int(marks)
        max_marks = int(total_marks)
        if obs_marks > max_marks:
            flash(f"Error: Obtained marks ({obs_marks}) cannot be greater than Total marks ({max_marks})!", "danger")
            return redirect(request.referrer or url_for('teacher_dashboard'))
    except (ValueError, TypeError):
        flash("Error: Marks must be valid numbers.", "danger")
        return redirect(request.referrer or url_for('teacher_dashboard'))

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM students WHERE id = %s", (student_id,))
        if not cursor.fetchone():
            flash("Error: Student ID not found in database.", "danger")
            return redirect(request.referrer or url_for('teacher_dashboard'))

        query = """
            INSERT INTO grades (student_id, subject, marks_obtained, total_marks, exam_type, class_name) 
            VALUES (%s, %s, %s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE 
            marks_obtained = VALUES(marks_obtained),
            total_marks = VALUES(total_marks),
            class_name = VALUES(class_name)
        """
        cursor.execute(query, (student_id, subject, marks, total_marks, exam_type, class_name))
        conn.commit()
        flash("Grade posted successfully!", "success")
    except Exception as e: 
        flash(f"Database Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(request.referrer or url_for('teacher_dashboard'))

@app.route('/teacher/add_diary', methods=['POST'])
@teacher_required
def add_diary():
    class_name = request.form.get('class_name')
    task = request.form.get('task')
    date = datetime.now().strftime('%Y-%m-%d')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO daily_diaries (class_name, task_description, assigned_date) VALUES (%s, %s, %s)", 
                        (class_name, task, date))
        conn.commit()
        flash("Diary task published!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('teacher_dashboard'))

# --- STUDENT PORTAL LOGIC ---

@app.route('/student_portal/<int:student_id>')
@student_required
def student_portal(student_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # 1. Basic Student Info
        cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        if not student:
            return "Student not found", 404

        # --- NEW SECTION: FETCH CLASS RESOURCES ---
        cursor.execute("""
            SELECT * FROM class_resources 
            WHERE class_name = %s 
            ORDER BY upload_date DESC
        """, (student['class_name'],))
        all_res = cursor.fetchall()
        
        resources = {}
        for res in all_res:
            if res['category'] not in resources:
                resources[res['category']] = res
        # ------------------------------------------

        # 2. Attendance Logs
        cursor.execute("SELECT * FROM attendance WHERE student_name = %s ORDER BY log_date DESC", (student['name'],))
        logs = cursor.fetchall()
        
        total_days = len(logs) if len(logs) > 0 else 1
        present_days = sum(1 for log in logs if log['status'] == 'Present')
        percentage = round((present_days / total_days) * 100, 1)

        # 3. Subject Wise Attendance
        cursor.execute("""
            SELECT subject, 
                   COUNT(CASE WHEN status='Present' THEN 1 END) as present,
                   COUNT(*) as total
            FROM attendance 
            WHERE student_name = %s 
            GROUP BY subject
        """, (student['name'],))
        subject_attendance = cursor.fetchall()

        # 4. Invoices, Leaves, and Grades
        cursor.execute("SELECT * FROM invoices WHERE student_id = %s ORDER BY due_date DESC", (student_id,))
        invoices = cursor.fetchall()

        cursor.execute("SELECT id, start_date, end_date, leave_reason as reason, status FROM leave_applications WHERE student_name = %s ORDER BY id DESC", (student['name'],))
        leave_requests = cursor.fetchall()
        
        cursor.execute("SELECT * FROM grades WHERE student_id = %s", (student_id,))
        grades = cursor.fetchall()

        # 5. Performance Comments
        total_obtained = sum(g['marks_obtained'] for g in grades)
        total_possible = sum(g['total_marks'] for g in grades)
        final_perc = (total_obtained / total_possible * 100) if total_possible > 0 else 0
        
        if final_perc >= 90:
            auto_comment = f"Exceptional work, {student['name']}! Masterly command over all subjects."
        elif final_perc >= 75:
            auto_comment = f"A very strong performance. {student['name']} is a dedicated student."
        elif final_perc >= 50:
            auto_comment = f"Satisfactory progress. {student['name']} shows potential."
        else:
            auto_comment = f"Performance below expectations. Needs additional support."

        # 6. Diaries
        cursor.execute("SELECT * FROM daily_diaries WHERE class_name = %s ORDER BY assigned_date DESC LIMIT 10", (student['class_name'],))
        diaries = cursor.fetchall()
        
        return render_template('student_portal.html', student=student, logs=logs, percentage=percentage, 
                               subject_attendance=subject_attendance, invoices=invoices, 
                               leave_requests=leave_requests, grades=grades, diaries=diaries,
                               auto_comment=auto_comment, resources=resources, all_res=all_res)
    except Exception as e: 
        return f"Error: {e}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/student/request_leave', methods=['POST'])
@student_required
def request_leave():
    student_name = request.form.get('student_name')
    class_name = request.form.get('class_name')
    start_date = request.form.get('start_date')
    end_date = request.form.get('end_date')
    reason = request.form.get('reason')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO leave_applications (student_name, class_name, start_date, end_date, leave_reason, status) 
            VALUES (%s, %s, %s, %s, %s, 'Pending')
        """, (student_name, class_name, start_date, end_date, reason))
        conn.commit()
        flash("Leave request submitted successfully!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('student_portal', student_id=session.get('user_id')))

# --- PASSWORD SECURITY ---

@app.route('/change_password', methods=['POST'])
def change_password():
    user_id = request.form.get('user_id')
    role = session.get('role')
    old_pwd = request.form.get('old_password')
    new_pwd = request.form.get('new_password')
    table = "teachers" if role == "teacher" else "students"
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute(f"SELECT password FROM {table} WHERE id=%s", (user_id,))
        user = cursor.fetchone()
        if user and user['password'] == old_pwd:
            cursor.execute(f"UPDATE {table} SET password=%s WHERE id=%s", (new_pwd, user_id))
            conn.commit()
            flash("Password updated successfully!", "success")
        else: 
            flash("Current password incorrect!", "danger")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(request.referrer or url_for('index'))

# --- UTILITIES ---

@app.route('/add_teacher', methods=['POST'])
@admin_required
def add_teacher():
    name = request.form.get('t_name').strip()
    user = request.form.get('t_username')
    pwd = request.form.get('t_password')
    t_class = request.form.get('t_class')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO teachers (full_name, username, password, assigned_class) VALUES (%s, %s, %s, %s)", 
                        (name, user, pwd, t_class))
        conn.commit()
        success = system_camera.enroll_face(name)
        if success: 
            time.sleep(0.5) 
            ret, frame = system_camera.video.read()
            if ret:
                img_path = os.path.join(UPLOAD_FOLDER, f"{name}.jpg")
                cv2.imwrite(img_path, frame)
            flash(f"Teacher {name} added!", "success")
        else: 
            flash(f"Teacher added, but face failed.", "warning")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/remove_teacher/<int:id>')
@admin_required
def remove_teacher(id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("DELETE FROM teachers WHERE id = %s", (id,))
        conn.commit()
        flash("Teacher removed successfully!", "success")
    except Exception as e: 
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return redirect(url_for('admin_dashboard'))

@app.route('/backup')
@admin_required
def backup_data():
    try:
        conn = mysql.connector.connect(**db_config)
        query = "SELECT * FROM attendance"
        df = pd.read_sql(query, conn)
        conn.close()
        output = io.BytesIO()
        with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
            df.to_excel(writer, index=False, sheet_name='Attendance')
        output.seek(0)
        return send_file(output, download_name=f"backup_{datetime.now().strftime('%Y%m%d')}.xlsx", as_attachment=True)
    except Exception as e: 
        flash(f"Backup Error: {e}", "danger")
        return redirect(url_for('admin_dashboard'))

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('index'))

# --- SCANNER & FEED LOGIC ---

@app.route('/video_feed')
def video_feed():
    current_subject = session.get('current_subject', 'General')

    return Response(
        gen_feed(current_subject),
        mimetype='multipart/x-mixed-replace; boundary=frame'
    )


def gen_feed(current_subject):
    while True:
        frame = system_camera.get_processed_frame(
            lambda name: log_attendance(name, current_subject)
        )

        if frame:
            yield (
                b'--frame\r\n'
                b'Content-Type: image/jpeg\r\n\r\n' + frame + b'\r\n\r\n'
            )

@app.route('/get_students_by_class/<string:class_name>')
def get_students_by_class(class_name):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, name FROM students WHERE class_name = %s", (class_name,))
        students = cursor.fetchall()
        return jsonify(students)
    except Exception as e:
        print(f"Error: {e}")
        return jsonify([])
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- RESULT CARD DOWNLOAD ---
@app.route('/download_result/<int:student_id>')
def download_result(student_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM students WHERE id = %s", (student_id,))
        student = cursor.fetchone()
        if not student: 
            return "Student Record Not Found", 404
        cursor.execute("SELECT * FROM grades WHERE student_id = %s", (student_id,))
        grades = cursor.fetchall()

        total_obt = sum(g['marks_obtained'] for g in grades)
        total_max = sum(g['total_marks'] for g in grades)
        perc = round((total_obt / total_max * 100), 2) if total_max > 0 else 0

        pdf = FPDF()
        pdf.add_page()
        pdf.set_font("Arial", 'B', 20)
        pdf.cell(0, 10, "EDUSCAN RESULT CARD", ln=True, align='C')
        pdf.ln(10)
        pdf.set_font("Arial", '', 12)
        pdf.cell(0, 10, f"Name: {student['name']}", ln=True)
        pdf.cell(0, 10, f"Class: {student['class_name']}", ln=True)
        pdf.ln(5)
        pdf.cell(80, 10, "Subject", 1)
        pdf.cell(40, 10, "Marks", 1)
        pdf.ln()
        for g in grades:
            pdf.cell(80, 10, g['subject'], 1)
            pdf.cell(40, 10, f"{g['marks_obtained']}/{g['total_marks']}", 1)
            pdf.ln()
        pdf.ln(10)
        pdf.cell(0, 10, f"Total Percentage: {perc}%", ln=True)

        pdf_content = pdf.output(dest='S').encode('latin-1')
        return send_file(io.BytesIO(pdf_content), download_name=f"Result_{student['name']}.pdf", as_attachment=True)
    except Exception as e: 
        return f"Error: {e}"
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/admin/view_tables')
@admin_required
def view_tables():
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT * FROM students")
        students = cursor.fetchall()
        
        cursor.execute("SELECT * FROM teachers")
        teachers = cursor.fetchall()
        
        cursor.execute("SELECT * FROM classes")
        classes_data = cursor.fetchall()
        
        cursor.execute("SELECT * FROM attendance ORDER BY log_date DESC, log_time DESC LIMIT 50")
        attendance_data = cursor.fetchall()
        
        return render_template('view_tables.html', 
                               students=students, 
                               teachers=teachers, 
                               classes=classes_data, 
                               attendance=attendance_data)
    except Exception as e:
        flash(f"Database View Error: {e}", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- ATTENDANCE MANAGEMENT ROUTES ---

@app.route('/teacher/attendance_sheet')
@teacher_required
def attendance_sheet():
    my_class = session.get('assigned_class')
    today = datetime.now().strftime('%Y-%m-%d')
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT id, name FROM students WHERE class_name = %s ORDER BY name ASC", (my_class,))
        students = cursor.fetchall()
        
        cursor.execute("SELECT subjects FROM classes WHERE class_name = %s", (my_class,))
        class_info = cursor.fetchone()
        subjects = class_info['subjects'].split(',') if class_info and class_info['subjects'] else ['General']

        return render_template('attendance_sheet.html', 
                               students=students, 
                               subjects=subjects, 
                               today=today, 
                               my_class=my_class)
    except Exception as e:
        flash(f"Error loading attendance sheet: {e}", "danger")
        return redirect(url_for('teacher_dashboard'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/teacher/save_attendance', methods=['POST'])
@teacher_required
def save_attendance():
    my_class = session.get('assigned_class')
    attendance_date = request.form.get('attendance_date')
    subject = request.form.get('subject')
    attendance_data = request.form.getlist('attendance_status')
    
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        now_time = datetime.now().strftime("%H:%M:%S")

        cursor.execute("SELECT name FROM students WHERE class_name = %s", (my_class,))
        all_students = cursor.fetchall()

        for student in all_students:
            name = student['name']
            status = 'Present' if str(name) in attendance_data else 'Absent'

            cursor.execute("""
                SELECT id FROM attendance 
                WHERE student_name=%s AND log_date=%s AND subject=%s
            """, (name, attendance_date, subject))
            
            existing = cursor.fetchone()
            
            if existing:
                cursor.execute("""
                    UPDATE attendance SET status=%s, log_time=%s 
                    WHERE id=%s
                """, (status, now_time, existing['id']))
            else:
                cursor.execute("""
                    INSERT INTO attendance (student_name, class_name, log_date, log_time, status, subject) 
                    VALUES (%s, %s, %s, %s, %s, %s)
                """, (name, my_class, attendance_date, now_time, status, subject))
        
        conn.commit()
        flash(f"Attendance for {subject} saved successfully!", "success")
    except Exception as e:
        flash(f"Error saving attendance: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        
    return redirect(url_for('attendance_sheet'))

@app.route('/teacher/attendance_logs')
@teacher_required
def view_attendance_logs():
    my_class = session.get('assigned_class')
    
    if not my_class:
        flash("No class assigned to your profile.", "danger")
        return redirect(url_for('teacher_dashboard'))

    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        query = """
            SELECT student_name, status, log_date, log_time, subject 
            FROM attendance 
            WHERE class_name = %s 
            ORDER BY log_date DESC, log_time DESC
        """
        cursor.execute(query, (my_class,))
        logs = cursor.fetchall()
        
        return render_template('attendance_logs.html', logs=logs, class_name=my_class)
        
    except Exception as e:
        flash(f"Error fetching attendance logs: {e}", "danger")
        return redirect(url_for('teacher_dashboard'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# --- CLASS DETAILS & UPLOAD LOGIC ---

@app.route('/teacher/class_details/<string:class_name>')
@teacher_required
def class_details(class_name):
    return render_template('class_details.html', class_name=class_name)

@app.route('/teacher/upload_file', methods=['POST'])
@teacher_required
def upload_file():
    if 'resource_file' not in request.files:
        flash('No file part', 'danger')
        return redirect(request.referrer or url_for('teacher_dashboard'))
    
    file = request.files['resource_file']

    if file.filename == '':
        flash('No selected file', 'danger')
        return redirect(request.referrer or url_for('teacher_dashboard'))

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        save_path = os.path.join(app.config['RESOURCE_UPLOAD_FOLDER'], filename)
        
        file.save(save_path)

        try:
            db_path = f"uploads/resources/{filename}"
            
            new_resource = ClassResources(
                file_name=filename,
                file_type=request.form.get('file_type'),
                file_path=db_path,
                description=request.form.get('description', ''),
                class_name=request.form.get('class_name')
            )
            db.session.add(new_resource)
            db.session.commit()
            flash(f'File "{filename}" uploaded successfully!', 'success')
        except Exception as e:
            db.session.rollback()
            flash('Database save failed', 'danger')
            
        return redirect(request.referrer or url_for('teacher_dashboard'))
    
    flash('File type not allowed', 'danger')
    return redirect(request.referrer or url_for('teacher_dashboard'))

# --- TEACHER RESOURCE MANAGEMENT ---

@app.route('/teacher/class_details/<string:class_name>')
@teacher_required
def teacher_class_details(class_name):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT id, name FROM students WHERE class_name = %s", (class_name,))
        students = cursor.fetchall()
        
        cursor.execute("SELECT * FROM class_resources WHERE class_name = %s ORDER BY upload_date DESC", (class_name,))
        all_resources = cursor.fetchall()
        
        resources = {}
        for res in all_resources:
            if res['category'] not in resources:
                resources[res['category']] = res
        
        return render_template('teacher_class_details.html', 
                               class_name=class_name, 
                               students=students, 
                               resources=resources,
                               all_resources=all_resources)
    except Exception as e:
        flash(f"Error: {e}", "danger")
        return redirect(url_for('teacher_dashboard'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/teacher/upload_resource', methods=['POST'])
@teacher_required
def teacher_upload_resource():
    file = request.files.get('file')
    class_name = request.form.get('class_name')
    category = request.form.get('category') 
    description = request.form.get('description', '')

    if file and file.filename != '':
        filename = secure_filename(file.filename)
        save_dir = os.path.join(app.config['RESOURCE_UPLOAD_FOLDER'], class_name, category)
        if not os.path.exists(save_dir):
            os.makedirs(save_dir)
            
        full_path = os.path.join(save_dir, filename)
        file.save(full_path)
        
        conn = None
        cursor = None
        try:
            conn = mysql.connector.connect(**db_config)
            cursor = conn.cursor()
            query = """INSERT INTO class_resources 
                       (class_name, file_name, file_path, description, category, upload_date) 
                       VALUES (%s, %s, %s, %s, %s, %s)"""
            cursor.execute(query, (class_name, filename, full_path, description, category, datetime.now()))
            conn.commit()
            flash(f"Successfully uploaded to {category}!", "success")
        except Exception as e:
            flash(f"Database error: {e}", "danger")
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
            
    return redirect(request.referrer or url_for('teacher_dashboard'))

@app.route('/teacher/delete_resource/<int:resource_id>')
@teacher_required
def delete_teacher_resource(resource_id):
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        cursor.execute("SELECT file_path FROM class_resources WHERE id = %s", (resource_id,))
        resource = cursor.fetchone()
        
        if resource:
            if os.path.exists(resource['file_path']):
                os.remove(resource['file_path'])
            
            cursor.execute("DELETE FROM class_resources WHERE id = %s", (resource_id,))
            conn.commit()
            flash("Resource deleted successfully!", "success")
        
    except Exception as e:
        flash(f"Delete Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
        
    return redirect(request.referrer or url_for('teacher_dashboard'))

@app.route('/student')
def student_dashboard():
    student_class = session.get('class_name') 
    
    student_resources = ClassResources.query.filter_by(class_name=student_class).all()
    
    return render_template('student_portal.html', uploads=student_resources)

@app.route('/resources', methods=['GET'])
def get_resources():
    resources = ClassResources.query.all() 
    
    output = []
    for r in resources:
        output.append({
            "id": r.id,
            "class_name": r.class_name,
            "filename": r.file_name
        })
    
    return jsonify(output)
@app.route('/resources/<class_name>')
def show_resources(class_name):
    data = ClassResources.query.filter_by(class_name=class_name).all()
    
    formatted_resources = []
    for r in data:
        formatted_resources.append([
            r.file_name,  # row[0]: Display Title
            getattr(r, 'file_type', 'Resource'),  # row[1]: Type (safe get)
            r.file_name,  # row[2]: Just filename - template will add path
            getattr(r, 'description', 'No description available.'),  # row[3]
            r.id  # row[4]: ID for delete
        ])
    
    return render_template('resources.html', 
                           resources=formatted_resources, 
                           class_name=class_name)

@app.route('/upload_resource', methods=['POST'])
@teacher_required
def upload_resource():
    title = request.form.get('title')
    description = request.form.get('description')
    class_name = request.form.get('class_name')
    file = request.files.get('file')

    # 2. Check if file exists and validate the extension
    if not file or not allowed_file(file.filename):
        # THIS PREVENTS THE CRASH: It shows a popup message instead
        flash("Invalid file type! Please select a valid document (PDF, Doc, Image, etc.).", "danger")
        return redirect(url_for('teacher_dashboard'))

    filename = file.filename
    # Get the file extension (e.g., 'pdf' or 'jpg')
    file_type = filename.rsplit('.', 1)[1].lower() 
    
    try:
        # 3. Save the file
        file.save(os.path.join('static/uploads', filename))
        
        # 4. Database Connection
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()

        # NOTE: I included 'title' here. 
        # IMPORTANT: You MUST run "ALTER TABLE class_resources ADD COLUMN title VARCHAR(255) AFTER id;" 
        # in your MySQL console first for this query to work!
        query = """
            INSERT INTO class_resources 
            (title, class_name, file_name, file_type, file_path, description) 
            VALUES (%s, %s, %s, %s, %s, %s)
        """
        
        # 5. Execute with all fields including the file_type we extracted
        cursor.execute(query, (title, class_name, filename, file_type, f"uploads/{filename}", description))
        
        conn.commit()
        flash("Resource uploaded successfully!", "success")
        
    except mysql.connector.Error as err:
        print(f"MySQL Error: {err}")
        flash(f"Database Error: {err}", "danger")
    finally:
        if conn.is_connected():
            cursor.close()
            conn.close()
            
    return redirect(url_for('teacher_dashboard'))
@app.route('/delete_resource/<int:resource_id>', methods=['POST'])
def delete_resource(resource_id):
    resource = ClassResources.query.get_or_404(resource_id)
    class_name = resource.class_name 
    
    try:
        file_path = os.path.join(app.config['RESOURCE_UPLOAD_FOLDER'], resource.file_name)
        if os.path.exists(file_path):
            os.remove(file_path)
        
        db.session.delete(resource)
        db.session.commit()
        flash("Resource deleted.", "info")
    except Exception as e:
        db.session.rollback()
        flash(f"Delete Error: {e}", "danger")

    return redirect(url_for('show_resources', class_name=class_name))



# --- CLASS FEE MANAGEMENT ROUTES ---

@app.route('/admin/class_fees')
@admin_required
def manage_class_fees():
    """Admin page to set fees for each class"""
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor(dictionary=True)
        
        # Get all classes with their fees
        cursor.execute("""
            SELECT c.class_name, c.subjects, cf.monthly_fee, cf.admission_fee, cf.exam_fee, cf.other_charges
            FROM classes c
            LEFT JOIN class_fees cf ON c.class_name = cf.class_name
            ORDER BY c.class_name
        """)
        class_fees = cursor.fetchall()
        
        return render_template('admin_class_fees.html', class_fees=class_fees)
    except Exception as e:
        flash(f"Error: {e}", "danger")
        return redirect(url_for('admin_dashboard'))
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

@app.route('/admin/update_class_fee', methods=['POST'])
@admin_required
def update_class_fee():
    """Update fee structure for a class"""
    class_name = request.form.get('class_name')
    monthly_fee = float(request.form.get('monthly_fee', 0))
    admission_fee = float(request.form.get('admission_fee', 0))
    exam_fee = float(request.form.get('exam_fee', 0))
    other_charges = float(request.form.get('other_charges', 0))
    
    conn = None
    cursor = None
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        
        # Check if record exists
        cursor.execute("SELECT id FROM class_fees WHERE class_name = %s", (class_name,))
        existing = cursor.fetchone()
        
        if existing:
            cursor.execute("""
                UPDATE class_fees 
                SET monthly_fee = %s, admission_fee = %s, exam_fee = %s, other_charges = %s, last_updated = %s
                WHERE class_name = %s
            """, (monthly_fee, admission_fee, exam_fee, other_charges, datetime.now(), class_name))
        else:
            cursor.execute("""
                INSERT INTO class_fees (class_name, monthly_fee, admission_fee, exam_fee, other_charges, last_updated)
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (class_name, monthly_fee, admission_fee, exam_fee, other_charges, datetime.now()))
        
        conn.commit()
        flash(f"Fee structure updated for {class_name}!", "success")
    except Exception as e:
        flash(f"Error: {e}", "danger")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    
    return redirect(url_for('manage_class_fees'))

@app.route('/admin/generate_invoices_now', methods=['POST'])
@admin_required
def generate_invoices_now():
    """Manual trigger to generate invoices immediately"""
    count = generate_monthly_invoices()
    flash(f"Generated {count} invoices successfully!", "success")
    return redirect(url_for('admin_dashboard'))



# Initialize invoice scheduler when app starts
with app.app_context():
    # Create class_fees table if not exists
    try:
        conn = mysql.connector.connect(**db_config)
        cursor = conn.cursor()
        cursor.execute("""
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
        """)
        conn.commit()
        cursor.close()
        conn.close()
        print("Class fees table initialized")
    except Exception as e:
        print(f"Table init error: {e}")

# Start background scheduler (only in production, not in debug reloader)
if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
    start_invoice_scheduler()


def save_enrollment_image(frame, name):
    # Use your helper to get a safe filename
    filename = generate_image_filename(name) 
    save_path = os.path.join('static/enrollments', filename)
    
    # Save the actual image to the folder
    cv2.imwrite(save_path, frame)
    return filename



# MOVE THIS TO THE VERY BOTTOM OF app.py
# PLACE THIS AT THE VERY BOTTOM OF app.py

from flask import current_app, jsonify, flash, redirect, url_for

@app.route('/delete_student_face/<int:student_id>')
def delete_student_face(student_id):
    student = Student.query.get_or_404(student_id)
    
    # Check if we have an image to remove from the folder
    if student.face_image:
        # Construct the physical path
        # Note: If s.face_image is 'student_1.jpg', this points to static/enrollments/student_1.jpg
        file_path = os.path.join(current_app.root_path, 'static', 'enrollments', student.face_image)
        
        try:
            # 1. Delete physical file from disk if it exists
            if os.path.exists(file_path):
                os.remove(file_path)
        except Exception as e:
            print(f"File deletion error: {str(e)}")

    try:
        # 2. DELETE the entire student record from the database
        db.session.delete(student)
        db.session.commit()
        
        # 3. CHECK: Is this an AJAX request? 
        # If the user clicked our new button, return JSON so it doesn't refresh
        return jsonify({"status": "success", "message": "Student record and face data deleted"}), 200

    except Exception as e:
        db.session.rollback()
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/edit_student/<int:student_id>', methods=['POST'])
def edit_student(student_id):
    student = db.session.get(Student, student_id)
    
    if request.method == 'POST':
        # Get the new text from the form input 'name'
        new_name = request.form.get('student_name')
        
        if student:
            student.name = new_name  # Update the object
            db.session.commit()      # <--- IS THIS LINE MISSING?
            print(f"DEBUG: Saved {new_name} to database")
            
    return redirect(url_for('index'))



@app.route('/student_resources/<class_name>')
def student_resources(class_name):
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    
    # Query the 'uploads' table for files matching this class
    # Selecting: title, filename, file_path, description
    query = "SELECT title, filename, file_path, description FROM uploads WHERE class_name = %s"
    cursor.execute(query, (class_name,))
    
    resources = cursor.fetchall() # This returns a list of tuples: [(t1, f1, p1, d1), ...]
    
    cursor.close()
    conn.close()
    
    return render_template('student_resources.html', 
                           class_name=class_name, 
                           resources=resources)




@app.route('/attendance_dashboard')
@teacher_required
def attendance_dashboard():
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    
    cursor.execute("SELECT DISTINCT class_name FROM attendance")
    all_classes = [row['class_name'] for row in cursor.fetchall()]
    
    cursor.execute("SELECT * FROM attendance ORDER BY log_date DESC, log_time DESC")
    all_records = cursor.fetchall()
    
    cursor.close()
    conn.close()
    
    return render_template('attendance_logs.html', classes=all_classes, records=all_records)









if __name__ == '__main__':
    app.run(debug=True)