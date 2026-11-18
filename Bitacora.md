# Bitácora

## 2026-11-18
- Docker Desktop 29.9.3 instalado; datos en E:\YoutubeClips\docker-wsl (C: intacto). GPU RTX 5080 visible en contenedores (CUDA 12.8 / torch 2.11+cu130).
- Claves validadas (sin mostrarlas): GEMINI, YOUTUBE (Data API), TWITCH (app token). OAuth YouTube ES/EN y TikTok: pendientes.
- OpenShorts construido y levantado con `docker compose up -d` (backend :8000, frontend :5175, renderer :3100).
- Fix local: render-service fijaba remotion 4.0.446 y el bundle usa 4.0.541 -> el renderer fallaba ("export 'Interactive' not found").
  Se actualizó render-service/package.json y package-lock.json a 4.0.541 (copias .bak en esa carpeta). Si se actualiza OpenShorts, revisar.
- Twitch GTA V: ~96 directos, mayoría ja/ru; ES casi inexistente en Twitch -> completar con YouTube.
- Script de descubrimiento: E:\YoutubeClips\descubrimiento\descubrir.py (solo stdlib). Salida en datos\descubrimiento
  (candidatos_*.json, ultimo.json, streamers.json acumulado). ~404 unidades de YouTube por ejecución; --sin-youtube no gasta cuota.
  Twitch: directos GTA V + GTA VI por idioma y VODs de los top filtrados por título (excluye otros juegos y "cuenta atrás").
- GTA VI: ya hay ~1.450 directos en su categoría de Twitch (early access / lanzamiento por zonas horarias).
- yt-dlp en el backend lee metadatos de VOD de Twitch (1080p60) y de YouTube sin cookies (a 18-nov).
- Prueba de OpenShorts bloqueada a propósito: /api/process exige acknowledged=true; lo marca el usuario, no Claude.
- PRUEBA OK (18-nov): VOD GTA VI de Xop0 (75 min, 720p, EN) -> 6 clips 9:16 1080x1920 de 34-55 s, subtitulados, con título/
  descripción/hook de Gemini. Tiempo total ~8,5 min (incluye 1ª descarga de modelos ASR). GPU usada (ASR CUDA + h264_nvenc).
  Problemas: encuadre automático corta la facecam/subtítulos del juego en algunos clips, se ve el chat/overlay de Twitch,
  un clip es en pantalla de carga; hook de Gemini con tono político (filtrar). Parakeet detectó idioma 'ky' -> cayó a Whisper (bien).
  Probar layouts=["screencast"] o "auto" para juego + facecam.
- Grabador de directos por tramos: E:\YoutubeClips\directos\ (grabar_directo.py + iniciar/parar_grabador.ps1). En prueba.
- Grabador probado en seco con Vicens (tramos 1,5 min, solape 30 s): 1080p60 + audio, decodifican sin errores, offsets correctos.
  ~58 MB/min -> tramo de 20 min ~1,2 GB (límite subida 2 GB). Añadido --layouts (por defecto screencast).
- 13:38 UTC: grabador real lanzado con DjMaRiiO (GTA VI, ~121k espectadores): contenedor grabador-djmariio, tramos 20/2 min.
- OAuth YouTube canal ES AUTORIZADO (14:07 UTC): @gta-vi_viral_clip (UC1b8FKG1ztgGWoy9TMWxG-A), token en secretos\youtube_token_es.json.
  Script subida\autorizar_youtube.py <es|en> [id_o_@esperado] (descarta el token si el canal no coincide). Canal EN: pendiente.
  Ojo: un primer intento autorizó por error el canal personal del usuario (revocar en myaccount.google.com/permissions).
