# Bot de alertas Zonaprop → Telegram

Chequea tu búsqueda de Zonaprop cada X minutos y te manda un Telegram por cada
publicación nueva. Guarda los IDs ya vistos en `vistos.json`.

## 1. Instalar

```bash
pip install requests beautifulsoup4
```

## 2. Crear el bot de Telegram

1. En Telegram, hablale a **@BotFather** → `/newbot` → elegí nombre y usuario.
   Te devuelve el token (`123456789:AAF...`).
2. Mandale un `/start` a tu bot nuevo (si no, no te puede escribir).
3. Sacá tu chat_id:

```bash
curl "https://api.telegram.org/bot<TU_TOKEN>/getUpdates"
```

Buscá `"chat":{"id":123456789`. Ese número es tu `TELEGRAM_CHAT_ID`.

## 3. Configurar

```bash
export TELEGRAM_BOT_TOKEN="123456789:AAF..."
export TELEGRAM_CHAT_ID="123456789"
```

## 4. Primera corrida (importante)

```bash
python zonaprop_bot.py --seed
```

Esto guarda las ~60 publicaciones que ya están sin mandarte 60 mensajes.
De ahí en más, corré sin `--seed` y solo te avisa lo nuevo.

## 5. Automatizar

**Opción A — cron en tu máquina / VPS** (recomendada: IP residencial, menos
chance de que Zonaprop te bloquee):

```cron
*/20 * * * * cd /ruta/al/bot && TELEGRAM_BOT_TOKEN=xxx TELEGRAM_CHAT_ID=yyy /usr/bin/python3 zonaprop_bot.py >> bot.log 2>&1
```

**Opción B — GitHub Actions** (gratis, sin máquina prendida). Ver
`.github/workflows/zonaprop.yml`. Ojo: corre desde IPs de datacenter, que
Zonaprop a veces bloquea (403). Si pasa, volvé a la opción A o metele un proxy.

**Opción C — Cloud Run Job + Cloud Scheduler** en tu GCP, con el estado en un
bucket de GCS en vez de `vistos.json` local.

## Ajustes

Arriba de `zonaprop_bot.py`:

- `SEARCH_SLUG`: pegá otra búsqueda de Zonaprop (sacale el `-pagina-N` y dejá
  siempre `-orden-publicado-descendente`).
- `PAGES`: cuántas páginas revisar. Con 1 alcanza si corrés cada 20 min.
- `MAX_EXPENSAS`, `MAX_TOTAL`: filtros que Zonaprop no te deja poner. Útil
  cuando el alquiler es barato pero las expensas te matan.
- `EXCLUIR_PALABRAS`: descarta por título (uso comercial, temporal, amoblado…).

## Si deja de encontrar publicaciones

Zonaprop cambia el DOM cada tanto. Corré:

```bash
python zonaprop_bot.py --dump
```

y revisá `dump_pagina1.html` para actualizar los selectores `data-qa` en
`parse_cards()`. El link del aviso (`/propiedades/clasificado/...-<id>.html`)
es lo único que casi nunca cambia, y por eso hay un fallback que se apoya solo
en eso: si los `data-qa` se rompen, igual te sigue avisando, pero sin precio.
