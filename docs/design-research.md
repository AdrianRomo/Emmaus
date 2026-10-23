# Investigación de diseño · Emmaus Campamentos

Fecha: 23 de octubre de 2026. Alcance: patrones de interacción, no identidad visual.

## Cómo se hizo (y qué no se pudo ver)

- Las fuentes se leyeron con un lector que convierte la página en texto. **No vimos pantallas reales de ningún producto**: donde había capturas, solo leímos su texto alternativo, casi siempre vacío. Todo lo que sigue sale de documentación y guías publicadas, no de observar interfaces.
- **Mobbin y Refero no se consultaron**: ambos piden cuenta de pago para ver pantallas y no tenemos acceso. No afirmamos nada sobre ellos.
- Typeform devolvió HTTP 403 en su centro de ayuda; lo poco que se menciona viene de resultados de búsqueda y se marca como tal.
- La página "gender or sex" de GOV.UK devolvió 410 (retirada); usamos la del Home Office en su lugar.
- La lista completa de direcciones consultadas, con las que fallaron, está al final.

Flujos de Emmaus que usamos como referencia: **F1** entrevista asistida, **F2** captura supervisada (entregar y recuperar la computadora), **F3** 10 computadoras compartidas, **F4** encontrar a un campero entre 400, **F5** seguimiento del tutor, **F6** resumen del pastor y presentación, **F7** cortes cada 2 horas, **F8** edición y versiones del cuestionario.

---

## 1. Planning Center Check-Ins

Fuentes: artículos de ayuda sobre tipos de estación, bloqueo, configuración, plan de respaldo y capacitación de voluntarios; entrada de blog de 2018 sobre "station keys". La página comercial solo tiene ilustraciones.

| | |
|---|---|
| **Qué funciona y por qué** | El modo pertenece a la estación, no a la persona: *manned* (voluntario de confianza), *self* (autoservicio) y *roster* (lista). Cada computadora deja claro para qué sirve. Una estación se bloquea a un evento. La documentación dice abiertamente qué tan delicado es cada modo ("solo da acceso a voluntarios de confianza"). Tiene un plan de respaldo en papel: listas impresas por si fallan las computadoras. |
| **Flujo de Emmaus que se beneficia** | F3 (cada computadora con nombre), F2 (el modo autoservicio es el modelo del modo campero), F4 (búsqueda por nombre), F5 (pestañas de estado). |
| **Qué NO copiar** | Etiquetas impresas y códigos de recogida (no hay recogida de niños). Una sola "llave" compartida que desbloquea toda la estación: en un campamento esa clave se filtra en una hora. |
| **Cómo lo adaptamos** | Cada computadora tiene número y nombre ("Computadora 3") que el coordinador elige al entrar; en modo campero la barra superior dice "Computadora 3 · Modo campero". Para salir del modo campero se usa **la contraseña del propio coordinador**, nunca una clave compartida. "Mis camperos" usa pestañas Todos / Sin entrevista / En curso / Completada. Agregamos **"Imprimir lista de respaldo"** por coordinador. |

## 2. GOV.UK Design System (y Home Office)

Fuentes: patrones de páginas de pregunta, revisión de respuestas, confirmación, validación, resumen de errores, mensajes de error, radios, casillas, lista de tareas, información de igualdad; guía de escritura del Service Manual; patrón de sexo/género del Home Office.

| | |
|---|---|
| **Qué funciona y por qué** | Una pregunta por página, con la pregunta como encabezado. Botón "Continuar" alineado a la izquierda para que no se pierda. Campos opcionales marcados "(opcional)", nunca asteriscos. Validación en el servidor al enviar, conservando lo escrito; resumen de errores arriba con enlaces a cada campo y título que empieza con "Error: ". Casilla exclusiva ("Ninguna") después de un separador "o". Revisión de respuestas con "Cambiar" en cada fila. Página de confirmación con panel destacado y "qué sigue". Preguntas sensibles opcionales, con "Prefiero no responder" y explicación de por qué se pregunta. |
| **Flujo de Emmaus que se beneficia** | F1 y F2 (todas las pantallas de pregunta), F8 (controles del formulario), preguntas sensibles de F2. |
| **Qué NO copiar** | Su identidad (corona, tipografía Transport, colores). Sus categorías de igualdad (estándares de Inglaterra). La regla de que todas las tareas deben estar "completas" para avanzar: en el campamento las entrevistas se interrumpen. |
| **Cómo lo adaptamos** | "Continuar", "Cambiar", "Hay un problema", "Error: " en el título. Mensajes como instrucciones: "Escribe la edad del campero", "Elige una respuesta para continuar". Preguntas espirituales opcionales con "Prefiero no responder" después de un separador "o bien". Respuestas condicionales que se revelan debajo de la respuesta que las activa, solo para textos simples. Panel verde de "Entrevista completada" con el siguiente paso grande: "Continuar con otro campero". Decidimos **"tú" en toda la aplicación**: es lo natural en México para voluntarios y camperos. |