- Páginas legales en GitHub Pages (https://acrutor.github.io/gta-clips/) necesarias para publicar la app OAuth; app en producción.
- Filtro de calidad: E:\YoutubeClips\filtro\filtrar.py (+ filtrar.ps1, iniciar_filtro_continuo.ps1). Contenedor filtro-continuo cada 2 min.
  Puertas: duración, score>=70, voz>=1 pal/s, idioma>=60%, insultos, duplicados entre tramos, Gemini (carga/menú, político, calidad>=6).
  Salida: datos\cola\{es,en}\*.json aprobados, rechazados.jsonl, decisiones.json.
  Gemini: gemini-2.5-* da 404 (aunque salga en el listado); se usa gemini-3.1-flash-lite.
- 1er tramo DjMaRiiO (job 29330ce7): 5 clips -> 1 aprobado, 4 rechazados (2 idioma/voz, 1 idioma, 1 poca acción).
- Script de subida: subida\subir.py (simula por defecto; --subir sube). Tope 6/día (hora del Pacífico), orden por nota repartiendo
  entre directos, privacidad private, descripción con crédito al directo + #Shorts. Registro en datos\cola\subidas.jsonl.
  Token ES verificado (renueva y lee el canal @gta-vi_viral_clip). AÚN NO se ha subido ningún vídeo.
- 18-nov 14:3x: PRIMERA SUBIDA (privada) al canal ES: https://www.youtube.com/shorts/SD53t6MKWp4
  Título generado: "DjMaRiiO flipa con este detalle de GTA VI que nadie esperaba 🤯". Miniatura -> 403 forbidden
  (probable: canal sin verificar por teléfono; youtube.com/verify). subida\metadatos.py genera título con gancho + nombre del
  streamer y miniatura 1080x1920 (Anton, caja morada con streamer, etiqueta del juego, gancho). Integrado en filtro-continuo.
- Skills yt-* / youtube-content NO están instaladas (ni en C:\Users\Adrian\.claude ni en E: ni D:). Ruta D:\Proyectos Claude no existe.
- Tramo 2 DjMaRiiO (c1f5feae): 6 clips, 0 aprobados (idioma EN de cinemáticas, poca acción, poca voz).
- Skills yt-* instaladas en E:\YoutubeClips\.claude\skills (revisadas: scripts solo leen archivos locales). No existe una
  "youtube-content": la equivalente para títulos/miniaturas es yt-package. Su linter (title.py) se usa en metadatos.py para
  ordenar 8 propuestas de Gemini (+ penalización de vagos en español, nombre del streamer obligatorio, 25-60 caracteres).
  Prueba: nota 66 -> 88 ("DjMaRiiO se desespera con este cambio en GTA VI ❌", miniatura «ES UN ERROR»).
- Cámara del streamer cortada en el vertical (aviso del usuario): subida\camara.py. Gemini localiza la webcam en el tramo
  original (uploads\<job>_*.mp4; se exige que coincida en 2 fotogramas); OpenCV (matchTemplate multiescala) calcula qué parte
  del original se ve en el vertical -> completa / cortada / ausente. Si no está completa: recorta la webcam del original
  sincronizada y la pone entera arriba a la derecha (40 % del ancho, marco blanco, y=190 bajo los iconos de Shorts) y
  difumina el trozo cortado si el encuadre es fijo. Gemini solo NO servía para juzgar el vertical (dijo "completa" en un
  caso cortado). Prueba: clip 1 tramo 0 -> cámara visible 42 % (corr 0,91) -> arreglado (_cam.mp4).
  Orden del bucle filtro-continuo: filtrar -> camara -> metadatos.
- Archivo en Google Drive (G:\Mi unidad\Clips YOutube GTA\<día>\<streamer>\{fragmentos,clips,aprobados}): archivo\archivar.py.
  El trabajo en curso sigue en E: (Drive es unidad virtual: no apta para segmentos ni montajes Docker). Registro anti-duplicado
  en datos\archivado.json. 1ª pasada OK: 2 fragmentos + 11 clips + 1 aprobado (DjMaRiiO, 2026-11-18).
  La tarea programada cada 5 min NO la pude crear (bloqueo de permisos): la crea el usuario con archivo\crear_tarea_archivar.ps1.
- Umbral de voz del filtro bajado de 1.0 a 0.6 pal/s (--min-voz). Reevaluados 8 rechazados por voz: 1 aprobado nuevo
  (tramo 2 clip 6); cocodrilo y persecución caen ahora por Gemini (poca acción, calidad 3). Cola ES: 2 aprobados pendientes.
- Petición del usuario: priorizar contenido sexual de GTA y títulos orgánicos (que no suenen a IA).
  * Filtro: Gemini marca "picante" (sugerente dentro del juego: club, bailes, ligoteo, bromas) y "desnudez_explicita".
    Picante: no se rechaza por poca acción, se le exige 1 punto menos de calidad y sube +15 en la prioridad de subida.
    Explícito (genitales/pezones/acto sexual): se rechaza (política de YouTube; un Short con restricción de edad no se distribuye).
    OpenShorts no admite indicarle qué momentos buscar: la prioridad se aplica en filtro y cola, no en la selección.
  * Títulos: plantillas de canal de clips numeradas por streamer ("Mejores momentos de X en GTA VI #1", "Top momentos de X
    jugando GTA VI #n", "Los clips más locos de X en GTA VI #n", "<gancho> 💀 X en GTA VI"...) + gancho corto coloquial de
    Gemini, descartado si suena a IA. Contador en datos\cola\series.json. Se evitó "los clips más vistos" (afirmación falsa).
  * Miniatura: si el clip lleva la cámara superpuesta, el nombre del streamer baja (y=480) para no taparle la cara.
- Plantilla fija de streamer para TODO lo aprobado (subida\componer.py, sustituye a camara.py en el bucle): webcam entera
  arriba (1080x608, recortada del original), juego abajo (recorte central que evita la webcam), línea morada, subtítulos
  propios palabra a palabra (Anton 138, activa en amarillo) desde la transcripción. Motivo: OpenShorts dejaba la cara
  cortada o una cámara grande mal encuadrada (diseños inset/screencast). Sin webcam -> clip de OpenShorts.
  subir.py ya no sube nada sin formato/título/miniatura. camara.gemini reintenta 429/5xx.
- Drive reorganizado: aprobados\ (final + .jpg + .json + " - TikTok.txt") y descartados\ ("tramoNNN_clipM - motivo.mp4").
  La carpeta clips\ desaparece. El txt de TikTok sigue el formato de publicar_short.txt del proyecto Faceless.
- DECISIÓN 18-nov: objetivo ~30 clips/día; el usuario los sube A MANO a YouTube y TikTok. No se automatiza la interfaz
  (bot de navegador/escritorio con horarios aleatorios) -> rechazado: elude controles antibots y viola ToS.
- Orquestador (directos\orquestador.py + config.json): cada 10 min mantiene hasta 3 grabadores con los streamers ES de
  GTA V/VI más vistos; para al llegar a objetivo_clips_dia (aprobados hoy). Listas solo_/nunca_estos_streamers.
  Tarea programada: la crea el usuario con directos\crear_tarea_orquestador.ps1. 1ª pasada: arrancó FolagorLives y aXoZer.
  Grabador: carpeta por canal y día (<canal>_AAAAMMDD).
- Drive: cada aprobado lleva también "<título> - YouTube.txt" (título, descripción con crédito, etiquetas, ajustes,
  checklist) y cada día "00 - LISTA PARA SUBIR.txt" (orden recomendado, 🔥 picante primero, casillas [x] YT/TT que se
  conservan al regenerar).
- Avisos al móvil: ntfy.sh (gratis, sin cuenta), tema en .env NTFY_TOPIC; lo envía archivar.py cada 3 clips nuevos
  aprobados y archivados (registro datos\notificados.json). La app de Claude solo sirve con la sesión abierta + Remote Control.
- Respaldo sin directos: si nadie de GTA (ES) está en directo durante min_sin_directos_para_vods (30) minutos y faltan
  clips, el orquestador lanza directos\vod_tramos.py (contenedor vod-*): ventanas de 20 min de VODs de Twitch de los
  streamers conocidos (últimos 7 días, título GTA, sin repetir lo grabado en directo ese día) o, si no hay, vídeos largos
  de YouTube de la última búsqueda. Descarga solo cada ventana (yt-dlp --download-sections) y la envía como un tramo
  (mismo estado.json): filtro, plantilla, Drive, txt y avisos sin cambios. OpenShorts autoalojado IGNORA
  source_start/source_end (solo en su versión de pago), por eso se corta antes. Prueba OK con VOD de Vicens.
- Pendiente de respuesta del usuario: ¿los vídeos subidos por API quedan bloqueados en privado? (decide la subida automática a YouTube).
- Confirmado por el usuario: los vídeos subidos por la API (privados) SÍ se pueden pasar a Público a mano en Studio.
  -> Subida automática a YouTube: subida\crear_tarea_subida.ps1 (tarea cada 30 min: subir.py --subir --max-por-pasada 1
  --min-intervalo 90 [+0-60 aleatorio] --max-dia 6). Privado; el usuario los publica. Log datos\subir.log.
  La tarea la crea el usuario (Claude no puede crear tareas programadas).

- 18-nov 15:50: canal ES verificado por teléfono (API: longUploadsStatus=allowed). Tarea 'Subir a YouTube' activa (resultado 0); las miniaturas deberían aplicarse desde la próxima subida.
- 18-nov 16:00: a petición del usuario, la subida automática a YouTube pasa a PÚBLICO (--privacidad public en la tarea). subir.log registra la 'privacidad real' que devuelve YouTube.
