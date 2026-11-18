"""
Saca tramos de un vídeo YA EMITIDO (VOD de Twitch o vídeo largo de YouTube) y los manda a OpenShorts, igual que el
grabador de directos. Lo lanza el orquestador cuando no hay nadie de GTA en directo y aún faltan clips para el día.

  python vod_tramos.py <url> --canal <login> --idioma es --tramos 6 [--tramo-min 20]

- Reparte N ventanas de --tramo-min minutos a lo largo del vídeo (salta los 10 primeros minutos, que suelen ser
  espera/intro, y los 5 últimos).
- Cada ventana se descarga con yt-dlp --download-sections (solo ese trozo, sin bajar el vídeo entero), se sube a
  OpenShorts (/api/uploads) y se procesa (/api/process) con la plantilla y opciones de siempre.
- Escribe datos/directos/<canal>_<tipo><id>/estado.json con el MISMO formato que el grabador, así el filtro, la
  plantilla de streamer, el archivo en Drive y los avisos funcionan sin cambios.

Corre dentro de un contenedor con la imagen openshorts-backend (yt-dlp, ffmpeg, requests).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

BACKEND = os.environ.get("OPENSHORTS_URL", "http://openshorts-backend:8000")
RAIZ_DATOS = Path(os.environ.get("DATOS_DIRECTOS", "/yc/datos/directos"))
MAX_BYTES = 1900 * 1024 * 1024


def ahora() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def duracion(url: str) -> float:
    r = subprocess.run(["yt-dlp", "--no-warnings", "--skip-download", "--print", "%(duration)s", url],
                       capture_output=True, text=True, timeout=180)
    try:
        return float(r.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        raise RuntimeError(f"no se pudo leer la duración: {r.stderr.strip()[-300:]}")


def ventanas(dur: float, n: int, largo: float) -> list[tuple[float, float]]:
    ini, fin = min(600.0, dur * 0.1), dur - min(300.0, dur * 0.05)
    util = fin - ini
    if util < largo:
        return [(max(0.0, dur - largo), dur)] if dur >= 180 else []
    n = max(1, min(n, int(util // largo)))
    paso = (util - largo) / max(1, n - 1) if n > 1 else 0
    return [(ini + i * paso, ini + i * paso + largo) for i in range(n)]


def hms(s: float) -> str:
    s = int(s)
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def enviar(archivo: Path, nombre: str, layouts: list[str]) -> str:
    r = requests.post(f"{BACKEND}/api/uploads", json={"filename": nombre}, timeout=30)
    r.raise_for_status()
    up = r.json()["upload_id"]
    with open(archivo, "rb") as f:
        requests.put(f"{BACKEND}/api/uploads/{up}", data=f, timeout=900).raise_for_status()
    r = requests.post(f"{BACKEND}/api/process", json={"upload_id": up, "acknowledged": True,
                                                       "output_format": "vertical", "layouts": layouts}, timeout=120)
    if r.status_code >= 400:
        raise RuntimeError(f"/api/process {r.status_code}: {r.text[:300]}")
    return r.json()["job_id"]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("url")
    ap.add_argument("--canal", required=True, help="login del streamer (para nombre y crédito)")
    ap.add_argument("--idioma", default="es", choices=["es", "en"])
    ap.add_argument("--tramos", type=int, default=6)
    ap.add_argument("--tramo-min", type=float, default=20)
    ap.add_argument("--formato", default="best[height<=1080]/best")
    a = ap.parse_args()

    m = re.search(r"twitch\.tv/videos/(\d+)", a.url) or re.search(r"[?&]v=([\w-]{6,})|youtu\.be/([\w-]{6,})", a.url)
    vid = next(g for g in m.groups() if g) if m else re.sub(r"\W+", "", a.url)[-12:]
    tipo = "vod" if "twitch.tv" in a.url else "yt"
    carpeta = RAIZ_DATOS / f"{a.canal.lower()}_{tipo}{vid}"
    (carpeta / "tramos").mkdir(parents=True, exist_ok=True)
    est_p = carpeta / "estado.json"
    estado = json.loads(est_p.read_text(encoding="utf-8")) if est_p.exists() else {
        "canal": a.canal.lower(), "url": a.url, "idioma": a.idioma, "origen": tipo, "video_id": vid,
        "creado": ahora(), "tramos": []}

    def guardar():
        estado["actualizado"] = ahora()
        est_p.write_text(json.dumps(estado, ensure_ascii=False, indent=2), encoding="utf-8")

    dur = duracion(a.url)
    hechas = {round(t["offset_s"]) for t in estado["tramos"]}
    plan = [(i, s, e) for i, (s, e) in enumerate(ventanas(dur, a.tramos, a.tramo_min * 60)) if round(s) not in hechas]
    print(f"[{ahora()}] {a.url} ({hms(dur)}): {len(plan)} tramos por procesar", flush=True)
    for i, s, e in plan:
        n = len(estado["tramos"])
        salida = carpeta / "tramos" / f"tramo_{n:03d}.mp4"
        r = subprocess.run(["yt-dlp", "--no-warnings", "--quiet", "--no-part", "-f", a.formato,
                            "--download-sections", f"*{hms(s)}-{hms(e)}", "--force-overwrites",
                            "--merge-output-format", "mp4", "-o", str(salida), a.url],
                           capture_output=True, text=True, timeout=3600)
        info = {"n": n, "archivo": salida.name, "offset_s": round(s), "dur_s": round(e - s), "creado": ahora(),
                "job_id": None, "estado": None}
        if r.returncode != 0 or not salida.exists():
            info["estado"] = f"error_descarga: {r.stderr.strip()[-200:]}"
            print(f"  tramo {n} ({hms(s)}-{hms(e)}): ERROR al descargar: {r.stderr.strip()[-200:]}", flush=True)
        elif salida.stat().st_size > MAX_BYTES:
            info["estado"] = "demasiado_grande"
            print(f"  tramo {n}: {salida.stat().st_size >> 20} MB, demasiado grande", flush=True)
        else:
            try:
                info["job_id"] = enviar(salida, f"{a.canal.lower()}_{tipo}{vid}_tramo{n:03d}.mp4", ["screencast"])
                info["estado"] = "enviado"
                print(f"  tramo {n} ({hms(s)}-{hms(e)}): enviado, job {info['job_id']}", flush=True)
            except Exception as ex:  # noqa: BLE001
                info["estado"] = f"error_envio: {ex}"
                print(f"  tramo {n}: ERROR al enviar: {ex}", flush=True)
        salida.unlink(missing_ok=True)  # OpenShorts guarda su copia; aquí no hace falta
        estado["tramos"].append(info)
        guardar()
    estado["terminado"] = ahora()
    guardar()
    print(f"[{ahora()}] fin: {sum(1 for t in estado['tramos'] if t.get('job_id'))} tramos enviados", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
