import asyncio
import os
import re
from urllib.parse import quote

import requests
from playwright.async_api import async_playwright


# =========================================================
# CONFIG
# =========================================================

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

SEARCHES = [
    "iPhone 11",
    "iPhone 12",
    "iPhone 13",
    "iPhone 14",
    "iPhone 15",
    "iPhone 16",
    "Samsung S21",
    "Samsung S22",
    "Samsung S23",
]

MIN_PRICE = 40
MAX_PRICE = 150
MIN_MARGIN = 50
MAX_ITEMS_PER_SEARCH = 40


# =========================================================
# REVENTE ESTIMÉE
# =========================================================

RESALE_PRICES = {
    "iphone 11": 170,
    "iphone 12": 210,
    "iphone 13": 270,
    "iphone 14": 340,
    "iphone 15": 420,
    "iphone 16": 500,
    "samsung s21": 190,
    "samsung s22": 240,
    "samsung s23": 300,
}


# =========================================================
# TELEGRAM
# =========================================================

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    try:
        response = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": message,
                "disable_web_page_preview": False,
            },
            timeout=20,
        )

        response.raise_for_status()
        print("📨 Telegram : message envoyé", flush=True)

    except Exception as error:
        print(f"❌ Telegram : {error}", flush=True)


# =========================================================
# PRIX
# =========================================================

def extract_real_price(body):
    pattern = (
        r"(\d+(?:[.,]\d{1,2})?)\s*€"
        r"\s*"
        r"(\d+(?:[.,]\d{1,2})?)\s*€"
        r"\s*"
        r"Inclut la Protection acheteurs"
    )

    match = re.search(pattern, body, re.IGNORECASE)

    if match:
        try:
            return float(match.group(1).replace(",", "."))
        except Exception:
            pass

    return None


# =========================================================
# TITRE
# =========================================================

def extract_listing_title(body):
    lines = [
        line.strip()
        for line in body.splitlines()
        if line.strip()
    ]

    for i, line in enumerate(lines):
        if "Inclut la Protection acheteurs" in line:
            for previous in reversed(lines[max(0, i - 8):i]):
                lower = previous.lower()

                if (
                    "€" not in previous
                    and "très bon état" not in lower
                    and "bon état" not in lower
                    and "satisfaisant" not in lower
                    and "apple" not in lower
                    and "samsung" not in lower
                ):
                    return previous

    return ""


# =========================================================
# MODÈLE
# =========================================================

def detect_model(title, search):
    text = title.lower()
    search_lower = search.lower()

    if search_lower in text:
        return search_lower

    models = [
        "iphone 16",
        "iphone 15",
        "iphone 14",
        "iphone 13",
        "iphone 12",
        "iphone 11",
        "samsung s23",
        "samsung s22",
        "samsung s21",
    ]

    for model in models:
        if model in text:
            return model

    return None


# =========================================================
# FILTRE MODÈLE
# =========================================================

def is_allowed_model(title):
    text = title.lower()

    if re.search(r"\bpro\s+max\b", text):
        return False

    if re.search(r"\bpro\b", text):
        return False

    if re.search(r"\bmini\b", text):
        return False

    return True


# =========================================================
# CATÉGORIE
# =========================================================

def is_phone_category(body):
    text = body.lower()

    forbidden_categories = [
        "coques pour téléphones",
        "pièces de rechange",
        "accessoires pour téléphones",
    ]

    for category in forbidden_categories:
        if category in text:
            return False

    if "téléphones portables" not in text:
        return False

    return True


# =========================================================
# INFOS
# =========================================================

def extract_info(body):
    info = {
        "battery": None,
        "storage": None,
        "condition": None,
        "simlock": None,
    }

    patterns = {
        "battery": r"État de la batterie\s*\n([^\n]+)",
        "storage": r"Capacité de stockage\s*\n([^\n]+)",
        "condition": r"État\s*\n([^\n]+)",
        "simlock": r"Simlockage\s*\n([^\n]+)",
    }

    for key, pattern in patterns.items():
        match = re.search(
            pattern,
            body,
            re.IGNORECASE,
        )

        if match:
            info[key] = match.group(1).strip()

    return info


# =========================================================
# RÉPARATION
# =========================================================

def calculate_repair(body):
    text = body.lower()

    repair = 0
    damages = []

    if any(
        word in text
        for word in [
            "écran cassé",
            "ecran casse",
            "écran hs",
            "ecran hs",
            "vitre cassée",
            "vitre cassee",
            "fissuré",
            "fissurée",
            "fissure",
        ]
    ):
        repair += 60
        damages.append("écran")

    if any(
        word in text
        for word in [
            "batterie hs",
            "batterie morte",
        ]
    ):
        repair += 30
        damages.append("batterie")

    if any(
        word in text
        for word in [
            "face id hs",
            "face id ne fonctionne",
            "face id fonctionne pas",
        ]
    ):
        repair += 70
        damages.append("Face ID")

    return repair, damages


# =========================================================
# ANALYSE
# =========================================================

