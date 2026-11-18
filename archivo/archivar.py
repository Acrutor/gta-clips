"""
Archiva en Google Drive lo ya terminado, organizado por día y por streamer:

  G:\\Mi unidad\\Clips YOutube GTA\\
      2026-11-18\\
          DjMaRiiO\\
              fragmentos\\  tramo000_1538.mp4        (fragmento de 20 min del directo; HHMM = hora local de inicio)
              aprobados\\   <título>.mp4 / .jpg / .json / " - TikTok.txt"  (vídeo FINAL, miniatura, ficha y hoja
                            para publicarlo a mano en TikTok: descripción, hashtags, crédito, ajustes y checklist)
              descartados\\ tramo000_clip3 - <motivo>.mp4  (los que rechazó el filtro, tal cual salieron de OpenShorts)

El trabajo en curso (segmentos de grabación, OpenShorts) se queda en E:: Google Drive es una unidad virtual que sube
cada archivo a la nube y no aguanta bien la escritura continua ni los montajes de Docker.

Copia a un nombre temporal y renombra al terminar (Drive no sube archivos a medias). Lleva un registro en
E:\\YoutubeClips\\datos\\archivado.json para no copiar dos veces. Uso:
  python archivar.py                 # una pasada
  python archivar.py --sin-fragmentos   # no copia los fragmentos de 20 min (ahorra ~3,5 GB por hora de directo)
"""
from __future__ import annotations

import argparse
import datetime as dt
import glob
import json
import os
import re
import shutil
import sys
import urllib.request
from pathlib import Path

RAIZ = Path(r"E:\YoutubeClips")
DESTINO = Path(os.environ.get("ARCHIVO_DESTINO", r"G:\Mi unidad\Clips YOutube GTA"))
DIRECTOS = RAIZ / "datos" / "directos"
COLA = RAIZ / "datos" / "cola"
OUT = RAIZ / "openshorts" / "output"
UPLOADS = RAIZ / "openshorts" / "uploads"
REGISTRO = RAIZ / "datos" / "archivado.json"
BACKEND = "http://localhost:8000"


def local(iso: str) -> dt.datetime:
    return dt.datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone()


def limpio(texto: str, maximo: int = 80) -> str:
    t = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", texto).strip().rstrip(".")
    return re.sub(r"\s+", " ", t)[:maximo] or "sin_titulo"


def nombre_visible(login: str) -> str:
    p = RAIZ / "datos" / "descubrimiento" / "streamers.json"
    if p.exists():
        for v in json.loads(p.read_text(encoding="utf-8")).values():
            if v.get("login", "").lower() == login.lower():
                return v["creador"]
    return login


def copiar(origen: Path, destino: Path, hecho: set, clave: str) -> bool:
    if clave in hecho or not origen.exists():
        return False
    destino.parent.mkdir(parents=True, exist_ok=True)
    tmp = destino.with_name(destino.name + ".parcial")
    shutil.copyfile(origen, tmp)
    os.replace(tmp, destino)
    hecho.add(clave)
    print(f"  -> {destino.relative_to(DESTINO)}  ({origen.stat().st_size >> 20} MB)")
    return True


