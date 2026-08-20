#!/usr/bin/env python3
"""
Mapa de alquileres en San Martín con la ubicación de cada aviso (pin exacto
para Zonaprop, dirección geocodificada para Argenprop) + los polígonos de
barrio como capa de fondo + evolución histórica de precios por barrio, con
filtros de Tipo / Ambientes / Fuente / Métrica que recalculan todo en el
navegador.

Uso:
    python sanmartin_mapa_geo.py

Requiere:
    san_martin_avisos_combinado.csv -> generado por sanmartin_merge_fuentes.py
                                        (combina Zonaprop + Argenprop) y
                                        enriquecido por sanmartin_geomatch.py
    san_martin_barrios.geojson      -> opcional (si no está, el mapa se arma
                                        igual, sin los polígonos de fondo)
    san_martin_historico.csv        -> opcional, generado por
                                        sanmartin_historico.py

Salida:
    san_martin_mapa_geo.html    -> abrilo con doble clic
"""

import csv
import json
from pathlib import Path

CSV_AVISOS = Path("san_martin_avisos_combinado.csv")
GEOJSON_BARRIOS = Path("san_martin_barrios.geojson")
CSV_HISTORICO = Path("san_martin_historico.csv")
MAPA_HTML = Path("san_martin_mapa_geo.html")


def cargar_avisos() -> list[dict]:
    if not CSV_AVISOS.exists():
        raise SystemExit(
            f"No encontré {CSV_AVISOS}. Corré primero sanmartin_merge_fuentes.py."
        )
    avisos = []
    with CSV_AVISOS.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                lat, lon = float(row["lat"]), float(row["lon"])
                precio = int(row["precio"])
                m2 = int(row["m2"])
                precio_m2 = int(row["precio_m2"])
                ambientes = int(row["ambientes"]) if row["ambientes"] else None
            except (KeyError, ValueError):
                continue
            if ambientes is None:
                continue
            avisos.append(
                {
                    "zona": row.get("zona_geo") or row["zona"],
                    "zona_zonaprop": row["zona"],
                    "tipo": row["tipo"],
                    "ambientes": min(ambientes, 5),
                    "precio": precio,
                    "m2": m2,
                    "precio_m2": precio_m2,
                    "direccion": row.get("direccion", ""),
                    "url": row.get("url", ""),
                    "fuente": row.get("fuente") or "Zonaprop",
                    "aprox": row.get("visibilidad") == "APPROX",
                    "lat": lat,
                    "lon": lon,
                }
            )
    return avisos


def cargar_barrios():
    if not GEOJSON_BARRIOS.exists():
        return None
    return json.loads(GEOJSON_BARRIOS.read_text(encoding="utf-8"))


