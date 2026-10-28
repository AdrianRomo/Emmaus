# Sistema de diseño · Atrio

Todas las pantallas de Emmaus usan este sistema: la portada, el coordinador y la entrevista, el modo campero, tutores, pastores, administración, reportes y presentación, inicio de sesión y errores, y el módulo de discipulado. **Atrio reemplazó a Rótulo el 26 de octubre de 2026.** Rótulo había reemplazado a "Cuaderno de campo" (`emmaus.css`) el 23 de octubre; la historia de las dos y por qué se retiraron está en `design-direction.md`, y la investigación que llevó a Atrio, en `design-research.md` ("Investigación v3").

Atrio es la puerta abierta del templo: blanco, un solo color de acción, frases como títulos y el logotipo de la iglesia como único adorno. Cambió los tokens y el trato de cada componente, no la estructura: los nombres de clase, los marcos, las vistas y el JavaScript son los de Rótulo. Por eso la hoja **conserva su nombre**, `static/css/rotulo.css`, los marcos siguen en `templates/events/rotulo/` y la variable de plantilla sigue siendo `rotulo_shell`.

| | |
|---|---|
| Hoja de estilo | `static/css/rotulo.css`: tokens y componentes compartidos, y al final una sección por área (liderazgo, administración, público, discipulado, reportes y presentación) con lo que solo esa área necesita |
| Base de plantilla | `templates/layout/skeleton.html` (fuentes, hoja, colores propios de la iglesia) |
| Marcos | `events/rotulo/shell_coordinator.html` y `shell_leader.html` para el evento (la variable `rotulo_shell` elige por rol), `events/shell_focus.html` y `shell_kiosk.html` para la entrevista y el campero, `base.html` para la portada, discipulado, inicio de sesión y errores. La barra y su "Menú" son los mismos en `base.html` y en los dos marcos del evento: `partials/ui/bar.html` y `partials/ui/menu_panel.html` |
| Comportamiento | `static/js/emmaus.js`, sin dependencias; depende de atributos `data-*`, no de clases |

La prueba `test_every_screen_of_every_role_is_on_rotulo` recorre las pantallas de cada rol y las públicas (portada, entrar, contraseña, `/unirme/` y la página de un evento), y `test_rotulo_css_blocks_are_balanced` revisa que ningún bloque de la hoja quede abierto (una fusión dejó tres sin cerrar y todo lo que seguía dejó de aplicarse). Las dos conservan el nombre de la hoja.

## 1. Tokens

Todos viven en `:root`, al inicio de `rotulo.css`. Salieron con Rótulo: `--maiz`, `--maiz-tint`, `--anil`, `--anil-hover`, `--anil-tint`, `--carbon`, `--cal`, `--cal-2`, `--b-sign` y `--font-voice`.

### Color

Contrastes calculados sobre blanco.

| Token | Valor | Uso | Contraste |
|---|---|---|---|
| `--page`, `--surface` | `#FFFFFF` | Página; campos, opciones, barras y paneles | — |
| `--hundido` | `#F3F5F6` | Hover, encabezados de tabla, renglón que pide una decisión, avisos | — |
| `--ink` | `#16222B` | Texto y títulos; el anillo de foco | 16.18:1 |
| `--ink-2` | `#44525D` | Texto secundario, entradillas | 8.04:1 |
| `--ink-3` | `#5A6872` | El texto más claro permitido; la marca de la pregunta actual | 5.74:1 |
| `--line` | `#DCE1E4` | Bordes de paneles, separadores, reglas de tabla; nunca el único borde de un control | — |
| `--line-strong` | `#7B8A94` | Bordes de campos, opciones y botones secundarios; el contorno de "sin iniciar" | 3.56:1, cumple WCAG 1.4.11 |
| **`--petroleo`** / `--petroleo-hover` | `#0B6270` / `#084E5A` | Botón principal, enlaces, la orilla de la opción elegida, confirmación, datos | 7.01:1; letra blanca encima, 7.01:1; hover, 9.3:1 |
| `--petroleo-tint` | `#E4F1F3` | Fondo del panel de lo que sigue, de la opción elegida y de los avisos de información | — |
| `--mark` | `var(--petroleo)` | "Estás aquí": el punto de la computadora y el subrayado de la página, la vista o el renglón del menú actuales | 7.01:1 |
| `--chile` / `--chile-tint` | `#B8321C` / `#FBE6E1` | **Solo error y riesgo** | 5.98:1 |

Reglas:

- **El color hace tres trabajos y nada más.** Petróleo es la acción (y los enlaces), el estado ("estás aquí", lo hecho) y los datos. Todo lo demás es tinta y gris.
- **Nada es amarillo.** El maíz de Rótulo salió de todo uso: el letrero, el foco, los avisos y la marca de "estás aquí".
- **Chile nunca es estado normal.** Un seguimiento "pendiente" es la primera etapa (contorno), no un error.
- El color nunca es la única señal: toda marca lleva palabra, cifra o leyenda.
- Sin degradados, sin brillos y sin sombras de profundidad (el anillo blanco del foco es la única `box-shadow`).
- Si la iglesia activa sus colores propios, `layout/skeleton.html` los asigna así: el color principal a `--petroleo` y `--petroleo-hover`, el color de acento a `--mark` (solo "estás aquí": lo que sigue ya no se pinta con el acento), el fondo a `--page`, la superficie a `--surface`, el texto a `--ink`, el texto tenue a `--ink-3` y el borde a `--line`.

### Datos

El avance es **ordinal** (sin iniciar, en curso, completada), así que usa un solo tono en escalones de claridad:

| Token | Estado | Cal (sobre blanco) | Noche (sobre `#141B33`) |
|---|---|---|---|
| `--data-done` | Completada, atendido | `#0B6270`, 7.01:1 | `#8FC1CA`, 8.63:1 |
| `--data-progress` | En curso, en proceso | `#4C95A2`, 3.43:1 | `#4C95A2`, 4.96:1 |
| `--data-track` | Sin iniciar, pendiente | `#FFFFFF` con contorno `--line-strong`, 3.56:1 | `#141B33` con contorno `#7B8A94`, 4.78:1 |
| `--data-grid` / `--data-axis` | Cuadrícula y eje | `#DCE1E4` / `#7B8A94` | `#2C355A` / `#7B8A94` |

- Una sola serie (respuestas, columnas por hora) usa `--data-done`. Las gráficas de una sola serie no llevan cuadro de leyenda: el título dice qué se mide.
- El dato se dice en una frase: "Llevas 18 de 31." Sin cifras gigantes con una etiqueta chica.
- **Noche** (`.deck--noche`, el proyector en salones oscuros) vuelve a asignar los mismos tokens dentro de la diapositiva: `--page` y `--surface` `#141B33`, `--hundido` `#1D2647`, `--ink` `#F3F5F6` (15.56:1), `--ink-2` `#C9D2D8`, `--ink-3` `#A9B4BC`, `--line` `#2C355A`, `--line-strong` `#7B8A94`, y `--petroleo` y `--mark` `#8FC1CA`.

### Tipografía

