# Plan de rediseño UX · Emmaus Campamentos

Estado al 23 de octubre de 2026, rama `feat/campamentos-redesign`. Este documento registra la auditoría de la aplicación existente, las decisiones tomadas y el avance por fases.

## 1. Auditoría de la aplicación existente (commit `d1d1312`)

| Área | Lo que había |
|---|---|
| Estructura | Monolito Django 5.2 con una sola app, `core`: discipulado (rutas, lecciones, preguntas de reflexión, preguntas de participantes, seguimientos, notas pastorales, borradores con IA opcionales) |
| Modelos | `Church`, `UserChurchRole` (un rol global por iglesia: church_admin, pastor, mentor, participant), `Invitation`, `ParticipantProfile` (participantes **con cuenta**), `DiscipleshipPath`, `Lesson`, `ReflectionQuestion`, `Enrollment`, `LessonProgress`, `ReflectionAnswer`, `ParticipantQuestion`, `QuestionResponse`, `FollowUpRequest`, `PastoralNote`, `AIDraft` |
| Autenticación | Login de Django; invitaciones con token de un solo uso |
| Permisos | `core/access.py`, que asume un rol por usuario en una iglesia; no existía el concepto de evento |
| Eventos / campamentos | No existían. Tampoco camperos sin cuenta, cuestionarios versionados, estaciones ni reportes periódicos |
| Plantillas | 28 plantillas en inglés; utilidades al estilo Tailwind escritas a mano en `static/css/emmaus.css` (sin CDN) |
| JavaScript / HTMX | Ninguno |
| Gráficas | Ninguna |
| Docker | `docker-compose.yml` (desarrollo con runserver) y `docker-compose.prod.yml` (gunicorn); `collectstatic` en la imagen sin `DEBUG=false` y sin servidor de estáticos (por eso el sandbox corre con `DEBUG=true` y runserver) |
| Pruebas | 21 pruebas en `core/tests.py`, todas pasando sobre PostgreSQL |
| Producción | `SECRET_KEY` obligatorio, cookies seguras si `DEBUG=false`; datos de demostración con contraseña fija `password12345` (incluida `participant@example.org`) |

### Problemas de UX encontrados

1. Todo en inglés, incluidos mensajes y correos.
2. Misma estructura para todos los roles: columna lateral con hasta 8 entradas, tarjetas dentro de tarjetas, texto de 14 px gris claro.
3. Estilo genérico: degradados de fondo, retícula decorativa, radios de 18 px por todas partes, sombras grandes.
4. Sin estados vacíos útiles ni resumen de errores; errores sin relación visual con el campo.
5. Los correos de notificación incluían el texto escrito por el participante (pregunta, motivo de seguimiento): un riesgo de privacidad.
6. Nada pensado para computadoras compartidas: sin cierre por inactividad, sin `no-store` y sin modo restringido.

### Limitaciones de arquitectura

- Un rol global por usuario, sin alcance por evento. Hacía imposible que una persona sea tutora en un campamento y coordinadora en otro.
- Los participantes requieren cuenta y correo: no sirve para 400 camperos menores de edad.
- Sin noción de cuestionario versionado ni de respuestas estructuradas (solo texto libre por lección).
- Sin tablas de auditoría ni de reportes.
- Estáticos rotos con `DEBUG=false`.

## 2. Qué se reutilizó, adaptó o retiró

| Elemento | Decisión |
|---|---|
| `Church`, `UserChurchRole` | **Reutilizados**: la iglesia es el contenedor de eventos y cuestionarios; church_admin = administrador de todos sus eventos; pastor de iglesia = pastor en todos sus eventos |
| Módulo de discipulado completo | **Conservado y traducido**. Se muestra solo si la iglesia activa "Usar el módulo de discipulado" (la migración 0004 lo activa para iglesias que ya tenían rutas) |
| `FollowUpRequest` | Se conserva para discipulado; los campamentos usan `FollowUp` porque sus personas no tienen cuenta |
| Colores por iglesia | Se conservan, pero solo se aplican con "Usar colores propios de la iglesia" |
| `participant@example.org` | **Desactivado** por la migración 0004; `seed_dev_data` solo corre con `DEBUG=true` y genera contraseña aleatoria |
| Utilidades CSS tipo Tailwind | **Retiradas**; reemplazadas por el sistema de componentes |
| Correos con contenido del participante | **Retirados**; ahora son avisos genéricos sin nombres ni texto |

## 3. Nueva arquitectura de información

