# Investigación de diseño · Emmaus

Fecha: 23 de octubre de 2026. Alcance: cómo resuelven otros productos los mismos trabajos que Emmaus (estación compartida, cuestionario de una pregunta por pantalla, tablero operativo, errores y confirmaciones en lenguaje claro) y qué **no** debemos copiar de ellos.

## Cómo se hizo

- Un navegador automatizado (Playwright 1.63, Chromium) abrió páginas públicas sin iniciar sesión, tomó capturas a 1440 px y a 390 px y guardó los estilos calculados (tipo, tamaño, color, bordes, espacios) de los elementos clave. **No se creó ninguna cuenta y no se envió ningún formulario**: en Tally solo se presionó "Next" en páginas que no eran la última.
- Las capturas, los estilos en JSON y las notas completas (`NOTES.md`, con cada URL y su resultado) están fuera del repositorio, en `~/sandbox-docs/emmaus/referencias/` (más de 200 archivos). El repositorio es público, así que **aquí solo se incluyen capturas de GOV.UK** (Open Government Licence), recortadas para dejar fuera el logotipo, la corona y el escudo, que esa licencia no cubre. De los demás productos describimos lo que vimos, sin reproducir sus imágenes.
- Todo lo que sigue se vio en pantalla. Cuando algo viene de un texto de ayuda y no de una interfaz, se dice.

### Lo que no se pudo ver

| Referencia | Qué quedó fuera | Por qué |
|---|---|---|
| Planning Center Check-Ins | La aplicación de estación en vivo y sus estilos | Pide cuenta y una estación emparejada. Usamos las capturas y GIF de su centro de ayuda (`help.planningcenter.com`) |
| Tally | Un formulario publicado (`tally.so/r/…`) | Solo encontramos uno incrustado en un video. Todo es de la vista previa de plantillas; la barra de progreso existe en el código pero no se ve en esa vista |
| Airtable | Una interfaz real | Pide cuenta. Vimos imágenes de su centro de ayuda y de la página de producto. Las páginas de "Number" y "Chart" ahora redirigen a un artículo de funciones antiguas |
| Mobbin | `/discover`, búsqueda, flujos, imágenes en resolución completa | Piden cuenta. Las páginas de categoría y de pantalla individual sí son públicas, en tamaño reducido |
| Refero | Más de 6 resultados por búsqueda, pantallas similares, flujos completos | "1,373 more pages hidden": piden cuenta |
| Mobbin y Refero | Flujos de kiosco o de registro en sitio (check-in) | No aparecieron en las categorías públicas y no pudimos buscarlos sin cuenta |

## 1. Planning Center Check-Ins

**Lo que vimos.** Capturas y GIF del centro de ayuda de la estación atendida, la de autoservicio y la de lista (roster):

- Estación atendida en reposo: un solo campo, "Search or add new by name, phone number, or barcode". Arriba, el nombre del evento y "Start over"; abajo, el nombre de la estación ("Lobby").
- Después de buscar: **una familia por pantalla**, con cuatro renglones de persona (casilla, foto, nombre) y "Show more people to check in". Abajo a la derecha, un solo botón que dice cuántos: **"Check in 1 person"**.
- El GIF completo: escribir "mi jo", aparece "Johnson Household", se marca una persona, botón, y la confirmación "You're all set! Have an amazing time!" con una palomita verde.
- Autoservicio: teclado numérico grande para los últimos cuatro dígitos del teléfono, o escanear un código.
- El texto de ayuda recomienda buscar con "las primeras dos letras del nombre y las primeras dos del apellido". Hay atajos de teclado (`/` busca, `s` empieza de nuevo, `esc` regresa) y la estación se reinicia sola tras un tiempo sin uso.
- Todas las capturas usan fondos degradados morados o azules detrás de tarjetas blancas.

**Qué funciona y por qué.** Una estación compartida no tiene "inicio": tiene **una acción**, buscar. El voluntario escribe dos letras y dos letras, no un nombre completo con acentos. El botón final dice exactamente qué va a pasar y a cuántas personas. "Start over" siempre está visible porque la computadora pasa de mano en mano.

