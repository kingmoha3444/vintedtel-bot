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


# =========================================================
# FILTRES ASSOUPLIS
# =========================================================

MIN_PRICE = 40
MAX_PRICE = 180

MIN_MARGIN = 40

MAX_ITEMS_PER_SEARCH = 60


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

def send_telegram(message, photo_url=None):

    try:

        if photo_url:

            url = (
                f"https://api.telegram.org/"
                f"bot{TOKEN}/sendPhoto"
            )

            response = requests.post(
                url,
                data={
                    "chat_id": CHAT_ID,
                    "photo": photo_url,
                    "caption": message,
                },
                timeout=20,
            )

            if response.ok:

                print(
                    "📨 Telegram : photo + message envoyé",
                    flush=True,
                )

                return

            print(
                f"⚠️ Photo Telegram refusée : "
                f"{response.text}",
                flush=True,
            )

        # Fallback texte
        url = (
            f"https://api.telegram.org/"
            f"bot{TOKEN}/sendMessage"
        )

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

        print(
            "📨 Telegram : message envoyé",
            flush=True,
        )

    except Exception as error:

        print(
            f"❌ Telegram : {error}",
            flush=True,
        )


# =========================================================
# PRIX
# =========================================================

def extract_real_price(body):

    patterns = [

        # Format habituel Vinted
        r"(\d+(?:[.,]\d{1,2})?)\s*€"
        r"\s*"
        r"(\d+(?:[.,]\d{1,2})?)\s*€"
        r"\s*"
        r"Inclut la Protection acheteurs",

        # Fallback : prix juste avant la protection
        r"(\d+(?:[.,]\d{1,2})?)\s*€"
        r"\s*"
        r"Inclut la Protection acheteurs",
    ]

    for pattern in patterns:

        match = re.search(
            pattern,
            body,
            re.IGNORECASE,
        )

        if match:

            try:

                return float(
                    match.group(1).replace(
                        ",",
                        ".",
                    )
                )

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

        if (
            "Inclut la Protection acheteurs"
            in line
        ):

            previous_lines = lines[
                max(0, i - 12):i
            ]

            for previous in reversed(
                previous_lines
            ):

                lower = previous.lower()

                if (
                    "€" not in previous
                    and
                    len(previous) >= 3
                    and
                    "très bon état" not in lower
                    and
                    "bon état" not in lower
                    and
                    "satisfaisant" not in lower
                    and
                    "apple" not in lower
                    and
                    "samsung" not in lower
                    and
                    "inclut" not in lower
                ):

                    return previous

    return ""


# =========================================================
# TITRE META
# =========================================================

async def extract_meta_title(page):

    try:

        title = await page.locator(
            'meta[property="og:title"]'
        ).get_attribute(
            "content"
        )

        if title:
            return title.strip()

    except Exception:
        pass

    return ""


# =========================================================
# MODÈLE
# =========================================================

def detect_model(title, search):

    text = title.lower()

    # On cherche d'abord les modèles exacts.
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

        if re.search(
            rf"\b{re.escape(model)}\b",
            text,
        ):

            return model

    # Fallback sur la recherche
    search_lower = search.lower()

    if search_lower in text:
        return search_lower

    return None


# =========================================================
# VARIANTES À REFUSER
# =========================================================

def is_allowed_model(title):

    text = title.lower()

    forbidden_variants = [

        # Apple
        r"\bpro\s*max\b",
        r"\bpro\b",
        r"\bmini\b",
        r"\bplus\b",

        # Samsung
        r"\bultra\b",
        r"\bfe\b",
        r"\bplus\b",

        # Autres variantes/accessoires
        r"\bclone\b",
        r"\breplique\b",
        r"\bmaquette\b",
        r"\bfactice\b",
        r"\bcoque\b",
        r"\bcase\b",
    ]

    for pattern in forbidden_variants:

        if re.search(
            pattern,
            text,
            re.IGNORECASE,
        ):

            return False

    return True


# =========================================================
# CATÉGORIE
# =========================================================

