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

// Pressing a button or a link. A form that acts in place (form[data-inline]) changes its region without a new page:
// wait until nothing is busy and the region has settled; anything else is a page load. Either way the step ends
// with the page as the person sees it.
async function act(page, selector) {
  const target = page.locator(selector).first();
  const inPlace = await target.evaluate((node) => !!node.closest("form[data-inline]"));
  if (!inPlace) {
    await Promise.all([page.waitForNavigation(), target.click()]);
    return;
  }
  await target.click();
  await page.waitForFunction(() => !document.querySelector("[aria-busy='true']"), null, { timeout: 15000 });
  await page.waitForLoadState("load");
  await page.waitForTimeout(200);  // the region's crossfade
}

// 1. The former campero, on a phone: home, groups, ask to join.
let { ctx, page } = await session("valeria.soto@demo.invalid", PHONE);
expect(new URL(page.url()).pathname === "/mi-iglesia/", `member lands on ${page.url()}, not on /mi-iglesia/`);
expect(await page.locator("h1").innerText() === "Hoy", "the home is today");
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
await Promise.all([page.waitForNavigation(), page.click("section[aria-labelledby=devocional] .row__title a:has-text('Permanecer')")]);
await audit(page, "miembro-devocional");
await page.setViewportSize(PHONE);
// The mark may act in place or reload the page: either way the button becomes the word «Leído»
await page.click("button:has-text('Marcar como leído')");
await page.waitForSelector(".reading__done:has-text('Leído')");
expect(await page.locator("button:has-text('Marcar como leído')").count() === 0, "reading the devotional is confirmed");
await page.goto(BASE + "/mi-iglesia/recursos/");
expect(await page.locator("main a[href^='https://www.youtube.com']").getAttribute("rel") === "noopener noreferrer", "a resource opens on the other site, without a referrer");
await audit(page, "miembro-recursos");
// Reading plans: the plain seed has none, so this is the page that says so (the plan itself comes with --eventos).
await page.goto(BASE + "/mi-iglesia/recursos/planes/");
expect(await page.locator("main").getByText("todavía no publica planes de lectura").count() > 0, "the reading plans say there is none yet");
await audit(page, "miembro-planes");
// Serving: her team asked her for a date; she confirms it from the phone.
await page.goto(BASE + "/mi-iglesia/");
expect(await page.locator("main").getByText(/¿Puedes servir el/).count() > 0, "the home asks for the date her team asked for");
await page.goto(BASE + "/mi-iglesia/servir/");
await audit(page, "miembro-servir");
await page.setViewportSize(PHONE);
await act(page, "main button:has-text('Sí, puedo')");
expect(await page.locator("main").getByText("Confirmaste").count() > 0, "confirming a date is said back, in place");
expect(await page.locator("main button:has-text('Ya no puedo')").count() > 0, "the other answer stays at hand");
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
await act(page, "main button:has-text('Inscribirme')");
expect(await page.locator("main").getByText("Te inscribiste").count() > 0, "signing up is confirmed on the page");
await audit(page, "miembro-evento-inscrita");
await page.goto(BASE + "/mi-iglesia/");
expect(await page.locator("main").getByText("Te inscribiste").count() > 0, "the home says she signed up");
await page.setViewportSize(PHONE);
await act(page, "main a:has-text('Encuentra tu grupo')");
await audit(page, "miembro-grupos");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click(".row__title a:has-text('Jóvenes Roca')")]);
expect(await page.getByText("Salón 2 de la iglesia").count() === 0, "the meeting place is hidden from a non-member");
await audit(page, "miembro-grupo-antes");
await page.setViewportSize(PHONE);
await act(page, "button:has-text('Pedir unirme')");
expect(await page.getByText("Ya pediste unirte.").count() > 0, "request confirmed on screen, in place");
expect(await page.getByText("le avisamos por correo").count() === 0, "no e-mail is claimed when none can be sent");
await audit(page, "miembro-grupo-pedido");
for (const path of ["/mi-iglesia/personas/", "/mi-iglesia/grupos/administrar/", "/mi-iglesia/eventos/administrar/", "/mi-iglesia/eventos/noche-de-alabanza-demo/inscritos/"]) {
  const response = await page.goto(BASE + path);
  expect(response.status() === 403, `member got ${response.status()} on ${path}`);
}
await ctx.close();

