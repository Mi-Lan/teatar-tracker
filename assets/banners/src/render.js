// Renders banners.html (made by build.py) to ../<name>.jpg, one per <div id="name">.
const path = require('path');
const { chromium } = require('playwright');
(async () => {
  const browser = await chromium.launch();
  const page = await browser.newPage({ viewport: { width: 1200, height: 900 }, deviceScaleFactor: 1 });
  await page.goto('file://' + path.join(__dirname, 'banners.html'));
  await page.waitForTimeout(600);
  for (const id of await page.$$eval('body > div[id]', (els) => els.map((e) => e.id))) {
    await page.locator(`#${id} > .b`).screenshot({ path: path.join(__dirname, '..', `${id}.jpg`), type: 'jpeg', quality: 90 });
    console.log('rendered', id);
  }
  await browser.close();
})();
