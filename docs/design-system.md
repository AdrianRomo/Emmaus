# Sistema de diseño · "Cuaderno de campo"

Todo vive en un solo archivo sin compilación: `static/css/emmaus.css`. Los componentes de plantilla están en `templates/partials/ui/` y `templates/events/partials/`; el comportamiento opcional, en `static/js/emmaus.js` (JavaScript sin dependencias: todo funciona también sin él). Las fuentes se sirven desde el propio servidor (`static/fonts/`, licencia OFL incluida), así que no hay dependencia de CDN.

## 1. Tokens

### Color

| Token | Valor | Uso | Contraste |
|---|---|---|---|
| `--paper` | `#F5F0E6` | Fondo de página | — |
| `--paper-2` | `#EDE6D8` | Columna lateral, paneles, filtros | — |
| `--sheet` | `#FFFDF8` | Superficies elevadas, campos | — |
| `--sunken` | `#ECE4D4` | Zonas hundidas | — |
| `--ink` | `#1C2420` | Texto principal | 13.98:1 sobre papel |
| `--ink-2` | `#48524C` | Texto secundario | 7.15:1 |
| `--ink-3` | `#5D645E` | Texto tenue (el más claro permitido) | 5.36:1 |
| `--line` | `#DDD3C1` | Separadores decorativos | — |
| `--line-strong` | `#8F8673` | Bordes de controles | 3.17:1 (WCAG 1.4.11) |
| `--pine` | `#1F4A3A` | Acción principal, selección | 9.84:1 con texto hoja |
| `--terracotta` | `#A3461E` | Marca, elemento activo, numeración | 5.36:1 |
| `--ochre` | `#C98321` | Solo datos ("en curso") | — |
| `--ok` / `--ok-tint` | `#1D6142` / `#E3EFE6` | Estado correcto | 6.25:1 sobre su tinte |
| `--warn` / `--warn-tint` | `#7A5200` / `#FBEFD3` | Atención | 6.06:1 |
| `--danger` / `--danger-tint` | `#A12A1E` / `#F9E3DE` | Error, riesgo | 5.96:1 |
| `--info` / `--info-tint` | `#2E5573` / `#E3EBF2` | Información, privacidad | 6.55:1 |

Los colores de iglesia propios (modelo `Church`) solo se aplican si la iglesia activa "Usar colores propios de la iglesia"; si no, rige esta paleta.

### Datos (validada con la prueba de seis criterios del skill de visualización)

| Rol | Claro (sobre `#FFFDF8`) | Oscuro, presentación (sobre `#13241C`) |
|---|---|---|
| Completadas | `--viz-done` `#1E7A50` | `--deck-done` `#3FA673` |
| En curso | `--viz-progress` `#C98321` | `--deck-progress` `#C4842A` |
| Sin iniciar (pista) | `--viz-track` `#E7DECD` | `--deck-track` `#24392F` |
| Medida neutra (resúmenes) | `#5B6F66` | — |
| Cuadrícula / eje | `#E4DCCD` / `#B7AD99` | `#2B4237` |

Resultados del validador: claro, ΔE CVD 11.1 (protan), visión normal 22.9, ambos ≥ 3:1 contra la superficie; oscuro, ΔE CVD 8.6, visión normal 17.4, ambos dentro de la banda de luminosidad. El color nunca es la única señal: toda barra lleva su cifra escrita y toda gráfica tiene leyenda o título que nombra la serie.

### Tipografía

| Token | Tamaño | Uso |
|---|---|---|
| `--font-display` | Literata 400–600 | Títulos, preguntas, cifras grandes (figuras de altura uniforme) |
| `--font-ui` | Atkinson Hyperlegible Next 400–700 | Todo lo demás; cifras tabulares en tablas |
| `--text-xs` | 14 px | Solo leyendas de gráficas; nunca cuerpo |
| `--text-sm` | 15 px | Metadatos |
| `--text-base` | 17 px | Cuerpo |
| `--text-md` | 19 px | Opciones de respuesta, lead |
| `--text-lg` | 22 px | Subtítulos |
| `--text-xl` | 28 px | h2 |
| `--text-2xl` | 36 px | h1 (fluido desde 28 px en móvil) |
| `--text-3xl` | 48 px | Cifras de tablero (hasta 72 px en la cifra principal) |

Interlineado 1.55 en cuerpo, 1.18 en títulos. Los meses van en minúscula ("23 de octubre"); los filtros `fecha_larga` y `fecha_corta` lo resuelven sin depender de la traducción de Django.