Una sola familia: **Figtree**, variable de 400 a 800 (OFL), en un solo archivo, `static/fonts/figtree-latin.woff2`, de 23 KB (latín y latín extendido A). Reemplaza a Recursive (109 KB) y a Atkinson Hyperlegible Next. `--font-ui: "Figtree", system-ui, …`.

| Peso | Para qué |
|---|---|
| 400 | Texto, a 18 px |
| 500 | Etiquetas, metadatos, estado, navegación, el texto de las opciones |
| 700 | Botones, énfasis, nombres en renglones, encabezados de lista, la palabra "Emmaus" |
| 800 | Títulos y la clase `.voice`: títulos de página, preguntas, saludos, el panel de lo que sigue, las cifras |

| Token | Tamaño | Uso |
|---|---|---|
| `--t-caption` | 15 px | Solo texto dentro de gráficas y notas al pie |
| `--t-small` | 16 px | Metadatos, etiquetas de cifras, estado |
| `--t-body` | 18 px | Cuerpo (interlineado 1.5) |
| `--t-lead` | 20 px | Opciones, nombres en renglones, respuestas, entradilla |
| `--t-h3` | 22 px | Encabezado de lista (`.heading`) |
| `--t-h2` | 26 px | El primer tamaño de título en 800: la pregunta del informe, el saludo |
| `--t-h1` | 44 px | El título más grande (con `clamp()` en teléfono) |
| `--t-display` | 44 px | Confirmación y bienvenida: ya no hay un tamaño mayor que el del título |

- `.voice` se queda como clase (las plantillas la ponen en cada título que es una frase), pero ya no es otra letra: es Figtree 800. En Rótulo era la "voz pintada" de Recursive Casual.
- Títulos con `text-wrap: balance`; entradillas con `text-wrap: pretty`.
- Cifras en columnas con `tabular-nums` (la función `tnum` de Figtree); cifras sueltas, proporcionales.
- **Figtree no tiene "≈".** La interfaz nunca lo escribe: dice "unas 85" (ver §3).
- Fuentes propias con su licencia OFL: `static/fonts/` para la pantalla y `events/fonts/` (Figtree 400, 700 y 800 en TrueType) para el PDF. Los archivos de Recursive y de Atkinson salieron con Rótulo.

### Espacio, forma, bordes

- Escalones de 4 px: `--s-1` (4) a `--s-20` (80).
- Dos radios, por función: `--r-mark` 4 px (marcas del conteo y de estado, etiquetas) y `--r-control` 10 px (botones, campos, opciones, paneles, el panel de lo que sigue). Nada en píldora.
- Un borde: `--b-control` 1.5 px en campos, opciones, botones secundarios y el diálogo. Los bordes de tinta de 2 y 3 px de Rótulo salieron.
- Sin sombras. La sombra de "tecla" bajo el botón principal salió con Rótulo.
- Densidad generosa: botón principal de 60 px, opciones de 64 px (también en teléfono), campos de 56 px, objetivos de 44 px como mínimo (`--control`, 48 px, es el alto normal de un botón).
- Anchos: `--w-wide` 80 rem (páginas y barras), `--w-focus` 46 rem (entrevista y campero). Las páginas públicas de una columna (`.auth-card`: portada, entrar, contraseñas, `/unirme/`) van centradas.
- Puntos de quiebre (literales en las consultas): 600 px (teléfono), 760 px (la barra pasa a "Menú"), 900 px (una columna), 1280 px (los enlaces de la barra bajan a su propia fila).
- Movimiento: `--dur-1`, `--dur-2`, `--dur-3`, `--ease` y `--rise` (ver §Movimiento).

### Foco

Un anillo de tinta de 3 px separado del control por 2 px de blanco (16:1): `outline: 3px solid var(--ink); outline-offset: 2px; box-shadow: 0 0 0 2px #fff`. Es el mismo sobre blanco, hundido, tinte o petróleo, y vale para enlaces, botones, campos, opciones (el anillo rodea toda la opción) y `summary`. Rótulo tenía cinco variantes (fondo maíz en enlaces, el negativo del rotulista en el letrero, contorno maíz en campos…).

La única variación es de lugar, no de forma: en los enlaces de la barra, de los filtros y del menú, y en los enlaces de acción de la tabla del corte, el anillo se dibuja hacia adentro (`outline-offset: -3px`) para no salirse de su franja.

## 2. Componentes

### Lo que sigue (`.sign`)
El panel de la tarea: fondo tinte, radio de 10 px, sin borde de tinta. Su título (`.sign__title`) es **una pregunta** ("¿A quién vas a entrevistar?"), debajo una frase (`.sign__text`) y **una** acción rellena de petróleo (`.sign__actions`, `.search`). Es lo siguiente que hay que hacer. **Uno por pantalla, a lo mucho.** Si el contenido no es una acción inmediata, no es un panel. **Si no hay nada que hacer, no hay panel:** el tutor lo ve solo si una entrevista se detuvo, y la administración solo si algo impide entrevistar. La clase conserva el nombre del letrero de Rótulo.

Cuando hay varias cosas pendientes, el panel muestra solo la primera. En el inicio de administración el orden es: "Confirma los procedimientos de la iglesia", "Elige el cuestionario de las entrevistas", "Registra o importa a los camperos", las solicitudes de acceso al equipo ("2 personas piden acceso al equipo") y "Asigna coordinador a N camperos". Lo demás espera en "Preparación" (§4).

### Estás aquí (`.here`)
Un punto petróleo (`.here__blaze`) y el nombre del lugar: "Estás en Computadora 4". Va en la barra del coordinador y en la de la entrevista. Sin amarillo.

### Botones (`.btn`)

| Variante | Uso |
|---|---|
| `.btn--primary` | La acción principal. Petróleo con letra blanca (hover `#084E5A`), sin sombra. Uno por formulario; en una pantalla con un solo formulario, uno por pantalla |
| `.btn` / `.btn--secondary` | Blanco con borde de control de 1.5 px. Acciones de renglón ("Entrevistar", "Decidir") y salidas ("Guardar y salir") |
| `.btn--link` | Alternativas de menor peso ("Mejor hago yo las preguntas", "Volver a mi inicio") |
| `.btn--danger` | Solo para borrar ("Sí, descartar y borrar respuestas"), en chile |
| `.btn--danger-outline` | Con `.btn--secondary`: letra y borde chile sobre blanco. Solo para la acción de riesgo ("Reportar una situación de riesgo", en la ficha del campero y en la revisión) |
| `.btn--large` / `.btn--small` | 60 px / 44 px de alto (normal: 48 px) |

El texto del botón dice lo que pasa: "Comenzar entrevista", "Finalizar entrevista", "Entregar la computadora". Sin flechas.

**Un botón relleno por formulario.** Una pantalla con varios formularios lleva uno en cada uno, y el botón dice qué guarda. Configuración tiene dos: "Guardar datos del evento" y "Guardar cortes y privacidad" (más "Confirmar" mientras los procedimientos no estén confirmados). Antes eran dos columnas con su propio "Guardar": quien editaba una y guardaba la otra perdía el cambio sin aviso (auditoría UX-14), y un "Guardar" a veces relleno y a veces no, no enseñaba cuál era la acción (UI-26).

