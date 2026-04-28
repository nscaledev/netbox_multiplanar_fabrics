const fs = require('fs');
const path = require('path');
const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');

const BASE_URL = process.env.NETBOX_BASE_URL || 'http://127.0.0.1:8000';
const USERNAME = process.env.NETBOX_ADMIN_USERNAME || 'admin';
const PASSWORD = process.env.NETBOX_ADMIN_PASSWORD;
const OUTPUT_DIR =
  process.env.PHASE3_SCREENSHOT_DIR ||
  path.join(process.cwd(), 'docs/images/runbook-roce-phase3-ui');

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

async function clickPrimarySubmit(page) {
  await page.locator('form button[type="submit"].btn-primary').last().click();
}

async function clickReslug(page, slug) {
  await page.locator('button.reslug').click();
  await page.waitForFunction(
    ({ expectedSlug }) => {
      const input = document.querySelector('#id_slug');
      return !!input && input.value === expectedSlug;
    },
    { expectedSlug: slug },
  );
}

async function login(page) {
  await page.goto(`${BASE_URL}/login/?next=/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Username').fill(USERNAME);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.locator('form button[type="submit"]').first().click();
  await readyPage(page);
}

async function createDeviceRole(page, role) {
  await page.goto(`${BASE_URL}/dcim/device-roles/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Name').fill(role.name);
  await clickReslug(page, role.slug);
  await page.locator('#id_color').selectOption(role.color);

  const vmRoleCheckbox = page.locator('#id_vm_role');
  if (await vmRoleCheckbox.isChecked()) {
    await vmRoleCheckbox.uncheck();
  }

  await screenshot(page, `${role.screenshotPrefix}-form.png`);
  await clickPrimarySubmit(page);
  await readyPage(page);
  await screenshot(page, `${role.screenshotPrefix}-detail.png`);
}

async function main() {
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });

  const roles = [
    { name: 'GPU Server', slug: 'gpu-server', color: '00bcd4' },
    { name: 'GPU-Leaf Shuffle Board', slug: 'gpu-leaf-shuffle-board', color: '607d8b' },
    { name: 'RoCE Leaf Switch', slug: 'roce-leaf-switch', color: '4caf50' },
    { name: 'RoCE Spine Switch', slug: 'roce-spine-switch', color: '8bc34a' },
    { name: 'Frontside Leaf Switch', slug: 'frontside-leaf-switch', color: 'ff9800' },
    { name: 'Frontside Spine Switch', slug: 'frontside-spine-switch', color: 'ff5722' },
    { name: 'Management Switch', slug: 'management-switch', color: '9e9e9e' },
    { name: 'Edge Router', slug: 'edge-router', color: '795548' },
  ].map((role) => ({
    ...role,
    screenshotPrefix: `phase3-step3-${slugify(role.slug)}`,
  }));

  try {
    await login(page);
    for (const role of roles) {
      await createDeviceRole(page, role);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