## 3. Tally (y Typeform, solo por resultados de búsqueda)

| | |
|---|---|
| **Qué funciona y por qué** | Saltos de página por pregunta o por grupo. Barra de progreso arriba, no fija; la propia guía recomienda quitarla cuando hay saltos porque puede mentir. **Sin avance automático** por omisión. Historial de versiones del formulario. (Typeform, solo por búsqueda: respuestas con teclado; reportes de que Enter elegía "Sí" por error.) |
| **Flujo de Emmaus que se beneficia** | F2 (flujo enfocado de una pregunta) y F1 (velocidad con teclado para quien entrevista todo el día). |
| **Qué NO copiar** | Avance automático (un toque accidental se salta una pregunta). Enter que elige una respuesta. Animaciones largas entre preguntas. Versiones que no quedan ligadas a cada respuesta. |
| **Cómo lo adaptamos** | Una pregunta por pantalla, opciones grandes, botón "Continuar" explícito, sin avance automático. Enter siempre significa "Continuar" (hay un botón predeterminado oculto al inicio del formulario). El progreso muestra "Pregunta 2 de 5" con un segmento por paso visible y "Sección 1 de 3", recalculado con las condiciones. |

## 4. Modos quiosco y captura en dispositivos compartidos

Fuentes: modo quiosco de SurveyMonkey, app offline de Qualtrics, código de salida y tiempo de espera de QuickTapSurvey, ODK Central y ODK Collect, XLSForm, KoboToolbox.

| | |
|---|---|
| **Qué funciona y por qué** | Quiosco en bucle con temporizador de inactividad que regresa al inicio (SurveyMonkey). Código para salir sin terminar (Qualtrics, QuickTapSurvey). Tiempo de espera recomendado de 90–120 s (QuickTapSurvey). Versiones de formulario: se prueba en borrador, al publicar las respuestas previas no cambian, y conviene no reutilizar claves (ODK Central). Cambiar etiquetas de opciones vuelve inconsistentes los datos entre versiones (KoboToolbox). |
| **Flujo de Emmaus que se beneficia** | F2 (entregar y bloquear), F3 (computadoras sin atender), F8 y F7 (cada corte debe saber la versión de cada respuesta). |
| **Qué NO copiar** | La pantalla final de SurveyMonkey que ofrece "Cerrar modo quiosco" al propio encuestado. Gestos ocultos para salir. Un tiempo de espera que tira la entrevista a medias. Sincronización offline (operamos en la red del campamento). |
| **Cómo lo adaptamos** | Al entregar la computadora, **la sesión del coordinador se cierra**; el navegador queda limitado a `/estacion/` por el servidor. El campero solo ve su nombre de pila y sus preguntas. Al terminar ve "¡Gracias! Avisa a tu coordinador" y nada más. Tras 4 minutos sin actividad aparece "¿Sigues aquí?" y al minuto siguiente la pantalla se cierra sin mostrar respuestas; lo respondido queda guardado. Elegimos 5 minutos (no 90 s) porque son preguntas de reflexión con texto libre; el servidor vence la sesión a los 10 minutos y como máximo a los 60. Cada versión publicada es inmutable y cada entrevista guarda su versión; las claves de opción se copian sin cambios al crear una versión nueva. |

## 5. Tableros operativos

Fuentes: Airtable (artículo de diseño de tableros; sus capturas no tenían texto alternativo), guía de tableros del Government Analysis Function del Reino Unido (sustituye al Performance Platform, cerrado en 2021), Stripe (navegación del panel y gráficas de inicio).

