#!/usr/bin/env python3
"""
Le asigna a cada aviso de san_martin_avisos_geo.csv el barrio "real" al que
pertenece según sus coordenadas (lat/lon) y los polígonos de
san_martin_barrios.geojson, en vez de depender del nombre de zona que trae
Zonaprop (que es más genérico y no usa varios de los nombres oficiales de
villa: Villa Pueyrredón, Villa Marqués de Aguado, etc. quedaban siempre
vacíos aunque tuvieran avisos, porque Zonaprop nunca etiqueta un aviso con
ese nombre puntual).

Agrega una columna "zona_geo" al CSV:
    - Si el punto cae dentro de un polígono oficial (aprox=false), se usa
      ese barrio.
    - Si no, se prueba con los polígonos aproximados/legacy (aprox=true).
    - Si no cae dentro de ninguno, se usa el "zona" original de Zonaprop
      como último recurso (para no perder el aviso).

Se corre después de sanmartin_scrape_geo.py y antes de sanmartin_historico.py
/ sanmartin_mapa_geo.py.

Uso:
    python sanmartin_geomatch.py

Dependencias:
    pip install shapely
"""

import csv
import json
from pathlib import Path

from shapely.geometry import Point, shape
from shapely.prepared import prep

CSV_AVISOS = Path("san_martin_avisos_combinado.csv")
GEOJSON_BARRIOS = Path("san_martin_barrios.geojson")


def cargar_poligonos() -> tuple[list[tuple[str, object]], list[tuple[str, object]]]:
    data = json.loads(GEOJSON_BARRIOS.read_text(encoding="utf-8"))
    oficiales, legacy = [], []
    for f in data["features"]:
        zona = f["properties"]["zona"]
        geom = prep(shape(f["geometry"]))
        (legacy if f["properties"].get("aprox") else oficiales).append((zona, geom))
    return oficiales, legacy


def matchear(lat: float, lon: float, oficiales, legacy) -> str | None:
    punto = Point(lon, lat)
    for zona, geom in oficiales:
        if geom.contains(punto):
            return zona
    for zona, geom in legacy:
        if geom.contains(punto):
            return zona
    return None


def main() -> None:
    if not CSV_AVISOS.exists():
        raise SystemExit(f"No encontré {CSV_AVISOS}. Corré primero sanmartin_merge_fuentes.py.")
    if not GEOJSON_BARRIOS.exists():
        raise SystemExit(f"No encontré {GEOJSON_BARRIOS}.")

    oficiales, legacy = cargar_poligonos()

    with CSV_AVISOS.open(encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        filas = list(reader)

    sin_match = 0
    for row in filas:
        try:
            lat, lon = float(row["lat"]), float(row["lon"])
        except (KeyError, ValueError):
            row["zona_geo"] = row.get("zona", "")
            continue
        zona_geo = matchear(lat, lon, oficiales, legacy)
        if zona_geo is None:
            sin_match += 1
            zona_geo = row["zona"]
        row["zona_geo"] = zona_geo

    if "zona_geo" not in fieldnames:
        fieldnames = fieldnames + ["zona_geo"]

    with CSV_AVISOS.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        w.writerows(filas)

    print(f"[i] {len(filas)} avisos geolocalizados por barrio real ({sin_match} sin polígono que los contenga, quedaron con su zona de Zonaprop)")


if __name__ == "__main__":
    main()