### Opciones (`.choices`, `.choice`)
Opciones de 64 px a todo el ancho (también en teléfono), texto de 20 px en 500, borde `--line-strong` de 1.5 px, radio de 10 px, marca redonda (o cuadrada en `.choice--multi`) de 28 px. Elegida: fondo tinte, orilla y marca petróleo (la cuadrada lleva palomita blanca); las palabras conservan su tinta. Al elegir, la marca crece de 1 a 1.06 y vuelve (§Movimiento). `.choice--quiet` para "Prefiero no responder" y opciones de salida, después de "o bien" (`.choices__or`). El `input` real queda transparente, de 44 × 44 px, exactamente encima de la marca dibujada (técnica de GOV.UK): recibe clics, teclado y lectores de pantalla. `.choice__sub` agrega una línea de explicación.

### Pregunta (`.question`)
La pregunta es el `h1` (dentro de la `legend` en opciones, o envolviendo la `label` en texto libre), en Figtree 800 y a lo mucho de 22 caracteres por línea. Pista (`.question__help`) a 20 px. Las preguntas que se revelan (`.reveal`) llevan una línea `--line-strong` de 3 px a la izquierda. Confidencial: `.privacy` con la etiqueta `.privacy__tag`.

**Enter avanza.** El navegador envía con Enter el primer botón de envío del formulario en el orden de la página. Por eso `step.html` y `kiosk/step.html` ponen un "Continuar" oculto en el bloque `default_submit`, antes de la barra ("Guardar y salir") y de "Atrás". Cualquier pantalla nueva con botones fuera del formulario debe hacer lo mismo.

### Camino (`.trail`)
"Atrás" arriba a la izquierda (botón de envío que guarda), "Pregunta 2 de 5", la sección a la derecha y una marca de 8 px de alto por pregunta: petróleo las respondidas, la actual rellena de tinta tenue (`--ink-3`) y las que faltan con contorno `--line-strong`. Las marcas son decorativas (`aria-hidden`); el texto da la posición.

### Conteo (`{% tally completados en_curso total %}`)
Una marca por campero, en la escala de datos. Con más de 80 personas se dibuja como regla. Siempre con `role="img"` y la frase completa en `aria-label`. Va con su clave (`.key`).

### Regla (`{% ruler completados en_curso total marcador etiqueta %}`)
Barra apilada con 2 px de separación entre segmentos y, si se pide, una raya de tinta en el promedio. Para avance de equipos y del campamento.

### Por cuestionario (`.quests`, `.table__sub`)
Solo si el evento usa varios cuestionarios. `.quests` (`events/rotulo/_questionnaire_progress.html`) va después de la clave (`.key`) de cada bloque de avance: un título "Por cuestionario" y, por cuestionario, una línea ("«Primer cuestionario»: 320 de 400, 5 en curso.") con su regla; la línea se parte como cualquier frase, nunca `nowrap`. Con dos en una página, cada uno lleva su `quests_id`. En la presentación es su propia diapositiva, en unidades del contenedor y con el piso de 22 px. `.table__sub` es la línea bajo un nombre, en una tabla o en la lista de equipos de un corte (`.teams__name`), a 16 px en `--ink-2`, sin margen: "«Primero»: 3 de 4, «Segundo»: 1 de 4." En las filas de coordinadores (`.crew`) es un `.row__meta` bajo las marcas. La clave cuenta aparte a los que van "a medias" ("2 sin iniciar y 1 a medias") y el titular dice "con los N cuestionarios"; con un solo cuestionario no aparece nada de esto.

### Estado (`{% status "en_curso" %}`, `{% campero_status p %}`, `{% follow_up_status f.status %}`)
Una marca pequeña más la palabra. Las tres marcas de etapa significan solo etapa: completada / atendido / publicada, petróleo; en curso / en proceso, petróleo medio; sin iniciar / pendiente, contorno. Lo que terminó sin hacerse (descartada, cancelado, vencida, retirada, archivado, rechazado, cerrada) va en gris relleno (`status--closed`, `--line-strong`) y lo que espera que alguien revise o decida (revisar, por decidir, pasada al pastor) en medio gris (`status--review`): ningún estado que terminó sin hacerse usa el contorno de "sin iniciar".

En las listas de camperos, `{% campero_status p %}` dibuja el estado del campero; si el evento usa varios cuestionarios, los tres estados llevan la cuenta: "0 de 2 cuestionarios" o "1 de 2 cuestionarios" con la marca de pendiente, "En curso, 1 de 2" con la de en curso y "2 de 2 cuestionarios" con la de completada. Es una sola regla (`campero_progress_label`), también para la lista descargada y la de respaldo. Las cuentas salen de `annotate_questionnaire_progress`; sin ellas, o con un solo cuestionario, la marca es la misma que `{% status p.interview_status %}`.

### Renglones (`.rows`, `.row`)
Nombre a 20 px en 700 (`.row__title`), dato debajo a 16 px (`.row__meta`), el estado y la acción a la derecha (`.row__end`). Separados por líneas de 1 px en `--line`. **Una línea de datos lleva tres datos a lo mucho, en una frase con comas; si hacen falta más, una segunda línea**, nunca un cuarto dato en la primera: "Cuarto 2, 17 años, Mérida" y debajo "#0133, Iglesia Centro". Un dato que no se explica solo lleva su etiqueta: "Responsable: Ana López", "Mentor: Ana López". Antes, cinco a siete datos sin etiqueta en una sola frase no se podían recorrer con la vista (UI-09, LANG-23). `.row--attention` (fondo hundido y una orilla petróleo de 4 px) para lo que espera una decisión. El encabezado de una lista es `.heading`: texto de 22 px en 700 con una línea `--line` de 1.5 px debajo. La portada usa renglones sin acción para sus tres promesas. Una lista que se ordena a mano ("En este evento", en Cuestionarios) lleva en `.row__end` "Subir" y "Bajar" (`.btn--secondary.btn--small`) y "Quitar" (`.btn--link`, con `data-confirm`), cada uno en su propio formulario POST y con texto oculto que nombra el renglón; el primero no lleva "Subir" ni el último "Bajar".

### Lista resumen (`.summary-list`)
Para datos de una persona ("¿Es la persona correcta?") y, con `.summary-list--answers`, para revisar respuestas: la pregunta arriba en gris, la respuesta abajo en negrita, "Cambiar" a la derecha con texto oculto que dice qué se cambia.

### Mensajes
- `.error-summary`: "Hay un problema" con enlaces a cada campo, cada uno con el nombre del campo ("Correo electrónico: Escribe un correo válido…"); recibe el foco al cargar.
- Campos: `partials/ui/field.html` dibuja el control con `{{ field|described }}`, que le pone `aria-describedby` con su pista y su error, y `aria-invalid` cuando hay error. Los formularios propios de Django (contraseña) usan subclases en tú (`core/forms.py`).
- `.error-message` junto al campo, y `.field--error` / `.question--error` con una barra chile a la izquierda.
- `.notice`: fondo hundido con una orilla izquierda de 4 px en `--line-strong`. Variantes: `--info` y `--success` (tinte, orilla petróleo), `--warning` (hundido con orilla petróleo: llama la atención sin un color propio), `--danger` (tinte chile, orilla chile), `--quiet` (hundido, orilla `--line-strong`).
- `.live-status`: "Última actualización: 10:44:14"; en chile si se perdió la conexión.

