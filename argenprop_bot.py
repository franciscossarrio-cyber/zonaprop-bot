#!/usr/bin/env python3
"""
Bot de alertas Argenprop -> Telegram

Mismo patrón que zonaprop_bot.py: chequea un listado, detecta avisos nuevos
y manda un Telegram por cada uno. Usa el MISMO bot/chat de Telegram (mismas
variables de entorno) para que te lleguen todos los avisos al mismo chat.

Uso:
    python argenprop_bot.py --seed     # primera corrida: guarda lo actual SIN avisar
    python argenprop_bot.py            # corridas siguientes: avisa solo lo nuevo
    python argenprop_bot.py --dump     # guarda el HTML crudo para ajustar selectores

Variables de entorno requeridas (las mismas que ya tenés cargadas):
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID

NOTA: los selectores de abajo (parse_cards) son un primer intento. Argenprop
no me dejó leer su HTML desde acá (robots.txt), así que hay que correr
--dump una vez y ajustar los selectores con el HTML real. Ver README.
"""

import argparse
import html
import json
import os
import random
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
except ImportError:
    sync_playwright = None

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

BASE = "https://www.argenprop.com"

# Pegá acá tu URL de búsqueda tal cual la copiaste del navegador (con el
# ?orden-masnuevos para que lo nuevo aparezca primero).
SEARCH_URL = (
    "https://www.argenprop.com/departamentos/alquiler/"
    "boedo-o-caballito-o-flores-norte-o-parque-centenario-o-parque-chacabuco/"
    "2-dormitorios?orden-masnuevos"
)

PAGES = 1  # ver nota: paginación con start=N no está confirmada todavía, ver README

