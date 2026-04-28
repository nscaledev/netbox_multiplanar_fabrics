const fs = require('fs');
const path = require('path');
const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');

const BASE_URL = process.env.NETBOX_BASE_URL || 'http://127.0.0.1:8000';
const USERNAME = process.env.NETBOX_ADMIN_USERNAME || 'admin';
const PASSWORD = process.env.NETBOX_ADMIN_PASSWORD;
const OUTPUT_DIR =
  process.env.PHASE6_SCREENSHOT_DIR ||
  path.join(process.cwd(), 'docs/images/runbook-roce-phase6-ui');
const ONLY_SECTION = process.env.PHASE6_ONLY || null;

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

async function chooseTomSelectOption(page, controlSelector, dropdownSelector, label) {
  const control = page.locator(controlSelector);
  await control.click({ force: true });
  await control.fill(label);
  await page.locator(dropdownSelector).filter({ hasText: label }).first().click();
  await page.locator('body').click({ position: { x: 20, y: 20 }, force: true });
}

async function chooseSelectOption(page, selectSelector, controlSelector, dropdownSelector, label) {
  const choseViaDom = await page.evaluate(
    ({ localSelectSelector, localLabel }) => {
      const select = document.querySelector(localSelectSelector);
      if (!select) {
        return false;
      }
      const option = Array.from(select.options).find(
        (candidate) => candidate.textContent.trim() === localLabel,
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
    { localSelectSelector: selectSelector, localLabel: label },
  );

  if (choseViaDom) {
    await page.waitForTimeout(300);
    await page.locator('body').click({ position: { x: 20, y: 20 }, force: true });
    return;
  }

  await chooseTomSelectOption(page, controlSelector, dropdownSelector, label);
}

async function selectNativeOption(page, selector, value) {
  await page.locator(selector).selectOption(value);
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

async function createRack(page, rack) {
  await page.goto(`${BASE_URL}/dcim/racks/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await chooseSelectOption(page, '#id_site', '#id_site-ts-control', '#id_site-ts-dropdown .option', rack.site);
  await chooseSelectOption(
    page,
    '#id_location',
    '#id_location-ts-control',
    '#id_location-ts-dropdown .option',
    rack.location,
  );
  await page.getByLabel('Name').fill(rack.name);
  await selectNativeOption(page, '#id_status', 'active');
  await selectNativeOption(page, '#id_form_factor', '4-post-cabinet');
  await selectNativeOption(page, '#id_width', '19');
  await setInputValue(page, '#id_u_height', '42');
  await setInputValue(page, '#id_outer_width', '600');
  await selectNativeOption(page, '#id_outer_unit', 'mm');
  if (rack.tenant) {
    await chooseSelectOption(
      page,
      '#id_tenant',
      '#id_tenant-ts-control',
      '#id_tenant-ts-dropdown .option',
      rack.tenant,
    );
  }
  if (rack.captureForm) {
    await screenshot(page, `${rack.screenshotPrefix}-form.png`);
  }
  await clickPrimarySubmit(page);
  await readyPage(page);
  if (rack.captureDetail) {
    await screenshot(page, `${rack.screenshotPrefix}-detail.png`);
  }
}

function buildComputeRacks() {
  const halls = [
    { code: 'A', location: 'Hall Compute A' },
    { code: 'B', location: 'Hall Compute B' },
    { code: 'C', location: 'Hall Compute C' },
  ];

  const racks = [];
  for (const hall of halls) {
    racks.push(
      { name: `RACK-${hall.code}-GPU-A1`, site: 'DC Alpha', location: hall.location, tenant: 'Tenant Alpha' },
      { name: `RACK-${hall.code}-GPU-A2`, site: 'DC Alpha', location: hall.location, tenant: 'Tenant Alpha' },
      { name: `RACK-${hall.code}-GPU-B1`, site: 'DC Alpha', location: hall.location, tenant: 'Tenant Beta' },
      { name: `RACK-${hall.code}-GPU-B2`, site: 'DC Alpha', location: hall.location, tenant: 'Tenant Beta' },
      { name: `RACK-${hall.code}-SHUFFLE-1`, site: 'DC Alpha', location: hall.location, tenant: null },
      { name: `RACK-${hall.code}-ROCE-LEAF-P1`, site: 'DC Alpha', location: hall.location, tenant: null },
      { name: `RACK-${hall.code}-ROCE-LEAF-P2`, site: 'DC Alpha', location: hall.location, tenant: null },
      { name: `RACK-${hall.code}-ROCE-LEAF-P3`, site: 'DC Alpha', location: hall.location, tenant: null },
      { name: `RACK-${hall.code}-ROCE-LEAF-P4`, site: 'DC Alpha', location: hall.location, tenant: null },
      { name: `RACK-${hall.code}-FS-LEAF-1`, site: 'DC Alpha', location: hall.location, tenant: null },
    );
  }
  racks[0].captureForm = true;
  racks[0].captureDetail = true;
  racks[0].screenshotPrefix = 'phase6-step61-rack-a-gpu-a1';
  return racks;
}

function buildNetworkRacks() {
  const names = [
    'RACK-NET-ROCE-SHUFFLE-P1',
    'RACK-NET-ROCE-SHUFFLE-P2',
    'RACK-NET-ROCE-SHUFFLE-P3',
    'RACK-NET-ROCE-SHUFFLE-P4',
    'RACK-NET-ROCE-SPINE-P1-A',
    'RACK-NET-ROCE-SPINE-P1-B',
    'RACK-NET-ROCE-SPINE-P2-A',
    'RACK-NET-ROCE-SPINE-P2-B',
    'RACK-NET-ROCE-SPINE-P3-A',
    'RACK-NET-ROCE-SPINE-P3-B',
    'RACK-NET-ROCE-SPINE-P4-A',
    'RACK-NET-ROCE-SPINE-P4-B',
    'RACK-NET-FS-SPINE-1',
    'RACK-NET-FS-SPINE-2',
    'RACK-NET-MGMT-1',
    'RACK-NET-EDGE-1',
  ];

  const racks = names.map((name) => ({
    name,
    site: 'DC Alpha',
    location: 'Hall Network',
    tenant: null,
  }));
  racks[0].captureForm = true;
  racks[0].captureDetail = true;
  racks[0].screenshotPrefix = 'phase6-step62-rack-net-roce-shuffle-p1';
  return racks;
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
    if (!ONLY_SECTION || ONLY_SECTION === 'compute') {
      for (const rack of buildComputeRacks()) {
        await createRack(page, rack);
      }
    }
    if (!ONLY_SECTION || ONLY_SECTION === 'network') {
      for (const rack of buildNetworkRacks()) {
        await createRack(page, rack);
      }
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
