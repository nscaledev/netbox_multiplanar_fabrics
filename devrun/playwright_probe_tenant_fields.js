const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');
(async () => {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });
  const dismiss = async () => { const hide = page.locator('#djHideToolBarButton'); if (await hide.count()) { try { await hide.first().click({ timeout: 1500 }); await page.waitForTimeout(250); } catch {} } };
  await page.goto('http://127.0.0.1:8000/login/?next=/', { waitUntil: 'networkidle' });
  await dismiss();
  await page.getByLabel('Username').fill('admin');
  await page.getByLabel('Password').fill('Ig49XIWlQIzmzYYSaYEGKOvc');
  await page.locator('form button[type="submit"]').first().click();
  await page.waitForLoadState('networkidle');
  await dismiss();
  await page.goto('http://127.0.0.1:8000/tenancy/tenants/add/', { waitUntil: 'networkidle' });
  await dismiss();
  const fields = await page.locator('form input, form select, form textarea').evaluateAll(nodes => nodes.map(n => ({tag:n.tagName, type:n.getAttribute('type'), name:n.getAttribute('name'), id:n.id, required:n.required, value:n.value, ariaLabel:n.getAttribute('aria-label'), placeholder:n.getAttribute('placeholder')})));
  console.log(JSON.stringify(fields, null, 2));
  await browser.close();
})();