def cargar_historico() -> list[dict]:
    if not CSV_HISTORICO.exists():
        return []
    filas = []
    with CSV_HISTORICO.open(encoding="utf-8") as f:
        for row in csv.DictReader(f):
            try:
                filas.append(
                    {
                        "fecha": row["fecha"],
                        "zona": row["zona"],
                        "tipo": row["tipo"],
                        "cantidad": int(row["cantidad"]),
                        "precio": int(row["precio_mediana"]),
                        "precio_m2": int(row["precio_m2_mediana"]),
                    }
                )
            except (KeyError, ValueError):
                continue
    return filas


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Alquileres en San Martín — mapa geolocalizado</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4"></script>
<style>
  html, body { margin: 0; height: 100%; font-family: system-ui, -apple-system, "Segoe UI", sans-serif; }
  #map { position: absolute; top: 0; bottom: 0; left: 0; right: 0; }
  .panel {
    position: absolute; z-index: 1000; background: rgba(255,255,255,0.96);
    border-radius: 8px; box-shadow: 0 1px 6px rgba(0,0,0,0.35); font-size: 13px;
  }
  #controles { top: 12px; left: 12px; padding: 12px 14px; min-width: 220px; }
  #controles h1 { font-size: 14px; margin: 0 0 8px; }
  #controles label { display: block; margin-top: 8px; font-weight: 600; color: #333; }
  #controles select { width: 100%; margin-top: 3px; padding: 4px; font-size: 13px; }
  #controles .fila-check { margin-top: 10px; display: flex; align-items: center; gap: 6px; font-weight: 600; color: #333; }
  #controles .conteo { margin-top: 10px; color: #555; font-size: 12px; }
  #leyenda { bottom: 22px; left: 12px; padding: 10px 14px; }
  #leyenda .titulo { font-weight: 700; margin-bottom: 6px; }
  #leyenda .barra { height: 10px; border-radius: 4px; background: linear-gradient(to right, #2b83ba, #ffffbf, #d7191c); margin-bottom: 4px; }
  #leyenda .rango { display: flex; justify-content: space-between; color: #555; }
  #leyenda .nota { margin-top: 6px; color: #777; font-size: 11px; max-width: 230px; }
  .aviso-popup b { font-size: 13px; }
  .aviso-popup a { color: #1f6feb; }
  #evolucion { top: 12px; right: 12px; padding: 12px 14px; width: 280px; }
  #evolucion h1 { font-size: 14px; margin: 0 0 8px; }
  #evolucion label { display: block; margin-top: 6px; font-weight: 600; color: #333; font-size: 12px; }
  #evolucion select { width: 100%; margin-top: 3px; padding: 4px; font-size: 13px; }
  #evolucion .sin-datos { margin-top: 10px; color: #777; font-size: 12px; }
  #evChartWrap { margin-top: 10px; height: 150px; }

  #panelToggle {
    display: none; position: absolute; z-index: 1200; top: 12px; left: 12px;
    width: 42px; height: 42px; border: none; border-radius: 8px; background: #fff;
    box-shadow: 0 1px 6px rgba(0,0,0,0.35); font-size: 20px; line-height: 1; cursor: pointer;
  }
  #scrim {
    display: none; position: absolute; inset: 0; z-index: 900; background: rgba(0,0,0,0.25);
  }

  @media (max-width: 680px) {
    #panelToggle { display: block; }
    #panelSidebar {
      position: fixed; top: 0; left: 0; bottom: 0; z-index: 1100;
      width: min(88vw, 320px); padding: 62px 10px 16px; overflow-y: auto;
      -webkit-overflow-scrolling: touch;
      transform: translateX(-105%); transition: transform 0.2s ease;
    }
    body.panels-open #panelSidebar { transform: translateX(0); }
    body.panels-open #scrim { display: block; }
    #panelSidebar .panel {
      position: static !important; width: auto !important; min-width: 0 !important;
      max-width: none !important; margin-bottom: 10px; padding: 12px !important;
    }
    #leyenda .nota { max-width: none; }
    .leaflet-popup-content-wrapper { max-width: 78vw; }
  }
</style>
</head>
<body>
<div id="map"></div>
<button id="panelToggle" aria-label="Mostrar filtros">☰</button>
<div id="scrim"></div>

<div id="panelSidebar">
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

  <label for="fFuente">Fuente</label>
  <select id="fFuente">
    <option value="Todos">Todas (Zonaprop + Argenprop)</option>
    <option value="Zonaprop">Solo Zonaprop</option>
    <option value="Argenprop">Solo Argenprop</option>
  </select>

  <label for="fMetrica">Color según</label>
  <select id="fMetrica">
    <option value="precio">Alquiler ($)</option>
    <option value="precio_m2">Precio por m² ($/m²)</option>
  </select>

  <div class="fila-check">
    <input type="checkbox" id="fPuntos" checked>
    <label for="fPuntos" style="margin:0">Mostrar avisos (puntos)</label>
  </div>

  <div class="fila-check">
    <input type="checkbox" id="fBarrios" checked>
    <label for="fBarrios" style="margin:0">Mostrar barrios (fondo)</label>
  </div>

  <div class="conteo" id="conteoTotal"></div>
</div>

