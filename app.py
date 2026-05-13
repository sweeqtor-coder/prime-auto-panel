"""
PRIME AUTO — Flask Web Server
Serves the control panel and runs the scraper via API.
"""

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

from flask import Flask, jsonify, send_from_directory, request
from scraper import fetch_car_urls, parse_car, get_session, DELAY

app = Flask(__name__, static_folder="static")

DATA_FILE = Path("data/catalog.json")
DATA_FILE.parent.mkdir(exist_ok=True)

# In-memory scrape state
scrape_state = {
    "running": False,
    "progress": 0,
    "total": 0,
    "log": [],
    "result": None,
    "started_at": None,
}

def _load_cache():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return None

def _save_cache(data):
    DATA_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def _scrape_thread(user_ids):
    global scrape_state
    scrape_state["log"] = []
    scrape_state["progress"] = 0
    scrape_state["started_at"] = datetime.utcnow().isoformat()

    def log(msg, level="info"):
        scrape_state["log"].append({"msg": msg, "level": level, "t": datetime.utcnow().isoformat()})

    try:
        log(f"🔍 Збираємо посилання зі сторінки пошуку ({len(user_ids)} продавців)...")
        sess = get_session()
        urls = fetch_car_urls(sess, user_ids)

        if not urls:
            log("❌ Посилання не знайдено", "error")
            scrape_state["running"] = False
            return

        scrape_state["total"] = len(urls)
        log(f"✓ Знайдено {len(urls)} оголошень", "ok")

        cars = []
        for i, url in enumerate(urls):
            slug = url.split("/uk/")[1].replace(".html", "")
            log(f"[{i+1}/{len(urls)}] {slug}")
            car = parse_car(sess, url)
            cars.append(car)
            scrape_state["progress"] = round((i + 1) / len(urls) * 100)
            time.sleep(DELAY)

        result = {
            "cars": cars,
            "total": len(cars),
            "active": sum(1 for c in cars if c.get("active", True)),
            "errors": sum(1 for c in cars if c.get("error")),
            "scraped_at": datetime.utcnow().isoformat(),
        }
        _save_cache(result)
        scrape_state["result"] = result
        log(f"✅ Готово! {len(cars)} авто оброблено", "ok")

    except Exception as e:
        scrape_state["log"].append({"msg": f"❌ Fatal: {e}", "level": "error",
                                    "t": datetime.utcnow().isoformat()})
    finally:
        scrape_state["running"] = False
        scrape_state["progress"] = 100

# ─── Routes ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return send_from_directory("static", "index.html")

@app.route("/api/scrape", methods=["POST"])
def api_scrape():
    if scrape_state["running"]:
        return jsonify({"error": "Scrape already running"}), 409
    
    data = request.json or {}
    user_ids = data.get("user_ids", ["1640523"])
    
    scrape_state["running"] = True
    threading.Thread(target=_scrape_thread, args=(user_ids,), daemon=True).start()
    return jsonify({"status": "started", "user_ids": user_ids})

@app.route("/api/status")
def api_status():
    return jsonify({
        "running": scrape_state["running"],
        "progress": scrape_state["progress"],
        "total": scrape_state["total"],
        "log": scrape_state["log"][-50:],  # last 50 lines
        "has_result": scrape_state["result"] is not None or _load_cache() is not None,
        "started_at": scrape_state.get("started_at"),
    })

@app.route("/api/catalog")
def api_catalog():
    data = scrape_state.get("result") or _load_cache()
    if not data:
        return jsonify({"error": "No data yet. Run scrape first."}), 404
    return jsonify(data)

@app.route("/api/export/json")
def api_export_json():
    data = scrape_state.get("result") or _load_cache()
    if not data:
        return jsonify({"error": "No data"}), 404
    return jsonify(data)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
