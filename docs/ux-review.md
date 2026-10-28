# Revisión UX con capturas · "Rótulo" y "Atrio"

## Método

- **Herramienta:** Playwright 1.63 (Chromium), `es-MX`, zona horaria America/Mexico_City. El guion está en el repositorio: `e2e/design-review.mjs` recorre todo el flujo (inicio del coordinador, búsqueda, antes de empezar con y sin error, preguntas, opción múltiple, revisión, completada, entrega, modo campero completo, recuperar la computadora, inicio con una entrevista abierta y resumen del pastor) y, si se le da `AXE`, corre axe-core en cada pantalla y cada ancho.
- **Toda la aplicación:** `e2e/all_urls.py` arma la lista de pantallas de cada cuenta (coordinador, tutor, pastor, administración, y en discipulado administración, mentor y participante, más las páginas sin sesión) y `e2e/review-all.mjs` visita cada una en tres anchos con axe, y revisa que no haya desborde horizontal, que haya un solo `h1` y que cargue `rotulo.css`.
- **Anchos:** escritorio 1440 × 900, tableta 820 × 1180, teléfono 390 × 844.
- **Datos:** solo sintéticos. `seed_camp_demo` crea un campamento de 400 camperos ficticios; los nombres son combinaciones aleatorias de nombres y apellidos comunes. Antes de cada ronda se volvió a sembrar la base, porque el recorrido abre y termina entrevistas y los tableros cambian.
- **Rondas:** 18 rondas de captura → crítica → cambio. La ronda 0 es la versión anterior, la 10 fue una revisión independiente del código, la 13 cerró al coordinador, la 14 llevó el resto de la aplicación al sistema, la 15 cerró sus pendientes, la 16 pasó toda la aplicación de Rótulo a Atrio, la 17 ordenó cada pantalla por lo que su rol tiene que hacer y la 18 dejó que un evento use varios cuestionarios.

## Rondas

### Ronda 20 · descargar las respuestas

El 28 de octubre de 2026. Lo pidió el dueño: una forma de sacar los datos, solo para la administración por ahora, probablemente en CSV. Había dos descargas, la lista de camperos (sin respuestas) y los datos completos de un campero (JSON, uno por uno); ninguna juntaba las respuestas de todos.

| Qué | Lo que cambió |
|---|---|
| Configuración | Una sección nueva, "Exportar datos", después de "Privacidad y protección" y en el índice de la página: una frase dice qué trae el archivo y que queda registrado, y el botón "Descargar las respuestas". Quien administra sin acceso confidencial lee en su lugar "Solo quien tiene acceso confidencial puede descargar las respuestas." |
| Camperos e Inicio | Junto a "Descargar la lista de camperos", "o descargar las respuestas", solo con acceso confidencial |
| El archivo | `respuestas-<evento>-<fecha>.csv`: un renglón por campero, también los que ya no participan, con sus datos de registro y, por cada cuestionario, su estado, cuándo se completó y una columna por pregunta con la respuesta en palabras. Abre en Excel con acentos (BOM), y una celda que empieza como fórmula lleva `'` |
| El registro | Una fila `exportacion` por descarga ("Descargó las respuestas de todos los camperos (CSV)"), que sale en "Descargas recientes" sin nombres |

19 pruebas nuevas en `events/tests/test_answers_export.py`. El archivo sale de 6 consultas a la base, tenga el campamento 30 o 400 camperos (12 la primera vez que el proceso lee los cuestionarios). Esta ronda no se capturó con Playwright.

Queda pendiente: descargar los seguimientos y los reportes de riesgo, filtrar por cuestionario y el formato XLSX.

### Ronda 19 · cuántos de los N cuestionarios, en cada nivel

El 28 de octubre de 2026. Con varios cuestionarios, las pantallas hablaban de camperos "con todo" y nada decía cuántos de los N llevaba cada uno ni cuánto había avanzado cada cuestionario. Lo pidió el dueño: que el dato se entienda en cada nivel, del campero al campamento. La regla de la ronda sigue siendo la de la 18: **con un solo cuestionario, ninguna pantalla, PDF ni diapositiva cambia** (las pruebas comparan las palabras; antes de entregar se compararon byte a byte 34 salidas, páginas, fragmentos, presentación y textos del PDF, contra las de antes de la ronda, sin contar horas ni fichas de seguridad).