def is_phone_category(body, title):

    text = (
        body
        + "\n"
        + title
    ).lower()

    forbidden_categories = [

        "coques pour téléphones",
        "coque de téléphone",
        "coque téléphone",
        "pièces de rechange",
        "pièces détachées",
        "accessoires pour téléphones",
        "accessoire téléphone",
        "chargeur",
        "câble usb",
        "cable usb",
        "vitre de protection",
        "film de protection",
    ]

    for category in forbidden_categories:

        if category in text:

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

        "battery":
            r"État de la batterie\s*\n([^\n]+)",

        "storage":
            r"Capacité de stockage\s*\n([^\n]+)",

        "condition":
            r"État\s*\n([^\n]+)",

        "simlock":
            r"Simlockage\s*\n([^\n]+)",
    }

    for key, pattern in patterns.items():

        match = re.search(
            pattern,
            body,
            re.IGNORECASE,
        )

        if match:

            info[key] = (
                match.group(1)
                .strip()
            )

    return info


# =========================================================
# RÉPARATION
# =========================================================

def calculate_repair(body):

    text = body.lower()

    repair = 0

    damages = []

    # -----------------------------------------------------
    # ÉCRAN
    # -----------------------------------------------------

    screen_words = [

        "écran cassé",
        "ecran casse",

        "écran hs",
        "ecran hs",

        "vitre cassée",
        "vitre cassee",

        "fissuré",
        "fissurée",

        "fissure",

        "écran fissuré",
        "ecran fissure",
    ]

    if any(
        word in text
        for word in screen_words
    ):

        repair += 60

        damages.append(
            "écran"
        )

    # -----------------------------------------------------
    # BATTERIE
    # -----------------------------------------------------

    battery_words = [

        "batterie hs",
        "batterie morte",
        "batterie à changer",
        "batterie a changer",
    ]

    if any(
        word in text
        for word in battery_words
    ):

        repair += 30

        damages.append(
            "batterie"
        )

    # -----------------------------------------------------
    # FACE ID
    # -----------------------------------------------------

    faceid_words = [

        "face id hs",
        "face id ne fonctionne",
        "face id fonctionne pas",
        "faceid hs",
    ]

    if any(
        word in text
        for word in faceid_words
    ):

        repair += 70

        damages.append(
            "Face ID"
        )

    # -----------------------------------------------------
    # TACTILE
    # -----------------------------------------------------

    touch_words = [

        "tactile hs",
        "tactile ne fonctionne",
        "tactile fonctionne pas",
    ]

    if any(
        word in text
        for word in touch_words
    ):

        repair += 80

        damages.append(
            "tactile"
        )

    return repair, damages


# =========================================================
# ANALYSE
# =========================================================

def analyse(
    body,
    search,
    meta_title="",
):

    # -----------------------------------------------------
    # TITRE
    # -----------------------------------------------------

    title = extract_listing_title(
        body
    )

    if not title:
        title = meta_title

    if not title:
        return None

    # -----------------------------------------------------
    # CATÉGORIE
    # -----------------------------------------------------

    if not is_phone_category(
        body,
        title,
    ):

        return None

    # -----------------------------------------------------
    # PRIX
    # -----------------------------------------------------

    price = extract_real_price(
        body
    )

    if price is None:
        return None

    if (
        price < MIN_PRICE
        or
        price > MAX_PRICE
    ):

        return None

    # -----------------------------------------------------
    # MODÈLE
    # -----------------------------------------------------

    model = detect_model(
        title,
        search,
    )

    if model is None:
        return None

    # -----------------------------------------------------
    # VARIANTE
    # -----------------------------------------------------

    if not is_allowed_model(
        title
    ):

        return None

    # -----------------------------------------------------
    # REVENTE
    # -----------------------------------------------------

    resale = RESALE_PRICES.get(
        model
    )

    if resale is None:
        return None

    # -----------------------------------------------------
    # RÉPARATION
    # -----------------------------------------------------

    repair, damages = (
        calculate_repair(body)
    )

    # -----------------------------------------------------
    # MARGE
    # -----------------------------------------------------

    margin = (
        resale
        - price
        - repair
    )

    if margin < MIN_MARGIN:
        return None

    # -----------------------------------------------------
    # INFOS
    # -----------------------------------------------------

    info = extract_info(
        body
    )

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