// 2. The guide: the panel names the pending requests; accept Valeria.
({ ctx, page } = await session("guia.grupo@demo.invalid", DESKTOP));
const asks = await page.locator(".ask .sign__title").allInnerTexts();
expect(asks.length === 2 && asks.some((t) => t.startsWith("Valeria Soto Marín quiere unirse")), `each request raised on its own card: ${asks.join(" | ")}`);
expect(await page.locator(".rail__nav").isVisible() && await page.locator(".tabs").isHidden(), "wide screens get the rail, not the tabs");
expect((await page.locator(".rail .rail__link:has-text('Jóvenes Roca') .badge").textContent()).trim().startsWith("2"), "the rail counts what waits in her group");
await audit(page, "guia-inicio");
await act(page, ".ask:has-text('Valeria Soto Marín') a:has-text('Ver solicitud')");
await audit(page, "guia-personas");
await act(page, "li:has-text('Valeria Soto') button[value=aceptar]");
expect(await page.locator("main .hecho").getByText("Valeria Soto Marín ya es parte de Jóvenes Roca.").count() > 0, "acceptance leaves its line where the request was");
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
expect(await page.locator("main").getByText(/Se reúne el jueves/).count() > 0, "home shows the next meeting");
await audit(page, "miembro-inicio-con-grupo");
await page.setViewportSize(PHONE);
await act(page, "main .row__title a:has-text('Jóvenes Roca')");
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
await act(page, "main .row__title a:has-text('Renata Aguilar Cruz')");
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
  // A row of the list opens the week of its space; who may ask finds the page's one filled action there.
  await Promise.all([guide.page.waitForNavigation(), guide.page.click("main .row__title a:has-text('Salón 2')")]);
  expect(await guide.page.locator("main a.btn--primary").count() === 1, "the week of a space has one filled action for who may ask");
  await Promise.all([guide.page.waitForNavigation(), guide.page.click("main a:has-text('Pedir este espacio')")]);
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
  expect(await member.page.locator("main a:has-text('Pedir')").count() === 0, "nor in the week of a space, where who may ask finds it");
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
  // Approving a request that overlaps nothing acts in place: the row leaves, a line keeps the trace where it was.
  const queueUrl = admin.page.url();
  await act(admin.page, "main li:has-text('Ensayo de prueba') button:has-text('Aprobar')");
  expect(admin.page.url() === queueUrl, "approving a request that overlaps nothing stays on the list");
  expect(await admin.page.locator("#solicitudes .hecho").getByText("Aprobaste la reserva de Salón 2").count() === 1, "approving is said back, where the request was");
  expect(await admin.page.locator("main li:has-text('Ensayo de prueba')").count() === 0, "and the request leaves the list of what waits");
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
  // «Este día no hay» / «Sí hay» act in place: the row stays where it is and says the real result.
  const offRow = "#" + await coming.nth(1).getAttribute("id");
  await act(admin.page, `${offRow} button:has-text('Este día no hay')`);
  expect(admin.page.url() === seriesUrl, "calling a day off stays on the series");
  expect(await admin.page.locator(`${offRow} .status`).getByText("Este día no hay").count() === 1 && await admin.page.locator(`${offRow} button:has-text('Sí hay')`).count() === 1, "the date is marked «Este día no hay» and offers «Sí hay»");
  expect(await admin.page.locator(offRow).getByText("Ya no aparece en Eventos ni en el calendario público.").count() === 1, "and the row says what calling it off did");
  await audit(admin.page, "admin-serie-dia-sin");
  await member.page.goto(seriesPage);
  expect(await listed() === dates - 1 && !(await member.page.locator("main").innerText()).toLowerCase().includes(offDay), `a day called off (${offDay}) leaves the member's list`);
  await act(admin.page, `${offRow} button:has-text('Sí hay')`);
  // The message is announced once (the hidden role="status" line); the row itself shows the new state.
  expect(await admin.page.locator("[role=status]").getByText("Listo: ese día sí hay.").count() > 0, "bringing the day back is announced");
  expect(await admin.page.locator(`${offRow} .status`).getByText("Este día no hay").count() === 0 && await admin.page.locator(`${offRow} button:has-text('Este día no hay')`).count() === 1, "and the row is a date again, with «Este día no hay» at hand");
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
  // The row opens by its title (the form of the devotional); a reader's page of it is one level up.
  const id = (await adminRow().locator(".row__title a").getAttribute("href")).match(/\/devocional\/(\d+)\/editar\/$/)[1];
  const detail = `/mi-iglesia/recursos/devocional/${id}/`;
  const review = `/mi-iglesia/recursos/aprobaciones/devocional/${id}/`;
  await member.page.goto(BASE + past);
  expect(await member.page.getByText(title).count() === 0 && (await member.page.goto(BASE + detail)).status() === 404, "a member does not see what waits for the pastor");

  expect(await pastor.page.locator(`.rail a[href='/mi-iglesia/recursos/aprobaciones/']`).count() === 1, "the pastor's rail leads to «Aprobación pastoral»");
  await pastor.page.goto(BASE + "/mi-iglesia/recursos/aprobaciones/");
  await audit(pastor.page, "pastor-aprobaciones");
  await Promise.all([pastor.page.waitForNavigation(), pastor.page.click(`main li:has-text('${title}') .row__title a`)]);
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
  await Promise.all([admin.page.waitForNavigation(), adminRow().locator(".row__title a").click()]);
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
    return (await admin.page.locator("main .personas-lista .row__title a").allInnerTexts()).map((text) => text.trim()).filter((name) => name.startsWith("Renata")).sort().join("|");
  };
  expect(await found() === "Renata Ibarra|Renata Ibarra Solís", `the seed's pair is in Personas: ${await found()}`);
  await admin.page.goto(BASE + "/mi-iglesia/personas/");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main a:has-text('Revisar posibles duplicados')")]);
  expect(await admin.page.locator("main li:has-text('Renata Ibarra Solís y Renata Ibarra')").getByText("Tienen el mismo correo.").count() === 1, "the pair is listed with the signal that joins it");
  await audit(admin.page, "admin-duplicados");
  await Promise.all([admin.page.waitForNavigation(), admin.page.click("main li:has-text('Renata Ibarra Solís y Renata Ibarra') .row__title a")]);
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
  for (const path of ["/mi-iglesia/personas/duplicados/", "/mi-iglesia/recursos/aprobaciones/"]) {
    const response = await member.page.goto(BASE + path);
    expect(response.status() === 403, `member got ${response.status()} on ${path}`);
  }
  // Since wave 10 an address of Personas that names a row answers one 404 to who may not manage, whether the row
  // exists or not (people.views.church_admin_required): a 403 for real rows would let a member count people and pairs.
  for (const path of ["/mi-iglesia/personas/duplicados/5/6/", "/mi-iglesia/personas/duplicados/999998/999999/"]) {
    const response = await member.page.goto(BASE + path);
    expect(response.status() === 404, `member got ${response.status()} on ${path}`);
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

// Wave 10 (docs/v2/wave10/). As above, each step takes back what it did where a screen allows it. What stays: a closed
// form with its two answers, an import that was undone (ids and counts), a swap that was accepted, a role that is no
// longer used, the audit rows. They need the seed's second church, rotation and prayer requests:
// --dos-iglesias --rotas --oracion. The gate also seeds --formularios (rows for Formularios and «Pendientes»); step 13
// writes its own form and reads only its own rows.
const go = (target, selector) => Promise.all([target.waitForNavigation(), target.click(selector)]);
const mainText = async (target) => (await target.locator("main").innerText()).replace(/\s+/g, " ");

// 11. The church of the request, complete (issue #32): the account of two churches changes church from the rail; the
// second church has its own colours and rail; the discipleship-era screens follow the role held in each church; and a
// form opened before the change saves nothing after it.
{
  const FIRST = "Iglesia de demostración";
  const SECOND = "Iglesia del Norte (demostración)";
  const both = await session("dos.iglesias@demo.invalid", DESKTOP);
  const p = both.page;
  // The rail folds its sections (Para crecer, A tu cargo, Administración): what it holds is read with them open.
  const rail = async () => {
    await p.locator(".rail details").evaluateAll((folds) => folds.forEach((fold) => { fold.open = true; }));
    return p.locator(".rail").innerText();
  };
  // The redesigned home says the day («Hoy»); the church of the request is named at the top of the rail.
  const church = async () => (await p.locator(".rail .rail__church").innerText()).trim();
  const change = (name) => go(p, `.rail form[action='/cuenta/iglesia/'] button:has-text('Cambiar a ${name}')`);
  const primary = () => p.evaluate(() => getComputedStyle(document.documentElement).getPropertyValue("--petroleo").trim().toLowerCase());
  await p.goto(BASE + "/mi-iglesia/");
  expect(await church() === FIRST, `the account of two churches starts in its first church, not in ${await church()}`);
  expect(await p.locator(".rail a[href='/church/settings/']").count() === 1 && await p.locator(".rail button:has-text('Cambiar a')").count() === 1, "its rail has «Configuración» and one church to change to");
  await audit(p, "dos-iglesias-primera");
  await p.setViewportSize(DESKTOP);
  const firstColour = await primary();
  expect((await p.goto(BASE + "/church/settings/")).status() === 200 && await p.inputValue("input[name=name]") === FIRST, "the settings open in the first church, with its name");
  await audit(p, "dos-iglesias-configuracion");
  await p.setViewportSize(DESKTOP);
  // A second tab keeps the first church's settings form open while the first tab changes church.
  const stale = await both.ctx.newPage();
  await stale.goto(BASE + "/church/settings/");
  await stale.fill("input[name=name]", "Nombre de prueba que no se guarda");
  await p.goto(BASE + "/mi-iglesia/");
  await change(SECOND);
  expect(new URL(p.url()).pathname === "/mi-iglesia/" && await church() === SECOND, `changing church lands on the second church's «Mi iglesia», not on ${p.url()}`);
  expect(await primary() === "#7a3b12" && firstColour !== "#7a3b12", `the second church has its own colour: ${await primary()} after ${firstColour}`);
  const second = await rail();
  expect(second.includes("Jóvenes del Norte") && second.includes("Discipulado") && second.includes("Seguimientos"), "its rail has her group and the discipleship section");
  expect(await p.locator(".rail a[href='/church/settings/']").count() === 0 && !second.includes("Personas") && !second.includes("Jóvenes Roca"), "and nothing of the first church's administration");
  expect(await p.locator(`.rail button:has-text('Cambiar a ${FIRST}')`).count() === 1, "the way back is offered");
  await audit(p, "dos-iglesias-segunda");
  await p.setViewportSize(DESKTOP);
  const refused = await Promise.all([stale.waitForNavigation(), stale.click("main button:has-text('Guardar cambios')")]).then(([response]) => response);
  expect(refused.status() === 403 && await stale.getByText("Esta pantalla ya no está al día").count() > 0, `a settings form opened before the change answered ${refused.status()}, not «Esta pantalla ya no está al día»`);
  await audit(stale, "dos-iglesias-formulario-viejo");
  await stale.close();
  await p.goto(BASE + "/mi-iglesia/");
  expect(await church() === SECOND, "and the second church kept its name");
  const denied = await p.goto(BASE + "/church/settings/");
  expect(denied.status() === 403 && (await mainText(p)).includes("pide acceso a la administración de tu iglesia o del evento"), `the settings of the second church answered ${denied.status()}, not the 403 with the new sentence`);
  await audit(p, "dos-iglesias-configuracion-403");
  expect((await p.goto(BASE + "/mentor/")).status() === 200, "the mentor's screen opens in the church where she is a mentor");
  await audit(p, "dos-iglesias-mentora");
  await p.goto(BASE + "/eventos/?todos=1");
  expect(await p.getByText("Campamento de Jóvenes 2025").count() === 0 && await p.locator("main a:has-text('Crear evento')").count() === 0, "the event list of the second church has no event of the first and no «Crear evento»");
  await audit(p, "dos-iglesias-eventos");
  // On a phone the change of church is in the «Más» panel.
  await p.setViewportSize(PHONE);
  await p.goto(BASE + "/mi-iglesia/");
  await p.locator(".tabs button[data-menu-open]").click();
  expect(await p.locator(`#menu-panel button:has-text('Cambiar a ${FIRST}')`).isVisible(), "on a phone the «Más» panel offers the other church");
  await p.screenshot({ path: `${out}/dos-iglesias-segunda-mas-movil.png`, fullPage: true });
  await p.keyboard.press("Escape");
  await p.setViewportSize(DESKTOP);
  await change(FIRST);
  expect(await church() === FIRST && await p.locator(".rail a[href='/church/settings/']").count() === 1, "back in the first church «Configuración» is there again");
  expect((await p.goto(BASE + "/church/settings/")).status() === 200 && await p.inputValue("input[name=name]") === FIRST, "and its name is the one the seed gave it");
  expect((await p.goto(BASE + "/mentor/")).status() === 403, "the mentor's screen is closed where she is not a mentor");
  await both.ctx.close();
}

// 12. People, second slice (issue #91): a tag and a stage, the filtered list, the CSV of the whole church, an import
// of three rows through its preview and its undo, and the two dates a member may say in «Mis datos».
{
  const TAG = "Etiqueta de prueba";
  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  const p = admin.page;
  await p.goto(BASE + "/mi-iglesia/personas/etiquetas/");
  await audit(p, "admin-etiquetas");
  await p.fill("input[name=nombre]", TAG);
  await go(p, "main button:has-text('Crear etiqueta')");
  expect(await p.getByText(`Creaste la etiqueta «${TAG}».`).count() > 0, "creating a tag is said back");
  await audit(p, "admin-etiquetas-con-una");
  await p.goto(BASE + "/mi-iglesia/personas/?q=hugo");
  await go(p, "main .personas-lista .row__title a:has-text('Hugo Paredes Lima')");
  const hugo = p.url();
  await p.selectOption("main select[name=etapa]", "visita");
  await go(p, "main button:has-text('Guardar etapa')");
  expect(await p.getByText("Guardamos la etapa.").count() > 0, "the stage is saved");
  await p.check(`main label:has-text('${TAG}') input[name=etiqueta]`);
  await go(p, "main button:has-text('Guardar etiquetas')");
  expect(await p.getByText("Guardamos sus etiquetas.").count() > 0, "the tag is saved on the person");
  await audit(p, "admin-persona-etapa-etiqueta");
  await p.goto(BASE + "/mi-iglesia/personas/");
  // The list's tools: the stage is a view with its count, the tag a second filter folded under one line.
  await go(p, "main .personas-filtros ul[aria-label=Etapa] a:has-text('Visita')");
  expect(await p.locator("main .personas-filtros ul[aria-label=Etapa] a[aria-current=page]").getByText("Visita").count() === 1, "the stage chosen is the marked view");
  await p.click("main .personas-filtros details > summary");
  await go(p, `main .personas-filtros details a:has-text('${TAG}')`);
  expect(await p.locator("main .personas-filtros details[open] > summary").getByText(`Etiqueta: ${TAG}`).count() === 1, "the fold says which tag filters the list, and stays open");
  const filtered = (await p.locator("main .personas-lista .row__title a").allInnerTexts()).map((text) => text.trim()).join("|");
  expect(filtered === "Hugo Paredes Lima" && await p.locator("main").getByText("1 resultado").count() === 1, `the list filtered by stage and tag shows one person: ${filtered}`);
  await audit(p, "admin-personas-filtradas");
  // The file: a page first, then a POST that answers the CSV.
  await p.goto(BASE + "/mi-iglesia/personas/exportar/");
  await audit(p, "admin-personas-exportar");
  const file = await p.evaluate(async () => {
    const token = document.querySelector("main input[name=csrfmiddlewaretoken]").value;
    const response = await fetch(location.pathname, { method: "POST", body: new URLSearchParams({ csrfmiddlewaretoken: token }) });
    return { status: response.status, type: response.headers.get("content-type") || "", text: await response.text() };
  });
  expect(file.status === 200 && file.type.startsWith("text/csv"), `the export answered ${file.status} ${file.type}`);
  const exported = file.text.split(/\r?\n/).find((line) => line.includes("Paredes Lima")) || "";
  expect(file.text.includes("Etapa") && file.text.includes("Etiquetas") && exported.includes("Visita") && exported.includes(TAG), `the file carries the stage and the tag of the person: ${exported}`);
  // The import: one new person, one e-mail that already exists and one row with an error (a tag nobody created).
  const csv = [
    "Nombre,Apellidos,Correo,Teléfono,Fecha de nacimiento,Etapa,Etiquetas",
    `Persona,Importada de Prueba,importada.prueba@demo.invalid,,,visita,${TAG}`,
    "Valeria,Soto Marín,valeria.soto@demo.invalid,,,,",
    "Otra,Fila con Error,otra.fila@demo.invalid,,,,Etiqueta que no existe",
  ].join("\n") + "\n";
  await p.goto(BASE + "/mi-iglesia/personas/importar/");
  await audit(p, "admin-personas-importar");
  await p.setInputFiles("input[name=file]", { name: "personas-de-prueba.csv", mimeType: "text/csv", buffer: Buffer.from(csv, "utf8") });
  await go(p, "main button:has-text('Revisar el archivo')");
  expect(await p.locator("main").getByText("Tu archivo tiene 3 filas con datos").count() === 1, "the preview counts the three rows");
  expect(await p.locator("section[aria-labelledby=se-registran]").getByText("Persona Importada de Prueba").count() > 0, "the new person is under «Se registran»");
  expect(await p.locator("section[aria-labelledby=ya-existen]").getByText("Valeria").count() > 0, "the e-mail that exists is under «Ya existen»");
  expect(await p.locator("section[aria-labelledby=con-errores]").getByText("Otra Fila con Error").count() > 0, "the row with a tag nobody created is under «Filas con errores»");
  await audit(p, "admin-personas-importar-vista-previa");
  // The preview is the answer to a POST and is not kept by the browser: the list is read in a second tab.
  const list = await admin.ctx.newPage();
  const imported = async () => {
    await list.goto(BASE + "/mi-iglesia/personas/?q=importada");
    return list.locator("main .personas-lista .row__title a:has-text('Persona Importada de Prueba')").count();
  };
  expect(await imported() === 0, "the preview saved nobody");
  await go(p, "main button:has-text('Importar 1 persona')");
  expect(await p.getByText("Registraste a 1 persona desde el archivo.").count() > 0, "the import is said back");
  const importUrl = p.url();
  expect(/\/personas\/importaciones\/\d+\/$/.test(importUrl), `the import lands on its own page, not on ${importUrl}`);
  await audit(p, "admin-importacion");
  expect(await imported() === 1, "the imported person is in Personas");
  await p.goto(importUrl);
  await go(p, "main button:has-text('Deshacer: borrar 1 persona')");
  expect(await p.getByText("Deshiciste la importación: se borró 1 persona.").count() > 0, "the undo is said back");
  await audit(p, "admin-importacion-deshecha");
  expect(await imported() === 0, "after the undo the imported person is gone");
  // Back to the seed: the tag is deleted while one person carries it (the question says how many), the stage is empty again.
  await p.goto(BASE + "/mi-iglesia/personas/etiquetas/");
  await go(p, `main li:has-text('${TAG}') .row__end a:has-text('Borrar')`);
  expect((await mainText(p)).includes("1 persona"), "deleting a tag says how many people carry it");
  await audit(p, "admin-etiqueta-borrar");
  await go(p, "main button:has-text('Sí, borrar la etiqueta')");
  expect(await p.getByText(`Borraste la etiqueta «${TAG}».`).count() > 0, "the tag is deleted");
  await p.goto(hugo);
  await p.selectOption("main select[name=etapa]", "");
  await go(p, "main button:has-text('Guardar etapa')");
  await p.goto(BASE + "/mi-iglesia/personas/?etapa=visita&q=hugo");
  expect(await p.locator("main .personas-lista .row__title a").count() === 0 && (await mainText(p)).includes("Nadie coincide con lo que buscas"), "the person has no stage again");
  await admin.ctx.close();
  const member = await session("valeria.soto@demo.invalid", PHONE);
  await member.page.goto(BASE + "/mi-iglesia/mis-datos/");
  expect(await member.page.locator("main input[name=baptized_on]").count() === 1 && await member.page.locator("main input[name=arrived_on]").count() === 1, "«Mis datos» asks a member for her two dates");
  expect(await member.page.locator("main [name=member_since], main [name=stage], main [name=etapa]").count() === 0, "and neither for the membership date nor the stage");
  await audit(member.page, "miembro-mis-datos-fechas");
  for (const path of ["/mi-iglesia/personas/importar/", "/mi-iglesia/personas/exportar/", "/mi-iglesia/personas/etiquetas/"]) {
    const response = await member.page.goto(BASE + path);
    expect(response.status() === 403, `member got ${response.status()} on ${path}`);
  }
  await member.ctx.close();
}

// 13. General forms and «Pendientes» (issue #92): the administration writes and publishes a public form; somebody
// without an account answers it; the same contact again leaves no second answer; the task goes to the guide who reads
// the form, who notes and closes it; a member reads no answer; a closed form's link is a 404.
{
  const TITLE = "Formulario de prueba";
  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  const p = admin.page;
  await p.goto(BASE + "/mi-iglesia/formularios/administrar/");
  await audit(p, "admin-formularios");
  await go(p, "main a:has-text('Crear un formulario')");
  await audit(p, "admin-formulario-nuevo");
  await p.fill("input[name=title]", TITLE);
  await p.fill("input[name=purpose]", "Para probar los formularios públicos.");
  await p.check("input[name=audience][value=publico]");
  await p.fill("input[name=closes_on]", localDay(20));
  await p.check("input[name=retention_days][value='30']");
  await p.check("input[name=creates_task]");
  await go(p, "main button:has-text('Crear el borrador')");
  const formUrl = p.url();
  expect(/\/formularios\/administrar\/\d+\/$/.test(formUrl) && await p.getByText("Creaste el formulario como borrador.").count() > 0, `a new form lands on its page as a draft, not on ${formUrl}`);
  await go(p, "main a:has-text('Datos de contacto')");
  await audit(p, "admin-formulario-pregunta-contacto");
  await p.check("input[name=required]");
  await p.check("input[name=ask_email]");
  await go(p, "main button:has-text('Agregar al borrador')");
  await go(p, "main a:has-text('Una opción')");
  await p.fill("input[name=label]", "¿Qué día prefieres?");
  await p.check("input[name=required]");
  await p.fill("textarea[name=options]", "Sábado\nDomingo");
  await audit(p, "admin-formulario-pregunta-opcion");
  await go(p, "main button:has-text('Agregar al borrador')");
  expect(await p.getByText("Guardamos la pregunta en el borrador.").count() > 0, "the question is saved in the draft");
  await audit(p, "admin-formulario-borrador");
  await p.goto(formUrl + "publicar/");
  expect(await p.locator("main").getByText("¿Qué día prefieres?").count() > 0, "the review before publishing shows the form as it will be answered");
  await audit(p, "admin-formulario-publicar");
  await go(p, "main button:has-text('Publicar así')");
  expect(await p.getByText("Publicaste el formulario.").count() > 0, "publishing is said back");
  const publicUrl = await p.locator("#enlace-publico").inputValue().catch(() => "");
  expect(/\/formulario\/[^/]+\/$/.test(publicUrl), `the published public form shows its link: ${publicUrl}`);
  const publicPath = new URL(publicUrl || "/missing/", BASE).pathname;
  await audit(p, "admin-formulario");
  await p.goto(formUrl + "lectores/");
  await p.selectOption("select[name=lector]", { label: "Quien guía el grupo Jóvenes Roca" });
  await go(p, "main button:has-text('Agregar lector')");
  expect(await p.getByText("Agregamos el lector.").count() > 0, "the guide of a group is added as a reader");
  await audit(p, "admin-formulario-lectores");

  const answers = async () => {
    await p.goto(formUrl + "respuestas/");
    return p.locator("main a[href*='/respuestas/']:not([href$='/descargar/'])").count();
  };
  // A public send needs a page that was open for at least three seconds (formularios.public.MIN_SECONDS).
  const send = async (reader, name, wait = 3500) => {
    await reader.waitForTimeout(wait);
    await reader.getByLabel("Nombre", { exact: false }).first().fill(name);
    await reader.locator("main input[type=email]").fill("visita.prueba@demo.invalid");
    await reader.locator("main input[type=checkbox]").check();
    await reader.locator("main label:has-text('Domingo')").click();
    await go(reader, "main button:has-text('Enviar mis respuestas')");
  };
  const onlyCsrf = async (context) => (await context.cookies()).map((cookie) => cookie.name).join(",") === "csrftoken";
  const anon = await anonymous();
  const reader = await anon.newPage();
  reader.on("pageerror", (e) => { if (!BENIGN_JS_ERROR.test(String(e))) problems.push(`sin cuenta: JS error ${e}`); });
  expect((await reader.goto(BASE + publicPath)).status() === 200 && await reader.locator("h1").innerText() === TITLE, "somebody without an account opens the public form");
  expect(await reader.locator("main").getByText("Para probar los formularios públicos.").count() > 0, "and reads what it is asked for");
  await audit(reader, "publico-formulario");
  await reader.setViewportSize(PHONE);
  await go(reader, "main button:has-text('Enviar mis respuestas')");
  expect(await reader.locator("h1").innerText() === TITLE && await reader.locator("main .error-message").count() >= 3, "an empty form is shown again with its errors");
  await audit(reader, "publico-formulario-errores");
  await reader.setViewportSize(PHONE);
  expect(await answers() === 0, "an empty send leaves no answer");
  await send(reader, "Visita de Prueba");
  expect(await reader.locator("h1").innerText() === "Gracias", "a valid send is thanked");
  expect(await onlyCsrf(anon), `the public form leaves only the CSRF cookie: ${(await anon.cookies()).map((cookie) => cookie.name).join(",")}`);
  await audit(reader, "publico-formulario-gracias");
  await anon.close();
  expect(await answers() === 1, "the administration reads one answer");
  const again = await anonymous();
  const second = await again.newPage();
  await second.goto(BASE + publicPath);
  await send(second, "Visita de Prueba");
  expect(await second.locator("h1").innerText() === "Gracias", "the same contact again gets the same thanks");
  // Sent at once, before the page is three seconds old: nothing is saved and what was written stays on the page.
  await second.goto(BASE + publicPath);
  await send(second, "Visita con Prisa", 0);
  expect(await second.getByText("Eso fue muy rápido y todavía no guardamos nada.").count() === 1 && await second.getByLabel("Nombre", { exact: false }).first().inputValue() === "Visita con Prisa", "a send faster than a person is told so and loses nothing typed");
  await again.close();
  // Anybody can type somebody else's e-mail, so the second answer is kept and marked; the contact leaves one task.
  expect(await answers() === 2, `the same contact twice leaves two answers, not ${await answers()}`);
  const listed = await mainText(p);
  expect((listed.match(/Mismo contacto que otra respuesta\./g) || []).length === 1 && (listed.match(/Pendiente para/g) || []).length === 1, `the second is marked «Mismo contacto» and there is one task: ${listed}`);
  await audit(p, "admin-formulario-respuestas");

  // The task of that answer: the administration gives it to the guide; he notes and closes it from «Pendientes».
  await go(p, "main li:not(:has-text('Mismo contacto')) a:has-text('Visita de Prueba')");
  const answerPath = new URL(p.url()).pathname;
  expect(await p.locator("main").getByText("visita.prueba@demo.invalid").count() > 0 && await p.locator("main").getByText("Domingo").count() > 0, "the answer shows the contact and what was chosen");
  await audit(p, "admin-formulario-respuesta");
  await go(p, "main a:has-text('responsable')");
  await audit(p, "admin-formulario-asignar");
  await p.selectOption("select[name=owner]", { label: "Gabriel Ortega Luna" });
  await go(p, "main button:has-text('Guardar cambios')");
  expect(await p.getByText("Guardamos quién da seguimiento y para cuándo.").count() > 0, "the task is assigned");
  const guide = await session("guia.grupo@demo.invalid", PHONE);
  const mine = guide.page.locator(`main li.row:has(a[href='${answerPath}'])`);
  await guide.page.goto(BASE + "/mi-iglesia/formularios/pendientes/");
  expect(await mine.count() === 1, "the guide finds the task in «Pendientes»");
  expect(!(await mainText(guide.page)).includes("Visita de Prueba") && !(await mainText(guide.page)).includes("visita.prueba"), "«Pendientes» names neither who answered nor the contact");
  await audit(guide.page, "guia-pendientes");
  await guide.page.setViewportSize(PHONE);
  await go(guide.page, `main a[href='${answerPath}']`);
  await audit(guide.page, "guia-formulario-respuesta");
  await guide.page.setViewportSize(PHONE);
  expect(await guide.page.locator("main a:has-text('responsable')").count() === 0 && await guide.page.locator("main a:has-text('Agregar a personas')").count() === 0, "a reader neither assigns nor adds people");
  await guide.page.fill("input[name=note]", "Nota de prueba.");
  await go(guide.page, "main button:has-text('Guardar la nota')");
  expect(await guide.page.getByText("Guardamos la nota.").count() > 0, "the note is saved");
  // «Marcar como hecho» acts in place: the task's region says the new state and offers the way back.
  const answerUrl = guide.page.url();
  await act(guide.page, "main button:has-text('Marcar como hecho')");
  expect(guide.page.url() === answerUrl && await guide.page.locator("[role=status]").getByText("Marcaste el pendiente como hecho.").count() > 0, "closing the task is announced, on the same page");
  expect(await guide.page.locator("#seguimiento .status").getByText("Hecho").count() === 1 && await guide.page.locator("#seguimiento button:has-text('Marcar como hecho')").count() === 0, "and the task's region shows it done");
  const download = await handPost(guide.page, new URL(formUrl).pathname + "respuestas/descargar/");
  expect(download.status === 403, `a reader's own POST to download the answers answered ${download.status}`);
  await guide.page.goto(BASE + "/mi-iglesia/formularios/pendientes/");
  expect(await mine.count() === 0, "a closed task leaves «Pendientes»");
  await guide.ctx.close();
  // A member who is an adult (the seed's Rosa; the church's forms do not exist for who its records say is a minor).
  const member = await session("rosa.rotas@demo.invalid", PHONE);
  await member.page.goto(BASE + "/mi-iglesia/formularios/");
  expect(await member.page.getByText(TITLE).count() === 0, "a public form is not on the member's list of forms");
  await audit(member.page, "miembro-formularios");
  // Who does not read a form gets the 404 of a form that does not exist (its number says nothing); the list of forms
  // of the administration is the ordinary 403.
  for (const path of [new URL(formUrl).pathname + "respuestas/", answerPath, new URL(formUrl).pathname]) {
    const response = await member.page.goto(BASE + path);
    expect(response.status() === 404, `member got ${response.status()} on ${path}`);
  }
  expect((await member.page.goto(BASE + "/mi-iglesia/formularios/administrar/")).status() === 403, "a member cannot open the forms of the administration");
  expect((await member.page.goto(BASE + "/mi-iglesia/formularios/pendientes/")).status() === 200 && await member.page.locator("main li.row").count() === 0, "a member's «Pendientes» is empty");
  await audit(member.page, "miembro-pendientes");
  await member.ctx.close();
  // Closed for good: the link answers the same 404 as one that never existed.
  await p.goto(formUrl + "cerrar/");
  await audit(p, "admin-formulario-cerrar");
  await go(p, "main button:has-text('Sí, cerrar el formulario')");
  expect(await p.getByText("Cerraste el formulario.").count() > 0, "closing the form is said back");
  const gone = await anonymous();
  expect((await gone.request.get(BASE + publicPath)).status() === 404, "the closed form's public link is a 404");
  expect((await gone.request.get(BASE + "/formulario/no-existe-este-enlace/")).status() === 404, "as is a link nobody made");
  await gone.close();
  await admin.ctx.close();
}

// 14. Updates of one event by e-mail, without an account (issue #96, second part). The offer exists only where the
// server can send (EMMAUS_PUBLIC_BASE_URL and a mail backend that delivers). The gate's server cannot, so there this
// step proves the honest side: the option is on, the staff screen says why nothing is sent, and the public page offers
// nothing. On a server that sends, it walks the section, the form and «Revisa tu correo».
{
  const SLUG = "noche-de-alabanza-demo";
  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  const p = admin.page;
  const savePage = () => go(p, "main button:has-text('Guardar la página del evento')");
  await p.goto(BASE + `/mi-iglesia/eventos/${SLUG}/pagina/`);
  expect(await p.locator("main label:has-text('Avisos por correo para quien no tiene cuenta')").count() === 1 && !(await p.isChecked("input[name=notice_mail]")), "the event's page offers the e-mail option, off");
  await p.check("input[name=public]");
  await p.check("input[name=notice_mail]");
  await savePage();
  await audit(p, "admin-evento-pagina-avisos-por-correo");
  await p.goto(BASE + `/mi-iglesia/eventos/${SLUG}/avisos-publicos/`);
  const staff = await mainText(p);
  const sends = staff.includes("direcciones suscritas");
  await audit(p, "admin-evento-avisos-publicos-correo");
  const anon = await anonymous();
  const reader = await anon.newPage();
  reader.on("pageerror", (e) => { if (!BENIGN_JS_ERROR.test(String(e))) problems.push(`sin cuenta: JS error ${e}`); });
  expect((await reader.goto(BASE + `/evento/${SLUG}/`)).status() === 200, "the public page of the event answers");
  const offer = reader.locator("main a:has-text('Recibir los avisos por correo')");  // beside the notices, or in a section of its own when there is none
  if (sends) {
    expect(staff.includes("menos de 5 direcciones suscritas"), "the staff screen says «menos de 5 direcciones suscritas», never a small number");
    expect(await offer.count() === 1, "the public page offers the updates by e-mail");
    expect((await anon.cookies()).length === 0, "and reading it leaves no cookie");
    await audit(reader, "publico-evento-avisos-por-correo");
    await reader.setViewportSize(PHONE);
    await go(reader, "main a:has-text('Recibir los avisos por correo')");
    await audit(reader, "publico-suscripcion");
    await reader.setViewportSize(PHONE);
    await reader.fill("main input[type=email]", "avisos.prueba@demo.invalid");
    await go(reader, "main button:has-text('Pedir los avisos por correo')");
    expect(await reader.locator("h1").innerText() === "Revisa tu correo", `the form lands on «Revisa tu correo», not on ${reader.url()}`);
    expect(!(await mainText(reader)).includes("avisos.prueba@demo.invalid"), "the answer does not repeat the address");
    await audit(reader, "publico-suscripcion-revisa-tu-correo");
    await p.goto(BASE + `/mi-iglesia/eventos/${SLUG}/avisos-publicos/`);
    expect((await mainText(p)).includes("menos de 5 direcciones suscritas"), "an address that was not confirmed changes nothing on the staff screen");
  } else {
    expect(staff.includes("no se envía ningún correo") && staff.includes("en la página pública no se ofrece dejar el correo"), "where the server cannot send, the staff screen says so and why");
    expect(await offer.count() === 0 && await reader.getByText("Avisos por correo").count() === 0, "and the public page does not offer what would not arrive");
    expect((await anon.cookies()).length === 0, "reading it leaves no cookie");
    expect((await reader.goto(BASE + `/evento/${SLUG}/avisos-por-correo/`)).status() === 404, "nor does the form's address answer");
  }
  const dead = await reader.goto(BASE + "/evento/avisos-por-correo/baja/un-enlace-inventado/");
  expect(dead.status() === 404 && await reader.locator("h1").innerText() === "Este enlace ya no sirve", `an invented unsubscribe link answered ${dead.status()}, not the page of a link that no longer works`);
  await audit(reader, "publico-suscripcion-enlace-muerto");
  const deadConfirm = await reader.goto(BASE + "/evento/avisos-por-correo/confirmar/un-enlace-inventado/");
  expect(deadConfirm.status() === 404 && await reader.locator("h1").innerText() === "Este enlace ya no sirve", "and so does an invented confirmation link");
  await anon.close();
  // Back to the seed: the option off and the page not public.
  await p.goto(BASE + `/mi-iglesia/eventos/${SLUG}/pagina/`);
  await p.uncheck("input[name=notice_mail]");
  await p.uncheck("input[name=public]");
  await savePage();
  expect((await p.goto(BASE + `/evento/${SLUG}/`)).status() === 404, "the event's page is not public again");
  await admin.ctx.close();
}

// 15. Serving: roles, rotation, swaps and the order of a date (issue #95). The coordinator adds a role and says a date
// needs it, proposes with the rotation and confirms one place; who got it asks a teammate to take it and he accepts;
// the coordinator writes two moments and who serves reads them without a form.
{
  const ROLE = "Sonido de prueba";
  const TEAM = "/mi-iglesia/servir/equipos/1/";
  const guide = await session("guia.grupo@demo.invalid", DESKTOP);
  const p = guide.page;
  await p.goto(BASE + TEAM);
  await audit(p, "guia-equipo-con-roles");
  const dateLinks = await p.locator("main a[href^='/mi-iglesia/servir/fechas/']").evaluateAll((links) => links.map((a) => a.getAttribute("href")).filter((href) => /fechas\/\d+\/$/.test(href)));
  const datePath = dateLinks[dateLinks.length - 1];
  const dateId = datePath.match(/(\d+)\/$/)[1];
  const dateRow = () => p.locator(`main li:has(a[href='${datePath}'])`);
  await go(p, "main a:has-text('Roles, rotación y recordatorios')");
  await audit(p, "guia-equipo-organizar");
  await p.fill("main form:has(button:has-text('Agregar el rol')) input[name=nombre]", ROLE);
  await go(p, "main button:has-text('Agregar el rol')");
  expect(await p.getByText(`Guardamos el rol ${ROLE}.`).count() > 0, "adding a role is said back");
  const roleId = await p.locator(`main form:has(input[name=nombre][value='${ROLE}']) input[name=rol]`).inputValue();
  await p.goto(BASE + datePath);
  await p.fill(`input[name=necesita_${roleId}]`, "1");
  await audit(p, "guia-fecha-con-roles");
  await go(p, "main button:has-text('Guardar la fecha')");
  await p.goto(BASE + TEAM);
  expect((await dateRow().locator(".row__meta--con-marca:has(.status:has-text('Falta'))").innerText()).replace(/\s+/g, " ").includes(`${ROLE}: 1 persona de 1`), "the team page says what the date still needs of the new role");
  await go(p, "main a:has-text('Proponer con la rotación')");
  const places = p.locator("main select[name^='lugar_']");
  const proposed = await places.evaluateAll((selects) => selects.filter((select) => select.value).length);
  expect(proposed > 0, "the rotation proposes somebody for the places that are missing");
  await audit(p, "guia-rotacion");
  // One place only, so the rest of the seed's dates stay as they were: Rosa at the door of the last date.
  for (const name of await places.evaluateAll((selects) => selects.map((select) => select.name))) await p.selectOption(`select[name='${name}']`, "");
  await p.selectOption(`select[name='lugar_${dateId}-1-0']`, { label: "Rosa Rentería Demo" });
  await go(p, "main button:has-text('Confirmar')");
  expect(await p.getByText("Le pediste servir a 1 persona.").count() > 0, "confirming the proposal says how many were asked");
  expect((await dateRow().innerText()).includes("Rosa Rentería Demo"), "and the date shows who was asked");

  const rosa = await session("rosa.rotas@demo.invalid", PHONE);
  await rosa.page.goto(BASE + "/mi-iglesia/servir/");
  await audit(rosa.page, "miembro-servir-con-rol");
  await rosa.page.setViewportSize(PHONE);
  await go(rosa.page, "main a:has-text('Pedir un cambio')");
  await audit(rosa.page, "miembro-cambio-pedir");
  await rosa.page.setViewportSize(PHONE);
  await rosa.page.locator("main label:has-text('Tomás Treviño Demo')").click();
  await go(rosa.page, "main button:has-text('Pedir el cambio')");
  expect(await rosa.page.getByText("Le pediste el cambio a Tomás.").count() > 0, "asking for the swap is said back, and that the date is still hers");
  await audit(rosa.page, "miembro-cambio-pedido");
  const tomas = await session("tomas.rotas@demo.invalid", PHONE);
  await tomas.page.goto(BASE + "/mi-iglesia/servir/");
  expect(await tomas.page.locator("main").getByText("Rosa Rentería Demo te pide tomar su fecha").count() === 1, "the teammate reads who asks him");
  await audit(tomas.page, "companero-cambio-por-responder");
  await tomas.page.setViewportSize(PHONE);
  // Accepting acts in place: the request leaves the raised surface, a line keeps the trace, «Tus fechas» gets the date.
  const hisDates = await tomas.page.locator("#fechas li.row").count();
  await act(tomas.page, "main .ask button[name=respuesta][value=aceptar]");
  await tomas.page.waitForSelector("main .hecho");
  expect(await tomas.page.locator("[role=status]").getByText("Ya está en «Tus fechas», confirmada.").count() > 0 && await tomas.page.locator("main .ask").count() === 0, "accepting is announced and the request leaves");
  expect(await tomas.page.locator("#fechas li.row").count() === hisDates + 1, "and the date is in «Tus fechas» without a new page");
  await audit(tomas.page, "companero-cambio-aceptado");
  await p.goto(BASE + TEAM);
  const after = (await dateRow().innerText()).replace(/\s+/g, " ");
  expect(after.includes("Tomás Treviño Demo") && !after.includes("Rosa Rentería Demo"), `the coordinator reads the date under its new name: ${after}`);
  await audit(p, "guia-equipo-con-cambio");

  // The order of that date: two moments, read by who serves it, with nothing to send.
  await p.goto(BASE + datePath + "orden/");
  const addMoment = async (title, minutes) => {
    const form = p.locator("main form:has(button:has-text('Agregar al orden'))");
    await form.locator("input[name=titulo]").fill(title);
    await form.locator("input[name=minutos]").fill(minutes);
    await go(p, "main button:has-text('Agregar al orden')");
  };
  await addMoment("Momento de prueba uno", "10");
  await addMoment("Momento de prueba dos", "5");
  expect(await p.getByText("Agregamos el momento al orden.").count() > 0 && await p.locator("main input[name=titulo][value^='Momento de prueba']").count() === 2, "the coordinator's order has the two moments");
  // A moment's fields open under it («Cambiar»): closed, the list reads as who serves reads it.
  const firstMoment = p.locator("main li:has-text('Momento de prueba uno')").first();
  expect(await firstMoment.locator("input[name=titulo]").isHidden(), "a moment's fields are folded until asked for");
  await firstMoment.locator("details > summary").click();
  expect(await firstMoment.locator("input[name=titulo]").isVisible() && await firstMoment.locator("input[name=titulo]").inputValue() === "Momento de prueba uno", "«Cambiar» opens the moment's own fields");
  await audit(p, "guia-orden");
  expect((await tomas.page.goto(BASE + datePath + "orden/")).status() === 200, "who serves the date opens its order");
  const order = await mainText(tomas.page);
  expect(order.includes("Momento de prueba uno") && order.includes("Momento de prueba dos") && order.indexOf("uno") < order.indexOf("dos"), "and reads the two moments in their order");
  expect(await tomas.page.locator("main form, main input, main button").count() === 0, "without a form");
  await audit(tomas.page, "miembro-orden");
  expect((await rosa.page.goto(BASE + datePath + "orden/")).status() !== 200, "who no longer serves that date does not read its order");
  await rosa.ctx.close();
  await tomas.ctx.close();
  // Back to the seed: no moments, nobody asked, the need at 0 and the role out of use.
  // «Quitar» lives where a moment is changed (inside «Cambiar»); a row of the order ends in «Subir» / «Bajar» only.
  expect(await p.locator("main .plan > li > .row__end button").count() === 2 && await p.locator("main .plan > li > .row__end .btn--quieto").count() === 0, "a moment's row ends in its place in the order, nothing else");
  for (let left = 2; left > 0; left -= 1) {
    const moment = p.locator("main li:has-text('Momento de prueba')").first();
    if (!(await moment.locator("details").evaluate((fold) => fold.open))) await moment.locator("details > summary").click();  // the first one is still open from above
    await Promise.all([p.waitForNavigation(), moment.locator("button:has-text('Quitar Momento de prueba')").click()]);
  }
  expect(await p.locator("main input[name=titulo][value^='Momento de prueba']").count() === 0, "the moments are taken out");
  await p.goto(BASE + datePath);
  for (const box of await p.locator("main input[name=sirve]:checked").all()) await box.uncheck();
  await p.fill(`input[name=necesita_${roleId}]`, "0");
  await go(p, "main button:has-text('Guardar la fecha')");
  await p.goto(BASE + TEAM + "organizar/");
  await go(p, `main button:has-text('Ya no se usa: ${ROLE}')`);
  expect(await p.getByText(`El rol ${ROLE} ya no se usa.`).count() > 0, "the role is retired");
  await p.goto(BASE + TEAM);
  const restored = (await dateRow().innerText()).replace(/\s+/g, " ");
  expect(restored.includes("Falta elegir quién sirve") && !restored.includes(ROLE), `the date is as the seed left it: ${restored}`);
  await guide.ctx.close();
}

// 16. Private prayer requests (issue #97). Only obviously synthetic words are typed here. The guide writes one to the
// pastor the form names; the administration can neither open nor find the readers' side; the pastor reads, marks and
// answers once; the guide reads the answer and deletes the request; a minor is offered nothing.
{
  const WORDS = "Texto de prueba de una petición.";
  const REPLY = "Respuesta de prueba.";
  const guide = await session("guia.grupo@demo.invalid", PHONE);
  const g = guide.page;
  await g.locator(".tabs button[data-menu-open]").click();
  expect(await g.locator("#menu-panel a[href='/mi-iglesia/oracion/']:has-text('Pedir oración')").count() === 1, "the guide's menu offers «Pedir oración»");
  await g.keyboard.press("Escape");
  await g.goto(BASE + "/mi-iglesia/oracion/");
  await audit(g, "guia-oracion");
  await g.setViewportSize(PHONE);
  await go(g, "main a:has-text('Pedir oración')");
  const form = await mainText(g);
  expect(form.includes("Lo lee: Ernesto Salas Mejía"), "the form names the pastor who reads");
  expect(form.includes("Si tú o alguien más está en peligro en este momento, llama al 911."), "and shows the 911 sentence");
  await audit(g, "guia-oracion-nueva");
  await g.setViewportSize(PHONE);
  await g.fill("textarea[name=body]", WORDS);
  await go(g, "main button:has-text('Enviar mi petición')");
  expect(await g.getByText("Guardamos tu petición.").count() > 0, "sending it is said back");
  // Her new request is the one whose text is the test's (the seed left her another one); its block holds its own delete link.
  const mineBlock = g.locator("main section.block").filter({ hasText: WORDS });
  expect(await mineBlock.count() === 1 && (await mineBlock.innerText()).includes("Todavía no la han leído"), "her page shows it with «Todavía no la han leído»");
  const deletePath = await mineBlock.locator("a[href$='/borrar/']").getAttribute("href").catch(() => "/mi-iglesia/oracion/0/borrar/");
  const id = deletePath.match(/(\d+)\/borrar\/$/)[1];
  await audit(g, "guia-oracion-enviada");

  const admin = await session("admin.comunidad@demo.invalid", DESKTOP);
  for (const path of ["/mi-iglesia/oracion/recibidas/", `/mi-iglesia/oracion/recibidas/${id}/`, "/mi-iglesia/oracion/politica/"]) {
    const response = await admin.page.goto(BASE + path);
    expect(response.status() === 404, `the administration got ${response.status()} on ${path}`);
  }
  await admin.page.goto(BASE + "/mi-iglesia/");
  expect(await admin.page.locator("a[href^='/mi-iglesia/oracion/recibidas']").count() === 0 && await admin.page.getByText("Peticiones de oración").count() === 0, "and nothing on her screen leads to the readers' side");
  await admin.ctx.close();

  const pastor = await session("pastor.iglesia@demo.invalid", DESKTOP);
  const r = pastor.page;
  const inboxLink = r.locator(".rail a[href='/mi-iglesia/oracion/recibidas/']");
  const unread = async () => Number(((await inboxLink.locator(".badge").textContent().catch(() => "")) || "").trim().match(/^\d+/)?.[0] || 0);
  const before = await unread();
  expect(await inboxLink.count() === 1 && before >= 1, `the pastor's rail counts what he has not read: ${before}`);
  await go(r, ".rail a[href='/mi-iglesia/oracion/recibidas/']");
  await audit(r, "pastor-oracion-recibidas");
  await r.setViewportSize(DESKTOP);
  await go(r, `main a[href='/mi-iglesia/oracion/recibidas/${id}/']`);
  expect((await mainText(r)).includes(WORDS), "the pastor reads the words");
  await audit(r, "pastor-oracion-leer");
  // «Lo leí» acts in place: the region says «Leída», the button leaves, the rail's count follows.
  await act(r, "main button:has-text('Lo leí')");
  expect(await r.locator("[role=status]").getByText("La marcaste como leída.").count() > 0 && await unread() === before - 1, "marking it read is announced and the rail counts one less");
  expect(await r.locator("#atendida .status").getByText("Leída").count() === 1 && await r.locator("main button:has-text('Lo leí')").count() === 0, "and the page says «Leída», with nothing left to press for it");
  await r.fill("textarea[name=reply]", REPLY);
  await go(r, "main button:has-text('Enviar mi respuesta')");
  expect(await r.getByText("Guardamos tu respuesta.").count() > 0, "the reply is saved");
  expect(await r.locator("main textarea[name=reply]").count() === 0 && (await mainText(r)).includes(REPLY), "there is one reply: the form is gone and the reply is shown");
  await audit(r, "pastor-oracion-respondida");
  const twice = await handPost(r, `/mi-iglesia/oracion/recibidas/${id}/responder/`, { reply: "Otra respuesta de prueba." });
  await r.goto(BASE + `/mi-iglesia/oracion/recibidas/${id}/`);
  expect(!(await mainText(r)).includes("Otra respuesta de prueba.") && (await mainText(r)).includes(REPLY), `a second reply written by hand (it answered ${twice.status}) changes nothing`);

  await g.goto(BASE + "/mi-iglesia/oracion/");
  const answered = (await mineBlock.innerText().catch(() => "")).replace(/\s+/g, " ");
  expect(answered.includes("Leída") && answered.includes(REPLY) && !answered.includes("Todavía no la han leído"), `the guide reads «Leída» and the reply: ${answered}`);
  await audit(g, "guia-oracion-respondida");
  await g.setViewportSize(PHONE);
  await go(g, `main a[href='${deletePath}']`);
  await audit(g, "guia-oracion-borrar");
  await go(g, "main form button[type=submit]");
  expect(await g.getByText("Borramos tu petición y su respuesta.").count() > 0 && !(await mainText(g)).includes(WORDS), "the guide deletes it, with its reply");
  expect((await r.goto(BASE + `/mi-iglesia/oracion/recibidas/${id}/`)).status() === 404, "and the pastor can no longer open it");
  await r.goto(BASE + "/mi-iglesia/oracion/recibidas/");
  expect(await unread() === before - 1 && !(await mainText(r)).includes(WORDS), "the pastor's list is as the seed left it");
  await pastor.ctx.close();
  await guide.ctx.close();
  // The demo campera is 17: the feature does not exist for her.
  const minor = await session("valeria.soto@demo.invalid", PHONE);
  for (const path of ["/mi-iglesia/oracion/", "/mi-iglesia/oracion/nueva/"]) {
    const response = await minor.page.goto(BASE + path);
    expect(response.status() === 404, `a minor got ${response.status()} on ${path}`);
  }
  await minor.page.goto(BASE + "/mi-iglesia/");
  await minor.page.locator(".tabs button[data-menu-open]").click();
  expect(await minor.page.locator("#menu-panel a[href^='/mi-iglesia/oracion/']").count() === 0, "and her menu does not offer it");
  await minor.ctx.close();
}
await browser.close();

fs.writeFileSync(`${out}/results.json`, JSON.stringify({ checks, problems }, null, 2));
if (problems.length) {
  console.log(problems.join("\n"));
  console.log(`community flow FAILED: ${problems.length} problem(s) in ${checks} checks`);
  process.exit(1);
}
console.log(`community flow OK: ${checks} checks, 0 problems`);