| Qué | Lo que cambió |
|---|---|
| El campero | Su etiqueta dice "0 de 2 cuestionarios", "En curso, 1 de 2" o "2 de 2 cuestionarios" en cada lista, en la lista descargada y en la de respaldo; la pestaña y el filtro "Sin entrevista" se llaman "Sin terminar" |
| Los conteos | "X de Y con los N cuestionarios" ("Llevamos…", "Llevas…", "Tu equipo lleva…", "Entre todos llevan…") en Mi inicio, Mi avance, Mi equipo, Resumen general, Avance, Inicio de la administración, Coordinadores, la página de un coordinador, Reportes y cada corte; la clave separa "sin iniciar" de "a medias" |
| Por cuestionario | Después de la clave, una línea y una regla por cuestionario (`.quests`); en las filas de coordinadores y en las tablas de equipos y coordinadores, una línea bajo el nombre (`.table__sub`); en el historial, bajo "Completadas", y los cortes se pliegan solo si tampoco cambió ningún cuestionario |
| PDF y presentación | El PDF dice "con los N cuestionarios", "a medias", trae la tabla "Por cuestionario" y define "A medias" y "Por cuestionario" en "Cómo se calcula" (solo con varios cuestionarios); la presentación suma la diapositiva "Por cuestionario" después de "cómo va" y cuenta seis (cinco mientras no haya camperos) |
| El rol | La barra dice el rol de quien entró bajo el nombre del evento y "Campamentos", en su propio renglón ("Coordinador", "Tutor", "Pastor" o "Administrador"). Cada inicio lo repite en una frase, con la fórmula de la lista de campamentos: "Tu rol: coordinador." bajo "Hola, N.", "Tu rol: tutor." en Mi equipo, y al final de la línea bajo el título en Resumen general y en el inicio de la administración. En el teléfono la barra oculta el evento y el rol: ahí quedan esa frase y la línea de quién eres de "Menú". Por qué: al entrar, cada rol se confundía; fuera de "Menú", nada decía con qué rol estaba |

Los cortes guardados antes de la ronda no traen las cifras nuevas y se ven como siempre. Una revisión independiente encontró cuatro cosas, ya corregidas: "Entrevistas pendientes" celebraba ("No quedan entrevistas por iniciar. ¡Buen trabajo!") sobre una lista "A medias" con camperos (ahora dice "Nadie sin iniciar."); la frase que el lector de pantalla oye en las marcas y en las reglas contaba como "sin iniciar" a los que van a medias, contra lo que dice la clave (ahora los separa en el avance del campamento, de cada equipo y de cada coordinador: "…, 3 a medias, 7 sin iniciar"); Cuestionarios decía "1 de 1 camperos."; y la exportación de una persona y el enlace "Ver los …" de Mi inicio no tenían una prueba que los fijara. 75 pruebas nuevas en `events/tests/test_multi_questionnaires_progress.py` (49) y `events/tests/test_progreso_agregados.py` (26), y 9 del rol en `events/tests/test_rol_visible.py`.

Queda pendiente: ver la diapositiva nueva en un proyector real (está medida en Chromium contra el piso de 22 px, con marcos de 1192, 1500 y 1744 px, no en sala). En "Por cuestionario" cada cuestionario ocupa un renglón: un título largo termina en "…" y su cifra queda entera; desde siete renglones se aprietan otra vez, con la letra en el piso de 22 px; diez no caben ni así, de modo que la diapositiva muestra los primeros ocho y un último renglón, "y 2 más". La diapositiva 2 dice "Llevamos 120 de 400." y, en un renglón propio debajo, "Con los 2 cuestionarios." (en el titular la frase ocupaba cuatro renglones y la clave caía sobre el pie). Del rol: los nombres neutros («Administración», «Pastoral», «Tutoría», «Coordinación»; D3 y LANG-08 de `auditoria-ux-2026-10.md`), que hoy siguen en masculino, y las páginas de `base.html` (fuera de un evento) de quien no tiene rol en la iglesia, que no muestran renglón de rol. Las reglas de los equipos en la presentación ya separan "a medias" (la presentación copia esa cifra por equipo desde esta ronda).

### Ronda 18 · varios cuestionarios por evento

El 26 de octubre de 2026 un evento pasó a poder usar varios cuestionarios a la vez. Lo pidió el dueño: más de un cuestionario por campamento sin complicar a nadie, en orden fijo o en cualquier orden según lo decida la administración con una casilla, y un botón para crear un cuestionario nuevo, que Cuestionarios no tenía. La regla de la ronda: **con un solo cuestionario, ninguna pantalla cambia** (sin opción, sin "de N", sin casilla de orden; las pruebas lo comprueban en la entrevista, la ficha y las listas). En la misma ronda, quién lee las respuestas confidenciales pasó a decidirse por evento, la tabla de Camperos se ordena por columna y la presentación respeta el equipo elegido en Reportes. Los permisos de cada rol no cambiaron; lo nuevo es que administración y pastoral deciden quién lee las respuestas confidenciales de su evento.

| Qué | Lo que cambió |
|---|---|
| Cuestionarios | "Nuevo cuestionario" (borrador, versión 1). "En este evento", en orden, con "Subir", "Bajar" y "Quitar", y con dos o más la casilla "Los camperos los responden en este orden". En la versión, "Usar en este evento", "Usar esta versión en este evento" y "Dejar de usar en este evento". Configuración ya no elige el cuestionario: lo enlaza |
| Entrevista | "¿Qué cuestionario?" en "Antes de empezar"; "Continuar con «…»" en "Entrevista completada"; "Ya terminaste este cuestionario." en la computadora del campero; una fila por cuestionario en la ficha |
| Listas y cifras | "1 de 2 cuestionarios" para quien respondió algunos (listas, lista de respaldo, lista descargada); "Completadas", "Avance" y "Ritmo" cuentan camperos que terminaron todos; los resúmenes de respuestas, por cuestionario (métricas versión 2); Mi inicio dice "Llevas X de Y con todo." y cuántos van "a medias", y "Completadas hace poco" nombra el cuestionario de cada fila |
| Respuestas confidenciales | Una página para administración y pastoral con tres niveles. Abrir no es retroactivo y cerrar aplica de inmediato; la frase del campero, la nota de la pregunta, el recordatorio de "Entrega la computadora" y la hoja en papel siguen al nivel, y el editor de preguntas no promete uno (cada evento decide); la etiqueta "Confidencial" queda junto a la respuesta, una respuesta oculta lleva la de su nivel ("Sensible" o "Confidencial") y la pantalla de corrección registra la consulta como la ficha |
| Camperos | Cada encabezado ordena la tabla (`?orden=`, `?dir=`), con `aria-sort`, una marca dibujada con CSS (la fuente no trae el glifo) y palabras para lector de pantalla |
| Presentación | Abierta desde un equipo en Reportes, muestra ese equipo (`?alcance=`); un corte de equipo ya era del equipo |

