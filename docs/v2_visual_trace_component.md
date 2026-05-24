# V2 Visual Trace Component

The visual trace component is the shared rendering shell used by:

- `Path Query`
- `Interface Fanout Trace`

It preserves the existing operator-facing SVG behavior while removing duplicate
card, control, asset, and JSON payload markup from the two pages.

## Reusable Pieces

Template includes:

- `templates/netbox_plant_graph/includes/visual_trace_styles.html`
- `templates/netbox_plant_graph/includes/visual_trace_scripts.html`
- `templates/netbox_plant_graph/includes/visual_trace_card.html`

Static renderer:

- `static/netbox_plant_graph/fanout_trace.js`
- `static/netbox_plant_graph/fanout_trace.css`

The card include owns:

- the visual trace card wrapper,
- the show/hide button,
- the SVG export button,
- the SVG mount point,
- the schematic path JSON payload,
- the schematic stage JSON payload,
- a deterministic `data-fanout-fixture-key`.

The JavaScript renderer still owns:

- SVG drawing,
- collapsible visual section behavior,
- collapsible fanout summary sections,
- SVG export,
- object hyperlinks,
- tooltip behavior,
- cable assembly cylinder/braces,
- lane coloring and legend rendering.

## Page-Specific Inputs

Each page still prepares its own data:

- `Path Query` resolves one selected source/destination path and passes
  `visual_path_trace_json` plus `visual_path_trace_stages_json`.
- `Interface Fanout Trace` resolves expanded or consolidated per-interface
  paths and passes `aggregate_schematic_paths_json` plus
  `aggregate_schematic_stage_connectors_json`. It also renders a
  page-specific Transceiver Context card for the selected OSFP interface when a
  matching NetBox module bay/module, semantic `TransceiverProfile`, or plugin
  `TransceiverConnector` binding exists.

Page templates decide:

- card title,
- body ID,
- trace mode,
- source title and source URL,
- warning copy for identity shuffle mappings,
- any non-SVG summary/context cards, including transceiver module/profile
  details.

The shared SVG renderer currently depicts the optical-lane, MPO connector,
fiber-position, cable-assembly, shuffle, and destination hierarchy. It links to
represented objects when URLs are present in the payload. The installed
transceiver module/profile itself is surfaced in the Interface Fanout Trace
context card, not yet as a dedicated SVG layer.

## Test Hooks

Every reusable visual trace card renders:

```html
data-visual-trace-component="mpf-visual-trace"
data-fanout-fixture-key="path-query"
```

or:

```html
data-fanout-fixture-key="interface-fanout"
```

After JavaScript renders, the schematic container receives:

```html
data-fanout-rendered="true"
data-fanout-render-signature="path-query|expanded|paths:1|stages:4|cables:1"
```

The exact counts vary with fixture data, but the signature is deterministic for
a fixed server-side payload and is intended for future Playwright/golden tests.
Rendered SVG elements also expose inert counting hooks for regression tests:

```html
data-fanout-visual-section="true"
data-fanout-connector-frame="true"
class="fanout-cable-assembly-cylinder"
class="fanout-object-link"
```

The public browser hook is:

```javascript
window.NetBoxPlantGraphVisualTrace
```

with:

- `initialize()`
- `render(container)`
- `exportedSvgMarkup(svg)`
- `renderedSvgCounts(svgOrRoot)`
- `exportSanity(svg)`
- `payloadCounts(container)`
- `renderSignature(container)`
- `goldenSnapshot(container)`

The diagnostic helpers are intentionally read-only. They return deterministic
counts for section shells, connector frames, cable cylinders, object links,
payload path/stage/cable-span totals, and standalone export sanity checks
without changing renderer behavior.

`goldenSnapshot(container)` renders the fixture if needed, then returns a
deterministic object with:

- render signature,
- payload counts,
- rendered SVG counts,
- export sanity flags,
- viewBox, width, and height.

This is the bridge between the current Node harness and a future browser
golden-regression job.

## Regression Harness

`netbox_plant_graph.tests.test_v2_visual_trace_component` is the current
lightweight contract harness. It does not try to pixel-match the schematic, but
it does catch the highest-risk breakages:

- `node --check` syntax validation for `fanout_trace.js` when Node is available.
- Required reusable template hooks for the card, SVG mount, payload scripts,
  collapse button, and SVG export button.
- Shared asset include/version strings.
- Public browser API hooks, render signatures, SVG export support, object link
  support, tooltip installation, and cable assembly overlay rendering.
- Node-backed diagnostic API probes for deterministic payload counts, rendered
  SVG counts, export dimensions, retained hyperlinks, tooltip/title metadata,
  visible content hooks, and graceful unlinked-object export.
- A rendered fixture golden-snapshot probe that asserts deterministic signature,
  nonzero dimensions, section/connector/cable/link counts, and standalone SVG
  export sanity.

Future browser/golden tests should build on this instead of replacing it:
assert rendered section counts, link counts, cable-cylinder counts, nonzero SVG
geometry, and exported standalone SVG content against deterministic fixture
payloads.

## Manual Checks

For any visual trace change, manually verify:

1. Path Query renders the same visual path trace.
2. Interface Fanout Trace renders expanded mode.
3. Interface Fanout Trace renders consolidated mode.
4. Interface Fanout Trace shows the selected OSFP's transceiver context when a
   module/profile/connector binding exists, and degrades cleanly when it does
   not.
5. Show/hide still toggles the visual card body.
6. Sub-interface summary and matrix sections still collapse independently.
7. Export SVG downloads a usable SVG.
8. SVG object links still navigate to endpoint, position, interface, and cable
   assembly objects.
9. Cable assembly cylinders and brace labels still align with their lane bundle.

The component is not a contract for machine scraping. External automation
should use REST or GraphQL contracts documented in
`docs/v2_external_contracts.md`.
