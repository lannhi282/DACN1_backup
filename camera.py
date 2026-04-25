import cv2
import mysql.connector
import numpy as np
import threading
import time


class VideoCamera:
    def __init__(self, db_config):
        self.db_config = db_config

        self.video = cv2.VideoCapture(0, cv2.CAP_DSHOW)

        if not self.video.isOpened():
            print("[CAMERA ERROR] Khong mo duoc webcam. Thu doi VideoCapture(0) thanh VideoCapture(1).")

        self.face_cascade = cv2.CascadeClassifier(
            cv2.data.haarcascades + "haarcascade_frontalface_default.xml"
        )

        if not hasattr(cv2, "face") or not hasattr(cv2.face, "LBPHFaceRecognizer_create"):
            raise RuntimeError(
                "Thieu LBPHFaceRecognizer. Hay cai opencv-contrib-python."
            )

        self.recognizer = cv2.face.LBPHFaceRecognizer_create(
            radius=1,
            neighbors=8,
            grid_x=8,
            grid_y=8
        )

        self.label_to_name = {}
        self.trained = False

        self.current_name = "Scanning..."
        self.is_recognizing = False
        self.last_name = "Unknown"
        self.stable_count = 0
        self.required_stable_frames = 3
        self.last_valid_name = None
        self.last_valid_time = 0
        self.unknown_tolerance_seconds = 1.5

        # Trang enroll trong app.py dang can cac bien nay
        self.enroll_mode = False
        self.enroll_done = False
        self.enroll_success = False
        self.enroll_student_name = None
        self.enroll_samples = []
        self.enroll_needed = 15
        self.enroll_preview_frame = None
        self.enroll_message = "Dang cho camera..."

        self.lock = threading.Lock()

        self.load_known_faces()

    def get_connection(self):
        return mysql.connector.connect(**self.db_config)

    def get_largest_face(self, gray):
        faces = self.face_cascade.detectMultiScale(
            gray,
            scaleFactor=1.1,
            minNeighbors=6,
            minSize=(80, 80)
        )

        if len(faces) == 0:
            return None

        return max(faces, key=lambda f: f[2] * f[3])

    def preprocess_face(self, frame, face_box):
        x, y, w, h = face_box

        pad = int(w * 0.15)
        x1 = max(0, x - pad)
        y1 = max(0, y - pad)
        x2 = min(frame.shape[1], x + w + pad)
        y2 = min(frame.shape[0], y + h + pad)

        face = frame[y1:y2, x1:x2]
        gray_face = cv2.cvtColor(face, cv2.COLOR_BGR2GRAY)
        gray_face = cv2.resize(gray_face, (200, 200))
        gray_face = cv2.equalizeHist(gray_face)

        return gray_face

    def is_face_clear(self, gray_face):
        blur_score = cv2.Laplacian(gray_face, cv2.CV_64F).var()
        return blur_score > 45

    def start_enroll(self, student_name):
        """
        Ham nay KHONG duoc block.
        app.py goi start_enroll xong redirect sang trang enroll_camera.
        Neu ham nay chay capture truc tiep thi trang se bi den/khong stream.
        """
        with self.lock:
            self.enroll_mode = True
            self.enroll_done = False
            self.enroll_success = False
            self.enroll_student_name = student_name
            self.enroll_samples = []
            self.enroll_preview_frame = None
            self.enroll_message = "Hay nhin thang vao camera"

        thread = threading.Thread(
            target=self._enroll_worker,
            args=(student_name,),
            daemon=True
        )
        thread.start()

        return True

    def reset_enroll(self):
        with self.lock:
            self.enroll_mode = False
            self.enroll_done = False
            self.enroll_success = False
            self.enroll_student_name = None
            self.enroll_samples = []
            self.enroll_preview_frame = None
            self.enroll_message = "Dang cho camera..."

    def _enroll_worker(self, student_name):
        print(f"[ENROLL] Bat dau quet khuon mat cho: {student_name}")

        conn = None
        cursor = None

        try:
            conn = self.get_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                "SELECT id FROM students WHERE name = %s ORDER BY id DESC LIMIT 1",
                (student_name,)
            )
            student = cursor.fetchone()

            if not student:
                print("[ENROLL] Khong tim thay student.")
                with self.lock:
                    self.enroll_done = True
                    self.enroll_success = False
                    self.enroll_mode = False
                    self.enroll_message = "Khong tim thay student"
                return

            student_id = student["id"]

            cursor.execute(
                "DELETE FROM student_face_samples WHERE student_id = %s",
                (student_id,)
            )
            conn.commit()

            max_seconds = 25
            min_interval = 0.45
            start_time = time.time()
            last_capture = 0

            hints = [
                "Nhin thang vao camera",
                "Quay mat nhe sang trai",
                "Quay mat nhe sang phai",
                "Cui nhe xuong",
                "Ngua nhe len",
                "Nhin thang lai lan nua"
            ]

            while True:
                with self.lock:
                    current_count = len(self.enroll_samples)

                if current_count >= self.enroll_needed:
                    break

                if time.time() - start_time > max_seconds:
                    break

                ret, frame = self.video.read()

                if not ret or frame is None:
                    with self.lock:
                        self.enroll_message = "Khong doc duoc camera"
                    time.sleep(0.1)
                    continue

                gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
                face_box = self.get_largest_face(gray)

                hint_index = min(current_count // 3, len(hints) - 1)
                message = hints[hint_index]

                display = frame.copy()

                if face_box is None:
                    cv2.putText(
                        display,
                        "Khong thay khuon mat",
                        (30, 40),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.9,
                        (0, 0, 255),
                        2
                    )
                    with self.lock:
                        self.enroll_message = "Khong thay khuon mat"
                else:
                    x, y, w, h = face_box
                    cv2.rectangle(display, (x, y), (x + w, y + h), (0, 255, 0), 2)

                    gray_face = self.preprocess_face(frame, face_box)

                    now = time.time()

                    if now - last_capture >= min_interval:
                        if self.is_face_clear(gray_face):
                            success, buffer = cv2.imencode(".jpg", gray_face)

                            if success:
                                with self.lock:
                                    self.enroll_samples.append(buffer.tobytes())
                                    current_count = len(self.enroll_samples)

                                last_capture = now
                                print(f"[ENROLL] Da chup mau {current_count}/{self.enroll_needed}")
                        else:
                            message = "Anh bi mo, giu yen khuon mat"

                    cv2.putText(
                        display,
                        message,
                        (30, 40),
                        cv2.FONT_HERSHEY_SIMPLEX,
                        0.8,
                        (0, 255, 255),
                        2
                    )

                with self.lock:
                    count_text = f"{len(self.enroll_samples)} / {self.enroll_needed}"
                    self.enroll_message = message

                cv2.putText(
                    display,
                    count_text,
                    (30, 80),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.8,
                    (255, 255, 255),
                    2
                )

                ok, jpeg = cv2.imencode(".jpg", display)
                if ok:
                    with self.lock:
                        self.enroll_preview_frame = jpeg.tobytes()

                time.sleep(0.05)

            with self.lock:
                samples_to_save = list(self.enroll_samples)

            if len(samples_to_save) < 10:
                print("[ENROLL] Khong du mau khuon mat.")
                with self.lock:
                    self.enroll_done = True
                    self.enroll_success = False
                    self.enroll_mode = False
                    self.enroll_message = "Khong du mau khuon mat"
                return

            for blob in samples_to_save:
                cursor.execute("""
                    INSERT INTO student_face_samples
                    (student_id, student_name, face_data)
                    VALUES (%s, %s, %s)
                """, (student_id, student_name, blob))

                cursor.execute(
                "UPDATE students SET face_data = %s WHERE id = %s",
                (samples_to_save[0], student_id)
            )

            # Luu them 1 anh dai dien vao thu muc static/enrollments
            import os

            enroll_dir = os.path.join("static", "enrollments")
            os.makedirs(enroll_dir, exist_ok=True)

            safe_name = "".join(
                c for c in student_name
                if c.isalnum() or c in (" ", "_", "-")
            ).strip()

            file_path = os.path.join(enroll_dir, f"{safe_name}.jpg")

            nparr = np.frombuffer(samples_to_save[0], np.uint8)
            face_img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

            if face_img is not None:
                cv2.imwrite(file_path, face_img)
                print(f"[ENROLL] Da luu anh dai dien vao {file_path}")

            conn.commit()

            self.load_known_faces()

            print(f"[ENROLL] Hoan tat. Da luu {len(samples_to_save)} mau cho {student_name}.")

            with self.lock:
                self.enroll_done = True
                self.enroll_success = True
                self.enroll_mode = False
                self.enroll_message = "Hoan tat"

        except Exception as e:
            print(f"[ERROR] _enroll_worker: {e}")

            with self.lock:
                self.enroll_done = True
                self.enroll_success = False
                self.enroll_mode = False
                self.enroll_message = str(e)

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    def get_enroll_frame(self):
        """
        app.py/enroll_feed dang goi ham nay.
        Tra ve: frame_bytes, done, success
        """
        with self.lock:
            frame_bytes = self.enroll_preview_frame
            done = self.enroll_done
            success = self.enroll_success

        if frame_bytes:
            return frame_bytes, done, success

        ret, frame = self.video.read()

        if not ret or frame is None:
            black = np.zeros((480, 640, 3), dtype=np.uint8)
            cv2.putText(
                black,
                "Khong mo duoc camera",
                (90, 240),
                cv2.FONT_HERSHEY_SIMPLEX,
                1,
                (0, 0, 255),
                2
            )
            ok, jpeg = cv2.imencode(".jpg", black)
            return (jpeg.tobytes() if ok else None), done, success

        cv2.putText(
            frame,
            "Dang khoi dong camera...",
            (30, 40),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.8,
            (255, 255, 255),
            2
        )

        ok, jpeg = cv2.imencode(".jpg", frame)
        return (jpeg.tobytes() if ok else None), done, success

    def load_known_faces(self):
        try:
            conn = self.get_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT s.id AS student_id, s.name, fs.face_data
                FROM student_face_samples fs
                JOIN students s ON fs.student_id = s.id
                WHERE fs.face_data IS NOT NULL
            """)

            rows = cursor.fetchall()
            cursor.close()
            conn.close()

            faces = []
            labels = []
            self.label_to_name = {}

            label_map = {}
            next_label = 0

            for row in rows:
                student_id = row["student_id"]
                name = row["name"]

                if student_id not in label_map:
                    label_map[student_id] = next_label
                    self.label_to_name[next_label] = name
                    next_label += 1

                nparr = np.frombuffer(row["face_data"], np.uint8)
                img = cv2.imdecode(nparr, cv2.IMREAD_GRAYSCALE)

                if img is None:
                    continue

                img = cv2.resize(img, (200, 200))
                img = cv2.equalizeHist(img)

                faces.append(img)
                labels.append(label_map[student_id])

            if len(faces) > 0:
                self.recognizer.train(faces, np.array(labels))
                self.trained = True
                print(f"[CAMERA] Da train {len(faces)} mau khuon mat.")
            else:
                self.trained = False
                print("[CAMERA] Chua co mau khuon mat de train.")

        except Exception as e:
            self.trained = False
            print(f"[ERROR] load_known_faces: {e}")

    def recognize_async(self, face_roi):
        def run():
            try:
                if not self.trained:
                    self.current_name = "Unknown"
                    return

                gray = cv2.cvtColor(face_roi, cv2.COLOR_BGR2GRAY)
                gray = cv2.resize(gray, (200, 200))
                gray = cv2.equalizeHist(gray)

                label, confidence = self.recognizer.predict(gray)

                threshold = 95

                if confidence < threshold:
                    name = self.label_to_name.get(label, "Unknown")
                else:
                    name = "Unknown"

                print(f"[RECOGNIZE] name={name}, confidence={confidence:.2f}")

                self.current_name = name

            except Exception as e:
                print(f"[ERROR] recognize_async: {e}")
                self.current_name = "Unknown"

            finally:
                self.is_recognizing = False

        if not self.is_recognizing:
            self.is_recognizing = True
            t = threading.Thread(target=run, daemon=True)
            t.start()

    def get_processed_frame(self, log_callback):
        ret, frame = self.video.read()

        if not ret or frame is None:
            return None

        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        face_box = self.get_largest_face(gray)

        if face_box is not None:
            x, y, w, h = face_box
            face_roi = frame[y:y + h, x:x + w]

            self.recognize_async(face_roi)

            name = self.current_name
            now = time.time()

# Neu vua nhan dien dung xong ma 1-2 frame sau bi Unknown,
# giu lai ten cu de tranh bi nhap nhay Unknown
            if name not in ["Unknown", "Scanning..."]:
                self.last_valid_name = name
                self.last_valid_time = now
            else:
                if self.last_valid_name and (now - self.last_valid_time) <= self.unknown_tolerance_seconds:
                    name = self.last_valid_name

            if name == self.last_name and name not in ["Unknown", "Scanning..."]:
                self.stable_count += 1
            else:
                self.stable_count = 0
                self.last_name = name

            can_log = self.stable_count >= self.required_stable_frames

            if can_log:
                display_name = name
                color = (0, 255, 0)
                log_callback(name)
            else:
                display_name = "Scanning..." if name != "Unknown" else "Unknown"
                color = (0, 165, 255)

            cv2.rectangle(frame, (x, y), (x + w, y + h), color, 2)
            cv2.putText(
                frame,
                display_name,
                (x, y - 10),
                cv2.FONT_HERSHEY_DUPLEX,
                0.8,
                color,
                2
            )

        success, jpeg = cv2.imencode(".jpg", frame)
        return jpeg.tobytes() if success else None

    def get_raw_frame(self):
        if not self.video.isOpened():
            print("[CAMERA ERROR] Webcam chua mo.")
            return None

        success, frame = self.video.read()

        if not success or frame is None:
            print("[CAMERA ERROR] Khong doc duoc frame tu webcam.")
            return None

        return frame

    def __del__(self):
        if hasattr(self, "video") and self.video.isOpened():
            self.video.release()