126 pruebas nuevas en cinco módulos: `test_multi_questionnaires.py` (34), `test_multi_questionnaires_admin.py` (43), `test_multi_questionnaires_flow.py` (31), `test_multi_questionnaires_metrics.py` (13) y `test_participants_sort.py` (5), en `events/tests/`. `e2e/all_urls.py` suma "Nuevo cuestionario" y "Respuestas confidenciales" (de administración y de pastoral).

**Límites.** Un campero responde un cuestionario a la vez: no puede tener dos entrevistas en curso. Agregar un cuestionario a un evento con entrevistas completadas regresa a esos camperos a "faltan" y baja el avance. Los resúmenes de respuestas cuentan solo la versión que cada cuestionario usa ahora. El aviso de privacidad del evento es texto libre y se alinea a mano con el nivel confidencial (`privacy-and-safeguarding.md`). La prueba de carga sigue con un solo cuestionario. `events/0011` no es compatible con el código anterior entre `migrate` y `up -d` (`deployment.md`).

**Resultado.**

| Prueba | Resultado |
|---|---|
| `pytest` después de integrar (`DEBUG=true python -m pytest -q --ignore=e2e`) | 1292 pruebas pasan, 6 omitidas (solo corren en PostgreSQL), SQLite |
| `manage.py test` | 1298 pruebas, OK (las mismas 6 omitidas), SQLite |

### Ronda 17 · cada rol sabe qué hacer

El 26 y 26 de octubre de 2026 cada pantalla pasó a responder tres cosas para quien la abre: dónde está, qué hacer ahora y cómo. Es la parte de estructura que la auditoría (`auditoria-ux-2026-10.md`) dejó para después del campamento. Atrio se quedó: los mismos tokens y el mismo trato de cada componente. Tampoco cambiaron los permisos ni las reglas del negocio. Cambiaron el orden de cada pantalla, lo que muestra, sus palabras y algunos componentes (la barra, el plegable en línea, el selector de "Asignar"). Las decisiones están en `design-direction.md` y las pantallas, en `design-system.md` (§2 y §4).

Siete paquetes en paralelo, cada uno en su rama y con su propio módulo de pruebas, integrados después en una sola rama:

| Paquete | Lo que cambió | Pruebas nuevas |
|---|---|---|
| Marco | Una sola barra para todas las pantallas con sesión (`partials/ui/bar.html`); tres pasos (una fila, los enlaces en su propia fila, "Menú" solo en el celular); "Campamentos" como única palabra para la lista; "Ayuda" con las cinco tareas de cada rol | 26, en `events/tests/test_ux_marco.py` |
| Coordinación y campero | "Esperan algo de ti" en Mi inicio y "Completadas hace poco" en Mi avance; la línea de la computadora; Mis camperos por lo que falta, con tres estados vacíos verdaderos; volver al origen al decidir; la bienvenida del campero con "¿Quién lee tus respuestas?" | 31, en `events/tests/test_ux_coordinator.py` |
| Pastoral | El Resumen en el orden cómo va, qué coordinar (con enlaces), equipos, cuánto falta y necesidades; un solo mensaje sin camperos; la presentación con su franja de controles, letra chica de 22 px y la hora de cada dato | 21, en `events/tests/test_ux_pastoral.py` |
| Seguimiento | Orden por urgencia, filtro "Responsable", "Asignar" en el renglón, "Mostrando 200 de N", volver al origen | 48, en `events/tests/test_ux_seguimiento.py` |
| Administración: inicio y configuración | "N de M pasos listos", con lo pendiente primero y lo listo plegado; un solo renglón de cortes; Configuración por secciones, con índice y aviso al salir | 24, en `events/tests/test_ux_admin_inicio.py` |
| Administración: alta y listas | El nombre corto se forma solo; un mensaje listo para enviar; subir otro archivo al importar; "Datos técnicos" en el editor; renglones de camperos con tres datos por línea; "Ya no participan"; "Ninguna" en Computadoras en uso | 46, en `events/tests/test_ux_admin_flujos.py` |
| Discipulado | La navegación como datos y apagada con el módulo; una palabra por estado de seguimiento; el inicio del mentor y el panorama de la iglesia; las invitaciones en renglones | 45, en `core/test_ux_discipulado.py` |

En total, 241 pruebas nuevas en siete módulos. `e2e/all_urls.py` suma la Ayuda del coordinador, las siete pantallas del pastor en el discipulado, la ficha de un participante para el mentor y la lección del participante.

