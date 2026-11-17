// The camp-to-group journey in a browser, on synthetic data (seed_community_demo): a former campero signs in on a
// phone, finds a group and asks to join; the guide accepts; she sees her group and its next meeting. Then every
// community and people screen per role at phone and desktop widths, with axe, overflow, h1 and JS-error checks.
// Usage: AXE=node_modules/axe-core/axe.min.js EMMAUS_URL=http://127.0.0.1:8000 EMMAUS_PASSWORD=... node community-flow.mjs <outdir>
import { chromium } from "playwright";
import fs from "node:fs";

const out = process.argv[2] || "community-out";
const BASE = process.env.EMMAUS_URL || "http://127.0.0.1:8000";
const PASS = process.env.EMMAUS_PASSWORD || "DemoCamp-2026";
const AXE = fs.readFileSync(process.env.AXE || "node_modules/axe-core/axe.min.js", "utf8");
const BENIGN_JS_ERROR = /InvalidStateError: Transition was aborted because of invalid state/; // issue #16
const PHONE = { width: 390, height: 844 };
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
  for (const [label, vp] of [["movil", PHONE], ["escritorio", DESKTOP]]) {
    await page.setViewportSize(vp);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${out}/${name}-${label}.png`, fullPage: true });
    await page.addScriptTag({ content: AXE });
    const info = await page.evaluate(async () => {
      const r = await axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] });
      const small = [...document.querySelectorAll("main a.btn, main button")].filter((el) => {
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
await Promise.all([page.waitForNavigation(), page.click("a:has-text('Ver los grupos')")]);
await audit(page, "miembro-grupos");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("a.row__link:has-text('Jóvenes Roca')")]);
expect(await page.getByText("Salón 2 de la iglesia").count() === 0, "the meeting place is hidden from a non-member");
await audit(page, "miembro-grupo-antes");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("button:has-text('Pedir unirme')")]);
expect(await page.getByText("Ya pediste unirte.").count() > 0, "request confirmed on screen");
expect(await page.getByText("le avisamos por correo").count() === 0, "no e-mail is claimed when none can be sent");
await audit(page, "miembro-grupo-pedido");
for (const path of ["/mi-iglesia/personas/", "/mi-iglesia/grupos/administrar/"]) {
  const response = await page.goto(BASE + path);
  expect(response.status() === 403, `member got ${response.status()} on ${path}`);
}
await ctx.close();

// 2. The guide: the panel names the pending requests; accept Valeria.
({ ctx, page } = await session("guia.grupo@demo.invalid", DESKTOP));
expect((await page.locator(".sign__title").innerText()).startsWith("¿Recibes a 2 personas"), "guide's next step");
await audit(page, "guia-inicio");
await Promise.all([page.waitForNavigation(), page.click("a:has-text('Ver solicitudes')")]);
await audit(page, "guia-personas");
await Promise.all([page.waitForNavigation(), page.click("li:has-text('Valeria Soto') button[value=aceptar]")]);
expect(await page.getByText("Valeria Soto Marín ya es parte de Jóvenes Roca.").count() > 0, "acceptance message");
expect((await page.goto(BASE + "/mi-iglesia/personas/")).status() === 403, "guide cannot open the directory");
await ctx.close();

// 3. Valeria again: her group, the next meeting, and now the place.
({ ctx, page } = await session("valeria.soto@demo.invalid", PHONE));
expect(await page.getByText("Próxima reunión: jueves").count() > 0, "home shows the next meeting");
await audit(page, "miembro-inicio-con-grupo");
await page.setViewportSize(PHONE);
await Promise.all([page.waitForNavigation(), page.click("a:has-text('Abrir')")]);
expect(await page.getByText("Salón 2 de la iglesia").count() > 0, "a member sees where the group meets");
await audit(page, "miembro-grupo-despues");
await ctx.close();

// 4. The administration: every screen.
({ ctx, page } = await session("admin.comunidad@demo.invalid", DESKTOP));
const adminPages = [
  ["admin-inicio", "/mi-iglesia/"], ["admin-grupos", "/mi-iglesia/grupos/administrar/"], ["admin-grupo-nuevo", "/mi-iglesia/grupos/nuevo/"],
  ["admin-personas", "/mi-iglesia/personas/"], ["admin-persona-nueva", "/mi-iglesia/personas/nueva/"],
  ["admin-eventos", "/mi-iglesia/personas/desde-un-evento/"], ["admin-evento", "/mi-iglesia/personas/desde-un-evento/jovenes-2025/"],
  ["admin-cuenta", "/cuenta/"],
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
await Promise.all([page.waitForNavigation(), page.click("a:has-text('Personas'):near(:text('Jóvenes Roca'))")]);
await audit(page, "admin-grupo-personas");
await ctx.close();
await browser.close();

fs.writeFileSync(`${out}/results.json`, JSON.stringify({ checks, problems }, null, 2));
if (problems.length) {
  console.log(problems.join("\n"));
  console.log(`community flow FAILED: ${problems.length} problem(s) in ${checks} checks`);
  process.exit(1);
}
console.log(`community flow OK: ${checks} checks, 0 problems`);
