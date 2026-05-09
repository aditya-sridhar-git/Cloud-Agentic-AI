const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto('http://localhost:8080', { waitUntil: 'networkidle2' });
  await page.screenshot({ path: 'dashboard.png' });
  await browser.close();
})();