**Resultado.** Las cifras son del verificador, sobre la rama integrada.

| Prueba | Resultado |
|---|---|
| `manage.py test` después de integrar | 1172 pruebas, OK (6 omitidas: solo corren en PostgreSQL), SQLite en serie |
| `e2e/review-all.mjs` (cada pantalla en tres anchos, con axe, desbordes y un solo `h1`) | 225 comprobaciones (75 pantallas × 3 anchos, 10 más que en la ronda 16); 0 violaciones de axe, 0 desbordes, un `h1` en todas; las 3 «con problema» son la sonda d-403 esperada |
| `e2e/station-flow.mjs` | OK: flujo de estación completo (asistida + supervisada) |

### Ronda 16 · Atrio

El 26 de octubre de 2026 toda la aplicación pasó de Rótulo a Atrio (`design-direction.md`): Figtree en vez de Recursive y Atkinson, petróleo en vez de añil, ningún amarillo, el panel tinte de lo que sigue en vez del letrero, bordes de 1.5 px, radios de 10 px (controles) y 4 px (marcas), sin sombras, el anillo de foco de tinta, movimiento con propósito (`design-system.md`, §Movimiento), las páginas públicas de una columna centradas y una portada nueva.

**Método.** El mismo de la ronda 14: `e2e/all_urls.py` arma la lista de pantallas de cada cuenta (las del evento, las de discipulado y las páginas sin sesión, entre ellas la portada) y `e2e/review-all.mjs` visita cada una en tres anchos (1440 × 900, 820 × 1180 y 390 × 844) con axe-core (WCAG 2.0, 2.1 y 2.2, A y AA), y revisa que no haya desborde horizontal, que haya un solo `h1` y que cargue `rotulo.css`. `e2e/design-review.mjs` recorre el flujo de la entrevista asistida y del campero. La presentación se revisa a 1920 × 1080 en Cal y Noche. Solo datos sintéticos de `seed_camp_demo`, sembrados de nuevo antes de la ronda.

**Resultado.**

| Prueba | Resultado |
|---|---|
| `e2e/review-all.mjs` | **195 comprobaciones de pantalla y ancho (65 pantallas × 3 anchos): 0 problemas de axe, 0 desbordes horizontales y un solo `h1` en todas.** Lo único que marca son las pruebas intencionales de 403 (una cuenta de la iglesia que abre la administración de un evento), que deben responder 403 |
| `e2e/station-flow.mjs` | Correcto: asistida, supervisada, confinamiento del campero, opción exclusiva, desbloqueo |
| Movimiento | Revisado con el movimiento normal y con "reducir movimiento" (`prefers-reduced-motion: reduce`), que lo apaga todo |
| Capturas y videos | Las capturas de abajo, en `docs/img/atrio/`. Los videos de la revisión quedan fuera del repositorio |

**Capturas.** Están en `docs/img/atrio/`. El número del nombre es el ancho de la ventana en píxeles; las de la presentación son de 1920 × 1080.

| Captura | Qué muestra |
|---|---|
| ![Portada en teléfono](img/atrio/portada-390.png) | `portada-390.png`: La portada (`/`) en teléfono: el titular, "Entrar a Emmaus" y, justo debajo, el enlace para quien trae el código del evento. |
| ![Portada en escritorio](img/atrio/portada-1440.png) | `portada-1440.png`: La portada en escritorio: una sola columna sin fotos, con las tres promesas y quién usa Emmaus. |
| ![Entrar en teléfono](img/atrio/entrar-390.png) | `entrar-390.png`: Entrar en teléfono: la misma columna que la portada y un solo botón. |
| ![Inicio del coordinador en teléfono](img/atrio/coordinador-inicio-390.png) | `coordinador-inicio-390.png`: El inicio del coordinador en teléfono, con el panel de lo que sigue: "¿A quién vas a entrevistar?" y la búsqueda. |
| ![Inicio del coordinador en escritorio](img/atrio/coordinador-inicio-1440.png) | `coordinador-inicio-1440.png`: El inicio del coordinador en escritorio: el panel, "Estás en Computadora 4", el conteo y las dos columnas de renglones. |
| ![Una pregunta de la entrevista en teléfono](img/atrio/pregunta-390.png) | `pregunta-390.png`: Una pregunta de la entrevista asistida en teléfono: el camino, la pregunta como título y las opciones de 64 px. |
| ![Una pregunta del campero en teléfono](img/atrio/campero-pregunta-390.png) | `campero-pregunta-390.png`: La misma clase de pregunta cuando el campero responde solo, sin navegación. |
| ![Revisión de respuestas en teléfono](img/atrio/revision-390.png) | `revision-390.png`: Revisar las respuestas en teléfono: la pregunta arriba, la respuesta abajo y "Cambiar" a la derecha. |
| ![Resumen del pastor en escritorio](img/atrio/pastor-resumen-1440.png) | `pastor-resumen-1440.png`: El resumen del pastor: cada pregunta con su respuesta en una frase y la evidencia (gráficas y equipos) a la derecha. |
| ![Mi equipo del tutor en escritorio](img/atrio/tutor-equipo-1440.png) | `tutor-equipo-1440.png`: Mi equipo del tutor: "Tu equipo lleva X de Y." y una fila por coordinador con su conteo. |
| ![Inicio de administración en escritorio](img/atrio/admin-inicio-1440.png) | `admin-inicio-1440.png`: El inicio de administración: "Preparación" con el estado de cada paso. |
| ![Camperos de administración en escritorio](img/atrio/admin-camperos-1440.png) | `admin-camperos-1440.png`: La lista de camperos de administración, la pantalla más densa, con cifras tabulares. |
| ![Presentación en Cal](img/atrio/presentacion-cal.png) | `presentacion-cal.png`: La presentación a 1920 × 1080 en Cal, para salones con luz. |
| ![Presentación en Noche](img/atrio/presentacion-noche.png) | `presentacion-noche.png`: La misma diapositiva en Noche, para salones oscuros. |

