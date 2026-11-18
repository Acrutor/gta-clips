"""
Arregla la cámara del streamer en los clips verticales aprobados.

Problema: al pasar de 16:9 a 9:16, OpenShorts a veces deja la webcam del streamer cortada contra un borde (o fuera).
Solución, para cada ficha aprobada de datos\\cola\\{es,en} que aún no se haya revisado (`camara` ausente):
  1. Localiza la webcam en el tramo ORIGINAL 16:9 (Gemini, detección de caja en 2 fotogramas; se exige que coincidan).
  2. Pregunta a Gemini si en el clip vertical la cámara se ve completa, cortada o no se ve (2 fotogramas).
  3. Si está cortada o no se ve: recorta la webcam del original, sincronizada con el clip, y la superpone ENTERA en la
     esquina superior derecha con un borde. Si el diseño del clip es fijo (un solo encuadre), difumina además el trozo
     cortado que quedaba en el borde.
  4. Guarda <clip>_cam.mp4 junto al original y apunta la ficha a él (`video`), con `camara` = completa/superpuesta/sin_camara.

Corre dentro de un contenedor (ffmpeg, requests). Usar camara.ps1 o el bucle de filtro-continuo.
"""
from __future__ import annotations

import base64
import glob
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import requests

YC = Path(os.environ.get("YC_RAIZ", "/yc"))
COLA = YC / "datos" / "cola"
OUT = YC / "openshorts" / "output"
UPLOADS = YC / "openshorts" / "uploads"
BACKEND = os.environ.get("OPENSHORTS_URL", "http://openshorts-backend:8000")
MODELO = os.environ.get("GEMINI_FILTRO_MODELO", "gemini-3.1-flash-lite")
VW, VH = 1080, 1920                 # tamaño del clip vertical
ANCHO_CAM = 0.40                    # ancho de la cámara superpuesta (fracción del ancho vertical)
MARGEN_X, MARGEN_Y = 36, 190        # esquina superior derecha, por debajo de los iconos de la app de Shorts


def clave() -> str:
    for l in (YC / ".env").read_text(encoding="utf-8-sig").splitlines():
        m = re.match(r"^\s*GEMINI_API_KEY\s*=\s*(.*)$", l)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    raise SystemExit("falta GEMINI_API_KEY")


def a_contenedor(ruta_win: str) -> Path:
    return YC / ruta_win.replace("E:\\YoutubeClips\\", "").replace("\\", "/")


def a_host(p: Path) -> str:
    return str(p).replace(str(YC), "E:\\YoutubeClips").replace("/", "\\")


def fotograma(video: Path, t: float, ancho: int = 960) -> bytes:
    r = subprocess.run(["ffmpeg", "-v", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", f"scale={ancho}:-2", "-f", "image2pipe", "-vcodec", "mjpeg", "-q:v", "3", "-"],
                       capture_output=True)
    if not r.stdout:
        raise RuntimeError(f"sin fotograma en {video.name} t={t:.1f}")
    return r.stdout


def duracion(video: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(video)],
                       capture_output=True, text=True)
    return float(r.stdout.strip() or 0)


def gemini(k: str, texto: str, imgs: list[bytes]) -> dict:
    import time
    partes = [{"text": texto}] + [{"inline_data": {"mime_type": "image/jpeg", "data": base64.b64encode(i).decode()}} for i in imgs]
    for intento in range(4):  # 503/429 pasajeros de Gemini: reintentar con espera creciente
        r = requests.post(f"https://generativelanguage.googleapis.com/v1beta/models/{MODELO}:generateContent",
                          headers={"x-goog-api-key": k}, timeout=120,
                          json={"contents": [{"parts": partes}],
                                "generationConfig": {"responseMimeType": "application/json", "temperature": 0}})
        if r.status_code not in (429, 500, 503):
            break
        time.sleep(8 * (intento + 1))
    r.raise_for_status()
    d = json.loads(r.json()["candidates"][0]["content"]["parts"][0]["text"])
    if isinstance(d, list):  # a veces envuelve el objeto en una lista
        d = d[0] if d and isinstance(d[0], dict) else {}
    return d


def caja_webcam(k: str, fuente: Path, ini: float, fin: float) -> tuple[float, float, float, float] | None:
    """Caja (x0, y0, x1, y1) normalizada 0-1 de la webcam en el original, o None si no hay webcam clara."""
    imgs = [fotograma(fuente, ini + (fin - ini) * f) for f in (0.3, 0.7)]
    d = gemini(k, (
        "Son 2 fotogramas de un directo de videojuego (16:9). Localiza la WEBCAM del streamer: el recuadro superpuesto "
        "donde se ve a la persona real (no personajes del juego, no el chat). Incluye su marco decorativo si lo tiene. "
        'Responde SOLO JSON: {"hay_webcam": true/false, "cajas": [[ymin, xmin, ymax, xmax], ...]} con una caja por '
        "fotograma en coordenadas normalizadas 0-1000."), imgs)
    cajas = d.get("cajas") or []
    if not d.get("hay_webcam") or len(cajas) < 2:
        return None
    (ay0, ax0, ay1, ax1), (by0, bx0, by1, bx1) = cajas[0], cajas[1]
    # La webcam es un overlay fijo: si las dos cajas no coinciden, la detección no es fiable.
    if max(abs(ay0 - by0), abs(ax0 - bx0), abs(ay1 - by1), abs(ax1 - bx1)) > 40:
        return None
    y0, x0, y1, x1 = [(a + b) / 2000 for a, b in zip(cajas[0], cajas[1])]
    w, h = x1 - x0, y1 - y0
    if not (0.05 <= w <= 0.6 and 0.05 <= h <= 0.6 and 0.6 <= (w * 16) / (h * 9) <= 3.0):
        return None
    pad = 0.006
    return max(0, x0 - pad), max(0, y0 - pad), min(1, x1 + pad), min(1, y1 + pad)


