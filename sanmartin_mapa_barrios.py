#!/usr/bin/env python3
"""
Mapa de alquileres en San Martín con polígonos de barrio y filtro por ambientes.

Lee san_martin_avisos.csv (generado por sanmartin_mapa_m2.py), arma los
polígonos reales de cada barrio (de OpenStreetMap, o un círculo aproximado
si OSM no tiene ese límite cargado) y genera un mapa Leaflet standalone con
selectores de Tipo de propiedad / Ambientes / Métrica que recalculan el
color de cada barrio en el navegador (no hace falta re-correr el script
para cambiar el filtro).

Uso:
    python sanmartin_mapa_barrios.py

Salida:
    san_martin_mapa_barrios.html   -> abrilo con doble clic
    san_martin_barrios.geojson     -> cache de los polígonos (se reusa)

Dependencias:
    pip install shapely
(usa san_martin_avisos.csv ya generado; no vuelve a scrapear Zonaprop)
"""

import csv
import json
import re
import time
import urllib.request
from pathlib import Path
from statistics import median

from shapely.geometry import LineString, Point, mapping, shape
from shapely.ops import linemerge, polygonize, unary_union

CSV_AVISOS = Path("san_martin_avisos.csv")
GEOJSON_CACHE = Path("san_martin_barrios.geojson")
MAPA_HTML = Path("san_martin_mapa_barrios.html")

OVERPASS_URL = "https://overpass-api.de/api/interpreter"

# Límite oficial del partido (relation OSM "Partido de General San Martín").
PARTIDO_REL_ID = 1719022

# zona (como la etiqueta Zonaprop en el CSV) -> uno o más relation id de OSM
# cuya unión arma el polígono del barrio. Los nombres largos de "Villa X"
# son la forma en que OSM tiene cargadas las villas/barrios informales del
# partido (no existe una capa catastral oficial de barrios para San Martín,
# a diferencia de CABA).
ZONA_A_RELATIONS = {
    "Villa Ballester": [9166567, 19325392],  # Noreste + Noroeste
    "José León Suárez": [9166483],
    "San Andrés": [9166767],
    "Villa Maipu": [9168613],
    "San Martín Centro": [9168783],  # Ciudad del Libertador General San Martín
    "Villa Lynch": [9168846],
    "Billinghurst": [9168868],
    "Villa Libertad": [17499867],
    "Villa Monteagudo": [19269606],  # Villa Bernardo de Monteagudo
    "Villa Granaderos De San Martin": [19269733],
    "Loma Hermosa": [2433872],
}

# Zonas sin polígono en OSM: se dibujan como círculo aproximado alrededor
# de un centroide oficial (georef-ar-api / INDEC).
ZONA_A_PUNTO_APROX = {
    "Barrio Parque General San Martin": (-34.5656365, -58.5861316),
    "Villa Bonich": (-34.5712921, -58.5625402),
}

# "General San Martín" es la etiqueta "cajón de sastre" que pone Zonaprop
# cuando no reconoce un barrio más específico. No corresponde a un barrio
# real puntual: se representa como el remanente del partido que no quedó
# cubierto por ningún barrio con nombre propio.
ZONA_LEFTOVER = "General San Martín"

RADIO_APROX_GRADOS = 0.0035  # ~350 m, para los círculos de respaldo

HEADERS = {"User-Agent": "sanmartin-mapa-barrios-script/1.0"}


# --------------------------------------------------------------------------
# OSM: bajar y armar los polígonos (con cache local)
# --------------------------------------------------------------------------


def _overpass(query: str) -> dict:
    req = urllib.request.Request(
        OVERPASS_URL, data=query.encode("utf-8"), headers=HEADERS
    )
    last_exc = None
    for attempt in range(4):
        try:
            with urllib.request.urlopen(req, timeout=150) as resp:
                return json.load(resp)
        except Exception as exc:  # Overpass es propenso a 504 bajo carga
            last_exc = exc
            print(f"[!] Overpass falló (intento {attempt + 1}/4): {exc}")
            time.sleep(6)
    raise RuntimeError(f"No pude consultar Overpass: {last_exc}")


def _relation_to_polygon(el: dict):
    lines = []
    for m in el.get("members", []):
        if m["type"] == "way" and "geometry" in m and m.get("role", "outer") != "inner":
            coords = [(pt["lon"], pt["lat"]) for pt in m["geometry"]]
            if len(coords) >= 2:
                lines.append(LineString(coords))
    if not lines:
        return None
    merged = linemerge(lines)
    geoms = [merged] if merged.geom_type == "LineString" else list(merged.geoms)
    polys = list(polygonize(geoms))
    if not polys:
        return None
    return unary_union(polys)


