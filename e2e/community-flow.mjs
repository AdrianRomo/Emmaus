// The camp-to-group journey in a browser, on synthetic data (seed_community_demo): a former campero signs in on a
// phone, finds a group and asks to join; the guide accepts; she sees her group and its next meeting. Then every
// community and people screen per role at phone, tablet and desktop widths, with axe, overflow, h1 and JS-error checks,
// and the redesigned shell: the rail on wide screens, the tabs and the "Más" panel on a phone, by keyboard too.
// Usage: AXE=node_modules/axe-core/axe.min.js EMMAUS_URL=http://127.0.0.1:8000 EMMAUS_PASSWORD=... node community-flow.mjs <outdir>
import { chromium } from "playwright";
import fs from "node:fs";

const out = process.argv[2] || "community-out";
const BASE = process.env.EMMAUS_URL || "http://127.0.0.1:8000";
const PASS = process.env.EMMAUS_PASSWORD || "DemoCamp-2026";
const AXE = fs.readFileSync(process.env.AXE || "node_modules/axe-core/axe.min.js", "utf8");
const BENIGN_JS_ERROR = /InvalidStateError: Transition was aborted because of invalid state/; // issue #16
const PHONE = { width: 390, height: 844 };
const TABLET = { width: 768, height: 1024 };
const DESKTOP = { width: 1440, height: 900 };
fs.mkdirSync(out, { recursive: true });
const problems = [];
let checks = 0;

const browser = await chromium.launch();

