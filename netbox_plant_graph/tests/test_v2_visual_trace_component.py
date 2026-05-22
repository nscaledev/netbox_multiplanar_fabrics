from pathlib import Path
import shutil
import subprocess
import textwrap

from django.test import SimpleTestCase


REPO_ROOT = Path(__file__).parents[2]
FANOUT_TRACE_JS = REPO_ROOT / 'netbox_plant_graph' / 'static' / 'netbox_plant_graph' / 'fanout_trace.js'
VISUAL_TRACE_CARD = (
    REPO_ROOT / 'netbox_plant_graph' / 'templates' / 'netbox_plant_graph' / 'includes' / 'visual_trace_card.html'
)
VISUAL_TRACE_SCRIPTS = (
    REPO_ROOT / 'netbox_plant_graph' / 'templates' / 'netbox_plant_graph' / 'includes' / 'visual_trace_scripts.html'
)
VISUAL_TRACE_STYLES = (
    REPO_ROOT / 'netbox_plant_graph' / 'templates' / 'netbox_plant_graph' / 'includes' / 'visual_trace_styles.html'
)


class V2VisualTraceComponentContractTestCase(SimpleTestCase):
    def run_renderer_node_probe(self, probe_source):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not available for fanout_trace.js diagnostic probing')

        harness = f"""
        const assert = require('assert');
        const fs = require('fs');
        const vm = require('vm');

        class FakeNode {{
          constructor(tagName, attrs, textContent) {{
            this.tagName = tagName;
            this.attributes = Object.assign({{}}, attrs || {{}});
            this.children = [];
            this.parentNode = null;
            this.textContent = textContent || '';
            this.style = {{}};
          }}

          setAttribute(name, value) {{
            this.attributes[name] = String(value);
          }}

          setAttributeNS(_namespace, name, value) {{
            this.setAttribute(name, value);
          }}

          getAttribute(name) {{
            return Object.prototype.hasOwnProperty.call(this.attributes, name) ? this.attributes[name] : null;
          }}

          hasAttribute(name) {{
            return Object.prototype.hasOwnProperty.call(this.attributes, name);
          }}

          removeAttribute(name) {{
            delete this.attributes[name];
          }}

          appendChild(child) {{
            child.parentNode = this;
            this.children.push(child);
            return child;
          }}

          insertBefore(child, reference) {{
            child.parentNode = this;
            const index = this.children.indexOf(reference);
            if (index === -1) {{
              this.children.unshift(child);
            }} else {{
              this.children.splice(index, 0, child);
            }}
            return child;
          }}

          replaceChildren(...children) {{
            this.children = [];
            children.forEach((child) => this.appendChild(child));
          }}

          addEventListener() {{}}

          getBoundingClientRect() {{
            return {{ width: 0, height: 0, left: 0, top: 0, right: 0, bottom: 0 }};
          }}

          closest(selector) {{
            var node = this;
            while (node) {{
              if (node.matches && node.matches(selector)) {{
                return node;
              }}
              node = node.parentNode;
            }}
            return null;
          }}

          cloneNode(deep) {{
            const clone = new FakeNode(this.tagName, this.attributes, this.textContent);
            if (deep) {{
              this.children.forEach((child) => clone.appendChild(child.cloneNode(true)));
            }}
            return clone;
          }}

          matches(selector) {{
            if (selector.startsWith('[') && selector.endsWith(']')) {{
              const name = selector.slice(1, -1).split('=')[0];
              return this.hasAttribute(name);
            }}
            if (selector.startsWith('.')) {{
              return (this.getAttribute('class') || '').split(/\\s+/).includes(selector.slice(1));
            }}
            if (selector.includes('.')) {{
              const [tag, className] = selector.split('.');
              return this.tagName === tag && (this.getAttribute('class') || '').split(/\\s+/).includes(className);
            }}
            return this.tagName === selector;
          }}

          querySelectorAll(selector) {{
            const matches = [];
            function visit(node) {{
              node.children.forEach((child) => {{
                if (child.matches(selector)) {{
                  matches.push(child);
                }}
                visit(child);
              }});
            }}
            visit(this);
            return matches;
          }}

          querySelector(selector) {{
            return this.querySelectorAll(selector)[0] || null;
          }}

          serialize() {{
            const escapeText = (value) => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;');
            const escapeAttr = (value) => escapeText(value).replace(/"/g, '&quot;');
            const attrs = Object.keys(this.attributes)
              .map((name) => ` ${{name}}="${{escapeAttr(this.attributes[name])}}"`)
              .join('');
            const body = escapeText(this.textContent) + this.children.map((child) => child.serialize()).join('');
            return `<${{this.tagName}}${{attrs}}>${{body}}</${{this.tagName}}>`;
          }}
        }}

        function node(tagName, attrs, children, textContent) {{
          const item = new FakeNode(tagName, attrs, textContent);
          (children || []).forEach((child) => item.appendChild(child));
          return item;
        }}

        const context = {{
          window: {{}},
          document: {{
            readyState: 'loading',
            addEventListener() {{}},
            querySelectorAll() {{ return []; }},
            createElementNS(_namespace, tagName) {{ return new FakeNode(tagName); }},
            createElement(tagName) {{ return new FakeNode(tagName); }},
            body: new FakeNode('body'),
            documentElement: new FakeNode('html'),
          }},
          XMLSerializer: class {{
            serializeToString(item) {{
              return item.serialize();
            }}
          }},
          Blob: class {{}},
          URL: {{ createObjectURL() {{ return 'blob:test'; }}, revokeObjectURL() {{}} }},
          setTimeout(callback) {{ callback(); }},
        }};
        context.window.setTimeout = context.setTimeout;

        vm.createContext(context);
        vm.runInContext(fs.readFileSync({str(FANOUT_TRACE_JS)!r}, 'utf8'), context);
        const api = context.window.NetBoxPlantGraphVisualTrace;
        {probe_source}
        """

        result = subprocess.run(
            [node, '-e', textwrap.dedent(harness)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_renderer_javascript_syntax_is_valid(self):
        node = shutil.which('node')
        if node is None:
            self.skipTest('node is not available for fanout_trace.js syntax checking')

        result = subprocess.run(
            [node, '--check', str(FANOUT_TRACE_JS)],
            cwd=str(REPO_ROOT),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)

    def test_card_include_exposes_required_controls_and_payload_hooks(self):
        markup = VISUAL_TRACE_CARD.read_text()

        required_fragments = (
            'data-visual-trace-component="mpf-visual-trace"',
            'data-fanout-toggle-visual',
            'data-fanout-export-svg',
            'data-fanout-visual-body',
            'data-fanout-schematic',
            'data-fanout-schematic-svg',
            'data-fanout-schematic-data',
            'data-fanout-schematic-stages',
            'data-fanout-fixture-key="{{ fixture_key }}"',
            'data-fanout-source-title="{{ source_title }}"',
            'data-fanout-source-url="{{ source_url }}"',
            'data-fanout-diagnostics',
            'data-fanout-diagnostic-value="render-signature"',
            'data-fanout-diagnostic-value="path-count"',
            'data-fanout-diagnostic-value="stage-count"',
            'data-fanout-diagnostic-value="cable-span-count"',
            'data-fanout-diagnostic-value="golden-harness-status"',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, markup)

    def test_static_includes_pin_shared_visual_trace_assets(self):
        scripts = VISUAL_TRACE_SCRIPTS.read_text()
        styles = VISUAL_TRACE_STYLES.read_text()

        self.assertIn("netbox_plant_graph/fanout_trace.js", scripts)
        self.assertIn("netbox_plant_graph/fanout_trace.css", styles)
        self.assertIn("20260522-visual-trace-component", scripts)
        self.assertIn("20260522-visual-trace-component", styles)

    def test_renderer_keeps_public_browser_api_and_export_hooks(self):
        source = FANOUT_TRACE_JS.read_text()

        required_fragments = (
            'window.NetBoxPlantGraphVisualTrace',
            'initialize: initializeFanoutTracePage',
            'render: renderSchematic',
            'goldenSnapshot: goldenSnapshot',
            'exportedSvgMarkup: exportedSvgMarkup',
            'renderedSvgCounts: renderedSvgCounts',
            'exportSanity: exportSanity',
            'payloadCounts: function (container)',
            'renderSignature: function (container)',
            'data-fanout-render-signature',
            'updateDiagnostics(container, rawPaths, stagePayload)',
            'data-fanout-diagnostic-value',
            'data-fanout-visual-section',
            'data-fanout-connector-frame',
            'data-fanout-export-svg',
            'image/svg+xml;charset=utf-8',
            'fanout-object-link',
            'xlink:href',
            'installTooltips(container, svg)',
            'drawCableAssemblyOverlays',
            'function goldenSnapshot(container)',
        )
        for fragment in required_fragments:
            with self.subTest(fragment=fragment):
                self.assertIn(fragment, source)

    def test_renderer_diagnostic_api_counts_payload_and_rendered_svg_hooks(self):
        self.run_renderer_node_probe(
            """
            const payload = [
              {path_found: true, cable_spans: [{cable_assembly: {display: 'bundle-a'}}]},
              {path_found: false, cable_spans: []},
            ];
            const stages = [{stage_index: 0}, {stage_index: 1}];
            const payloadNode = node('script', {'data-fanout-schematic-data': 'true'}, [], JSON.stringify(payload));
            const stageNode = node('script', {'data-fanout-schematic-stages': 'true'}, [], JSON.stringify(stages));
            const container = node('div', {
              'data-fanout-fixture-key': 'diagnostic-fixture',
              'data-fanout-trace-mode': 'expanded',
            }, [payloadNode, stageNode]);

            assert.strictEqual(JSON.stringify(api.payloadCounts(container)), JSON.stringify({
              pathCount: 2,
              stageCount: 2,
              cableSpanCount: 1,
            }));
            assert.strictEqual(
              api.renderSignature(container),
              'diagnostic-fixture|expanded|paths:2|stages:2|cables:1'
            );

            const svg = node('svg', {}, [
              node('rect', {'data-fanout-visual-section': 'true'}),
              node('rect', {'data-fanout-visual-section': 'true'}),
              node('rect', {'data-fanout-connector-frame': 'true'}),
              node('g', {class: 'fanout-cable-assembly-cylinder'}),
              node('a', {class: 'fanout-object-link', href: '/plugins/netbox-plant-graph/object/1/'}),
            ]);
            assert.strictEqual(JSON.stringify(api.renderedSvgCounts(svg)), JSON.stringify({
              sectionCount: 2,
              connectorCount: 1,
              cableCylinderCount: 1,
              objectLinkCount: 1,
            }));

            const diagnosticFields = ['render-signature', 'path-count', 'stage-count', 'cable-span-count', 'golden-harness-status']
              .map((name) => node('td', {'data-fanout-diagnostic-value': name}, [], 'pending'));
            const diagnosticContainer = node('details', {'data-fanout-diagnostics': 'true'}, diagnosticFields);
            const diagnosticPayload = [
              {path_found: false, cable_spans: [{cable_assembly: {display: 'bundle-a'}}]},
              {path_found: false, cable_spans: []},
            ];
            const diagnosticSvg = node('svg', {'data-fanout-schematic-svg': 'true'});
            const diagnosticPayloadNode = node('script', {'data-fanout-schematic-data': 'true'}, [], JSON.stringify(diagnosticPayload));
            const diagnosticStageNode = node('script', {'data-fanout-schematic-stages': 'true'}, [], JSON.stringify(stages));
            const diagnosticSchematic = node('div', {
              'data-fanout-fixture-key': 'diagnostic-fixture',
              'data-fanout-trace-mode': 'expanded',
              'data-fanout-schematic': 'true',
            }, [diagnosticPayloadNode, diagnosticStageNode, diagnosticSvg]);
            node('div', {'data-visual-trace-component': 'mpf-visual-trace'}, [diagnosticSchematic, diagnosticContainer]);
            api.render(diagnosticSchematic);
            function diagnosticValue(name) {
              return diagnosticFields.find((field) => field.getAttribute('data-fanout-diagnostic-value') === name).textContent;
            }
            assert.strictEqual(diagnosticValue('render-signature'), 'diagnostic-fixture|expanded|paths:2|stages:2|cables:1');
            assert.strictEqual(diagnosticValue('path-count'), '2');
            assert.strictEqual(diagnosticValue('stage-count'), '2');
            assert.strictEqual(diagnosticValue('cable-span-count'), '1');
            assert(diagnosticValue('golden-harness-status').includes('CI golden regression harness pending'));
            """
        )

    def test_renderer_golden_snapshot_covers_rendered_fixture_shape(self):
        self.run_renderer_node_probe(
            """
            const payload = [
              {
                path_found: true,
                source_subinterface_label: '',
                destination_interface_layer_label: '',
                connector_hops: [
                  {
                    endpoint_label: 'source.MPO-1',
                    endpoint_url: '/plugins/plant-graph/endpoints/1/',
                    position_url: '/plugins/plant-graph/connector-positions/1/',
                    position: 1,
                    position_count: 12,
                  },
                  {
                    endpoint_label: 'destination.MPO-1',
                    endpoint_url: '/plugins/plant-graph/endpoints/2/',
                    position_url: '/plugins/plant-graph/connector-positions/12/',
                    position: 12,
                    position_count: 12,
                  },
                ],
                cable_spans: [
                  {
                    from_hop_index: 0,
                    to_hop_index: 1,
                    cable_assembly: {
                      display: 'CABLE-1',
                      label: 'CABLE-1',
                      url: '/plugins/plant-graph/cable-assemblies/1/',
                    },
                  },
                ],
              },
            ];
            const stages = [
              {
                stage_index: 0,
                connectors: [
                  {
                    endpoint_label: 'source.MPO-1',
                    endpoint_url: '/plugins/plant-graph/endpoints/1/',
                    position_count: 12,
                    positions: [{position: 1, url: '/plugins/plant-graph/connector-positions/1/'}],
                  },
                ],
              },
              {
                stage_index: 1,
                connectors: [
                  {
                    endpoint_label: 'destination.MPO-1',
                    endpoint_url: '/plugins/plant-graph/endpoints/2/',
                    position_count: 12,
                    positions: [{position: 12, url: '/plugins/plant-graph/connector-positions/12/'}],
                  },
                ],
              },
            ];
            const svg = node('svg', {'data-fanout-schematic-svg': 'true'});
            const payloadNode = node('script', {'data-fanout-schematic-data': 'true'}, [], JSON.stringify(payload));
            const stageNode = node('script', {'data-fanout-schematic-stages': 'true'}, [], JSON.stringify(stages));
            const container = node('div', {
              'data-fanout-fixture-key': 'golden-fixture',
              'data-fanout-trace-mode': 'expanded',
              'data-fanout-source-title': 'Source: fixture-osfp1',
              'data-fanout-source-url': '/plugins/plant-graph/endpoints/1/',
            }, [payloadNode, stageNode, svg]);

            const snapshot = api.goldenSnapshot(container);
            assert.strictEqual(snapshot.signature, 'golden-fixture|expanded|paths:1|stages:2|cables:1');
            assert.strictEqual(snapshot.payload.pathCount, 1);
            assert.strictEqual(snapshot.payload.stageCount, 2);
            assert.strictEqual(snapshot.payload.cableSpanCount, 1);
            assert.strictEqual(snapshot.rendered.sectionCount, 2);
            assert.strictEqual(snapshot.rendered.connectorCount, 2);
            assert.strictEqual(snapshot.rendered.cableCylinderCount, 1);
            assert(snapshot.rendered.objectLinkCount >= 3);
            assert(snapshot.width > 0);
            assert(snapshot.height > 0);
            assert.strictEqual(snapshot.export.hasXmlDeclaration, true);
            assert.strictEqual(snapshot.export.hasSvgNamespace, true);
            assert.strictEqual(snapshot.export.hasObjectLinks, true);
            assert.strictEqual(container.getAttribute('data-fanout-rendered'), 'true');
            """
        )

    def test_exported_svg_keeps_links_tooltips_dimensions_and_visible_hooks(self):
        self.run_renderer_node_probe(
            """
            const link = node('a', {
              class: 'fanout-object-link',
              href: '/plugins/netbox-plant-graph/object/1/',
              'xlink:href': '/plugins/netbox-plant-graph/object/1/',
              'aria-label': 'Source connector',
              'data-fanout-tooltip': 'Source connector',
            }, [
              node('rect', {
                'data-fanout-connector-frame': 'true',
                'data-fanout-tooltip': 'Source connector position 1',
              }),
            ]);
            const svg = node('svg', {
              viewBox: '0 0 1660 420',
              'data-fanout-schematic-svg': 'true',
              focusable: 'false',
            }, [
              node('rect', {'data-fanout-visual-section': 'true', 'data-fanout-stage-title': 'Source Endpoint'}),
              link,
            ]);

            const markup = api.exportedSvgMarkup(svg);
            assert(markup.startsWith('<?xml version="1.0" encoding="UTF-8"?>'));
            assert(markup.includes('xmlns="http://www.w3.org/2000/svg"'));
            assert(markup.includes('xmlns:xlink="http://www.w3.org/1999/xlink"'));
            assert(markup.includes('width="1660"'));
            assert(markup.includes('height="420"'));
            assert(markup.includes('fanout-object-link'));
            assert(markup.includes('xlink:href="/plugins/netbox-plant-graph/object/1/"'));
            assert(markup.includes('aria-label="Source connector"'));
            assert(markup.includes('data-fanout-tooltip="Source connector position 1"'));
            assert(markup.includes('data-fanout-visual-section="true"'));
            assert(!markup.includes('data-fanout-schematic-svg'));
            assert(!markup.includes('focusable='));

            const sanity = api.exportSanity(svg);
            assert.strictEqual(sanity.hasXmlDeclaration, true);
            assert.strictEqual(sanity.hasSvgNamespace, true);
            assert.strictEqual(sanity.hasXlinkNamespace, true);
            assert.strictEqual(sanity.hasDimensions, true);
            assert.strictEqual(sanity.hasObjectLinks, true);
            assert.strictEqual(sanity.hasTooltips, true);
            assert.strictEqual(sanity.hasVisibleContentHooks, true);
            """
        )

    def test_exported_svg_handles_missing_urls_without_empty_links(self):
        self.run_renderer_node_probe(
            """
            const svg = node('svg', {viewBox: '0 0 980 280'}, [
              node('rect', {
                'data-fanout-visual-section': 'true',
                'data-fanout-tooltip': 'Unlinked object',
              }),
            ]);

            const counts = api.renderedSvgCounts(svg);
            const markup = api.exportedSvgMarkup(svg);
            assert.strictEqual(counts.objectLinkCount, 0);
            assert(!markup.includes('href=""'));
            assert(!markup.includes('xlink:href=""'));
            assert(markup.includes('data-fanout-tooltip="Unlinked object"'));
            """
        )
