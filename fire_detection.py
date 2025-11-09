import cv2
import json
import subprocess
from ultralytics import YOLO
from time import sleep
import numpy as np
import os
import pigpio
import threading

# ======================
# 🔧 Setup (Servo, Relay, Buzzer)
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

# ======================
# ⚙️ Servo Range
# ======================
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

# ======================
# 🔔 Buzzer
# ======================
def buzzer_alert():
    print("🔔 Buzzer aktif (beep dua kali)")
    for _ in range(15):
        pi.write(BUZZER_PIN, 1)
        sleep(0.2)
        pi.write(BUZZER_PIN, 0)
        sleep(0.2)

def buzzer_background():
    thread = threading.Thread(target=buzzer_alert)
    thread.daemon = True
    thread.start()

# ======================
# 📍 Titik Scan
# ======================
positions = [
    ("TL", 0.0, 0.0), ("TM", 0.5, 0.0), ("TR", 1.0, 0.0),
    ("MR", 1.0, 0.5), ("MM", 0.5, 0.5), ("ML", 0.0, 0.5),
    ("BR", 1.0, 1.0), ("BM", 0.5, 1.0), ("BL", 0.0, 1.0)
]

# ======================
# 🎯 Frame Setup
# ======================
FRAME_WIDTH, FRAME_HEIGHT = 640, 480
FRAME_CENTER_X, FRAME_CENTER_Y = FRAME_WIDTH // 2, FRAME_HEIGHT // 2
GAIN_X, GAIN_Y = 0.8, 0.8
CENTER_TOLERANCE = 20

# ======================
# 🤖 YOLO Model
# ======================
model = YOLO("/home/edonthopo/Downloads/best3.pt")
CONF_THRESHOLD = 0.5
RESULTS = []

# ======================
# 📸 Capture Function
# ======================
def capture_image(filename):
    try:
        subprocess.run(
            ["rpicam-still", "-t", "500", "-o", filename, "--width", "640", "--height", "480", "-n"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=True
        )
        print(f"📸 Captured {filename}")
        return True
    except subprocess.CalledProcessError:
        print(f"⚠ Gagal mengambil gambar: {filename}")
        return False

# ======================
# 🧭 Scan Area
# ======================
captured_images = []
for label, x_pos, y_pos in positions:
    move_servo(x_pos, y_pos)
    sleep(0.1)
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
                cx = (x1 + x2) // 2
                cy = (y1 + y2) // 2
                detected = True
                break
        if detected: break

    RESULTS.append({
        "pos": label,
        "servo_x": x_pos,
        "servo_y": y_pos,
        "detected": detected,
        "confidence": conf,
        "cx": cx,
        "cy": cy
    })

    if detected:
        print(f"🔥 Fire detected in {label} (conf={conf:.2f})")
        if best_detection is None or conf > best_detection["confidence"]:
            best_detection = {"label": label, "servo_x": x_pos, "servo_y": y_pos,
                              "confidence": conf, "cx": cx, "cy": cy}

# ======================
# 🎯 Tracking & Relay
# ======================
if best_detection:
    print(f"\n🎯 Moving servo to {best_detection['label']} (best fire spot)")
    buzzer_background()  # 🔔 jalankan buzzer tanpa menunggu
    move_servo(best_detection["servo_x"], best_detection["servo_y"])
    sleep(0.3)
    print("🤖 Fine-centering fire object...")

    while True:
        result = subprocess.run(
            ["rpicam-still", "-t", "500", "-o", "-", "--width", "640", "--height", "480", "-n"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL
        )
        image_array = np.frombuffer(result.stdout, dtype=np.uint8)
        frame = cv2.imdecode(image_array, cv2.IMREAD_COLOR)
        frame = cv2.flip(frame, -1)
        if frame is None: break

        results = model.predict(frame, stream=False, verbose=False)
        fire_box = None
        for r in results:
            for box in r.boxes:
                cls = int(box.cls[0])
                conf = float(box.conf[0])
                name = model.names[cls].lower()
                if name == "fire" and conf > CONF_THRESHOLD:
                    x1, y1, x2, y2 = map(int, box.xyxy[0])
                    cx = (x1 + x2) // 2
                    cy = (y1 + y2) // 2
                    fire_box = (cx, cy)
                    break
            if fire_box: break

        if not fire_box:
            print("⚠ Api hilang, berhenti tracking.")
            pi.write(RELAY_PIN, 0)
            # 🔁 Jalankan kembali sensor suhu & gas
            print("♻️ Mengalihkan ke sensor_trigger.py...")
            subprocess.run(["python3", "sensor_trigger.py"])
            break

        dx = FRAME_CENTER_X - fire_box[0]
        dy = FRAME_CENTER_Y - fire_box[1]

        if abs(dx) < CENTER_TOLERANCE and abs(dy) < CENTER_TOLERANCE:
            print("✅ Api sudah di tengah frame.")
            save_path = os.path.join(os.getcwd(), "fire_centered.jpg")
            cv2.imwrite(save_path, frame)
            print(f"📸 Gambar tersimpan: {save_path}")
            pi.write(RELAY_PIN, 1)
            sleep(5)
            pi.write(RELAY_PIN, 0)
            continue

        adjust_x = np.clip((-dx / 640) * GAIN_X, -0.2, 0.2)
        adjust_y = np.clip((-dy / 480) * GAIN_Y, -0.2, 0.2)
        current_x = np.clip(best_detection["servo_x"] + adjust_x, 0.0, 1.0)
        current_y = np.clip(best_detection["servo_y"] + adjust_y, 0.0, 1.0)
        move_servo(current_x, current_y)
        best_detection["servo_x"] = current_x
        best_detection["servo_y"] = current_y
        sleep(0.5)

else:
    print("\n❌ Tidak ada api terdeteksi di semua gambar.")

# ======================
# 💾 Save Result ke JSON
# ======================
with open("fire_servo_results.json", "w") as f:
    json.dump(RESULTS, f, indent=4)
print("\n✅ Done. Results saved in fire_servo_results.json")

# ======================
# 🧹 Cleanup
# ======================
pi.set_servo_pulsewidth(SERVO_X_PIN, 0)
pi.set_servo_pulsewidth(SERVO_Y_PIN, 0)
pi.write(RELAY_PIN, 0)
pi.write(BUZZER_PIN, 0)
pi.stop()
