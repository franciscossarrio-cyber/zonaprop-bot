#!/usr/bin/env python3
"""
Combina los avisos de Zonaprop (san_martin_avisos_geo.csv, pin exacto) y
Argenprop (san_martin_avisos_argenprop.csv, dirección geocodificada
aproximada) en un único san_martin_avisos_combinado.csv, que es lo que
consumen sanmartin_geomatch.py, sanmartin_historico.py y
sanmartin_mapa_geo.py de acá en adelante.

No deduplica avisos cross-posteados en ambos sitios (mismo depto publicado
en Zonaprop y Argenprop) porque no hay un ID común entre plataformas.

Uso:
    python sanmartin_merge_fuentes.py
"""

import csv
from pathlib import Path

CSV_ZONAPROP = Path("san_martin_avisos_geo.csv")
CSV_ARGENPROP = Path("san_martin_avisos_argenprop.csv")
CSV_OUT = Path("san_martin_avisos_combinado.csv")

FIELDNAMES = [
    "posting_id", "tipo", "precio", "m2", "precio_m2", "ambientes",
    "dormitorios", "zona", "direccion", "visibilidad", "lat", "lon", "url", "fuente",
]


def cargar(path: Path) -> list[dict]:
    if not path.exists():
        print(f"[!] No encontré {path}, sigo sin esa fuente")
        return []
    with path.open(encoding="utf-8") as f:
        return list(csv.DictReader(f))


def main() -> None:
    filas = cargar(CSV_ZONAPROP) + cargar(CSV_ARGENPROP)
    if not filas:
        raise SystemExit("No hay avisos de ninguna fuente. Corré los scrapers primero.")

    with CSV_OUT.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        w.writeheader()
        for row in filas:
            row.setdefault("fuente", "Zonaprop")
            w.writerow(row)

    por_fuente: dict[str, int] = {}
    for row in filas:
        f_ = row.get("fuente", "Zonaprop")
        por_fuente[f_] = por_fuente.get(f_, 0) + 1
    print(f"[i] {len(filas)} avisos combinados -> {CSV_OUT} ({por_fuente})")


if __name__ == "__main__":
    main()