async def get_item_links(
    page,
    search,
):

    url = (
        "https://www.vinted.fr/catalog"
        "?search_text="
        + quote(search)
    )

    print(
        f"🌐 Ouverture : {search}",
        flush=True,
    )

    await page.goto(
        url,
        wait_until="domcontentloaded",
        timeout=60000,
    )

    await page.wait_for_timeout(
        4000
    )

    links = await page.locator(
        'a[href*="/items/"]'
    ).all()

    urls = []

    for link in links:

        try:

            href = await link.get_attribute(
                "href"
            )

            if not href:
                continue

            if "/items/" not in href:
                continue

            if href.startswith("/"):

                href = (
                    "https://www.vinted.fr"
                    + href
                )

            if href not in urls:

                urls.append(
                    href
                )

        except Exception:
            pass

    return urls


# =========================================================
# ANALYSE ANNONCE
# =========================================================

async def analyse_item(
    page,
    url,
    search,
):

    try:

        await page.goto(
            url,
            wait_until="domcontentloaded",
            timeout=30000,
        )

        await page.wait_for_timeout(
            1200
        )

        body = await page.locator(
            "body"
        ).inner_text()

        # Titre supplémentaire
        meta_title = (
            await extract_meta_title(
                page
            )
        )

        result = analyse(
            body,
            search,
            meta_title,
        )

        if result is None:
            return None

        # =================================================
        # PHOTO
        # =================================================

        photo_url = None

        try:

            photo_url = await page.locator(
                'meta[property="og:image"]'
            ).get_attribute(
                "content"
            )

        except Exception:
            photo_url = None

        if not photo_url:

            try:

                image = page.locator(
                    "img"
                ).first

                photo_url = (
                    await image.get_attribute(
                        "src"
                    )
                )

            except Exception:
                photo_url = None

        result["photo_url"] = (
            photo_url
        )

        result["url"] = url

        return result

    except Exception as error:

        print(
            f"⚠️ Annonce ignorée : "
            f"{error}",
            flush=True,
        )

        return None


# =========================================================
# MAIN
# =========================================================

async def main():

    print(
        "🚀 LE BOT DEMARRE",
        flush=True,
    )

    print(
        "==============================",
        flush=True,
    )

    print(
        "    VINTED PHONE DEAL BOT V2",
        flush=True,
    )

    print(
        "==============================",
        flush=True,
    )

    print(
        f"💰 Prix : "
        f"{MIN_PRICE} - {MAX_PRICE} €",
        flush=True,
    )

    print(
        f"📈 Marge minimum : "
        f"{MIN_MARGIN} €",
        flush=True,
    )

    print(
        f"🔍 Annonces/recherche : "
        f"{MAX_ITEMS_PER_SEARCH}",
        flush=True,
    )

    print(
        "==============================",
        flush=True,
    )

    async with async_playwright() as p:

        print(
            "🌐 Lancement Chromium...",
            flush=True,
        )

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

        print(
            "✅ Chromium lancé",
            flush=True,
        )

        page = await browser.new_page(
            locale="fr-FR"
        )

        already_sent = set()

        # =================================================
        # RECHERCHES
        # =================================================

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
                    f"❌ Erreur recherche : "
                    f"{error}",
                    flush=True,
                )

                continue

            print(
                f"📦 {len(urls)} annonces trouvées",
                flush=True,
            )

            urls = urls[
                :MAX_ITEMS_PER_SEARCH
            ]

            print(
                f"🔍 Analyse de "
                f"{len(urls)} annonces...",
                flush=True,
            )

            # =================================================
            # ANNONCES
            # =================================================

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

                # =================================================
                # MESSAGE
                # =================================================

                message = (
                    "🚨 BON PLAN VINTED\n\n"
                    f"📱 "
                    f"{result['model'].upper()}\n"
                    f"📝 "
                    f"{result['title']}\n\n"
                    f"💰 Achat : "
                    f"{result['price']:.0f} €\n"
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

                # =================================================
                # TELEGRAM
                # =================================================

                send_telegram(
                    message,
                    result.get(
                        "photo_url"
                    ),
                )

                already_sent.add(
                    url
                )

                print(
                    f"🚨 OFFRE ENVOYÉE ! "
                    f"({number}/{len(urls)})",
                    flush=True,
                )

        await browser.close()

    print(
        "✅ Recherche terminée.",
        flush=True,
    )


# =========================================================
# LANCEMENT
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