**Qué flujo de Emmaus beneficia.** La pantalla de inicio del coordinador y el arranque de cada entrevista: un campero se sienta enfrente y hay que encontrarlo rápido. También la estación del campero (supervisada), que debe poder abandonarse y recuperarse.

**Qué no copiar.** El degradado morado de fondo (justo lo que este proyecto quiere evitar), las fotos de cada persona (no las tenemos y no queremos pedirlas a menores), los atajos de una sola tecla (WCAG 2.1.4 pide poder desactivarlos, y un voluntario puede activarlos sin querer al escribir) y la confirmación con mensajes aleatorios "divertidos", que no caben en una conversación espiritual.

**Cómo lo adaptamos.** El inicio del coordinador abre con un letrero cuya única acción es buscar ("¿A quién vas a entrevistar?") y cuyo texto enseña el truco: "las primeras letras de su nombre y apellido, por ejemplo 'mat jim'". La búsqueda ahora acepta palabras en cualquier orden (`events/views/interview.py`, prueba `test_search_matches_the_first_letters_of_each_name`). La barra dice "Estás en Computadora 4", igual que la estación de Planning Center lleva su nombre al pie. "Terminar por ahora" y "Guardar y salir" cumplen el papel de "Start over".

## 2. Tally

**Lo que vimos** (vista previa de plantillas): una pregunta por paso con "← Back" arriba, escala de 0 a 10 en casillas de 36 px, botón negro "Next →" de 36 px de alto. Las opciones son "chips" con letras A, B, C, D. Al dejar vacío un campo obligatorio y presionar "Next", aparece debajo un texto rojo de **12 px** ("Please enter a value"), el campo no cambia y surge un botón "Continue anyway". Al presionar la tecla `b` no se eligió la opción B: las letras no funcionaron como atajo en nuestra prueba.

**Qué funciona y por qué.** Mucho aire alrededor de una sola pregunta; nada compite con ella. "Back" arriba y a la izquierda, lejos del botón de avanzar. La pregunta es el título de la página.

**Qué flujo de Emmaus beneficia.** El cuestionario, sobre todo cuando el campero responde solo.

**Qué no copiar.** Los controles de 36 px (pequeños para dedos y para personas mayores), el error de 12 px que no cambia el campo, las letras A/B/C que parecen atajos y no lo son, y "Continue anyway", que en Emmaus no tendría sentido: ninguna pregunta es obligatoria y nadie debe sentirse presionado.

**Cómo lo adaptamos.** Una pregunta por pantalla con la pregunta como único `h1`, "Atrás" arriba a la izquierda, opciones de 64 px de alto que ocupan todo el ancho y un solo botón de avanzar. El error, cuando existe, sigue el patrón de GOV.UK (más abajo), no el de Tally.

## 3. Airtable Interface Designer

**Lo que vimos.** En la imagen de ayuda del diseño "Dashboard": barra lateral azul, título y descripción, **una fila de filtros**, **una fila de seis números** iguales (uno con barra de progreso, dos con color condicional verde y rojo), **tres tarjetas de gráficas** del mismo alto (barras agrupadas, barras simples, dona) y al final la lista de registros. El texto de ayuda dice que el diseño "se basa sobre todo en números y gráficas" y que cada gráfica puede abrir los datos que la forman.

**Qué funciona y por qué.** El orden: filtro → cifras → gráficas → registros. Cada nivel responde a una pregunta más detallada que el anterior, y siempre se puede bajar al dato.

**Qué flujo de Emmaus beneficia.** El resumen del pastor y, después, el tablero del tutor.

**Qué no copiar.** Casi todo lo visual: es exactamente la plantilla de tablero genérico (tarjetas iguales, seis cifras sin jerarquía, dona). Ninguna cifra dice si es buena o mala ni qué hacer con ella; el color condicional rojo/verde depende solo del color.