### Encabezado de página (`events/rotulo/_page_head.html`)
Título en Figtree 800 (`h1` con `.voice`), entradilla opcional, una línea de datos, el enlace "Volver" de GOV.UK arriba (`back_url`, `back_label`) y, a la derecha, la acción principal y una secundaria. Úsalo en vez de escribir el encabezado a mano.

### Filtros (`.filters`)
Vistas de la misma lista ("Todos", "Sin entrevista", "En curso"…), con su cuenta. La vista actual es "estás aquí": en 700, con un subrayado de 3 px en `--mark` y `aria-current`. Para buscar dentro de la lista, `.list-search` con una pista visible debajo.

### Ficha (`.split`, `.block`)
Dos columnas en escritorio (datos | lo que pasó con la persona), una en teléfono. `.narrow` limita formularios y fichas al ancho de lectura. `.link-button` es un botón de formulario que se lee como enlace ("Marcar como recibida"). `.status--risk` (marca chile) solo para "Urgente" en situaciones de riesgo.

### Plegable (`.fold`)
`<details class="fold" id="…" open data-fold-below="600">` para bloques largos y secundarios: abierto en escritorio, cerrado al cargar en teléfono. En las regiones que se actualizan solas, todo `<details>` con `id` conserva el estado que dejó la persona (antes, una actualización cerraba la tabla de datos que alguien había abierto).

### Plegable en línea (`.fold--inline`)
`<details class="fold fold--inline">` para datos técnicos dentro de un renglón o de un formulario: empieza cerrado, sin la línea de arriba del plegable normal, con el resumen a 16 px y poco espacio dentro. Lo técnico queda a mano para quien lo necesita y fuera de la lectura de los demás. Lo usan el editor de cuestionarios, con "Datos técnicos" en cada pregunta (los identificadores internos, en `events/admin/version_detail.html` y `question_form.html`; ningún `<code>` queda fuera del plegable), y las invitaciones pendientes del discipulado, con "Ver enlace" (el enlace en un campo de solo lectura y "Copiar enlace"; las aceptadas y las vencidas no traen enlace).

### Cifras (`.figures`)
Lista de definiciones: etiqueta de 16 px en 500, valor de 36 px en 800, nota de 16 px. Una línea fina arriba de cada una, sin tarjetas. Dos por renglón en teléfono. `.figures__alert` solo para situaciones de riesgo.

### Confirmación (`.confirm`)
Panel petróleo con el título en blanco, en Figtree 800. Después, el siguiente paso como botón.

### Promesas (`.promises`)
Lista corta de lo que la pantalla le asegura a la persona, con una palomita petróleo antes de cada frase, dibujada como la de la opción múltiple elegida (o números en un cuadro, si es `ol`). La usa la bienvenida del campero. La portada no: sus tres promesas van en renglones (`.rows`) sin marcas.

### Agenda (`.agenda`)
Asuntos numerados para la reunión (sí es una secuencia: es el orden de la reunión), con la evidencia debajo.

### Tablas (`.table` dentro de `.table-wrap`)
Siempre dentro de `<div class="table-wrap" tabindex="0" role="region" aria-label="…">`, que desplaza la tabla en teléfono sin ensanchar la página. Encabezados de columna en 700 sobre fondo hundido. Números con `.num` (cifras tabulares, a la derecha). Un `th scope="row"` no lleva el fondo ni la regla de los encabezados de columna. En administración, la fila que pide una decisión lleva una orilla de tinta y la que tiene un error, una orilla chile.

**Tabla que se ordena** (Camperos, en administración): cada encabezado (`th scope="col"`) es un enlace que ordena por su columna (`?orden=`, `?dir=asc|desc`), conserva la búsqueda y los filtros y vuelve a la página 1; el activo cambia de dirección al tocarlo otra vez y lleva `aria-sort`. Su marca es un triángulo dibujado con bordes (`.table__sort--asc`, `--desc`), decorativo (`aria-hidden`), no un glifo: la fuente es un subconjunto. Para el lector de pantalla, el activo dice ", ordenado de menor a mayor" (o "de mayor a menor") y los demás ", ordenar"; "#" se lee "Número".

### Paginación (`events/rotulo/_pagination.html`)
"Anterior", "Página 2 de 7", "Siguiente"; conserva los demás parámetros de la dirección.

### Presentación (`.deck`)
Diapositivas de 16:9 para proyector: titular en Figtree 800 de más de 100 px a 1920 × 1080. La letra chica (la fila de arriba, el pie, las notas, la clave y los renglones de equipos) tiene un piso de 22 px desde 1280 × 720 hasta 1440 × 900; en un proyector de 1920 × 1080 queda en unos 24 px, y en una pantalla menor se achica con la diapositiva en vez de empujar el texto bajo el pie. "Cal" (claro) por omisión y "Noche" (`.deck--noche`: fondo `#141B33`, texto `#F3F5F6`, datos `#8FC1CA` y `#4C95A2`, ver §1 "Datos") con el botón "Modo noche", que se recuerda en esa computadora. Al pie de cada diapositiva: "Solo totales. Sin nombres ni respuestas." Teclado: flechas, Av Pág/Re Pág, Inicio, Fin, F (pantalla completa) y Esc (vuelve a la página que abrió la presentación; en pantalla completa, la primera Esc solo sale de ella). La barra de controles (`.deck-controls`) tiene su propia franja debajo de la diapositiva (`--deck-controls-h`, 3.5 rem), así nunca tapa el pie; se oculta a los 3 s sin mover el puntero y las teclas no la despiertan: solo el puntero, un toque o el foco en un control. Cada diapositiva dice de qué hora son sus datos: en un corte guardado, la hora del corte ("corte de las 16:00"); en vivo, la de seguimientos nombra sus dos relojes en una frase ("Según el corte de las 14:00; lo demás, datos de las 14:35.").

### Barras (`.bar`, `.focusbar`)
Una sola barra para toda pantalla con sesión: `partials/ui/bar.html` y su panel, `partials/ui/menu_panel.html`. La usan `base.html` y los dos marcos del evento, con el mismo orden en el documento: la marca ("Emmaus" o el logotipo de la iglesia), el contexto (el evento, o la iglesia y tu rol), "Estás en" (solo el coordinador), los enlaces del rol con "Ayuda" al final, y el fin de la barra ("Menú" y "Cerrar sesión"). La página actual lleva un subrayado de 3 px en `--mark` y `aria-current="page"` (en "Menú", una orilla a la izquierda). Antes había cuatro barras, cada una con su orden y sus palabras (UI-44).

Tres pasos:

| Ancho | La barra |
|---|---|
| 1281 px o más | Una fila |
| De 761 a 1280 px | Los enlaces bajan a su propia fila y, si no caben, se acomodan en dos; "Cerrar sesión" sigue a la vista |
| 760 px o menos | Los enlaces y "Cerrar sesión" pasan a "Menú" |

