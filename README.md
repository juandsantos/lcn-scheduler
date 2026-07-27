# Bot para agendar clases en LCN

Este proyecto intenta agendar clases en tu cuenta de LCN usando siempre la hora de Colombia. En cada ejecucion prueba manana y pasado manana, saltando domingos.

Cuando la fecha objetivo es sabado, usa horarios especiales: 9:45 AM, 11:15 AM, 1:30 PM y 3:00 PM. Por eso, el jueves intenta viernes con horario normal y sabado con horario especial. Si corre un sabado, salta domingo e intenta lunes.

Usalo solo con tu propia cuenta y respetando las reglas de LCN. La idea es revisar con una frecuencia razonable, no saturar el portal ni saltarse captchas, bloqueos o limites.

## Instalacion local

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m playwright install chromium
Copy-Item .env.example .env
```

Edita `.env` y pon tu correo, contrasena y preferencias.

## Probar en tu computador

Para ver lo que hace en el navegador, pon esto en `.env`:

```env
LCN_HEADLESS=false
```

Luego ejecuta:

```powershell
.\.venv\Scripts\python.exe lcn_scheduler.py
```

Si quieres que se quede revisando en modo continuo:

```powershell
$env:LCN_CONTINUOUS="true"
.\.venv\Scripts\python.exe lcn_scheduler.py
```

## Ejecutarlo gratis en GitHub Actions

GitHub Actions puede correrlo gratis en un repositorio privado, aunque los cron no son exactos al segundo y pueden retrasarse unos minutos.

1. Sube este proyecto a un repositorio privado de GitHub.
2. En `Settings > Secrets and variables > Actions`, crea estos secrets:
   - `LCN_EMAIL`
   - `LCN_PASSWORD`
3. En `Variables`, puedes crear:
   - `LCN_MAX_CLASSES`
   - `LCN_PREFERRED_TIMES`
4. Activa Actions.

La tarea incluida corre cada 10 minutos entre 6:00 AM y 7:10 PM, hora de Colombia. GitHub Actions puede retrasar los cron unos minutos; para mayor puntualidad, lo mas confiable sigue siendo correrlo localmente o en un VPS barato.

## Ajustes importantes

- `LCN_MAX_CLASSES`: cantidad maxima de clases por ejecucion. Usa `99` para intentar todas las horas configuradas.
- `LCN_PREFERRED_TIMES`: orden de horarios preferidos.
- `LCN_SATURDAY_TIMES`: horarios especiales para fechas objetivo que caen sabado.
- `LCN_RUN_START`: hora Colombia desde la que puede correr en modo continuo. Por defecto `06:00`.
- `LCN_RUN_END`: ultimo intento permitido en modo continuo. Por defecto `19:10`.
- `LCN_POLL_SECONDS`: intervalo de revision cuando lo corres en modo continuo. Por defecto `600` segundos.
- `LCN_HEADLESS`: `false` para depurar en tu PC, `true` para servidor.

Si LCN cambia textos, campos o ventanas de confirmacion, puede que haya que ajustar selectores en `lcn_scheduler.py`.
