# Dirección de diseño · Emmaus

Emmaus cambió de dirección visual dos veces en una semana. Este documento guarda por qué: lo que fue cada dirección retirada, por qué se retiró y lo que hoy es **Atrio**, la dirección vigente desde el 26 de octubre de 2026. Los tokens y componentes están en `design-system.md`; la investigación, en `design-research.md`.

## El encargo, en una frase por persona

Estas frases no cambiaron con ninguna dirección. Son la prueba de cada pantalla.

- **Coordinador:** "Tengo a un campero enfrente. ¿Cómo lo encuentro y empiezo?"
- **Campero:** "¿Esto es privado? ¿Cuánto falta? ¿Puedo no contestar?"
- **Tutor:** "¿Cuál de mis coordinadores necesita ayuda ahorita?"
- **Pastor:** "¿Cómo vamos, qué falta y qué tengo que decidir en la reunión?"
- **Proyector:** "Que se lea desde la última fila, sin un solo nombre."

## Dos direcciones retiradas

### Cuaderno de campo (hasta el 23 de octubre de 2026)

**Qué era.** Papel crema `#F5F0E6`, títulos en Literata (serif de alto contraste), marcas terracota `#A3461E`, metadatos unidos con "·" ("#0066 · Cabaña 14"), flechas al final de los botones ("Iniciar entrevista →"), una etiqueta en cursiva encima de cada título, numeración decorativa, una cifra enorme como héroe de cada tablero ("63 %") y reglas de 1 px entre todo. Vivía en `emmaus.css`.

**Por qué se retiró.** Era cuidadosa y accesible, pero juntaba casi todos los rasgos que hoy delatan una interfaz hecha por inteligencia artificial; crema, serif y terracota son, casi al hexadecimal, la combinación por defecto de los generadores. Las capturas de antes siguen en `img/rotulo/antes/`.

### Rótulo (del 23 al 26 de octubre de 2026)

**Qué era.** Los letreros pintados a mano de un campamento. Dos voces: **Recursive Casual** para lo que dice una persona (preguntas, saludos, titulares, la clase `.voice`) y **Atkinson Hyperlegible Next** para lo que informa el sistema. Blanco, carbón `#1C1B19`, añil `#1F3C8F` para acción y datos, un **letrero amarillo** maíz `#F4C20D` por pantalla para lo que sigue y para "estás aquí", y chile `#B8321C` solo para riesgo. Bordes de tinta de 2 y 3 px, una sombra de "tecla" bajo el botón principal, radios de 2 y 4 px. Se eligió entre tres conceptos construidos como tableros reales: Rótulo, *Bitácora* (la hoja de control de la oficina, fría para un joven que habla de su fe) y *Sobremesa* (la plática después de la comida, otra vez serif sobre casi crema).

**Por qué se retiró.** El dueño la encontró poco profesional y poco tranquila. La investigación v3 lo explica: es el antipatrón de la **personalidad prestada por la tipografía**. Como "Cuaderno", Rótulo ponía primero una metáfora (el letrero, la letra pintada) que el voluntario tenía que descifrar antes de hacer su tarea, y el amarillo con bordes de tinta gritaba en cada inicio.

**Lo que dejó.** Casi todo lo que no se ve: la estructura de cada pantalla, los componentes y sus nombres de clase, la accesibilidad (axe en 0, un solo `h1`, objetivos de 44 px, foco visible) y las reglas de lenguaje. Por eso la hoja conserva su nombre, `static/css/rotulo.css`.

## Atrio (2026-10-26)

La puerta abierta del templo. Atrio es lo que hacen Church Center (iglesias, personas no técnicas) y Basecamp y Cal.com (trabajo diario): blanco, un solo color, frases como títulos. La calidez la dan las palabras y un titular pesado y redondeado que recuerda al logotipo de la iglesia, no un adorno.

El dueño pidió algo "profesional, tranquilo y muy fácil para personas no técnicas", mirando herramientas parecidas y cristianas. Se estudiaron 32 (`design-research.md`, "Investigación v3") y se compararon tres direcciones con las fuentes reales: **Atrio**, **Paloma** (la iglesia en blanco y negro) y **Quietud** (el producto tranquilo). El dueño aprobó Atrio.

### Por qué Atrio

1. **Es lo que pidió el dueño:** mirar herramientas cristianas y decidir. Church Center (Planning Center dice servir a más de 78,000 iglesias) es exactamente un marco neutro más un color.
2. **Evita las dos objeciones.** Sin crema ni serif, sin voz pintada, sin amarillo, sin bordes de tinta. Lo que "se ve" de Emmaus pasa a ser lo suyo: frases como títulos, las marcas del conteo, "Estás en Computadora 4".
3. **La señal de lo que sigue es tranquila y no falla:** un panel tinte con un solo botón relleno. Quietud tenía la señal más débil (sin caja); Paloma, la más ruidosa (un bloque negro en cada inicio).
4. **Es más ligera:** un archivo de Figtree de 23 KB reemplaza a Recursive (109 KB) y a Atkinson. Paloma cargaba 85 KB en dos familias.
5. **Se ve en proyector y en pantallas viejas:** 7.01:1 en el color de acción y 16.18:1 en la tinta.