Antes, "Menú" de pantalla completa empezaba en 1180 px, también en una tableta (UI-14). `.bar--stacked` pone los enlaces en su propia fila en todo ancho mayor de 760 px: la llevan `base.html`, la barra de liderazgo con más de cinco enlaces y la del coordinador cuando dice "Estás en".

"Menú" (`.menu-panel`, pantalla completa) es modal: un diálogo (`role="dialog"`, `aria-modal="true"`) con la página detrás inerte; Tab no sale de él, Esc o "Cerrar menú" lo cierran y el foco vuelve a "Menú". Lleva los mismos enlaces en el mismo orden, "Campamentos" al final, y una línea de quién eres: tu nombre, tu rol, la computadora y el acceso confidencial ("Carla Uno, coordinador, en Computadora 1").

- **Una sola palabra para la lista de eventos: "Campamentos".** En los marcos del evento es un enlace junto al nombre del evento; en `base.html`, un botón al final de la barra; en "Menú", el último renglón. Ya no existen "Cambiar de evento" ni "Campamentos y eventos" en la barra. La página de la lista conserva su título, "Campamentos y eventos".
- **"Ayuda" en toda barra con sesión**, como el último enlace: dentro de un evento lleva a `events/ayuda.html`; fuera, a `core/ayuda.html` (§4).
- **"Discipulado"** aparece en la barra de administración del evento solo si la iglesia tiene encendido el módulo (`Church.discipleship_enabled`).

`.focusbar` (entrevista y campero): quién o qué computadora, y una salida. Mismo ancho que la columna de contenido. No lleva "Ayuda".

### Marca de la iglesia (`{% church_brand iglesia variant="claro" size="bar" %}`)
La iglesia sube su logotipo en "Configuración de la iglesia" (solo PNG, con fondo transparente, de hasta 512 KB y con el lado mayor de 32 a 2000 px; `core/logos.py` lo revisa por su contenido, no por su nombre; negro sobre transparente es lo recomendado, y una versión en blanco, opcional, para la presentación en Noche) y ocupa el lugar de la palabra "Emmaus". Sin logotipo, la etiqueta dibuja la palabra "Emmaus" (`.wordmark`, Figtree 700 de 24 px) en el mismo lugar. Emmaus es el producto y la iglesia es la marca: con logotipo, nada en la pantalla ni en los reportes dice "Emmaus", y donde solo caben palabras (el `<title>` de cada página, el autor del PDF) va el nombre de la iglesia (`{% brand_name iglesia %}`); sin logotipo, "Emmaus" también ahí.

| Lugar | `size` | Medida | Dónde |
|---|---|---|---|
| Barra | `bar` | 32 px de alto, hasta 180 px de ancho | `base.html` (la iglesia de la persona o, si no tiene, la primera activa), `shell_coordinator.html` y `shell_leader.html` (`event.church`), y la cabeza de "Menú" |
| Entrar y portada | `auth` | hasta 220 × 72 px, centrado sobre el título | `registration/login.html` y `core/landing.html`, solo si la iglesia tiene logotipo. Cuando el logotipo encabeza, esas dos páginas no dibujan barra: el logotipo va una sola vez y nada dice "Emmaus". Sin logotipo, la barra con "Emmaus" de siempre, y no se dibuja un segundo "Emmaus" |
| Campero | `mark` | 40 px de alto | La barra del campero (`shell_kiosk.html`), desde la bienvenida "Hola, …" |
| Presentación, portada | `deck` | hasta 320 × 120 px en una diapositiva de 1920, en `cqw` | La portada |
| Presentación, demás diapositivas | `deck-top` | hasta 220 × 48 px en una diapositiva de 1920, en `cqw` | La fila de arriba de cada diapositiva, junto al nombre del evento y la hora del corte |
| PDF del corte | — | hasta 2.2 × 0.72 pulgadas | El encabezado |

- **Monocromo, y el color nunca toca el logotipo.** El logotipo no se recolorea y la paleta no cambia por él: tinta sobre blanco, blanco sobre Noche. Lo recomendado es negro sobre transparente. En Atrio es el único adorno de la pantalla, junto a un solo color de acción (como hace Church Center con cada iglesia).
- **Nunca se estira.** `width` y `height` salen de los píxeles del PNG, ajustados al lugar sin pasarse de su ancho ni de su alto, así la página no salta al cargar; CSS solo lo deja encoger, con `object-fit: contain` y el alto de cada lugar como tope. Un logotipo de más de 10:1 no se acepta.
- **Texto alternativo: el nombre de la iglesia.** En la barra es el enlace a inicio, como lo era "Emmaus". No es un título: cada página sigue con un solo `h1`.
- **En Noche** (`variant="oscuro"`): la versión en blanco si la iglesia la subió; si no, su logotipo de siempre sobre una placa blanca pequeña (`.brand__plate`), porque negro sobre el fondo de Noche desaparece. Cada diapositiva lleva las dos versiones y `.deck--noche` muestra una.
- **Nunca en datos ni gráficas**: ni como marca de una serie, ni en conteos, reglas, tablas, paneles o botones. Es identidad, no información.
- La página de error 500 conserva "Emmaus": se dibuja sin base de datos.
- La dirección de la imagen (`/iglesia/<slug>/logotipo/claro/`) lleva `?v=`: el navegador la guarda un día, y un logotipo nuevo cambia la dirección.

## 3. Gráficas

`events/charts.py` dibuja SVG en el servidor; `rotulo.css` les da estilo. Los colores son los de §1 "Datos".

- Línea de 2.5 px en petróleo, sin área rellena, punto final con anillo blanco y la cifra junto a él.
- Columnas de 24 px como máximo, punta redondeada de 4 px y base recta.
- Cuadrícula de 1 px muy tenue y eje de 1 px; sin segundo eje.
- `eta_minutes`: proyección punteada desde el último punto hasta la meta, con "Hacia las 16:43". Solo si el término cae dentro de 12 horas.
- `partial_last`: la última columna (la hora en curso) en petróleo medio, explicado en el subtítulo.
- `compact`: el mismo dibujo en un lienzo de 360 px para teléfonos, con a lo mucho cuatro etiquetas de hora. La plantilla incluye los dos y CSS muestra uno (`.chart__wide`, `.chart__narrow`), así el texto se lee a su tamaño real.
- Gráficas compartidas: `events/rotulo/_chart_cumulative.html` y `_chart_columns.html` (dibujo ancho, dibujo compacto y tabla de datos). Úsalas en vez de llamar a las etiquetas de plantilla directamente.
- Las líneas de la cuadrícula caen en números redondos (0, 10, 20 o 0, 100, 200; nunca 0, 12, 25, 37 ni 0, 125, 250). Cuando el último punto queda junto a la meta, su cifra va debajo para no tapar "Total".
- Las etiquetas de hora nunca chocan (`_label_indexes`): siempre está la primera y la última, y ninguna queda a menos de tres cuartos del espaciado normal. Esto aplica a **todas** las gráficas (avance, reportes, presentación): cuando la penúltima etiqueta quedaba pegada a la última, se omite.
- Toda gráfica tiene `<title>`, `<desc>` y, si importa el detalle, "Ver los datos en una tabla".
- **Cantidades redondeadas: "unas N", nunca "≈".** Los totales protegidos se redondean para que nadie pueda ser identificado y se escriben "unas 85" (o "Menos de 5"). Figtree no tiene el signo "≈": el navegador lo tomaría de otra letra, más chica y de otro grueso.