def texto_tiktok(f: dict, video_drive: Path) -> str:
    """Hoja para publicar A MANO en TikTok (mismo estilo que publicar_short.txt del proyecto Faceless)."""
    es = f.get("idioma", "es") == "es"
    nombre = f.get("streamer_nombre") or f.get("creador") or ""
    juego = f.get("juego") or "GTA VI"
    tag_juego = juego.replace(" ", "").lower()                 # gtavi
    tag_num = "gta6" if "VI" in juego else "gta5"
    tag_streamer = re.sub(r"[^A-Za-z0-9_]", "", nombre).lower()
    titulo = f.get("titulo_publicado") or f.get("titulo_final") or f.get("titulo") or ""
    url = f.get("fuente_url") or ""
    try:
        dur = f"{round(f['datos']['duracion_s'])} s"
    except Exception:  # noqa: BLE001
        dur = "?"
    hashtags = (f"#{tag_num} #{tag_juego} #{tag_streamer} #gta #parati #fyp #clips" if es
                else f"#{tag_num} #{tag_juego} #{tag_streamer} #gta #fyp #foryou #clips")
    credito = (f"Clip del directo de {nombre}: {url}" if es else f"Clip from {nombre}'s stream: {url}") if url else ""
    linea = "-" * 60
    return "\n".join([
        "PUBLICAR EN TIKTOK (a mano, desde la app o tiktok.com/upload)",
        "=" * 60, "",
        f"ARCHIVO: {video_drive}  ({dur})",
        f"PORTADA: {video_drive.with_suffix('.jpg').name}  (texto de portada: «{f.get('texto_miniatura', '')}»)", "",
        f"DESCRIPCIÓN ({len(titulo)} caracteres el título) - copia y pega todo el bloque:",
        linea, titulo, "", credito, "", hashtags, linea, "",
        "AJUSTES AL PUBLICAR",
        linea,
        "- Quién puede verlo: Todos",
        "- Comentarios: activados   - Dúo/Pegar: activados",
        "- Contenido de marca / promoción: NO (no es publicidad)",
        "- Contenido generado por IA: NO (es un clip real; solo los subtítulos son automáticos)",
        "- Sonido: el original del vídeo (no añadas música con copyright encima de la voz)",
        "", "ANTES DE PUBLICAR", "=" * 60,
        "[ ] Lo has visto entero con sonido: subtítulos bien y la cara del streamer completa arriba",
        "[ ] Nada explícito (desnudos o sexo): TikTok lo retira y penaliza la cuenta" if f.get("picante") else
        "[ ] Sin insultos graves ni temas sensibles fuera del juego",
        f"[ ] El crédito a {nombre} está en la descripción (evita reportes de 'contenido robado')",
        "[ ] No es el mismo momento que otro clip ya publicado",
        "", "AVISO: el clip es de un directo ajeno sin permiso del creador; si lo reclama, retíralo.",
        f"Ficha interna: {f.get('id')}  ·  nota {f.get('score')}  ·  minuto {f.get('minuto_en_directo')} del directo", "",
    ])


def texto_youtube(f: dict, video_drive: Path) -> str:
    """Hoja para subir A MANO en YouTube Studio (Short)."""
    es = f.get("idioma", "es") == "es"
    nombre = f.get("streamer_nombre") or f.get("creador") or ""
    juego = f.get("juego") or "GTA VI"
    titulo = f.get("titulo_publicado") or f.get("titulo_final") or f.get("titulo") or ""
    url = f.get("fuente_url") or ""
    tag_streamer = re.sub(r"[^A-Za-z0-9_]", "", nombre)
    descripcion = "\n".join(x for x in [
        (f"Clip del directo de {nombre}: {url}" if es else f"Clip from {nombre}'s stream: {url}") if url else "",
        "",
        f"#Shorts #{juego.replace(' ', '')} #GTA #{tag_streamer}" if tag_streamer else f"#Shorts #{juego.replace(' ', '')} #GTA",
    ])
    etiquetas = ", ".join(t for t in (nombre, juego, "GTA 6" if "VI" in juego else "GTA 5", "GTA", "Rockstar",
                                      "gameplay", "clips", "shorts") if t)
    linea = "-" * 60
    return "\n".join([
        "SUBIR A YOUTUBE (a mano, desde studio.youtube.com > Crear > Subir vídeos)",
        "=" * 60, "",
        f"ARCHIVO: {video_drive}",
        f"MINIATURA: {video_drive.with_suffix('.jpg').name}  (en Shorts solo se ve en el canal y en búsquedas)", "",
        f"TÍTULO ({len(titulo)} caracteres):", titulo, "",
        "DESCRIPCIÓN:", linea, descripcion, linea, "",
        "ETIQUETAS (Mostrar más > Etiquetas):", etiquetas, "",
        "AJUSTES",
        linea,
        "- Audiencia: «No, no es contenido creado para niños»",
        "- Contenido alterado o sintético: NO",
        "- Categoría: Videojuegos",
        "- Visibilidad: Pública (o Programar, repartiendo las subidas a lo largo del día)",
        "", "ANTES DE PUBLICAR", "=" * 60,
        "[ ] Visto entero con sonido; la cara del streamer completa arriba",
        f"[ ] El crédito a {nombre} con su enlace está en la descripción",
        "[ ] Nada explícito (desnudos o sexo)" if f.get("picante") else "[ ] Sin insultos graves ni temas sensibles fuera del juego",
        "",
    ])


