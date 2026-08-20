#!/usr/bin/env python3
"""
Importa los polígonos oficiales de barrio/villa de San Martín desde el
shapefile de localidades (fuente: UNGS, capa "localidades san martin_UNGS")
y el límite del partido (capa "limite partido San Martin_3857"), y arma
san_martin_barrios.geojson con los 24 polígonos oficiales de villa dentro
de la ciudad cabecera (filtrados por código de localidad clc que empieza
con "0637101", el código INDEC del partido de San Martín). No se mezclan
polígonos aproximados de otras fuentes (OSM, a mano, etc.) — solo lo que
sale de este shapefile oficial.

También guarda san_martin_partido.geojson con el límite del partido
(reproyectado de EPSG:3857 a WGS84) como capa de referencia.

Uso:
    python sanmartin_importar_poligonos_oficiales.py [--src RUTA_CARPETA_SHAPEFILES]

Dependencias:
    pip install pyshp pyproj
"""

import argparse
import json
from pathlib import Path

import shapefile
from pyproj import Transformer

BARRIOS_GEOJSON = Path("san_martin_barrios.geojson")
PARTIDO_GEOJSON = Path("san_martin_partido.geojson")

CLC_PREFIX_SAN_MARTIN = "0637101"

# Nombre oficial (localidades UNGS) -> etiqueta a mostrar.
# Donde Zonaprop ya usa un nombre para esa zona en los avisos (ver
# sanmartin_mapa_barrios.py / san_martin_avisos_geo.csv), se usa EXACTAMENTE
# esa cadena para que el mapa pueda cruzar avisos <-> polígono por nombre.
OFICIAL_A_ZONA = {
    "VILLA BALLESTER": "Villa Ballester",
    "VILLA PARQUE SAN LORENZO": "Villa Parque San Lorenzo",
    "VILLA MARQUES ALEJANDRO MARIA DE AGUADO": "Villa Marqués de Aguado",
    "VILLA JUAN MARTIN PUEYRREDON": "Villa Pueyrredón",
    "VILLA CORONEL JOSE M. ZAPIOLA": "Villa Zapiola",
    "CIUDAD DEL LIBERTADOR GENERAL SAN MARTIN": "San Martín Centro",
    "VILLA YAPEYU": "Villa Yapeyú",
    "VILLA SAN ANDRES": "San Andrés",
    "VILLA GENERAL JUAN G. LAS HERAS": "Villa Las Heras",
    "VILLA GENERAL JOSE TOMAS GUIDO": "Villa Guido",
    "VILLA GENERAL ANTONIO J. DE SUCRE": "Villa Sucre",
    "VILLA GODOY CRUZ": "Villa Godoy Cruz",
    "BARRIO PARQUE GENERAL SAN MARTIN": "Barrio Parque General San Martin",
    "BILLINGHURST": "Billinghurst",
    "VILLA LIBERTAD": "Villa Libertad",
    "VILLA AYACUCHO": "Villa Ayacucho",
    "VILLA BERNARDO MONTEAGUDO": "Villa Monteagudo",
    "VILLA PARQUE PRESIDENTE FIGUEROA ALCORTA": "Villa Parque Figueroa Alcorta",
    "VILLA LYNCH": "Villa Lynch",
    "VILLA CHACABUCO": "Villa Chacabuco",
    "VILLA MAIPU": "Villa Maipu",
    "VILLA GRANADEROS DE SAN MARTIN": "Villa Granaderos De San Martin",
    "VILLA GREGORIA MATORRAS": "Villa Gregoria Matorras",
    "CIUDAD JARDIN EL LIBERTADOR": "Ciudad Jardín El Libertador",
}


def fix_mojibake(s: str) -> str:
    """El .dbf trae varios nombres UTF-8 codificados dos veces. Si el string
    tiene ese patrón, decodificarlo una vuelta más lo arregla; si no, se
    devuelve tal cual."""
    try:
        return s.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return s


def cargar_villas_oficiales(carpeta: Path) -> list[dict]:
    sf = shapefile.Reader(str(carpeta / "localidades san martin_UNGS"), encoding="utf-8")
    features = []
    sin_mapear = []
    for sr in sf.shapeRecords():
        rec = sr.record.as_dict()
        if not str(rec["clc"]).startswith(CLC_PREFIX_SAN_MARTIN):
            continue
        nombre_oficial = fix_mojibake(rec["fna"]).strip()
        zona = OFICIAL_A_ZONA.get(nombre_oficial)
        if zona is None:
            sin_mapear.append(nombre_oficial)
            zona = nombre_oficial.title()
        features.append(
            {
                "type": "Feature",
                "properties": {
                    "zona": zona,
                    "aprox": False,
                    "oficial": True,
                    "clc": rec["clc"],
                },
                "geometry": sr.shape.__geo_interface__,
            }
        )
    if sin_mapear:
        print(f"[!] Villas oficiales sin alias en OFICIAL_A_ZONA (usé Title Case): {sin_mapear}")
    print(f"[i] {len(features)} villas oficiales cargadas de la capa UNGS")
    return features


def cargar_partido(carpeta: Path) -> dict:
    sf = shapefile.Reader(str(carpeta / "limite partido San Martin_3857"), encoding="utf-8")
    transformer = Transformer.from_crs("EPSG:3857", "EPSG:4326", always_xy=True)
    sr = sf.shapeRecords()[0]
    geo = sr.shape.__geo_interface__

    def reproyectar(coords):
        if isinstance(coords[0], (int, float)):
            lon, lat = transformer.transform(coords[0], coords[1])
            return [lon, lat]
        return [reproyectar(c) for c in coords]

    geo = dict(geo)
    geo["coordinates"] = reproyectar(geo["coordinates"])
    return {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"zona": "Partido de General San Martín"},
                "geometry": geo,
            }
        ],
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--src",
        default=r"C:\Users\Usuario\Downloads\limite partido san martin",
        help="Carpeta con los shapefiles (limite partido San Martin_3857.* y localidades san martin_UNGS.*)",
    )
    args = ap.parse_args()
    carpeta = Path(args.src)

    features = cargar_villas_oficiales(carpeta)

    barrios_fc = {"type": "FeatureCollection", "features": features}
    BARRIOS_GEOJSON.write_text(json.dumps(barrios_fc, ensure_ascii=False, indent=None), encoding="utf-8")
    print(f"[i] Guardado {BARRIOS_GEOJSON} ({len(features)} polígonos totales)")

    partido_fc = cargar_partido(carpeta)
    PARTIDO_GEOJSON.write_text(json.dumps(partido_fc, ensure_ascii=False), encoding="utf-8")
    print(f"[i] Guardado {PARTIDO_GEOJSON}")


if __name__ == "__main__":
    main()