async function session(email, viewport) {
  const ctx = await browser.newContext({ viewport, locale: "es-MX", timezoneId: "America/Mexico_City", bypassCSP: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => { if (!BENIGN_JS_ERROR.test(String(e))) problems.push(`${email}: JS error ${e}`); });
  await page.goto(BASE + "/cuenta/entrar/");
  await page.fill("input[name=username]", email);
  await page.fill("input[name=password]", PASS);
  await Promise.all([page.waitForNavigation(), page.click("form button:has-text('Entrar')")]);
  return { ctx, page };
}

async function audit(page, name) {
  for (const [label, vp] of [["movil", PHONE], ["tableta", TABLET], ["escritorio", DESKTOP]]) {
    await page.setViewportSize(vp);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${out}/${name}-${label}.png`, fullPage: true });
    await page.addScriptTag({ content: AXE });
    const info = await page.evaluate(async () => {
      const r = await axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] });
      const small = [...document.querySelectorAll("main a.btn, main button, .tabs__item, .rail__link, .rail__who, .rail__account, .row__contact a")].filter((el) => {
        const box = el.getBoundingClientRect();
        return box.width > 0 && box.height < 44;
      }).map((el) => el.textContent.trim().slice(0, 30));
      return {
        violations: r.violations.map((v) => `${v.id} (${v.nodes.length}): ${v.nodes[0].target.join(" ")}`),
        overflow: document.documentElement.scrollWidth - window.innerWidth,
        h1: document.querySelectorAll("h1").length,
        small,
      };
    });
    checks += 1;
    for (const v of info.violations) problems.push(`${name} ${label}: axe ${v}`);
    if (info.overflow > 0) problems.push(`${name} ${label}: horizontal overflow ${info.overflow}px`);
    if (info.h1 !== 1) problems.push(`${name} ${label}: ${info.h1} h1`);
    if (info.small.length) problems.push(`${name} ${label}: targets under 44px: ${info.small.join(" | ")}`);
  }
}

function expect(condition, message) {
  checks += 1;
  if (!condition) problems.push(message);
}

// 1. The former campero, on a phone: home, groups, ask to join.
let { ctx, page } = await session("valeria.soto@demo.invalid", PHONE);
expect(new URL(page.url()).pathname === "/mi-iglesia/", `member lands on ${page.url()}, not on /mi-iglesia/`);
expect(await page.locator("h1").innerText() === "Hola, Valeria.", "home greeting");
expect(await page.getByText("Campamento de Jóvenes 2025").count() > 0, "home shows the camp she was part of");
await audit(page, "miembro-inicio");
await page.setViewportSize(PHONE);
// The shell on a phone: no rail links, the tabs, and "Más" as a dialog that the keyboard can open and leave.
expect(await page.locator(".rail__nav").isHidden(), "the rail's links are hidden on a phone");
expect((await page.locator(".tabs__text").allInnerTexts()).join("|") === "Mi iglesia|Eventos|Grupos|Más", "a member's tabs");
expect(await page.locator(".tabs__item[aria-current=page]").innerText() === "Mi iglesia", "the tab of the current place is marked");
await page.keyboard.press("Tab");
expect(await page.evaluate(() => document.activeElement.className) === "skip-link", "the first Tab reaches the skip link");
await page.locator(".tabs button[data-menu-open]").focus();
await page.keyboard.press("Enter");
expect(await page.locator("#menu-panel").isVisible(), "Enter on Más opens the panel");
expect(await page.evaluate(() => document.getElementById("menu-panel").contains(document.activeElement)), "focus moves into the panel");
expect(await page.locator("#menu-panel").getByText("Cerrar sesión").count() === 1, "the panel holds the logout");
await page.keyboard.press("Escape");
expect(await page.locator("#menu-panel").isHidden(), "Escape closes the panel");
expect(await page.evaluate(() => document.activeElement.hasAttribute("data-menu-open")), "focus returns to Más");
const gap = await page.evaluate(() => {
  window.scrollTo(0, document.body.scrollHeight);
  return document.querySelector(".tabs").getBoundingClientRect().top - document.querySelector("main").lastElementChild.getBoundingClientRect().bottom;
});
expect(gap >= 0, `the tabs cover the end of the page by ${-gap}px`);
await page.evaluate(() => window.scrollTo(0, 0));
// A notice for the whole church: new on the home, read once opened.
expect(await page.getByText("El domingo empezamos a las 11").count() > 0, "the home shows the church's notice");
await Promise.all([page.waitForNavigation(), page.click(".row__title a:has-text('El domingo empezamos a las 11')")]);
await audit(page, "miembro-aviso");
await page.goto(BASE + "/mi-iglesia/avisos/");
expect(await page.locator("main").getByText("Nuevo", { exact: true }).count() === 0, "an opened notice is no longer new");
await audit(page, "miembro-avisos");
// The devotional of the day: she reads it and says so; the mark is hers alone.
await page.goto(BASE + "/mi-iglesia/");
await Promise.all([page.waitForNavigation(), page.click(".meeting__title a:has-text('Permanecer')")]);
await audit(page, "miembro-devocional");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Ya lo leí')")]);
expect(await page.locator("main").getByText("Ya lo leíste.").count() > 0, "reading the devotional is confirmed");
await page.goto(BASE + "/mi-iglesia/recursos/");
expect(await page.locator("main a[href^='https://www.youtube.com']").getAttribute("rel") === "noopener noreferrer", "a resource opens on the other site, without a referrer");
await audit(page, "miembro-recursos");
// Reading plans: the plain seed has none, so this is the page that says so (the plan itself comes with --eventos).
await page.goto(BASE + "/mi-iglesia/recursos/planes/");
expect(await page.locator("main").getByText("todavía no publica planes de lectura").count() > 0, "the reading plans say there is none yet");
await audit(page, "miembro-planes");
// Serving: her team asked her for a date; she confirms it from the phone.
await page.goto(BASE + "/mi-iglesia/");
expect(await page.getByText("Tus fechas para servir").count() > 0, "the home shows the date her team asked for");
await page.goto(BASE + "/mi-iglesia/servir/");
await audit(page, "miembro-servir");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Confirmar')")]);
expect(await page.locator("main").getByText("Confirmaste").count() > 0, "confirming a date is said back");
expect((await page.goto(BASE + "/mi-iglesia/servir/equipos/1/")).status() === 403, "who serves cannot open the team's page");
expect((await page.goto(BASE + "/mi-iglesia/acompanar/1/")).status() === 403, "a follow-up is closed to who does not own it");
// An event the church published: she signs up from the phone.
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click(".tabs a:has-text('Eventos')")]);
await audit(page, "miembro-eventos");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click(".row__title a:has-text('Noche de alabanza')")]);
await audit(page, "miembro-evento");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Inscribirme')")]);
expect(await page.locator("main").getByText("Te inscribiste.").count() > 0, "signing up is confirmed on the page");
await audit(page, "miembro-evento-inscrita");
await page.goto(BASE + "/mi-iglesia/");
expect(await page.locator(".dates").getByText("Te inscribiste").count() > 0, "the home says she signed up");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click(".sign a:has-text('Ver los grupos')")]);
await audit(page, "miembro-grupos");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click(".row__title a:has-text('Jóvenes Roca')")]);
expect(await page.getByText("Salón 2 de la iglesia").count() === 0, "the meeting place is hidden from a non-member");
await audit(page, "miembro-grupo-antes");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Pedir unirme')")]);
expect(await page.getByText("Ya pediste unirte.").count() > 0, "request confirmed on screen");
expect(await page.getByText("le avisamos por correo").count() === 0, "no e-mail is claimed when none can be sent");
await audit(page, "miembro-grupo-pedido");
for (const path of ["/mi-iglesia/personas/", "/mi-iglesia/grupos/administrar/", "/mi-iglesia/eventos/administrar/", "/mi-iglesia/eventos/noche-de-alabanza-demo/inscritos/"]) {
  const response = await page.goto(BASE + path);
  expect(response.status() === 403, `member got ${response.status()} on ${path}`);
}
await ctx.close();

// 2. The guide: the panel names the pending requests; accept Valeria.
({ ctx, page } = await session("guia.grupo@demo.invalid", DESKTOP));
expect((await page.locator(".sign__title").innerText()).startsWith("¿Recibes a 2 personas"), "guide's next step");
expect(await page.locator(".rail__nav").isVisible() && await page.locator(".tabs").isHidden(), "wide screens get the rail, not the tabs");
expect((await page.locator(".rail .rail__link:has-text('Jóvenes Roca') .badge").textContent()).trim().startsWith("2"), "the rail counts what waits in her group");
await audit(page, "guia-inicio");
await Promise.all([page.waitForNavigation(), page.click("a:has-text('Ver solicitudes')")]);
await audit(page, "guia-personas");
await Promise.all([page.waitForNavigation(), page.click("li:has-text('Valeria Soto') button[value=aceptar]")]);
expect(await page.getByText("Valeria Soto Marín ya es parte de Jóvenes Roca.").count() > 0, "acceptance message");
// He coordinates a team and was asked to accompany somebody: both are under "A tu cargo".
await page.goto(BASE + "/mi-iglesia/servir/equipos/1/");
expect(await page.locator("main").getByText("1 de 1 confirmaron").count() > 0, "the coordinator reads who confirmed");
await audit(page, "guia-equipo");
await page.goto(BASE + "/mi-iglesia/acompanar/");
await audit(page, "guia-acompanar");
await Promise.all([page.waitForNavigation(), page.click("main a:has-text('Emilio Vargas')")]);
await audit(page, "guia-seguimiento");
await page.locator("label:has-text('Le llamé')").click();
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Guardar lo que hice')")]);
expect(await page.locator("main").getByText("En proceso").count() > 0, "the first step moves the follow-up to «En proceso»");
await page.goto(BASE + "/mi-iglesia/grupos/1/personas/");
// The guide's other view of the work: meetings. He passes the list of a past day.
await Promise.all([page.waitForNavigation(), page.click(".filters a:has-text('Reuniones')")]);
await audit(page, "guia-reuniones");
const past = new Date(Date.now() - 2 * 86400000).toISOString().slice(0, 10);
await page.fill("input[name=fecha]", past);
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Elegir este día')")]);
await audit(page, "guia-pasar-lista");
await page.locator("label:has-text('Valeria Soto Marín')").click();
expect(await page.locator("input[name=vino]:checked").count() === 1, "one person marked");
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Guardar la lista')")]);
expect(await page.getByText("vino 1 persona de 2.").count() > 0, "the list is saved and says how many came");
expect(await page.getByText("Vino 1 de 2").count() > 0, "the meeting appears with its count");
expect((await page.goto(BASE + "/mi-iglesia/personas/")).status() === 403, "guide cannot open the directory");
await ctx.close();

// 3. Valeria again: her group, the next meeting, and now the place.
({ ctx, page } = await session("valeria.soto@demo.invalid", PHONE));
expect(await page.getByText("Próxima reunión: jueves").count() > 0, "home shows the next meeting");
await audit(page, "miembro-inicio-con-grupo");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click(".dates .row__title a:has-text('Jóvenes Roca')")]);
expect(await page.getByText("Salón 2 de la iglesia").count() > 0, "a member sees where the group meets");
await audit(page, "miembro-grupo-despues");
// "Mis datos": she corrects how the church reaches her, and the home stops asking.
await page.setViewportSize(PHONE);
await page.goto(BASE + "/mi-iglesia/mis-datos/");
await audit(page, "miembro-mis-datos");
await page.setViewportSize(PHONE);
await page.fill("input[name=phone]", "55 5550 0199");
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Guardar mis datos')")]);
expect(await page.getByText("Guardamos tus datos.").count() > 0, "her data is saved");
await ctx.close();

// 4. The administration: every screen.
({ ctx, page } = await session("admin.comunidad@demo.invalid", DESKTOP));
const adminPages = [
  ["admin-inicio", "/mi-iglesia/"], ["admin-grupos", "/mi-iglesia/grupos/administrar/"], ["admin-grupo-nuevo", "/mi-iglesia/grupos/nuevo/"],
  ["admin-personas", "/mi-iglesia/personas/"], ["admin-persona-nueva", "/mi-iglesia/personas/nueva/"],
  ["admin-eventos", "/mi-iglesia/personas/desde-un-evento/"], ["admin-evento", "/mi-iglesia/personas/desde-un-evento/jovenes-2025/"],
  ["admin-cuenta", "/cuenta/"], ["admin-avisos", "/mi-iglesia/avisos/"], ["admin-aviso-nuevo", "/mi-iglesia/avisos/nuevo/"],
  ["admin-eventos-iglesia", "/mi-iglesia/eventos/administrar/"], ["admin-evento-pagina", "/mi-iglesia/eventos/noche-de-alabanza-demo/pagina/"],
  ["admin-evento-inscritos", "/mi-iglesia/eventos/noche-de-alabanza-demo/inscritos/"], ["admin-eventos-donde-sirve", "/eventos/?todos=1"],
  ["admin-evento-inicio", "/eventos/jovenes-2025/"], ["admin-devocionales", "/mi-iglesia/recursos/devocional/administrar/"],
  ["admin-devocional-nuevo", "/mi-iglesia/recursos/devocional/nuevo/"], ["admin-recursos", "/mi-iglesia/recursos/administrar/"],
  ["admin-recurso-nuevo", "/mi-iglesia/recursos/nuevo/"], ["admin-planes", "/mi-iglesia/recursos/planes/administrar/"],
  ["admin-plan-nuevo", "/mi-iglesia/recursos/planes/nuevo/"], ["admin-equipos", "/mi-iglesia/servir/equipos/"],
  ["admin-equipo", "/mi-iglesia/servir/equipos/1/"], ["admin-equipo-nuevo", "/mi-iglesia/servir/equipos/nuevo/"],
  ["admin-seguimientos", "/mi-iglesia/acompanar/todos/"], ["admin-seguimiento-nuevo", "/mi-iglesia/acompanar/nuevo/"],
  ["admin-panorama", "/mi-iglesia/panorama/"], ["admin-evento-preguntas", "/mi-iglesia/eventos/noche-de-alabanza-demo/preguntas/"],
  ["admin-evento-programa", "/mi-iglesia/eventos/noche-de-alabanza-demo/programa/"],
  ["admin-evento-opiniones", "/mi-iglesia/opiniones/noche-de-alabanza-demo/resultados/"],
  ["admin-politicas", "/mi-iglesia/politicas/"], ["admin-pagina-publica", "/mi-iglesia/pagina-publica/"], ["admin-avisos-correo", "/mi-iglesia/avisos/correo/"],
];
for (const [name, path] of adminPages) {
  const response = await page.goto(BASE + path);
  expect(response.status() === 200, `admin got ${response.status()} on ${path}`);
  await audit(page, name);
}
// 4b. Public notices of an event (issue #96): the administration makes the event's page public, publishes a notice and
// somebody without an account reads it on the public page. The message to share says there is news, never what the notice says.
await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/avisos-publicos/");
await audit(page, "admin-evento-avisos-publicos");
expect(await page.getByText("nadie de fuera los puede leer hasta que la página sea pública").count() === 1, "while the page is not public, the notices screen says so");
expect(await page.locator("main a[href^='https://wa.me/']").count() === 0, "and offers nothing to share");
await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/pagina/");
await page.check("input[name=public]");
await Promise.all([page.waitForNavigation(), page.click("main button:has-text('Guardar la página del evento')")]);
await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/avisos-publicos/");
await page.setViewportSize(DESKTOP);
await page.fill("input[name=title]", "El camión sale a las 7:00");
await page.fill("textarea[name=body]", "Lleguen media hora antes.\nLleven chamarra.");
await Promise.all([page.waitForNavigation(), page.click("main button:has-text('Publicar el aviso')")]);
expect(await page.getByText("Publicaste el aviso.").count() > 0, "the public notice is published");
await audit(page, "admin-evento-avisos-publicos-con-aviso");
const noticeShare = decodeURIComponent((await page.locator("main a[href^='https://wa.me/']").getAttribute("href")) || "");
expect(noticeShare.includes("/evento/noche-de-alabanza-demo/#avisos"), "the WhatsApp message carries the link to the notices");
expect(!/camión|chamarra|alabanza/i.test(noticeShare.replace("/evento/noche-de-alabanza-demo/", "")), `the WhatsApp message says nothing of the notice or the event: ${noticeShare}`);
{
  const anon = await browser.newContext({ viewport: PHONE, locale: "es-MX", timezoneId: "America/Mexico_City", bypassCSP: true });
  const reader = await anon.newPage();
  reader.on("pageerror", (e) => { if (!BENIGN_JS_ERROR.test(String(e))) problems.push(`sin cuenta: JS error ${e}`); });
  const response = await reader.goto(BASE + "/evento/noche-de-alabanza-demo/#avisos");
  expect(response.status() === 200, `the public page of the event answered ${response.status()}`);
  expect(await reader.locator("section[aria-labelledby=avisos]").getByText("El camión sale a las 7:00").count() === 1, "somebody without an account reads the notice");
  expect(await reader.locator("section[aria-labelledby=avisos]").getByText("Lleven chamarra.").count() === 1, "and its text");
  expect((await anon.cookies()).length === 0, "reading the public page leaves no cookie");
  await audit(reader, "publico-evento-avisos");
  // She retires it and deletes it for good: nobody outside reads it any more, and its text is gone from Emmaus.
  await page.setViewportSize(DESKTOP);
  await Promise.all([page.waitForNavigation(), page.click("main a:has-text('Editar')")]);
  await Promise.all([page.waitForNavigation(), page.click("main a:has-text('Retirar este aviso')")]);
  await audit(page, "admin-evento-aviso-publico-retirar");
  await Promise.all([page.waitForNavigation(), page.click("main button:has-text('Sí, retirar el aviso')")]);
  expect(await page.locator("main a[href^='https://wa.me/']").count() === 0, "with no notice in force there is nothing to share");
  await audit(page, "admin-evento-avisos-publicos-retirado");
  await reader.goto(BASE + "/evento/noche-de-alabanza-demo/");
  expect(await reader.locator("#avisos").count() === 0 && await reader.getByText("El camión sale a las 7:00").count() === 0, "a retired notice is gone from the public page");
  await anon.close();
  await Promise.all([page.waitForNavigation(), page.click("main a:has-text('Borrar para siempre')")]);
  await Promise.all([page.waitForNavigation(), page.click("main button:has-text('Sí, borrar para siempre')")]);
  expect(await page.getByText("Borraste el aviso para siempre.").count() > 0 && await page.getByText("Este evento todavía no tiene avisos públicos.").count() === 1, "the retired notice is deleted for good");
  // The event's page goes back to how the seed left it (not public): nothing later in this file, and the review
  // that runs after it, sees a different event.
  await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/pagina/");
  await page.uncheck("input[name=public]");
  await Promise.all([page.waitForNavigation(), page.click("main button:has-text('Guardar la página del evento')")]);
  expect((await page.goto(BASE + "/evento/noche-de-alabanza-demo/")).status() === 404, "the event's page is not public again");
}
await page.goto(BASE + "/mi-iglesia/personas/?q=renata");
expect(await page.getByText("Renata Aguilar").count() === 0, "a campero is not a person until somebody brings them");
await page.goto(BASE + "/mi-iglesia/personas/desde-un-evento/jovenes-2025/");
await Promise.all([page.waitForNavigation(), page.click("li:has-text('Renata Aguilar Cruz') button")]);
await page.goto(BASE + "/mi-iglesia/personas/?q=renata");
await Promise.all([page.waitForNavigation(), page.click("a.row__link:has-text('Renata Aguilar Cruz')")]);
expect(await page.getByText("Su mamá, papá o tutor está de acuerdo").count() === 0, "no access form before there is an e-mail");
await audit(page, "admin-persona");
await page.goto(BASE + "/mi-iglesia/grupos/administrar/");
await Promise.all([page.waitForNavigation(), page.click("main li:has-text('Jóvenes Roca') a:has-text('Personas')")]);
await audit(page, "admin-grupo-personas");
// 5. A simple event: created in the agenda, lands on its page, and has none of the camp's screens.
await page.goto(BASE + "/mi-iglesia/eventos/nuevo/");
await audit(page, "admin-evento-sencillo-nuevo");
await page.fill("input[name=name]", "Cena de prueba");
await page.fill("input[name=starts_on]", new Date(Date.now() + 7 * 864e5).toISOString().slice(0, 10));
await Promise.all([page.waitForNavigation(), page.click("main button:has-text('Crear el evento')")]);
expect(page.url().endsWith("/mi-iglesia/eventos/cena-de-prueba/pagina/"), `a new simple event lands on its page, not on ${page.url()}`);
await audit(page, "admin-evento-sencillo-pagina");
expect((await page.goto(BASE + "/eventos/cena-de-prueba/")).status() === 404, "a simple event has no camp screens");
await page.goto(BASE + "/mi-iglesia/eventos/cena-de-prueba/datos/");
await audit(page, "admin-evento-sencillo-datos");
await page.goto(BASE + "/mi-iglesia/eventos/cena-de-prueba/duplicar/");
await audit(page, "admin-evento-sencillo-duplicar");
expect((await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/duplicar/")).status() === 404, "a camp is not duplicated as a simple event");
await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/inscritos/llego/");
expect(await page.getByText("todavía no empieza").count() > 0 || (await page.locator("main form").count()) > 0, "the walk-up page answers");
await audit(page, "admin-evento-llego");
await ctx.close();

// Wave 9 (docs/v2/wave9/). Each step below ends by taking back what it did, as far as the screens allow: what cannot
// be deleted (a cancelled booking, an ended series without published dates, a draft devotional, an undone join) stays
// where only the administration reads it. They need the seed's pastor and its pair of records: --panorama --duplicados.
const TZ = "America/Mexico_City";
const localDay = (days) => new Intl.DateTimeFormat("en-CA", { timeZone: TZ }).format(new Date(Date.now() + days * 864e5));
const anonymous = () => browser.newContext({ viewport: PHONE, locale: "es-MX", timezoneId: TZ, bypassCSP: true });
// A POST written by hand from a signed-in page, with that page's own CSRF token: what answers is the permission rule.
const handPost = (from, path, fields = {}) => from.evaluate(async ([url, data]) => {
  const token = document.querySelector("input[name=csrfmiddlewaretoken]").value;
  const response = await fetch(url, { method: "POST", body: new URLSearchParams({ csrfmiddlewaretoken: token, ...data }), redirect: "manual" });
  return { status: response.status, text: response.status === 403 ? await response.text() : "" };
}, [path, fields]);

// 6. Spaces (issue #94): the guide asks for a room, a member reads "Ocupado", the administration approves, books the
// same hours over the clash it was shown, and both bookings are cancelled.
{
  const day = localDay(9);
  const guide = await session("guia.grupo@demo.invalid", PHONE);
  await guide.page.goto(BASE + "/mi-iglesia/espacios/");
  expect(await guide.page.locator("main").getByText("Lo que pediste").count() === 1, "the guide reads what he asked for");
  await audit(guide.page, "guia-espacios");
  await guide.page.setViewportSize(PHONE);
  const weekPath = await guide.page.locator("main .row__title a:has-text('Salón 2')").getAttribute("href");
  await Promise.all([guide.page.waitForNavigation(), guide.page.click("main li:has-text('Salón 2') a:has-text('Pedir')")]);
  await audit(guide.page, "guia-espacio-pedir");
  await guide.page.setViewportSize(PHONE);
  await guide.page.fill("input[name=title]", "Ensayo de prueba");
  await guide.page.fill("input[name=day]", day);
  await guide.page.fill("input[name=starts]", "10:00");
  await guide.page.fill("input[name=ends]", "11:00");
  await Promise.all([guide.page.waitForNavigation(), guide.page.click("main button:has-text('Pedir la reserva')")]);
  await guide.page.goto(BASE + "/mi-iglesia/espacios/");
  expect(await guide.page.locator("main li:has-text('Ensayo de prueba')").getByText("Por decidir").count() === 1, "the guide's request waits: «Por decidir»");
  await guide.page.goto(BASE + weekPath + "?semana=" + day);
  expect(await guide.page.locator(`section[aria-labelledby='dia-${day}'] .row__title`).getByText("Ensayo de prueba").count() === 1, "who asked reads the title in the week");
  await audit(guide.page, "guia-espacio-semana");

  const member = await session("valeria.soto@demo.invalid", PHONE);
  await member.page.goto(BASE + "/mi-iglesia/espacios/");
  expect(await member.page.locator("main a:has-text('Pedir')").count() === 0, "a member is not offered to ask for a space");
  await audit(member.page, "miembro-espacios");
  await member.page.goto(BASE + weekPath + "?semana=" + day);
  const busy = member.page.locator(`section[aria-labelledby='dia-${day}']`);
  expect(await busy.getByText("Ocupado", { exact: true }).count() === 1 && await busy.getByText("De 10:00 a 11:00.").count() === 1, "a member reads «Ocupado» and the hours");
  expect(await member.page.getByText("Ensayo de prueba").count() === 0 && await member.page.getByText("Gabriel").count() === 0, "a member reads neither the title nor who asked");
  await audit(member.page, "miembro-espacio-semana");
  expect((await member.page.goto(BASE + weekPath + "pedir/")).status() === 403, "a member cannot open the request form");
  expect((await member.page.goto(BASE + "/mi-iglesia/espacios/solicitudes/")).status() === 403, "nor the administration's requests");

  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  await admin.page.goto(BASE + "/mi-iglesia/espacios/");
  await audit(admin.page, "admin-espacios");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main a:has-text('Solicitudes de reserva')")]);
  expect(await admin.page.locator("main li:has-text('Salón 2: Ensayo de prueba')").getByText("La pidió Gabriel Ortega Luna.").count() === 1, "the administration reads the request and who asked");
  await audit(admin.page, "admin-espacios-solicitudes");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main li:has-text('Ensayo de prueba') button:has-text('Aprobar')")]);
  expect(await admin.page.getByText("Aprobaste la reserva de Salón 2").count() > 0, "approving is said back");
  await admin.page.goto(BASE + weekPath + "pedir/?dia=" + day);
  await admin.page.fill("input[name=title]", "Junta de prueba");
  await admin.page.fill("input[name=day]", day);
  await admin.page.fill("input[name=starts]", "10:00");
  await admin.page.fill("input[name=ends]", "11:00");
  await admin.page.click("main button[type=submit]:not([name])");
  await admin.page.waitForSelector("#empalme");
  expect(await admin.page.locator("#empalme").getByText("de 10:00 a 11:00: Ensayo de prueba, de Gabriel Ortega Luna").count() === 1, "the clash is listed before anything is booked");
  await audit(admin.page, "admin-espacio-empalme");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Reservar aunque se empalme')")]);
  expect(await admin.page.getByText("Reservaste Salón 2").count() > 0, "the administration books over the clash it saw");
  await admin.page.goto(BASE + "/mi-iglesia/espacios/solicitudes/?estado=decididas");
  expect(await admin.page.locator("main li:has-text('Junta de prueba')").getByText("Se aprobó aunque se empalma con otra reserva.").count() === 1, "the override is on the record");
  await audit(admin.page, "admin-espacios-decididas");
  await admin.page.goto(BASE + `/mi-iglesia/espacios/uso/?desde=${day}&hasta=${day}`);
  expect((await admin.page.locator("main tr:has-text('Salón 2') td[data-label='Reservas']").innerText()).trim() === "2", "the usage of that day counts both approved bookings");
  await audit(admin.page, "admin-espacios-uso");
  await admin.page.goto(BASE + "/mi-iglesia/espacios/administrar/");
  await audit(admin.page, "admin-espacios-administrar");

  // The guide cancels his; the administration cancels its own: the day is free again.
  await guide.page.setViewportSize(PHONE);
  await guide.page.goto(BASE + "/mi-iglesia/espacios/");
  await Promise.all([guide.page.waitForNavigation(), guide.page.click("main li:has-text('Ensayo de prueba') a:has-text('Cancelar')")]);
  await audit(guide.page, "guia-espacio-cancelar");
  await Promise.all([guide.page.waitForNavigation(), guide.page.click("main button:has-text('Sí, cancelar la reserva')")]);
  expect(await guide.page.getByText("Cancelaste la reserva de Salón 2").count() > 0, "the guide's cancellation is said back");
  await admin.page.goto(BASE + weekPath + "?semana=" + day);
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main li:has-text('Junta de prueba') a:has-text('Cancelar')")]);
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Sí, cancelar la reserva')")]);
  await member.page.goto(BASE + weekPath + "?semana=" + day);
  expect(await member.page.locator(`section[aria-labelledby='dia-${day}']`).getByText("Libre todo el día.").count() === 1, "with both cancelled the day is free again");
  await guide.ctx.close();
  await member.ctx.close();
  await admin.ctx.close();
}

// 7. Series and the calendar (issue #93): a weekly series, its dates, one row for a member, a day called off and
// brought back, and the church's calendar file, which answers only while a public event exists.
{
  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  const member = await session("valeria.soto@demo.invalid", PHONE);
  await admin.page.goto(BASE + "/mi-iglesia/eventos/administrar/");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main a:has-text('Crear una serie')")]);
  await audit(admin.page, "admin-serie-nueva");
  await admin.page.fill("input[name=name]", "Oración de prueba");
  await admin.page.check("input[name=weekdays][value='2']");
  await admin.page.fill("input[name=starts_at]", "07:00");
  await admin.page.fill("input[name=location]", "Salón 2");
  await admin.page.check("input[name=published]");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Crear la serie')")]);
  const seriesUrl = admin.page.url();
  const coming = admin.page.locator("section[aria-labelledby=fechas] li.row");
  const dates = await coming.count();
  expect(/\/administrar\/series\/\d+\/$/.test(seriesUrl) && dates >= 7 && dates <= 9, `a new weekly series shows its dates of the coming eight weeks, not ${dates} on ${seriesUrl}`);
  expect(await admin.page.locator(".summary-list").getByText("Cada miércoles, a las 07:00, 90 minutos").count() === 1, "the series says when it happens");
  await audit(admin.page, "admin-serie");
  await admin.page.goto(seriesUrl + "editar/");
  await audit(admin.page, "admin-serie-editar");

  await member.page.goto(BASE + "/mi-iglesia/eventos/");
  const row = member.page.locator("main li.row:has-text('Oración de prueba')");
  expect(await row.count() === 1, `a member's Eventos shows one row for the series, not ${await row.count()}`);
  expect(await row.getByText("Se repite cada miércoles.").count() === 1, "and the row says it repeats");
  await audit(member.page, "miembro-eventos-con-serie");
  await member.page.setViewportSize(PHONE);
  await Promise.all([member.page.waitForNavigation(), row.locator("a:has-text('Ver las otras fechas')").click()]);
  const seriesPage = member.page.url();
  const listed = () => member.page.locator("main ul.rows > li").count();
  expect(await listed() === dates, `the member reads every date of the series: ${await listed()} of ${dates}`);
  await audit(member.page, "miembro-serie");
  // The second date is called off: it leaves the member's list, and comes back with "Sí hay".
  await admin.page.goto(seriesUrl);
  const offDay = (await coming.nth(1).locator(".row__title").innerText()).trim().toLowerCase().replace(/ de \d{4}/, "");  // the member's list says the day without its year
  await Promise.all([admin.page.waitForNavigation(), coming.nth(1).locator("button:has-text('Este día no hay')").click()]);
  expect(await coming.nth(1).locator(".status, .badge, [class*=status]").getByText("Este día no hay").count() > 0 || await coming.nth(1).locator("button:has-text('Sí hay')").count() === 1, "the date is marked «Este día no hay»");
  await audit(admin.page, "admin-serie-dia-sin");
  await member.page.goto(seriesPage);
  expect(await listed() === dates - 1 && !(await member.page.locator("main").innerText()).toLowerCase().includes(offDay), `a day called off (${offDay}) leaves the member's list`);
  await Promise.all([admin.page.waitForNavigation(), coming.nth(1).locator("button:has-text('Sí hay')").click()]);
  expect(await admin.page.getByText("Listo: ese día sí hay.").count() > 0, "bringing the day back is said");
  await member.page.goto(seriesPage);
  expect(await listed() === dates && (await member.page.locator("main").innerText()).toLowerCase().includes(offDay), `«Sí hay» brings ${offDay} back to the member's list`);

  // The calendar file. The seed has no public event: the address is the same 404 as one that never existed.
  const anon = await anonymous();
  const calendar = BASE + "/iglesia/iglesia-demo/calendario.ics";
  expect((await anon.request.get(calendar)).status() === 404, "with no public event the church's calendar is a 404");
  expect((await anon.request.get(BASE + "/evento/noche-de-alabanza-demo/calendario.ics")).status() === 404, "and so is the calendar of an event that is not public");
  await admin.page.goto(BASE + "/mi-iglesia/eventos/administrar/");
  expect(await admin.page.locator("#enlace-calendario").count() === 0, "the administration is not shown an address that does not answer");
  await admin.page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/pagina/");
  await admin.page.check("input[name=public]");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Guardar la página del evento')")]);
  await admin.page.goto(BASE + "/mi-iglesia/eventos/administrar/");
  expect(((await admin.page.locator("#enlace-calendario").inputValue().catch(() => "")) || "").endsWith("/iglesia/iglesia-demo/calendario.ics"), "with a public event the administration reads the calendar's address");
  await audit(admin.page, "admin-eventos-con-serie-y-calendario");
  const feed = await anon.request.get(calendar);
  const ics = feed.status() === 200 ? await feed.text() : "";
  expect(feed.status() === 200 && (feed.headers()["content-type"] || "").startsWith("text/calendar"), `the church's calendar answered ${feed.status()} ${feed.headers()["content-type"]}`);
  expect(ics.startsWith("BEGIN:VCALENDAR") && ics.includes("SUMMARY:Noche de alabanza") && ics.includes("/evento/noche-de-alabanza-demo/"), "the calendar carries the public event and its address");
  expect(!ics.includes("Oración de prueba") && !/ATTENDEE|ORGANIZER|@demo\.invalid/.test(ics), "it carries neither the dates that are not public nor any person");
  const reader = await anon.newPage();
  await reader.goto(BASE + "/evento/noche-de-alabanza-demo/");
  const add = reader.locator("main a:has-text('Agregar a mi calendario')");
  expect(await add.count() === 1, "the public page of the event offers «Agregar a mi calendario»");
  const one = await anon.request.get(new URL((await add.getAttribute("href").catch(() => null)) || "/missing", BASE).href);
  expect(one.status() === 200 && (await one.text()).includes("SUMMARY:Noche de alabanza"), `the event's own calendar file answered ${one.status()}`);
  expect((await anon.cookies()).length === 0, "the calendar and the public page leave no cookie");
  await audit(reader, "publico-evento-calendario");
  // Back to the seed: the event is not public, the series publishes nothing and adds no more dates.
  await admin.page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/pagina/");
  await admin.page.uncheck("input[name=public]");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Guardar la página del evento')")]);
  expect((await anon.request.get(calendar)).status() === 404, "the calendar is a 404 again once nothing is public");
  await anon.close();
  await admin.page.goto(seriesUrl + "editar/");
  await admin.page.uncheck("input[name=published]");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Guardar la serie')")]);
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Terminar la serie')")]);
  expect(await admin.page.getByText("Esta serie terminó.").count() === 1, "the series is ended");
  await audit(admin.page, "admin-serie-terminada");
  await member.page.goto(BASE + "/mi-iglesia/eventos/");
  expect(await member.page.getByText("Oración de prueba").count() === 0, "a series that is not published is gone from the member's Eventos");
  expect((await member.page.goto(seriesPage)).status() === 404, "and its page is a 404 for her");
  await admin.ctx.close();
  await member.ctx.close();
}

// 8. Pastoral approval (issue #99): the administration prepares and asks, only the pastor publishes, and a change of
// the published words takes them out of sight.
{
  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  const member = await session("valeria.soto@demo.invalid", PHONE);
  const pastor = await session("pastor.iglesia@demo.invalid", DESKTOP);
  const title = "Devocional de prueba";
  const save = () => Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Guardar el devocional')")]);
  const adminRow = () => admin.page.locator(`main li:has-text('${title}')`);
  const past = "/mi-iglesia/recursos/devocional/anteriores/";
  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/nuevo/");
  await admin.page.fill("input[name=date]", localDay(-3));
  await admin.page.fill("input[name=title]", title);
  await admin.page.fill("input[name=passage]", "Salmo 23");
  await admin.page.fill("textarea[name=body]", "Texto de prueba para la aprobación pastoral.");
  await admin.page.check("input[name=publish]");
  await save();
  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/administrar/");
  expect(await adminRow().getByText("Por aprobar").count() === 1, "asking for approval leaves the devotional «Por aprobar»");
  const detail = await admin.page.locator(`main a:has-text('${title}')`).first().getAttribute("href");
  const id = detail.match(/(\d+)\/$/)[1];
  const review = `/mi-iglesia/recursos/aprobaciones/devocional/${id}/`;
  await member.page.goto(BASE + past);
  expect(await member.page.getByText(title).count() === 0 && (await member.page.goto(BASE + detail)).status() === 404, "a member does not see what waits for the pastor");

  expect(await pastor.page.locator(`.rail a[href='/mi-iglesia/recursos/aprobaciones/']`).count() === 1, "the pastor's rail leads to «Aprobación pastoral»");
  await pastor.page.goto(BASE + "/mi-iglesia/recursos/aprobaciones/");
  await audit(pastor.page, "pastor-aprobaciones");
  await Promise.all([pastor.page.waitForNavigation(), pastor.page.click(`main li:has-text('${title}') a:has-text('Revisar')`)]);
  expect(await pastor.page.locator("main").getByText("Texto de prueba para la aprobación pastoral.").count() === 1, "the pastor reads the whole text before deciding");
  await audit(pastor.page, "pastor-aprobacion-revisar");
  await pastor.page.click("main details summary:has-text('No lo apruebo')");
  await audit(pastor.page, "pastor-aprobacion-devolver");
  await pastor.page.fill("textarea[name=reason]", "Falta decir de qué versículos se trata.");
  await Promise.all([pastor.page.waitForNavigation(), pastor.page.click("main button:has-text('Devolver sin aprobar')")]);
  expect(await pastor.page.getByText(`Devolviste «${title}».`).count() > 0, "returning it is said back to the pastor");
  await audit(pastor.page, "pastor-aprobaciones-devuelto");

  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/administrar/");
  expect(await adminRow().getByText("Devuelto").count() > 0, "the administration's list says «Devuelto»");
  await audit(admin.page, "admin-devocionales-devuelto");
  await Promise.all([admin.page.waitForNavigation(), adminRow().locator("a:has-text('Editar')").click()]);
  expect(await admin.page.locator("main").getByText("Falta decir de qué versículos se trata.").count() === 1, "the administration reads the pastor's reason on the form");
  await audit(admin.page, "admin-devocional-devuelto");
  await admin.page.fill("input[name=passage]", "Salmo 23:1-4");
  await admin.page.check("input[name=publish]");
  await save();
  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/administrar/");
  expect(await adminRow().getByText("Por aprobar").count() === 1, "saved and asked again: «Por aprobar»");
  await admin.page.goto(BASE + review);
  expect(await admin.page.locator("main form").count() === 0 && await admin.page.locator("main").getByText("Solo un pastor de tu iglesia puede aprobarlo.").count() === 1, "the administration reads the review page without the decision");
  await audit(admin.page, "admin-aprobacion-revisar");
  await admin.page.goto(BASE + "/mi-iglesia/recursos/aprobaciones/");
  await audit(admin.page, "admin-aprobaciones");
  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/" + id + "/editar/");
  const forged = await handPost(admin.page, review + "aprobar/", { permitted_use: "propia" });
  expect(forged.status === 403 && forged.text.includes("No tienes acceso a esta página"), `the administration's own POST to approve answered ${forged.status}, not the 403 of the permission`);
  await member.page.goto(BASE + past);
  expect(await member.page.getByText(title).count() === 0, "and it published nothing");

  await pastor.page.goto(BASE + review);
  await pastor.page.check("input[name=permitted_use][value=propia]");
  await Promise.all([pastor.page.waitForNavigation(), pastor.page.click("main button:has-text('Aprobar y publicar')")]);
  expect(await pastor.page.getByText(`Aprobaste «${title}».`).count() > 0, "the pastor's approval is said back");
  await member.page.goto(BASE + past);
  await Promise.all([member.page.waitForNavigation(), member.page.click(`main a:has-text('${title}')`)]);
  expect(await member.page.locator("main").getByText("Texto de prueba para la aprobación pastoral.").count() === 1, "once approved, the member reads it");
  await audit(member.page, "miembro-devocional-aprobado");

  // A change of the published words: the form warns before saving, and the member stops finding it.
  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/" + id + "/editar/");
  expect(await admin.page.locator("main").getByText("Este devocional está publicado. Si cambias").count() === 1, "the form of a published devotional warns before saving");
  await audit(admin.page, "admin-devocional-publicado");
  await admin.page.fill("textarea[name=body]", "Texto de prueba, cambiado después de publicarse.");
  await save();
  await member.page.goto(BASE + past);
  expect(await member.page.getByText(title).count() === 0 && (await member.page.goto(BASE + detail)).status() === 404, "editing the published text takes it away from the member");
  // Back to a draft nobody was asked about: the pastor's list is as the seed left it.
  await admin.page.goto(BASE + "/mi-iglesia/recursos/devocional/" + id + "/editar/");
  await audit(admin.page, "admin-devocional-fuera-de-publicacion");
  await admin.page.uncheck("input[name=publish]");
  await save();
  await pastor.page.goto(BASE + "/mi-iglesia/recursos/aprobaciones/");
  expect(await pastor.page.locator("main").getByText(title).count() === 0, "a draft nobody asked about is not on the pastor's list");
  expect(await pastor.page.locator("main").getByText("Dar fruto").count() > 0, "what the seed left waiting still waits");
  await admin.ctx.close();
  await member.ctx.close();
  await pastor.ctx.close();
}

// 9. Possible duplicates (issue #91): the pair of the seed is reviewed, joined, gone from the search, and two again
// after the undo.
{
  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  const found = async () => {
    await admin.page.goto(BASE + "/mi-iglesia/personas/?q=ibarra");
    return (await admin.page.locator("main a.row__link").allInnerTexts()).map((text) => text.trim()).filter((name) => name.startsWith("Renata")).sort().join("|");
  };
  expect(await found() === "Renata Ibarra|Renata Ibarra Solís", `the seed's pair is in Personas: ${await found()}`);
  await admin.page.goto(BASE + "/mi-iglesia/personas/");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main a:has-text('Revisar posibles duplicados')")]);
  expect(await admin.page.locator("main li:has-text('Renata Ibarra Solís y Renata Ibarra')").getByText("Tienen el mismo correo.").count() === 1, "the pair is listed with the signal that joins it");
  await audit(admin.page, "admin-duplicados");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main li:has-text('Renata Ibarra Solís y Renata Ibarra') a:has-text('Revisar')")]);
  expect(await admin.page.locator("main").getByText("Nada cambia hasta que confirmes.").count() === 1, "the preview says nothing changes yet");
  expect(await admin.page.locator("input[name=elige_phone]").count() === 2, "the administrator chooses which of the two phones stays");
  await audit(admin.page, "admin-duplicados-vista-previa");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Unir estos registros')")]);
  expect(await admin.page.getByText("Uniste los dos registros.").count() > 0, "the join is said back");
  const merge = admin.page.url();
  await audit(admin.page, "admin-union");
  expect(await found() === "Renata Ibarra Solís", `the absorbed record is gone from the search: ${await found()}`);
  await admin.page.goto(BASE + "/mi-iglesia/personas/duplicados/");
  expect(await admin.page.locator("main").getByText("Tienen el mismo correo.").count() === 0, "a joined pair is not suggested again");
  await audit(admin.page, "admin-duplicados-con-union");
  await admin.page.goto(merge);
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main button:has-text('Deshacer la unión')")]);
  expect(await admin.page.getByText("Deshiciste la unión: vuelven a ser dos registros.").count() > 0, "the undo is said back");
  expect(await admin.page.locator("main").getByText("Regresó todo lo que la unión había pasado.").count() === 1, "and everything went back");
  await audit(admin.page, "admin-union-deshecha");
  expect(await found() === "Renata Ibarra|Renata Ibarra Solís", `after the undo both are back: ${await found()}`);
  await admin.ctx.close();
  const member = await session("valeria.soto@demo.invalid", PHONE);
  for (const path of ["/mi-iglesia/personas/duplicados/", "/mi-iglesia/personas/duplicados/5/6/", "/mi-iglesia/recursos/aprobaciones/"]) {
    const response = await member.page.goto(BASE + path);
    expect(response.status() === 403, `member got ${response.status()} on ${path}`);
  }
  // 10. The church of the request (issue #32): an account of one church is never offered another one. The seed has no
  // account in two churches, so the switcher itself is not walked here.
  for (const path of ["/mi-iglesia/", "/cuenta/", "/mi-iglesia/eventos/"]) {
    await member.page.goto(BASE + path);
    expect(await member.page.locator("form[action='/cuenta/iglesia/']").count() === 0 && await member.page.getByText("Cambiar a «").count() === 0, `an account of one church is offered no other church on ${path}`);
  }
  expect((await member.page.goto(BASE + "/cuenta/iglesia/")).status() === 405, "the address that changes church takes no GET");
  await member.ctx.close();
}
await browser.close();

fs.writeFileSync(`${out}/results.json`, JSON.stringify({ checks, problems }, null, 2));
if (problems.length) {
  console.log(problems.join("\n"));
  console.log(`community flow FAILED: ${problems.length} problem(s) in ${checks} checks`);
  process.exit(1);
}
console.log(`community flow OK: ${checks} checks, 0 problems`);
