#!/usr/bin/env python3
"""
Scraper de San Martín (Argenprop). A diferencia de Zonaprop, Argenprop no
expone lat/lon en el listado de resultados (solo en la ficha de cada aviso,
lo cual sería carísimo de visitar uno por uno para ~900 avisos), así que
este script solo saca los datos de cada card del listado: precio, m²,
dirección, ambientes, dormitorios, tipo, url. La geolocalización se hace
aparte, en sanmartin_geocode_argenprop.py, geocodificando el texto de
dirección.

Argenprop está detrás de un desafío JS de AWS WAF, así que hace falta un
navegador real (Playwright), no alcanza con requests.

Uso:
    python sanmartin_scrape_argenprop.py

Salida:
    san_martin_avisos_argenprop_raw.csv

Dependencias:
    pip install playwright
    playwright install chromium
"""

import csv
import random
import re
import time
from pathlib import Path

from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError

BASE = "https://www.argenprop.com"

TIPOS = [
    ("Departamento", "https://www.argenprop.com/departamentos/alquiler/partido-de-general-san-martin"),
    ("Casa", "https://www.argenprop.com/casas/alquiler/partido-de-general-san-martin"),
    ("PH", "https://www.argenprop.com/ph/alquiler/partido-de-general-san-martin"),
]

CSV_OUT = Path("san_martin_avisos_argenprop_raw.csv")

POR_PAGINA = 20
MAX_PAGINAS_SEGURIDAD = 60

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)

M2_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*m")
AMBIENTES_RE = re.compile(r"(\d+)\s*amb", re.IGNORECASE)
DORM_RE = re.compile(r"(\d+)\s*dorm", re.IGNORECASE)
ZONA_RE = re.compile(r"en alquiler en\s+([^,]+)", re.IGNORECASE)
PRECIO_MIN, PRECIO_MAX = 50_000, 5_000_000
M2_MIN, M2_MAX = 15, 800


def page_url(base_url: str, n: int) -> str:
    if n == 1:
        return f"{base_url}?orden-masnuevos"
    return f"{base_url}?orden-masnuevos&pagina-{n}"


def _num(txt: str, pattern: re.Pattern) -> str | None:
    m = pattern.search(txt or "")
    if not m:
        return None
    return m.group(1).replace(",", ".")


def parse_cards(page, tipo: str) -> list[dict]:
    cards = page.query_selector_all("a[data-item-card]")
    out = []
    for card in cards:
        pid = card.get_attribute("data-item-card")
        href = card.get_attribute("href")
        if not pid or not href:
            continue

        precio_attr = card.get_attribute("montooperacion")
        precio = int(precio_attr) if precio_attr and precio_attr.isdigit() else None

        direccion_el = card.query_selector(".card__address")
        direccion = direccion_el.inner_text().strip() if direccion_el else ""

        titulo_zona_el = card.query_selector(".card__title--primary")
        titulo_zona = titulo_zona_el.inner_text().strip() if titulo_zona_el else ""
        zona_m = ZONA_RE.search(titulo_zona)
        zona = zona_m.group(1).strip() if zona_m else ""

        titulo_el = card.query_selector("h2.card__title")
        titulo = titulo_el.inner_text().strip() if titulo_el else ""

        features_txt = " ".join(
            el.inner_text() for el in card.query_selector_all(".card__main-features li")
        )
        m2_txt = _num(features_txt, M2_RE)
        try:
            m2 = round(float(m2_txt)) if m2_txt else None
        except ValueError:
            m2 = None

        dorm_txt = _num(features_txt, DORM_RE)
        dormitorios = int(float(dorm_txt)) if dorm_txt else None

        texto_ambientes = f"{features_txt} {titulo} {titulo_zona}"
        amb_txt = AMBIENTES_RE.search(texto_ambientes)
        if amb_txt:
            ambientes = int(amb_txt.group(1))
        elif re.search(r"monoamb", texto_ambientes, re.IGNORECASE):
            ambientes = 1
        else:
            ambientes = None

        if not precio or not (PRECIO_MIN <= precio <= PRECIO_MAX):
            continue
        if not m2 or not (M2_MIN <= m2 <= M2_MAX):
            continue

        out.append(
            {
                "posting_id": f"AP{pid}",
                "tipo": tipo,
                "precio": precio,
                "m2": m2,
                "ambientes": ambientes,
                "dormitorios": dormitorios,
                "zona": zona,
                "direccion": direccion,
                "titulo": titulo,
                "url": BASE + href if href.startswith("/") else href,
            }
        )
    return out


