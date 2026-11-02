// Renders the brand kit PNGs from tools/kit.html.
// Serve the mkb/ folder first (e.g. `python3 -m http.server 8799` from mkb/), then: node tools/export-kit.js
const { chromium } = require('playwright');
const path = require('path');
const BASE = process.env.KIT_BASE || 'http://localhost:8799/tools/kit.html';
const pub = (p) => path.join(__dirname, '..', 'public', p);
const ONLY = process.argv[2];
const JOBS = [
  ['profile', 'assets/kit/mkb-profile-1080.png'], ['xheader', 'assets/kit/mkb-x-header-1500x500.png'], ['igpost', 'assets/kit/mkb-ig-post-1080x1350.png'],
  ['story', 'assets/kit/mkb-story-1080x1920.png'], ['cardfront', 'assets/kit/mkb-card-front.png'], ['cardback', 'assets/kit/mkb-card-back.png'],
  ['og', 'assets/img/og-image.png'], ['icon', 'assets/img/icon-512.png'], ['icon', 'assets/img/icon-192.png', 192], ['icon', 'assets/img/apple-touch-icon.png', 180],
  ['mark1024', 'assets/kit/mkb-mark-1024.png', null, true], ['logoh', 'assets/kit/mkb-logo-horizontal.png', null, true], ['logohl', 'assets/kit/mkb-logo-horizontal-light.png', null, true],
];
(async () => {
  const browser = await chromium.launch();
  for (const [item, out, size, transparent] of JOBS.filter((j) => !ONLY || ONLY.split(',').includes(j[0]))) {
    const page = await browser.newPage({ viewport: { width: 2400, height: 2000 }, deviceScaleFactor: size ? size / 512 : 1 });
    await page.goto(`${BASE}?item=${item}`, { waitUntil: 'networkidle' });
    await page.waitForSelector('body[data-ready="1"]', { timeout: 20000 });
    await page.locator('#art').screenshot({ path: pub(out), omitBackground: !!transparent });
    console.log('wrote', out);
    await page.close();
  }
  await browser.close();
})();