def _gris(jpg: bytes):
    import cv2
    import numpy as np
    return cv2.imdecode(np.frombuffer(jpg, np.uint8), cv2.IMREAD_GRAYSCALE)


def ventana_visible(fuente: Path, clip: Path, t_fuente: float, t_clip: float):
    """Dónde cae el fotograma vertical dentro del original: (x0, y0, escala, correlación).
    Busca una franja central del vertical en el original a varias escalas (cv2.matchTemplate).
    x0/y0 = esquina superior izquierda del vídeo vertical en píxeles del original; escala = px vertical por px original."""
    import cv2
    import numpy as np
    S = _gris(fotograma(fuente, t_fuente, 1920))
    V = _gris(fotograma(clip, t_clip, VW))
    vh, vw = V.shape
    # Franjas candidatas (los diseños 'wide' solo tienen nítida la banda central ~32-68 %; los subtítulos van más abajo).
    # Se prueba cada una y se queda la de mejor correlación.
    mejor = (-1.0, None, None, 0, 0)
    for fy0, fy1 in ((0.38, 0.58), (0.34, 0.50), (0.20, 0.40), (0.45, 0.62)):
        tx0, ty0, tx1, ty1 = int(vw * 0.12), int(vh * fy0), int(vw * 0.88), int(vh * fy1)
        T = V[ty0:ty1, tx0:tx1]
        if T.std() < 6:          # franja casi lisa (fondo negro/difuminado): no sirve para buscar
            continue
        for esc in np.linspace(0.6, 2.4, 46):
            tw, th = int(T.shape[1] / esc), int(T.shape[0] / esc)
            if tw < 40 or th < 30 or tw >= S.shape[1] or th >= S.shape[0]:
                continue
            r = cv2.matchTemplate(S, cv2.resize(T, (tw, th), interpolation=cv2.INTER_AREA), cv2.TM_CCOEFF_NORMED)
            _, mx, _, loc = cv2.minMaxLoc(r)
            if mx > mejor[0]:
                mejor = (mx, esc, loc, tx0, ty0)
    corr, esc, loc, tx0, ty0 = mejor
    if esc is None:
        return None
    x0 = loc[0] - tx0 / esc
    y0 = loc[1] - ty0 / esc
    return x0, y0, esc, corr


def estado_en_vertical(fuente: Path, clip: Path, ini: float, caja) -> dict:
    """Compara la ventana visible con la caja de la webcam en 2 instantes. Devuelve camara + caja_resto (px del vertical)."""
    sw = 1920
    sh = int(1920 * 9 / 16)
    cx0, cy0, cx1, cy1 = caja[0] * sw, caja[1] * sh, caja[2] * sw, caja[3] * sh
    dur = duracion(clip)
    resultados = []
    for f in (0.3, 0.7):
        v = ventana_visible(fuente, clip, ini + dur * f, dur * f)
        if not v or v[3] < 0.55:
            return {"camara": "desconocido", "motivo": f"correspondencia baja ({v[3]:.2f})" if v else "sin correspondencia"}
        x0, y0, esc, corr = v
        wx0, wy0, wx1, wy1 = x0, y0, x0 + VW / esc, y0 + VH / esc
        ix0, iy0, ix1, iy1 = max(cx0, wx0), max(cy0, wy0), min(cx1, wx1), min(cy1, wy1)
        vis = max(0, ix1 - ix0) * max(0, iy1 - iy0) / ((cx1 - cx0) * (cy1 - cy0))
        resto = None
        if 0 < vis < 0.97:
            resto = [int((ix0 - x0) * esc), int((iy0 - y0) * esc), int((ix1 - x0) * esc), int((iy1 - y0) * esc)]
        resultados.append({"visible": round(vis, 2), "resto": resto, "x0": round(x0), "esc": round(esc, 3), "corr": round(corr, 2)})
    peor = min(r["visible"] for r in resultados)
    estado = "completa" if peor >= 0.97 else ("ausente" if peor == 0 else "cortada")
    # el trozo cortado solo se difumina si el encuadre no se mueve entre los dos instantes
    quieto = abs(resultados[0]["x0"] - resultados[1]["x0"]) < 12 and abs(resultados[0]["esc"] - resultados[1]["esc"]) < 0.03
    return {"camara": estado, "caja_resto": resultados[0]["resto"] if quieto else None, "medidas": resultados}