### Rondas 0 a 15 · Rótulo

| Ronda | Lo que vimos | Lo que cambió |
|---|---|---|
| 0 | La versión anterior: crema, serif, terracota, "·" y "→" (ver `design-direction.md`) | Se diagnosticó y se exploraron tres conceptos |
| 1 | Primera versión de Rótulo. La barra del coordinador partía "Mi / inicio" y "Cerrar / sesión". "Pendiente" en rojo (el rojo es solo para riesgo). La pregunta dejaba "fe?" sola en otra línea. La barra de la entrevista era más ancha que la columna. La revisión partía las preguntas largas en una columna angosta y "Sin responder" se veía como respuesta. "5 h 33 / min" y "16:14." partidos. Etiquetas de hora encimadas ("09:0010:00"). La cifra "253" chocaba con la proyección | Barras al ancho de la página, sin cortes de línea, "Menú" desde 1180 px. Estado ordinal (contorno, añil claro, añil). `text-wrap: balance` en títulos. Barra alineada a la columna. Revisión con la pregunta arriba y la respuesta abajo. Cifras sin corte. Etiquetas de gráfica con espaciado garantizado |
| 2 | En escritorio, el conteo dejaba vacía la mitad derecha. En teléfono, "Cambiar" quedaba encima de la pregunta (una regla general le ganaba a la de respuestas) | El avance pasó a la columna izquierda, arriba de "Por entrevistar". Regla de teléfono corregida |
| 3 | La bienvenida del campero repetía "Prefiero no responder" (ya venía en el texto del cuestionario) y usaba viñetas cuadradas genéricas. La proyección decía solo "18:48" | Una promesa nueva con el número de preguntas; la repetida desaparece si el texto ya la trae; viñetas como marcas de sendero. "Hacia las 18:48" |
| 4 | **axe: 0 problemas en 48 combinaciones.** Pero en teléfono las gráficas del pastor se encogían a texto de unos 7 px, las cifras iban una por renglón y "42 %" se partía. En entrega, conflicto y recuperar, una etiqueta encima del título | Dibujo compacto de cada gráfica para teléfono (360 px, texto de 12 px a su tamaño real). Cifras de dos en dos. Espacio de no separación en "42 %". Las etiquetas pasaron a la entradilla ("Solo para coordinadores. …") |
| 5 | "Hacia las 16:43" cruzaba la línea punteada | La etiqueta va arriba del punto final, donde la línea nunca pasa |
| 6 | La prueba de navegador no podía marcar las opciones: el `input` medía 1 px y quedaba debajo de la marca pintada. En el letrero, el botón con foco quedaba amarillo sobre amarillo | Técnica de GOV.UK: el `input` real, transparente, de 44 × 44 px, encima de la marca. En el letrero el foco usa el negativo del rotulista (fondo carbón, letra maíz, anillo blanco) |
| 7–8 | La búsqueda del letrero llevaba a la página de resultados **del sistema anterior**: cada entrevista pasaba por nuevo → anterior → nuevo | La búsqueda se migró: mismo letrero con lo escrito, resultados en renglones con estado y acción |
| 9 | **axe: 0 problemas en 51 combinaciones** (17 pantallas × 3 anchos). Prueba de navegador completa | — |
| 10 | **Revisión independiente** (otro agente intentó refutar que no cambiaron permisos, exposición de datos, inyección ni comportamiento). Permisos, datos e inyección: sin hallazgos. Encontró dos errores reales: (1) al poner "Atrás" antes del formulario, **Enter regresaba a la pregunta anterior** en el modo campero, porque el navegador usa como botón por omisión el primer botón de envío del formulario en el orden de la página; en la entrevista asistida Enter ya mandaba "Guardar y salir" desde antes del rediseño. (2) Con un ritmo muy lento (más de 12 horas para terminar) la proyección aplastaba la gráfica contra la orilla izquierda. Además: "Cambiar de evento" y "Discipulado" (administración) no se alcanzaban en la barra del pastor en escritorio | El botón "Continuar" oculto ahora es el primero del documento (bloque `default_submit` antes de la barra): Enter siempre avanza, comprobado en Chromium y con pruebas que fallan si se quita. La proyección solo se dibuja si el término cae dentro de 12 horas. La barra del pastor muestra "Cambiar de evento" y, para administración, "Discipulado" |
| 11 | 0 problemas de axe en 51 combinaciones, prueba de navegador completa, 196 pruebas en PostgreSQL | — |
| 12 | Se migraron las pantallas que quedaban del coordinador (elegir computadora, Mis camperos, pendientes, Mi avance, registrar, ficha del campero, ficha de la entrevista, riesgo, seguimientos, lista de respaldo). En la revisión: "Computadora 10" se partía en dos líneas; los campos del formulario salían angostos (los controles de Django no traen clase); en Mi avance la cifra final tapaba "Total 31" y la cuadrícula decía 12, 25, 37 | Cuadrícula de computadoras más ancha. Los controles dentro de `.field` se ven como `.input`. La cifra final va debajo del punto cerca de la meta; la cuadrícula elige pasos redondos |
| 13 | El resumen del pastor en teléfono medía 6,600 px | Los totales de respuestas van en un plegable que arranca cerrado en teléfono (4,575 px, 31 % menos) y conserva lo que la persona abrió aunque la página se actualice sola, comprobado con una actualización real. **axe: 0 problemas en 84 combinaciones** (28 pantallas × 3 anchos); 198 pruebas |
| 14 | **Toda la aplicación.** Seis implementadores en paralelo, cada uno en su propia rama y con sus propias capturas en tres anchos y axe: liderazgo (tutor, coordinadores, entrevistas, pendientes, equipo, avance), reportes y presentación, administración, público (entrar, contraseñas, errores, eventos, inicio), y discipulado (gestión y acompañamiento). Luego se integró todo, se retiró el sistema anterior y se revisó de nuevo. Hallazgos al integrar: la fusión de las secciones de CSS perdió tres llaves de cierre y todo lo que seguía (discipulado y la presentación) dejó de aplicarse; el borde rojo de error perdía contra la regla de los campos; la ayuda de los campos no llegaba a los lectores de pantalla; el resumen de errores repetía "Este campo es obligatorio." sin decir cuál; los formularios de contraseña de Django hablaban de usted; la barra de administración apretaba el nombre del evento en cuatro renglones; el enlace "Cambiar de evento" era un objetivo de menos de 24 px; la lista de respaldo desbordaba en teléfono | La hoja se reconstruyó sección por sección y una prueba revisa que no quede ningún bloque abierto. Error en rojo con la regla correcta. Cada control nombra su pista y su error (`aria-describedby`, `aria-invalid`) y el resumen nombra el campo. Formularios de contraseña en tú. Barra de dos renglones con más de cinco enlaces. Objetivo de 24 px. La tabla de respaldo se desplaza dentro de su región. **Revisión final: 0 problemas de axe, sin desbordes y un solo `h1` en 57 pantallas de 8 cuentas × 3 anchos**; la presentación, revisada a 1920 × 1080 en Cal y Noche |
| 15 | **Pendientes de la ronda 14.** El PDF de los cortes seguía con Literata y la paleta anterior, unía datos con "·" y decía "de forma automático"; Django respondía en usted ("Seleccione una opción válida", "Su contraseña…", "fichero"); con varios borradores con IA en una pregunta los campos repetían `id`, y si la revisión no era válida el error no se mostraba; quien entraba sin rol veía un 403; la configuración inicial pedía leer un aviso que no aparecía; la lista de personas agrupaba a quien está en dos iglesias por su primer rol de cualquiera de ellas | PDF con Recursive Casual y Atkinson Next, añil para datos, chile solo para riesgo, frases como en la pantalla del corte. Catálogo `locale/es_MX` con 59 mensajes de Django en tú y una prueba que impide que el `.mo` se quede atrás. Cada borrador con su propio prefijo y su error a la vista. Sin rol, la lista de eventos explica quién puede agregarte. El aviso para participantes aparece en la configuración inicial. La lista de personas usa el rol de esta iglesia, en una sola consulta |

