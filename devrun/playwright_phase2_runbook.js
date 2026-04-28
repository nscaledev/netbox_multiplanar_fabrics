const fs = require('fs');
const path = require('path');
const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');

const BASE_URL = process.env.NETBOX_BASE_URL || 'http://127.0.0.1:8000';
const USERNAME = process.env.NETBOX_ADMIN_USERNAME || 'admin';
const PASSWORD = process.env.NETBOX_ADMIN_PASSWORD;
const OUTPUT_DIR =
  process.env.PHASE2_SCREENSHOT_DIR ||
  path.join(process.cwd(), 'docs/images/runbook-roce-phase2-ui');
const ONLY_STEP = process.env.PHASE2_ONLY || null;

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

async function chooseSelectOption(page, selectSelector, controlSelector, dropdownSelector, value) {
  const choseViaDom = await page.evaluate(
    ({ selectSelector: localSelectSelector, value: localValue }) => {
      const select = document.querySelector(localSelectSelector);
      if (!select) {
        return false;
      }

      const option = Array.from(select.options).find(
        (candidate) => candidate.textContent.trim() === localValue,
      );
      if (!option) {
        return false;
      }

      if (select.tomselect) {
        select.tomselect.setValue(option.value);
      } else {
        select.value = option.value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      }

      return true;
    },
    { selectSelector, value },
  );

  if (choseViaDom) {
    await page.waitForTimeout(300);
    try {
      await page.keyboard.press('Escape');
    } catch (error) {
      // Ignore keyboard focus problems; the click below also closes active overlays.
    }
    await page.locator('body').click({ position: { x: 20, y: 20 }, force: true });
    return;
  }

  const control = page.locator(controlSelector);
  await control.click({ force: true });
  await control.fill(value);
  await page.locator(dropdownSelector).filter({ hasText: value }).first().click();
  await page.waitForTimeout(300);
  await page.locator('body').click({ position: { x: 20, y: 20 }, force: true });
}

async function login(page) {
  await page.goto(`${BASE_URL}/login/?next=/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Username').fill(USERNAME);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.locator('form button[type="submit"]').first().click();
  await readyPage(page);
}

async function createSite(page, { name, slug, screenshotPrefix }) {
  await page.goto(`${BASE_URL}/dcim/sites/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Name').fill(name);
  await clickReslug(page, slug);

  const statusInput = page.locator('#id_status');
  if (await statusInput.count()) {
    try {
      await statusInput.selectOption({ label: 'Active' });
    } catch (error) {
      // NetBox may already default this field to Active or render it differently.
    }
  }

  await screenshot(page, `${screenshotPrefix}-form.png`);
  await clickPrimarySubmit(page);
  await readyPage(page);
  await screenshot(page, `${screenshotPrefix}-detail.png`);
  return page.url();
}

async function createLocation(page, { name, slug, siteName, parentName, description, screenshotPrefix }) {
  await page.goto(`${BASE_URL}/dcim/locations/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Name').fill(name);
  await clickReslug(page, slug);
  await chooseSelectOption(
    page,
    '#id_site',
    '#id_site-ts-control',
    '#id_site-ts-dropdown .option',
    siteName,
  );

  if (parentName) {
    await chooseSelectOption(
      page,
      '#id_parent',
      '#id_parent-ts-control',
      '#id_parent-ts-dropdown .option',
      parentName,
    );
  }

  if (description) {
    await page.getByLabel('Description').fill(description);
  }

  await screenshot(page, `${screenshotPrefix}-form.png`);
  await clickPrimarySubmit(page);
  await readyPage(page);
  await screenshot(page, `${screenshotPrefix}-detail.png`);
  return page.url();
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

    const shouldRun = (stepName) => !ONLY_STEP || ONLY_STEP === stepName;

    if (shouldRun('site')) {
      await createSite(page, {
        name: 'DC Alpha',
        slug: 'dc-alpha',
        screenshotPrefix: 'phase2-step21-site',
      });
    }
    if (shouldRun('building-1')) {
      await createLocation(page, {
        name: 'Building 1',
        slug: 'building-1',
        siteName: 'DC Alpha',
        parentName: null,
        description: '',
        screenshotPrefix: 'phase2-step22-building-1',
      });
    }

    const hallDefinitions = [
      {
        name: 'Hall Compute A',
        slug: 'hall-compute-a',
        description: 'Compute hall 1 (GPU workloads)',
      },
      {
        name: 'Hall Compute B',
        slug: 'hall-compute-b',
        description: 'Compute hall 2 (GPU workloads)',
      },
      {
        name: 'Hall Compute C',
        slug: 'hall-compute-c',
        description: 'Compute hall 3 (GPU workloads)',
      },
      {
        name: 'Hall Network',
        slug: 'hall-network',
        description: 'Network-dedicated hall',
      },
    ];

    for (const hall of hallDefinitions) {
      if (!shouldRun(hall.slug)) {
        continue;
      }
      await createLocation(page, {
        name: hall.name,
        slug: hall.slug,
        siteName: 'DC Alpha',
        parentName: 'Building 1',
        description: hall.description,
        screenshotPrefix: `phase2-step23-${slugify(hall.name)}`,
      });
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
