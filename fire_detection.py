import cv2
import json
import subprocess
from ultralytics import YOLO
from time import sleep
import time
import numpy as np
import os
import pigpio
import threading
import requests # Gunakan requests untuk kirim ke API

# ======================
# 🔧 Setup (Servo, Relay, Buzzer) - SAMA SEPERTI SEBELUMNYA
# ======================
pi = pigpio.pi()
SERVO_X_PIN = 12
SERVO_Y_PIN = 13
RELAY_PIN = 22
BUZZER_PIN = 27

pi.set_mode(RELAY_PIN, pigpio.OUTPUT)
pi.set_mode(BUZZER_PIN, pigpio.OUTPUT)
pi.write(RELAY_PIN, 0)
pi.write(BUZZER_PIN, 0)

SERVO_X_MIN, SERVO_X_MAX = 900, 2100
SERVO_Y_MIN, SERVO_Y_MAX = 1000, 2000

def servo_us_x(value): return SERVO_X_MIN + (SERVO_X_MAX - SERVO_X_MIN) * value
def servo_us_y(value): return SERVO_Y_MIN + (SERVO_Y_MAX - SERVO_Y_MIN) * value

def move_servo(x_val, y_val):
    x_us = servo_us_x(x_val)
    y_us = servo_us_y(y_val)
    pi.set_servo_pulsewidth(SERVO_X_PIN, x_us)
    pi.set_servo_pulsewidth(SERVO_Y_PIN, y_us)
    print(f"🎛 Servo moved -> X:{x_val:.2f}, Y:{y_val:.2f}")

def buzzer_alert():
    print("🔔 Buzzer aktif")
    for _ in range(15):
        pi.write(BUZZER_PIN, 1); sleep(0.2)
        pi.write(BUZZER_PIN, 0); sleep(0.2)

def buzzer_background():
    thread = threading.Thread(target=buzzer_alert)
    thread.daemon = True
    thread.start()

# ======================
# 📡 Konfigurasi API
# ======================
# Ganti dengan URL Hosting Anda
API_ENDPOINT = "http://103.250.11.139/api_receiver.php" 
# Atau domain: "http://shieldweb.icu/api_receiver.php"

def send_ai_result_to_api(json_data, image_filename=None):
    """Mengirim hasil AI dan gambar ke API PHP"""
    try:
        # Siapkan data teks
        payload = {'json_data': json.dumps(json_data)}
        files = []
        
        # Jika ada gambar, siapkan file untuk diupload
        if image_filename and os.path.exists(image_filename):
            files = [('image_file', (image_filename, open(image_filename, 'rb'), 'image/jpeg'))]
            print(f"📤 Mengirim data + gambar ({image_filename}) ke API...")
        else:
            print("📤 Mengirim data (tanpa gambar) ke API...")

        # Kirim POST Request
        response = requests.post(API_ENDPOINT, data=payload, files=files, timeout=15)
        
        # Cek Respon
        if response.status_code == 201:
            print("✅ Sukses: Data berhasil disimpan di server.")
        else:
            print(f"❌ Gagal: Server merespon {response.status_code} - {response.text}")

    except Exception as e:
        print(f"❌ Error mengirim ke API: {e}")

# ... (Bagian positions, frame setup, YOLO model TETAP SAMA) ...
positions = [("TL", 0.0, 0.0), ("TM", 0.5, 0.0), ("TR", 1.0, 0.0), ("MR", 1.0, 0.5), ("MM", 0.5, 0.5), ("ML", 0.0, 0.5), ("BR", 1.0, 1.0), ("BM", 0.5, 1.0), ("BL", 0.0, 1.0)]
FRAME_WIDTH, FRAME_HEIGHT = 640, 480
FRAME_CENTER_X, FRAME_CENTER_Y = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
GAIN_X, GAIN_Y = 0.8, 0.8
CENTER_TOLERANCE = 20
model = YOLO("/home/edonthopo/Downloads/best3.pt")
CONF_THRESHOLD = 0.5
RESULTS = []

