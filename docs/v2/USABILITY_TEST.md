# Usability test: five tasks, five people, one afternoon

Automated checks and review agents are not user research. This is the plan for a real test the
church can run in an afternoon; nothing in this repository claims it has been done.

## Who

Five people who have never used Emmaus: two members over 55, one member under 25, one person who
guides or would guide a small group, one teenager who served or could serve as a camp coordinator.
At least three on their own phone.

## Setup

- The sandbox (synthetic data), never production. One account per role from the demo seed.
- One facilitator, one note-taker. Ten minutes per task at most. Say: "We are testing Emmaus, not you.
  Think aloud. I will not help unless you are stuck for a minute."
- Record per task: success (yes / with help / no), time, wrong turns, words they used for things, and
  any moment of doubt ("¿ya quedó?", "¿dónde…?").

## Tasks (read them aloud as written; never name the screen or the button)

1. **Servir.** "Te pidieron ayudar a recibir a la gente el lunes 23. Diles que sí puedes. Luego
   imagina que te surgió algo: cambia tu respuesta."
   Success: answers from the home, sees the confirmation, changes it without leaving the home.
2. **Evento.** "Este fin de semana hay una convivencia de jóvenes. Apúntate. ¿Cómo sabes que quedó?"
   Success: signs up in under a minute; can say where it shows that it worked.
3. **Grupo.** "Quieres empezar a ir a un grupo los jueves. Pide entrar." Then, as the guide: "Alguien
   pidió entrar a tu grupo. Respóndele."
   Success: finds Grupos, requests, sees the request's state; the guide finds and decides it from the home.
4. **Entrevista (coordinador).** "Dejaste una entrevista a medias ayer. Termínala." Then: "¿Hay algo
   que tengas que decidir? ¿Por qué no puedes entrevistar a uno de tus camperos?"
   Success: resumes from the home and lands where it stopped; names the missing authorization.
5. **Camperos (administración).** "Encuentra a Camila Herrera. ¿Qué cabaña tiene y quién la
   coordina? Luego: ¿cuántos camperos no tienen coordinador? Asígnale uno a dos de ellos."
   Success: finds by name; reads cabin and coordinator; filters; assigns.

## After each person (two questions)

- "¿Qué fue lo más fácil y lo más confuso?"
- "Si Emmaus fuera una persona de tu iglesia, ¿cómo la describirías?"

## What to do with it

Any task that fails for two of five people is a defect to fix before release. Write the findings as a
GitHub issue with rounded numbers only (the repository is public: no names, no church, no answers).