def bajar_geometrias_osm() -> dict:
    """Devuelve {relation_id (str): shapely Polygon/MultiPolygon}."""
    ids = [PARTIDO_REL_ID] + sorted({rid for rids in ZONA_A_RELATIONS.values() for rid in rids})
    idlist = ",".join(str(i) for i in ids)
    query = f"[out:json][timeout:120];\nrelation(id:{idlist});\nout geom;\n"
    print(f"[i] Bajando {len(ids)} límites de OpenStreetMap (Overpass)...")
    data = _overpass(query)

    poligonos = {}
    for el in data["elements"]:
        poly = _relation_to_polygon(el)
        nombre = el.get("tags", {}).get("name", str(el["id"]))
        if poly is None:
            print(f"[!] No pude armar el polígono de '{nombre}' ({el['id']})")
            continue
        poligonos[str(el["id"])] = poly
    return poligonos


def armar_barrios(poligonos_osm: dict) -> dict:
    """Combina los polígonos de OSM en el diccionario final zona -> (poly, aprox: bool)."""
    partido = poligonos_osm[str(PARTIDO_REL_ID)]

    barrios = {}
    cubierto = []

    for zona, rel_ids in ZONA_A_RELATIONS.items():
        piezas = [poligonos_osm[str(r)] for r in rel_ids if str(r) in poligonos_osm]
        if not piezas:
            continue
        poly = unary_union(piezas)
        recortado = poly.intersection(partido)

        # Si casi todo el barrio cae fuera del partido (p.ej. Loma Hermosa,
        # que es sobre todo de Tres de Febrero), el recorte queda ínfimo y
        # es mejor mostrar un círculo aproximado que un pedacito confuso.
        if recortado.is_empty or recortado.area < 0.15 * poly.area:
            c = poly.centroid
            barrios[zona] = (c.buffer(RADIO_APROX_GRADOS), True)
            continue

        barrios[zona] = (recortado.simplify(0.00003, preserve_topology=True), False)
        cubierto.append(recortado)

    for zona, (lat, lon) in ZONA_A_PUNTO_APROX.items():
        barrios[zona] = (Point(lon, lat).buffer(RADIO_APROX_GRADOS), True)

    remanente = partido.difference(unary_union(cubierto)) if cubierto else partido
    barrios[ZONA_LEFTOVER] = (remanente.simplify(0.00005, preserve_topology=True), True)

    return barrios


def cargar_o_bajar_barrios() -> dict:
    if GEOJSON_CACHE.exists():
        print(f"[i] Usando polígonos cacheados en {GEOJSON_CACHE}")
        fc = json.loads(GEOJSON_CACHE.read_text(encoding="utf-8"))
        return {
            feat["properties"]["zona"]: (shape(feat["geometry"]), feat["properties"]["aprox"])
            for feat in fc["features"]
        }

    poligonos_osm = bajar_geometrias_osm()
    barrios = armar_barrios(poligonos_osm)

    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"zona": zona, "aprox": aprox},
                "geometry": mapping(poly),
            }
            for zona, (poly, aprox) in barrios.items()
        ],
    }
    GEOJSON_CACHE.write_text(json.dumps(fc, ensure_ascii=False), encoding="utf-8")
    print(f"[i] Guardado {GEOJSON_CACHE}")
    return barrios


# --------------------------------------------------------------------------
# CSV de avisos -> listado para el mapa
# --------------------------------------------------------------------------


def _ambientes(features: str):
    m = re.search(r"(\d+)\s*amb", features or "")
    return int(m.group(1)) if m else None


def cargar_avisos() -> list[dict]:
    if not CSV_AVISOS.exists():
        raise SystemExit(
            f"No encontré {CSV_AVISOS}. Corré primero sanmartin_mapa_m2.py para generarlo."
        )

    avisos = []
    with CSV_AVISOS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            amb = row.get("ambientes")
            amb = int(amb) if amb and amb.isdigit() else _ambientes(row.get("features", ""))
            if amb is None:
                continue
            try:
                precio = int(row["precio"])
                m2 = int(row["m2"])
                precio_m2 = int(row["precio_m2"])
            except (KeyError, ValueError):
                continue
            avisos.append(
                {
                    "zona": row["zona"],
                    "tipo": row["tipo"],
                    "ambientes": min(amb, 5),  # 5 = "5+"
                    "precio": precio,
                    "m2": m2,
                    "precio_m2": precio_m2,
                }
            )
    return avisos