LISTA = "00 - LISTA PARA SUBIR.txt"
AVISADOS = RAIZ / "datos" / "notificados.json"
CADA_N_CLIPS = int(os.environ.get("AVISAR_CADA", "3"))


def ntfy_topic() -> str | None:
    env = RAIZ / ".env"
    if env.exists():
        for l in env.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"^\s*NTFY_TOPIC\s*=\s*(\S+)", l)
            if m:
                return m.group(1)
    return None


def avisar_movil(fichas: list[dict]) -> None:
    """Aviso al móvil (ntfy.sh, gratis y sin cuenta) cada vez que hay CADA_N_CLIPS clips nuevos listos para subir.
    Solo se envían títulos y nombres de streamer; nada de claves ni datos personales."""
    topic = ntfy_topic()
    if not topic:
        return
    ya = set(json.loads(AVISADOS.read_text(encoding="utf-8"))) if AVISADOS.exists() else set()
    nuevos = [f for f in fichas if f["id"] not in ya and f.get("estado_subida") != "subido"]
    if len(nuevos) < CADA_N_CLIPS:
        return
    orden = sorted(nuevos, key=lambda f: -(f.get("score", 0) + (15 if f.get("picante") else 0)))
    lineas = [f"{'🔥 ' if f.get('picante') else '• '}{f.get('titulo_final') or f.get('titulo', '')}" for f in orden[:6]]
    if len(orden) > 6:
        lineas.append(f"… y {len(orden) - 6} más")
    pendientes = sum(1 for f in fichas if f.get("estado_subida") != "subido")
    cuerpo = "\n".join(lineas) + f"\n\nPendientes de subir hoy: {pendientes}. Lista: Drive > Clips YOutube GTA > {dt.date.today()}"
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}", data=cuerpo.encode("utf-8"), method="POST",
        headers={"Title": f"{len(nuevos)} clips nuevos listos para subir".encode("utf-8"),
                 "Tags": "clapper", "Priority": "default"})
    try:
        urllib.request.urlopen(req, timeout=20).read()
        ya.update(f["id"] for f in nuevos)
        AVISADOS.write_text(json.dumps(sorted(ya), indent=0), encoding="utf-8")
        print(f"aviso al móvil: {len(nuevos)} clips nuevos")
    except Exception as e:  # noqa: BLE001 - sin red: se reintenta en la próxima pasada
        print(f"no se pudo enviar el aviso al móvil: {e}")