## 4. Pantallas

| Plantilla | Base | Qué la distingue |
|---|---|---|
| `events/coordinator/home.html` + `_home_live.html` | `events/rotulo/shell_coordinator.html` | "Hola, N."; bajo el saludo, "Todavía no eliges computadora." con "Elegir computadora", solo si no hay computadora elegida y el evento tiene computadoras activas (ya elegida, la barra dice "Estás en"). El panel: la entrevista abierta, la búsqueda, "¡Terminaste!" o el registro del primer campero. "Llevas X de Y." con el conteo y "Por entrevistar". Al lado, "También sin terminar" (si hay más de una a medias) y "Esperan algo de ti": las entrevistas que esperan tu decisión de seguimiento ("Decidir") y tus seguimientos abiertos, o "Nada espera tu decisión por ahora." "Completadas hace poco" vive en Mi avance (`_progress_live.html`) |
| `events/coordinator/participants.html` (Mis camperos) | `events/rotulo/shell_coordinator.html` | Primero las entrevistas en curso, luego las que faltan, al final las completadas, en cada pestaña. Dos líneas de datos: "Cuarto 2, 17 años, Mérida" y "#0133, Iglesia Centro"; el estado y la acción a la derecha. Tres estados vacíos distintos, con palabras propias para coordinación y para administración: sin camperos ("Todavía no tienes camperos asignados. Registra al primero o pide a la administración que te los asigne."), una búsqueda sin resultados ("No encontramos camperos con esa búsqueda. Prueba con su número o con menos letras.") y una pestaña vacía ("No queda ningún campero sin entrevista.") |
| `events/interview/*.html`, `events/kiosk/*.html` | `events/shell_focus.html`, `events/shell_kiosk.html` | Sin navegación; camino; una pregunta; revisión; confirmación. La bienvenida del campero (`kiosk/welcome.html`): "Hola, N.", una frase, tres promesas (`.promises`: cuántas preguntas son, "Prefiero no responder" y "Parar por ahora"), el bloque "¿Quién lee tus respuestas?" y un solo botón. La frase de quién lee vive en un solo archivo, `kiosk/_who_reads.html`, que también usa la revisión: no se reescribe en otra plantilla (las pruebas la fijan). Salió de la bienvenida la promesa "Nadie más verá tus respuestas en esta pantalla.": el sistema no puede cumplirla (auditoría LANG-27). Con dos o más cuestionarios en el evento, "Antes de empezar" agrega el paso 3, "¿Qué cuestionario?" (opciones, o el que sigue), "Entrevista completada" pone primero "Continuar con «…»" y el campero lee "Ya terminaste este cuestionario." |
| `events/pastor/home.html` + `_home_live.html` | `events/rotulo/shell_leader.html` | Informe de cinco preguntas, pregunta y respuesta a la izquierda y evidencia a la derecha, en este orden: "¿Cómo va…?" (el titular en una frase, `.headline--sentence`, y la regla), "¿Qué hay que coordinar?", "¿Qué equipos necesitan apoyo?", "¿Cuánto falta?" y "¿Qué necesidades hay?". Cada asunto de la agenda es un enlace a la pantalla donde se atiende; la dirección se arma al mostrar, nunca se guarda en el corte. Tres tamaños de título: el `h1` de 44 px, la frase de 28 a 36 px y las preguntas de 26 px. La frase del titular lleva una cifra; el ritmo por hora vive solo en su cifra, "Ritmo". Sin camperos, solo la primera pregunta, con una frase: "Aún no hay camperos registrados." y "La administración los registra o importa." El aviso "Solo totales" no pasa de 65 caracteres por línea |
| `events/tutor/home.html` + `_home_live.html` | `rotulo_shell` (líder) | Panel solo si hay entrevistas detenidas; "Tu equipo lleva X de Y." (con varios cuestionarios, "con los N cuestionarios", "a medias" y "Por cuestionario" del equipo); una fila por coordinador con su conteo (`events/rotulo/_coordinator_rows.html`) y, con varios cuestionarios, una línea por cuestionario bajo las marcas |
| `events/admin/*.html` | `rotulo_shell` (líder, barra de dos renglones) | Panel solo si algo impide entrevistar (§2, "Lo que sigue"). "Llevamos X de Y entrevistas." con la regla (con varios cuestionarios, "Llevamos X de Y con los N cuestionarios.", la clave con "a medias" y "Por cuestionario"). "Preparación" dice "N de M pasos listos." (o "Todo listo: M de M pasos."), lista primero los pasos que faltan, cada uno con su estado ("Falta", "Revisar", "Esperando" o "Apagados") y su botón, y pliega los listos en `#preparacion-listos`, cerrado, que conserva su estado al actualizarse. "Cortes" es un solo renglón (antes eran "Cortes" y "Cortes automáticos"): el horario, el último corte y, mientras hay cortes automáticos, si el programador corre; se marca solo cuando el programador no ha corrido. "Apagados" no cuenta como listo. Al lado, la actividad y las descargas. Tablas densas |
| `events/admin/settings.html` (Configuración) | `rotulo_shell` (líder) | Una columna con índice de secciones (`.page-toc`): Procedimientos, Datos del evento, Cortes, Privacidad y protección y Computadoras. Dos formularios con `data-guard`, que avisan al salir con cambios sin guardar, y un botón relleno cada uno: "Guardar datos del evento" y "Guardar cortes y privacidad" (dos secciones, un formulario). Al guardar se vuelve a la sección (`#datos`, `#cortes`, `#procedimientos`). Los cuestionarios se eligen en Cuestionarios y quién lee las respuestas confidenciales tiene su página: una línea con enlace a cada una |
| `events/admin/questionnaires.html` (Cuestionarios), `questionnaire_form.html` | `rotulo_shell` (líder) | "Nuevo cuestionario" es la acción del encabezado (nombre, título que ven los camperos, texto de bienvenida; nace como borrador, versión 1). "En este evento": los cuestionarios del evento en su orden, como renglones con "Subir", "Bajar" y "Quitar" (§2, Renglones), y con dos o más la casilla "Los camperos los responden en este orden" con su "Guardar". Debajo, "Versiones", agrupadas por cuestionario, con "En uso" y "Usar en este evento" (o "Usar esta versión en este evento" si el evento usa otra versión del mismo). Con un solo cuestionario, la entrevista no muestra nada nuevo |
| `events/admin/confidential_level.html` (Respuestas confidenciales) | `rotulo_shell` (líder) | La abren administración (desde Configuración) y pastoral (desde Resumen general); su "Volver" regresa a esa pantalla. Tres opciones en `.choices`, la pista "Si abres el acceso, aplica a las entrevistas que empiecen después. Si lo restringes, aplica de inmediato." y la frase que hoy leen los camperos. Al guardar un nivel que no es solo el equipo de cuidado, el mensaje pide revisar el aviso de privacidad |
| `events/supervision/follow_ups.html` (Seguimiento) | `rotulo_shell` (líder) | Dos filtros: el estado y "Responsable" ("Todos", "Sin asignar", "Míos"). Primero las situaciones de riesgo, luego los pendientes (sin responsable antes), los que están en proceso y al final los atendidos. "N seguimientos" y, si la lista se corta, "Mostrando 200 de N. Usa los filtros para ver los demás." Cada renglón: el motivo, el coordinador y hace cuánto en la primera línea; "Responsable: …" o "Sin asignar" en la segunda; a la derecha, el estado y, para quien puede atenderlo, un selector con "Asignar" (`.row-assign`, `seguimiento/<id>/asignar/`) que solo cambia al responsable. La ficha regresa a la página desde la que se abrió (`?siguiente=`, §7) |
| `events/ayuda.html` (`/eventos/<evento>/ayuda/`), `core/ayuda.html` (`/ayuda/`) | `rotulo_shell`; `base.html` | "Ayuda": las cinco tareas más comunes del rol, cada una con su título (`h2.heading`), tres frases o tres pasos, y el enlace a la pantalla donde se hace; al final, a quién preguntar. Fuera de un evento: entrar y salir, unirte con el código, elegir el campamento en "Campamentos" y, si la iglesia usa el discipulado, tu camino o tus participantes. Es el último enlace de la barra |
| `events/reports/*.html`, `events/presentation/*.html` | `rotulo_shell`; la presentación usa `layout/skeleton.html` | El corte se lee como el resumen del pastor; diapositivas de 16:9 en Cal y Noche, con su franja de controles (`--deck-controls-h`), letra chica con piso de 22 px y la hora del corte en cada diapositiva de un corte guardado (§2, "Presentación") |
| `templates/core/*.html` | `base.html` (barra de dos renglones) | Discipulado: el panorama de la iglesia como el resumen del pastor; el participante con su siguiente lección en el panel de lo que sigue; las lecciones como las preguntas de la entrevista. La barra sale de datos (`core/context_processors.py`): un enlace por destino, sin "Mi cuenta" ni "Lecciones" (van en "Rutas"), y "Para el pastor". Con el módulo apagado (`Church.discipleship_enabled`), la administración solo ve "Configuración", el pastor ningún enlace del módulo y `/dashboard/` lleva al pastor a "Campamentos y eventos". Los seguimientos dicen "Pendiente", "En proceso", "Atendido" y "Cancelado", como en el campamento, con la marca compartida (`core/rotulo/_acompanamiento_estado.html`) y "Marcar como atendido"; uno nuevo nace pendiente. El mentor aterriza en "Esperan tu respuesta", "Seguimientos pendientes" y "Mis participantes"; el panorama pregunta además "¿Quién se quedó callado?". Las invitaciones son renglones: el correo, el rol y hace cuánto se envió, "Mentor: …" o "Sin asignar", y el enlace de una pendiente bajo "Ver enlace" (§2, "Plegable en línea"); con más de 100, "Mostrando 100 de N" |
| `core/landing.html` (`/`, la portada) | `base.html` | Una columna centrada (`.auth-card`), sin fotos ni iconos: el logotipo de la iglesia si lo tiene, "Platicar con cada joven, sin perder a nadie." como único título, la entradilla, "Entrar a Emmaus" (el único botón relleno), justo debajo "¿Vas a ayudar en un evento? Escribe el código" (a `/unirme/`), tres promesas de una frase en renglones (`.rows`) y quién la usa ("Iglesia del Camino usa Emmaus en sus campamentos."). Con sesión iniciada lleva a tu inicio; sin ninguna iglesia, a la configuración inicial |
| `registration/*.html`, `errors/*.html`, `events/events_list.html` | `base.html` | Una columna (centrada en las de `.auth-card`), el título en Figtree 800, un solo botón principal |
| `events/join/join.html` (`/unirme/`), `events/join/entry.html` (`/e/<código>/`), `events/join/requested.html` | `base.html` | Como la entrada: una columna (`.auth-card`). `/unirme/` es un solo campo con su botón (`.search`); la página del evento lleva su nombre como título, la iglesia y las fechas, y pide nombre, correo y rol en `.choices` (solo tutor o coordinador) |
| `events/admin/sedes.html` (`/administracion/sedes/`), `events/admin/sede_form.html` | `rotulo_shell` (líder) | Sin panel de lo que sigue. Una fila por sede (`.rows`): el nombre y la frase "Naucalpan, activa, 80 camperos en este evento", con "Editar" solo para quien administra la iglesia (los demás leen "Las sedes las administra la iglesia."). Debajo, "Sedes que participan" en `.choices` (`.choice--multi`); ninguna marcada es toda la iglesia, y una marcada que está inactiva se avisa en una línea `.notice--warning`. El formulario de la sede: nombre, identificador (se forma con el nombre), ciudad, orden y "Activa"; "Borrar la sede" (`.btn--danger`) solo si ningún campero la tiene. En los renglones de camperos, la sede es el tercer dato de la primera línea: "Cuarto 2, 17 años, Mérida" |
| `events/admin/join_code_sign.html` | `rotulo_shell` (líder) | La hoja para imprimir: el nombre del evento, el código a 96 px en Figtree 800, la dirección y tres pasos. Sin panel; al imprimir no salen la barra ni los botones |