**Cómo lo adaptamos.** Conservamos el orden, no la forma. El resumen del pastor es un informe de cinco preguntas: a la izquierda la pregunta y su respuesta en una frase; a la derecha la evidencia (cifras, gráficas, equipos, totales). Cada gráfica tiene "Ver los datos en una tabla". No hay donas ni tarjetas.

## 4. GOV.UK Design System

**Lo que vimos.** Todo es público. Revisamos los patrones de página de pregunta, revisar respuestas, confirmación, validación y resumen de errores, y los componentes de opciones (incluido el separador "or" y el contenido que se revela), lista resumen, botón, panel, etiqueta y aviso. Medimos sus estilos: pregunta como `h1` de 36 px (27 px en móvil), pistas de 19 px en `#484949`, opciones redondas de 40 px, campo con borde negro de 2 px, botón verde con sombra inferior de 2 px, contenido revelado con línea gris de 4 px a la izquierda.

El foco con teclado: fondo amarillo `#ffdd00` y subrayado negro grueso en enlaces; en botones, fondo amarillo y texto negro; en campos, contorno amarillo de 3 px y borde negro más grueso. Al pulsar un enlace del resumen de errores, el foco salta al campo con el problema.

| Pregunta | Revisar respuestas |
|---|---|
| ![GOV.UK, página de pregunta](img/investigacion/govuk-pregunta.png) | ![GOV.UK, revisar respuestas](img/investigacion/govuk-revisar-respuestas.png) |

| Resumen de errores | Confirmación |
|---|---|
| ![GOV.UK, resumen de errores](img/investigacion/govuk-resumen-de-errores.png) | ![GOV.UK, confirmación](img/investigacion/govuk-confirmacion.png) |

**Qué funciona y por qué.** Cada patrón resuelve un momento de incertidumbre: "¿qué me preguntan?", "¿qué contesté?", "¿qué salió mal y dónde?", "¿ya quedó?". El foco amarillo se ve en cualquier fondo. El botón con sombra parece algo que se puede presionar.

**Qué flujo de Emmaus beneficia.** Todo el flujo de entrevista: "Antes de empezar" (errores), cada pregunta, "Revisa las respuestas", "Entrevista completada", y el campero solo.

**Qué no copiar.** La identidad institucional: tipografía GDS Transport (de uso exclusivo del gobierno británico), el azul y verde del gobierno, el tono de trámite. Emmaus acompaña a jóvenes en una conversación personal; no es una oficina.

**Cómo lo adaptamos.** Copiamos los patrones de interacción, no la apariencia: "Hay un problema" con enlaces al campo, error junto al campo con barra roja a la izquierda, "Cambiar" con texto oculto que dice qué se cambia, panel de confirmación (en añil, no en verde), separador "o bien" antes de "Prefiero no responder", revelado con línea a la izquierda, foco amarillo con borde de tinta y botones con sombra inferior de tinta.

## 5. Mobbin y Refero

**Lo que vimos** (páginas públicas de pantalla individual):

| Pantalla | Qué muestra | Qué tomamos o descartamos |
|---|---|---|
| **Typeform**, encuesta de salida | Una pregunta, opciones en píldora con letras, botón "OK", pasos arriba | El paso a paso arriba sí; las píldoras pequeñas y las letras, no |
| **Udemy**, opción múltiple | Opciones a todo el ancho con borde, botón negro abajo | Confirma el renglón de opción a todo el ancho |
| **IKEA**, recoger pedido | Dos mosaicos grandes para elegir (Entrega / Recoger) | Confirma las opciones grandes como mosaicos para decisiones de una sola vez |
| **Better Stack**, incidentes | Saludo "How is it going, Alex?", buscar y el botón principal arriba, la tabla abajo | Saludo y acción principal juntos arriba; tema oscuro y barra lateral de 8 apartados, no |
| **Square**, **Fingerprint**, **Midday** | Barra lateral, fila de tarjetas de cifras, gráfica de línea, tablas. Refero lista las fuentes de Fingerprint: Inter y JetBrains Mono | Son el tablero genérico. Evidencia de lo que **no** queremos parecer |
| **15Five**, encuesta | Varias preguntas por página con escalas de 1 a 5 | Varias preguntas por pantalla no sirven para un campero nervioso |
| **Duolingo**, opción múltiple (iOS) | Barra de progreso arriba, botón deshabilitado hasta elegir | Deshabilitar el botón confunde; en Emmaus siempre se puede avanzar |

