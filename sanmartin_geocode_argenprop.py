#!/usr/bin/env python3
"""
Geocodifica los avisos de san_martin_avisos_argenprop_raw.csv (dirección en
texto, sin lat/lon) contra Nominatim (OpenStreetMap) y arma
san_martin_avisos_argenprop.csv con el mismo esquema de columnas que
san_martin_avisos_geo.csv (el de Zonaprop), listo para mezclar con
sanmartin_merge_fuentes.py.

La ubicación queda aproximada a la altura/cuadra de la dirección que
publica Argenprop (no es el pin exacto que se ve en la ficha de cada
aviso — sacar eso implicaría visitar cada ficha individualmente, ver
sanmartin_scrape_argenprop.py).

Respeta la política de uso de Nominatim: 1 request/segundo, User-Agent
identificable, y cachea todo en argenprop_geocode_cache.json para no
volver a pedir una dirección ya geocodificada en corridas futuras.

Uso:
    python sanmartin_geocode_argenprop.py

Entrada:
    san_martin_avisos_argenprop_raw.csv

Salida:
    san_martin_avisos_argenprop.csv
    argenprop_geocode_cache.json (se actualiza)
"""

import csv
import json
import re
import time
from pathlib import Path

import requests

CSV_IN = Path("san_martin_avisos_argenprop_raw.csv")
CSV_OUT = Path("san_martin_avisos_argenprop.csv")
CACHE_FILE = Path("argenprop_geocode_cache.json")

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"
USER_AGENT = "sanmartin-mapeo-bot/1.0 (uso personal, no comercial; contacto vía github.com/franciscossarrio-cyber/zonaprop-bot)"

# Caja aproximada del partido de San Martín, para sesgar (no forzar) los
# resultados de Nominatim hacia la zona correcta.
VIEWBOX = "-58.62,-34.49,-58.51,-34.60"  # lon_min,lat_max,lon_max,lat_min

M2_MIN, M2_MAX = 15, 800
PRECIO_M2_MIN, PRECIO_M2_MAX = 300, 15000


def cargar_cache() -> dict:
    if CACHE_FILE.exists():
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    return {}


def guardar_cache(cache: dict) -> None:
    CACHE_FILE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")


def limpiar_direccion(direccion: str) -> str:
    """Nominatim devuelve 0 resultados si la query trae 'Piso N', 'Dto N',
    'entre X y Y', etc. — se los saca, se geocodifica solo la calle y
    altura."""
    txt = direccion
    txt = re.sub(r",?\s*piso\s*\S+", "", txt, flags=re.IGNORECASE)
    txt = re.sub(r",?\s*(dto|depto|dpto)\.?\s*\S+", "", txt, flags=re.IGNORECASE)
    txt = re.split(r"\bentre\b", txt, flags=re.IGNORECASE)[0]
    return txt.strip(" ,")


def _buscar(query: str, session: requests.Session) -> tuple[float, float] | None:
    r = session.get(
        NOMINATIM_URL,
        params={
            "q": query,
            "format": "jsonv2",
            "limit": 1,
            "viewbox": VIEWBOX,
            "bounded": 1,
            "countrycodes": "ar",
        },
        headers={"User-Agent": USER_AGENT},
        timeout=15,
    )
    r.raise_for_status()
    resultados = r.json()
    if not resultados:
        return None
    return float(resultados[0]["lat"]), float(resultados[0]["lon"])


def geocodificar(direccion: str, zona: str, session: requests.Session) -> tuple[float, float] | None:
    direccion_limpia = limpiar_direccion(direccion)

    # 1er intento: con el barrio, que ayuda a desambiguar cuando es un
    # nombre de lugar real para OSM (ej. "Villa Ballester").
    if zona:
        query = ", ".join([direccion_limpia, zona, "Partido de General San Martín", "Buenos Aires", "Argentina"])
        coords = _buscar(query, session)
        if coords:
            return coords
        time.sleep(1.1)

    # 2do intento: sin el barrio, que a veces es una etiqueta genérica
    # (ej. "Centro") que no matchea ningún lugar real y tira todo el query.
    query = ", ".join([direccion_limpia, "Partido de General San Martín", "Buenos Aires", "Argentina"])
    return _buscar(query, session)


def main() -> None:
    if not CSV_IN.exists():
        raise SystemExit(f"No encontré {CSV_IN}. Corré primero sanmartin_scrape_argenprop.py.")

    with CSV_IN.open(encoding="utf-8") as f:
        filas = list(csv.DictReader(f))

    cache = cargar_cache()
    session = requests.Session()

    salida = []
    nuevos_geocodificados = 0
    sin_geocodificar = 0

    for row in filas:
        try:
            precio = int(row["precio"])
            m2 = int(row["m2"])
            ambientes = int(row["ambientes"]) if row["ambientes"] else None
        except (KeyError, ValueError):
            continue
        if ambientes is None:
            continue
        if not (M2_MIN <= m2 <= M2_MAX):
            continue
        precio_m2 = round(precio / m2)
        if not (PRECIO_M2_MIN <= precio_m2 <= PRECIO_M2_MAX):
            continue

        clave = f"{row['direccion'].strip().lower()}|{row['zona'].strip().lower()}"
        if clave in cache:
            coords = cache[clave]
        else:
            try:
                coords = geocodificar(row["direccion"], row["zona"], session)
            except Exception as exc:
                print(f"[!] Error geocodificando '{row['direccion']}': {exc}")
                coords = None
            cache[clave] = coords
            nuevos_geocodificados += 1
            time.sleep(1.1)

        if coords is None:
            sin_geocodificar += 1
            continue

        lat, lon = coords
        salida.append(
            {
                "posting_id": row["posting_id"],
                "tipo": row["tipo"],
                "precio": precio,
                "m2": m2,
                "precio_m2": precio_m2,
                "ambientes": ambientes,
                "dormitorios": row.get("dormitorios") or "",
                "zona": row["zona"] or "General San Martín",
                "direccion": row["direccion"],
                "visibilidad": "APPROX",
                "lat": lat,
                "lon": lon,
                "url": row["url"],
                "fuente": "Argenprop",
            }
        )

    guardar_cache(cache)

    fieldnames = [
        "posting_id", "tipo", "precio", "m2", "precio_m2", "ambientes",
        "dormitorios", "zona", "direccion", "visibilidad", "lat", "lon", "url", "fuente",
    ]
    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(salida)

    print(
        f"[i] {len(salida)} avisos de Argenprop geocodificados "
        f"({nuevos_geocodificados} consultas nuevas a Nominatim, {sin_geocodificar} sin resultado) -> {CSV_OUT}"
    )


if __name__ == "__main__":
    main()
