#!/usr/bin/env python3
"""
Scraper de San Martín (Zonaprop) que usa el JSON que trae cada página de
resultados (window.__PRELOADED_STATE__) en vez de parsear el HTML de las
tarjetas. Ese JSON incluye, por cada aviso, la latitud/longitud exacta de
la propiedad (postingLocation.postingGeolocation.geolocation) y la
dirección, así que el mapa puede ubicar cada aviso en su punto real en vez
de aproximarlo al centroide del barrio.

Uso:
    python sanmartin_scrape_geo.py

Salida:
    san_martin_avisos_geo.csv
"""

import csv
import json
import random
import time
from pathlib import Path

import requests

BASE = "https://www.zonaprop.com.ar"

TIPOS = [
    ("Departamento", "departamentos-alquiler-general-san-martin"),
    ("Casa", "casas-alquiler-general-san-martin"),
    ("PH", "ph-alquiler-general-san-martin"),
]

CSV_OUT = Path("san_martin_avisos_geo.csv")

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

M2_MIN, M2_MAX = 15, 800
PRECIO_M2_MIN, PRECIO_M2_MAX = 300, 15000
MAX_PAGINAS_SEGURIDAD = 40  # techo por si paging.totalPages viniera mal

DECODER = json.JSONDecoder()
PRELOADED_MARKER = "window.__PRELOADED_STATE__ = "

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch(url: str) -> str:
    for intento in range(4):
        r = SESSION.get(url, timeout=30)
        if r.status_code == 403:
            espera = 12 * (intento + 1)
            print(f"[!] 403 en {url}, espero {espera}s y reintento...")
            time.sleep(espera)
            continue
        r.raise_for_status()
        return r.text
    raise RuntimeError(f"403 persistente en {url}")


def page_url(slug: str, n: int) -> str:
    if n == 1:
        return f"{BASE}/{slug}.html"
    return f"{BASE}/{slug}-pagina-{n}.html"


def _mainfeature(mf: dict, key: str):
    v = (mf.get(key) or {}).get("value")
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return None


def parse_preloaded(html: str) -> dict:
    idx = html.find(PRELOADED_MARKER)
    if idx == -1:
        raise ValueError("No encontré window.__PRELOADED_STATE__ en la página")
    start = idx + len(PRELOADED_MARKER)
    data, _end = DECODER.raw_decode(html, start)
    return data


def extraer_avisos(data: dict, tipo: str) -> list[dict]:
    postings = data.get("listStore", {}).get("listPostings", [])
    out = []
    for p in postings:
        precio_info = None
        for op in p.get("priceOperationTypes") or []:
            prices = op.get("prices") or []
            if prices and prices[0].get("currency") == "$":
                precio_info = prices[0]
                break
        if not precio_info:
            continue
        precio = precio_info.get("amount")

        mf = p.get("mainFeatures") or {}
        m2 = _mainfeature(mf, "CFT100") or _mainfeature(mf, "CFT101")
        ambientes = _mainfeature(mf, "CFT1")
        dormitorios = _mainfeature(mf, "CFT2")

        if not precio or not m2:
            continue
        if not (M2_MIN <= m2 <= M2_MAX):
            continue
        precio_m2 = round(precio / m2)
        if not (PRECIO_M2_MIN <= precio_m2 <= PRECIO_M2_MAX):
            continue

        pl = p.get("postingLocation") or {}
        addr = pl.get("address") or {}
        loc = pl.get("location") or {}
        geo = ((pl.get("postingGeolocation") or {}).get("geolocation")) or {}
        lat, lon = geo.get("latitude"), geo.get("longitude")
        if lat is None or lon is None:
            continue

        out.append(
            {
                "posting_id": p.get("postingId"),
                "tipo": tipo,
                "precio": precio,
                "m2": m2,
                "precio_m2": precio_m2,
                "ambientes": ambientes,
                "dormitorios": dormitorios,
                "zona": loc.get("name") or "Sin especificar",
                "direccion": addr.get("name") or "",
                "visibilidad": addr.get("visibility") or "",
                "lat": lat,
                "lon": lon,
                "url": (BASE + p["url"]) if p.get("url") else "",
                "fuente": "Zonaprop",
            }
        )
    return out


def scrapear_tipo(tipo: str, slug: str) -> list[dict]:
    html = fetch(page_url(slug, 1))
    data = parse_preloaded(html)
    paging = data.get("listStore", {}).get("paging", {})
    total_paginas = min(paging.get("totalPages", 1), MAX_PAGINAS_SEGURIDAD)
    total_avisos = paging.get("total", "?")
    print(f"[i] [{tipo}] {total_avisos} avisos en {total_paginas} páginas")

    resultados = extraer_avisos(data, tipo)
    print(f"[i] [{tipo}] Página 1: {len(resultados)} avisos válidos")

    for n in range(2, total_paginas + 1):
        time.sleep(random.uniform(2.5, 5))
        try:
            html = fetch(page_url(slug, n))
            data = parse_preloaded(html)
        except Exception as exc:
            print(f"[!] [{tipo}] Error en página {n}: {exc}")
            continue
        listings = extraer_avisos(data, tipo)
        print(f"[i] [{tipo}] Página {n}: {len(listings)} avisos válidos")
        resultados.extend(listings)

    return resultados


def main() -> None:
    todos: list[dict] = []
    vistos = set()
    for tipo, slug in TIPOS:
        print(f"\n[i] === {tipo} ===")
        for aviso in scrapear_tipo(tipo, slug):
            if aviso["posting_id"] in vistos:
                continue
            vistos.add(aviso["posting_id"])
            todos.append(aviso)
        time.sleep(random.uniform(2, 4))

    if not todos:
        print("[!] No se obtuvo ningún aviso válido.")
        return

    fieldnames = [
        "posting_id", "tipo", "precio", "m2", "precio_m2", "ambientes",
        "dormitorios", "zona", "direccion", "visibilidad", "lat", "lon", "url", "fuente",
    ]
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(todos)

    print(f"\n[i] Total avisos geolocalizados: {len(todos)}")
    print(f"[i] Guardado {CSV_OUT}")


if __name__ == "__main__":
    main()