| | |
|---|---|
| **Qué funciona y por qué** | Pirámide invertida: lo más importante primero, pocas cifras arriba. Minimizar desplazamiento y clics. Mostrar siempre la última actualización, la próxima y el periodo. Cada número abre la lista que lo explica (Airtable). Separar estimaciones en vivo de reportes cerrados (Stripe). Títulos que dicen el mensaje, no solo la variable. |
| **Flujo de Emmaus que se beneficia** | F6 (resumen del pastor y presentación), F5 (vista del tutor), F7 (cortes). |
| **Qué NO copiar** | Tableros configurables por cada usuario. Selectores de periodo comparativo. Pasteles, donas y gráficas con muchas series. Color como única señal (los proyectores lavan el color). |
| **Cómo lo adaptamos** | El pastor lee un informe que responde, en orden, las cinco preguntas del brief ("¿Cómo va el campamento?", "¿Cuántas entrevistas nos faltan?", …). Línea de frescura arriba: "Última actualización 14:03 · Próximo corte: 16:00". Los cortes se rotulan "Corte guardado: no cambia" y la presentación dice "En vivo" o "Corte guardado". Cada barra lleva su cifra escrita, para no depender del color. |

---

## Los 12 patrones que adoptamos (en orden)

1. **Modo campero bloqueado y recuperación con la contraseña del coordinador.** Protege los datos de menores en computadoras compartidas.
2. **Versiones publicadas inmutables; cada entrevista guarda su versión; claves de opción estables.** Las respuestas viejas conservan su significado.
3. **Inactividad que cierra la pantalla sin perder lo respondido.**
4. **Una pregunta por pantalla, "Continuar" explícito, sin avance automático.**
5. **Revisión de respuestas antes de finalizar, con "Cambiar" en cada fila.**
6. **Errores al estilo GOV.UK:** resumen "Hay un problema", el mismo mensaje junto al campo, título "Error: ", respuestas conservadas.
7. **Controles honestos:** nada preseleccionado, "Por ahora no" o "Prefiero no responder" exclusivos, "(opcional)" en lugar de asteriscos.
8. **Preguntas sensibles opcionales con "Prefiero no responder" después de un separador.**
9. **Pestañas de estado tipo lista:** Sin entrevista / En curso / Completada.
10. **Pocas cifras arriba, en pirámide invertida, y cada una abre su lista.**
11. **Línea de frescura y cortes rotulados como fotos fijas.**
12. **Respaldo en papel e identidad de estación:** lista imprimible por coordinador y nombre visible de cada computadora.

Lo que no adoptamos de la investigación, y por qué:
- **Atajos numéricos 1–9 para opciones.** Útiles para entrevistadores expertos, pero agregan algo que explicar a voluntarios de una sola vez; se dejan como mejora futura.
- **Una "puerta" previa a las preguntas sensibles** ("¿Quieres contestar estas preguntas?"). La conversación espiritual *es* el propósito de la entrevista; en su lugar cada pregunta es opcional, ofrece "Prefiero no responder" y la pantalla de bienvenida lo explica.

## Direcciones consultadas

**Leídas (solo texto):**
- GOV.UK Design System: `/patterns/question-pages/`, `/patterns/check-answers/`, `/patterns/confirmation-pages/`, `/components/error-summary/`, `/components/error-message/`, `/patterns/validation/`, `/components/radios/`, `/components/checkboxes/`, `/components/task-list/`, `/patterns/equality-information/`.
- GOV.UK Service Manual "Writing for user interfaces"; Home Office "sex or gender".
- Planning Center: planningcenter.com/check-ins; help.planningcenter.com artículos 138181, 138184, 138213, 138214, 138221, 138287, 138309; blog 2018 "station keys".
- Tally: progress-bar, how-to-create-a-multi-step-form, conditional-form-logic, form-version-history, features.
- SurveyMonkey kiosk mode; Qualtrics Offline App; QuickTapSurvey exit code and timeout; docs.getodk.org central-forms, collect-settings, xlsform; KoboToolbox deploy.
- Airtable interface layout dashboard; Analysis Function: testing dashboards, building and managing dashboards; Stripe dashboard basics y home page charts.

**Fallidas:** GOV.UK gender-or-sex (410); varias URL sin número de Planning Center, ODK form-versioning, Airtable interface-element-number, Analysis Function data-visualisation-dashboards (404); centro de ayuda de Typeform (403).

**No consultadas:** mobbin.com y refero.design (requieren cuenta de pago).