**Qué funciona y por qué.** Las aplicaciones que mejor guían (Typeform, Udemy, IKEA) hacen grande lo que se toca y pequeño todo lo demás. Las que más se parecen entre sí son los tableros.

**Qué no copiar.** La estética de tablero de producto de software: tarjetas de cifras iguales, Inter con una monoespaciada para datos, tema oscuro con un acento brillante.

## Guías de apoyo usadas

- **frontend-design** (el SKILL.md de la extensión, leído desde el catálogo de extensiones porque no estaba instalada como extensión activa). Su lista de rasgos de "diseño generado" describe casi exactamente la versión anterior de Emmaus: fondo crema cercano a `#F4F1EA` con una serif de alto contraste y un acento terracota; metadatos unidos con "·"; flecha "→" al final de los botones; etiquetas encima de cada título. Eso guió el diagnóstico de `design-direction.md`.
- **ui-ux-pro-max**. Su generador de sistema de diseño, para "iglesia, comunidad, voluntarios", devolvió exactamente el cliché que hay que evitar: morado "espiritual" `#7C3AED` con dorado, Cormorant Garamond con Crimson Pro y una página de aterrizaje con carrusel de testimonios. Lo descartamos y lo citamos como ejemplo. Sí usamos su lista de verificación (contraste 4.5:1, objetivos de 44 px, foco visible, movimiento reducido, etiquetas visibles) y sus guías de indicador de progreso y de gráficas de avance contra una meta.
- **dataviz**. Su validador de paleta decidió el color de los datos: avance y "en curso" son una escala **ordinal** de un solo tono (añil oscuro y añil claro), no dos colores distintos. El amarillo quedó fuera de los datos (contraste 1.67:1 contra blanco). Ver `design-system.md`.

## Decisiones que salen de esta investigación

| Decisión | Fuente | Dónde está |
|---|---|---|
| El inicio del coordinador es una búsqueda, no un menú | Planning Center | `templates/events/coordinator/home.html` |
| Buscar con las primeras letras de nombre y apellido, en cualquier orden | Planning Center (texto de ayuda) | `events/views/interview.py`, `events/views/coordinator.py` |
| "Estás en Computadora 4" visible siempre | Planning Center (nombre de la estación) | barras del coordinador y de la entrevista |
| Una pregunta por pantalla, "Atrás" arriba a la izquierda | Tally, GOV.UK, Typeform | `templates/events/interview/_step_form.html` |
| Opciones de 64 px a todo el ancho, con el control real encima de la marca pintada | GOV.UK, Udemy, IKEA | `.choice` en `static/css/rotulo.css` |
| Resumen de errores con enlaces, error junto al campo | GOV.UK | `.error-summary`, `.question--error` |
| Revisar respuestas con "Cambiar" | GOV.UK | `interview/review.html`, `kiosk/review.html` |
| Panel de confirmación | GOV.UK | `.confirm` |
| Foco amarillo con borde de tinta | GOV.UK | sección "Foco" de `rotulo.css` |
| Informe en orden: respuesta → cifras → gráficas → datos | Airtable (solo el orden) | `templates/events/pastor/_home_live.html` |
| Sin tarjetas de cifras iguales, sin donas, sin tema oscuro por defecto | Airtable, Square, Fingerprint, Midday, Better Stack (como contraejemplo) | todo el sistema |

## Investigación v3 (octubre de 2026)

