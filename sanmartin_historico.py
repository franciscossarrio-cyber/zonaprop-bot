#!/usr/bin/env python3
"""
Agrega una "foto" del día a san_martin_historico.csv: por cada corrida del
scraper, calcula la mediana de precio y de precio/m² por barrio (y por
barrio+tipo de propiedad), y suma una fila nueva. Con esto se arma la serie
histórica para ver la evolución de precios por barrio en el tiempo.

Se corre después de sanmartin_scrape_geo.py (lee su salida, no vuelve a
scrapear). Pensado para correr 1 vez por día (cron / GitHub Actions); si se
corre más de una vez el mismo día, agrega filas duplicadas para esa fecha en
vez de pisarlas (así no se pierde info si hace falta correrlo dos veces).

Uso:
    python sanmartin_historico.py

Entrada:
    san_martin_avisos_geo.csv     -> generado por sanmartin_scrape_geo.py

Salida:
    san_martin_historico.csv      -> se agrega (append), no se pisa
"""

import csv
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from statistics import median

CSV_AVISOS = Path("san_martin_avisos_geo.csv")
CSV_HISTORICO = Path("san_martin_historico.csv")

FIELDNAMES = [
    "fecha", "zona", "tipo", "cantidad",
    "precio_mediana", "precio_m2_mediana",
]

ZONA_TODAS = "__todas__"
TIPO_TODOS = "Todos"


def cargar_avisos() -> list[dict]:
    if not CSV_AVISOS.exists():
        raise SystemExit(f"No encontré {CSV_AVISOS}. Corré primero sanmartin_scrape_geo.py.")
    avisos = []
    with CSV_AVISOS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                precio = int(row["precio"])
                precio_m2 = int(row["precio_m2"])
            except (KeyError, ValueError):
                continue
            avisos.append({"zona": row["zona"], "tipo": row["tipo"], "precio": precio, "precio_m2": precio_m2})
    return avisos


def agrupar(avisos: list[dict]) -> dict[tuple[str, str], dict]:
    grupos: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for a in avisos:
        grupos[(a["zona"], a["tipo"])].append(a)
        grupos[(a["zona"], TIPO_TODOS)].append(a)
        grupos[(ZONA_TODAS, a["tipo"])].append(a)
        grupos[(ZONA_TODAS, TIPO_TODOS)].append(a)

    stats = {}
    for clave, items in grupos.items():
        precios = [x["precio"] for x in items]
        precios_m2 = [x["precio_m2"] for x in items]
        stats[clave] = {
            "cantidad": len(items),
            "precio_mediana": round(median(precios)),
            "precio_m2_mediana": round(median(precios_m2)),
        }
    return stats


def guardar(fecha: str, stats: dict[tuple[str, str], dict]) -> None:
    existe = CSV_HISTORICO.exists()
    with CSV_HISTORICO.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if not existe:
            w.writeheader()
        for (zona, tipo), s in sorted(stats.items()):
            w.writerow({"fecha": fecha, "zona": zona, "tipo": tipo, **s})


def main() -> None:
    avisos = cargar_avisos()
    if not avisos:
        print("[!] No hay avisos para agregar al histórico.")
        return
    fecha = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    stats = agrupar(avisos)
    guardar(fecha, stats)
    print(f"[i] Agregadas {len(stats)} filas ({fecha}) a {CSV_HISTORICO}")


if __name__ == "__main__":
    main()
