#!/usr/bin/env python3
"""
Mapa de precio del m² por zona - Alquileres en General San Martín (Zonaprop)

Scrapea TODOS los departamentos, casas y PH en alquiler del partido de San
Martín, calcula el precio del m² de cada aviso, los agrupa por barrio (la
propia clasificación de Zonaprop) y arma un mapa interactivo en HTML
coloreado por precio de m², con una capa separada por tipo de propiedad
que podés prender/apagar.

Uso:
    python sanmartin_mapa_m2.py

Salidas (en la misma carpeta):
    san_martin_avisos.csv       -> un renglón por aviso (precio, m², $/m², barrio, tipo)
    san_martin_por_barrio.csv   -> resumen: mediana $/m², cantidad por barrio y tipo
    san_martin_mapa.html        -> mapa interactivo, abrilo con doble clic

Dependencias:
    pip install requests beautifulsoup4 folium

Nota: cada tipo de propiedad se agrega POR SEPARADO (un departamento y una
casa no valen lo mismo por m² aunque midan igual, mezclarlos distorsiona
el número). El mapa tiene una capa por tipo que podés tildar/destildar; el
CSV de detalle tiene una columna "tipo" para que filtres como quieras.
"""

import csv
import json
import random
import re
import time
from pathlib import Path
from statistics import median, mean

import requests
from bs4 import BeautifulSoup

# --------------------------------------------------------------------------
# CONFIG
# --------------------------------------------------------------------------

BASE = "https://www.zonaprop.com.ar"

# Los tres tipos que se pueden comparar razonablemente por m² (se excluye
# "terrenos" y "local comercial", cuyo valor no se rige por lo mismo).
TIPOS = [
    ("Departamento", "departamentos-alquiler-general-san-martin"),
    ("Casa", "casas-alquiler-general-san-martin"),
    ("PH", "ph-alquiler-general-san-martin"),
]

MAX_PAGES_POR_TIPO = 25   # techo de seguridad; corta antes si una página viene vacía

CSV_AVISOS = Path("san_martin_avisos.csv")
CSV_BARRIOS = Path("san_martin_por_barrio.csv")
MAPA_HTML = Path("san_martin_mapa.html")
CACHE_GEOCODE = Path("san_martin_geocode_cache.json")

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

COLOR_BORDE_TIPO = {
    "Departamento": "#1f77b4",
    "Casa": "#2ca02c",
    "PH": "#9467bd",
}

FALLBACK_COORDS = {
    "San Martín Centro": (-34.5745, -58.5386),
    "Villa Ballester": (-34.5484, -58.5583),
    "José León Suárez": (-34.5252, -58.5636),
    "San Andrés": (-34.5657, -58.5495),
    "Villa Maipu": (-34.5793, -58.5462),
    "Villa Lynch": (-34.5975, -58.5194),
    "Billinghurst": (-34.5563, -58.5313),
    "Loma Hermosa": (-34.5563, -58.5900),
    "Villa Libertad": (-34.5389, -58.5511),
    "Villa Bonich": (-34.5850, -58.5650),
    "Barrio Parque General San Martin": (-34.5700, -58.5700),
}

# --------------------------------------------------------------------------
# SCRAPING
# --------------------------------------------------------------------------


def page_url(slug: str, n: int) -> str:
    if n == 1:
        return f"{BASE}/{slug}.html"
    return f"{BASE}/{slug}-pagina-{n}.html"


def fetch(url: str, session: requests.Session) -> str:
    r = session.get(url, headers=HEADERS, timeout=30)
    r.raise_for_status()
    return r.text


def _txt(node, default=""):
    return node.get_text(" ", strip=True) if node else default


def _money(s: str):
    digits = re.sub(r"[^\d]", "", s or "")
    return int(digits) if digits else None


def _m2_total(features_text: str):
    m = re.search(r"(\d+)\s*m", features_text or "")
    return int(m.group(1)) if m else None


def _ambientes(features_text: str):
    m = re.search(r"(\d+)\s*amb", features_text or "")
    return int(m.group(1)) if m else None


def _zona(location_text: str) -> str:
    if not location_text:
        return "Sin especificar"
    return location_text.split(",")[0].strip()


def parse_listings(html_text: str, tipo: str) -> list[dict]:
    soup = BeautifulSoup(html_text, "html.parser")
    cards = soup.select('div[data-qa="posting PROPERTY"]') or soup.select("div[data-id]")

    out = []
    for card in cards:
        precio_num = _money(_txt(card.select_one('[data-qa="POSTING_CARD_PRICE"]')))
        features = _txt(card.select_one('[data-qa="POSTING_CARD_FEATURES"]'))
        location = _txt(card.select_one('[data-qa="POSTING_CARD_LOCATION"]'))
        m2 = _m2_total(features)

        if not precio_num or not m2:
            continue
        if not (M2_MIN <= m2 <= M2_MAX):
            continue

        precio_m2 = round(precio_num / m2)
        if not (PRECIO_M2_MIN <= precio_m2 <= PRECIO_M2_MAX):
            continue

        out.append(
            {
                "tipo": tipo,
                "precio": precio_num,
                "m2": m2,
                "precio_m2": precio_m2,
                "ambientes": _ambientes(features),
                "zona": _zona(location),
                "features": features,
            }
        )
    return out


