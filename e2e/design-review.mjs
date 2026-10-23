// Design review: captures every redesigned screen at desktop / tablet / mobile and, optionally,
// runs axe-core on each one. Synthetic data only (docs/ux-review.md).
//
//   cd e2e && npm install && npx playwright install chromium
//   DEBUG=true python manage.py seed_camp_demo --reset --password DemoCamp-2026
//   EMMAUS_URL=http://127.0.0.1:8000 AXE=node_modules/axe-core/axe.min.js npm run review -- /tmp/capturas raul.ochoa@demo.invalid
//
// Reseed before each run: the flow opens and finishes interviews, so the dashboards change.
import { chromium } from "playwright";
import fs from "node:fs";

const BASE = process.env.EMMAUS_URL || "http://127.0.0.1:8000";
const S = "/eventos/jovenes-2026";
const PASS = process.env.EMMAUS_PASSWORD || "DemoCamp-2026";
const out = process.argv[2];
const coord = process.argv[3] || "ana.lopez@demo.invalid";
fs.mkdirSync(out, { recursive: true });

const WIDTHS = {
  desktop: { width: 1440, height: 900 },
  tablet: { width: 820, height: 1180 },
  mobile: { width: 390, height: 844 },
};
const go = (page, action) => Promise.all([page.waitForNavigation(), action]);
const next = (page) => go(page, page.click("button[name=direction][value=next]:not([tabindex='-1'])"));
const errors = [];
const AXE = process.env.AXE ? fs.readFileSync(process.env.AXE, "utf8") : null;
const axeReport = {};
async function audit(page, key) {
  if (!AXE) return;
  await page.addScriptTag({ content: AXE });
  axeReport[key] = await page.evaluate(async () => (await axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] }))
    .violations.map((v) => ({ id: v.id, impact: v.impact, n: v.nodes.length, sample: v.nodes[0].target.join(" "), html: v.nodes[0].html.slice(0, 160) })));
}

async function login(browser, email) {
  const ctx = await browser.newContext({ viewport: WIDTHS.desktop, locale: "es-MX", timezoneId: "America/Mexico_City", deviceScaleFactor: 1, bypassCSP: true });
  const page = await ctx.newPage();
  page.on("pageerror", (e) => errors.push(String(e)));
  await page.goto(BASE + "/cuenta/entrar/");
  await page.fill("input[name=username]", email);
  await page.fill("input[name=password]", PASS);
  await go(page, page.click("form button:has-text('Entrar')"));
  return page;
}

async function everyWidth(page, name, { full = true } = {}) {
  for (const [label, vp] of Object.entries(WIDTHS)) {
    await page.setViewportSize(vp);
    await page.waitForTimeout(250);
    await page.screenshot({ path: `${out}/${name}-${label}.png`, fullPage: false });
    if (full) await page.screenshot({ path: `${out}/${name}-${label}-full.png`, fullPage: true });
    await audit(page, `${name}-${label}`);
  }
  await page.setViewportSize(WIDTHS.desktop);
  console.log("captured", name);
}

async function startFromHome(page) {
  await page.goto(BASE + S + "/mi-inicio/");
  await go(page, page.click(".coord__queue a:has-text('Entrevistar') >> nth=0"));
}

const browser = await chromium.launch();
try {
  let page = await login(browser, coord);
  // Register the station first, as a coordinator does at the start of the day
  await page.goto(BASE + S + "/entrevistas/iniciar/");
  if (page.url().includes("/computadora/")) {
    await page.check("input[name=station] >> nth=3");
    await go(page, page.click("form button:has-text('Continuar')"));
  }
  await page.goto(BASE + S + "/mi-inicio/");
  await everyWidth(page, "coordinador");
  await page.goto(BASE + S + "/entrevistas/iniciar/?q=" + encodeURIComponent("mat"));
  await everyWidth(page, "busqueda");

  // Interview: start (and its error state) -> questions -> review -> done
  await startFromHome(page);
  await everyWidth(page, "entrevista-antes");
  await go(page, page.click("button:has-text('Comenzar entrevista')"));
  await everyWidth(page, "entrevista-antes-error", { full: false });
  for (const box of await page.$$("input[type=checkbox][name^=consent_]")) await box.check();
  await page.click("label[for$='mode_0']");
  await go(page, page.click("button:has-text('Comenzar entrevista')"));
  await everyWidth(page, "entrevista-paso1", { full: false });
  await page.click("label:has-text('Sí') >> nth=0");
  await next(page);
  await page.click("label:has-text('Sí') >> nth=0");
  await page.fill("textarea", "Me cuesta entender cómo orar.");
  await everyWidth(page, "entrevista-pregunta");
  await next(page);
  await page.click("label:has-text('Familia')");
  await everyWidth(page, "entrevista-multiple", { full: false });
  await next(page);
  await page.click("label:has-text('Quiero seguir aprendiendo')");
  await next(page);
  while (!page.url().includes("/revision/")) await next(page);
  await everyWidth(page, "entrevista-revision");
  await page.click("label[for^='decision-'] >> nth=0");
  await go(page, page.click("button:has-text('Finalizar entrevista')"));
  await everyWidth(page, "entrevista-completada", { full: false });

  // Supervised capture: the campero answers alone on the shared computer
  await startFromHome(page);
  for (const box of await page.$$("input[type=checkbox][name^=consent_]")) await box.check();
  await page.click("label[for$='mode_1']");
  await go(page, page.click("button:has-text('Comenzar entrevista')"));
  await everyWidth(page, "entrega", { full: false });
  await go(page, page.click("button:has-text('Entregar la computadora')"));
  await everyWidth(page, "campero-bienvenida", { full: false });
  await go(page, page.click("a:has-text('Empezar')"));
  await page.click("label:has-text('No estoy seguro/a')");
  await everyWidth(page, "campero-pregunta", { full: false });
  await next(page);
  await go(page, page.click("a:has-text('Terminar por ahora')"));
  await everyWidth(page, "campero-revision", { full: false });
  await go(page, page.click("button:has-text('Terminar')"));
  await everyWidth(page, "campero-gracias", { full: false });
  await go(page, page.click("a:has-text('recuperar la computadora')"));
  await everyWidth(page, "campero-recuperar", { full: false });
  await page.fill("#password", PASS);
  await go(page, page.click("button:has-text('Recuperar la computadora')"));

  // Back with the coordinator: the interview is still open, so the sign says so
  await go(page, page.click("a:has-text('Guardar y salir')"));
  await page.goto(BASE + S + "/mi-inicio/");
  await everyWidth(page, "coordinador-encurso", { full: false });

  // Pastor
  page = await login(browser, "ernesto.salas@demo.invalid");
  await page.goto(BASE + S + "/resumen/");
  await everyWidth(page, "pastor");
} finally {
  await browser.close();
  if (errors.length) console.log("JS errors:", errors);
  if (AXE) {
    fs.writeFileSync(`${out}/axe.json`, JSON.stringify(axeReport, null, 2));
    const bad = Object.entries(axeReport).filter(([, v]) => v.length);
    console.log(`axe: ${Object.keys(axeReport).length} screen/width combinations, ${bad.length} with violations`);
    for (const [k, v] of bad) console.log(k, JSON.stringify(v));
  }
}