<div id="leyenda" class="panel">
  <div class="titulo" id="leyendaTitulo">Alquiler ($)</div>
  <div class="barra"></div>
  <div class="rango"><span id="leyendaMin">-</span><span id="leyendaMax">-</span></div>
  <div class="nota">Los avisos de Zonaprop usan el pin exacto que informa el sitio. Los de Argenprop (borde punteado) están ubicados por dirección aproximada, geocodificada, ya que ese sitio no publica el pin exacto en el listado.</div>
</div>

<div id="evolucion" class="panel">
  <h1>Evolución por barrio</h1>
  <label for="evZona">Barrio</label>
  <select id="evZona"></select>
  <label for="evMetrica">Métrica</label>
  <select id="evMetrica">
    <option value="precio">Alquiler ($), mediana</option>
    <option value="precio_m2">Precio por m² ($/m²), mediana</option>
  </select>
  <div id="evChartWrap"><canvas id="evChart"></canvas></div>
  <div class="sin-datos" id="evSinDatos" style="display:none">
    Todavía no hay histórico guardado para este barrio. Se va acumulando un
    punto por corrida del scraper (ver sanmartin_historico.py).
  </div>
</div>
</div>

<script>
const AVISOS = __AVISOS_JSON__;
const BARRIOS = __BARRIOS_GEOJSON__;
const HISTORICO = __HISTORICO_JSON__;

const map = L.map('map', { scrollWheelZoom: true, preferCanvas: true }).setView([-34.5657, -58.5495], 13);
L.tileLayer('https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
  attribution: '&copy; OpenStreetMap contributors &copy; CARTO',
  maxZoom: 19,
}).addTo(map);

// --- Cajón de filtros en mobile (< 680px) ---
document.getElementById('panelToggle').addEventListener('click', () => {
  document.body.classList.toggle('panels-open');
});
document.getElementById('scrim').addEventListener('click', () => {
  document.body.classList.remove('panels-open');
});

function mediana(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const mid = Math.floor(s.length / 2);
  return s.length % 2 ? s[mid] : (s[mid - 1] + s[mid]) / 2;
}

function colorEscala(t) {
  t = Math.max(0, Math.min(1, t));
  if (t < 0.5) return interp('#2b83ba', '#ffffbf', t / 0.5);
  return interp('#ffffbf', '#d7191c', (t - 0.5) / 0.5);
}
function interp(c1, c2, k) {
  const a = hexToRgb(c1), b = hexToRgb(c2);
  return `rgb(${Math.round(a[0]+(b[0]-a[0])*k)},${Math.round(a[1]+(b[1]-a[1])*k)},${Math.round(a[2]+(b[2]-a[2])*k)})`;
}
function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

const fmt = new Intl.NumberFormat('es-AR');
const puntosLayer = L.layerGroup();
let barriosLayer = null;

function filtrarAvisos() {
  const tipo = document.getElementById('fTipo').value;
  const amb = document.getElementById('fAmbientes').value;
  const fuente = document.getElementById('fFuente').value;
  return AVISOS.filter(a =>
    (tipo === 'Todos' || a.tipo === tipo) &&
    (amb === 'Todos' || String(a.ambientes) === amb) &&
    (fuente === 'Todos' || a.fuente === fuente)
  );
}

function statsPorZona(filtrados) {
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
  return stats;
}

