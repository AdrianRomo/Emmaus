# Sistema de diseño · Atrio

Todas las pantallas de Emmaus usan este sistema: la portada, el coordinador y la entrevista, el modo campero, tutores, pastores, administración, reportes y presentación, inicio de sesión y errores, y el módulo de discipulado. **Atrio reemplazó a Rótulo el 26 de octubre de 2026.** Rótulo había reemplazado a "Cuaderno de campo" (`emmaus.css`) el 23 de octubre; la historia de las dos y por qué se retiraron está en `design-direction.md`, y la investigación que llevó a Atrio, en `design-research.md` ("Investigación v3").

Atrio es la puerta abierta del templo: blanco, un solo color de acción, frases como títulos y el logotipo de la iglesia como único adorno. Cambió los tokens y el trato de cada componente, no la estructura: los nombres de clase, los marcos, las vistas y el JavaScript son los de Rótulo. Por eso la hoja **conserva su nombre**, `static/css/rotulo.css`, los marcos siguen en `templates/events/rotulo/` y la variable de plantilla sigue siendo `rotulo_shell`.

| | |
|---|---|
| Hoja de estilo | `static/css/rotulo.css`: tokens y componentes compartidos, y al final una sección por área (liderazgo, administración, público, discipulado, reportes y presentación) con lo que solo esa área necesita |
| Base de plantilla | `templates/layout/skeleton.html` (fuentes, hoja, colores propios de la iglesia) |
| Marcos | `events/rotulo/shell_coordinator.html` y `shell_leader.html` para el evento (la variable `rotulo_shell` elige por rol), `events/shell_focus.html` y `shell_kiosk.html` para la entrevista y el campero, `base.html` para la portada, discipulado, inicio de sesión y errores |
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
- Puntos de quiebre (literales en las consultas): 600 px (teléfono), 900 px (una columna), 1180 px (la barra pasa a "Menú").
- Movimiento: `--dur-1`, `--dur-2`, `--dur-3`, `--ease` y `--rise` (ver §Movimiento).

### Foco

Un anillo de tinta de 3 px separado del control por 2 px de blanco (16:1): `outline: 3px solid var(--ink); outline-offset: 2px; box-shadow: 0 0 0 2px #fff`. Es el mismo sobre blanco, hundido, tinte o petróleo, y vale para enlaces, botones, campos, opciones (el anillo rodea toda la opción) y `summary`. Rótulo tenía cinco variantes (fondo maíz en enlaces, el negativo del rotulista en el letrero, contorno maíz en campos…).

La única variación es de lugar, no de forma: en los enlaces de la barra, de los filtros y del menú, y en los enlaces de acción de la tabla del corte, el anillo se dibuja hacia adentro (`outline-offset: -3px`) para no salirse de su franja.

## 2. Componentes

### Lo que sigue (`.sign`)
El panel de la tarea: fondo tinte, radio de 10 px, sin borde de tinta. Su título (`.sign__title`) es **una pregunta** ("¿A quién vas a entrevistar?"), debajo una frase (`.sign__text`) y **una** acción rellena de petróleo (`.sign__actions`, `.search`). Es lo siguiente que hay que hacer. **Uno por pantalla, a lo mucho.** Si el contenido no es una acción inmediata, no es un panel. La clase conserva el nombre del letrero de Rótulo.

### Estás aquí (`.here`)
Un punto petróleo (`.here__blaze`) y el nombre del lugar: "Estás en Computadora 4". Va en la barra del coordinador y en la de la entrevista. Sin amarillo.

### Botones (`.btn`)

| Variante | Uso |
|---|---|
| `.btn--primary` | La acción principal. Petróleo con letra blanca (hover `#084E5A`), sin sombra. Una por pantalla |
| `.btn` / `.btn--secondary` | Blanco con borde de control de 1.5 px. Acciones de renglón ("Entrevistar", "Decidir") y salidas ("Guardar y salir") |
| `.btn--link` | Alternativas de menor peso ("Mejor hago yo las preguntas", "Volver a mi inicio") |
| `.btn--danger` | Solo para borrar ("Sí, descartar y borrar respuestas"), en chile |
| `.btn--danger-outline` | Con `.btn--secondary`: letra y borde chile sobre blanco. Solo para la acción de riesgo ("Reportar una situación de riesgo", en la ficha del campero y en la revisión) |
| `.btn--large` / `.btn--small` | 60 px / 44 px de alto (normal: 48 px) |