def componer(clip: Path, fuente: Path, ini: float, caja, resto, salida: Path) -> None:
    x0, y0, x1, y1 = caja
    sw, sh = map(int, subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                                      "stream=width,height", "-of", "csv=p=0", str(fuente)],
                                     capture_output=True, text=True).stdout.strip().split(","))
    cx, cy = int(x0 * sw) // 2 * 2, int(y0 * sh) // 2 * 2
    cw, ch = int((x1 - x0) * sw) // 2 * 2, int((y1 - y0) * sh) // 2 * 2
    ow = int(VW * ANCHO_CAM) // 2 * 2
    oh = int(ow * ch / cw) // 2 * 2
    borde = 6
    dur = duracion(clip)
    filtros = []
    base = "[0:v]"
    if resto:  # difuminar el trozo cortado que se queda en el borde (px del vertical: x0, y0, x1, y1)
        rx0, ry0, rx1, ry1 = resto
        bx, by = max(0, rx0 - 6) // 2 * 2, max(0, ry0 - 6) // 2 * 2
        bw = min(VW - bx, rx1 - rx0 + 12) // 2 * 2
        bh = min(VH - by, ry1 - ry0 + 12) // 2 * 2
        filtros.append(f"[0:v]split[b0][b1];[b1]crop={bw}:{bh}:{bx}:{by},boxblur=28:3[bl];[b0][bl]overlay={bx}:{by}[base]")
        base = "[base]"
    filtros.append(f"[1:v]crop={cw}:{ch}:{cx}:{cy},scale={ow}:{oh},"
                   f"pad={ow + 2 * borde}:{oh + 2 * borde}:{borde}:{borde}:color=white[cam]")
    filtros.append(f"{base}[cam]overlay={VW - ow - 2 * borde - MARGEN_X}:{MARGEN_Y}:shortest=1[v]")
    subprocess.run(["ffmpeg", "-v", "error", "-y", "-i", str(clip), "-ss", f"{ini:.3f}", "-t", f"{dur:.3f}", "-i", str(fuente),
                    "-filter_complex", ";".join(filtros), "-map", "[v]", "-map", "0:a?",
                    "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                    "-c:a", "copy", "-movflags", "+faststart", str(salida)], check=True)


def main() -> int:
    k = None
    hechos = 0
    for ruta in sorted(glob.glob(str(COLA / "*" / "*.json"))):
        p = Path(ruta)
        if p.parent.name not in ("es", "en"):
            continue
        f = json.loads(p.read_text(encoding="utf-8-sig"))
        if f.get("estado_subida") != "pendiente" or f.get("camara"):
            continue
        job, n = f["id"].split(":")
        k = k or clave()
        try:
            clips = requests.get(f"{BACKEND}/api/status/{job}", timeout=30).json()["result"]["clips"]
            c = clips[int(n) - 1]
            fuentes = glob.glob(str(UPLOADS / f"{job}_*.mp4"))
            clip = a_contenedor(f["video"])
            if not fuentes:
                f["camara"] = "sin_fuente"; print(f"[{p.stem}] el tramo original ya no está en uploads: se deja igual")
            else:
                fuente = Path(fuentes[0])
                caja = caja_webcam(k, fuente, c["start"], c["end"])
                if caja is None:
                    f["camara"] = "sin_camara"; print(f"[{p.stem}] sin webcam clara en el original: se deja igual")
                else:
                    ev = estado_en_vertical(fuente, clip, float(c["start"]), caja)
                    print(f"[{p.stem}] webcam en original x {caja[0]:.2f}-{caja[2]:.2f}, y {caja[1]:.2f}-{caja[3]:.2f} | "
                          f"vertical: {ev.get('camara')} {ev.get('medidas') or ev.get('motivo', '')}")
                    if ev.get("camara") in ("completa", "desconocido"):
                        f["camara"] = ev["camara"]
                        print(f"[{p.stem}] se deja igual ({ev['camara']})")
                    else:
                        resto = ev.get("caja_resto") if ev.get("camara") == "cortada" else None
                        salida = clip.with_name(clip.stem + "_cam.mp4")
                        componer(clip, fuente, float(c["start"]), caja, resto, salida)
                        f.update({"video_sin_camara": f["video"], "video": a_host(salida), "camara": "superpuesta",
                                  "camara_caja": [round(v, 3) for v in caja], "camara_motivo": ev.get("camara")})
                        f.pop("titulo_final", None); f.pop("miniatura", None)  # rehacer miniatura con la cámara
                        print(f"[{p.stem}] cámara {ev.get('camara')} -> superpuesta arriba a la derecha"
                              + (" (resto difuminado)" if resto else ""))
            p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
            hechos += 1
        except Exception as e:  # noqa: BLE001 - se reintenta en la próxima pasada
            print(f"[{p.stem}] ERROR: {str(e)[:200]}")
    print(f"{hechos} clips revisados (cámara)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