def scrapear_tipo(tipo: str, slug: str, session: requests.Session) -> list[dict]:
    resultados: list[dict] = []
    for n in range(1, MAX_PAGES_POR_TIPO + 1):
        url = page_url(slug, n)
        try:
            body = fetch(url, session)
        except Exception as exc:
            print(f"[!] [{tipo}] Error en página {n}: {exc}")
            break

        listings = parse_listings(body, tipo)
        print(f"[i] [{tipo}] Página {n}: {len(listings)} avisos con precio y m² válidos")

        if not listings:
            print(f"[i] [{tipo}] Página vacía, asumo que llegué al final.")
            break

        resultados.extend(listings)

        if n < MAX_PAGES_POR_TIPO:
            time.sleep(random.uniform(2.5, 5))

    return resultados


def scrapear_todo() -> list[dict]:
    session = requests.Session()
    todos: list[dict] = []
    for tipo, slug in TIPOS:
        print(f"\n[i] === {tipo} ===")
        todos.extend(scrapear_tipo(tipo, slug, session))
        time.sleep(random.uniform(2, 4))
    return todos


# --------------------------------------------------------------------------
# GEOCODING
# --------------------------------------------------------------------------


def cargar_cache() -> dict:
    if CACHE_GEOCODE.exists():
        return json.loads(CACHE_GEOCODE.read_text(encoding="utf-8"))
    return {}


def guardar_cache(cache: dict) -> None:
    CACHE_GEOCODE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def geocodificar(zona: str, cache: dict):
    if zona in cache:
        return tuple(cache[zona]) if cache[zona] else None

    query = f"{zona}, General San Martín, Buenos Aires, Argentina"
    try:
        r = requests.get(
            "https://nominatim.openstreetmap.org/search",
            params={"q": query, "format": "json", "limit": 1},
            headers={"User-Agent": "sanmartin-mapa-m2-script/1.0"},
            timeout=15,
        )
        r.raise_for_status()
        data = r.json()
        time.sleep(1.1)
        if data:
            lat, lon = float(data[0]["lat"]), float(data[0]["lon"])
            cache[zona] = [lat, lon]
            return (lat, lon)
    except Exception as exc:
        print(f"[!] No pude geocodificar '{zona}': {exc}")

    if zona in FALLBACK_COORDS:
        print(f"[i] Usando coordenada aproximada de respaldo para '{zona}'")
        cache[zona] = list(FALLBACK_COORDS[zona])
        return FALLBACK_COORDS[zona]

    cache[zona] = None
    return None


# --------------------------------------------------------------------------
# AGREGACIÓN Y MAPA
# --------------------------------------------------------------------------


def agregar_por_zona_y_tipo(avisos: list[dict]) -> dict:
    grupos: dict = {}
    for a in avisos:
        grupos.setdefault((a["zona"], a["tipo"]), []).append(a["precio_m2"])

    resumen = {}
    for (zona, tipo), valores in grupos.items():
        resumen[(zona, tipo)] = {
            "mediana_m2": round(median(valores)),
            "promedio_m2": round(mean(valores)),
            "cantidad": len(valores),
            "min_m2": min(valores),
            "max_m2": max(valores),
        }
    return resumen


def color_por_precio(precio_m2: float, p_min: float, p_max: float) -> str:
    if p_max == p_min:
        t = 0.5
    else:
        t = (precio_m2 - p_min) / (p_max - p_min)
    t = max(0.0, min(1.0, t))

    if t < 0.5:
        r = int(255 * (t / 0.5))
        g = 200
    else:
        r = 255
        g = int(200 * (1 - (t - 0.5) / 0.5))
    return f"#{r:02x}{g:02x}00"


