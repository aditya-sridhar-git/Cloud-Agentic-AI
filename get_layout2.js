const puppeteer = require('puppeteer');

(async () => {
  const browser = await puppeteer.launch();
  const page = await browser.newPage();
  await page.setViewport({ width: 1280, height: 800 });
  await page.goto('http://localhost:8080/dashboard', { waitUntil: 'networkidle2' });
  
  try {
    await page.waitForSelector('.inst-tile', { timeout: 5000 });
  } catch(e) {}
  
  const layout = await page.evaluate(() => {
    const getRect = (selector) => {
      const el = document.querySelector(selector);
      if (!el) return null;
      const rect = el.getBoundingClientRect();
      return { id: selector, top: rect.top, bottom: rect.bottom, height: rect.height, left: rect.left };
    };
    return [
      getRect('#panel-thoughts'),
      getRect('#panel-instances'),
      getRect('.instance-comparison'),
      getRect('.inst-tile'),
      getRect('.instance-grid')
    ];
  });
  
  console.log(JSON.stringify(layout, null, 2));
  await browser.close();
})();