Fecha: 26 de octubre de 2026. El dueño rechazó dos direcciones seguidas ("Cuaderno de campo" y "Rótulo") y pidió algo "profesional, tranquilo y muy fácil para personas no técnicas", mirando herramientas parecidas y cristianas. Esta ronda estudió 32 herramientas y llevó a **Atrio** (`design-direction.md`, `design-system.md`).

### Cómo se hizo

- Páginas públicas abiertas con Playwright 1.63 (Chromium, 1440 y 390 px), las capturas oficiales de cada ficha de App Store (bajadas con la API pública de iTunes) y centros de ayuda. **No se inició sesión, no se creó ninguna cuenta y no se envió ningún formulario.** Tipos, tamaños y colores salen de los estilos calculados de cada página, no de una estimación a ojo.
- Las capturas (67 archivos), las notas largas por herramienta y la muestra de las tres direcciones con sus fuentes reales están **fuera del repositorio**, en `~/sandbox-docs/emmaus/design-v3/`. El repositorio es público: aquí solo se describe lo que se vio, sin reproducir imágenes de otros productos.
- No se repitieron Typeform, Tally, GOV.UK, Udemy, IKEA, Airtable, Square, Fingerprint, Midday ni Better Stack (secciones anteriores). Sus conclusiones (una pregunta por pantalla, los errores de GOV.UK) se conservan.

### Lo que se estudió

| Grupo | Herramientas |
|---|---|
| Administración de iglesias y sus apps (10) | Planning Center (People, Check-Ins y Church Center), Breeze (hoy Tithely Church Management), Tithely, Pushpay, Subsplash, Realm, Rock RMS, Faithlife (Proclaim), Text In Church, Nucleus |
| Campamentos y jóvenes (7) | CampDoc, UltraCamp, CampMinder, Regpack, Grow Curriculum, Orange, Download Youth Ministry |
| Biblia y discipulado (6) | YouVersion, BibleProject, RightNow Media, Dwell, Hallow, Lectio 365 |
| Controles tranquilos, no de iglesia (9) | Linear, Notion, Stripe, Basecamp, HEY, Cal.com, Mercury, Headspace, Apple Health |

Lo que más pesó, por grupo:

- **Iglesias.** Planning Center usa Gilroy 700 en su sitio, pero Church Center, en la página de una iglesia, usa la letra del sistema, deja elegir **un** color principal y todo lo demás es blanco y gris: el logotipo de la iglesia es lo único con identidad. Rock RMS usa Figtree 700 (de ahí la elección de Figtree como letra "de iglesia"), con una densidad de administrador. Tithely y Breeze, Pushpay y Subsplash son el sitio genérico de software: píldoras, degradados, estrellas, muros de logotipos. Realm y Faithlife se ven datados: foto oscura con velo, fondo oscuro con brillos. Text In Church pone en su titular el mismo propósito de Emmaus: "Make sure no one slips through the cracks".
- **Campamentos y jóvenes.** El pase de lista del staff (CampDoc) y el aviso que primero tranquiliza (CampMinder: "Nothing here requires setup. It's already working for you.") sirven. Casi todo lo demás es lo que hay que evitar: cuatro familias tipográficas y palabras con degradado (CampDoc, Regpack), fotos de menores con velo (UltraCamp, CampDoc), reconocimiento de rostros de niños (Campanion, de CampMinder: lo contrario de lo que Emmaus promete), mayúsculas espaciadas de 11 px y fondo crema (Orange), letra a mano y tono de videojuego o de tienda (Grow, Download Youth Ministry).
- **Biblia y discipulado.** YouVersion usa serifa solo en el texto de la Escritura y una sans en la interfaz. BibleProject: hojas blancas, chips grises y una línea de avance fina; su guía de marca dice "if you can't use color use black on white". Hallow: una tarjeta, un título de una frase y botones apilados con el verbo completo. Lectio 365 nombra los momentos del día (mañana, mediodía, noche). Dwell es casi exactamente "Cuaderno de campo" (serifa sobre crema): está bien hecha y aun así es la combinación que el dueño rechazó.
- **Controles tranquilos.** Linear explica su rediseño de 2026 con "Don't compete for attention you haven't earned" y "Structure should be felt not seen". Stripe usa su violeta solo en acciones y enlaces y pone las etiquetas encima de los campos. Basecamp: títulos 800, cuerpo llano, enlaces subrayados que se ven enlaces. HEY ("The Screener"): una frase que dice qué se decide y, en cada renglón, Sí y No. Cal.com: opciones a todo el ancho, el elegido en negro. Mercury pone las tareas antes que cualquier número. Headspace es tranquilo por sus ilustraciones y su tamaño de letra, no por sus colores.