def armar_mapa(resumen: dict, cache_geo: dict) -> None:
    import folium

    todos_valores = [d["mediana_m2"] for d in resumen.values()]
    p_min, p_max = min(todos_valores), max(todos_valores)

    mapa = folium.Map(location=[-34.5657, -58.5495], zoom_start=13, tiles="CartoDB positron")

    capas = {}
    for tipo, _ in TIPOS:
        capas[tipo] = folium.FeatureGroup(name=tipo, show=True)

    zonas_sin_coords = set()

    for (zona, tipo), datos in sorted(resumen.items(), key=lambda kv: -kv[1]["mediana_m2"]):
        coords = geocodificar(zona, cache_geo)
        if not coords:
            zonas_sin_coords.add(zona)
            continue

        offset = {"Departamento": (0, 0), "Casa": (0.0009, 0.0009), "PH": (-0.0009, 0.0009)}[tipo]
        lat, lon = coords[0] + offset[0], coords[1] + offset[1]

        color = color_por_precio(datos["mediana_m2"], p_min, p_max)
        radio = 8 + min(datos["cantidad"], 40) * 0.7

        popup = (
            f"<b>{zona} — {tipo}</b><br>"
            f"Mediana: ${datos['mediana_m2']:,.0f}/m²<br>"
            f"Promedio: ${datos['promedio_m2']:,.0f}/m²<br>"
            f"Rango: ${datos['min_m2']:,.0f} - ${datos['max_m2']:,.0f}<br>"
            f"Avisos: {datos['cantidad']}"
        ).replace(",", ".")

        folium.CircleMarker(
            location=(lat, lon),
            radius=radio,
            popup=folium.Popup(popup, max_width=250),
            tooltip=f"{zona} ({tipo}): ${datos['mediana_m2']:,.0f}/m²".replace(",", "."),
            color=COLOR_BORDE_TIPO.get(tipo, "#333"),
            weight=2,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
        ).add_to(capas[tipo])

    for capa in capas.values():
        capa.add_to(mapa)
    folium.LayerControl(collapsed=False).add_to(mapa)

    if zonas_sin_coords:
        print(f"[!] Sin coordenadas para: {', '.join(sorted(zonas_sin_coords))}")

    leyenda = f"""
    <div style="position: fixed; bottom: 30px; left: 30px; z-index: 9999;
                background: white; padding: 10px 14px; border-radius: 6px;
                box-shadow: 0 1px 4px rgba(0,0,0,0.3); font-family: sans-serif; font-size: 13px;">
      <b>Precio del m² (alquiler)</b><br>
      <span style="color:{color_por_precio(p_min, p_min, p_max)}">●</span> ${p_min:,.0f} (más barato)<br>
      <span style="color:{color_por_precio((p_min+p_max)/2, p_min, p_max)}">●</span> ${(p_min+p_max)/2:,.0f}<br>
      <span style="color:{color_por_precio(p_max, p_min, p_max)}">●</span> ${p_max:,.0f} (más caro)<br>
      <i>Relleno = precio · Borde = tipo (panel de capas arriba a la derecha)</i><br>
      <i>Tamaño del círculo = cantidad de avisos</i>
    </div>
    """.replace(",", ".")
    mapa.get_root().html.add_child(folium.Element(leyenda))

    mapa.save(str(MAPA_HTML))


# --------------------------------------------------------------------------
# MAIN
# --------------------------------------------------------------------------


def main() -> None:
    print("[i] Scrapeando San Martín en Zonaprop (departamentos, casas y PH)...")
    avisos = scrapear_todo()

    if not avisos:
        print("[!] No se obtuvo ningún aviso válido. Nada para mapear.")
        return

    print(f"\n[i] Total de avisos válidos: {len(avisos)}")

    with CSV_AVISOS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(
            f, fieldnames=["tipo", "precio", "m2", "precio_m2", "ambientes", "zona", "features"]
        )
        w.writeheader()
        w.writerows(avisos)
    print(f"[i] Guardado {CSV_AVISOS}")

    resumen = agregar_por_zona_y_tipo(avisos)

    with CSV_BARRIOS.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["zona", "tipo", "mediana_$/m2", "promedio_$/m2", "min_$/m2", "max_$/m2", "cantidad_avisos"])
        for (zona, tipo), d in sorted(resumen.items(), key=lambda kv: -kv[1]["mediana_m2"]):
            w.writerow([zona, tipo, d["mediana_m2"], d["promedio_m2"], d["min_m2"], d["max_m2"], d["cantidad"]])
    print(f"[i] Guardado {CSV_BARRIOS}")

    print("\n[i] Resumen (mediana $/m²):")
    for (zona, tipo), d in sorted(resumen.items(), key=lambda kv: -kv[1]["mediana_m2"]):
        print(f"    {zona:32} {tipo:14} ${d['mediana_m2']:>7,.0f}/m²  ({d['cantidad']} avisos)".replace(",", "."))

    print("\n[i] Geocodificando barrios y armando el mapa...")
    cache_geo = cargar_cache()
    armar_mapa(resumen, cache_geo)
    guardar_cache(cache_geo)
    print(f"[i] Mapa guardado en {MAPA_HTML} - abrilo con doble clic")


if __name__ == "__main__":
    main()