# --------------------------------------------------------------------------
# Armado del HTML (Leaflet standalone, filtros client-side)
# --------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Alquileres en San Martín — mapa por barrio</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  #map { position: absolute; top: 0; bottom: 0; left: 0; right: 0; }
  .panel {
    position: absolute; z-index: 1000; background: rgba(255,255,255,0.96);
    border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,0.35); font-size: 13px;
  }
  #controles { top: 12px; left: 12px; padding: 12px 14px; min-width: 210px; }
  #controles h1 { font-size: 14px; margin: 0 0 8px; }
  #controles label { display: block; margin-top: 8px; font-weight: 600; color: #333; }
  #controles select { width: 100%; margin-top: 3px; padding: 4px; font-size: 13px; }
  #controles .conteo { margin-top: 10px; color: #555; font-size: 12px; }
  #leyenda { bottom: 22px; left: 12px; padding: 10px 14px; }
  #leyenda .titulo { font-weight: 700; margin-bottom: 6px; }
  #leyenda .barra { height: 10px; border-radius: 4px; background: linear-gradient(to right, #2b83ba, #ffffbf, #d7191c); margin-bottom: 4px; }
  #leyenda .rango { display: flex; justify-content: space-between; color: #555; }
  #leyenda .nota { margin-top: 6px; color: #777; font-size: 11px; max-width: 230px; }
  .barrio-tooltip { font-size: 12px; }
  .barrio-popup b { font-size: 13px; }
</style>
</head>
<body>
<div id="map"></div>

<div id="controles" class="panel">
  <h1>Alquileres — San Martín</h1>
  <label for="fTipo">Tipo de propiedad</label>
  <select id="fTipo">
    <option value="Todos">Todos</option>
    <option value="Departamento">Departamento</option>
    <option value="Casa">Casa</option>
    <option value="PH">PH</option>
  </select>

  <label for="fAmbientes">Ambientes</label>
  <select id="fAmbientes">
    <option value="Todos">Todos</option>
    <option value="1">1 ambiente</option>
    <option value="2">2 ambientes</option>
    <option value="3">3 ambientes</option>
    <option value="4">4 ambientes</option>
    <option value="5">5 o más</option>
  </select>

  <label for="fMetrica">Color según</label>
  <select id="fMetrica">
    <option value="precio">Alquiler mediana ($)</option>
    <option value="precio_m2">Precio por m² mediana ($/m²)</option>
  </select>

  <div class="conteo" id="conteoTotal"></div>
</div>

<div id="leyenda" class="panel">
  <div class="titulo" id="leyendaTitulo">Alquiler mediana ($)</div>
  <div class="barra"></div>
  <div class="rango"><span id="leyendaMin">-</span><span id="leyendaMax">-</span></div>
  <div class="nota">Gris = sin avisos para este filtro. Los barrios marcados "aprox." no tienen límite catastral oficial: se dibujan con un círculo o como área remanente del partido.</div>
</div>

<script>
const BARRIOS = __BARRIOS_GEOJSON__;
const AVISOS = __AVISOS_JSON__;

const map = L.map('map', { scrollWheelZoom: true }).setView([-34.5657, -58.5495], 13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  maxZoom: 19,
}).addTo(map);