El texto del botón dice lo que pasa: "Comenzar entrevista", "Finalizar entrevista", "Entregar la computadora". Sin flechas.

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

### Estado (`{% status "en_curso" %}`, `{% follow_up_status f.status %}`)
Una marca pequeña más la palabra. Las tres marcas de etapa significan solo etapa: completada / atendido / publicada, petróleo; en curso / en proceso, petróleo medio; sin iniciar / pendiente, contorno. Lo que terminó sin hacerse (descartada, cancelado, vencida, retirada, archivado, rechazado, cerrada) va en gris relleno (`status--closed`, `--line-strong`) y lo que espera que alguien revise o decida (revisar, por decidir, pasada al pastor) en medio gris (`status--review`): ningún estado que terminó sin hacerse usa el contorno de "sin iniciar".

### Renglones (`.rows`, `.row`)
Nombre a 20 px en 700, dato debajo a 16 px, la acción a la derecha. Separados por líneas de 1 px en `--line`. `.row--attention` (fondo hundido y una orilla petróleo de 4 px) para lo que espera una decisión. El encabezado de una lista es `.heading`: texto de 22 px en 700 con una línea `--line` de 1.5 px debajo. La portada usa renglones sin acción para sus tres promesas.

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

### Paginación (`events/rotulo/_pagination.html`)
"Anterior", "Página 2 de 7", "Siguiente"; conserva los demás parámetros de la dirección.

### Presentación (`.deck`)
Diapositivas de 16:9 para proyector: titular en Figtree 800 de más de 100 px a 1920 × 1080 y nada por debajo de unos 26 px. "Cal" (claro) por omisión y "Noche" (`.deck--noche`: fondo `#141B33`, texto `#F3F5F6`, datos `#8FC1CA` y `#4C95A2`, ver §1 "Datos") con el botón "Modo noche", que se recuerda en esa computadora. Al pie de cada diapositiva: "Solo totales. Sin nombres ni respuestas." Teclado: flechas, Av Pág/Re Pág, Inicio, Fin, F (pantalla completa) y Esc (vuelve a la página que abrió la presentación; en pantalla completa, la primera Esc solo sale de ella). La barra de controles (`.deck-controls`) se oculta a los 3 s sin mover el puntero y las teclas no la despiertan: solo el puntero, un toque o el foco en un control.

