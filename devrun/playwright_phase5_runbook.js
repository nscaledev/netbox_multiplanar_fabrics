const fs = require('fs');
const path = require('path');
const { chromium } = require('../.tmp/playwright-run/node_modules/playwright');

const BASE_URL = process.env.NETBOX_BASE_URL || 'http://127.0.0.1:8000';
const USERNAME = process.env.NETBOX_ADMIN_USERNAME || 'admin';
const PASSWORD = process.env.NETBOX_ADMIN_PASSWORD;
const OUTPUT_DIR =
  process.env.PHASE5_SCREENSHOT_DIR ||
  path.join(process.cwd(), 'docs/images/runbook-roce-phase5-ui');

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

async function openComponentForm(page, url) {
  await page.goto(url, { waitUntil: 'domcontentloaded' });
  await page.waitForTimeout(750);
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

async function selectNativeOption(page, selector, value) {
  await page.locator(selector).selectOption(value);
  await page.locator('body').click({ position: { x: 20, y: 20 }, force: true });
}

async function setCheckbox(page, selector, checked) {
  const checkbox = page.locator(selector);
  if ((await checkbox.isChecked()) !== checked) {
    if (checked) {
      await checkbox.check();
    } else {
      await checkbox.uncheck();
    }
  }
}

async function login(page) {
  await page.goto(`${BASE_URL}/login/?next=/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await page.getByLabel('Username').fill(USERNAME);
  await page.getByLabel('Password').fill(PASSWORD);
  await page.locator('form button[type="submit"]').first().click();
  await readyPage(page);
}

async function createDeviceType(page, config) {
  await page.goto(`${BASE_URL}/dcim/device-types/add/`, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await chooseTomSelectOption(
    page,
    '#id_manufacturer-ts-control',
    '#id_manufacturer-ts-dropdown .option',
    config.manufacturer,
  );
  if (config.defaultPlatform) {
    await chooseTomSelectOption(
      page,
      '#id_default_platform-ts-control',
      '#id_default_platform-ts-dropdown .option',
      config.defaultPlatform,
    );
  }
  await page.getByLabel('Model').fill(config.model);
  await setInputValue(page, '#id_slug', config.slug);
  await setInputValue(page, '#id_u_height', String(config.uHeight));
  if (config.hasOwnProperty('isFullDepth')) {
    await setCheckbox(page, '#id_is_full_depth', config.isFullDepth);
  }
  if (config.captureForm) {
    await screenshot(page, `${config.screenshotPrefix}-device-type-form.png`);
  }
  await clickPrimarySubmit(page);
  await readyPage(page);
  return page.url();
}

function extractPkFromUrl(url) {
  const match = url.match(/\/(\d+)\/?$/);
  if (!match) {
    throw new Error(`Could not extract PK from ${url}`);
  }
  return match[1];
}

async function addInterfaceTemplate(page, config) {
  await openComponentForm(page, `${BASE_URL}/dcim/interface-templates/add/?device_type=${config.deviceTypePk}`);
  await setInputValue(page, '#id_name', config.name);
  await selectNativeOption(page, '#id_type', config.type);
  await setCheckbox(page, '#id_mgmt_only', !!config.mgmtOnly);
  if (config.captureForm) {
    await screenshot(page, `${config.screenshotPrefix}-interface-template-form.png`);
  }
  await clickPrimarySubmit(page);
  await readyPage(page);
}

async function addRearPortTemplate(page, config) {
  await openComponentForm(page, `${BASE_URL}/dcim/rear-port-templates/add/?device_type=${config.deviceTypePk}`);
  await setInputValue(page, '#id_name', config.name);
  await selectNativeOption(page, '#id_type', config.type);
  await setInputValue(page, '#id_positions', String(config.positions));
  if (config.captureForm) {
    await screenshot(page, `${config.screenshotPrefix}-rear-port-form.png`);
  }
  await clickPrimarySubmit(page);
  await readyPage(page);
}

async function addFrontPortTemplate(page, config) {
  await openComponentForm(page, `${BASE_URL}/dcim/front-port-templates/add/?device_type=${config.deviceTypePk}`);
  await setInputValue(page, '#id_name', config.name);
  await selectNativeOption(page, '#id_type', config.type);
  await setInputValue(page, '#id_positions', String(config.positions));
  await page.evaluate(
    ({ rearPortName, rearPosition }) => {
      const select = document.querySelector('#id_rear_ports');
      if (!select) {
        throw new Error('Missing #id_rear_ports');
      }
      const option = Array.from(select.options).find(
        (candidate) =>
          candidate.textContent.includes(rearPortName) &&
          candidate.value.endsWith(`:${rearPosition}`),
      );
      if (!option) {
        throw new Error(`Missing rear port mapping ${rearPortName}:${rearPosition}`);
      }
      option.selected = true;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    },
    { rearPortName: config.rearPortName, rearPosition: config.rearPosition },
  );
  if (config.captureForm) {
    await screenshot(page, `${config.screenshotPrefix}-front-port-form.png`);
  }
  await clickPrimarySubmit(page);
  await readyPage(page);
}

async function captureFinalDetail(page, detailUrl, screenshotName) {
  await page.goto(detailUrl, { waitUntil: 'networkidle' });
  await dismissToolbar(page);
  await screenshot(page, screenshotName);
}

async function main() {
  const launchOptions = { headless: true };
  if (process.env.PLAYWRIGHT_EXECUTABLE_PATH) {
    launchOptions.executablePath = process.env.PLAYWRIGHT_EXECUTABLE_PATH;
  }
  const browser = await chromium.launch(launchOptions);
  const page = await browser.newPage({ viewport: { width: 1440, height: 1200 } });

  const deviceTypes = [
    {
      key: 'gpu',
      model: 'GPU-Server-8x800G',
      slug: 'gpu-server-8x800g',
      manufacturer: 'Generic',
      defaultPlatform: 'GPU Host OS',
      uHeight: 2,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step51-gpu-server-8x800g',
      captureForm: true,
      interfaces: [
        { name: 'NIC0', type: '800gbase-cr8', mgmtOnly: false, captureForm: true },
        { name: 'NIC1', type: '800gbase-cr8', mgmtOnly: false, captureForm: false },
        { name: 'mgmt0', type: '1000base-t', mgmtOnly: true, captureForm: false },
      ],
    },
    {
      key: 'roce-leaf',
      model: 'RoCE-Leaf-Plane-200G',
      slug: 'roce-leaf-200g-plane',
      manufacturer: 'Generic',
      defaultPlatform: 'RoCE Switch NOS',
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step52-roce-leaf-200g-plane',
      captureForm: true,
      interfaces: [
        ...Array.from({ length: 16 }, (_, index) => ({
          name: `Eth1/${index + 1}`,
          type: '200gbase-cr4',
          mgmtOnly: false,
          captureForm: index === 0,
        })),
        { name: 'Eth1/49', type: '800gbase-cr8', mgmtOnly: false, captureForm: false },
      ],
    },
    {
      key: 'roce-spine',
      model: 'RoCE-Spine-Plane-200G',
      slug: 'roce-spine-200g-plane',
      manufacturer: 'Generic',
      defaultPlatform: 'RoCE Switch NOS',
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step53-roce-spine-200g-plane',
      captureForm: true,
      interfaces: Array.from({ length: 6 }, (_, index) => ({
        name: `Eth1/${index + 1}`,
        type: '200gbase-cr4',
        mgmtOnly: false,
        captureForm: index === 0,
      })),
    },
    {
      key: 'gpu-shuffle',
      model: 'GPU-Leaf-Shuffle-1x4',
      slug: 'gpu-leaf-shuffle-1x4',
      manufacturer: 'Generic',
      defaultPlatform: null,
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step54-gpu-leaf-shuffle-1x4',
      captureForm: true,
      rearPorts: [{ name: 'bp1', type: 'mpo', positions: 4, captureForm: true }],
      frontPorts: [
        { name: 'fp1', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 1, captureForm: true },
        { name: 'fp2', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 2, captureForm: false },
        { name: 'fp3', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 3, captureForm: false },
        { name: 'fp4', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 4, captureForm: false },
      ],
    },
    {
      key: 'leaf-spine-shuffle',
      model: 'Leaf-Spine-Shuffle-1x4',
      slug: 'leaf-spine-shuffle-1x4',
      manufacturer: 'Generic',
      defaultPlatform: null,
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step55-leaf-spine-shuffle-1x4',
      captureForm: true,
      rearPorts: [{ name: 'bp1', type: 'mpo', positions: 4, captureForm: true }],
      frontPorts: [
        { name: 'fp1', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 1, captureForm: true },
        { name: 'fp2', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 2, captureForm: false },
        { name: 'fp3', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 3, captureForm: false },
        { name: 'fp4', type: 'mpo', positions: 1, rearPortName: 'bp1', rearPosition: 4, captureForm: false },
      ],
    },
    {
      key: 'frontside-leaf',
      model: 'Frontside-Leaf-Generic',
      slug: 'frontside-leaf-generic',
      manufacturer: 'Generic',
      defaultPlatform: 'Frontside NOS',
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step56-frontside-leaf-generic',
      captureForm: true,
    },
    {
      key: 'frontside-spine',
      model: 'Frontside-Spine-Generic',
      slug: 'frontside-spine-generic',
      manufacturer: 'Generic',
      defaultPlatform: 'Frontside NOS',
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step56-frontside-spine-generic',
      captureForm: true,
    },
    {
      key: 'management-switch',
      model: 'Management-Switch-Generic',
      slug: 'management-switch-generic',
      manufacturer: 'Generic',
      defaultPlatform: null,
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step56-management-switch-generic',
      captureForm: true,
    },
    {
      key: 'edge-router',
      model: 'Edge-Router-Generic',
      slug: 'edge-router-generic',
      manufacturer: 'Generic',
      defaultPlatform: null,
      uHeight: 1,
      isFullDepth: true,
      screenshotPrefix: 'phase5-step56-edge-router-generic',
      captureForm: true,
    },
  ];

  try {
    await login(page);

    for (const config of deviceTypes) {
      const detailUrl = await createDeviceType(page, config);
      const deviceTypePk = extractPkFromUrl(detailUrl);

      for (const interfaceTemplate of config.interfaces || []) {
        await addInterfaceTemplate(page, {
          ...interfaceTemplate,
          deviceTypePk,
          screenshotPrefix: config.screenshotPrefix,
        });
      }

      for (const rearPortTemplate of config.rearPorts || []) {
        await addRearPortTemplate(page, {
          ...rearPortTemplate,
          deviceTypePk,
          screenshotPrefix: config.screenshotPrefix,
        });
      }

      for (const frontPortTemplate of config.frontPorts || []) {
        await addFrontPortTemplate(page, {
          ...frontPortTemplate,
          deviceTypePk,
          screenshotPrefix: config.screenshotPrefix,
        });
      }

      await captureFinalDetail(page, detailUrl, `${config.screenshotPrefix}-detail.png`);
    }
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
