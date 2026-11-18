# YoutubeClips — canal de clips de GTA (ES + EN) — contexto del proyecto

> Creado el 18-nov-2026. Fuente de verdad del proyecto. Mantenerlo al día cuando cambie una decisión, ruta o comando.

## 1. Objetivo
Canales de **Shorts y clips solo sobre GTA** (sobre todo **GTA VI**), en **dos canales separados: español (ES) y inglés (EN)**.
Los clips salen de **directos y vídeos largos de los streamers/youtubers más vistos** que jueguen a GTA. Una IA elige los
mejores momentos, los reencuadra a 9:16, les pone subtítulos y los sube a **YouTube y TikTok**, con la menor intervención
humana posible.

## 2. Decisiones del usuario (18-nov-2026)
- **Todo autoalojado**: OpenShorts (MIT) en Docker, en este PC. Sin la nube de openshorts.app.
- **Derechos: sin permiso de los creadores; el usuario asume el riesgo.** La casilla `acknowledged: true` de OpenShorts es una
  declaración del propio usuario ante el software: **Claude no la marca por su cuenta**; solo se activa si el usuario la
  configura él explícitamente. Claude debe recordar el riesgo (strikes, reclamos, «contenido reutilizado») cuando sea pertinente.
  **18-nov-2026: el usuario autorizó de forma permanente que Claude envíe `acknowledged: true` en todos los envíos del flujo
  automático, asumiendo él el riesgo** (respuesta explícita a la pregunta, opción «Autorización permanente»).
- **Revisión humana: ninguna** como objetivo; sí **filtros automáticos de calidad** antes de subir (sin voz, poca acción,
  duplicados, insultos graves, marcas de agua ajenas).
- **Contenido prioritario (18-nov-2026): lo picante/sugerente de GTA** (club, bailes, ligoteo, bromas) va primero en la cola.
  Lo **explícito** (desnudos, actos sexuales) se descarta por las políticas de YouTube. **Títulos orgánicos**, de canal de
  clips (p. ej. «Mejores momentos de <streamer> en GTA VI #n»), nunca con tono de IA.
- **Publicación pública directa deseada**, pero ver límites técnicos (sección 4). Por defecto, lo que fuerce la plataforma.
- **Fuentes: detección automática** de los streamers que juegan a GTA con más audiencia (YouTube Data API y Twitch API),
  separados por idioma (ES / EN).
- El usuario es de nivel técnico medio; Claude responde **en español**, explica comandos y **cuestiona** ideas poco viables.

## 3. Entorno
| Elemento | Valor |
|---|---|
| SO | Windows 10 Pro, shell PowerShell (Git Bash disponible) |
| CPU / GPU / RAM | Ryzen 7 9800X3D · **RTX 5080 (16 GB, Blackwell: necesita CUDA 12.8+)** · 31 GB |
| Discos | **C: solo ~40 GB libres: no instalar cosas pesadas en C:**. E: 1,3 TB libres |
| Carpeta de instalación | **`E:\YoutubeClips`** (app, imágenes Docker, datos, clips) |
| Carpeta del workspace (Claude) | `E:\YoutubeClips` (este archivo, `.claude/skills`, Bitacora.md). La antigua `D:\Proyectos Claude\YoutubeClips` no existe |
| Skills instaladas (proyecto) | `yt-*` (11, de Jakeschincariol/youtube-agent-skill, MIT, commit a2feb21, instaladas el 18-nov-2026 en `E:\YoutubeClips\.claude\skills`) y `openshorts` (de mutonby/openshorts, MIT, en `openshorts\skills`) |

Secretos (`.env`, tokens OAuth): **nunca** mostrarlos, leerlos en pantalla ni subirlos a git. Pedir solo longitud y primeras/últimas letras si hay que depurar.

## 4. Límites técnicos y legales conocidos (decirlos, no esconderlos)
- **YouTube API**: proyecto sin auditar = **10.000 unidades/día**; cada subida cuesta 1.600 (~6 vídeos/día) y **los vídeos quedan
  bloqueados en privado** hasta que Google audita la app. Es improbable que apruebe «clips de terceros en automático».
- **TikTok API**: sin auditoría solo **borrador** en la bandeja de la app (o publicación en cuenta privada). Sin cambio posible desde código.
- **Automatizar el navegador para saltarse esto va contra los términos** de ambas plataformas y suele acabar en baneo: **no lo hacemos**.
- **yt-dlp** (lo usa OpenShorts) incumple los términos de YouTube y YouTube bloquea IPs / pide cookies: mantenimiento continuo.
- **Twitch/Kick**: los directos duran horas y el VOD caduca: descargar pronto.
- Material de GTA VI: es de Rockstar/Take-Two (reclamos frecuentes). YouTube puede no monetizar contenido reutilizado.
- Cualquier cosa **pública e irreversible** (publicar, borrar) se confirma con el usuario salvo autorización duradera explícita.

## 4 bis. Decisión de publicación (18-nov-2026)
- Objetivo **~30 clips/día**. **El usuario los sube a mano** a YouTube y TikTok con las hojas que se generan en Drive
  (`<día>\00 - LISTA PARA SUBIR.txt` y, por clip, `- YouTube.txt` / `- TikTok.txt`).
- **No** se automatiza la interfaz de YouTube/TikTok (bots de navegador o escritorio, horarios aleatorios para no ser
  detectado): Claude lo rechazó aunque el usuario insistió; elude los controles antibots y viola los términos.
- **Subida automática por la API oficial** (tarea «YoutubeClips\Subir a YouTube», cada 30 min): hasta 6/día, 1 cada
  1,5–2,5 h, **en público** (decisión del usuario, 18-nov-2026). El resto, a mano.
- Vía para automatizar de verdad: auditoría/ampliación de cuota de Google y auditoría de TikTok (gratis). El usuario
  dice tener permiso de los creadores: hay que guardar las pruebas para esas solicitudes.

## 5. Flujo previsto
1. **Descubrir**: tarea programada → YouTube/Twitch API → lista de streamers de GTA más vistos por idioma.
2. **Descargar y analizar**: OpenShorts (Docker, GPU) transcribe, detecta momentos (Gemini), reencuadra 9:16, subtitula.
3. **Filtrar**: control de calidad automático descarta clips malos.
4. **Subir**: cola a YouTube y TikTok, un canal ES y otro EN. Estado visible en un panel / `estado.json`.
5. **Registro**: cada clip guarda fuente, creador, hora de subida y resultado.

## 6. Claves/credenciales que aporta el usuario (nunca Claude)
`GEMINI_API_KEY` · `YOUTUBE_API_KEY` (descubrimiento) · OAuth de YouTube (canal ES y EN) · OAuth/app de TikTok ·
(opcional) Twitch Client ID/Secret · (opcional) clave de Upload-Post para publicar desde OpenShorts.

## 7. Reglas de trabajo
- Probar antes de entregar; ser honesto con lo que no se pudo probar.
- Archivos completos cuando sustituyan a otros. Comandos para PowerShell.
- No instalar nada pesado en `C:`; todo bajo `E:\YoutubeClips`.
- Revisar código de terceros antes de instalarlo (como se hizo con las skills).
- Anotar avances y decisiones en `Bitacora.md`.
