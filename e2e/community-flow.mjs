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
  ["admin-recurso-nuevo", "/mi-iglesia/recursos/nuevo/"], ["admin-equipos", "/mi-iglesia/servir/equipos/"],
  ["admin-equipo", "/mi-iglesia/servir/equipos/1/"], ["admin-equipo-nuevo", "/mi-iglesia/servir/equipos/nuevo/"],
  ["admin-seguimientos", "/mi-iglesia/acompanar/todos/"], ["admin-seguimiento-nuevo", "/mi-iglesia/acompanar/nuevo/"],
  ["admin-panorama", "/mi-iglesia/panorama/"], ["admin-evento-preguntas", "/mi-iglesia/eventos/noche-de-alabanza-demo/preguntas/"],
  ["admin-evento-programa", "/mi-iglesia/eventos/noche-de-alabanza-demo/programa/"],
  ["admin-evento-opiniones", "/mi-iglesia/opiniones/noche-de-alabanza-demo/resultados/"],
];
for (const [name, path] of adminPages) {
  const response = await page.goto(BASE + path);
  expect(response.status() === 200, `admin got ${response.status()} on ${path}`);
  await audit(page, name);
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
await page.goto(BASE + "/mi-iglesia/eventos/noche-de-alabanza-demo/inscritos/llego/");
expect(await page.getByText("todavía no empieza").count() > 0 || (await page.locator("main form").count()) > 0, "the walk-up page answers");
await audit(page, "admin-evento-llego");
await ctx.close();
await browser.close();

fs.writeFileSync(`${out}/results.json`, JSON.stringify({ checks, problems }, null, 2));
if (problems.length) {
  console.log(problems.join("\n"));
  console.log(`community flow FAILED: ${problems.length} problem(s) in ${checks} checks`);
  process.exit(1);
}
console.log(`community flow OK: ${checks} checks, 0 problems`);