Las partes vivas (`_home_live.html`) se recargan cada 20 o 30 s con `data-live-url`; el indicador `.live-status` queda fuera de la región que se reemplaza.

## 5. Lenguaje

- Español de México, tuteando, frases completas y cortas. "Llevas 18 de 31." "Te faltan 2 entrevistas."
- Los datos se escriben como frase, con comas, nunca con "·": "Cabaña 11, 17 años", "#0102, Cabaña 19".
- El nombre de una acción es el mismo en todo el flujo: "Entregar la computadora" → "Recuperar la computadora".
- Los errores dicen qué pasó y cómo arreglarlo; no se disculpan.
- Lo privado se dice en voz alta: "Confidencial. Solo el equipo de cuidado autorizado podrá leer esta respuesta." Si el evento abrió las respuestas confidenciales, la nota dice quién las lee ("Esta respuesta la leen …"), y la etiqueta "Confidencial" sigue junto a la respuesta.
- Los mensajes propios de Django también tutean: `locale/es_MX/LC_MESSAGES/django.po` reemplaza los de validación, archivos y contraseñas (ver `docs/architecture.md`).
- **Una palabra por cosa.** Las que fijó la ronda del 26 y 26 de octubre de 2026 (`design-direction.md`):
  - **"Campamentos"** es la lista de eventos en la barra y en "Menú". La página conserva su título, "Campamentos y eventos".
  - **"Faltan"** para las entrevistas que faltan; **"sin atender"** para los seguimientos. "Pendientes" ya no nombra a los dos (LANG-19).
  - Los estados de un seguimiento son **"Pendiente", "En proceso", "Atendido" y "Cancelado"**, en el campamento y en el discipulado (LANG-20; en el discipulado, `core/0009`). Como estado, un seguimiento nunca dice "Abierto" ni "Completado"; "abiertos" solo junta a los pendientes y a los que están en proceso ("Seguimientos abiertos").
  - **"Ya no participan"** (antes "Dados de baja"); en un renglón o en la ficha, "ya no participa".
  - **"Computadora: ninguna"**: en Computadoras en uso, la columna "Computadora" dice "Ninguna" cuando la entrevista no está abierta en ninguna (antes "Abierta en" y "Libre").
  - Un evento **usa** cuestionarios: "Usar en este evento" lo agrega al final, "Usar esta versión en este evento" cambia la versión en su mismo lugar, y "Quitar" (en la lista) o "Dejar de usar en este evento" (en la versión) lo saca; las entrevistas hechas se conservan. El orden se cambia con "Subir" y "Bajar".
  - **"Datos técnicos"** guarda los identificadores internos del editor de cuestionarios; el tipo de respuesta empieza en "Elige un tipo de respuesta", no en la opción vacía de Django.
  - El campo de la dirección sigue llamándose **"Nombre corto para la dirección"** ("Nombre corto de la iglesia para la dirección" en la configuración inicial): la palabra es la decisión D6 de la auditoría, todavía abierta. Lo que cambió es que se forma solo con el nombre, mientras escribes y en el servidor si llega vacío, y se puede cambiar.
  - Los colores de la iglesia tienen nombres llanos: "Color de “estás aquí”", "Color de campos y paneles", "Color del texto tenue"; ninguno dice "acento" ni "Superficies".
