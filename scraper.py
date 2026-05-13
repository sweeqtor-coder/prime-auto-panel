"""
PRIME AUTO — Enhanced Scraper
Extracts: English name, year, fuel, mileage, price, DTP status, full-size photo URLs
"""

import requests
import re
import time
import json
import logging
from datetime import datetime

log = logging.getLogger(__name__)


DELAY      = 0.6

FUEL_MAP = [
    (r'\bбензин\b',         "Бензин"),
    (r'\bдизель\b',         "Дизель"),
    (r'\bелектр',           "Електро"),
    (r'\bгібрид\b|\bphev\b',"Гібрид"),
    (r'\bгаз\b',            "Газ"),
]

# Slug → pretty English name corrections
BRAND_FIX = {
    "mercedes benz": "Mercedes-Benz",
    "land rover":    "Land Rover",
    "alfa romeo":    "Alfa Romeo",
    "aston martin":  "Aston Martin",
    "rolls royce":   "Rolls-Royce",
}
MODEL_FIX = {
    "gla class": "GLA-Class", "gle class": "GLE-Class", "glb class": "GLB-Class",
    "glc class": "GLC-Class", "gls class": "GLS-Class", "gl class":  "GL-Class",
    "s class":   "S-Class",   "e class":   "E-Class",   "c class":   "C-Class",
    "a class":   "A-Class",   "b class":   "B-Class",   "g class":   "G-Class",
    "eqs suv":   "EQS SUV",   "eqe suv":   "EQE SUV",   "eqs":       "EQS",
    "eqe":       "EQE",       "eqb":       "EQB",       "eqa":       "EQA",
    "x1":  "X1",  "x2":  "X2",  "x3":  "X3",  "x4":  "X4",
    "x5":  "X5",  "x6":  "X6",  "x7":  "X7",  "ix":  "iX",
    "i3":  "i3",  "i4":  "i4",  "i7":  "i7",  "i8":  "i8",
    "cx 5":  "CX-5",   "cx 9":  "CX-9",  "cx 30": "CX-30",
    "cx 60": "CX-60",  "ez 60": "EZ-60",
    "3 series": "3 Series", "5 series": "5 Series", "7 series": "7 Series",
    "q2": "Q2", "q3": "Q3", "q5": "Q5", "q7": "Q7", "q8": "Q8",
    "a3": "A3", "a4": "A4", "a5": "A5", "a6": "A6", "a7": "A7", "a8": "A8",
    "rx":  "RX",  "nx":  "NX",  "lx":  "LX",  "ux":  "UX",
    "xc60": "XC60", "xc90": "XC90",
    "range rover evoque": "Range Rover Evoque",
    "land cruiser prado": "Land Cruiser Prado",
    "model s": "Model S", "model 3": "Model 3", "model x": "Model X", "model y": "Model Y",
}

def _slug_to_english(url: str) -> str:
    """Convert auto.ria.com URL slug to English car name."""
    m = re.search(r'/auto_(.+)_\d+\.html', url)
    if not m:
        return ""
    slug = m.group(1)          # e.g. mercedes-benz_gla-class
    parts = slug.split("_")    # ['mercedes-benz', 'gla-class']
    brand_slug = parts[0].replace("-", " ")
    model_slug = " ".join(p.replace("-", " ") for p in parts[1:]) if len(parts) > 1 else ""

    brand = BRAND_FIX.get(brand_slug.lower(), brand_slug.title())
    model = MODEL_FIX.get(model_slug.lower(), model_slug.title())
    return f"{brand} {model}".strip()

def _detect_fuel(text: str) -> str:
    t = text.lower()
    for pat, label in FUEL_MAP:
        if re.search(pat, t, re.IGNORECASE):
            return label
    return "—"

def _clean_price(raw) -> str:
    digits = re.sub(r"\D", "", str(raw))
    return f"${int(digits):,}".replace(",", " ") if digits else "—"

def _extract_photos(html: str) -> list:
    """Extract unique full-size (fx) photo URLs from page HTML."""
    # fx = full-size, bx = big thumbnail
    all_ph = re.findall(
        r'https://cdn\d*\.riastatic\.com/photosnew/auto/photo/[^\"\'\s<>]+?(?:fx|bx)\.(?:webp|jpg)',
        html
    )
    seen = set()
    full = []
    for p in all_ph:
        # Prefer fx; convert bx → fx if fx version not already added
        fx = p.replace("bx.webp", "fx.webp").replace("bx.jpg", "fx.jpg")
        if fx not in seen:
            seen.add(fx)
            full.append(fx)
    return full[:30]  # cap at 30 photos per car

