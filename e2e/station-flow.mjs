// Browser end-to-end check of the station workflow. Synthetic data only.
//
//   cd e2e && npm install && npx playwright install chromium
//   python manage.py seed_camp_demo --reset --password DemoCamp-2026   # DEBUG=true
//   EMMAUS_URL=http://127.0.0.1:8000 EMMAUS_USER=jorge.pineda@demo.invalid EMMAUS_PASSWORD=DemoCamp-2026 npm test
//
// Optional: SHOTS_DIR=/tmp/shots saves a screenshot of every step.
import assert from "node:assert/strict";
import { chromium } from "playwright";

const BASE = process.env.EMMAUS_URL || "http://127.0.0.1:8000";
const EVENT = process.env.EMMAUS_EVENT || "jovenes-2026";
const USER = process.env.EMMAUS_USER || "jorge.pineda@demo.invalid";
const PASSWORD = process.env.EMMAUS_PASSWORD;
const SHOTS = process.env.SHOTS_DIR;
assert.ok(PASSWORD, "Define EMMAUS_PASSWORD");

let n = 0;
const shot = async (page, name) => { if (SHOTS) await page.screenshot({ path: `${SHOTS}/${String(++n).padStart(2, "0")}-${name}.png`, fullPage: true }); };
const go = (page, action) => Promise.all([page.waitForNavigation(), action]);

const browser = await chromium.launch();
const page = await (await browser.newContext({ viewport: { width: 1280, height: 900 }, locale: "es-MX" })).newPage();
const jsErrors = [];
page.on("pageerror", (e) => jsErrors.push(String(e)));

// Login
await page.goto(`${BASE}/cuenta/entrar/`);
assert.equal(await page.getAttribute("html", "lang"), "es-MX");
await page.fill("input[name=username]", USER);
await page.fill("input[name=password]", PASSWORD);
await go(page, page.click("form button:has-text('Entrar')"));

// Station choice happens before the first interview
await page.goto(`${BASE}/eventos/${EVENT}/entrevistas/iniciar/`);
if (page.url().includes("/computadora/")) {
  await page.check("input[name=station] >> nth=0");
  await go(page, page.click("form button:has-text('Usar esta computadora')"));
}
await shot(page, "buscar");

// Assisted interview
await go(page, page.click(".results a:has-text('Entrevistar') >> nth=0"));
await go(page, page.click("button:has-text('Comenzar entrevista')"));
assert.match(await page.title(), /^Error: /, "missing consent shows the error summary");
for (const box of await page.$$("input[type=checkbox][name^=consent_]")) await box.check();
await page.check("input[name=mode][value=asistida]");
await go(page, page.click("button:has-text('Comenzar entrevista')"));
assert.match(page.url(), /\/paso\/1\/$/);
await page.click("label:has-text('No lo tengo claro')");
await go(page, page.click(".step__actions button.btn--primary"));
await page.click("label:has-text('Sí') >> nth=0");
assert.ok(await page.isVisible("textarea"), "conditional question is revealed");
await page.fill("textarea", "Texto sintético de prueba.");
await go(page, page.click(".step__actions button.btn--primary"));
await page.click("label:has-text('Familia')");
await page.click("label:has-text('Prefiero no responder')");
assert.equal(await page.isChecked("input[value=familia]"), false, "exclusive option clears the others");
await go(page, page.click(".step__actions button.btn--primary"));
await go(page, page.click(".step__actions button.btn--primary"));
await go(page, page.click(".step__actions button.btn--primary"));
assert.match(page.url(), /\/revision\/$/);
await shot(page, "revision");
await go(page, page.click("button:has-text('Finalizar entrevista')"));
assert.match(await page.title(), /^Error: /, "a follow-up decision is required");
await page.check("input[name=follow_up_decision][value=no_requerido]");
await go(page, page.click("button:has-text('Finalizar entrevista')"));
assert.ok(await page.isVisible("text=Entrevista completada"));
await shot(page, "completada");

// Supervised capture with the next camper
await go(page, page.click("a:has-text('Entrevistar a otro campero')"));
await go(page, page.click(".results a:has-text('Entrevistar') >> nth=0"));
for (const box of await page.$$("input[type=checkbox][name^=consent_]")) await box.check();
await page.check("input[name=mode][value=supervisada]");
await go(page, page.click("button:has-text('Comenzar entrevista')"));
await go(page, page.click("button:has-text('Entregar la computadora')"));
assert.match(page.url(), /\/estacion\/$/);
await shot(page, "estacion");

// The camper cannot leave the station screens
await page.goto(`${BASE}/eventos/${EVENT}/mi-inicio/`);
assert.match(page.url(), /\/estacion\/$/, "kiosk confinement");
await page.goto(`${BASE}/admin/`);
assert.match(page.url(), /\/estacion\/$/, "kiosk confinement (admin)");

await go(page, page.click(".welcome a.btn--primary"));
await page.click("label:has-text('Sí') >> nth=0");
await go(page, page.click(".step__actions button.btn--primary"));
await go(page, page.click("button:has-text('Parar por ahora')"));
await go(page, page.click("button:has-text('Ya terminé')"));
const handback = await page.textContent("main");
assert.ok(handback.includes("Avisa a tu coordinador"));
assert.ok(!handback.includes("Sí"), "no answers on the hand-back screen");

// Unlock
await go(page, page.click("a:has-text('recuperar la computadora')"));
await page.fill("#password", "incorrecta");
await go(page, page.click("button:has-text('Recuperar la computadora')"));
assert.ok(await page.isVisible("text=La contraseña no es correcta."));
await page.fill("#password", PASSWORD);
await go(page, page.click("button:has-text('Recuperar la computadora')"));
assert.match(page.url(), /\/revision\/$/, "unlock returns to the coordinator review");
await page.check("input[name=follow_up_decision][value=por_evaluar]");
await go(page, page.click("button:has-text('Finalizar entrevista')"));
assert.ok(await page.isVisible("text=Entrevista completada"));

assert.deepEqual(jsErrors, [], "no JavaScript errors");
await browser.close();
console.log("OK: flujo de estación completo (asistida + supervisada)");