STATE_FILE = Path(os.getenv("AP_STATE_FILE", "vistos_argenprop.json"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",  # sin 'br': ya nos mordió en Zonaprop
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Mismos filtros que en Zonaprop
MAX_EXPENSAS = None
MAX_TOTAL = 2_000_000        # alquiler + expensas
EXCLUIR_PALABRAS = ["uso comercial", "temporal", "amoblado"]

# --------------------------------------------------------------------------
# SCRAPING
# --------------------------------------------------------------------------

# Los avisos de Argenprop terminan en "--<numero>" (sin .html). Ej:
# /departamento-en-alquiler-en-boedo-3-ambientes--20213389
# Se usa solo como fallback si el selector principal no encuentra nada.
POSTING_RE = re.compile(r'href="(/[a-z0-9\-]+--(\d+))"', re.IGNORECASE)

# Confirmado con el HTML real: cada aviso es un <a class="card" data-item-card="ID"
# montooperacion="PRECIO" ...> que envuelve toda la card (fotos, precio, dirección).
CARD_SELECTOR = 'a[data-item-card]'


POR_PAGINA = 20  # confirmado: data-avisos-count="20" en el HTML real


def page_url(n: int) -> str:
    """Argenprop pagina por offset: ?start=0, ?start=20, ?start=40..."""
    if n == 1:
        offset_param = "start=0"
    else:
        offset_param = f"start={(n - 1) * POR_PAGINA}"

    parts = urlsplit(SEARCH_URL)
    query = f"{parts.query}&{offset_param}" if parts.query else offset_param
    return urlunsplit((parts.scheme, parts.netloc, parts.path, query, parts.fragment))


COOKIES_FILE = Path(os.getenv("AP_COOKIES_FILE", "argenprop_cookies.json"))


def fetch(url: str, session: requests.Session) -> str:
    """
    Argenprop está detrás de AWS WAF con un desafío de JavaScript: la
    primera respuesta es una página vacía con un script que pide un token
    y, si lo consigue, recarga sola. requests no ejecuta JS, así que no
    hay forma de resolver esto con headers ni con un User-Agent más
    convincente -- hace falta un navegador real.

    Usamos Playwright en modo headless: carga la página, espera a que
    aparezca contenido real (un aviso con data-item-card) o, si no,
    espera un poco más por si la recarga automática tarda. Las cookies
    (con el token de WAF ya resuelto) se guardan en disco y se reusan en
    la próxima corrida, para no parecer un visitante nuevo cada vez.
    """
    if sync_playwright is None:
        raise RuntimeError(
            "Falta Playwright. Instalalo con:\n"
            "  python -m pip install playwright\n"
            "  python -m playwright install chromium"
        )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled"],
        )
        context_kwargs = {
            "user_agent": HEADERS["User-Agent"],
            "locale": "es-AR",
            "viewport": {"width": 1366, "height": 768},
        }
        if COOKIES_FILE.exists():
            context_kwargs["storage_state"] = str(COOKIES_FILE)

        context = browser.new_context(**context_kwargs)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )
        page = context.new_page()
        page.goto(url, timeout=45000, wait_until="domcontentloaded")

        try:
            page.wait_for_selector('[data-item-card]', timeout=25000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(8000)

        body = page.content()

        # Guardamos las cookies para la próxima corrida, tenga o no éxito
        # esta (si el desafío se resolvió, ya sirve para la próxima vez).
        try:
            context.storage_state(path=str(COOKIES_FILE))
        except Exception:
            pass

        browser.close()
        return body


def _txt(node, default=""):
    return node.get_text(" ", strip=True) if node else default


def _money(s: str):
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else None


def parse_cards(html_text: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.select(CARD_SELECTOR)

    out = []
    seen_ids = set()

    for card in cards:
        pid = card.get("data-item-card")
        href = card.get("href")
        if not pid or not href:
            continue
        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        precio_num = None
        monto_attr = card.get("montooperacion")
        if monto_attr and monto_attr.isdigit():
            precio_num = int(monto_attr)

        expensas_num = None
        exp_span = card.select_one(".card__expenses")
        if exp_span:
            # el atributo title trae el monto limpio: "$180.000 expensas"
            expensas_num = _money(exp_span.get("title") or _txt(exp_span))

        direccion = _txt(card.select_one(".card__address"))
        titulo = _txt(card.select_one("h2.card__title")) or _txt(
            card.select_one(".card__title--primary")
        )
        dormitorios = card.get("dormitorios")

        out.append(
            {
                "id": pid,
                "url": BASE + href if href.startswith("/") else href,
                "titulo": titulo[:140],
                "precio_num": precio_num,
                "expensas_num": expensas_num,
                "direccion": direccion,
                "dormitorios": dormitorios,
                "visto": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    # Fallback: el selector principal no encontró nada (Argenprop cambió el
    # DOM) -> al menos rescatamos los links de aviso desde el HTML crudo.
    if not out:
        for m in POSTING_RE.finditer(html_text):
            href, pid = m.group(1), m.group(2)
            if pid in seen_ids:
                continue
            seen_ids.add(pid)
            out.append(
                {
                    "id": pid,
                    "url": BASE + href,
                    "titulo": "(sin parsear - ajustar CARD_SELECTOR)",
                    "precio_num": None,
                    "expensas_num": None,
                    "direccion": "",
                    "dormitorios": None,
                    "visto": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            )

    return out


def pasa_filtros(p: dict) -> bool:
    titulo = (p.get("titulo") or "").lower()
    if any(w in titulo for w in EXCLUIR_PALABRAS):
        return False
    if MAX_EXPENSAS and p.get("expensas_num") and p["expensas_num"] > MAX_EXPENSAS:
        return False
    if MAX_TOTAL and p.get("precio_num"):
        total = p["precio_num"] + (p.get("expensas_num") or 0)
        if total > MAX_TOTAL:
            return False
    return True


# --------------------------------------------------------------------------
# TELEGRAM
# --------------------------------------------------------------------------


def telegram_send(text: str) -> None:
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("[!] Falta TELEGRAM_BOT_TOKEN o TELEGRAM_CHAT_ID; imprimo por consola:\n")
        print(text, "\n")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": False,
        },
        timeout=20,
    )
    if not r.ok:
        print(f"[!] Telegram error {r.status_code}: {r.text}")


def format_msg(p: dict) -> str:
    e = html.escape
    partes = [f"🟢 <b>{e(p['titulo'] or 'Nueva publicación (Argenprop)')}</b>"]
    if p.get("precio_num"):
        total = p["precio_num"] + (p.get("expensas_num") or 0)
        linea = f"$ {p['precio_num']:,.0f}".replace(",", ".")
        if p.get("expensas_num"):
            linea += f"  +  $ {p['expensas_num']:,.0f}".replace(",", ".") + " expensas"
        linea += f"\n💵 Total aprox: ${total:,.0f}".replace(",", ".")
        partes.append(linea)
    if p.get("dormitorios"):
        partes.append(f"🛏️ {p['dormitorios']} dorm.")
    if p.get("direccion"):
        partes.append(f"📍 {e(p['direccion'])}")
    partes.append(p["url"])
    return "\n".join(partes)


# --------------------------------------------------------------------------
# ESTADO
# --------------------------------------------------------------------------


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {"ids": {}}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--dump", action="store_true")
    ap.add_argument("--pages", type=int, default=PAGES)
    args = ap.parse_args()

    state = load_state()
    conocidos: dict = state.get("ids", {})

    session = requests.Session()
    publicaciones: dict[str, dict] = {}

    for n in range(1, args.pages + 1):
        url = page_url(n)
        try:
            body = fetch(url, session)
        except Exception as exc:
            print(f"[!] Error bajando página {n} ({url}): {exc}")
            continue

        if args.dump and n == 1:
            Path("dump_argenprop_pagina1.html").write_text(body, encoding="utf-8")
            print("[i] HTML guardado en dump_argenprop_pagina1.html")

        cards = parse_cards(body)
        print(f"[i] Pagina {n}: {len(cards)} publicaciones")
        for c in cards:
            publicaciones.setdefault(c["id"], c)

        if n < args.pages:
            time.sleep(random.uniform(3, 7))

    if not publicaciones:
        print("[!] No se parseó ninguna publicación. Corré con --dump y revisá el HTML.")
        return 1

    nuevas = [p for pid, p in publicaciones.items() if pid not in conocidos]
    nuevas = [p for p in nuevas if pasa_filtros(p)]
    nuevas.sort(key=lambda p: int(p["id"]))

    if args.seed:
        print(f"[i] Seed: guardando {len(publicaciones)} publicaciones sin notificar.")
    else:
        print(f"[i] {len(nuevas)} publicaciones nuevas.")
        for p in nuevas:
            telegram_send(format_msg(p))
            time.sleep(1.2)

    for pid, p in publicaciones.items():
        conocidos.setdefault(pid, {"url": p["url"], "visto": p["visto"]})

    state["ids"] = conocidos
    state["ultima_corrida"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    print(f"[i] Estado guardado: {len(conocidos)} IDs conocidos en {STATE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