def fetch_pagina(context, url: str):
    page = context.new_page()
    try:
        page.goto(url, timeout=45000, wait_until="domcontentloaded")
        try:
            page.wait_for_selector("[data-item-card]", timeout=20000)
        except PlaywrightTimeoutError:
            page.wait_for_timeout(6000)
        page.wait_for_timeout(1000)
        return page
    except Exception:
        page.close()
        raise


def scrapear_tipo(context, tipo: str, base_url: str) -> list[dict]:
    resultados: list[dict] = []
    vistos_en_tipo: set[str] = set()

    page = fetch_pagina(context, page_url(base_url, 1))
    title = page.title()
    m = re.search(r"^([\d.]+)\s", title)
    total_avisos = m.group(1) if m else "?"
    cards = parse_cards(page, tipo)
    page.close()
    print(f"[i] [{tipo}] {total_avisos} avisos en Argenprop. Página 1: {len(cards)} avisos válidos")
    for c in cards:
        if c["posting_id"] not in vistos_en_tipo:
            vistos_en_tipo.add(c["posting_id"])
            resultados.append(c)

    if not cards:
        return resultados

    try:
        total_n = int(total_avisos.replace(".", ""))
        total_paginas = min(-(-total_n // POR_PAGINA), MAX_PAGINAS_SEGURIDAD)
    except ValueError:
        total_paginas = 1

    for n in range(2, total_paginas + 1):
        time.sleep(random.uniform(4, 8))
        cards = []
        for intento in range(2):
            try:
                page = fetch_pagina(context, page_url(base_url, n))
                cards = parse_cards(page, tipo)
                page.close()
            except Exception as exc:
                print(f"[!] [{tipo}] Error en página {n} (intento {intento + 1}): {exc}")
                cards = []
            if cards:
                break
            if intento == 0:
                time.sleep(random.uniform(6, 10))
        nuevos = [c for c in cards if c["posting_id"] not in vistos_en_tipo]
        for c in nuevos:
            vistos_en_tipo.add(c["posting_id"])
            resultados.append(c)
        print(f"[i] [{tipo}] Página {n}: {len(cards)} avisos ({len(nuevos)} nuevos)")
        if not cards:
            print(f"[!] [{tipo}] Página {n} sin resultados tras reintentar, corto acá.")
            break

    return resultados


def main() -> None:
    todos: list[dict] = []
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True, args=["--disable-blink-features=AutomationControlled"]
        )
        for tipo, base_url in TIPOS:
            print(f"\n[i] === {tipo} ===")
            # Contexto (cookies/sesión) nuevo por tipo: si el WAF empieza a
            # desconfiar de una sesión con muchas navegaciones seguidas,
            # no queremos que eso arrastre a los siguientes tipos.
            context = browser.new_context(
                user_agent=USER_AGENT, locale="es-AR", viewport={"width": 1366, "height": 768}
            )
            try:
                todos.extend(scrapear_tipo(context, tipo, base_url))
            except Exception as exc:
                print(f"[!] [{tipo}] Falló por completo: {exc}")
            context.close()
            time.sleep(random.uniform(5, 9))
        browser.close()

    if not todos:
        print("[!] No se obtuvo ningún aviso válido de Argenprop.")
        return

    fieldnames = [
        "posting_id", "tipo", "precio", "m2", "ambientes", "dormitorios",
        "zona", "direccion", "titulo", "url",
    ]
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(todos)

    print(f"\n[i] Total avisos de Argenprop: {len(todos)}")
    print(f"[i] Guardado {CSV_OUT}")


if __name__ == "__main__":
    main()