### Lo que no se pudo ver

| Herramienta | No se vio | Se usó en su lugar |
|---|---|---|
| Pushpay ChMS | La interfaz web (pide cuenta) | El sitio y la app de donaciones |
| Realm, Subsplash, Tithely y Breeze ChMS, Planning Center People (web) | La interfaz de administración | Maquetas del sitio y fichas de App Store |
| Rock RMS | La interfaz completa | Una captura real del perfil de una persona en su página de funciones |
| Planning Center Check-Ins | La estación en vivo | La ficha de App Store y la investigación anterior de su centro de ayuda |
| CampDoc, UltraCamp, Regpack | Cualquier interfaz que no sea una maqueta de marketing | El sitio y, para CampDoc, la app de padres |
| Faithlife | La portada (salió vacía) | Proclaim y Proclaim Remote |
| Nucleus, Text In Church | La interfaz de la herramienta | El sitio (y la app de Text In Church) |
| Apple Health | La app (no está en App Store; la guía es solo texto) | La página de producto y la guía de ayuda |
| BibleProject, RightNow Media, Dwell, Hallow, YouVersion, Linear, Notion, Mercury, Headspace, Basecamp, HEY | La app dentro de una cuenta | Sitios, el lector público de YouVersion y fichas de App Store |

### Cinco patrones de lo "profesional y tranquilo"

1. **Un estado en una frase y un solo botón.** Church Center, en la página de una iglesia: "Registration is open. Already registered? Log in" y un botón, "Register". Check-Ins: "Check out 2 people". HEY: "You get to decide…" con Sí y No. Hallow: una tarjeta, tres botones con el verbo completo.
2. **Marco neutro, un color y el logotipo de la iglesia como único adorno.** Church Center deja elegir un color principal y todo lo demás es blanco y gris; en la página vista, el logotipo y el color del botón son toda su identidad. Cal.com y Stripe hacen lo mismo con su marca.
3. **Jerarquía por peso y espacio, no por cajas.** Linear (2026): "Don't compete for attention you haven't earned". Basecamp: títulos 800 y cuerpo llano. BibleProject: hoja blanca, chips grises, una línea de avance.
4. **El color hace tres trabajos y nada más.** Stripe: violeta para acción y enlaces, palomita verde para lo hecho, el resto gris. Church Center: enlaces y estados. Mercury pone "Tasks" antes que cualquier número.
5. **La calidez está en las palabras y en la hora, no en el adorno.** "Hello, Ashley!" y "We're so happy you're here" (Church Center); mañana, mediodía y noche (Lectio 365); "que nadie se escape" (Text In Church). La calidez por ilustración (Headspace, Hallow) pide un presupuesto que Emmaus no tiene y no debe fingir.

### Cinco antipatrones

1. **Degradados y brillos como identidad** (Subsplash, RightNow Media, Mercury, CampMinder, HEY): es el rasgo "hecho con IA".
2. **Foto de stock con velo oscuro y "Book a demo"** (UltraCamp, Realm, Orange, Pushpay). En Emmaus serían menores.
3. **Todo en píldora, más prueba social** (Tithely, Breeze, Text In Church): si todo pesa igual, nada dice "esto sigue".
4. **Personalidad prestada por la tipografía:** letra a mano (HEY, Download Youth Ministry), mayúsculas espaciadas (Orange, Subsplash), serifa sobre crema (Dwell). Es el error de las dos direcciones rechazadas: "Cuaderno" (crema, Literata, terracota) y "Rótulo" (Recursive Casual, letrero amarillo, bordes de tinta de 2 a 3 px). Las dos ponen primero una metáfora que el voluntario debe descifrar antes de hacer su tarea.
5. **Densidad de administrador:** texto diminuto (CampDoc, Rock RMS, People en la web) y paneles de muchas cifras.

