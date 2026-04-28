const fs = require('fs');
const path = require('path');
const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');

const BASE_URL = process.env.NETBOX_BASE_URL || 'http://127.0.0.1:8000';
const USERNAME = process.env.NETBOX_ADMIN_USERNAME || 'admin';
const PASSWORD = process.env.NETBOX_ADMIN_PASSWORD;
const OUTPUT_DIR = process.env.PHASE1_SCREENSHOT_DIR || path.join(process.cwd(), 'docs/images/runbook-roce-phase1');

if (!PASSWORD) {
  throw new Error('NETBOX_ADMIN_PASSWORD must be set');
}

fs.mkdirSync(OUTPUT_DIR, { recursive: true });

function slugify(value) {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}

async function screenshot(page, name) {
  const file = path.join(OUTPUT_DIR, name);
  await page.screenshot({ path: file, fullPage: true });
  console.log(`saved ${file}`);
}

async function dismissToolbar(page) {
  const hideButton = page.locator('#djHideToolBarButton');
  if (await hideButton.count()) {
    try {
      await hideButton.first().click({ timeout: 1500 });
      await page.waitForTimeout(250);
    } catch (error) {
      // Ignore cases where the toolbar is already hidden or not interactable.
    }
  }
}

async function readyPage(page) {
  await page.waitForLoadState('networkidle');
  await dismissToolbar(page);
}

async function login(page) {
  await page.goto(`${BASE_URL}/login/?next=/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Username').fill(USERNAME);
  await page.getByLabel('Password').fill(PASSWORD);
  await screenshot(page, 'phase1-login.png');
  await page.locator('form button[type="submit"]').first().click();
  await readyPage(page);
}

async function openAddTenant(page) {
  await page.goto(`${BASE_URL}/tenancy/tenants/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
}

async function clickPrimarySubmit(page) {
  await page.locator('form button[type="submit"].btn-primary').last().click();
}

async function createTenant(page, { name, slug, description, screenshotPrefix }) {
  await openAddTenant(page);
  await page.getByLabel('Name').fill(name);
  await page.locator('button.reslug').click();
  await page.waitForFunction(
    ({ expectedSlug }) => {
      const input = document.querySelector('#id_slug');
      return !!input && input.value === expectedSlug;
    },
    { expectedSlug: slug },
  );
  await page.getByLabel('Description').fill(description);
  await screenshot(page, `${screenshotPrefix}-form.png`);
  await clickPrimarySubmit(page);
  await readyPage(page);
  await screenshot(page, `${screenshotPrefix}-detail.png`);
  return page.url();
}

async function createTenantGroup(page, { name, tenantNames, tenantDetailUrls, screenshotPrefix }) {
  await page.goto(`${BASE_URL}/tenancy/tenant-groups/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Name').fill(name);
  await screenshot(page, `${screenshotPrefix}-form.png`);
  await clickPrimarySubmit(page);
  await readyPage(page);
  await screenshot(page, `${screenshotPrefix}-detail.png`);

  for (const tenantName of tenantNames) {
    const tenantDetailUrl = tenantDetailUrls?.[tenantName];
    if (!tenantDetailUrl) {
      throw new Error(`Could not resolve detail URL for tenant ${tenantName}`);
    }
    await page.goto(`${tenantDetailUrl.replace(/\/$/, '')}/edit/`, { waitUntil: 'networkidle' });
    await dismissToolbar(page);
    const groupInput = page.locator('#id_group-ts-control');
    await groupInput.click();
    await groupInput.fill(name);
    await page.locator('#id_group-ts-dropdown .option').filter({ hasText: name }).first().click();
    await screenshot(page, `${screenshotPrefix}-${slugify(tenantName)}-edit.png`);
    await clickPrimarySubmit(page);
    await readyPage(page);
    await screenshot(page, `${screenshotPrefix}-${slugify(tenantName)}-updated.png`);
  }
}

async function main() {
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });

  try {
    await login(page);
    await createTenant(page, {
      name: 'Operator',
      slug: 'operator',
      description: 'Infrastructure owner for shared resources',
      screenshotPrefix: 'phase1-step10-operator-tenant',
    });
    const tenantAlphaUrl = await createTenant(page, {
      name: 'Tenant Alpha',
      slug: 'tenant-alpha',
      description: 'GPU workload consumer',
      screenshotPrefix: 'phase1-step11-tenant-alpha',
    });
    const tenantBetaUrl = await createTenant(page, {
      name: 'Tenant Beta',
      slug: 'tenant-beta',
      description: 'GPU workload consumer',
      screenshotPrefix: 'phase1-step11-tenant-beta',
    });
    await createTenantGroup(page, {
      name: 'GPU Workload Tenants',
      tenantNames: ['Tenant Alpha', 'Tenant Beta'],
      tenantDetailUrls: {
        'Tenant Alpha': tenantAlphaUrl,
        'Tenant Beta': tenantBetaUrl,
      },
      screenshotPrefix: 'phase1-step12-tenant-group',
    });
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
