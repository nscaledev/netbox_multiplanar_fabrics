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
  const buttons = await page.locator('button,input[type="submit"],a.btn').evaluateAll(nodes => nodes.map(n => ({tag:n.tagName, type:n.getAttribute('type'), text:(n.innerText||n.value||'').trim(), name:n.getAttribute('name'), id:n.id, cls:n.className})));
  console.log(JSON.stringify(buttons, null, 2));
  await browser.close();
})();