function redibujar() {
  const filtrados = filtrarAvisos();
  const metrica = document.getElementById('fMetrica').value;
  const valores = filtrados.map(a => a[metrica]);
  const min = valores.length ? Math.min(...valores) : 0;
  const max = valores.length ? Math.max(...valores) : 1;

  document.getElementById('conteoTotal').textContent = `${filtrados.length} avisos con este filtro`;
  document.getElementById('leyendaTitulo').textContent =
    metrica === 'precio' ? 'Alquiler ($)' : 'Precio por m² ($/m²)';
  document.getElementById('leyendaMin').textContent = valores.length ? `$${fmt.format(min)}` : '-';
  document.getElementById('leyendaMax').textContent = valores.length ? `$${fmt.format(max)}` : '-';

  puntosLayer.clearLayers();
  for (const a of filtrados) {
    const t = max === min ? 0.5 : (a[metrica] - min) / (max - min);
    const marker = L.circleMarker([a.lat, a.lon], {
      radius: 6,
      weight: 1,
      color: '#333',
      fillColor: colorEscala(t),
      fillOpacity: 0.85,
      dashArray: a.aprox ? '3 2' : null,
    });
    const link = a.url ? `<br><a href="${a.url}" target="_blank" rel="noopener">Ver aviso en ${a.fuente} →</a>` : '';
    const zonaLinea = a.zona_zonaprop && a.zona_zonaprop !== a.zona
      ? `Barrio: ${a.zona} <span style="color:#888">(${a.fuente} lo publicó como "${a.zona_zonaprop}")</span>`
      : `Barrio: ${a.zona}`;
    const ubicacionNota = a.aprox
      ? `<br><span style="color:#888;font-size:11px">Ubicación aproximada (dirección geocodificada, no es el pin exacto)</span>`
      : '';
    marker.bindPopup(
      `<b>${a.direccion || a.zona}</b> <span style="color:#888;font-weight:400">· ${a.fuente}</span><br>` +
      `${a.tipo} · ${a.ambientes}${a.ambientes >= 5 ? '+' : ''} amb · ${a.m2} m²<br>` +
      `$${fmt.format(a.precio)} (\$${fmt.format(a.precio_m2)}/m²)<br>` +
      `${zonaLinea}${link}${ubicacionNota}`,
      { maxWidth: 260 }
    );
    marker.bindTooltip(`$${fmt.format(a[metrica])}${metrica === 'precio_m2' ? '/m²' : ''}`);
    marker.addTo(puntosLayer);
  }

  if (barriosLayer) { barriosLayer.remove(); barriosLayer = null; }
  if (BARRIOS && document.getElementById('fBarrios').checked) {
    const stats = statsPorZona(filtrados);
    barriosLayer = L.geoJSON(BARRIOS, {
      style: feature => {
        const s = stats[feature.properties.zona];
        if (!s) return { color: '#aaa', weight: 1, fillColor: '#ddd', fillOpacity: 0.15, dashArray: feature.properties.aprox ? '4 3' : null };
        const t = max === min ? 0.5 : (s[metrica] - min) / (max - min);
        return { color: '#666', weight: 1, fillColor: colorEscala(t), fillOpacity: 0.25, dashArray: feature.properties.aprox ? '4 3' : null };
      },
      onEachFeature: (feature, layer) => {
        layer.bindTooltip(feature.properties.zona, { sticky: true });
        layer.on('click', () => seleccionarZonaEvolucion(feature.properties.zona));
      },
    });
    barriosLayer.addTo(map);
    barriosLayer.bringToBack();
  }

  if (document.getElementById('fPuntos').checked) {
    puntosLayer.addTo(map);
  } else {
    puntosLayer.remove();
  }
}

document.getElementById('fTipo').addEventListener('change', redibujar);
document.getElementById('fAmbientes').addEventListener('change', redibujar);
document.getElementById('fFuente').addEventListener('change', redibujar);
document.getElementById('fMetrica').addEventListener('change', redibujar);
document.getElementById('fPuntos').addEventListener('change', redibujar);
document.getElementById('fBarrios').addEventListener('change', redibujar);
redibujar();

// --- Panel de evolución histórica por barrio ---
const ZONA_TODAS = '__todas__';
const TEXTO_SIN_DATOS_DEFAULT = 'Todavía no hay histórico guardado para este barrio. Se va acumulando un punto por corrida del scraper (ver sanmartin_historico.py).';
const zonasConHistorico = [...new Set(HISTORICO.filter(h => h.tipo === 'Todos').map(h => h.zona))]
  .filter(z => z !== ZONA_TODAS)
  .sort((a, b) => a.localeCompare(b, 'es'));

const evZonaSelect = document.getElementById('evZona');
const optTodas = document.createElement('option');
optTodas.value = ZONA_TODAS;
optTodas.textContent = 'Todos los barrios (San Martín)';
evZonaSelect.appendChild(optTodas);
for (const z of zonasConHistorico) {
  const opt = document.createElement('option');
  opt.value = z;
  opt.textContent = z;
  evZonaSelect.appendChild(opt);
}
evZonaSelect.value = ZONA_TODAS;