### Ronda 14 en capturas

| Reportes | Un corte | Importar camperos | Mentoría |
|---|---|---|---|
| ![Reportes](img/rotulo/reportes.png) | ![Un corte completo](img/rotulo/corte.png) | ![Revisión de la importación](img/rotulo/admin-importar.png) | ![Inicio del mentor](img/rotulo/mentor.png) |

| El PDF del corte (ronda 15) |
|---|
| ![Primera página del PDF de un corte](img/rotulo/pdf-corte.png) |

### Antes y después de algunos hallazgos

| Ronda 1: barra encimada | Final |
|---|---|
| ![Barra del coordinador en la ronda 1](img/rotulo/rondas/r1-barra-encimada.png) | ![Barra del coordinador final](img/rotulo/rondas/r7-barra.png) |

| Ronda 1: "fe?" huérfana | Ronda 1: cifras partidas | Ronda 1: revisión en columnas angostas |
|---|---|---|
| ![Pregunta con la última palabra sola](img/rotulo/rondas/r1-pregunta-huerfana.png) | ![Cifras partidas en dos líneas](img/rotulo/rondas/r1-cifras-partidas.png) | ![Revisión con columnas angostas](img/rotulo/rondas/r1-revision-columnas.png) |

| Ronda 2: mitad derecha vacía | Ronda 2: "Cambiar" encima |
|---|---|
| ![Coordinador con espacio vacío](img/rotulo/rondas/r2-espacio-vacio.png) | ![Cambiar encima de la pregunta](img/rotulo/rondas/r2-cambiar-arriba.png) |

