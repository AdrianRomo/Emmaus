# Dirección de diseño · Emmaus

## El problema con la interfaz anterior

La versión anterior se veía como cualquier tablero generado: degradados de fondo con líneas repetidas, tarjetas redondeadas de 18 px dentro de otras tarjetas, la misma columna lateral para todos los roles, texto gris claro de 14 px, sombras grandes y una tipografía de sistema sin carácter. Estaba en inglés y no distinguía entre quien entrevista con un campero enfrente y quien revisa números en una reunión.

Para Emmaus el diseño tiene tres trabajos: que un voluntario sin experiencia sepa qué hacer **sin capacitación**, que un campero sienta **calma y privacidad**, y que un pastor pueda **leer la situación en un minuto**, en su computadora o en un proyector.

## Tres conceptos explorados

### A. Cuaderno de campo (elegido)

| | |
|---|---|
| **Personalidad** | La libreta de un buen líder de campamento: papel cálido, tinta verde pino, notas al margen en terracota. Cercana, ordenada, seria sin ser institucional. |
| **Tipografía** | **Literata** (TypeTogether, cofundada por un diseñador argentino; excelente en español, con figuras de altura uniforme) para títulos, preguntas y cifras grandes. **Atkinson Hyperlegible Next** (Braille Institute) para la interfaz: diseñada para distinguir letras parecidas, ideal para personas mayores o con baja visión. |
| **Color** | Papel `#F5F0E6`, hoja `#FFFDF8`, tinta `#1C2420`, pino `#1F4A3A` (acción principal), terracota `#A3461E` (marcas activas), ocre `#C98321` (solo datos "en curso"). Estados: verde `#1D6142`, ámbar `#7A5200`, rojo `#A12A1E`, azul `#2E5573`. |
| **Espaciado y densidad** | Generoso en pantallas de entrevista (una idea por pantalla); medio en tableros, con listas renglonadas en vez de tarjetas. Retícula de 4 px. |
| **Navegación** | Distinta por rol: barra superior mínima para coordinadores, con "Iniciar entrevista" siempre visible; columna lateral para tutores, pastores y administración; **sin navegación** en entrevistas y en modo campero; escenario oscuro sin menús en presentación. |
| **Formularios** | Una pregunta como título grande en Literata. Opciones como tarjetas de 64 px de alto con borde fino; al elegir, borde y fondo pino. "Prefiero no responder" separado por "o bien" y en tono más discreto. |
| **Tableros** | Informe editorial: cifras grandes en Literata, una oración que explica la cifra, renglones finos que separan secciones, sin tarjetas anidadas. |
| **Gráficas** | Una sola serie por gráfica, líneas de 2 px, barras con punta redondeada y base recta, cuadrícula fina y sólida, etiqueta solo en el último punto. La barra de avance es una "regla": completadas en pino, en curso en ocre, el resto en arena. |
| **Por qué encaja** | Transmite cuidado y confianza sin imágenes religiosas trilladas. La legibilidad de Atkinson atiende a voluntarios de todas las edades. La calidez del papel funciona bien en salones con luz natural, y la versión oscura del pino da una presentación sobria para proyector. |

### B. Señalética de campamento

| | |
|---|---|
| **Personalidad** | Letreros de parque nacional: bloques sólidos verde bosque, texto crema, letras condensadas grandes, flechas de dirección. Directa y muy visible. |
| **Tipografía** | Barlow Condensed (títulos) + Public Sans (texto). |
| **Color** | Bosque `#23412F`, crema `#F4EAD5`, mostaza `#D9A21B`, cielo `#3C6E8F`, carbón `#1E1E1E`. |
| **Espaciado y densidad** | Bloques compactos y de alto contraste; botones enormes. |
| **Navegación** | Mosaico de "señales" grandes en la página de inicio de cada rol. |
| **Formularios** | Opciones como señales de color sólido. |
| **Tableros** | Cifras en bloques de color tipo marcador deportivo. |
| **Gráficas** | Barras gruesas saturadas. |
| **Por qué no** | Excelente para la estación de entrevistas y el proyector, pero cansada y ruidosa en tableros con muchos datos. Los bloques saturados compiten con la información, y la tipografía condensada pierde legibilidad en textos largos y en pantallas pequeñas. Tomamos de aquí la claridad de "letrero": el nombre de cada computadora y los botones grandes. |

### C. Vigilia (luz de tarde)

| | |
|---|---|
| **Personalidad** | Contemplativa: piedra, luz de vela, mucho aire, serif delicada. |
| **Tipografía** | Cormorant Garamond + Source Sans 3. |
| **Color** | Piedra `#EDEAE4`, grafito `#3B3A36`, oro viejo `#B08D57`, salvia `#8A9A86`. |
| **Espaciado y densidad** | Muy baja densidad, márgenes amplios. |
| **Navegación** | Menú oculto detrás de un ícono. |
| **Formularios** | Líneas finas, sin bordes de campo. |
| **Tableros** | Pocas cifras en serif fina. |
| **Gráficas** | Trazos dorados muy finos. |
| **Por qué no** | Hermosa para la pantalla de bienvenida del campero, pero falla en lo operativo: contraste bajo, controles poco evidentes, menú escondido detrás de un ícono y una serif que se rompe en tamaños pequeños. Para voluntarios sin experiencia, "elegante pero ambiguo" es un riesgo real. Tomamos de aquí el tono sereno de las pantallas del campero. |

## Decisión

**Cuaderno de campo**, con dos préstamos: la claridad de letrero de B para las estaciones de entrevista, y la serenidad de C para la experiencia del campero.

Razones:
1. **Usabilidad primero.** Atkinson Hyperlegible Next y los tamaños grandes (texto base de 17 px, preguntas de 28 a 40 px, controles de 48 a 64 px) reducen errores de lectura y de clic.
2. **Identidad propia sin clichés.** Ni degradados morados, ni vidrio esmerilado, ni íconos decorativos, ni cruces o palomas. El símbolo de Emmaus son dos líneas que convergen hacia un horizonte (el camino a Emaús, Lucas 24) con un sol ocre.
3. **Escala de un sistema a otro.** El mismo pino funciona como botón en papel claro y como fondo de la presentación oscura; la paleta de datos se validó en ambos fondos.
4. **Contraste verificado.** Todos los colores de texto superan 4.5:1 sobre papel y sobre hoja; los bordes de controles superan 3:1.

## Principios que se derivan

- **Un rol, una forma.** El coordinador no ve analítica de administración; el campero no ve nada que no sea suyo; el pastor no proyecta su tablero, proyecta una presentación hecha para eso.
- **Una acción principal por pantalla**, a la izquierda y grande. Las secundarias van como enlaces discretos.
- **Renglones, no tarjetas.** Las listas son renglones separados por líneas finas; la "hoja" con borde solo se usa en piezas aisladas (la siguiente acción, el inicio de sesión).
- **Radios pequeños y distintos según el elemento**: 3 px en etiquetas de estado, 6 px en controles, 10 px en hojas. Nada redondeado "por defecto".
- **Movimiento mínimo**: solo transiciones de color de 160 ms; se desactivan con "reducir movimiento".
- **Texto que dice lo que pasa**: "Te faltan 6 entrevistas", no "6 pendientes"; "Se requiere seguimiento", no "Flag".
- **Siempre en español de México, tuteando.**