- El PDF del corte usa el mismo sistema: Figtree 800 para el título, las preguntas y la frase principal; Figtree 400 para lo demás; petróleo para datos y chile solo para riesgo (en un documento no hay "lo siguiente que hay que hacer", así que no lleva panel).

## 6. Accesibilidad

- axe-core 4.10 (WCAG 2.0, 2.1 y 2.2 A y AA) en todas las pantallas y tres anchos: ver `ux-review.md`, "Ronda 16 · Atrio".
- Contraste: tinta 16.18:1, el texto más claro 5.74:1, bordes de control 3.56:1, petróleo 7.01:1 (y letra blanca sobre petróleo, 7.01:1), chile 5.98:1. En Noche, texto 15.56:1 y marcas de datos 8.63, 4.96 y 4.78:1. Se lee en proyector y en pantallas viejas.
- Objetivos de 44 px como mínimo; opciones de 64 px (también en teléfono); campos de 56 px; botón principal de 60 px.
- Un solo `h1` por página; títulos en orden; regiones con nombre; "Saltar al contenido".
- Foco visible en todo fondo: anillo de tinta de 3 px con 2 px de blanco (§1 "Foco").
- Las marcas de avance son imágenes con texto alternativo completo; nada depende solo del color.
- `prefers-reduced-motion: reduce` apaga todo el movimiento (ver §Movimiento); `forced-colors` agrega bordes a todas las marcas; impresión sin barras ni botones.

## Movimiento

Atrio se mueve poco y siempre por una razón: **el movimiento explica un cambio o confirma una acción; nunca decora.**

| Token | Valor | Para qué |
|---|---|---|
| `--dur-1` | 120 ms | Controles: color, foco, subrayado |
| `--dur-2` | 200 ms | El paso entre páginas, los avisos, los plegables, la marca elegida |
| `--dur-3` | 320 ms | El panel de lo que sigue, el encabezado de la página, la presentación |
| `--ease` | `cubic-bezier(.2,.7,.2,1)` | Una sola curva: arranca rápido y llega despacio |
| `--rise` | 6 px | La subida de lo que aparece |

Los ocho usos, y ninguno más:

1. **Entre páginas**, con `@view-transition`: la pregunta de la entrevista se funde con la siguiente.
2. **Al cargar**, el panel de lo que sigue y el encabezado aparecen con un fundido y una subida de 6 px.
3. **El conteo**: sus marcas se llenan en cascada solo en la primera carga, nunca al refrescar la región viva.
4. **La región viva** baja a 0.72 de opacidad mientras consulta.
5. **Botones, opciones y foco**, con transiciones de 120 ms. Al elegir una opción, su marca crece de 1 a 1.06 y vuelve a 1.
6. **El plegable**: su contenido se funde al abrir, sin animar la altura.
7. **Los avisos** entran con fundido y subida.
8. **La presentación** funde una diapositiva con la siguiente y el cambio entre Cal y Noche.

**Regla dura:** con `prefers-reduced-motion: reduce` no se mueve nada: ni transiciones, ni fundidos, ni la cascada del conteo, ni la transición entre páginas. Una prueba lo verifica.

## 7. Cómo hacer una pantalla nueva

1. Extiende el marco que corresponde: `{% extends rotulo_shell %}` dentro de un evento, `events/shell_focus.html` si no lleva navegación, `base.html` en discipulado y en las páginas públicas.
2. Empieza con `events/rotulo/_page_head.html`. Si hay algo que hacer ahora, ponlo en el panel de lo que sigue (`.sign`), con una pregunta como título y un solo botón relleno; si hay dos, elige. Si no hay nada que hacer, no pongas panel. Un botón relleno por formulario.
3. Usa los componentes de arriba antes de escribir CSS. Si de verdad hace falta CSS nuevo, va en la sección del área al final de `rotulo.css`.
4. Nada de iconos, tarjetas con sombra, píldoras, degradados ni amarillo; tampoco "·", flechas en los botones ni etiquetas encima de los títulos (§5). Petróleo para la acción y los datos; chile solo para errores y riesgo.
5. Agrega la pantalla a `e2e/design-review.mjs` (o a `e2e/all_urls.py`, que alimenta `e2e/review-all.mjs`) y a la prueba de alcance de `events/tests/test_rotulo.py`; revisa las capturas en los tres anchos y corre axe.
6. Si una tarea común de algún rol pasa en la pantalla, agrégala a su "Ayuda": `templates/events/ayuda.html` dentro de un evento (una sección por tarea, en el bloque de ese rol) o `templates/core/ayuda.html` fuera de él. La tarea como título, tres frases o tres pasos, y el enlace a la pantalla. La vista, `events/views/ayuda.py`, solo pone la entradilla.
7. Para volver al origen, el enlace a la pantalla lleva `?siguiente=` con la dirección de la página de donde sale, y la vista que lo recibe lo pasa por `safe_next` (`events/views/common.py`) con una dirección de respaldo. `safe_next` solo acepta direcciones de este mismo sitio; lo demás (otro dominio, `//otro.sitio`) cae en la de respaldo. Guárdalo en el formulario como `<input type="hidden" name="siguiente">` para que sobreviva al envío. Dentro de una región que se actualiza sola, usa la dirección de la página (`{% url … %}`), no `request.get_full_path`: la actualización pide el fragmento con un parámetro propio (`?_=`) que no debe viajar en `siguiente`. Así regresan la ficha de un seguimiento, "Decidir" y "Elegir computadora".

Si agregas movimiento, usa los tokens de §Movimiento y nada que no explique un cambio.