### Barras (`.bar`, `.focusbar`)
`.bar`: "Emmaus" (o el logotipo de la iglesia), el evento, "Estás en", los enlaces del rol con un subrayado de 3 px en `--mark` bajo la página actual (en "Menú", una orilla a la izquierda), y "Cerrar sesión". Bajo 1180 px los enlaces pasan a "Menú" (`.menu-panel`, pantalla completa). El panel es modal: un diálogo (`role="dialog"`, `aria-modal="true"`) con la página detrás inerte; Tab no sale de él, Esc o "Cerrar menú" lo cierran y el foco vuelve a "Menú". `.focusbar` (entrevista y campero): quién o qué computadora, y una salida. Mismo ancho que la columna de contenido.

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
| `events/coordinator/home.html` + `_home_live.html` | `events/rotulo/shell_coordinator.html` | Panel de búsqueda o de entrevista abierta; conteo; dos columnas de renglones |
| `events/interview/*.html`, `events/kiosk/*.html` | `events/shell_focus.html`, `events/shell_kiosk.html` | Sin navegación; camino; una pregunta; revisión; confirmación |
| `events/pastor/home.html` + `_home_live.html` | `events/rotulo/shell_leader.html` | Informe de cinco preguntas: pregunta y respuesta a la izquierda, evidencia a la derecha |
| `events/tutor/home.html` + `_home_live.html` | `rotulo_shell` (líder) | Panel solo si hay entrevistas detenidas; "Tu equipo lleva X de Y."; una fila por coordinador con su conteo (`events/rotulo/_coordinator_rows.html`) |
| `events/admin/*.html` | `rotulo_shell` (líder, barra de dos renglones) | Panel solo si algo impide entrevistar; "Preparación" con estado por paso; tablas densas |
| `events/reports/*.html`, `events/presentation/*.html` | `rotulo_shell`; la presentación usa `layout/skeleton.html` | El corte se lee como el resumen del pastor; diapositivas de 16:9 en Cal y Noche |
| `templates/core/*.html` | `base.html` (barra de dos renglones) | Discipulado: el panorama de la iglesia como el resumen del pastor; el participante con su siguiente lección en el panel de lo que sigue; las lecciones como las preguntas de la entrevista |
| `core/landing.html` (`/`, la portada) | `base.html` | Una columna centrada (`.auth-card`), sin fotos ni iconos: el logotipo de la iglesia si lo tiene, "Platicar con cada joven, sin perder a nadie." como único título, la entradilla, "Entrar a Emmaus" (el único botón relleno), justo debajo "¿Vas a ayudar en un evento? Escribe el código" (a `/unirme/`), tres promesas de una frase en renglones (`.rows`) y quién la usa ("Iglesia del Camino usa Emmaus en sus campamentos."). Con sesión iniciada lleva a tu inicio; sin ninguna iglesia, a la configuración inicial |
| `registration/*.html`, `errors/*.html`, `events/events_list.html` | `base.html` | Una columna (centrada en las de `.auth-card`), el título en Figtree 800, un solo botón principal |
| `events/join/join.html` (`/unirme/`), `events/join/entry.html` (`/e/<código>/`), `events/join/requested.html` | `base.html` | Como la entrada: una columna (`.auth-card`). `/unirme/` es un solo campo con su botón (`.search`); la página del evento lleva su nombre como título, la iglesia y las fechas, y pide nombre, correo y rol en `.choices` (solo tutor o coordinador) |
| `events/admin/sedes.html` (`/administracion/sedes/`), `events/admin/sede_form.html` | `rotulo_shell` (líder) | Sin panel de lo que sigue. Una fila por sede (`.rows`): el nombre y la frase "Naucalpan, activa, 80 camperos en este evento", con "Editar" solo para quien administra la iglesia (los demás leen "Las sedes las administra la iglesia."). Debajo, "Sedes que participan" en `.choices` (`.choice--multi`); ninguna marcada es toda la iglesia, y una marcada que está inactiva se avisa en una línea `.notice--warning`. El formulario de la sede: nombre, identificador (se forma con el nombre), ciudad, orden y "Activa"; "Borrar la sede" (`.btn--danger`) solo si ningún campero la tiene. En las listas, la sede va después del cuarto: "Cuarto 2, Mérida, 17 años" |
| `events/admin/join_code_sign.html` | `rotulo_shell` (líder) | La hoja para imprimir: el nombre del evento, el código a 96 px en Figtree 800, la dirección y tres pasos. Sin panel; al imprimir no salen la barra ni los botones |

Las partes vivas (`_home_live.html`) se recargan cada 20 o 30 s con `data-live-url`; el indicador `.live-status` queda fuera de la región que se reemplaza.

## 5. Lenguaje

- Español de México, tuteando, frases completas y cortas. "Llevas 18 de 31." "Te faltan 2 entrevistas."
- Los datos se escriben como frase, con comas, nunca con "·": "Cabaña 11, 17 años", "#0102, Cabaña 19".
- El nombre de una acción es el mismo en todo el flujo: "Entregar la computadora" → "Recuperar la computadora".
- Los errores dicen qué pasó y cómo arreglarlo; no se disculpan.
- Lo privado se dice en voz alta: "Confidencial. Solo el equipo de cuidado autorizado podrá leer esta respuesta."
- Los mensajes propios de Django también tutean: `locale/es_MX/LC_MESSAGES/django.po` reemplaza los de validación, archivos y contraseñas (ver `docs/architecture.md`).
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
2. Empieza con `events/rotulo/_page_head.html`. Si hay algo que hacer ahora, ponlo en el panel de lo que sigue (`.sign`), con una pregunta como título y un solo botón relleno; si hay dos, elige.
3. Usa los componentes de arriba antes de escribir CSS. Si de verdad hace falta CSS nuevo, va en la sección del área al final de `rotulo.css`.
4. Nada de iconos, tarjetas con sombra, píldoras, degradados ni amarillo; tampoco "·", flechas en los botones ni etiquetas encima de los títulos (§5). Petróleo para la acción y los datos; chile solo para errores y riesgo.
5. Agrega la pantalla a `e2e/design-review.mjs` (o a `e2e/all_urls.py`, que alimenta `e2e/review-all.mjs`) y a la prueba de alcance de `events/tests/test_rotulo.py`; revisa las capturas en los tres anchos y corre axe.

Si agregas movimiento, usa los tokens de §Movimiento y nada que no explique un cambio.