let evChart = null;
function redibujarEvolucion() {
  const zona = evZonaSelect.value;
  const metrica = document.getElementById('evMetrica').value;
  const serie = HISTORICO
    .filter(h => h.zona === zona && h.tipo === 'Todos')
    .sort((a, b) => a.fecha.localeCompare(b.fecha));

  const sinDatos = document.getElementById('evSinDatos');
  const wrap = document.getElementById('evChartWrap');
  if (!serie.length) {
    sinDatos.textContent = TEXTO_SIN_DATOS_DEFAULT;
    sinDatos.style.display = 'block';
    wrap.style.display = 'none';
    return;
  }
  sinDatos.style.display = 'none';
  wrap.style.display = 'block';

  const labels = serie.map(s => s.fecha);
  const valores = serie.map(s => s[metrica]);
  const cantidades = serie.map(s => s.cantidad);

  if (evChart) evChart.destroy();
  evChart = new Chart(document.getElementById('evChart'), {
    type: 'line',
    data: {
      labels,
      datasets: [{
        label: metrica === 'precio' ? 'Alquiler ($)' : 'Precio por m² ($/m²)',
        data: valores,
        borderColor: '#1f6feb',
        backgroundColor: 'rgba(31,111,235,0.15)',
        tension: 0.2,
        fill: true,
        pointRadius: 3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => `${cantidades[ctx.dataIndex]} avisos ese día`,
          },
        },
      },
      scales: {
        y: { ticks: { callback: (v) => fmt.format(v) } },
      },
    },
  });
}

function seleccionarZonaEvolucion(zona) {
  const tieneOpcion = [...evZonaSelect.options].some(o => o.value === zona);
  if (tieneOpcion) {
    evZonaSelect.value = zona;
    redibujarEvolucion();
  } else {
    document.getElementById('evChartWrap').style.display = 'none';
    const sinDatos = document.getElementById('evSinDatos');
    sinDatos.textContent = `Todavía no hay avisos registrados en "${zona}" para armar un histórico.`;
    sinDatos.style.display = 'block';
  }
  if (window.innerWidth <= 680) document.body.classList.add('panels-open');
  document.getElementById('evolucion').scrollIntoView({ block: 'nearest' });
}

evZonaSelect.addEventListener('change', redibujarEvolucion);
document.getElementById('evMetrica').addEventListener('change', redibujarEvolucion);
redibujarEvolucion();
</script>
</body>
</html>
"""


def armar_mapa(avisos: list[dict], barrios_geojson, historico: list[dict]) -> None:
    html = HTML_TEMPLATE.replace("__AVISOS_JSON__", json.dumps(avisos, ensure_ascii=False))
    html = html.replace(
        "__BARRIOS_GEOJSON__",
        json.dumps(barrios_geojson, ensure_ascii=False) if barrios_geojson else "null",
    )
    html = html.replace("__HISTORICO_JSON__", json.dumps(historico, ensure_ascii=False))
    MAPA_HTML.write_text(html, encoding="utf-8")
    print(f"[i] Mapa guardado en {MAPA_HTML} - abrilo con doble clic")


def main() -> None:
    avisos = cargar_avisos()
    print(f"[i] {len(avisos)} avisos geolocalizados cargados de {CSV_AVISOS}")
    barrios = cargar_barrios()
    if barrios:
        print(f"[i] Usando polígonos de barrio de {GEOJSON_BARRIOS} como fondo")
    else:
        print(f"[i] No encontré {GEOJSON_BARRIOS}; el mapa se arma sin fondo de barrios")
    historico = cargar_historico()
    if historico:
        print(f"[i] {len(historico)} filas de histórico cargadas de {CSV_HISTORICO}")
    else:
        print(f"[i] No encontré {CSV_HISTORICO}; el panel de evolución queda vacío por ahora")
    armar_mapa(avisos, barrios, historico)


if __name__ == "__main__":
    main()
