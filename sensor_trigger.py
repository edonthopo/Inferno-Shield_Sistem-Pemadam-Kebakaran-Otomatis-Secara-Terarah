import time
import spidev
import adafruit_dht
import board
import RPi.GPIO as GPIO
import json
import mysql.connector
import subprocess

# ======================
# 🔧 Konfigurasi Pin
# ======================
DHT_PIN = board.D4  # GPIO4 untuk DHT22
dht_sensor = adafruit_dht.DHT22(DHT_PIN, use_pulseio=False)

# ======================
# ⚙️ Inisialisasi SPI MCP3008
# ======================
spi = spidev.SpiDev()
spi.open(0, 0)  # bus 0, device 0
spi.max_speed_hz = 1350000

# ======================
# 🧩 GPIO Setup
# ======================
GPIO.setmode(GPIO.BCM)

# ======================
# 📈 Fungsi Baca MCP3008
# ======================
def read_adc(channel):
    """Membaca nilai ADC dari channel MCP3008 (0–7)."""
    if channel < 0 or channel > 7:
        return -1
    adc = spi.xfer2([1, (8 + channel) << 4, 0])
    value = ((adc[1] & 3) << 8) + adc[2]
    return value

def adc_to_voltage(adc_value):
    """Konversi nilai ADC ke tegangan (3.3V referensi)."""
    return (adc_value * 3.3) / 1023.0

def voltage_to_level(voltage):
    """Konversi tegangan MQ-2 ke estimasi level sederhana."""
    return round((voltage / 3.3) * 1000, 1)

# ======================
# 💾 Database Setup (Hanya Koneksi)
# ======================
db_config = {
    'host': '103.250.11.139',
    'user': 'shieldweb_remote',
    'password': 'P@ssw0rd',
    'database': 'shieldweb'
}

try:
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    print("✅ Terhubung ke database MySQL.")
except mysql.connector.Error as err:
    print(f"❌ Gagal terhubung ke MySQL: {err}")
    exit()

# (Bagian CREATE TABLE telah dihapus karena tabel sudah ada)

# ======================
# ⏱️ Timer untuk kontrol AI
# ======================
last_ai_run = 0               
AI_COOLDOWN = 60              
last_periodic_run = time.time()
PERIODIC_INTERVAL = 600       

# ======================
# 🔄 Loop Monitoring
# ======================
try:
    print("🚀 Memulai pemantauan sensor...")
    while True:
        # --- Baca MQ-2 ---
        gas_value = read_adc(0)
        gas_voltage = adc_to_voltage(gas_value)
        gas_level = voltage_to_level(gas_voltage)

        # --- Baca DHT22 ---
        try:
            temperature = dht_sensor.temperature
            # humidity = dht_sensor.humidity
        except RuntimeError as e:
            print(f"⚠️ Gagal membaca DHT22: {e}")
            time.sleep(2)
            continue

        if temperature is None:
            print("⚠️ DHT22 tidak terbaca, mencoba ulang...")
            time.sleep(2)
            continue

        # --- Simpan ke DB (tabel sensor_readings) ---
        try:
            # Cek koneksi sebelum insert, reconnect jika putus
            if not conn.is_connected():
                print("⚠️ Koneksi terputus, mencoba menghubungkan ulang...")
                conn.reconnect(attempts=3, delay=2)
            
            cursor.execute(
                "INSERT INTO sensor_readings (temperature, gas_level) VALUES (%s, %s)",
                (temperature, gas_level)
            )
            conn.commit()
            print(f"✅ Data Tersimpan: Temp={temperature:.1f}, Gas={gas_level:.1f}")

        except mysql.connector.Error as err:
            print(f"❌ Gagal menyimpan ke database: {err}")

        # --- Simpan ke JSON Lokal (Opsional/Backup) ---
        data = {
            "temperature": temperature,
            "gas_level": gas_level,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
        }
        try:
            with open("sensor_data.json", "w") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            print(f"⚠️ Gagal tulis JSON: {e}")

        # --- Log Tampilan ---
        print(f"🔥 MQ-2: {gas_level:.1f} | 🌡 Suhu: {temperature:.1f}°C")

        now = time.time()

        # --- Cek Ambang Batas (kondisi kritis) ---
        if gas_level > 50 or temperature > 35:
            if now - last_ai_run > AI_COOLDOWN:
                print("🚨 Kondisi kritis! Menjalankan deteksi AI...")
                try:
                    subprocess.run(["python3", "fire_detection.py"])
                    last_ai_run = now
                    last_periodic_run = now  
                except Exception as e:
                    print(f"❌ Gagal menjalankan script AI: {e}")
            else:
                remaining = int(AI_COOLDOWN - (now - last_ai_run))
                print(f"⏳ AI sedang cooldown ({remaining}s lagi).")

        # --- Jalankan fire_detection.py setiap 10 menit ---
        elif now - last_periodic_run > PERIODIC_INTERVAL:
            print("🕒 Menjalankan deteksi AI otomatis (setiap 10 menit)...")
            try:
                subprocess.run(["python3", "fire_detection.py"])
                last_periodic_run = now
                last_ai_run = now
            except Exception as e:
                print(f"❌ Gagal menjalankan script AI periodik: {e}")

        else:
            print("✅ Kondisi aman.")

        time.sleep(2)

except KeyboardInterrupt:
    print("\n⏹ Dihentikan oleh pengguna.")

finally:
    spi.close()
    if 'conn' in locals() and conn.is_connected():
        cursor.close()
        conn.close()
    GPIO.cleanup()
    print("GPIO & SPI ditutup dengan aman.")