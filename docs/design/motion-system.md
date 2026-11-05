# Sistema de movimiento · Atrio

Cómo se mueve Emmaus y por qué. Complementa `docs/design-system.md` (§Movimiento, que resume las reglas) con el detalle de cada patrón: cuándo sí, cuándo no, y cómo se escribe. Código: `static/css/rotulo.css` (§13 "Motion" y §14 "Reduced motion"), `static/js/emmaus.js` (regiones vivas, el panel que se guarda, el menú), y las pruebas de `events/tests/test_rotulo.py` (`AtrioMotionTests`).

**Filosofía.** El movimiento explica un cambio o confirma una acción; nunca decora. Toda interacción se siente viva, pero nada distrae. Cada regla responde a una de cinco frases: "esto es nuevo", "esto es tuyo", "esto está cargando", "esto cambió", "esto ya se fue". Si una animación no dice ninguna de las cinco, no va.

## 1. Tokens

Viven en `:root`, al inicio de `rotulo.css`. No hay duraciones literales en ninguna regla: `test_motion_uses_the_tokens` lo verifica.

| Token | Valor | Para qué |
|---|---|---|
| `--dur-1` | 120 ms | Microinteracciones: color, foco, subrayado, la presión de un botón, el fundido entre páginas |
| `--dur-2` | 200 ms | Transiciones de componente: la pregunta que cambia, los avisos, los plegables, la marca elegida, el menú, la bienvenida que se guarda, una pregunta que se revela |
| `--dur-3` | 320 ms | Lo que llega con la página: el panel de lo que sigue, el encabezado, la bienvenida, las diapositivas, una cifra que cambió |
| `--ease` | `cubic-bezier(.2,.7,.2,1)` | Una sola curva: arranca rápido y llega despacio (ease-out). Sirve para entradas, salidas y cambios de estado |
| `--rise` | 6 px | La subida de lo que aparece y la bajada de lo que se va. Nunca más |

Por qué una sola curva: una entrada que desacelera se lee como "ya llegó"; la misma curva en reversa (salida) se lee como "se fue". No hay resortes ni rebotes: nada en Emmaus es juguetón, y un resorte en un producto de iglesia que guarda respuestas íntimas se sentiría fuera de lugar.

Las duraciones están en el tramo corto de lo recomendado (100 a 180 ms microinteracciones, 180 a 280 ms componentes, 250 a 400 ms páginas). Se probaron a 160/280/400 y la página se sentía lenta de responder; a 120/200/320 se nota el cambio sin esperar.

## 2. Los fotogramas

Seis `@keyframes`, con prefijo `atrio-`. `test_every_animation_names_a_defined_keyframes` impide usar uno que no exista.

| Nombre | Qué hace | Lo usan |
|---|---|---|
| `atrio-rise` | De opacidad 0 y 6 px abajo a su lugar | Lo que llega: panel, encabezado, bienvenida, menú, pregunta nueva, pregunta revelada |
| `atrio-leave` | A opacidad 0 y 6 px arriba | Lo que se va: la pregunta anterior, la bienvenida guardada |
| `atrio-drop` | De opacidad 0 y 6 px arriba a su lugar | Los avisos (`.notice`), que caen desde arriba |
| `atrio-fade` | De opacidad 0 | El contenido de un plegable, el segmento hecho de una regla, una cifra que cambió |
| `atrio-fill` | Del color de "sin iniciar" al color de "hecho" | Las marcas completadas del conteo y los "Listo" de la bienvenida |
| `atrio-pop` | A escala 1.06 y de vuelta | La marca de la opción elegida |

Solo `transform` y `opacity` (y, en `atrio-fill`, dos colores de una marca de 10 × 16 px). Ninguna regla anima `height`, `width`, `margin` ni `top`: no hay reflujo, no hay saltos de contenido.

## 3. Las trece reglas

Cada una dice qué frase responde, cuándo aplica y cuándo no.

### 1. Entre páginas ("esto es nuevo")
`@view-transition { navigation: auto }`: la página entera se funde en `--dur-1` (120 ms, apenas visible: una página nunca debe sentirse lenta). En la entrevista, la pregunta (`.step > .question`, la única con `view-transition-name`) sale hacia arriba y la siguiente sube a su lugar en `--dur-2`.
**No** se nombra ninguna otra pieza: un nombre repetido en la página rompe la transición, y las preguntas que se revelan también son `.question`. Los navegadores sin transiciones entre documentos simplemente navegan.

### 2. Lo que llega con la página ("esto es nuevo")
`.sign`, `.page-head` y `.welcome` suben 6 px con fundido en `--dur-3`.
**No** dentro de una región viva que ya se actualizó (`[data-live-settled]` lo apaga): una actualización no es una llegada.

### 3. Las marcas que se llenan ("esto es tuyo")
Las marcas completadas del conteo (`.tally__mark--done`) pasan de contorno a petróleo en cascada: 8 ms entre marcas, 400 ms a lo mucho (`sibling-index()`; donde no existe, todas a la vez). Los "Listo" de la bienvenida se llenan igual: es el mismo dato ordinal.
**No** al refrescar la región viva, nunca en bucle, y nunca con más de 400 ms de espera: con 350 camperos la última marca no puede llegar un segundo después.

### 4. La región viva ("esto está cargando")
Mientras la consulta está en vuelo, la región baja a 0.72 de opacidad, pero solo si tarda más de `--dur-2` (`transition-delay`): una respuesta rápida no parpadea. Vuelve a 1 de inmediato.
**No** hay rueditas ni esqueletos: los datos de antes siguen a la vista y son válidos; la línea "Última actualización" dice de cuándo son.