### Tres direcciones

Las tres compartían esqueleto (los componentes de `design-system.md` §2) y cambiaban de tokens; se compararon en una muestra con las fuentes reales. Riesgo en las tres: chile `#B8321C` (6.0:1).

**A. Atrio, la puerta abierta del templo.** Lo que hacen Church Center (iglesias, personas no técnicas) y Basecamp y Cal.com (trabajo diario): blanco, un solo color, frases como títulos. Figtree variable (400 para el texto de 18 px, 500 para etiquetas, 700 para botones, 800 para títulos de 26 a 44 px), unos 20 KB. Tinta `#16222B` (16.2:1), tinta suave `#44525D` (8.0:1), tinta tenue `#5A6872` (5.7:1), acción petróleo `#0B6270` (7.0:1, letra blanca encima a 7.0:1; hover `#084E5A`, 9.3:1), tinte `#E4F1F3`, bordes `#DCE1E4` y de control `#7B8A94` (3.6:1). Densidad generosa, radios de 10 px, bordes de 1.5 px, sin sombras. Lo que sigue es un panel tinte con una pregunta como título y una acción rellena. Datos en petróleo, petróleo medio `#4C95A2` (3.4:1) y contorno, con la frase "Llevas 18 de 31." Portada: "Platicar con cada joven, sin perder a nadie."

**B. Paloma, la iglesia en blanco y negro.** Parte de la identidad monocroma de la iglesia: titular pesado y redondeado en Nunito 800 y 900 (lo más cercano a la letra de su logotipo) con Open Sans para el texto, la misma de la iglesia; unos 85 KB las dos. Acción negra `#111111` (letra blanca a 18.9:1). Lo que sigue es un panel invertido, negro con botón blanco: el único bloque oscuro de la pantalla. Datos en negro, gris `#8C8C8C` (3.4:1) y contorno; tres estados en gris se distinguen peor. Portada: "Una conversación a la vez."

**C. Quietud, el producto tranquilo.** De Linear, de la app de Stripe y de la herencia de GOV.UK: página gris muy suave `#F5F7FA` con superficies blancas, sin cajas, un azul acero `#2B5A9E` (6.9:1). Public Sans en una sola familia, unos 26 KB; radios de 6 px y bordes de 1 px, la más compacta. Lo que sigue no tiene caja: es el primer bloque, con letra más grande y el único botón relleno. Portada: "El campamento, en orden y en calma."

### Decisión

**Atrio**, aprobada por el dueño el 26 de octubre de 2026. Es lo que pidió (Church Center es exactamente un marco neutro más un color), evita las dos objeciones (sin crema ni serif, sin voz pintada, sin amarillo, sin bordes de tinta), tiene la señal de "lo que sigue" más tranquila que no falla, es la más ligera y se lee en proyector (7:1 en la acción, 16:1 en la tinta). Paloma era el concepto más propio, pero con dos familias, un bloque negro en cada inicio y tres estados en gris; Quietud era la más genérica y la más cercana al añil de Rótulo. El riesgo de Atrio es verse como el software de iglesia común; si el dueño lo siente así, la salida es Paloma sobre el mismo esqueleto.

Lo que esta ronda cambia de las decisiones anteriores: el foco amarillo de GOV.UK pasa a un anillo de tinta de 3 px con 2 px de blanco; el panel de confirmación pasa de añil a petróleo; el letrero amarillo pasa a un panel tinte. Los patrones de interacción (una pregunta por pantalla, el resumen de errores, "Cambiar", "o bien") se quedan.
