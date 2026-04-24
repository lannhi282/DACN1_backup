import cv2
import mysql.connector
import numpy as np
import os

class VideoCamera:
    def __init__(self, db_config):
        self.video = cv2.VideoCapture(0)
        self.db_config = db_config
        self.face_cascade = cv2.CascadeClassifier(cv2.data.haarcascades + 'haarcascade_frontalface_default.xml')
        # Cache list to store images in memory for speed
        self.known_students = []
        self.load_known_faces()

    def load_known_faces(self):
        """Fetches all students from DB and stores them in memory for instant recognition"""
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor(dictionary=True)
            # Only load students who actually have face data
            cursor.execute("SELECT name, face_data FROM students WHERE face_data IS NOT NULL")
            records = cursor.fetchall()
            
            temp_list = []
            for row in records:
                # Convert BLOB to image once and keep it in RAM
                nparr = np.frombuffer(row['face_data'], np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is not None:
                    temp_list.append({'name': row['name'], 'image': img})
            
            self.known_students = temp_list
            print(f"--- [CAMERA] Loaded {len(self.known_students)} students into memory ---")
            
            cursor.close()
            conn.close()
        except Exception as e:
            print(f"Error loading faces: {e}")

    def enroll_face(self, student_name):
        """Captures a frame and saves it to the database for the student"""
        success, frame = self.video.read()
        if not success:
            return False

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.1, 6)

        if len(faces) > 0:
            # Take the first face detected
            (x, y, w, h) = faces[0]
            face_roi = frame[y:y+h, x:x+w]

            # Encode image to binary for MySQL BLOB
            _, buffer = cv2.imencode('.jpg', face_roi)
            blob_data = buffer.tobytes()

            try:
                conn = mysql.connector.connect(**self.db_config)
                cursor = conn.cursor()
                # Update the student record created by app.py with the face data
                cursor.execute("UPDATE students SET face_data = %s WHERE name = %s", (blob_data, student_name))
                conn.commit()
                cursor.close()
                conn.close()
                
                # Refresh the memory cache so recognition works instantly
                self.load_known_faces()
                return True
            except Exception as e:
                print(f"Database enrollment error: {e}")
                return False
        return False

    def __del__(self):
        if self.video.isOpened():
            self.video.release()

    def get_raw_frame(self):
        success, frame = self.video.read()
        return frame if success else None

    def recognize_student(self, face_roi):
        # Local import to prevent startup lag
        from deepface import DeepFace
        best_match = "Unknown"
        
        # Use the memory cache (self.known_students) instead of querying the DB every frame
        try:
            for student in self.known_students:
                # ResNet Verification
                result = DeepFace.verify(face_roi, student['image'], 
                                          model_name='ResNet', 
                                          enforce_detection=False,
                                          detector_backend='opencv')
                
                if result['verified']:
                    best_match = student['name']
                    break
        except Exception as e:
            print(f"DeepFace Error: {e}")
                
        return best_match

    def get_processed_frame(self, log_callback):
        frame = self.get_raw_frame()
        if frame is None: return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        faces = self.face_cascade.detectMultiScale(gray, 1.1, 6)

        for (x, y, w, h) in faces:
            face_roi = frame[y:y+h, x:x+w]
            name = self.recognize_student(face_roi)

            # Draw UI Brackets
            color = (0, 255, 0) if name != "Unknown" else (0, 165, 255)
            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
            cv2.putText(frame, name, (x, y-10), cv2.FONT_HERSHEY_DUPLEX, 0.8, color, 2)

            if name != "Unknown":
                log_callback(name)

        _, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes()