// Whole-app review: every screen per role at three widths, with axe, overflow, h1 count and stylesheet checks.
// Usage: AXE=node_modules/axe-core/axe.min.js EMMAUS_URL=http://127.0.0.1:8000 node review-all.mjs <urls.json> <outdir>
// (urls.json from e2e/all_urls.py; synthetic data only). Fails the run when any page has a problem.
import { chromium } from "playwright";
import fs from "node:fs";

const [, , urlsFile, out] = process.argv;
const BASE = process.env.EMMAUS_URL || "http://127.0.0.1:8000";
const PASS = "DemoCamp-2026";
const AXE = fs.readFileSync(process.env.AXE, "utf8");
const pages = JSON.parse(fs.readFileSync(urlsFile, "utf8"));
const WIDTHS = { desktop: { width: 1440, height: 900 }, tablet: { width: 820, height: 1180 }, mobile: { width: 390, height: 844 } };
fs.mkdirSync(out, { recursive: true });
const results = [];

const browser = await chromium.launch();
for (const [email, list] of Object.entries(pages)) {
  const ctx = await browser.newContext({ viewport: WIDTHS.desktop, locale: "es-MX", timezoneId: "America/Mexico_City", bypassCSP: true });
  const page = await ctx.newPage();
  const jsErrors = [];
  page.on("pageerror", (e) => jsErrors.push(String(e)));
  if (email) {
    await page.goto(BASE + "/cuenta/entrar/");
    await page.fill("input[name=username]", email);
    await page.fill("input[name=password]", PASS);
    await Promise.all([page.waitForNavigation(), page.click("form button:has-text('Entrar')")]);
  }
  for (const [name, path] of list) {
    if (name === "x-404") continue; // DEBUG renders Django's debug 404, not ours
    const resp = await page.goto(BASE + path);
    await page.waitForTimeout(350);
    for (const [label, vp] of Object.entries(WIDTHS)) {
      await page.setViewportSize(vp);
      await page.waitForTimeout(250);
      await page.screenshot({ path: `${out}/${name}-${label}.png` });
      await page.screenshot({ path: `${out}/${name}-${label}-full.png`, fullPage: true });
      await page.addScriptTag({ content: AXE });
      const info = await page.evaluate(async () => {
        const r = await axe.run(document, { runOnly: ["wcag2a", "wcag2aa", "wcag21a", "wcag21aa", "wcag22aa"] });
        return {
          violations: r.violations.map((v) => ({ id: v.id, n: v.nodes.length, sample: v.nodes[0].target.join(" ") })),
          overflow: document.documentElement.scrollWidth - window.innerWidth,
          h1: document.querySelectorAll("h1").length,
          rotulo: !!document.querySelector('link[href*="css/rotulo"]'),
          old: !!document.querySelector('link[href*="emmaus.css"]'),
        };
      });
      results.push({ email, name, path, width: label, status: resp.status(), ...info });
    }
    // station choice for coordinators happens once, after capturing the chooser itself
    if (name === "c-computadora") {
      await page.setViewportSize(WIDTHS.desktop);
      await page.check("input[name=station] >> nth=3");
      await Promise.all([page.waitForNavigation(), page.click("form button:has-text('Continuar')")]);
    }
  }
  if (jsErrors.length) results.push({ email, jsErrors });
  await ctx.close();
}
await browser.close();
fs.writeFileSync(`${out}/results.json`, JSON.stringify(results, null, 2));
const bad = results.filter((r) => r.jsErrors || r.status !== 200 || r.violations?.length || r.overflow > 0 || r.h1 !== 1 || !r.rotulo || r.old);
console.log(`${results.length} page/width checks; ${bad.length} with a problem`);
for (const r of bad) console.log(JSON.stringify(r));
// The 403 probe (a church admin opening event administration) is expected to be a 403.
if (bad.some((r) => !(r.name === "d-403" && r.status === 403 && !r.violations?.length && r.overflow <= 0))) process.exitCode = 1;