### 5. Controles ("esto responde")
Botones, enlaces, opciones y foco cambian de color en `--dur-1`. Al elegir una opción, su marca hace `atrio-pop`. Y la regla 13: al presionar, el botón baja 1 px.
**No** se anima el borde del foco al moverse: el anillo aparece donde está el foco, sin viajar.

### 6. Plegables ("esto es nuevo")
El contenido de un `<details>` que se abre se funde en `--dur-2`. **No** se anima la altura: el navegador la calcula al instante y el contenido aparece ya en su lugar.

### 7. Avisos ("esto es nuevo")
`.notice` cae desde arriba 6 px con fundido en `--dur-2`. **No** se retiran solos ni se mueven después: un aviso que desaparece sin que lo pidas es un aviso que alguien no leyó.

### 8. La presentación ("esto cambió")
Una diapositiva se funde con la siguiente en `--dur-3`; Cal y Noche cambian de color en `--dur-3`. **No** hay desplazamiento lateral: en un proyector un barrido de 1920 px marea.

### 9. La bienvenida que se guarda ("esto ya se fue")
Al tocar "Entendido", `emmaus.js` manda el POST por `fetch`; con el 204 del servidor, `.welcome.is-leaving` sube 6 px y se funde en `--dur-2`, el bloque sale del documento, el foco pasa a `<main>` y una línea de estado dice que se puede volver a leer desde "Mi cuenta". Si el `fetch` falla, el formulario se envía como siempre.
**No** se anima lo que queda debajo: el hueco se cierra cuando el bloque ya no está, así nada se mueve mientras todavía se lee. Con movimiento reducido, el bloque simplemente se quita.

### 10. El menú ("esto es nuevo")
"Menú" abre con `atrio-rise` en `--dur-2`. **No** se anima el cierre: la página de atrás nunca se fue, y una salida animada haría esperar para seguir leyendo.

### 11. La pregunta que se revela ("esto es nuevo")
Una pregunta condicional (`.reveal`) que una opción destapa sube a su lugar en `--dur-2`. **No** se anima la altura (el formulario se acomoda de inmediato), y no vuelve a animarse dentro de una región viva ya actualizada.

### 12. Una cifra que cambió ("esto cambió")
Al refrescar una región viva, `emmaus.js` compara el texto de cada `[data-count]` con el que reemplaza y marca los que difieren (`.is-changed`): se funden de nuevo en `--dur-3`. Lo llevan la frase "Llevas X de Y", "Tu equipo lleva…", "Llevamos…" y las cifras del inicio de administración.
**No** se cuenta de un número a otro (un contador que corre de 18 a 19 no dice nada que "19" no diga) y **no** se marca lo que no cambió.

### 13. La presión ("esto responde")
`.btn:active` baja 1 px (`transform: translateY(1px)`, transición de `--dur-1`). **No** cambia de tamaño ni de sombra: no hay sombras en Atrio.

## 4. Lo que no se anima, y por qué

- **Los estados vacíos.** No llevan ilustración ni movimiento: una frase que dice qué pasa y quién actúa vale más que un dibujo que respira.
- **Las tablas y los filtros.** Cambian de página o de dirección con una navegación normal (regla 1). Un reordenamiento animado de 400 filas cuesta más de lo que explica.
- **Las gráficas.** Son SVG del servidor; se funde el segmento hecho de la regla (regla 3) y nada más. Una línea que se dibuja sola es un adorno.
- **La regla de avance al refrescar.** Los segmentos se reconstruyen con cada actualización; animar su anchura exigiría comparar el DOM anterior. La regla 12 marca la cifra que cambió, que es lo que la persona lee.
- **Celebraciones.** "¡Terminaste!" en el inicio del coordinador y la confirmación de la entrevista son texto y un panel, sin confeti ni destellos. Terminar una entrevista con un joven es rutina, no un logro de videojuego.
- **Cargas con esqueleto.** Las páginas llegan completas del servidor; las regiones vivas conservan lo anterior mientras consultan (regla 4).

## 5. Movimiento reducido

`@media (prefers-reduced-motion: reduce)` apaga **todo**: `@view-transition { navigation: none }`, `animation: none !important` y `transition: none !important` en todo elemento y pseudoelemento, incluidos los de la transición de vista. `emmaus.js` consulta la misma preferencia antes de esperar un `animationend` (regla 9): con movimiento reducido, el bloque se quita sin esperar. `test_reduced_motion_turns_every_animation_and_transition_off` comprueba que cada selector animado queda cubierto.

## 6. Cómo agregar movimiento

1. Di cuál de las cinco frases responde. Si ninguna, no lo agregues.
2. Usa un fotograma existente con un token de duración. Un fotograma nuevo necesita una frase nueva, y la lista de arriba.
3. Anima solo `transform` y `opacity`.
4. Si vive dentro de una región viva, agrégalo a la lista de `[data-live-settled]` que apaga las llegadas, salvo que sea la regla 12.
5. Corre `events/tests/test_rotulo.py`: tokens, fotogramas definidos, cobertura de movimiento reducido.
6. Míralo en el navegador a 390, 820 y 1440 px, y con "Emular prefers-reduced-motion" encendido. Toca el control cinco veces seguidas: nada debe encimarse ni quedar a medias.
7. Documenta la regla aquí y resume el cambio en `docs/design-system.md`, §Movimiento.
