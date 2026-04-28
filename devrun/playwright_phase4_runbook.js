const fs = require('fs');
const path = require('path');
const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');

const BASE_URL = process.env.NETBOX_BASE_URL || 'http://127.0.0.1:8000';
const USERNAME = process.env.NETBOX_ADMIN_USERNAME || 'admin';
const PASSWORD = process.env.NETBOX_ADMIN_PASSWORD;
const OUTPUT_DIR =
  process.env.PHASE4_SCREENSHOT_DIR ||
  path.join(process.cwd(), 'docs/images/runbook-roce-phase4-ui');

if (!PASSWORD) {
  throw new Error('NETBOX_ADMIN_PASSWORD must be set');
}

fs.mkdirSync(OUTPUT_DIR, { recursive: true });

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

async function chooseTomSelectOption(page, controlSelector, dropdownSelector, label) {
  const control = page.locator(controlSelector);
  await control.click({ force: true });
  await control.fill(label);
  await page.locator(dropdownSelector).filter({ hasText: label }).first().click();
  await page.locator('body').click({ position: { x: 20, y: 20 }, force: true });
}

async function setInputValue(page, selector, value) {
  await page.evaluate(
    ({ localSelector, localValue }) => {
      const input = document.querySelector(localSelector);
      if (!input) {
        throw new Error(`Missing input ${localSelector}`);
      }
      input.value = localValue;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      input.dispatchEvent(new Event('change', { bubbles: true }));
    },
    { localSelector: selector, localValue: value },
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

async function createPlatform(page, platform) {
  await page.goto(`${BASE_URL}/dcim/platforms/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Name').fill(platform.name);
  await setInputValue(page, '#id_slug', platform.slug);
  await chooseTomSelectOption(
    page,
    '#id_manufacturer-ts-control',
    '#id_manufacturer-ts-dropdown .option',
    platform.manufacturer,
  );
  await screenshot(page, `${platform.screenshotPrefix}-form.png`);
  await clickPrimarySubmit(page);
  await readyPage(page);
  await screenshot(page, `${platform.screenshotPrefix}-detail.png`);
}

async function main() {
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  }

  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });

  const platforms = [
    {
      name: 'GPU Host OS',
      slug: 'gpu-host-os',
      manufacturer: 'Generic',
      screenshotPrefix: 'phase4-gpu-host-os',
    },
    {
      name: 'RoCE Switch NOS',
      slug: 'roce-nos',
      manufacturer: 'Generic',
      screenshotPrefix: 'phase4-roce-nos',
    },
    {
      name: 'Frontside NOS',
      slug: 'frontside-nos',
      manufacturer: 'Generic',
      screenshotPrefix: 'phase4-frontside-nos',
    },
  ];

  try {
    await login(page);
    for (const platform of platforms) {
      await createPlatform(page, platform);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
