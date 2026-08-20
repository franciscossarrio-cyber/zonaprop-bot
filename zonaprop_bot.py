#!/usr/bin/env python3
"""
Bot de alertas Zonaprop -> Telegram

Chequea una (o varias) URL de listado de Zonaprop, detecta publicaciones que
no vio antes y manda un mensaje de Telegram por cada una.

Uso:
    python zonaprop_bot.py --seed     # primera corrida: guarda lo que hay hoy SIN avisar
    python zonaprop_bot.py            # corridas siguientes: avisa solo lo nuevo
    python zonaprop_bot.py --dump     # guarda el HTML crudo para debuggear selectores

Variables de entorno requeridas:
    TELEGRAM_BOT_TOKEN
    TELEGRAM_CHAT_ID
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

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

BASE = "https://www.zonaprop.com.ar"

# Tu bÃºsqueda, ordenada por "publicado descendente" (lo nuevo primero).
# OJO: usamos pÃ¡gina 1 (y opcionalmente 2), no la 3.
SEARCH_SLUG = (
    "departamentos-ph-alquiler-caballito-parque-centenario-parque-chacabuco"
    "-boedo-flores-norte-flores-3-ambientes-publicado-hace-menos-de-1-mes"
    "-menos-1900000-pesos-orden-publicado-descendente"
)

PAGES = 2  # cuÃ¡ntas pÃ¡ginas revisar por corrida (1 alcanza si corrÃ©s seguido)

STATE_FILE = Path(os.getenv("ZP_STATE_FILE", "vistos.json"))

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}

# Filtros extra que Zonaprop no te deja poner (opcionales, dejalos en None para ignorar)
MAX_EXPENSAS = None          # ej: 300000  -> descarta si expensas > 300k
MAX_TOTAL = 2_000_000        # descarta si alquiler + expensas > $2.000.000
EXCLUIR_PALABRAS = ["uso comercial", "temporal", "amoblado"]  # en el tÃ­tulo

# --------------------------------------------------------------------------
# SCRAPING
# --------------------------------------------------------------------------

POSTING_RE = re.compile(r"/propiedades/clasificado/[^\"'\s]*?-(\d+)\.html")


def page_url(n: int) -> str:
    if n == 1:
        return f"{BASE}/{SEARCH_SLUG}.html"
    return f"{BASE}/{SEARCH_SLUG}-pagina-{n}.html"


def fetch(url: str, session: requests.Session) -> str:
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def _txt(node, default=""):
    return node.get_text(" ", strip=True) if node else default


def _money(s: str):
    """'$ 1.250.000' -> 1250000 ; devuelve None si no hay nÃºmero."""
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else None


def parse_cards(html_text: str) -> list[dict]:
    """
    Extrae las publicaciones del listado.

    Estrategia: buscamos los contenedores de aviso por data-qa/data-id y, si eso
    falla (Zonaprop cambia el DOM cada tanto), caemos al link del aviso, que es
    lo Ãºnico realmente estable.
    """
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.select('div[data-qa="posting PROPERTY"]') or soup.select("div[data-id]")

    out = []
    seen_ids = set()

    for card in cards:
        link = card.find("a", href=POSTING_RE)
        if not link:
            link = card.find("h3")
            link = link.find("a") if link else None
        href = link["href"] if link and link.has_attr("href") else None
        if not href:
            continue

        m = POSTING_RE.search(href)
        if not m:
            continue
        pid = m.group(1)
        if pid in seen_ids:
            continue
        seen_ids.add(pid)

        precio = _txt(card.select_one('[data-qa="POSTING_CARD_PRICE"]'))
        expensas = _txt(card.select_one('[data-qa="POSTING_CARD_EXPENSES"]'))
        features = _txt(card.select_one('[data-qa="POSTING_CARD_FEATURES"]'))
        ubicacion = _txt(card.select_one('[data-qa="POSTING_CARD_LOCATION"]'))
        direccion = _txt(card.select_one('.postingAddress, [class*="postingAddress"]'))
        titulo = _txt(link)[:140]

        out.append(
            {
                "id": pid,
                "url": f"{BASE}{href.split('?')[0]}" if href.startswith("/") else href.split("?")[0],
                "titulo": titulo,
                "precio": precio,
                "precio_num": _money(precio),
                "expensas": expensas,
                "expensas_num": _money(expensas),
                "features": features,
                "ubicacion": ubicacion,
                "direccion": direccion,
                "visto": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }
        )

    # Fallback total: si no encontramos ninguna card, al menos sacamos los IDs+URLs
    if not out:
        for href in set(re.findall(r'href="(/propiedades/clasificado/[^"]+\.html)', html_text)):
            m = POSTING_RE.search(href)
            if m:
                out.append(
                    {
                        "id": m.group(1),
                        "url": BASE + href.split("?")[0],
                        "titulo": "(sin parsear)",
                        "precio": "",
                        "precio_num": None,
                        "expensas": "",
                        "expensas_num": None,
                        "features": "",
                        "ubicacion": "",
                        "direccion": "",
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
    partes = [f"ðŸ  <b>{e(p['titulo'] or 'Nueva publicaciÃ³n')}</b>"]
    linea_precio = p["precio"] or ""
    if p["expensas"]:
        linea_precio += f"  +  {p['expensas']}"
    if p.get("precio_num"):
        total = p["precio_num"] + (p.get("expensas_num") or 0)
        linea_precio += f"\nðŸ’µ Total aprox: ${total:,.0f}".replace(",", ".")
    if linea_precio:
        partes.append(e(linea_precio))
    if p["features"]:
        partes.append(f"ðŸ“ {e(p['features'])}")
    loc = " Â· ".join(x for x in [p.get("direccion"), p.get("ubicacion")] if x)
    if loc:
        partes.append(f"ðŸ“ {e(loc)}")
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
    ap.add_argument("--seed", action="store_true", help="guarda lo actual sin mandar Telegram")
    ap.add_argument("--dump", action="store_true", help="guarda el HTML crudo de la pÃ¡gina 1")
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
        except Exception as exc:  # 403 / timeout / etc
            print(f"[!] Error bajando pÃ¡gina {n}: {exc}")
            continue

        if args.dump and n == 1:
            Path("dump_pagina1.html").write_text(body, encoding="utf-8")
            print("[i] HTML guardado en dump_pagina1.html")

        cards = parse_cards(body)
        print(f"[i] PÃ¡gina {n}: {len(cards)} publicaciones")
        for c in cards:
            publicaciones.setdefault(c["id"], c)

        if n < args.pages:
            time.sleep(random.uniform(3, 7))  # no martillar el sitio

    if not publicaciones:
        print("[!] No se parseÃ³ ninguna publicaciÃ³n. CorrÃ© con --dump y revisÃ¡ el HTML.")
        return 1

    nuevas = [p for pid, p in publicaciones.items() if pid not in conocidos]
    nuevas = [p for p in nuevas if pasa_filtros(p)]
    # mÃ¡s viejas primero, para que en Telegram queden ordenadas
    nuevas.sort(key=lambda p: int(p["id"]))

    if args.seed:
        print(f"[i] Seed: guardando {len(publicaciones)} publicaciones sin notificar.")
    else:
        print(f"[i] {len(nuevas)} publicaciones nuevas.")
        for p in nuevas:
            telegram_send(format_msg(p))
            time.sleep(1.2)  # rate limit de Telegram

    for pid, p in publicaciones.items():
        conocidos.setdefault(pid, {k: p[k] for k in ("url", "precio", "visto")})

    state["ids"] = conocidos
    state["ultima_corrida"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    save_state(state)
    print(f"[i] Estado guardado: {len(conocidos)} IDs conocidos en {STATE_FILE}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

