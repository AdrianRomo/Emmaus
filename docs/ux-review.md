# Revisión UX con capturas · "Rótulo"

## Método

- **Herramienta:** Playwright 1.63 (Chromium), `es-MX`, zona horaria America/Mexico_City. El guion está en el repositorio: `e2e/design-review.mjs` recorre todo el flujo (inicio del coordinador, búsqueda, antes de empezar con y sin error, preguntas, opción múltiple, revisión, completada, entrega, modo campero completo, recuperar la computadora, inicio con una entrevista abierta y resumen del pastor) y, si se le da `AXE`, corre axe-core en cada pantalla y cada ancho.
- **Anchos:** escritorio 1440 × 900, tableta 820 × 1180, teléfono 390 × 844.
- **Datos:** solo sintéticos. `seed_camp_demo` crea un campamento de 400 camperos ficticios; los nombres son combinaciones aleatorias de nombres y apellidos comunes. Antes de cada ronda se volvió a sembrar la base, porque el recorrido abre y termina entrevistas y los tableros cambian.
- **Rondas:** 11 rondas de captura → crítica → cambio. La ronda 0 es la versión anterior, la 10 fue una revisión independiente del código y la 11 es el código final.

## Rondas

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
| 11 | Código final: 0 problemas de axe en 51 combinaciones, prueba de navegador completa, 196 pruebas en PostgreSQL | — |

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

- **Dos sistemas a la vez.** Al ir de "Mi inicio" a "Mis camperos", o de la entrevista a la ficha del campero, la apariencia cambia. Es lo más urgente. Orden propuesto: elegir computadora, Mis camperos, registro, pendientes y avance del coordinador; después tutor y presentación (según las propuestas de `design-direction.md`), administración y discipulado.
- **Elegir computadora** aparece una vez por sesión antes de la primera búsqueda y sigue con el sistema anterior.
- **Resumen del pastor en teléfono:** mide unos 6,500 px, sobre todo por los totales de respuestas. Podrían plegarse.
- **Conteo en teléfono:** con más de 40 camperos ocupa dos renglones de marcas; funciona, pero conviene revisar con equipos grandes. Arriba de 80 se cambia solo a una regla.
- **Proyector y voluntarios reales:** no se ha probado en un proyector ni con personas. Las propuestas de presentación están validadas en color, no en sala.
- **Peso:** Recursive agrega 109 KB (latín), una vez por dispositivo gracias al caché de WhiteNoise.

## Verificación

| Prueba | Resultado |
|---|---|
| `manage.py test` en PostgreSQL 16 | 196 pruebas, todas correctas (incluye `events/tests/test_rotulo.py`: alcance de hojas de estilo, conteo y regla, proyección y su límite de 12 horas, etiquetas de gráficas sin choques, búsqueda por palabras en los dos buscadores, Enter avanza en la entrevista y en el modo campero, búsqueda de administración) |
| `manage.py test` en SQLite | Las mismas, con 4 omitidas (las de concurrencia necesitan PostgreSQL) |
| Revisión independiente | Permisos, exposición de datos e inyección sin hallazgos; los dos errores que encontró están corregidos y cubiertos por pruebas (ronda 10) |
| `e2e/station-flow.mjs` | Correcto: asistida, supervisada, confinamiento del campero, opción exclusiva, desbloqueo |
| axe-core 4.10.2 (WCAG 2.0, 2.1, 2.2, A y AA) | 0 problemas en 17 pantallas × 3 anchos |
| `manage.py check` y `collectstatic` con `DEBUG=false` | Sin problemas; la hoja y las fuentes nuevas pasan por el manifiesto de WhiteNoise |
