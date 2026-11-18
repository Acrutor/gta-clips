"""
Grabador de directos por tramos -> OpenShorts, sin esperar a que el directo termine.

Cómo funciona
  1. yt-dlp lee el directo (Twitch / YouTube / Kick) y ffmpeg lo corta SIN recodificar en
     segmentos de SEG_S segundos (.ts, aguantan cortes de red sin corromperse).
  2. Cada TRAMO_MIN minutos se unen los segmentos en un .mp4 que se solapa SOLAPE_MIN con el
     anterior (para no perder una jugada que caiga justo en el corte).
  3. El tramo se sube a OpenShorts (/api/uploads + PUT) y se manda a procesar (/api/process).
  4. Si el directo se cae se reintenta; si sigue apagado ESPERA_FIN_MIN minutos, se envía el último
     tramo y se termina. También se para creando el archivo PARAR en la carpeta del canal.

Se ejecuta DENTRO de un contenedor con la imagen openshorts-backend (trae yt-dlp, ffmpeg, requests)
en la red de docker de OpenShorts. Usar iniciar_grabador.ps1, no lanzarlo a mano.

Datos en /yc/datos/directos/<canal>/ (= E:\\YoutubeClips\\datos\\directos\\<canal>):
  segmentos/  tramos/  estado.json  grabador.log
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import requests

BACKEND = os.environ.get("OPENSHORTS_URL", "http://openshorts-backend:8000")
RAIZ_DATOS = Path(os.environ.get("DATOS_DIRECTOS", "/yc/datos/directos"))
MAX_BYTES_SUBIDA = 1900 * 1024 * 1024  # OpenShorts admite 2048 MB; margen de seguridad
TRAMO_MINIMO_FINAL_S = 180            # el último trozo se envía solo si dura al menos esto

parar = False


def _senal(*_):
    global parar
    parar = True


signal.signal(signal.SIGTERM, _senal)
signal.signal(signal.SIGINT, _senal)


def ahora() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class Grabador:
    def __init__(self, a: argparse.Namespace):
        self.a = a
        self.url = a.canal if a.canal.startswith("http") else f"https://www.twitch.tv/{a.canal}"
        nombre = re.sub(r"[^A-Za-z0-9_-]+", "_", a.canal.rstrip("/").split("/")[-1])[:40]
        # Una carpeta por canal y día (los tramos de días distintos no se mezclan). La antigua sin fecha se respeta.
        self.dir = RAIZ_DATOS / f"{nombre}_{dt.datetime.now():%Y%m%d}"
        self.seg_dir = self.dir / "segmentos"
        self.tramo_dir = self.dir / "tramos"
        for d in (self.seg_dir, self.tramo_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.log_f = open(self.dir / "grabador.log", "a", encoding="utf-8")
        self.estado_path = self.dir / "estado.json"
        self.estado = self._cargar_estado()
        self.seg_por_tramo = max(2, round(a.tramo_min * 60 / a.seg_s))
        self.seg_solape = max(0, round(a.solape_min * 60 / a.seg_s))
        self.proc_ytdlp = self.proc_ffmpeg = None
        self.lista_csv = None

    # ------------------------------------------------------------------ utilidades
    def log(self, msg: str) -> None:
        linea = f"[{ahora()}] {msg}"
        print(linea, flush=True)
        self.log_f.write(linea + "\n"); self.log_f.flush()

    def _cargar_estado(self) -> dict:
        if self.estado_path.exists():
            return json.loads(self.estado_path.read_text(encoding="utf-8"))
        return {"canal": self.a.canal, "url": None, "idioma": self.a.idioma, "creado": ahora(),
                "segmentos": [], "siguiente_seg": 0, "tramos": [], "siguiente_inicio": 0}

    def guardar(self) -> None:
        self.estado["url"] = self.url
        self.estado["actualizado"] = ahora()
        tmp = self.estado_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.estado, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.estado_path)

    # ------------------------------------------------------------------ grabación
    def arrancar(self) -> bool:
        """Lanza yt-dlp | ffmpeg. Devuelve False si el canal no está en directo."""
        inicio = self.estado["siguiente_seg"]
        self.lista_csv = self.dir / f"lista_{inicio:05d}.csv"
        ytdlp = ["yt-dlp", "--no-warnings", "--quiet", "--no-part", "-f", self.a.formato, "-o", "-", self.url]
        ffmpeg = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", "pipe:0", "-map", "0", "-c", "copy",
                  "-f", "segment", "-segment_time", str(self.a.seg_s), "-segment_format", "mpegts",
                  "-segment_start_number", str(inicio), "-reset_timestamps", "1",
                  "-segment_list", str(self.lista_csv), "-segment_list_type", "csv",
                  str(self.seg_dir / "seg_%05d.ts")]
        err = open(self.dir / "ytdlp_ffmpeg.err", "a", encoding="utf-8")
        self.proc_ytdlp = subprocess.Popen(ytdlp, stdout=subprocess.PIPE, stderr=err)
        self.proc_ffmpeg = subprocess.Popen(ffmpeg, stdin=self.proc_ytdlp.stdout, stderr=err)
        self.proc_ytdlp.stdout.close()  # que ffmpeg reciba EOF si yt-dlp muere
        # Si en 40 s no ha salido nada, el canal no está en directo (o falló la conexión).
        t0 = time.time()
        while time.time() - t0 < 40:
            if any(self.seg_dir.glob("seg_*.ts")) and self._bytes_nuevos(inicio):
                self.log(f"grabando {self.url} (formato {self.a.formato}) desde el segmento {inicio}")
                return True
            if self.proc_ytdlp.poll() is not None and self.proc_ffmpeg.poll() is not None:
                break
            time.sleep(2)
        self.cortar_procesos()
        return False

    def _bytes_nuevos(self, inicio: int) -> bool:
        p = self.seg_dir / f"seg_{inicio:05d}.ts"
        return p.exists() and p.stat().st_size > 0

    def cortar_procesos(self) -> None:
        for p in (self.proc_ytdlp, self.proc_ffmpeg):
            if p and p.poll() is None:
                p.terminate()
        for p in (self.proc_ytdlp, self.proc_ffmpeg):
            if p:
                try:
                    p.wait(timeout=20)
                except subprocess.TimeoutExpired:
                    p.kill()

    def grabando(self) -> bool:
        return bool(self.proc_ffmpeg and self.proc_ffmpeg.poll() is None)

    def leer_segmentos_cerrados(self) -> None:
        """ffmpeg apunta en el CSV cada segmento cuando lo cierra: esos ya se pueden usar."""
        if not self.lista_csv or not self.lista_csv.exists():
            return
        conocidos = {s["archivo"] for s in self.estado["segmentos"]}
        with open(self.lista_csv, newline="", encoding="utf-8") as f:
            for fila in csv.reader(f):
                if len(fila) < 3 or fila[0] in conocidos:
                    continue
                dur = max(0.0, float(fila[2]) - float(fila[1]))
                n = int(re.search(r"(\d+)", fila[0]).group(1))
                self.estado["segmentos"].append({"archivo": fila[0], "n": n, "dur": round(dur, 2), "cerrado": ahora()})
                self.estado["siguiente_seg"] = max(self.estado["siguiente_seg"], n + 1)
        self.estado["segmentos"].sort(key=lambda s: s["n"])
        self.guardar()

    # ------------------------------------------------------------------ tramos
    def tramos_listos(self, final: bool = False) -> None:
        segs = self.estado["segmentos"]
        while True:
            i = self.estado["siguiente_inicio"]
            pendientes = segs[i:]
            if len(pendientes) >= self.seg_por_tramo:
                trozo = pendientes[: self.seg_por_tramo]
                siguiente = i + self.seg_por_tramo - self.seg_solape
            elif final and pendientes and sum(s["dur"] for s in pendientes) >= TRAMO_MINIMO_FINAL_S \
                    and (not self.estado["tramos"] or pendientes[-1]["n"] > self.estado["tramos"][-1]["seg_fin"]):
                trozo = pendientes
                siguiente = len(segs)
            else:
                return
            self.crear_y_enviar(trozo)
            self.estado["siguiente_inicio"] = siguiente
            self.guardar()
            self.limpiar_segmentos()
            if self.a.max_tramos and len(self.estado["tramos"]) >= self.a.max_tramos:
                global parar
                parar = True
                self.log(f"alcanzado --max-tramos={self.a.max_tramos}")
                return

    def crear_y_enviar(self, trozo: list[dict]) -> None:
        segs = self.estado["segmentos"]
        idx = len(self.estado["tramos"])
        offset = sum(s["dur"] for s in segs if s["n"] < trozo[0]["n"])
        dur = sum(s["dur"] for s in trozo)
        salida = self.tramo_dir / f"tramo_{idx:03d}.mp4"
        entradas = "|".join(str(self.seg_dir / s["archivo"]) for s in trozo)
        r = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", f"concat:{entradas}",
                            "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-bsf:a", "aac_adtstoasc",
                            "-movflags", "+faststart", str(salida)], capture_output=True, text=True)
        info = {"n": idx, "archivo": salida.name, "seg_ini": trozo[0]["n"], "seg_fin": trozo[-1]["n"],
                "offset_s": round(offset), "dur_s": round(dur), "creado": ahora(), "job_id": None, "estado": None}
        if r.returncode != 0 or not salida.exists():
            info["estado"] = f"error_ffmpeg: {r.stderr[-300:]}"
            self.log(f"tramo {idx}: ERROR al unir segmentos: {r.stderr[-300:]}")
        elif salida.stat().st_size > MAX_BYTES_SUBIDA:
            info["estado"] = "demasiado_grande"
            self.log(f"tramo {idx}: {salida.stat().st_size >> 20} MB supera el límite de subida; baja --tramo-min o el formato")
        elif self.a.no_procesar:
            info["estado"] = "solo_grabado"
            self.log(f"tramo {idx}: {salida.name} ({round(dur/60,1)} min, {salida.stat().st_size >> 20} MB) "
                     f"offset {round(offset/60,1)} min [--no-procesar]")
        else:
            try:
                info["job_id"] = self.enviar(salida, idx)
                info["estado"] = "enviado"
                self.log(f"tramo {idx}: enviado a OpenShorts, job {info['job_id']} "
                         f"({round(dur/60,1)} min, offset {round(offset/60,1)} min)")
                if not self.a.conservar_tramos:
                    salida.unlink(missing_ok=True)  # OpenShorts ya tiene su copia
            except Exception as e:  # noqa: BLE001 - se reintenta en la próxima pasada
                info["estado"] = f"error_envio: {e}"
                self.log(f"tramo {idx}: ERROR al enviar: {e}")
        self.estado["tramos"].append(info)
        self.guardar()

    def enviar(self, archivo: Path, idx: int) -> str:
        nombre = f"{self.dir.name}_tramo{idx:03d}.mp4"
        r = requests.post(f"{BACKEND}/api/uploads", json={"filename": nombre}, timeout=30)
        r.raise_for_status()
        up = r.json()["upload_id"]
        with open(archivo, "rb") as f:
            r = requests.put(f"{BACKEND}/api/uploads/{up}", data=f, timeout=600)
        r.raise_for_status()
        cuerpo = {"upload_id": up, "acknowledged": True, "output_format": self.a.formato_salida}
        if self.a.clips:
            cuerpo["target_clips"] = self.a.clips
        if self.a.layouts:
            cuerpo["layouts"] = [x.strip() for x in self.a.layouts.split(",") if x.strip()]
        r = requests.post(f"{BACKEND}/api/process", json=cuerpo, timeout=120)
        if r.status_code >= 400:
            raise RuntimeError(f"/api/process {r.status_code}: {r.text[:300]}")
        return r.json()["job_id"]

    def limpiar_segmentos(self) -> None:
        """Borra .ts que ya no entrarán en ningún tramo futuro."""
        if self.a.conservar_segmentos:
            return
        primero_util = self.estado["siguiente_inicio"]
        for s in self.estado["segmentos"][:primero_util]:
            (self.seg_dir / s["archivo"]).unlink(missing_ok=True)

    def reintentar_envios_fallidos(self) -> None:
        for t in self.estado["tramos"]:
            if str(t.get("estado", "")).startswith("error_envio") and (self.tramo_dir / t["archivo"]).exists():
                try:
                    t["job_id"] = self.enviar(self.tramo_dir / t["archivo"], t["n"])
                    t["estado"] = "enviado"
                    self.log(f"tramo {t['n']}: reenviado, job {t['job_id']}")
                    if not self.a.conservar_tramos:
                        (self.tramo_dir / t["archivo"]).unlink(missing_ok=True)
                except Exception as e:  # noqa: BLE001
                    self.log(f"tramo {t['n']}: sigue fallando el envío: {e}")
        self.guardar()

    # ------------------------------------------------------------------ bucle principal
    def ejecutar(self) -> int:
        self.log(f"inicio: {self.url} | tramos de {self.a.tramo_min} min, solape {self.a.solape_min} min, "
                 f"segmentos de {self.a.seg_s} s | procesar={'no' if self.a.no_procesar else 'sí'}")
        apagado_desde = None
        while not parar and not (self.dir / "PARAR").exists():
            if not self.grabando():
                if self.proc_ffmpeg is not None:
                    self.leer_segmentos_cerrados()
                    self.log("la grabación se ha cortado (fin del directo o caída de red); reintentando")
                    self.proc_ffmpeg = None
                if self.arrancar():
                    apagado_desde = None
                else:
                    apagado_desde = apagado_desde or time.time()
                    mins = (time.time() - apagado_desde) / 60
                    if mins >= self.a.espera_fin_min:
                        self.log(f"el canal lleva {round(mins)} min sin emitir: fin")
                        break
                    self.log(f"canal sin emitir ({round(mins, 1)} min); reintento en 60 s")
                    for _ in range(60):
                        if parar:
                            break
                        time.sleep(1)
                    continue
            self.leer_segmentos_cerrados()
            self.tramos_listos()
            self.reintentar_envios_fallidos()
            time.sleep(10)

        self.cortar_procesos()
        self.leer_segmentos_cerrados()
        if not (self.a.max_tramos and len(self.estado["tramos"]) >= self.a.max_tramos):
            self.tramos_listos(final=True)
        self.estado["terminado"] = ahora()
        self.guardar()
        (self.dir / "PARAR").unlink(missing_ok=True)
        self.log(f"fin: {len(self.estado['tramos'])} tramos")
        return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("canal", help="login de Twitch (p. ej. djmariio) o URL completa del directo")
    ap.add_argument("--idioma", default="es", choices=["es", "en"])
    ap.add_argument("--tramo-min", type=float, default=20, help="duración de cada tramo (min)")
    ap.add_argument("--solape-min", type=float, default=2, help="solape entre tramos (min)")
    ap.add_argument("--seg-s", type=int, default=120, help="duración de cada segmento base (s)")
    ap.add_argument("--formato", default="best[height<=1080]/best", help="selector de formato de yt-dlp")
    ap.add_argument("--formato-salida", default="vertical", choices=["vertical", "horizontal", "square", "auto"])
    ap.add_argument("--layouts", default="screencast",
                    help="diseños de OpenShorts separados por comas (screencast = juego arriba + streamer; "
                         "auto = decide Gemini; none = recorte simple)")
    ap.add_argument("--clips", type=int, default=0, help="clips por tramo (0 = lo que decida OpenShorts)")
    ap.add_argument("--espera-fin-min", type=float, default=10, help="minutos apagado para dar el directo por terminado")
    ap.add_argument("--max-tramos", type=int, default=0, help="parar tras N tramos (pruebas)")
    ap.add_argument("--no-procesar", action="store_true", help="solo grabar y unir tramos (no enviar a OpenShorts)")
    ap.add_argument("--conservar-tramos", action="store_true")
    ap.add_argument("--conservar-segmentos", action="store_true")
    a = ap.parse_args()
    if a.solape_min >= a.tramo_min:
        ap.error("--solape-min debe ser menor que --tramo-min")
    if not shutil.which("yt-dlp") or not shutil.which("ffmpeg"):
        print("faltan yt-dlp o ffmpeg (¿no estás en el contenedor de OpenShorts?)", file=sys.stderr)
        return 2
    return Grabador(a).ejecutar()


if __name__ == "__main__":
    sys.exit(main())