def capture_image(filename):
    try:
        subprocess.run(["rpicam-still", "-t", "500", "-o", filename, "--width", "640", "--height", "480", "-n"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=True)
        print(f"📸 Captured {filename}")
        return True
    except subprocess.CalledProcessError:
        print(f"⚠ Gagal capture: {filename}")
        return False

# ======================
# 🧭 Scan Area
# ======================
captured_images = []
for label, x_pos, y_pos in positions:
    move_servo(x_pos, y_pos); sleep(0.1)
    filename = f"scan_{label}.jpg"
    if capture_image(filename):
        captured_images.append((label, x_pos, y_pos, filename))

# ======================
# 🔍 Deteksi Api
# ======================
best_detection = None
for label, x_pos, y_pos, filename in captured_images:
    frame = cv2.imread(filename)
    results = model.predict(frame, stream=False, verbose=False)
    detected, conf, cx, cy = False, 0, None, None

    for r in results:
        for box in r.boxes:
            cls = int(box.cls[0])
            conf = float(box.conf[0])
            name = model.names[cls].lower()
            if name == "fire" and conf > CONF_THRESHOLD:
                x1, y1, x2, y2 = map(int, box.xyxy[0])
                cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
                detected = True
                break
        if detected: break

    RESULTS.append({"pos": label, "servo_x": x_pos, "servo_y": y_pos, "detected": detected, "confidence": conf, "cx": cx, "cy": cy})

    if detected:
        print(f"🔥 Fire detected in {label} (conf={conf:.2f})")
        if best_detection is None or conf > best_detection["confidence"]:
            best_detection = {"label": label, "servo_x": x_pos, "servo_y": y_pos, "confidence": conf, "cx": cx, "cy": cy}

# ======================
# 🎯 Tracking & Relay
# ======================
db_result_data = {"scan_results": RESULTS, "best_detection": best_detection, "fire_detected": False}

if best_detection:
    db_result_data["fire_detected"] = True
    print(f"\n🎯 Moving servo to {best_detection['label']}")
    buzzer_background()
    move_servo(best_detection["servo_x"], best_detection["servo_y"])
    sleep(0.3)
    print("🤖 Fine-centering...")

    while True:
        result = subprocess.run(["rpicam-still", "-t", "500", "-o", "-", "--width", "640", "--height", "480", "-n"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        image_array = np.frombuffer(result.stdout, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        frame = cv2.flip(frame, -1)
        if frame is None: break

        results = model.predict(frame, stream=False, verbose=False)
        fire_box = None
        for r in results:
            for box in r.boxes:
                cls, conf = int(box.cls[0]), float(box.conf[0])
                name = model.names[cls].lower()
                if name == "fire" and conf > CONF_THRESHOLD:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    fire_box = ((x1 + x2) // 2, (y1 + y2) // 2)
                    break
            if fire_box: break

        if not fire_box:
            print("⚠ Api hilang.")
            pi.write(RELAY_PIN, 0)
            # Kirim hasil akhir (mungkin api padam)
            send_ai_result_to_api(db_result_data, "fire_centered.jpg" if os.path.exists("fire_centered.jpg") else None)
            print("♻️ Kembali ke sensor_trigger...")
            subprocess.run(["python3", "sensor_trigger.py"])
            break

        dx, dy = FRAME_CENTER_X - fire_box[0], FRAME_CENTER_Y - fire_box[1]

        if abs(dx) < CENTER_TOLERANCE and abs(dy) < CENTER_TOLERANCE:
            print("✅ Api di tengah.")
            save_path = os.path.join(os.getcwd(), "fire_centered.jpg")
            cv2.imwrite(save_path, frame)
            
            # --- KIRIM KE API DI SINI (Saat api terkunci) ---
            send_ai_result_to_api(db_result_data, "fire_centered.jpg")
            
            pi.write(RELAY_PIN, 1)
            sleep(5)
            pi.write(RELAY_PIN, 0)
            continue

        # Servo adjustment logic (SAMA)
        adjust_x = np.clip((-dx / 640) * GAIN_X, -0.2, 0.2)
        adjust_y = np.clip((-dy / 480) * GAIN_Y, -0.2, 0.2)
        current_x = np.clip(best_detection["servo_x"] + adjust_x, 0.0, 1.0)
        current_y = np.clip(best_detection["servo_y"] + adjust_y, 0.0, 1.0)
        move_servo(current_x, current_y)
        best_detection["servo_x"], best_detection["servo_y"] = current_x, current_y
        sleep(0.5)

else:
    print("\n❌ Tidak ada api.")
    # Kirim hasil negatif (tanpa gambar)
    send_ai_result_to_api(db_result_data)

# ... (Cleanup code SAMA) ...
pi.set_servo_pulsewidth(SERVO_X_PIN, 0)
pi.set_servo_pulsewidth(SERVO_Y_PIN, 0)
pi.write(RELAY_PIN, 0)
pi.write(BUZZER_PIN, 0)
pi.stop()