### Espacio, forma, sombra, foco

- Espacio en múltiplos de 4 px: `--s-1` (4) … `--s-10` (64).
- Radios distintos según el elemento: `--r-1` 3 px (etiquetas), `--r-2` 6 px (controles, opciones), `--r-3` 10 px (hojas).
- Sombras mínimas: `--shadow-1` (una línea) y `--shadow-2` (diálogos).
- Foco: contorno de 3 px en tinta **más** halo amarillo de 7 px (`--focus-halo #F2C14E`). Se ve sobre papel, hoja y botones pino (WCAG 2.4.7 y 2.4.13).
- Controles: altura mínima de 48 px (`--control-h`); opciones de respuesta de 64 px; botón principal de entrevista de 56 px; botón héroe de 68 px.
- Cortes de pantalla: 560 px (móvil), 860 px (dos columnas), 1024 px (columna lateral fija), 1180 px (ancho amplio).

## 2. Estructuras de página (una por tipo de usuario)

| Estructura | Plantilla | Para | Rasgos |
|---|---|---|---|
| Barra | `events/shell_bar.html` | Coordinador | Barra superior con cuatro pestañas, "Iniciar entrevista" siempre visible y "Cerrar sesión" a la vista; en móvil, menú de pantalla completa |
| Columna | `events/shell_rail.html` | Tutor, pastor, administración | Columna lateral con el evento activo y como máximo 7 entradas; en móvil, barra con "Menú" |
| Enfoque | `events/shell_focus.html` | Entrevista asistida | Sin navegación; barra con el nombre del campero, la computadora y "Guardar y salir" |
| Estación | `events/shell_kiosk.html` | Campero | Sin navegación ni datos de otros; "Computadora N · Modo campero" |
| Escenario | `events/presentation/deck.html` | Proyector | Fondo pino oscuro, cuadro 16:9 que escala con unidades de contenedor, controles que se ocultan |
| Pública | `base.html` sin sesión | Entrar, invitaciones | Columna angosta centrada |

## 3. Componentes

| Componente | Clases / plantilla | Notas |
|---|---|---|
| Botones | `.btn`, `--primary`, `--quiet`, `--danger`, `--sm`, `--block`, `--hero` | Un solo `--primary` por pantalla. `--hero` solo para la siguiente acción del coordinador y el final de entrevista |
| Campo de formulario | `partials/ui/field.html`, `fields.html` | Orden GOV.UK: etiqueta, ayuda, error, control. "(opcional)" en lugar de asteriscos. Radios en `fieldset` + `legend` |
| Resumen de errores | `partials/ui/error_summary.html` | "Hay un problema" con enlaces a cada campo; el título de la página empieza con "Error: "; JS mueve el foco al resumen |
| Encabezado de página | `partials/ui/page_head.html` | Línea de folio (contexto), h1, lead, acción opcional |
| Migas | `.crumbs` | Solo cuando hay jerarquía real |
| Navegación | `events/_nav.html`, `.nav-link`, `.tab-link` | `aria-current="page"` marca la sección actual con borde terracota |
| Cifras | `.figure-row`, `.figure`, `--hero` | Etiqueta arriba, cifra en Literata, nota abajo. Máximo cuatro por fila |
| Avance | etiqueta `{% meter %}` | Barra tipo regla: completadas / en curso / resto, con separación de 2 px y texto equivalente en `aria-label` |
| Barras por grupo | `events/partials/team_bars.html` | Etiqueta, porcentaje y "n de N" escritos; leyenda única |
| Gráficas | `events/charts.py`, `partials/chart_*.html` | SVG del servidor con `<title>`/`<desc>`, unidades en el subtítulo, hora de los datos, tabla alternativa y estado vacío |
| Renglones | `.ledger`, `.ledger__row` | Reemplaza las tarjetas para personas y pendientes; la fila completa es el enlace |
| Lista de resumen | `.summary-list` | Revisión de respuestas y fichas, con "Cambiar"/"Corregir" |
| Etiquetas de estado | `{% status_chip %}`, `{% follow_up_chip %}` | Siempre con texto; la forma del punto cambia (lleno o hueco) además del color |
| Avisos | `.notice`, `--success/--warning/--danger/--quiet` | Barra lateral de color + título en negritas |
| Estado vacío | `partials/ui/empty.html` | Dice qué falta y cuál es el siguiente paso |
| Diálogo modal | `<dialog class="dialog">` | Solo para "¿Sigues aquí?"; las confirmaciones destructivas son páginas propias (funcionan sin JS) |
| Confirmación | `.confirm-panel` | Panel pino con palomita: "Entrevista completada", "¡Gracias!" |
| Opciones de cuestionario | `events/interview/_question.html`, `_option.html` | Tarjetas de opción; exclusivas tras "o bien"; condicionales en `.reveal` |
| Siguiente acción | `.next-action` | "¿Qué tengo que hacer ahora?" con una respuesta y un botón |
| Persona | `.person` | Iniciales en círculo pino claro + nombre + metadato |
| Diapositivas | `.deck`, `.slide`, `.deck-*` | Ver presentación |
| Estado en vivo y carga | `partials/ui/live_status.html`, `.spinner` | "Última actualización: 14:03:21"; en rojo si falla: "Sin conexión. Los datos pueden estar desactualizados…" |
| Lista de respaldo | `events/backup_list.html` | Hoja imprimible por coordinador, sin respuestas |