function mediana(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function calcularStats() {
  const tipo = document.getElementById('fTipo').value;
  const amb = document.getElementById('fAmbientes').value;
  const filtrados = AVISOS.filter(a =>
    (tipo === 'Todos' || a.tipo === tipo) &&
    (amb === 'Todos' || String(a.ambientes) === amb)
  );
  const porZona = {};
  for (const a of filtrados) {
    (porZona[a.zona] ||= { precio: [], precio_m2: [] });
    porZona[a.zona].precio.push(a.precio);
    porZona[a.zona].precio_m2.push(a.precio_m2);
  }
  const stats = {};
  for (const [zona, v] of Object.entries(porZona)) {
    stats[zona] = {
      precio: Math.round(mediana(v.precio)),
      precio_m2: Math.round(mediana(v.precio_m2)),
      cantidad: v.precio.length,
    };
  }
  return { stats, total: filtrados.length };
}

function colorEscala(t) {
  t = Math.max(0, Math.min(1, t));
  if (t < 0.5) {
    const k = t / 0.5;
    return interp('#2b83ba', '#ffffbf', k);
  }
  const k = (t - 0.5) / 0.5;
  return interp('#ffffbf', '#d7191c', k);
}
function interp(c1, c2, k) {
  const a = hexToRgb(c1), b = hexToRgb(c2);
  const r = Math.round(a[0] + (b[0] - a[0]) * k);
  const g = Math.round(a[1] + (b[1] - a[1]) * k);
  const bl = Math.round(a[2] + (b[2] - a[2]) * k);
  return `rgb(${r},${g},${bl})`;
}
function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const fmt = new Intl.NumberFormat('es-AR');

let geoLayer = null;

function redibujar() {
  const { stats, total } = calcularStats();
  const metrica = document.getElementById('fMetrica').value;
  const valores = Object.values(stats).map(s => s[metrica]).filter(v => v != null);
  const min = valores.length ? Math.min(...valores) : 0;
  const max = valores.length ? Math.max(...valores) : 1;

  document.getElementById('conteoTotal').textContent = `${total} avisos con este filtro`;
  document.getElementById('leyendaTitulo').textContent =
    metrica === 'precio' ? 'Alquiler mediana ($)' : 'Precio por m² mediana ($/m²)';
  document.getElementById('leyendaMin').textContent = valores.length ? `$${fmt.format(min)}` : '-';
  document.getElementById('leyendaMax').textContent = valores.length ? `$${fmt.format(max)}` : '-';

  if (geoLayer) geoLayer.remove();

  geoLayer = L.geoJSON(BARRIOS, {
    style: feature => {
      const zona = feature.properties.zona;
      const s = stats[zona];
      if (!s) {
        return { color: '#999', weight: 1, fillColor: '#ccc', fillOpacity: 0.35, dashArray: feature.properties.aprox ? '4 3' : null };
      }
      const t = max === min ? 0.5 : (s[metrica] - min) / (max - min);
      return {
        color: feature.properties.aprox ? '#888' : '#444',
        weight: 1.3,
        dashArray: feature.properties.aprox ? '4 3' : null,
        fillColor: colorEscala(t),
        fillOpacity: 0.75,
      };
    },
    onEachFeature: (feature, layer) => {
      const zona = feature.properties.zona;
      const s = stats[zona];
      const aproxTxt = feature.properties.aprox
        ? '<br><i style="color:#888">área aproximada, sin límite catastral oficial</i>' : '';
      let html = `<b>${zona}</b>${aproxTxt}<br>`;
      html += s
        ? `Alquiler mediana: $${fmt.format(s.precio)}<br>` +
          `Precio x m² mediana: $${fmt.format(s.precio_m2)}/m²<br>` +
          `Avisos: ${s.cantidad}`
        : 'Sin avisos para este filtro';
      layer.bindPopup(html, { maxWidth: 260 });
      layer.bindTooltip(zona, { sticky: true, className: 'barrio-tooltip' });
    },
  }).addTo(map);
}

document.getElementById('fTipo').addEventListener('change', redibujar);
document.getElementById('fAmbientes').addEventListener('change', redibujar);
document.getElementById('fMetrica').addEventListener('change', redibujar);
redibujar();
</script>
</body>
</html>
"""


def armar_mapa(barrios: dict, avisos: list[dict]) -> None:
    fc = {
        "type": "FeatureCollection",
        "features": [
            {
                "type": "Feature",
                "properties": {"zona": zona, "aprox": aprox},
                "geometry": mapping(poly),
            }
            for zona, (poly, aprox) in barrios.items()
        ],
    }

    html = HTML_TEMPLATE.replace("__BARRIOS_GEOJSON__", json.dumps(fc, ensure_ascii=False))
    html = html.replace("__AVISOS_JSON__", json.dumps(avisos, ensure_ascii=False))
    MAPA_HTML.write_text(html, encoding="utf-8")
    print(f"[i] Mapa guardado en {MAPA_HTML} - abrilo con doble clic")


def main() -> None:
    avisos = cargar_avisos()
    print(f"[i] {len(avisos)} avisos cargados de {CSV_AVISOS}")

    barrios = cargar_o_bajar_barrios()
    print(f"[i] {len(barrios)} barrios con polígono")

    armar_mapa(barrios, avisos)


if __name__ == "__main__":
    main()