def escribir_lista_dia(carpeta_dia: Path, fichas: list[dict]) -> None:
    """Lista del día con todos los aprobados (de todos los streamers), en el orden recomendado de subida.
    Conserva las marcas [x] que el usuario haya puesto en la versión anterior (por id del clip)."""
    ruta = carpeta_dia / LISTA
    marcas: dict[str, tuple[str, str]] = {}
    if ruta.exists():
        for l in ruta.read_text(encoding="utf-8-sig").splitlines():
            m = re.match(r"\[(.)\] YT\s+\[(.)\] TT.*id=(\S+)", l)
            if m:
                marcas[m.group(3)] = (m.group(1), m.group(2))
    orden = sorted(fichas, key=lambda f: -(f.get("score", 0) + (15 if f.get("picante") else 0)))
    filas = []
    for i, f in enumerate(orden, 1):
        yt, tt = marcas.get(f["id"], (" ", " "))
        if f.get("estado_subida") == "subido":
            yt = "x"  # ya subido por la API
        titulo = f.get("titulo_publicado") or f.get("titulo_final") or f.get("titulo", "")
        filas.append(f"[{yt}] YT  [{tt}] TT  | {i:02d} | {'🔥 ' if f.get('picante') else ''}{titulo} "
                     f"| {f.get('streamer_nombre') or f.get('creador')} | id={f['id']}")
    hechos_yt = sum(1 for l in filas if l.startswith("[x]"))
    hechos_tt = sum(1 for l in filas if re.match(r"\[.\] YT\s+\[x\]", l))
    cab = [
        f"CLIPS LISTOS PARA SUBIR - {carpeta_dia.name}",
        "=" * 70,
        f"{len(filas)} clips aprobados · subidos a YouTube: {hechos_yt} · a TikTok: {hechos_tt}",
        "Orden recomendado: de arriba abajo (🔥 = contenido picante, va primero).",
        "Cada clip está en <streamer>\\aprobados\\ con su vídeo, miniatura, «- YouTube.txt» y «- TikTok.txt».",
        "Marca con x lo que subas: [x] YT  [x] TT  (las marcas se conservan aunque la lista se actualice).",
        "Consejo: reparte las subidas a lo largo del día en vez de subirlas todas seguidas.",
        "=" * 70, "",
    ]
    ruta.write_text("\n".join(cab + filas) + "\n", encoding="utf-8-sig")