| Ronda 4: gráfica ilegible en teléfono | Ronda 5: dibujo compacto |
|---|---|
| ![Gráfica pequeña en teléfono](img/rotulo/rondas/r4-grafica-movil.png) | ![Gráfica compacta en teléfono](img/rotulo/rondas/r5-grafica-movil.png) |

![Gráficas de escritorio con proyección y hora en curso (ronda 3)](img/rotulo/rondas/r3-etiquetas-graficas.png)

## Pantallas finales

### Coordinador

| Escritorio | Tableta | Teléfono |
|---|---|---|
| ![Coordinador, escritorio](img/rotulo/coordinador-escritorio.png) | ![Coordinador, tableta](img/rotulo/coordinador-tableta.png) | ![Coordinador, teléfono](img/rotulo/coordinador-movil-completa.png) |

| Con una entrevista abierta | Búsqueda | Búsqueda en teléfono |
|---|---|---|
| ![Entrevista abierta](img/rotulo/coordinador-en-curso.png) | ![Búsqueda](img/rotulo/busqueda.png) | ![Búsqueda, teléfono](img/rotulo/busqueda-movil.png) |

Página completa en escritorio: ![Coordinador, página completa](img/rotulo/coordinador-escritorio-completa.png)

### Entrevista

| Antes de empezar | Error al empezar (teléfono) | Pregunta |
|---|---|---|
| ![Antes de empezar](img/rotulo/entrevista-antes.png) | ![Error al empezar](img/rotulo/entrevista-antes-error-movil.png) | ![Pregunta](img/rotulo/entrevista-pregunta.png) |

| Pregunta (tableta) | Opción múltiple (teléfono) | Completada |
|---|---|---|
| ![Pregunta en tableta](img/rotulo/entrevista-pregunta-tableta.png) | ![Opción múltiple](img/rotulo/entrevista-multiple-movil.png) | ![Completada](img/rotulo/entrevista-completada.png) |

| Revisión | Revisión (teléfono) | Entrega al campero |
|---|---|---|
| ![Revisión](img/rotulo/entrevista-revision.png) | ![Revisión en teléfono](img/rotulo/entrevista-revision-movil.png) | ![Entrega](img/rotulo/entrega.png) |

### Campero

| Bienvenida | Pregunta (teléfono) | Revisión (tableta) | Gracias (teléfono) | Recuperar |
|---|---|---|---|---|
| ![Bienvenida](img/rotulo/campero-bienvenida.png) | ![Pregunta del campero](img/rotulo/campero-pregunta-movil.png) | ![Revisión del campero](img/rotulo/campero-revision-tableta.png) | ![Gracias](img/rotulo/campero-gracias-movil.png) | ![Recuperar la computadora](img/rotulo/campero-recuperar.png) |

### Pastor

| Escritorio | Teléfono |
|---|---|
| ![Pastor, escritorio](img/rotulo/pastor-escritorio.png) | ![Pastor, teléfono](img/rotulo/pastor-movil.png) |

Páginas completas: ![Pastor, escritorio completo](img/rotulo/pastor-escritorio-completa.png) ![Pastor, tableta completa](img/rotulo/pastor-tableta-completa.png)

En teléfono, con los totales plegados: ![Pastor, teléfono completo](img/rotulo/pastor-movil-plegado.png)

### El resto del coordinador

| Elegir computadora | Mis camperos | Entrevistas pendientes |
|---|---|---|
| ![Elegir computadora](img/rotulo/computadora.png) | ![Mis camperos](img/rotulo/mis-camperos.png) | ![Entrevistas pendientes](img/rotulo/pendientes.png) |

| Mi avance | Registrar campero (teléfono) | Ficha del campero |
|---|---|---|
| ![Mi avance](img/rotulo/mi-avance.png) | ![Registrar campero](img/rotulo/registrar-movil.png) | ![Ficha del campero](img/rotulo/ficha.png) |

| Ficha de la entrevista | Reportar riesgo (teléfono) | Seguimiento |
|---|---|---|
| ![Ficha de la entrevista](img/rotulo/entrevista-detalle.png) | ![Reportar riesgo](img/rotulo/riesgo-movil.png) | ![Seguimiento](img/rotulo/seguimiento-detalle.png) |

| Seguimientos | Lista de respaldo |
|---|---|
| ![Seguimientos](img/rotulo/seguimientos.png) | ![Lista de respaldo](img/rotulo/respaldo.png) |

## Comparación con las referencias

| Referencia | Qué se buscaba | Cómo quedó |
|---|---|---|
| Planning Center, estación atendida | Un campo de búsqueda como inicio; dos letras y dos letras; la estación con nombre | El letrero del coordinador **es** la búsqueda, con el ejemplo "mat jim" (que ahora funciona). "Estás en Computadora 4" en la barra. A diferencia de Planning Center, no hay fondo degradado ni fotos |
| Tally, una pregunta por paso | Silencio alrededor de la pregunta; "Atrás" lejos de "Siguiente" | Igual de silencioso, con controles casi el doble de altos (64 px contra 36 px) y el error de GOV.UK en vez del de 12 px |
| GOV.UK, pregunta, revisión, errores, confirmación | Los mismos momentos y la misma claridad | Mismos patrones, otra voz: la pregunta pintada a mano, la confirmación en añil, el foco amarillo con borde de tinta. Las opciones son tablas completas en vez de círculos sueltos |
| Airtable, tablero | Orden de lo general a lo particular | Mismo orden, otra forma: el pastor lee una respuesta antes de ver una cifra, y no hay tarjetas ni donas |
| Typeform, Udemy, IKEA | Opciones grandes, una decisión por pantalla | Confirmado |
| Square, Fingerprint, Midday, Better Stack | Lo que no queríamos parecer | Ninguna pantalla tiene tarjetas de cifras iguales, tema oscuro por defecto, Inter o letra monoespaciada |