## 4. Patrones

**Cuestionario.** Una pregunta por pantalla con la pregunta como h1 dentro del `legend`. Progreso "Pregunta 2 de 5" y un segmento por paso visible. Sección en itálica terracota. Las preguntas que dependen de una respuesta aparecen debajo de ella. Sin JS se muestran todas, con una nota, y el servidor descarta lo que no aplica. "Continuar" a la izquierda y "Atrás" junto a él; los dos guardan. Enter siempre continúa. En móvil, los botones quedan fijos abajo para no tener que desplazarse.

**Errores.** Validación en el servidor al enviar; nunca se borra lo escrito. Los mensajes son instrucciones: "Escribe la edad del campero", "Elige una respuesta para continuar".

**Confirmaciones.** Las acciones irreversibles (descartar entrevista, eliminar datos) van en páginas propias y explican la consecuencia. Eliminar datos pide escribir el nombre completo.

**Tiempo real.** `data-live-url` vuelve a pedir un fragmento HTML cada 20 a 60 s. No refresca mientras el foco está dentro de la región, así que los filtros, que viven fuera de ella, nunca se reinician. Si una petición falla, la línea de estado avisa en rojo con la hora de la última actualización buena.

**Computadoras compartidas.** Sin caché del navegador (`Cache-Control: no-store`). Sin datos en `localStorage`. Al salir por inactividad se cierra la sesión. El modo campero queda confinado por el servidor.

## 5. Lenguaje

- Español de México, tuteando a todos.
- Verbos claros en los botones: "Iniciar entrevista", "Continuar", "Guardar y salir", "Finalizar entrevista", "Continuar con otro campero", "Generar corte ahora", "Mostrar presentación", "Descargar PDF".
- Oraciones cortas, una idea cada una; nada de "por favor", "inválido" ni "ups".
- Palabras del campamento: campero, coordinador, tutor, cuarto, iglesia o grupo, corte de seguimiento.
- Nunca se califica a las personas: "lo que el campero quiso compartir", nunca "diagnóstico" ni "riesgo espiritual".

## 6. Accesibilidad (objetivo WCAG 2.2 AA)

- Enlace "Saltar al contenido" en todas las páginas; un solo h1; regiones con `aria-labelledby`.
- Todo control tiene etiqueta visible; la prueba `events/tests/test_ui.py` lo verifica en las pantallas principales y que no quede texto en inglés.
- Contraste verificado (ver tabla); foco visible y grueso; objetivos de 48 px o más (2.5.8).
- Las gráficas tienen texto alternativo y tabla; el color nunca es la única señal.
- `prefers-reduced-motion` anula transiciones; `forced-colors` mantiene barras y selección visibles.
- Formato de fecha y hora de México, con hora de 24 h.

## 7. Cómo extenderlo

1. Usa los tokens; si necesitas un color nuevo, agrégalo a `:root` con su contraste documentado aquí.
2. Antes de crear un componente, busca uno en esta tabla. Si una pantalla necesita tarjetas dentro de tarjetas, probablemente necesita renglones.
3. Toda gráfica nueva va en `events/charts.py`: una serie, un eje, título en español, unidad, hora de los datos, `<title>`/`<desc>`, tabla y estado vacío.
4. Todo texto nuevo pasa la prueba de inglés de `test_ui.py`.
