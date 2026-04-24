# EduScan Portal: Advanced Face Attendance System 🎓🔍

EduScan is a high-performance biometric solution designed to automate student attendance using real-time facial recognition. The system features a multi-role dashboard architecture and deep integration with a MySQL backend to ensure data integrity and automated reporting.

## 🌟 Key Features
* **Role-Based Access Control**: Specialized portals for **Admin** (System Management), **Teacher** (Grade & Leave Management), and **Student** (Attendance & Grades).
* **Real-Time Biometric Scanning**: A live interface to start/stop the camera feed and capture student presence instantly.
* **Comprehensive Attendance Logging**: A robust backend log tracking Student Name, Class, Subject, and Status (Present/Absent) with precise timestamps.
* **Data Portability**: Integrated functionality to export attendance records directly to Excel for administrative use.

## 🛠️ Tech Stack
* **Backend**: Python (Flask/FastAPI)
* **Frontend**: Responsive HTML5, CSS3, and JavaScript
* **Database**: MySQL
* **Computer Vision**: OpenCV / Face Recognition library

## 📊 System Preview
The system maintains a structured log of all entries, allowing administrators to monitor attendance trends:

| Student Name | Class | Subject | Status | Date |
| :--- | :--- | :--- | :--- | :--- |
| ahad | Class 8 | Re-Engineering | Present | 2026-03-04 |
| qasim | Class 3 | English | Present | 2026-03-03 |
| hashim | Class 3 | Math | Present | 2026-03-01 |
*(Based on System Database Tables)*

## 🚀 Getting Started

### Prerequisites
* Python 3.10+
* MySQL Server
* Webcam (for live scanning)

### Installation
1.  **Clone the repository**:
    ```bash
    git clone [https://github.com/qasimnizam9-oss/face-attendance-system.git](https://github.com/qasimnizam9-oss/face-attendance-system.git)
    ```
2.  **Setup Virtual Environment**:
    ```bash
    python -m venv .venv
    .\.venv\Scripts\activate
    ```
3.  **Install Dependencies**:
    ```bash
    pip install -r requirements.txt
    ```
4.  **Database Configuration**:
    * Create a database in MySQL.
    * Import your `.sql` dump file to set up the `attendance_logs` table.
5.  **Run the Application**:
    ```bash
    python app.py
    ```

## 📜 License
Distributed under the MIT License. See `LICENSE` for more information.