def _extract_dtp(html: str) -> str:
    if re.search(r'Був\s+(?:у|в|в)\s*ДТП', html, re.IGNORECASE):
        return "Був в ДТП"
    return "Не ДТП"

def get_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "uk-UA,uk;q=0.9,en;q=0.8",
        "Referer": "https://auto.ria.com/",
    })
    return s

def fetch_car_urls(sess: requests.Session, user_ids: list) -> list:
    """Get all car listing URLs for the sellers."""
    all_urls = []
    for uid in user_ids:
        search_url = f"https://auto.ria.com/uk/search/?category_id=1&user_id={uid}&countpage=100&page=0"
        try:
            r = sess.get(search_url, timeout=20)
            r.raise_for_status()
            urls = list(dict.fromkeys(
                re.findall(r'"(https://auto\.ria\.com/uk/auto_[^"]+\.html)"', r.text)
            ))
            all_urls.extend(urls)
            log.info(f"Found {len(urls)} car URLs for seller {uid}")
        except Exception as e:
            log.error(f"fetch_car_urls error for seller {uid}: {e}")
    return list(dict.fromkeys(all_urls))

def parse_car(sess: requests.Session, url: str) -> dict:
    """Parse a single car page. Returns full car dict."""
    car = {
        "name_en": _slug_to_english(url),
        "name_ua": "",
        "year": "—",
        "fuel": "—",
        "mileage": "—",
        "transmission": "—",
        "price": "—",
        "dtp": "—",
        "photos": [],
        "url": url,
        "active": True,
        "fetched_at": datetime.utcnow().isoformat(),
    }

    try:
        r = sess.get(url, timeout=20, allow_redirects=True)
        if r.status_code == 404:
            car["active"] = False
            return car
        html = r.text
        car["url"] = r.url

        # DTP
        car["dtp"] = _extract_dtp(html)

        # Photos
        car["photos"] = _extract_photos(html)

        # Title
        title_m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
        raw_title = title_m.group(1) if title_m else ""
        raw_title = re.sub(r"&#x2F;", "/", raw_title)
        raw_title = re.sub(r"&amp;", "&", raw_title)

        # Year
        year_m = re.search(r"\b(19[5-9]\d|20[0-3]\d)\b", raw_title)
        if year_m:
            car["year"] = year_m.group(1)

        # Fuel
        car["fuel"] = _detect_fuel(raw_title + " " + html[:4000])

        # Price from title
        price_m = re.search(r"ціна\s+([\d\s\xa0]+)\s*\$", raw_title, re.IGNORECASE)
        if price_m:
            car["price"] = _clean_price(price_m.group(1))

        # Mileage
        km_m = re.search(r"(\d{2,3})\s*тис\.\s*км", html)
        if km_m:
            car["mileage"] = f"{km_m.group(1)} тис. км"

        # Transmission
        if re.search(r"автомат|автоматич", html, re.IGNORECASE):
            car["transmission"] = "Автомат"
        elif re.search(r"механ|ручна|механічна", html, re.IGNORECASE):
            car["transmission"] = "Механіка"

        # Ukrainian name from JSON-LD
        jld_blocks = re.findall(
            r'<script[^>]+type="application/ld\+json"[^>]*>([\s\S]*?)</script>',
            html
        )
        for raw in jld_blocks:
            try:
                d = json.loads(raw.strip())
                if d.get("@type") == "Product":
                    car["name_ua"] = d.get("name", "")
                    if d.get("offers", {}).get("price"):
                        car["price"] = _clean_price(d["offers"]["price"])
                    break
            except Exception:
                continue

        # Fallback UA name from title
        if not car["name_ua"]:
            clean = re.sub(r"AUTO\.RIA\s*[–-]\s*Продам\s*", "", raw_title).strip()
            name_m = re.match(rf"^(.*?)\s+{car['year']}", clean)
            car["name_ua"] = name_m.group(1).strip() if name_m else clean[:50]

    except Exception as e:
        log.error(f"parse_car error {url}: {e}")
        car["error"] = str(e)

    return car

def run_full_scrape() -> dict:
    """Main entry point. Returns dict with all cars and metadata."""
    sess = get_session()
    urls = fetch_car_urls(sess)

    cars = []
    errors = 0
    for i, url in enumerate(urls):
        log.info(f"[{i+1}/{len(urls)}] {url}")
        car = parse_car(sess, url)
        cars.append(car)
        if car.get("error"):
            errors += 1
        time.sleep(DELAY)

    return {
        "cars": cars,
        "total": len(cars),
        "active": sum(1 for c in cars if c.get("active", True)),
        "errors": errors,
        "scraped_at": datetime.utcnow().isoformat(),
        "seller_id": USER_ID,
    }