**El riesgo.** Blanco, turquesa y una sans geométrica es el aspecto común del software de iglesia. Si el dueño lo siente genérico, la salida es Paloma con el mismo esqueleto: acción `#111111` y Nunito. Frente a Paloma, Atrio usa una familia y no tres estados en gris; frente a Quietud, no es la más genérica ni la más cercana al añil de Rótulo.

### Cinco patrones que sigue

1. **Un estado en una frase y un solo botón.** Church Center, en la página de una iglesia: "Registration is open. Already registered? Log in" y un botón "Register". En Emmaus: el panel de lo que sigue, con una pregunta como título ("¿A quién vas a entrevistar?") y una acción rellena.
2. **Marco neutro, un color y el logotipo de la iglesia como único adorno.** Church Center deja elegir un color y todo lo demás es blanco y gris; el logotipo y el color del botón son toda su identidad. En Emmaus, petróleo `#0B6270` y el logotipo de la iglesia.
3. **Jerarquía por peso y espacio, no por cajas.** Linear: "Don't compete for attention you haven't earned". Basecamp: títulos 800 y cuerpo llano. En Emmaus, títulos en Figtree 800, sin tarjetas y sin sombras.
4. **El color hace tres trabajos y nada más.** Stripe: violeta para acción y enlaces, palomita para lo hecho, el resto gris. En Emmaus, petróleo para la acción, el estado y los datos; chile solo para riesgo.
5. **La calidez está en las palabras y en la hora, no en el adorno.** "We're so happy you're here" (Church Center), mañana, mediodía y noche (Lectio 365), "que nadie se escape" (Text In Church). En Emmaus, el saludo, "Llevas 18 de 31." y la portada.

### Principios

- **Un panel de lo que sigue por pantalla, a lo mucho.** Tinte, una pregunta, un botón relleno. Lo demás no compite.
- **Un color.** Petróleo es acción, estado y datos; nada es amarillo; chile solo es riesgo.
- **Frases antes que cifras.** "Llevas 18 de 31." "Getsemaní va más atrás, con 42 %." Sin cifras gigantes.
- **Una marca por persona** cuando el grupo cabe en la pantalla. Un coordinador ve a sus 31 camperos, no un porcentaje.
- **Sin tarjetas, sin sombras, sin píldoras, sin iconos, sin degradados.** Las piezas se separan con espacio y con líneas finas.
- **Dos radios y un borde:** 10 px para lo que se toca, 4 px para las marcas, bordes de 1.5 px.
- **Sin "·", sin "→", sin etiquetas encima de los títulos.** Los datos se escriben como frase: "Cabaña 11, 17 años".
- **El movimiento explica un cambio o confirma una acción; nunca decora.** Con "reducir movimiento", nada se mueve (`design-system.md`, §Movimiento).

### Lo que se conserva de Rótulo y lo que cambió

| Se conserva | Cambió |
|---|---|
| La estructura y los componentes: encabezado de página, camino, opciones, renglones, lista resumen, cifras, plegable, presentación, conteo y regla, con sus nombres de clase | Recursive Casual y Atkinson pasan a una sola familia, Figtree. `.voice` se queda como clase, pero ya no es otra letra: es Figtree 800 |
| Un solo `h1`, objetivos de 44 px (opciones de 64, botón de 60), texto base de 18 px, rejilla de 4 px | El letrero amarillo `.sign` pasa a ser el panel tinte de lo que sigue; la marca amarilla de "estás aquí" pasa a un punto petróleo |
| Los errores de GOV.UK, "Cambiar", "o bien" antes de "Prefiero no responder" | Los bordes de tinta de 2 y 3 px, la sombra de "tecla" y los radios de 2 y 4 px pasan a bordes de 1.5 px, sin sombra, y radios de 10 px (controles) y 4 px (marcas) |
| Foco visible en todo fondo | El foco amarillo pasa a un anillo de tinta de 3 px con 2 px de blanco (16:1), igual en todo fondo; en las barras y los filtros se dibuja hacia adentro |
| Chile solo para riesgo; el color nunca es la única señal; axe en 0 | Añil pasa a petróleo y el maíz sale de todo uso (foco, aviso, marca). El color de acento de la iglesia ya no pinta lo que sigue: solo "estás aquí" (`--mark`) |
| Las vistas, el JavaScript (depende de `data-*`) y las reglas de lenguaje | La hoja conserva su nombre, `static/css/rotulo.css`, y los marcos siguen en `templates/events/rotulo/` |

