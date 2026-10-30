// Screenshot di una pagina in preview, dopo il login con l'utente demo.
// Uso: NODE_PATH=$(npm root -g) node .claude/preview/screenshot.cjs / out.png [larghezza] [altezza]
const { chromium } = require('playwright');

const [route = '/', out = 'preview.png', width = '1280', height = '800'] = process.argv.slice(2);
const BASE = 'http://localhost:3000';

(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: +width, height: +height } });
  await page.goto(`${BASE}/login`);
  await page.fill('input[type=email]', 'demo@trading.local');
  await page.fill('input[type=password]', 'demo12345');
  await page.click('button[type=submit]');
  await page.waitForURL(`${BASE}/`);
  if (route !== '/') await page.goto(BASE + route);
  await page.waitForLoadState('networkidle');
  await page.waitForTimeout(800);
  await page.screenshot({ path: out, fullPage: true });
  await browser.close();
})();