| Rol | Navegación | Pantalla principal | Pregunta que responde |
|---|---|---|---|
| Coordinador | Mi inicio · Mis camperos · Entrevistas pendientes · Mi avance, y el botón "Iniciar entrevista" siempre visible | Mi inicio | ¿Qué tengo que hacer ahora? |
| Campero | Ninguna | Bienvenida → una pregunta por pantalla → revisión → gracias | ¿Qué me preguntan y quién lo verá? |
| Tutor | Mi equipo · Coordinadores · Entrevistas · Pendientes · Reportes | Mi equipo | ¿Quién de mis coordinadores necesita ayuda? |
| Pastor | Resumen general · Avance del campamento · Seguimiento · Reportes · Equipo | Resumen general | ¿Cómo va el campamento? |
| Administración | Inicio · Campamentos · Participantes · Equipo · Cuestionarios · Reportes · Configuración | Inicio | ¿Qué requiere mi atención? |
| Proyector | Ninguna | Modo presentación (5 diapositivas) | ¿Cómo vamos, sin exponer a nadie? |

Las acciones a las que un rol no tiene acceso no se muestran, y además el servidor las rechaza.

## 4. Decisiones de diseño

Detalle en `docs/design-direction.md` y `docs/design-system.md`. En resumen: dirección "Cuaderno de campo" (papel cálido, tinta pino, Literata + Atkinson Hyperlegible Next); una estructura de página distinta por tipo de usuario; renglones en vez de tarjetas; una acción principal por pantalla; gráficas del servidor con texto alternativo; JavaScript mínimo y opcional.

## 5. Cambios al modelo de datos (app `events`)

`Event`, `EventMembership` (un rol por usuario por evento, con `can_view_sensitive` explícito), `TutorAssignment` (tutor ↔ coordinador, uno a uno por coordinador), `InterviewStation`, `EventParticipant` (campero sin cuenta: número estable por evento, nombre, edad, cuarto, iglesia o grupo, coordinador, estado, auditoría de creación y modificación), `ConsentRecord`, `Questionnaire` → `QuestionnaireVersion` → `Section` → `Question` → `QuestionOption` (inmutables al publicarse), `Interview` (una viva por campero, reclamo con token y revisión optimista), `Answer` (opciones como relación M2M + texto), `AnswerRevision` (historial de correcciones), `ParticipantSession` (captura supervisada), `FollowUp` (seguimiento y situaciones de riesgo), `ReportSnapshot` (cortes inmutables), `AuditEvent`. Hay índices para cada filtro de los tableros. Detalle en `docs/architecture.md`.

## 6. Estrategia de migración

- Todas las migraciones son **aditivas**: ninguna tabla existente se borra ni cambia de significado.
- `core.0003` agrega `discipleship_enabled` y `custom_theme_enabled` a `Church` y traduce etiquetas.
- `core.0004` (datos) activa discipulado para iglesias con rutas existentes y desactiva `participant@example.org`.
- `events.0001` crea el módulo de campamentos completo.
- Los usuarios existentes conservan su rol de iglesia: church_admin entra a la administración de campamentos y pastor a sus resúmenes, sin pasos manuales.
- Reversión: la imagen anterior funciona con la base migrada (solo ignora tablas nuevas). Revertir `events` a `zero` **borra** los datos de campamentos, por lo que se debe restaurar un respaldo en lugar de migrar hacia atrás (ver `docs/deployment.md`).

## 7. Fases

| Fase | Estado | Evidencia |
|---|---|---|
| 1. Auditoría | Hecha | Esta sección; `docs/design-research.md` |
| 2. Modelo de dominio | Hecha | `events/models.py`, `events/access.py`, pruebas en `test_access.py` |
| 3. Sistema de diseño | Hecha | `static/css/emmaus.css`, `docs/design-system.md` |
| 4. Entrevistas en 10 estaciones | Hecha | `events/services/interviews.py`, `test_interviews.py`, `test_concurrency.py`, prueba de carga |
| 5. Cuestionarios | Hecha | Cuestionario inicial, condiciones, versiones, editor de borradores; `test_questionnaire.py` |
| 6. Tableros por rol | Hecha | Coordinador, tutor, pastor, administración |
| 7. Datos en vivo | Hecha | Consulta periódica de fragmentos, indicador de datos viejos |
| 8. Presentación y PDF | Hecha | `services/presentation.py`, `services/pdf.py` |
| 9. Cortes automáticos | Hecha | `run_report_cutoffs --loop 300`, servicio `scheduler` |
| 10. Endurecimiento | Hecha, con revisión adversarial | `docs/privacy-and-safeguarding.md`, encabezados, límites de intentos |
| 11. Pulido visual | Hecha (3 rondas) | `docs/ux-review.md` |
| 12. Documentación | Hecha | Carpeta `docs/` |

## 8. Pendiente antes de usarlo con el campamento real

Ver la sección final de `docs/ux-review.md` y la lista de verificación de `docs/privacy-and-safeguarding.md`. Lo principal:
- La iglesia debe aprobar sus procedimientos de autorización y protección; la aplicación no deja entrevistar hasta que la administración lo confirma.
- Prueba con voluntarios reales en una computadora del campamento: la aplicación se probó con datos sintéticos y navegadores automatizados.
- Desplegar detrás de HTTPS, con respaldos de la base de datos.