### La portada

| Teléfono | Escritorio |
|---|---|
| ![Portada en teléfono](img/atrio/portada-390.png) | ![Portada en escritorio](img/atrio/portada-1440.png) |

Una columna del ancho de la entrada, centrada, sin fotos y sin iconos, en este orden:

1. El logotipo de la iglesia, si lo tiene.
2. **"Platicar con cada joven, sin perder a nadie."** Es la promesa entera en una frase. "Platicar" es como se dice en México; "cada joven" es uno por uno, no un porcentaje; "sin perder a nadie" es el seguimiento, el mismo propósito que Text In Church pone en su titular ("Make sure no one slips through the cracks").
3. "Emmaus ayuda al equipo del campamento a conversar con cada campero sobre su fe y a recordar a quién buscar después." Dice quién la usa (el equipo, no el campero) y qué hace, sin palabras de software.
4. Un solo botón: **"Entrar a Emmaus"**, con el mismo nombre que el título de la página a la que lleva (el nombre de una acción es el mismo en todo el flujo).
5. Justo debajo, "¿Vas a ayudar en un evento? Escribe el código", a `/unirme/`: el voluntario nuevo llega con el código impreso del evento, no con una cuenta.
6. Tres promesas de una frase, en renglones y sin marcas: "Cada pregunta se puede dejar sin contestar.", "Cada persona ve solo lo que le toca." y "Si alguien necesita seguimiento, queda anotado." Son lo que un papá, un pastor o un voluntario necesita saber antes de entrar, y las tres son verdad en el código ("Prefiero no responder", los permisos por rol, los seguimientos). La investigación las dibujaba con palomita; quedaron sin marca porque Atrio no usa iconos y la frase ya lo dice.
7. Quién la usa: "Iglesia del Camino usa Emmaus en sus campamentos." (o "… y en su discipulado." si la iglesia lo activó). Sin una iglesia activa: "Emmaus es la herramienta de la iglesia para entrevistar y acompañar."

**Por qué así.** La portada anterior tenía dos columnas de texto de lectura ("Por qué existe", "En el campamento", "En el discipulado", privacidad): explicaba mucho y no decía qué hacer. Sin fotos, porque en Emmaus serían menores (el antipatrón de la foto de stock con velo y "Book a demo"). Con sesión iniciada, la portada no se ve: lleva a tu inicio.

### El logotipo de la iglesia

El logotipo es el único adorno de Atrio y la paleta está hecha para convivir con él:

- **Monocromo, y el color nunca lo toca.** Lo recomendado es negro sobre transparente; no se recolorea y la paleta no cambia por él. Un marco blanco con tinta casi negra y un solo color de acción deja que cualquier logotipo negro se vea como propio de la página, como en Church Center.
- **Va donde va "Emmaus".** En la barra, a 32 px de alto. En la entrada y en la portada, a la cabeza, centrado sobre el título (hasta 220 × 72 px); ahí la barra conserva la palabra "Emmaus" para no repetirlo, y sin logotipo no se dibuja un segundo "Emmaus".
- **Titular y logotipo hablan igual.** El titular pesado y redondeado de Figtree 800 recuerda el de un logotipo de iglesia; por eso Atrio no necesita otro adorno.
- **En Noche**, la versión en blanco o, si no hay, el logotipo sobre una placa blanca pequeña.
- **Nunca en datos ni gráficas.** Es identidad, no información.

Las reglas completas están en `design-system.md`, "Marca de la iglesia".

### Modo presentación

Diapositivas de 16:9 para leerse desde el fondo de un salón, con el titular en Figtree 800, la regla de avance, los equipos con el promedio marcado y al pie siempre "Solo totales. Sin nombres ni respuestas." **Cal** para salones con luz y **Noche** para salones oscuros: fondo `#141B33`, texto `#F3F5F6` (15.56:1) y datos `#8FC1CA` y `#4C95A2` (8.63:1 y 4.96:1); la investigación había propuesto `#0E1A20`, y se quedó el azul noche de Rótulo con los datos en petróleo claro. "Modo noche" es un botón de la barra de control y se recuerda en esa computadora; las diapositivas y el cambio entre Cal y Noche se funden.

| Cal | Noche |
|---|---|
| ![Presentación en Cal](img/atrio/presentacion-cal.png) | ![Presentación en Noche](img/atrio/presentacion-noche.png) |

## Lo que sigue

Probar Atrio con voluntarios reales en una computadora compartida y con un proyector real. Si el dueño lo siente genérico, probar Paloma sobre el mismo esqueleto antes de pensar en una cuarta dirección.