def estado_job(job: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"{BACKEND}/api/status/{job}", timeout=20) as r:
            d = json.loads(r.read().decode())
        return d if d.get("status") == "completed" else None
    except Exception:  # noqa: BLE001 - backend caído: se reintenta en la próxima pasada
        return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sin-fragmentos", action="store_true")
    a = ap.parse_args()
    if sys.stdout is None:  # lanzado con pythonw desde la tarea programada: escribir en un log
        sys.stdout = sys.stderr = open(RAIZ / "datos" / "archivar.log", "a", encoding="utf-8")
        print(f"--- {dt.datetime.now():%Y-%m-%d %H:%M:%S}")
    for flujo in (sys.stdout, sys.stderr):
        try: flujo.reconfigure(encoding="utf-8", errors="replace")
        except AttributeError: pass
    if not DESTINO.exists():
        print(f"No existe {DESTINO} (¿Google Drive no está iniciado?)."); return 1
    hecho = set(json.loads(REGISTRO.read_text(encoding="utf-8"))) if REGISTRO.exists() else set()
    n = 0
    try:
        for ep in glob.glob(str(DIRECTOS / "*" / "estado.json")):
            e = json.loads(Path(ep).read_text(encoding="utf-8"))
            streamer = limpio(nombre_visible(e["canal"]), 40)
            for t in e.get("tramos", []):
                if not t.get("job_id"):
                    continue
                job, cuando = t["job_id"], local(t["creado"]) - dt.timedelta(seconds=t.get("dur_s", 0))
                carpeta = DESTINO / cuando.strftime("%Y-%m-%d") / streamer
                base = f"tramo{t['n']:03d}"
                # 1) fragmento de 20 min (OpenShorts guarda el original en uploads hasta su limpieza)
                if not a.sin_fragmentos:
                    for src in glob.glob(str(UPLOADS / f"{job}_*.mp4")):
                        n += copiar(Path(src), carpeta / "fragmentos" / f"{base}_{cuando:%H%M}.mp4", hecho, f"frag:{job}")
                # 2) clips DESCARTADOS por el filtro (tal cual los generó OpenShorts, para revisarlos si se quiere).
                #    Los aprobados no se copian aquí: su versión final va a «aprobados».
                d = estado_job(job)
                if not d:
                    continue
                decisiones = json.loads((COLA / "decisiones.json").read_text(encoding="utf-8")) if (COLA / "decisiones.json").exists() else {}
                for i, c in enumerate((d.get("result") or {}).get("clips", []), 1):
                    dec = decisiones.get(f"{job}:{i}", {})
                    if dec.get("estado") != "rechazado":
                        continue
                    from urllib.parse import unquote
                    partes = unquote(c["video_url"]).split("/", 3)
                    motivo = limpio(dec.get("motivos", ["?"])[0].split("_(")[0], 30)
                    n += copiar(OUT / partes[2] / partes[3], carpeta / "descartados" / f"{base}_clip{i} - {motivo}.mp4",
                                hecho, f"desc:{job}:{i}")
        # 3) aprobados: vídeo final (con cámara arreglada), miniatura y ficha
        for fp in glob.glob(str(COLA / "*" / "*.json")):
            p = Path(fp)
            if p.parent.name not in ("es", "en"):
                continue
            f = json.loads(p.read_text(encoding="utf-8-sig"))
            if not f.get("titulo_final") and f.get("estado_subida") == "pendiente":
                continue  # aún le faltan título/miniatura: se archiva en una pasada posterior
            job = f["id"].split(":")[0]
            tramo = next((t for ep in glob.glob(str(DIRECTOS / "*" / "estado.json"))
                          for t in json.loads(Path(ep).read_text(encoding="utf-8")).get("tramos", []) if t.get("job_id") == job), None)
            cuando = local(tramo["creado"]) if tramo else local(f["aprobado"])
            carpeta = DESTINO / cuando.strftime("%Y-%m-%d") / limpio(f.get("streamer_nombre") or nombre_visible(f.get("creador", "")), 40) / "aprobados"
            titulo = limpio(f.get("titulo_publicado") or f.get("titulo_final") or f.get("titulo") or p.stem)
            clave = f"apro:{f['id']}:{f.get('video')}"
            if clave in hecho:
                # ya archivado: solo se reescriben las hojas de subida (por si cambió el formato de los txt)
                (carpeta / f"{titulo} - TikTok.txt").write_text(texto_tiktok(f, carpeta / f"{titulo}.mp4"), encoding="utf-8-sig")
                (carpeta / f"{titulo} - YouTube.txt").write_text(texto_youtube(f, carpeta / f"{titulo}.mp4"), encoding="utf-8-sig")
                if not f.get("video_drive"):
                    f["video_drive"] = str(carpeta / f"{titulo}.mp4")
                    p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
                continue
            ok = copiar(Path(f["video"]), carpeta / f"{titulo}.mp4", hecho, clave)
            if f.get("miniatura"):
                copiar(Path(f["miniatura"]), carpeta / f"{titulo}.jpg", set(), "x")
            if ok:
                # La copia de Drive sirve de respaldo para subir: OpenShorts borra sus archivos de E: a las 24 h.
                f["video_drive"] = str(carpeta / f"{titulo}.mp4")
                if f.get("miniatura"):
                    f["miniatura_drive"] = str(carpeta / f"{titulo}.jpg")
                p.write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
            (carpeta / f"{titulo}.json").write_text(json.dumps(f, ensure_ascii=False, indent=2), encoding="utf-8")
            (carpeta / f"{titulo} - TikTok.txt").write_text(texto_tiktok(f, carpeta / f"{titulo}.mp4"), encoding="utf-8-sig")
            (carpeta / f"{titulo} - YouTube.txt").write_text(texto_youtube(f, carpeta / f"{titulo}.mp4"), encoding="utf-8-sig")
            n += ok
        # 4) lista del día (todos los aprobados de ese día, de todos los streamers)
        por_dia: dict[Path, list[dict]] = {}
        for fp in glob.glob(str(COLA / "*" / "*.json")):
            p = Path(fp)
            if p.parent.name not in ("es", "en"):
                continue
            f = json.loads(p.read_text(encoding="utf-8-sig"))
            if not f.get("video_drive"):
                continue  # aún no archivado
            por_dia.setdefault(Path(f["video_drive"]).parents[2], []).append(f)
        for dia, fichas in por_dia.items():
            escribir_lista_dia(dia, fichas)
        avisar_movil([f for fichas in por_dia.values() for f in fichas])
    finally:
        REGISTRO.write_text(json.dumps(sorted(hecho), indent=0), encoding="utf-8")
    print(f"{n} archivos nuevos archivados en {DESTINO}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