## Crítica final

| Criterio | Evaluación |
|---|---|
| **Tipografía** | Dos voces con una regla que se entiende al verla. La letra pintada se reserva para lo que dice una persona y nunca baja de 28 px. La hiperlegible lleva todo el texto pequeño. Riesgo aceptado: el cero con diagonal de Atkinson en horas y cifras |
| **Jerarquía** | En cada pantalla hay una sola cosa más fuerte: el letrero, la pregunta o el titular del pastor. Lo segundo (conteo, opciones, evidencia) va claramente debajo |
| **Alineación** | Una sola orilla izquierda por pantalla; las barras se alinean con el contenido en las tres anchuras. Las cifras en columnas usan números tabulares |
| **Espacio** | Escalones de 4 px; espacio grande entre temas y chico dentro de cada uno. El informe del pastor respira más que la versión anterior sin perder densidad |
| **Contraste** | Texto de 5.3:1 o más; controles de 3.6:1 o más; foco visible en blanco, en añil y en maíz. axe: 0 problemas |
| **Legibilidad** | Base de 18 px, opciones de 20 px, ninguna línea de más de 60 a 65 caracteres en lectura, gráficas legibles en teléfono |
| **Densidad** | Baja donde se actúa (coordinador, entrevista), media donde se lee (pastor). La administración, cuando se migre, será la única pantalla densa |
| **Originalidad** | El letrero pintado, el conteo con una marca por persona y el camino de marcas no salen de ninguna plantilla de tablero. Lo que sigue siendo convencional está en la lista de abajo |
| **Consistencia** | Las mismas piezas resuelven las cuatro experiencias: el letrero aparece en el coordinador y en la búsqueda (y en la propuesta del tutor); las marcas de avance son las mismas en el conteo, la regla, el estado y el camino. La inconsistencia que queda es entre pantallas migradas y no migradas |
| **Accesibilidad** | axe 0 en 51 combinaciones; foco de GOV.UK adaptado; objetivos de 44 px o más; `aria-label` completo en todas las marcas; movimiento reducido; colores forzados |
| **Facilidad de uso** | El coordinador empieza a escribir sin tocar ningún menú; la búsqueda acepta "mat jim"; el campero sabe cuántas preguntas son; el pastor puede leer solo la columna izquierda. **Falta probarlo con voluntarios reales** |

## Lo que todavía se ve genérico

1. **Los renglones de lista** (nombre, dato, botón a la derecha) son el patrón más común que existe. Se quedaron porque son lo más claro, pero no tienen nada propio.
2. **La fila de cifras del pastor** sigue pareciéndose a una fila de indicadores. Ya no son tarjetas y quedan debajo de la frase, pero la siguiente versión debería meter esas cifras en la respuesta escrita.
3. **Los encabezados de lista con regla gruesa** (`.heading`) son un recurso editorial común.
4. **Los totales de respuestas** del pastor son barras horizontales estándar en múltiplos pequeños: correctas, sin carácter.

## Pendientes

- **Conteo en teléfono:** con más de 40 camperos ocupa dos renglones de marcas; funciona, pero conviene revisar con equipos grandes. Arriba de 80 se cambia solo a una regla.
- **Proyector y voluntarios reales:** no se ha probado en un proyector ni con personas. Las propuestas de presentación están validadas en color, no en sala.
- **Peso:** Recursive agrega 109 KB (latín), una vez por dispositivo gracias al caché de WhiteNoise.

## Verificación

| Prueba | Resultado |
|---|---|
| `manage.py test` en PostgreSQL 16 | 198 pruebas, todas correctas (incluye `events/tests/test_rotulo.py`: alcance de hojas de estilo, conteo y regla, proyección y su límite de 12 horas, etiquetas de gráficas sin choques, búsqueda por palabras en los dos buscadores, Enter avanza en la entrevista y en el modo campero, búsqueda de administración) |
| `manage.py test` en SQLite | Las mismas, con 4 omitidas (las de concurrencia necesitan PostgreSQL) |
| Revisión independiente | Permisos, exposición de datos e inyección sin hallazgos; los dos errores que encontró están corregidos y cubiertos por pruebas (ronda 10) |
| `e2e/station-flow.mjs` | Correcto: asistida, supervisada, confinamiento del campero, opción exclusiva, desbloqueo |
| axe-core 4.10.2 (WCAG 2.0, 2.1, 2.2, A y AA) | 0 problemas en 28 pantallas × 3 anchos |
| `manage.py check` y `collectstatic` con `DEBUG=false` | Sin problemas; la hoja y las fuentes nuevas pasan por el manifiesto de WhiteNoise |
