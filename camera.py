import cv2
import mysql.connector
import numpy as np
import threading
import time

class VideoCamera:
    def __init__(self, db_config):
        self.video = cv2.VideoCapture(0)
        self.db_config = db_config
        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + 'haarcascade_frontalface_default.xml'
        )
        self.known_faces = []
        self.current_name = "Scanning..."
        self.is_recognizing = False
        self.load_known_faces()

    def get_face_hist(self, face_img):
        """Tạo histogram màu từ ảnh mặt để so sánh"""
        face_resized = cv2.resize(face_img, (100, 100))
        hsv = cv2.cvtColor(face_resized, cv2.COLOR_BGR2HSV)
        hist = cv2.calcHist([hsv], [0, 1], None, [50, 60], [0, 180, 0, 256])
        cv2.normalize(hist, hist, 0, 1, cv2.NORM_MINMAX)
        return hist

    def load_known_faces(self):
        """Load ảnh từ DB và tính histogram"""
        try:
            conn = mysql.connector.connect(**self.db_config)
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT name, face_data FROM students WHERE face_data IS NOT NULL")
            records = cursor.fetchall()
            cursor.close()
            conn.close()

            self.known_faces = []
            for row in records:
                nparr = np.frombuffer(row['face_data'], np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
                if img is None:
                    print(f"[WARN] Không đọc được ảnh: {row['name']}")
                    continue

                hist = self.get_face_hist(img)
                self.known_faces.append({
                    'name': row['name'],
                    'hist': hist
                })
                print(f"[LOAD] ✅ {row['name']}")

            print(f"[CAMERA] Đã load {len(self.known_faces)} khuôn mặt")

        except Exception as e:
            print(f"Lỗi load: {e}")

    def enroll_face(self, student_name):
        """Chờ cho đến khi phát hiện mặt rồi mới chụp"""
        print(f"[ENROLL] Đang chờ mặt của {student_name}...")

        for attempt in range(100):
            ret, frame = self.video.read()
            if not ret:
                time.sleep(0.3)
                continue

            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            faces = self.face_cascade.detectMultiScale(gray, 1.1, 5)

            if len(faces) > 0:
                (x, y, w, h) = faces[0]
                face_img = frame[y:y+h, x:x+w]
                face_img = cv2.resize(face_img, (224, 224))

                _, buffer = cv2.imencode('.jpg', face_img)
                blob = buffer.tobytes()

                try:
                    conn = mysql.connector.connect(**self.db_config)
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE students SET face_data = %s WHERE name = %s",
                        (blob, student_name)
                    )
                    conn.commit()
                    cursor.close()
                    conn.close()
                    self.load_known_faces()
                    print(f"[ENROLL] ✅ Thành công: {student_name}")
                    return True
                except Exception as e:
                    print(f"Lỗi DB: {e}")
                    return False
            else:
                print(f"[ENROLL] Chưa thấy mặt, thử lại... ({attempt+1}/100)")
                time.sleep(0.3)

        print(f"[ENROLL] ❌ Hết thời gian chờ: {student_name}")
        return False

    def recognize_async(self, face_roi):
        """So sánh histogram trong thread riêng"""
        def run():
            try:
                if len(self.known_faces) == 0:
                    self.current_name = "Unknown"
                    return

                query_hist = self.get_face_hist(face_roi)
                best_name = "Unknown"
                best_score = 0.4

                for person in self.known_faces:
                    score = cv2.compareHist(
                        query_hist,
                        person['hist'],
                        cv2.HISTCMP_CORREL
                    )
                    if score > best_score:
                        best_score = score
                        best_name = person['name']

                self.current_name = best_name
                print(f"[RESULT] → {best_name} (score={best_score:.3f})")

            except Exception as e:
                print(f"Recognize error: {e}")
                self.current_name = "Unknown"
            finally:
                self.is_recognizing = False

        if not self.is_recognizing:
            self.is_recognizing = True
            t = threading.Thread(target=run, daemon=True)
            t.start()

    def get_processed_frame(self, log_callback):
        ret, frame = self.video.read()
        if not ret:
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        small_gray = cv2.resize(gray, (0, 0), fx=0.5, fy=0.5)
        faces = self.face_cascade.detectMultiScale(small_gray, 1.1, 5)

        for (x, y, w, h) in faces:
            x, y, w, h = x*2, y*2, w*2, h*2
            face_roi = frame[y:y+h, x:x+w]

            self.recognize_async(face_roi)

            name = self.current_name
            color = (0, 255, 0) if name not in ["Unknown", "Scanning..."] else (0, 165, 255)

            cv2.rectangle(frame, (x, y), (x+w, y+h), color, 2)
            cv2.putText(frame, name, (x, y-10),
                        cv2.FONT_HERSHEY_DUPLEX, 0.8, color, 2)

            if name not in ["Unknown", "Scanning..."]:
                log_callback(name)

        _, jpeg = cv2.imencode('.jpg', frame)
        return jpeg.tobytes()

    def get_raw_frame(self):
        success, frame = self.video.read()
        return frame if success else None

    def __del__(self):
        if self.video.isOpened():
            self.video.release()