def analyse(body, search):
    if not is_phone_category(body):
        return None

    price = extract_real_price(body)

    if price is None:
        return None

    if price < MIN_PRICE or price > MAX_PRICE:
        return None

    title = extract_listing_title(body)

    if not title:
        return None

    model = detect_model(title, search)

    if model is None:
        return None

    if not is_allowed_model(title):
        return None

    resale = RESALE_PRICES.get(model)

    if resale is None:
        return None

    repair, damages = calculate_repair(body)

    margin = resale - price - repair

    if margin < MIN_MARGIN:
        return None

    info = extract_info(body)

    return {
        "title": title,
        "model": model,
        "price": price,
        "resale": resale,
        "repair": repair,
        "margin": margin,
        "damages": damages,
        **info,
    }


# =========================================================
# RECHERCHE VINTED
# =========================================================

async def get_item_links(page, search):
    url = (
        "https://www.vinted.fr/catalog?search_text="
        + quote(search)
    )

    print(f"🌐 Ouverture : {search}", flush=True)

    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(4000)

    links = await page.locator(
        'a[href*="/items/"]'
    ).all()

    urls = []

    for link in links:
        try:
            href = await link.get_attribute("href")

            if not href:
                continue

            if "/items/" not in href:
                continue

            if href.startswith("/"):
                href = "https://www.vinted.fr" + href

            if href not in urls:
                urls.append(href)

        except Exception:
            pass

    return urls


# =========================================================
# ANALYSE ANNONCE
# =========================================================

async def analyse_item(page, url, search):
    try:
        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        await page.wait_for_timeout(1200)

        body = await page.locator("body").inner_text()

        result = analyse(body, search)

        if result is None:
            return None

        result["url"] = url

        return result

    except Exception as error:
        print(
            f"⚠️ Annonce ignorée : {error}",
            flush=True,
        )

        return None


# =========================================================
# MAIN
# =========================================================

async def main():
    print("🚀 LE BOT DEMARRE", flush=True)
    print("==============================", flush=True)
    print("    VINTED PHONE DEAL BOT", flush=True)
    print("==============================", flush=True)
    print(
        f"💰 Prix : {MIN_PRICE} - {MAX_PRICE} €",
        flush=True,
    )
    print(
        f"📈 Marge minimum : {MIN_MARGIN} €",
        flush=True,
    )

    print("1️⃣ PLAYWRIGHT", flush=True)

    async with async_playwright() as p:
        print("2️⃣ CHROMIUM", flush=True)

        browser = await p.chromium.launch(
            headless=True,
            executable_path="/usr/bin/chromium",
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-gpu",
            ],
            timeout=30000,
        )

        print("3️⃣ CHROMIUM LANCÉ", flush=True)

        page = await browser.new_page(
            locale="fr-FR"
        )

        already_sent = set()

        for search in SEARCHES:
            print(
                f"🔎 Recherche : {search}",
                flush=True,
            )

            try:
                urls = await get_item_links(
                    page,
                    search,
                )

            except Exception as error:
                print(
                    f"❌ Erreur recherche : {error}",
                    flush=True,
                )
                continue

            print(
                f"📦 {len(urls)} annonces trouvées",
                flush=True,
            )

            urls = urls[:MAX_ITEMS_PER_SEARCH]

            print(
                f"🔍 Analyse de {len(urls)} annonces...",
                flush=True,
            )

            for number, url in enumerate(
                urls,
                start=1,
            ):
                if url in already_sent:
                    continue

                result = await analyse_item(
                    page,
                    url,
                    search,
                )

                if result is None:
                    continue

                message = (
                    "🚨 BON PLAN VINTED\n\n"
                    f"📱 {result['model'].upper()}\n"
                    f"📝 {result['title']}\n\n"
                    f"💰 Achat : {result['price']:.0f} €\n"
                    f"📈 Revente estimée : "
                    f"{result['resale']:.0f} €\n"
                    f"🔧 Réparation : "
                    f"{result['repair']:.0f} €\n"
                    f"💵 Marge estimée : "
                    f"+{result['margin']:.0f} €\n"
                )

                if result["battery"]:
                    message += (
                        f"🔋 Batterie : "
                        f"{result['battery']}\n"
                    )

                if result["storage"]:
                    message += (
                        f"💾 Stockage : "
                        f"{result['storage']}\n"
                    )

                if result["condition"]:
                    message += (
                        f"✨ État : "
                        f"{result['condition']}\n"
                    )

                if result["simlock"]:
                    message += (
                        f"📶 Simlockage : "
                        f"{result['simlock']}\n"
                    )

                if result["damages"]:
                    message += (
                        "🛠️ Défauts : "
                        + ", ".join(
                            result["damages"]
                        )
                        + "\n"
                    )

                message += (
                    f"\n🔗 {result['url']}"
                )

                send_telegram(message)

                already_sent.add(url)

                print(
                    f"🚨 OFFRE ENVOYÉE ! "
                    f"({number}/{len(urls)})",
                    flush=True,
                )

        await browser.close()

    print("✅ Recherche terminée.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
