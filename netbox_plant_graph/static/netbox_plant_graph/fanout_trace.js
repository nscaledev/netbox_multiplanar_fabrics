(function () {
  var SVG_NS = 'http://www.w3.org/2000/svg';
  var XLINK_NS = 'http://www.w3.org/1999/xlink';
  var LANE_COLORS = [
    '#ff3b3b', '#ffe34a', '#38d7ff', '#3dff75',
    '#c7c7c7', '#ff8f3b', '#b57dff', '#89ffb8',
    '#ff6fb1', '#8fb7ff', '#d7ff5a', '#ffb86b',
    '#74fff0', '#f1a7ff', '#a6ff7a', '#ff7777',
  ];
  var POSITION_COUNT = 12;
  var CHANNEL_POSITION_COUNT = 4;
  var GRID_CONNECTOR_COLUMNS = 4;
  var BASE_SCHEMATIC_WIDTH = 1660;
  var CONNECTOR_RAIL_X = 360;
  var STAGE_BRACE_X = 198;
  var STAGE_BRACE_LABEL_X = 188;
  var POSITION_BRACE_X = 312;
  var POSITION_BRACE_LABEL_X = 300;
  var RANGE_BRACE_X = 92;
  var RANGE_BRACE_LABEL_X = 78;
  var SHUFFLE_CASSETTE_SIDE_GAP = 42;
  var SOURCE_COMPOSITE_LAYER_GAP = 20;
  var DESTINATION_COMPOSITE_LAYER_GAP = 3;
  var UNUSED_STRAND_COLOR = '#9aa3b2';
  var SHUFFLE_CASSETTE_STRAND_OPACITY = 0.5;
  var ANNOTATION_BRACE_COLOR = '#9fbce4';
  var ANNOTATION_TEXT_COLOR = '#9fbce4';
  var GROUP_ROW_GAP = 18;
  var INTERFACE_CHANNEL_GROUP_SPACER_RATIO = 0.5;
  var OUTER_TO_INNER_LABEL_GAP = 8;
  var MONO_TEXT_WIDTH_FACTOR = 0.62;
  var CONNECTOR_ROW_STRIDE = 58;
  var GROUP_FRAME_TOP_OFFSET = 16;
  var CONNECTOR_ROW_TOP_OFFSET = 30;
  var DEFAULT_STAGE_GAP = 20;
  var CABLE_ASSEMBLY_STAGE_GAP = 148;
  var CABLE_CYLINDER_HEIGHT = 120;
  var CABLE_CYLINDER_MIN_WIDTH = 150;
  var CABLE_CYLINDER_HORIZONTAL_PADDING = 24;
  var CABLE_CYLINDER_MAX_WIDTH = 420;

  function createSvgNode(tagName, attrs) {
    var node = document.createElementNS(SVG_NS, tagName);
    if (attrs) {
      Object.keys(attrs).forEach(function (key) {
        node.setAttribute(key, String(attrs[key]));
      });
    }
    return node;
  }

  function normalizeUrl(url) {
    var value = String(url || '').trim();
    if (!value || value === '#') {
      return '';
    }
    return value;
  }

  function linkedSvgNode(node, url, titleText) {
    var href = normalizeUrl(url);
    if (!href) {
      return node;
    }
    var link = createSvgNode('a', {
      href: href,
      class: 'fanout-object-link',
      style: 'cursor: pointer;',
    });
    link.setAttributeNS(XLINK_NS, 'xlink:href', href);
    if (titleText) {
      appendTitle(link, titleText);
      link.setAttribute('aria-label', String(titleText));
    }
    link.appendChild(node);
    return link;
  }

  function parsePayload(container) {
    var payloadNode = container.querySelector('[data-fanout-schematic-data]');
    if (!payloadNode) {
      return [];
    }
    try {
      var value = JSON.parse(payloadNode.textContent || '[]');
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  }

  function parseStagePayload(container) {
    var payloadNode = container.querySelector('[data-fanout-schematic-stages]');
    if (!payloadNode) {
      return [];
    }
    try {
      var value = JSON.parse(payloadNode.textContent || '[]');
      return Array.isArray(value) ? value : [];
    } catch (_error) {
      return [];
    }
  }

  function sourceStageTitle(container) {
    return container.getAttribute('data-fanout-source-title') || 'Source Endpoint';
  }

  function sourceStageUrl(container) {
    return container.getAttribute('data-fanout-source-url') || '';
  }

  function asPosition(value, positionCount) {
    var parsed = Number(value);
    var maxPosition = Number(positionCount) || POSITION_COUNT;
    if (!Number.isFinite(parsed)) {
      return null;
    }
    var rounded = Math.round(parsed);
    if (rounded < 1 || rounded > maxPosition) {
      return null;
    }
    return rounded;
  }

  function compactMiddle(label, maxLength) {
    var text = String(label || '');
    if (text.length <= maxLength) {
      return text;
    }
    var headLength = Math.ceil((maxLength - 1) * 0.42);
    var tailLength = Math.floor((maxLength - 1) * 0.58);
    return text.slice(0, headLength) + '\u2026' + text.slice(text.length - tailLength);
  }

  function shortConnectorLabel(endpointLabel) {
    var text = String(endpointLabel || 'connector');
    var pieces = text.split('.');
    if (pieces.length >= 3) {
      return pieces.slice(-3).join('.');
    }
    return text;
  }

  function stageTitle(index, maxDepth, withInterfaceLayers) {
    if (withInterfaceLayers) {
      if (index === 0) {
        return '200Gbps Source Interfaces';
      }
      if (index === 1) {
        return 'Source Endpoint';
      }
      if (index === maxDepth - 1) {
        return '200Gbps Destination Interfaces';
      }
      if (index === maxDepth - 2) {
        return 'Destination Endpoint';
      }
      if (index === 2) {
        return 'Shuffle Ingress';
      }
      if (index === 3) {
        return 'Shuffle Egress';
      }
      return 'Hop ' + (index - 1);
    }
    if (index === 0) {
      return 'Source Endpoint';
    }
    if (index === maxDepth - 1) {
      return 'Destination Endpoint';
    }
    if (index === 1) {
      return 'Shuffle Ingress';
    }
    if (index === 2) {
      return 'Shuffle Egress';
    }
    return 'Hop ' + index;
  }

  function stageRole(index, maxDepth, withInterfaceLayers) {
    if (withInterfaceLayers) {
      if (index === 0) {
        return 'source_interface_layer';
      }
      if (index === 1) {
        return 'source_endpoint';
      }
      if (index === maxDepth - 1) {
        return 'destination_interface_layer';
      }
      if (index === maxDepth - 2) {
        return 'destination_endpoint';
      }
      if (index === 2) {
        return 'shuffle_ingress';
      }
      if (index === 3) {
        return 'shuffle_egress';
      }
    }
    if (index === 0) {
      return 'source_endpoint';
    }
    if (index === maxDepth - 1) {
      return 'destination_endpoint';
    }
    if (index === 1) {
      return 'shuffle_ingress';
    }
    if (index === 2) {
      return 'shuffle_egress';
    }
    return 'intermediate';
  }

  function connectorKey(endpointLabel, fallback) {
    return endpointLabel || fallback;
  }

  function connectorHasActivePath(connector) {
    return Object.keys(connector.active || {}).length > 0;
  }

  function connectorFamilyKey(endpointLabel) {
    var text = String(endpointLabel || '');
    var match = text.match(/^(.*(?:mpo|MPO)[-.])0*\d+$/);
    return match ? match[1] : text;
  }

  function normalizedPositionCount(value) {
    var parsed = Number(value);
    if (!Number.isFinite(parsed) || parsed < 1) {
      return POSITION_COUNT;
    }
    return Math.min(POSITION_COUNT, Math.round(parsed));
  }

  function positionUrlMap(positions) {
    var urls = {};
    (positions || []).forEach(function (position) {
      var positionNumber = asPosition(position.position || position.position_number, POSITION_COUNT);
      var url = normalizeUrl(position.url);
      if (positionNumber && url) {
        urls[positionNumber] = url;
      }
    });
    return urls;
  }

  function mergePositionUrls(target, source) {
    Object.keys(source || {}).forEach(function (position) {
      if (!target[position]) {
        target[position] = source[position];
      }
    });
  }

  function connectorPositionUrl(connector, position) {
    return normalizeUrl((connector.positionUrls || {})[position] || connector.url);
  }

  function activePositionValue(connector, position) {
    var value = connector.active ? connector.active[position] : null;
    if (!value) {
      return null;
    }
    if (typeof value === 'string') {
      return {
        color: value,
        url: connectorPositionUrl(connector, position),
      };
    }
    return value;
  }

  function activePositionColor(connector, position) {
    var value = activePositionValue(connector, position);
    return value ? value.color : '';
  }

  function activePositionUrl(connector, position) {
    var value = activePositionValue(connector, position);
    return normalizeUrl(value && value.url) || connectorPositionUrl(connector, position);
  }

  function markActivePosition(connector, position, color, options) {
    var url = normalizeUrl(options && options.url) || connectorPositionUrl(connector, position);
    var existing = activePositionValue(connector, position);
    if (!existing) {
      connector.active[position] = {
        color: color,
        url: url,
      };
      return;
    }
    if (!existing.url && url) {
      existing.url = url;
      connector.active[position] = existing;
    }
  }

  function ensureConnector(stage, key, label, positionCount, kind, options) {
    if (!stage.connectors[key]) {
      stage.connectors[key] = {
        key: key,
        label: label || key,
        familyKey: connectorFamilyKey(label || key),
        positionCount: normalizedPositionCount(positionCount),
        kind: kind || 'mpo',
        url: normalizeUrl(options && options.url),
        parentUrl: normalizeUrl(options && options.parentUrl),
        positionUrls: positionUrlMap(options && options.positions),
        active: {},
      };
      stage.connectorOrder.push(key);
    } else if (positionCount) {
      stage.connectors[key].positionCount = normalizedPositionCount(positionCount);
      if (options) {
        if (!stage.connectors[key].url && options.url) {
          stage.connectors[key].url = normalizeUrl(options.url);
        }
        if (!stage.connectors[key].parentUrl && options.parentUrl) {
          stage.connectors[key].parentUrl = normalizeUrl(options.parentUrl);
        }
        mergePositionUrls(stage.connectors[key].positionUrls, positionUrlMap(options.positions));
      }
    }
    return stage.connectors[key];
  }

  function nextOrdinal(bucket, key, maxPosition) {
    var normalizedKey = key || 'unknown';
    bucket[normalizedKey] = (bucket[normalizedKey] || 0) + 1;
    return ((bucket[normalizedKey] - 1) % maxPosition) + 1;
  }

  function hasExpandedInterfaceLayers(container, paths) {
    if ((container.getAttribute('data-fanout-trace-mode') || '').toLowerCase() === 'consolidated') {
      return false;
    }
    return paths.some(function (path) {
      return path.source_subinterface_label || path.destination_interface_layer_label || path.destination_subinterface_label;
    });
  }

  function sourceInterfaceLabel(path) {
    return path.source_subinterface_label || 'source 200G';
  }

  function destinationInterfaceLabel(path) {
    return path.destination_interface_layer_label || path.remote_attachment_label || path.destination_subinterface_label || 'destination 200G';
  }

  function withInterfaceLayerHops(paths) {
    var sourceCounters = {};
    var destinationCounters = {};
    return paths.map(function (path) {
      var sourceLabel = sourceInterfaceLabel(path);
      var destinationLabel = destinationInterfaceLabel(path);
      var sourcePosition = nextOrdinal(sourceCounters, sourceLabel, CHANNEL_POSITION_COUNT);
      var destinationPosition = nextOrdinal(destinationCounters, destinationLabel, CHANNEL_POSITION_COUNT);
      var cloned = Object.assign({}, path);
      cloned.connector_hops = [
        {
          endpoint_label: sourceLabel,
          endpoint_url: path.source_subinterface_url,
          url: path.source_lane_url,
          position_url: path.source_lane_url,
          position: sourcePosition,
          position_count: CHANNEL_POSITION_COUNT,
          schematic_kind: 'interface_channel',
          step_type: 'source_interface',
        },
      ].concat(path.connector_hops || [], [
        {
          endpoint_label: destinationLabel,
          endpoint_url: path.destination_interface_layer_url || path.destination_subinterface_url,
          url: path.destination_lane_url,
          position_url: path.destination_lane_url,
          position: destinationPosition,
          position_count: CHANNEL_POSITION_COUNT,
          schematic_kind: 'interface_channel',
          step_type: 'destination_interface',
        },
      ]);
      cloned.cable_spans = (path.cable_spans || []).map(function (span) {
        var fromHopIndex = Number(span.from_hop_index);
        var toHopIndex = Number(span.to_hop_index);
        return Object.assign({}, span, {
          from_hop_index: Number.isFinite(fromHopIndex) ? fromHopIndex + 1 : span.from_hop_index,
          to_hop_index: Number.isFinite(toHopIndex) ? toHopIndex + 1 : span.to_hop_index,
        });
      });
      return cloned;
    });
  }

  function buildStages(paths, stagePayload, options) {
    var withInterfaceLayers = Boolean(options && options.interfaceLayers);
    var stageOffset = withInterfaceLayers ? 1 : 0;
    var maxDepth = 0;
    paths.forEach(function (path) {
      maxDepth = Math.max(maxDepth, (path.connector_hops || []).length);
    });
    (stagePayload || []).forEach(function (stageDefinition) {
      var stageIndex = Number(stageDefinition.stage_index);
      if (Number.isFinite(stageIndex)) {
        maxDepth = Math.max(maxDepth, stageIndex + 1);
      }
    });

    var stages = [];
    for (var depth = 0; depth < maxDepth; depth += 1) {
      var role = stageRole(depth, maxDepth, withInterfaceLayers);
      stages.push({
        depth: depth,
        title: stageTitle(depth, maxDepth, withInterfaceLayers),
        role: role,
        isDestination: role === 'destination_endpoint',
        isDestinationInterfaceLayer: role === 'destination_interface_layer',
        isSourceInterfaceLayer: role === 'source_interface_layer',
        isInterfaceLayer: role === 'source_interface_layer' || role === 'destination_interface_layer',
        isShuffle: role === 'shuffle_ingress' || role === 'shuffle_egress',
        connectors: {},
        connectorOrder: [],
        activeFamilyOrder: [],
      });
    }

    (stagePayload || []).forEach(function (stageDefinition) {
      var stageIndex = Number(stageDefinition.stage_index) + stageOffset;
      var stage = stages[stageIndex];
      if (!stage) {
        return;
      }
      (stageDefinition.connectors || []).forEach(function (connector) {
        var label = connector.endpoint_label || String(connector.endpoint_id || '');
        var key = connectorKey(label, 'stage-' + stageIndex + '-connector-' + connector.endpoint_id);
        ensureConnector(stage, key, label, connector.position_count || POSITION_COUNT, 'mpo', {
          url: connector.endpoint_url || connector.url,
          parentUrl: connector.parent_endpoint_url,
          positions: connector.positions || [],
        });
      });
    });

    paths.forEach(function (path, pathIndex) {
      var color = LANE_COLORS[pathIndex % LANE_COLORS.length];
      (path.connector_hops || []).forEach(function (hop, depth) {
        var stage = stages[depth];
        var positionCount = hop.position_count || (hop.schematic_kind === 'interface_channel' ? CHANNEL_POSITION_COUNT : POSITION_COUNT);
        var position = asPosition(hop.position, positionCount);
        if (!stage || !position) {
          return;
        }
        var key = connectorKey(hop.endpoint_label, 'connector-' + depth);
        var connector = ensureConnector(
          stage,
          key,
          hop.endpoint_label || key,
          positionCount,
          hop.schematic_kind === 'interface_channel' ? 'interface_channel' : 'mpo',
          {
            url: hop.endpoint_url,
            positions: hop.position_url ? [{ position: position, url: hop.position_url }] : [],
          }
        );
        markActivePosition(connector, position, color, {
          url: hop.position_url || hop.url || hop.lane_url,
        });
        if (stage.activeFamilyOrder.indexOf(connector.familyKey) === -1) {
          stage.activeFamilyOrder.push(connector.familyKey);
        }
      });
    });

    return stages.map(function (stage) {
      stage.connectorList = stage.connectorOrder.map(function (key) {
        return stage.connectors[key];
      }).filter(connectorHasActivePath);
      stage.connectorGroups = connectorGroupsForStage(stage);
      stage.groupRows = [];
      if ((stage.isDestination || stage.isDestinationInterfaceLayer) && stage.connectorGroups.length > 1) {
        stage.connectorGroups.forEach(function (group, index) {
          group.stageColumnIndex = index;
          stage.groupRows.push([group]);
        });
      } else if (stage.isSourceInterfaceLayer) {
        for (var sourceIndex = 0; sourceIndex < stage.connectorGroups.length; sourceIndex += GRID_CONNECTOR_COLUMNS) {
          var sourceGroupRow = stage.connectorGroups.slice(sourceIndex, sourceIndex + GRID_CONNECTOR_COLUMNS);
          sourceGroupRow.forEach(function (group, rowIndex) {
            group.stageColumnIndex = rowIndex;
          });
          stage.groupRows.push(sourceGroupRow);
        }
      } else if (stage.isShuffle && stage.connectorGroups.some(function (group) {
        return (group.slotConnectorCount || group.columnCount || 1) >= GRID_CONNECTOR_COLUMNS;
      })) {
        stage.connectorGroups.forEach(function (group, groupIndex) {
          group.stageColumnIndex = groupIndex;
        });
        stage.groupRows.push(stage.connectorGroups);
      } else {
        var groupRow = [];
        stage.connectorGroups.forEach(function (group) {
          var slots = group.slotConnectorCount || group.columnCount || 1;
          if (slots >= GRID_CONNECTOR_COLUMNS) {
            if (groupRow.length) {
              groupRow.forEach(function (rowGroup, rowIndex) {
                rowGroup.stageColumnIndex = rowIndex;
              });
              stage.groupRows.push(groupRow);
              groupRow = [];
            }
            group.stageColumnIndex = 0;
            stage.groupRows.push([group]);
            return;
          }

          groupRow.push(group);
          if (groupRow.length === 2) {
            groupRow.forEach(function (rowGroup, rowIndex) {
              rowGroup.stageColumnIndex = rowIndex;
            });
            stage.groupRows.push(groupRow);
            groupRow = [];
          }
        });
        if (groupRow.length) {
          groupRow.forEach(function (rowGroup, rowIndex) {
            rowGroup.stageColumnIndex = rowIndex;
          });
          stage.groupRows.push(groupRow);
        }
      }
      return stage;
    });
  }

  function visualStagePlan(stages, options) {
    var sourceTitle = (options && options.sourceTitle) || 'Source Endpoint';
    var sourceUrl = normalizeUrl(options && options.sourceUrl);
    var visualStages = [];
    var depthsByVisualIndex = [];
    stages.forEach(function (stage, index) {
      if (stage.role === 'source_endpoint' && index > 0 && stages[index - 1].role === 'source_interface_layer') {
        return;
      }
      if (stage.role === 'destination_interface_layer' && index > 0 && stages[index - 1].role === 'destination_endpoint') {
        return;
      }
      if (stage.role === 'shuffle_egress' && index > 0 && stages[index - 1].role === 'shuffle_ingress') {
        return;
      }

      if (stage.role === 'source_interface_layer' && stages[index + 1] && stages[index + 1].role === 'source_endpoint') {
        visualStages.push({
          depth: stage.depth,
          title: sourceTitle,
          url: sourceUrl,
          role: 'source_composite',
          isSourceComposite: true,
          subStages: [stage, stages[index + 1]],
        });
        depthsByVisualIndex.push([stage.depth, stages[index + 1].depth]);
        return;
      }

      if (stage.role === 'destination_endpoint' && stages[index + 1] && stages[index + 1].role === 'destination_interface_layer') {
        destinationCompositeStages(stage, stages[index + 1]).forEach(function (destinationStage) {
          visualStages.push(destinationStage);
          depthsByVisualIndex.push(destinationStage.subStages.map(function (subStage) {
            return subStage.depth;
          }));
        });
        return;
      }

      if (stage.role === 'shuffle_ingress' && stages[index + 1] && stages[index + 1].role === 'shuffle_egress') {
        visualStages.push({
          depth: stage.depth,
          title: shuffleCassetteStageTitle(stage, stages[index + 1]),
          role: 'shuffle_cassette',
          isShuffle: true,
          isShuffleCassette: true,
          subStages: [stage, stages[index + 1]],
        });
        depthsByVisualIndex.push([stage.depth, stages[index + 1].depth]);
        return;
      }

      visualStages.push(stage.role === 'source_endpoint' ? Object.assign({}, stage, { title: sourceTitle, url: sourceUrl }) : stage);
      depthsByVisualIndex.push([stage.depth]);
    });

    return {
      stages: visualStages,
      depthsByVisualIndex: depthsByVisualIndex,
    };
  }

  function cloneStageWithGroup(stage, group) {
    var cloned = Object.assign({}, stage);
    cloned.connectorList = (group.connectors || []).slice();
    cloned.connectorGroups = [group];
    cloned.groupRows = [[group]];
    cloned.activeFamilyOrder = [group.key];
    return cloned;
  }

  function destinationCagePortLabel(label) {
    var text = String(label || '').trim();
    var withoutMpo = text
      .replace(/(?:[._-])?MPO(?:[-.]?0*\d+)?$/i, '')
      .replace(/[._-]+$/, '');
    var lastDot = withoutMpo.lastIndexOf('.');
    if (lastDot !== -1) {
      return withoutMpo.slice(0, lastDot) + '-' + withoutMpo.slice(lastDot + 1);
    }
    return withoutMpo || text || 'destination';
  }

  function destinationCompositeTitle(destinationGroup, interfaceGroup) {
    var label = destinationGroup && destinationGroup.label ? destinationGroup.label : '';
    if (!label && interfaceGroup && interfaceGroup.label) {
      label = interfaceGroup.label.replace(/\/\d+$/, '');
    }
    return 'Destination: ' + destinationCagePortLabel(label);
  }

  function destinationCompositeStages(destinationStage, interfaceStage) {
    var destinationGroups = destinationStage.connectorGroups || [];
    var interfaceGroups = interfaceStage.connectorGroups || [];
    var blockCount = Math.max(destinationGroups.length, interfaceGroups.length);
    var blocks = [];
    for (var index = 0; index < blockCount; index += 1) {
      var destinationGroup = destinationGroups[index];
      var interfaceGroup = interfaceGroups[index];
      var subStages = [];
      if (destinationGroup) {
        subStages.push(cloneStageWithGroup(destinationStage, destinationGroup));
      }
      if (interfaceGroup) {
        subStages.push(cloneStageWithGroup(interfaceStage, interfaceGroup));
      }
      if (!subStages.length) {
        continue;
      }
      blocks.push({
        depth: destinationStage.depth,
        title: destinationCompositeTitle(destinationGroup, interfaceGroup),
        url: normalizeUrl((interfaceGroup && interfaceGroup.url) || (destinationGroup && destinationGroup.url)),
        role: 'destination_composite',
        isDestinationComposite: true,
        subStages: subStages,
      });
    }
    return blocks;
  }

  function cassetteNameFromConnectorLabel(label) {
    var text = String(label || '').trim();
    var name = text
      .replace(/(?:[._-])?(?:front|rear)[.-]?mpo[-.]?0*\d+\b/i, '')
      .replace(/[._-]+$/, '');
    return name || text;
  }

  function shuffleCassetteStageTitle(ingressStage, egressStage) {
    var names = [];
    [ingressStage, egressStage].forEach(function (stage) {
      (stage.connectorList || []).forEach(function (connector) {
        var name = cassetteNameFromConnectorLabel(connector.label);
        if (name && names.indexOf(name) === -1) {
          names.push(name);
        }
      });
    });
    if (!names.length) {
      return 'Shuffle Cassette';
    }
    return 'Shuffle Cassette: ' + names.join(' / ');
  }

  function connectorGroupsForStage(stage) {
    var groupsByKey = {};
    var passiveOrder = [];
    stage.connectorList.forEach(function (connector) {
      if (!groupsByKey[connector.familyKey]) {
        groupsByKey[connector.familyKey] = {
          key: connector.familyKey,
          label: connector.familyKey.replace(/[.-]$/, ''),
          connectors: [],
        };
        passiveOrder.push(connector.familyKey);
      }
      groupsByKey[connector.familyKey].connectors.push(connector);
    });

    var orderedKeys = [];
    stage.activeFamilyOrder.forEach(function (key) {
      if (groupsByKey[key] && orderedKeys.indexOf(key) === -1) {
        orderedKeys.push(key);
      }
    });
    passiveOrder.forEach(function (key) {
      if (orderedKeys.indexOf(key) === -1) {
        orderedKeys.push(key);
      }
    });

    return orderedKeys.map(function (key) {
      var group = groupsByKey[key];
      group.columnCount = group.connectors.length > 1 ? 2 : 1;
      group.url = normalizeUrl(
        (group.connectors[0] && (group.connectors[0].parentUrl || group.connectors[0].url)) || ''
      );
      if (stage.isShuffle && group.connectors.length >= GRID_CONNECTOR_COLUMNS) {
        group.columnCount = GRID_CONNECTOR_COLUMNS;
      }
      group.slotConnectorCount = group.columnCount;
      if (stage.isShuffle && group.slotConnectorCount < 2) {
        group.slotConnectorCount = 2;
      }
      if (stage.isInterfaceLayer) {
        group.slotConnectorCount = 1;
      }
      group.connectorRows = [];
      for (var index = 0; index < group.connectors.length; index += group.columnCount) {
        group.connectorRows.push(group.connectors.slice(index, index + group.columnCount));
      }
      group.height = 24 + Math.max(1, group.connectorRows.length) * CONNECTOR_ROW_STRIDE;
      return group;
    });
  }

  function drawText(svg, text, attrs, titleText) {
    var node = createSvgNode('text', attrs);
    if (!node.hasAttribute('pointer-events')) {
      node.setAttribute('pointer-events', 'none');
    }
    node.textContent = text;
    svg.appendChild(node);
    return node;
  }

  function appendTitle(node, titleText) {
    if (!titleText) {
      return node;
    }
    node.setAttribute('data-fanout-tooltip', String(titleText));
    return node;
  }

  function ensureTooltip(container) {
    var existing = container.querySelector('[data-fanout-tooltip-box]');
    if (existing) {
      return existing;
    }
    var tooltip = document.createElement('div');
    tooltip.setAttribute('data-fanout-tooltip-box', 'true');
    tooltip.style.position = 'fixed';
    tooltip.style.zIndex = '9999';
    tooltip.style.display = 'none';
    tooltip.style.maxWidth = '460px';
    tooltip.style.padding = '6px 8px';
    tooltip.style.border = '1px solid #4b5d73';
    tooltip.style.borderRadius = '4px';
    tooltip.style.background = '#050914';
    tooltip.style.color = '#d8e2f0';
    tooltip.style.font = '11px ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace';
    tooltip.style.boxShadow = '0 8px 24px rgba(0, 0, 0, 0.45)';
    tooltip.style.pointerEvents = 'none';
    tooltip.style.whiteSpace = 'normal';
    container.appendChild(tooltip);
    return tooltip;
  }

  function positionTooltip(event, tooltip) {
    var offset = 12;
    var left = event.clientX + offset;
    var top = event.clientY + offset;
    var viewportW = window.innerWidth || document.documentElement.clientWidth || 0;
    var viewportH = window.innerHeight || document.documentElement.clientHeight || 0;
    var rect = tooltip.getBoundingClientRect();
    if (viewportW && left + rect.width + offset > viewportW) {
      left = Math.max(offset, event.clientX - rect.width - offset);
    }
    if (viewportH && top + rect.height + offset > viewportH) {
      top = Math.max(offset, event.clientY - rect.height - offset);
    }
    tooltip.style.left = left + 'px';
    tooltip.style.top = top + 'px';
  }

  function installTooltips(container, svg) {
    var tooltip = ensureTooltip(container);
    if (svg.__fanoutTooltipInstalled) {
      return;
    }
    svg.__fanoutTooltipInstalled = true;

    svg.addEventListener('mousemove', function (event) {
      var node = event.target;
      while (node && node !== svg) {
        if (node.getAttribute && node.getAttribute('data-fanout-tooltip')) {
          tooltip.textContent = node.getAttribute('data-fanout-tooltip') || '';
          tooltip.style.display = tooltip.textContent ? 'block' : 'none';
          positionTooltip(event, tooltip);
          return;
        }
        node = node.parentNode;
      }
      tooltip.style.display = 'none';
    });

    svg.addEventListener('mouseleave', function () {
        tooltip.style.display = 'none';
    });
  }

  function drawMultilineText(svg, text, attrs, lineHeight, titleText) {
    var lines = String(text || '').split('\n');
    var y = Number(attrs.y) || 0;
    lines.forEach(function (line, index) {
      var lineAttrs = Object.assign({}, attrs, { y: y + index * lineHeight });
      drawText(svg, line, lineAttrs);
    });
  }

  function estimatedMultilineTextWidth(label, fontSize) {
    return String(label || '').split('\n').reduce(function (maxWidth, line) {
      return Math.max(maxWidth, line.length * fontSize * MONO_TEXT_WIDTH_FACTOR);
    }, 0);
  }

  function drawBraceLabel(svg, label, x, yTop, yBottom, options) {
    var height = Math.max(10, yBottom - yTop);
    var width = options && options.width ? options.width : 18;
    var lineHeight = options && options.lineHeight ? options.lineHeight : 12;
    var fontSize = options && options.fontSize ? options.fontSize : 11;
    var color = options && options.color ? options.color : ANNOTATION_BRACE_COLOR;
    var labelX = options && options.labelX ? options.labelX : x - 10;
    var centerY = yTop + height / 2;
    var curve = Math.min(18, Math.max(8, height / 4));
    var midGap = Math.min(8, Math.max(4, height / 10));
    var lines = String(label || '').split('\n');
    var textY = centerY - ((lines.length - 1) * lineHeight) / 2;
    var innerX = x + width * 0.35;
    var outerX = x + width * 1.35;

    svg.appendChild(
      appendTitle(
        createSvgNode('path', {
        d:
          'M ' +
          outerX +
          ' ' +
          yTop +
          ' C ' +
          innerX +
          ' ' +
          yTop +
          ', ' +
          innerX +
          ' ' +
          (yTop + curve) +
          ', ' +
          innerX +
          ' ' +
          (centerY - midGap) +
          ' C ' +
          innerX +
          ' ' +
          centerY +
          ', ' +
          x +
          ' ' +
          centerY +
          ', ' +
          x +
          ' ' +
          centerY +
          ' C ' +
          innerX +
          ' ' +
          centerY +
          ', ' +
          innerX +
          ' ' +
          (centerY + midGap) +
          ', ' +
          innerX +
          ' ' +
          (yBottom - curve) +
          ' C ' +
          innerX +
          ' ' +
          yBottom +
          ', ' +
          outerX +
          ' ' +
          yBottom +
          ', ' +
          outerX +
          ' ' +
          yBottom,
        fill: 'none',
        stroke: color,
        'stroke-width': options && options.strokeWidth ? options.strokeWidth : 2,
        'stroke-linecap': 'round',
        opacity: options && options.opacity ? options.opacity : 0.88,
        class: 'fanout-annotation-brace',
        }),
        label.replace(/\n/g, ' ')
      )
    );

    drawMultilineText(
      svg,
      label,
      {
        x: labelX,
        y: textY,
        fill: options && options.textColor ? options.textColor : ANNOTATION_TEXT_COLOR,
        'font-size': fontSize,
        'font-weight': options && options.fontWeight ? options.fontWeight : 600,
        'text-anchor': 'end',
        'dominant-baseline': 'middle',
        'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        class: 'fanout-annotation-label',
      },
      lineHeight,
      label.replace(/\n/g, ' ')
    );
  }

  function drawRightBraceLabel(svg, label, x, yTop, yBottom, options) {
    var height = Math.max(10, yBottom - yTop);
    var width = options && options.width ? options.width : 18;
    var lineHeight = options && options.lineHeight ? options.lineHeight : 12;
    var fontSize = options && options.fontSize ? options.fontSize : 11;
    var color = options && options.color ? options.color : ANNOTATION_BRACE_COLOR;
    var labelX = options && options.labelX ? options.labelX : x + 10;
    var centerY = yTop + height / 2;
    var curve = Math.min(18, Math.max(8, height / 4));
    var midGap = Math.min(8, Math.max(4, height / 10));
    var lines = String(label || '').split('\n');
    var textY = centerY - ((lines.length - 1) * lineHeight) / 2;
    var innerX = x - width * 0.35;
    var outerX = x - width * 1.35;

    svg.appendChild(
      appendTitle(
        createSvgNode('path', {
        d:
          'M ' +
          outerX +
          ' ' +
          yTop +
          ' C ' +
          innerX +
          ' ' +
          yTop +
          ', ' +
          innerX +
          ' ' +
          (yTop + curve) +
          ', ' +
          innerX +
          ' ' +
          (centerY - midGap) +
          ' C ' +
          innerX +
          ' ' +
          centerY +
          ', ' +
          x +
          ' ' +
          centerY +
          ', ' +
          x +
          ' ' +
          centerY +
          ' C ' +
          innerX +
          ' ' +
          centerY +
          ', ' +
          innerX +
          ' ' +
          (centerY + midGap) +
          ', ' +
          innerX +
          ' ' +
          (yBottom - curve) +
          ' C ' +
          innerX +
          ' ' +
          yBottom +
          ', ' +
          outerX +
          ' ' +
          yBottom +
          ', ' +
          outerX +
          ' ' +
          yBottom,
        fill: 'none',
        stroke: color,
        'stroke-width': options && options.strokeWidth ? options.strokeWidth : 2,
        'stroke-linecap': 'round',
        opacity: options && options.opacity ? options.opacity : 0.88,
        class: 'fanout-annotation-brace',
        }),
        label.replace(/\n/g, ' ')
      )
    );

    drawMultilineText(
      svg,
      label,
      {
        x: labelX,
        y: textY,
        fill: options && options.textColor ? options.textColor : ANNOTATION_TEXT_COLOR,
        'font-size': fontSize,
        'font-weight': options && options.fontWeight ? options.fontWeight : 600,
        'text-anchor': 'start',
        'dominant-baseline': 'middle',
        'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
        class: 'fanout-annotation-label',
      },
      lineHeight,
      label.replace(/\n/g, ' ')
    );
  }

  function layoutMetrics(width) {
    var connectorX = CONNECTOR_RAIL_X;
    var connectorAreaW = BASE_SCHEMATIC_WIDTH - connectorX - 32;
    var groupGap = 26;
    var innerGap = 12;
    var connectorW = Math.floor((connectorAreaW - groupGap - (GRID_CONNECTOR_COLUMNS - 2) * innerGap) / GRID_CONNECTOR_COLUMNS);
    return {
      connectorX: connectorX,
      connectorAreaW: width - connectorX - 32,
      groupGap: groupGap,
      innerGap: innerGap,
      connectorW: connectorW,
    };
  }

  function groupSlotWidth(group, metrics) {
    var slots = group.slotConnectorCount || group.columnCount || 1;
    return slots * metrics.connectorW + Math.max(0, slots - 1) * metrics.innerGap;
  }

  function groupX(stage, group, metrics, boxGap) {
    var columnIndex = Number.isFinite(Number(group.stageColumnIndex)) ? Number(group.stageColumnIndex) : 0;
    if (stage.isSourceInterfaceLayer) {
      var connector = group.connectors[0];
      return metrics.connectorX + columnIndex * interfaceChannelGroupStride(connector, metrics.connectorW, boxGap);
    }
    if (stage.isDestination || stage.isDestinationInterfaceLayer || group.slotConnectorCount === 1) {
      return metrics.connectorX + columnIndex * (metrics.connectorW + metrics.innerGap);
    }
    return metrics.connectorX + columnIndex * (groupSlotWidth(group, metrics) + metrics.groupGap);
  }

  function stageRightEdge(stage, metrics, boxGap) {
    var rightEdge = metrics.connectorX;
    function visitStageRows(rowStage) {
      (rowStage.groupRows || []).forEach(function (groupRow) {
        groupRow.forEach(function (group) {
          var groupW = groupSlotWidth(group, metrics);
          var currentGroupX = groupX(rowStage, group, metrics, boxGap);
          var groupFrame = groupFrameGeometry(rowStage, group, currentGroupX, groupW, metrics, boxGap);
          rightEdge = Math.max(rightEdge, groupFrame.x + groupFrame.width + 32);
        });
      });
    }

    if (stage.subStages) {
      (stage.subStages || []).forEach(visitStageRows);
    } else {
      visitStageRows(stage);
    }
    return rightEdge;
  }

  function schematicWidthForStages(stages) {
    var metrics = layoutMetrics(BASE_SCHEMATIC_WIDTH);
    var boxGap = 3;
    return Math.max(
      BASE_SCHEMATIC_WIDTH,
      Math.ceil(stages.reduce(function (maxRight, stage) {
        return Math.max(maxRight, stageRightEdge(stage, metrics, boxGap));
      }, BASE_SCHEMATIC_WIDTH))
    );
  }

  function interfaceChannelSizing(connector, connectorW, boxGap) {
    var mpoBoxW = Math.floor((connectorW - boxGap * (POSITION_COUNT + 1)) / POSITION_COUNT);
    var activePositionCount = connector.positionCount || CHANNEL_POSITION_COUNT;
    var activeWidth = activePositionCount * mpoBoxW + Math.max(0, activePositionCount - 1) * boxGap;
    return {
      boxW: mpoBoxW,
      activeWidth: activeWidth,
      frameWidth: activeWidth + boxGap * 2,
    };
  }

  function interfaceChannelGroupWidth(connector, connectorW, boxGap) {
    return interfaceChannelSizing(connector, connectorW, boxGap).frameWidth + 16;
  }

  function interfaceChannelGroupStride(connector, connectorW, boxGap) {
    var groupWidth = interfaceChannelGroupWidth(connector, connectorW, boxGap);
    return groupWidth + Math.round(groupWidth * INTERFACE_CHANNEL_GROUP_SPACER_RATIO);
  }

  function connectorFrameGeometry(connector, stage, cellX, connectorW, boxGap) {
    if (connector.kind !== 'interface_channel') {
      return {
        x: cellX,
        width: connectorW,
      };
    }

    var sizing = interfaceChannelSizing(connector, connectorW, boxGap);
    var frameX = cellX;
    if (stage.isDestinationInterfaceLayer) {
      frameX = cellX + Math.floor((connectorW - sizing.frameWidth) / 2);
    }

    return {
      x: frameX,
      width: sizing.frameWidth,
    };
  }

  function groupFrameGeometry(stage, group, currentGroupX, groupW, metrics, boxGap) {
    if (!stage.isInterfaceLayer) {
      return {
        x: currentGroupX - 8,
        width: groupW + 16,
      };
    }

    var connector = group.connectors[0];
    var connectorFrame = connectorFrameGeometry(connector, stage, currentGroupX, metrics.connectorW, boxGap);
    return {
      x: connectorFrame.x - 8,
      width: connectorFrame.width + 16,
    };
  }

  function positionBoxGeometry(connector, stage, cellX, connectorW, boxGap) {
    var logicalPositionCount = connector.positionCount || POSITION_COUNT;
    var defaultBoxW = Math.floor((connectorW - boxGap * (logicalPositionCount + 1)) / logicalPositionCount);
    if (connector.kind !== 'interface_channel') {
      return {
        boxW: defaultBoxW,
        startX: cellX + boxGap,
        step: defaultBoxW + boxGap,
      };
    }

    var sizing = interfaceChannelSizing(connector, connectorW, boxGap);
    var frame = connectorFrameGeometry(connector, stage, cellX, connectorW, boxGap);

    return {
      boxW: sizing.boxW,
      startX: frame.x + boxGap,
      step: sizing.boxW + boxGap,
    };
  }

  function groupRowsHeight(stage) {
    var groupRows = stage.groupRows.length ? stage.groupRows : [[]];
    return groupRows.reduce(function (total, groupRow) {
      var rowHeight = groupRow.reduce(function (maxHeight, group) {
        return Math.max(maxHeight, group.height || 82);
      }, 82);
      return total + rowHeight + GROUP_ROW_GAP;
    }, 0);
  }

  function stageHeight(stage) {
    if (stage.isSourceComposite) {
      return 42 + stage.subStages.reduce(function (total, subStage, index) {
        return total + groupRowsHeight(subStage) + (index < stage.subStages.length - 1 ? SOURCE_COMPOSITE_LAYER_GAP : 0);
      }, 0);
    }
    if (stage.isDestinationComposite) {
      return 42 + stage.subStages.reduce(function (total, subStage, index) {
        return total + groupRowsHeight(subStage) + (index < stage.subStages.length - 1 ? DESTINATION_COMPOSITE_LAYER_GAP : 0);
      }, 0);
    }
    if (stage.isShuffleCassette) {
      return 42 + stage.subStages.reduce(function (total, subStage, index) {
        return total + groupRowsHeight(subStage) + (index < stage.subStages.length - 1 ? SHUFFLE_CASSETTE_SIDE_GAP : 0);
      }, 0);
    }
    return 42 + groupRowsHeight(stage);
  }

  function sectionTitleLines(title) {
    var text = String(title || '');
    var colonIndex = text.indexOf(':');
    if (colonIndex === -1) {
      return [text];
    }
    return [
      text.slice(0, colonIndex + 1),
      '     ' + text.slice(colonIndex + 1).trimStart(),
    ];
  }

  function drawStageTitle(svg, title, attrs) {
    var lines = sectionTitleLines(title);
    var lineHeight = 16;
    var baseY = lines.length > 1 ? Number(attrs.y) - 4 : Number(attrs.y);
    lines.forEach(function (line, index) {
      drawText(
        svg,
        line,
        Object.assign({}, attrs, {
          y: baseY + index * lineHeight,
          'xml:space': 'preserve',
        })
      );
    });
  }

  function drawStageShell(svg, stage, top, width, stageH, options) {
    var shellX = options && Number.isFinite(Number(options.x)) ? Number(options.x) : 16;
    var shellW = options && Number.isFinite(Number(options.width)) ? Number(options.width) : width - 32;
    var title = options && options.title ? options.title : stage.title;
    var shellUrl = normalizeUrl(options && options.url) || normalizeUrl(stage.url);
    var titleAnchor = options && options.titleAnchor ? options.titleAnchor : 'start';
    var titleX =
      options && Number.isFinite(Number(options.titleX))
        ? Number(options.titleX)
        : titleAnchor === 'end'
          ? shellX + shellW - 14
          : shellX + 14;

    svg.appendChild(
      linkedSvgNode(
        createSvgNode('rect', {
        x: shellX,
        y: top,
        width: shellW,
        height: stageH,
        rx: 4,
        fill: '#0b111c',
        stroke: '#202936',
        'stroke-width': 1,
        'data-fanout-stage-role': stage.role || '',
        'data-fanout-stage-title': title || '',
        }),
        shellUrl,
        title
      )
    );

    drawStageTitle(svg, title, {
      x: titleX,
      y: top + 20,
      fill: '#9eabbc',
      'font-size': 14,
      'text-anchor': titleAnchor,
      'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
    });
  }

  function cassetteNameFromGroup(group) {
    var connector = group && group.connectors && group.connectors[0];
    return cassetteNameFromConnectorLabel(connector ? connector.label : group && group.label);
  }

  function renderedConnectorGroupBounds(stage, group, currentGroupX, groupW, metrics, boxGap) {
    var connectorW = metrics.connectorW;
    var left = Number.POSITIVE_INFINITY;
    var right = Number.NEGATIVE_INFINITY;
    (group.connectorRows || []).forEach(function (connectorRow) {
      connectorRow.forEach(function (connector, columnIndex) {
        var cellX = currentGroupX + columnIndex * (connectorW + metrics.innerGap);
        var connectorFrame = connectorFrameGeometry(connector, stage, cellX, connectorW, boxGap);
        left = Math.min(left, connectorFrame.x);
        right = Math.max(right, connectorFrame.x + connectorFrame.width);
      });
    });

    if (Number.isFinite(left) && Number.isFinite(right)) {
      return {
        left: left,
        right: right,
      };
    }

    var groupFrame = groupFrameGeometry(stage, group, currentGroupX, groupW, metrics, boxGap);
    return {
      left: groupFrame.x,
      right: groupFrame.x + groupFrame.width,
    };
  }

  function shuffleCassetteShellEntries(stage, metrics, boxGap) {
    var entries = [];
    var entriesByName = {};

    (stage.subStages || []).forEach(function (subStage) {
      (subStage.groupRows || []).forEach(function (groupRow) {
        groupRow.forEach(function (group) {
          var name = cassetteNameFromGroup(group);
          var columnIndex = Number.isFinite(Number(group.stageColumnIndex)) ? Number(group.stageColumnIndex) : entries.length;
          var groupW = groupSlotWidth(group, metrics);
          var currentGroupX = groupX(subStage, group, metrics, boxGap);
          var groupBounds = renderedConnectorGroupBounds(subStage, group, currentGroupX, groupW, metrics, boxGap);

          if (!entriesByName[name]) {
            entriesByName[name] = {
              name: name || 'cassette',
              columnIndex: columnIndex,
              left: groupBounds.left,
              right: groupBounds.right,
              url: group.url,
            };
            entries.push(entriesByName[name]);
          }

          entriesByName[name].columnIndex = Math.min(entriesByName[name].columnIndex, columnIndex);
          entriesByName[name].left = Math.min(entriesByName[name].left, groupBounds.left);
          entriesByName[name].right = Math.max(entriesByName[name].right, groupBounds.right);
          if (!entriesByName[name].url && group.url) {
            entriesByName[name].url = group.url;
          }
        });
      });
    });

    entries.sort(function (first, second) {
      return first.columnIndex - second.columnIndex;
    });
    return entries;
  }

  function drawShuffleCassetteShells(svg, stage, top, width, stageH, metrics, boxGap) {
    var entries = shuffleCassetteShellEntries(stage, metrics, boxGap);
    if (entries.length <= 1) {
      drawStageShell(svg, stage, top, width, stageH, {
        title: entries.length ? 'Shuffle Cassette: ' + entries[0].name : stage.title,
        url: entries.length ? entries[0].url : stage.url,
      });
      return;
    }

    var previousShellRight = 16;
    entries.forEach(function (entry, index) {
      var shellX = index === 0 ? 16 : previousShellRight + 4;
      var shellRight = Math.max(shellX + 48, Math.ceil(entry.right + 4));
      drawStageShell(svg, stage, top, width, stageH, {
        x: shellX,
        width: shellRight - shellX,
        title: 'Shuffle Cassette: ' + entry.name,
        titleAnchor: index === 0 ? 'start' : 'end',
        url: entry.url,
      });
      previousShellRight = shellRight;
    });
  }

  function stageLayerLabel(stage) {
    if (stage.isSourceInterfaceLayer || stage.isDestinationInterfaceLayer) {
      return '200gbps\nEthernet\nInterface';
    }
    if (stage.role === 'source_endpoint' || stage.role === 'destination_endpoint') {
      return 'MPO12\nconnectors';
    }
    if (stage.role === 'shuffle_ingress') {
      return 'Cassette\nfront MPOs';
    }
    if (stage.role === 'shuffle_egress') {
      return 'Cassette\nrear MPOs';
    }
    return stage.title;
  }

  function secondaryLayerLabel(stage) {
    if (stage.isInterfaceLayer) {
      return 'Optical\nlanes';
    }
    return 'Fiber\npositions';
  }

  function stageAnnotationOffsetX(stage, metrics, boxGap) {
    if (!(stage.isDestination || stage.isDestinationInterfaceLayer)) {
      return 0;
    }
    var firstGroup = stage.groupRows && stage.groupRows[0] && stage.groupRows[0][0];
    if (!firstGroup) {
      return 0;
    }
    var currentGroupX = groupX(stage, firstGroup, metrics, boxGap);
    if (stage.isDestinationInterfaceLayer && firstGroup.connectors && firstGroup.connectors[0]) {
      return connectorFrameGeometry(firstGroup.connectors[0], stage, currentGroupX, metrics.connectorW, boxGap).x - metrics.connectorX;
    }
    return currentGroupX - metrics.connectorX;
  }

  function drawStageLayerAnnotations(svg, stage, yTop, yBottom, options) {
    if (yBottom <= yTop) {
      return;
    }
    var offsetX = options && Number.isFinite(Number(options.offsetX)) ? Number(options.offsetX) : 0;
    var centerY = yTop + (yBottom - yTop) / 2;
    var secondaryTop = centerY - 17;
    var secondaryBottom = centerY + 17;
    var primaryLabel = stageLayerLabel(stage);
    var secondaryLabel = secondaryLayerLabel(stage);
    var primaryBraceWidth = 16;
    var secondaryFontSize = 9;
    var secondaryLabelX = POSITION_BRACE_LABEL_X + offsetX;
    var secondaryLabelLeft = secondaryLabelX - estimatedMultilineTextWidth(secondaryLabel, secondaryFontSize);
    var primaryBraceX = secondaryLabelLeft - OUTER_TO_INNER_LABEL_GAP - primaryBraceWidth * 1.35;
    drawBraceLabel(svg, primaryLabel, primaryBraceX, yTop, yBottom, {
      labelX: primaryBraceX - 10,
      width: primaryBraceWidth,
      fontSize: 10,
      lineHeight: 11,
    });
    drawBraceLabel(svg, secondaryLabel, POSITION_BRACE_X + offsetX, secondaryTop, secondaryBottom, {
      labelX: secondaryLabelX,
      width: 14,
      fontSize: secondaryFontSize,
      lineHeight: 10,
      strokeWidth: 1.5,
      opacity: 0.76,
    });
  }

  function drawStageRangeAnnotation(svg, label, yTop, yBottom) {
    drawBraceLabel(svg, label, RANGE_BRACE_X, yTop, yBottom, {
      labelX: RANGE_BRACE_LABEL_X,
      width: 22,
      fontSize: 11,
      lineHeight: 12,
      strokeWidth: 2.2,
      opacity: 0.9,
    });
  }

  function drawGroupRows(svg, stage, groupTop, metrics, connectorH, boxGap, options) {
    var anchors = {};
    var initialTop = groupTop;
    var showInlineLabels = !(options && options.hideInlineLabels);
    stage.groupRows.forEach(function (groupRow) {
      var rowHeight = groupRow.reduce(function (maxHeight, group) {
        return Math.max(maxHeight, group.height || 82);
      }, 82);
      groupRow.forEach(function (group, rowColumnIndex) {
        if (!Number.isFinite(Number(group.stageColumnIndex))) {
          group.stageColumnIndex = rowColumnIndex;
        }
        var groupW = groupSlotWidth(group, metrics);
        var currentGroupX = groupX(stage, group, metrics, boxGap);
        var connectorW = metrics.connectorW;
        var groupFrame = groupFrameGeometry(stage, group, currentGroupX, groupW, metrics, boxGap);

        if (showInlineLabels) {
          drawText(
            svg,
            compactMiddle(shortConnectorLabel(group.label), 46),
            {
              x: groupFrame.x + 8,
              y: groupTop + 10,
              fill: '#7fa6d7',
              'font-size': 10,
              'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
            },
            group.label
          );
        }

        if (stage.isInterfaceLayer) {
          svg.appendChild(
            linkedSvgNode(
              appendTitle(
                createSvgNode('rect', {
              x: groupFrame.x,
              y: groupTop + GROUP_FRAME_TOP_OFFSET,
              width: groupFrame.width,
              height: rowHeight - 20,
              rx: 4,
              fill: '#080d14',
              opacity: 0.62,
              stroke: '#253447',
              'stroke-width': 1,
                }),
                group.label
              ),
              group.url,
              group.label
            )
          );
        }

        group.connectorRows.forEach(function (connectorRow, rowIndex) {
          var rowY = groupTop + CONNECTOR_ROW_TOP_OFFSET + rowIndex * CONNECTOR_ROW_STRIDE;
          connectorRow.forEach(function (connector, columnIndex) {
            var cellX = currentGroupX + columnIndex * (connectorW + metrics.innerGap);
            var connectorLabel = compactMiddle(shortConnectorLabel(connector.label), 28);
            var positionCount = connector.positionCount || POSITION_COUNT;
            var connectorFrame = connectorFrameGeometry(connector, stage, cellX, connectorW, boxGap);
            var connectorFrameY = rowY;
            var connectorFrameH = connectorH;
            if (connector.kind !== 'interface_channel') {
              connectorFrameY = groupTop + GROUP_FRAME_TOP_OFFSET + rowIndex * CONNECTOR_ROW_STRIDE;
              connectorFrameH =
                rowIndex === group.connectorRows.length - 1
                  ? rowHeight - 20 - rowIndex * CONNECTOR_ROW_STRIDE
                  : CONNECTOR_ROW_STRIDE;
            }
            if (showInlineLabels) {
              drawText(
                svg,
                connectorLabel,
                {
                  x: connectorFrame.x,
                  y: rowY - 5,
                  fill: '#b4bfd0',
                  'font-size': 9,
                  'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
                },
                connector.label
              );
            }

            if (connector.kind !== 'interface_channel') {
              svg.appendChild(
                linkedSvgNode(
                  appendTitle(
                    createSvgNode('rect', {
                  x: connectorFrame.x,
                  y: connectorFrameY,
                  width: connectorFrame.width,
                  height: connectorFrameH,
                  rx: 4,
                  fill: '#080d14',
                  stroke: '#3c4450',
                  'stroke-width': 1.1,
                    }),
                    connector.label
                  ),
                  connector.url,
                  connector.label
                )
              );
            }

            var boxGeometry = positionBoxGeometry(connector, stage, cellX, connectorW, boxGap);
            for (var position = 1; position <= positionCount; position += 1) {
              var boxX = boxGeometry.startX + (position - 1) * boxGeometry.step;
              var boxW = boxGeometry.boxW;
              var activeColor = activePositionColor(connector, position);
              var isActive = Boolean(activeColor);
              svg.appendChild(
                linkedSvgNode(
                  appendTitle(
                    createSvgNode('rect', {
                  x: boxX,
                  y: rowY + 8,
                  width: boxW,
                  height: 18,
                  rx: 2,
                  fill: isActive ? activeColor : '#121923',
                  opacity: isActive ? 0.95 : 0.42,
                  stroke: isActive ? '#f6f8fb' : '#303744',
                  'stroke-width': isActive ? 1.1 : 0.8,
                    }),
                    connector.kind === 'interface_channel'
                      ? connector.label + ' lane ' + position
                      : connector.label + ' position ' + position
                  ),
                  activePositionUrl(connector, position),
                  connector.kind === 'interface_channel'
                    ? connector.label + ' lane ' + position
                    : connector.label + ' position ' + position
                )
              );
              drawText(svg, String(position), {
                x: boxX + boxW / 2,
                y: rowY + 20,
                fill: isActive ? '#ffffff' : '#9aa3b2',
                'font-size': 8,
                'text-anchor': 'middle',
                'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
              });
              anchors[connector.key + '|' + position] = {
                x: boxX + boxW / 2,
                yTop: rowY,
                yBottom: rowY + connectorH,
              };
            }
          });
        });
      });
      groupTop += rowHeight + GROUP_ROW_GAP;
    });

    return {
      anchors: anchors,
      height: groupTop - initialTop,
    };
  }

  function compositeStageGap(stage, index) {
    if (index !== 0) {
      return 0;
    }
    if (stage.isSourceComposite) {
      return SOURCE_COMPOSITE_LAYER_GAP;
    }
    if (stage.isDestinationComposite) {
      return DESTINATION_COMPOSITE_LAYER_GAP;
    }
    return 0;
  }

  function drawCompositeStage(svg, stage, top, width) {
    var stageH = stageHeight(stage);
    var metrics = layoutMetrics(width);
    var connectorH = 34;
    var boxGap = 3;
    var anchors = {};
    var groupTop = top + 42;
    var subStageRanges = [];

    drawStageShell(svg, stage, top, width, stageH);

    stage.subStages.forEach(function (subStage, index) {
      var subStageTop = groupTop;
      var result = drawGroupRows(svg, subStage, groupTop, metrics, connectorH, boxGap, { hideInlineLabels: true });
      Object.keys(result.anchors).forEach(function (key) {
        anchors[key] = result.anchors[key];
      });
      subStageRanges.push({
        stage: subStage,
        yTop: subStageTop + 8,
        yBottom: subStageTop + result.height - 14,
        offsetX: stageAnnotationOffsetX(subStage, metrics, boxGap),
      });
      groupTop += result.height + (index < stage.subStages.length - 1 ? compositeStageGap(stage, index) : 0);
    });
    subStageRanges.forEach(function (range) {
      drawStageLayerAnnotations(svg, range.stage, range.yTop, range.yBottom, { offsetX: range.offsetX });
    });

    return {
      height: stageH,
      anchors: anchors,
    };
  }

  function drawShuffleCassetteStage(svg, stage, top, width) {
    var stageH = stageHeight(stage);
    var metrics = layoutMetrics(width);
    var connectorH = 34;
    var boxGap = 3;
    var anchors = {};
    var groupTop = top + 42;
    var subStageRanges = [];

    drawShuffleCassetteShells(svg, stage, top, width, stageH, metrics, boxGap);

    stage.subStages.forEach(function (subStage, index) {
      var subStageTop = groupTop;
      var result = drawGroupRows(svg, subStage, groupTop, metrics, connectorH, boxGap, { hideInlineLabels: true });
      Object.keys(result.anchors).forEach(function (key) {
        anchors[key] = result.anchors[key];
      });
      subStageRanges.push({
        stage: subStage,
        yTop: subStageTop + 8,
        yBottom: subStageTop + result.height - 14,
        offsetX: stageAnnotationOffsetX(subStage, metrics, boxGap),
      });
      groupTop += result.height + (index < stage.subStages.length - 1 ? SHUFFLE_CASSETTE_SIDE_GAP : 0);
    });
    subStageRanges.forEach(function (range) {
      drawStageLayerAnnotations(svg, range.stage, range.yTop, range.yBottom, { offsetX: range.offsetX });
    });

    return {
      height: stageH,
      anchors: anchors,
    };
  }

  function drawStage(svg, stage, top, width) {
    if (stage.isSourceComposite || stage.isDestinationComposite) {
      return drawCompositeStage(svg, stage, top, width);
    }
    if (stage.isShuffleCassette) {
      return drawShuffleCassetteStage(svg, stage, top, width);
    }

    var stageH = stageHeight(stage);
    var metrics = layoutMetrics(width);
    var connectorH = 34;
    var boxGap = 3;
    drawStageShell(svg, stage, top, width, stageH);
    var result = drawGroupRows(svg, stage, top + 42, metrics, connectorH, boxGap, { hideInlineLabels: true });
    drawStageLayerAnnotations(svg, stage, top + 50, top + 42 + result.height - 14, {
      offsetX: stageAnnotationOffsetX(stage, metrics, boxGap),
    });
    return {
      height: stageH,
      anchors: result.anchors,
    };
  }

  function anchorForHop(stageAnchorMap, hop) {
    var position = asPosition(hop.position);
    if (!position) {
      return null;
    }
    return stageAnchorMap[(hop.endpoint_label || '') + '|' + position] || null;
  }

  function drawLanePath(svg, path, laneIndex, stageLayouts, color) {
    var hops = path.connector_hops || [];
    if (hops.length < 2) {
      return;
    }
    var laneOffset = ((laneIndex % 4) - 1.5) * 1.55;
    for (var i = 0; i < hops.length - 1; i += 1) {
      var fromAnchor = stageLayouts[i] ? anchorForHop(stageLayouts[i].anchors, hops[i]) : null;
      var toAnchor = stageLayouts[i + 1] ? anchorForHop(stageLayouts[i + 1].anchors, hops[i + 1]) : null;
      if (!fromAnchor || !toAnchor) {
        continue;
      }
      var d =
        'M ' +
        fromAnchor.x +
        ' ' +
        fromAnchor.yBottom +
        ' C ' +
        fromAnchor.x +
        ' ' +
        (fromAnchor.yBottom + 26) +
        ', ' +
        toAnchor.x +
        ' ' +
        (toAnchor.yTop - 26) +
        ', ' +
        toAnchor.x +
          ' ' +
          toAnchor.yTop;
      var isCassetteInternal = isShuffleCassetteInternalHop(hops[i], hops[i + 1]);
      svg.appendChild(
        createSvgNode('path', {
          d: d,
          fill: 'none',
          stroke: color,
              'stroke-width': path.bundle_size ? 4.5 : 2.3,
              'stroke-linecap': 'round',
              'stroke-linejoin': 'round',
              opacity: isCassetteInternal ? SHUFFLE_CASSETTE_STRAND_OPACITY : (path.bundle_size ? 0.78 : 0.94),
              'pointer-events': 'none',
              transform: 'translate(0,' + laneOffset + ')',
            })
          );
    }
  }

  function mpoDescriptor(label) {
    var match = String(label || '').match(/\b(front|rear)-mpo[-.]0*(\d+)\b/i);
    if (!match) {
      return null;
    }
    return {
      side: match[1].toLowerCase(),
      index: Number(match[2]),
    };
  }

  function cassetteIdentity(label) {
    return String(label || '').replace(/\b(?:front|rear)-mpo[-.]0*\d+\b/i, 'cassette-mpo');
  }

  function keyDownRoll(position) {
    return POSITION_COUNT + 1 - Number(position);
  }

  function keyDownPairs(sourcePositions, destinationBasePositions) {
    return sourcePositions.map(function (sourcePosition, index) {
      return [sourcePosition, keyDownRoll(destinationBasePositions[index])];
    });
  }

  function shuffleTransferPairs(frontIndex, rearIndex) {
    var groupA = [1, 12, 2, 11];
    var groupB = [3, 10, 4, 9];
    var firstFront = frontIndex <= 2 ? 1 : 3;
    var secondFront = firstFront + 1;
    if (frontIndex !== firstFront && frontIndex !== secondFront) {
      return [];
    }
    if (rearIndex !== firstFront && rearIndex !== secondFront) {
      return [];
    }
    if (frontIndex === firstFront && rearIndex === firstFront) {
      return keyDownPairs(groupA, groupA);
    }
    if (frontIndex === firstFront && rearIndex === secondFront) {
      return keyDownPairs(groupB, groupA);
    }
    if (frontIndex === secondFront && rearIndex === firstFront) {
      return keyDownPairs(groupA, groupB);
    }
    if (frontIndex === secondFront && rearIndex === secondFront) {
      return keyDownPairs(groupB, groupB);
    }
    return [];
  }

  function cassetteTransferPairs(fromLabel, toLabel) {
    if (cassetteIdentity(fromLabel) !== cassetteIdentity(toLabel)) {
      return [];
    }
    var from = mpoDescriptor(fromLabel);
    var to = mpoDescriptor(toLabel);
    if (!from || !to || from.side === to.side) {
      return [];
    }
    if (from.side === 'front' && to.side === 'rear') {
      return shuffleTransferPairs(from.index, to.index);
    }
    return shuffleTransferPairs(to.index, from.index).map(function (pair) {
      return [pair[1], pair[0]];
    });
  }

  function isShuffleCassetteInternalHop(fromHop, toHop) {
    if (!fromHop || !toHop) {
      return false;
    }
    var fromPosition = asPosition(fromHop.position);
    var toPosition = asPosition(toHop.position);
    if (!fromPosition || !toPosition) {
      return false;
    }
    return cassetteTransferPairs(fromHop.endpoint_label, toHop.endpoint_label).some(function (pair) {
      return pair[0] === fromPosition && pair[1] === toPosition;
    });
  }

  function internalStrandKey(fromLabel, fromPosition, toLabel, toPosition) {
    return [fromLabel, fromPosition, toLabel, toPosition].join('|');
  }

  function activeCassetteStrandKeys(paths, ingressDepth, egressDepth) {
    var active = {};
    paths.forEach(function (path) {
      var hops = path.connector_hops || [];
      var fromHop = hops[ingressDepth];
      var toHop = hops[egressDepth];
      if (!fromHop || !toHop) {
        return;
      }
      active[internalStrandKey(fromHop.endpoint_label, fromHop.position, toHop.endpoint_label, toHop.position)] = true;
    });
    return active;
  }

  function drawPassiveShuffleCassetteStrands(svg, stage, layout, paths) {
    if (!stage.isShuffleCassette || !stage.subStages || stage.subStages.length < 2) {
      return;
    }
    var ingressStage = stage.subStages[0];
    var egressStage = stage.subStages[1];
    var activeKeys = activeCassetteStrandKeys(paths, ingressStage.depth, egressStage.depth);

    ingressStage.connectorList.forEach(function (ingressConnector) {
      egressStage.connectorList.forEach(function (egressConnector) {
        cassetteTransferPairs(ingressConnector.label, egressConnector.label).forEach(function (pair) {
          var key = internalStrandKey(ingressConnector.label, pair[0], egressConnector.label, pair[1]);
          if (activeKeys[key]) {
            return;
          }
          var fromAnchor = layout.anchors[ingressConnector.key + '|' + pair[0]];
          var toAnchor = layout.anchors[egressConnector.key + '|' + pair[1]];
          if (!fromAnchor || !toAnchor) {
            return;
          }
          svg.appendChild(
            createSvgNode('path', {
              d:
                'M ' +
                fromAnchor.x +
                ' ' +
                fromAnchor.yBottom +
                ' C ' +
                fromAnchor.x +
                ' ' +
                (fromAnchor.yBottom + 22) +
                ', ' +
                toAnchor.x +
                ' ' +
                (toAnchor.yTop - 22) +
                ', ' +
                toAnchor.x +
                ' ' +
                toAnchor.yTop,
              fill: 'none',
              stroke: UNUSED_STRAND_COLOR,
              'stroke-width': 1.15,
              'stroke-linecap': 'round',
              'stroke-linejoin': 'round',
              opacity: SHUFFLE_CASSETTE_STRAND_OPACITY,
              'pointer-events': 'none',
            })
          );
        });
      });
    });
  }

  function pathsHaveCableAssemblySpans(paths) {
    return (paths || []).some(function (path) {
      return (path.cable_spans || []).some(function (span) {
        return span && span.cable_assembly;
      });
    });
  }

  function cableAssemblyDisplay(cableAssembly) {
    if (!cableAssembly) {
      return 'Unassigned cable assembly';
    }
    return cableAssembly.display || cableAssembly.label || cableAssembly.cable_id || 'Cable assembly';
  }

  function cableAssemblyLabel(cableAssembly) {
    if (!cableAssembly) {
      return 'cable-\nassembly:\nunassigned';
    }
    return 'cable-\nassembly:\n' + compactMiddle(cableAssembly.label || cableAssembly.cable_id || cableAssembly.display, 24);
  }

  function anchorForHopIndex(stageLayouts, hops, hopIndex) {
    var normalizedIndex = Number(hopIndex);
    if (!Number.isFinite(normalizedIndex)) {
      return null;
    }
    var hop = hops[normalizedIndex];
    var layout = stageLayouts[normalizedIndex];
    if (!hop || !layout) {
      return null;
    }
    return anchorForHop(layout.anchors, hop);
  }

  function xAtCableY(segment, y) {
    var yStart = segment.fromAnchor.yBottom;
    var yEnd = segment.toAnchor.yTop;
    var distance = yEnd - yStart;
    if (Math.abs(distance) < 1) {
      return (segment.fromAnchor.x + segment.toAnchor.x) / 2;
    }
    var t = Math.max(0, Math.min(1, (y - yStart) / distance));
    return segment.fromAnchor.x + (segment.toAnchor.x - segment.fromAnchor.x) * t;
  }

  function cableAssemblyGroups(paths, stageLayouts) {
    var groupsByKey = {};
    (paths || []).forEach(function (path, pathIndex) {
      var hops = path.connector_hops || [];
      (path.cable_spans || []).forEach(function (span) {
        var cableAssembly = span.cable_assembly;
        if (!cableAssembly) {
          return;
        }
        var fromIndex = Number(span.from_hop_index);
        var toIndex = Number(span.to_hop_index);
        var fromAnchor = anchorForHopIndex(stageLayouts, hops, fromIndex);
        var toAnchor = anchorForHopIndex(stageLayouts, hops, toIndex);
        if (!fromAnchor || !toAnchor) {
          return;
        }
        if (fromAnchor.visualIndex === toAnchor.visualIndex) {
          return;
        }
        var groupKey = [
          cableAssembly.key || cableAssembly.display || cableAssembly.label || 'cable',
          fromIndex,
          toIndex,
          fromAnchor.visualIndex,
          toAnchor.visualIndex,
        ].join('|');
        if (!groupsByKey[groupKey]) {
          groupsByKey[groupKey] = {
            cableAssembly: cableAssembly,
            fromIndex: fromIndex,
            toIndex: toIndex,
            fromVisualIndex: fromAnchor.visualIndex,
            toVisualIndex: toAnchor.visualIndex,
            segments: [],
          };
        }
        groupsByKey[groupKey].segments.push({
          fromAnchor: fromAnchor,
          toAnchor: toAnchor,
          pathIndex: pathIndex,
        });
      });
    });
    return Object.keys(groupsByKey).map(function (key) {
      return groupsByKey[key];
    });
  }

  function ensureCableCylinderGradient(svg) {
    var gradientId = 'fanout-cable-cylinder-gradient';
    if (svg.querySelector('#' + gradientId)) {
      return gradientId;
    }
    var defs = svg.querySelector('defs');
    if (!defs) {
      defs = createSvgNode('defs');
      svg.insertBefore(defs, svg.firstChild);
    }
    var gradient = createSvgNode('linearGradient', {
      id: gradientId,
      x1: '0%',
      y1: '0%',
      x2: '100%',
      y2: '0%',
    });
    gradient.appendChild(createSvgNode('stop', { offset: '0%', 'stop-color': '#737b84', 'stop-opacity': 0.2 }));
    gradient.appendChild(createSvgNode('stop', { offset: '50%', 'stop-color': '#aeb5bd', 'stop-opacity': 0.34 }));
    gradient.appendChild(createSvgNode('stop', { offset: '100%', 'stop-color': '#737b84', 'stop-opacity': 0.2 }));
    defs.appendChild(gradient);
    return gradientId;
  }

  function drawCableCylinder(svg, group, geometry) {
    var gradientId = ensureCableCylinderGradient(svg);
    var tooltip = 'Cable assembly: ' + cableAssemblyDisplay(group.cableAssembly);
    var cylinder = appendTitle(
      createSvgNode('g', {
        class: 'fanout-cable-assembly-cylinder',
        'data-fanout-cable-assembly': cableAssemblyDisplay(group.cableAssembly),
      }),
      tooltip
    );
    var x = geometry.x;
    var y = geometry.y;
    var width = geometry.width;
    var height = geometry.height;
    var radiusY = Math.max(5, Math.min(9, height * 0.08));

    cylinder.appendChild(
      createSvgNode('rect', {
        x: x,
        y: y + radiusY,
        width: width,
        height: Math.max(4, height - radiusY * 2),
        fill: 'url(#' + gradientId + ')',
        opacity: 0.46,
      })
    );
    cylinder.appendChild(
      createSvgNode('ellipse', {
        cx: x + width / 2,
        cy: y + radiusY,
        rx: width / 2,
        ry: radiusY,
        fill: 'url(#' + gradientId + ')',
        opacity: 0.24,
      })
    );
    cylinder.appendChild(
      createSvgNode('ellipse', {
        cx: x + width / 2,
        cy: y + height - radiusY,
        rx: width / 2,
        ry: radiusY,
        fill: '#717982',
        opacity: 0.16,
      })
    );
    cylinder.appendChild(
      createSvgNode('path', {
        d:
          'M ' +
          x +
          ' ' +
          (y + radiusY) +
          ' C ' +
          x +
          ' ' +
          (y + height * 0.5) +
          ', ' +
          x +
          ' ' +
          (y + height - radiusY) +
          ', ' +
          x +
          ' ' +
          (y + height - radiusY) +
          ' M ' +
          (x + width) +
          ' ' +
          (y + radiusY) +
          ' C ' +
          (x + width) +
          ' ' +
          (y + height * 0.5) +
          ', ' +
          (x + width) +
          ' ' +
          (y + height - radiusY) +
          ', ' +
          (x + width) +
          ' ' +
          (y + height - radiusY),
        fill: 'none',
        stroke: '#c3c9cf',
        'stroke-width': 0.8,
        opacity: 0.14,
      })
    );
    svg.appendChild(linkedSvgNode(cylinder, group.cableAssembly && group.cableAssembly.url, tooltip));
  }

  function visualBandForSegment(segment) {
    var fromAnchor = segment.fromAnchor;
    var toAnchor = segment.toAnchor;
    var upperAnchor = fromAnchor.visualIndex <= toAnchor.visualIndex ? fromAnchor : toAnchor;
    var lowerAnchor = fromAnchor.visualIndex <= toAnchor.visualIndex ? toAnchor : fromAnchor;
    return {
      top: Number.isFinite(Number(upperAnchor.visualBottom)) ? Number(upperAnchor.visualBottom) : upperAnchor.yBottom,
      bottom: Number.isFinite(Number(lowerAnchor.visualTop)) ? Number(lowerAnchor.visualTop) : lowerAnchor.yTop,
    };
  }

  function cableCylinderGeometry(group, canvasWidth) {
    var yStart = Math.min.apply(null, group.segments.map(function (segment) {
      return segment.fromAnchor.yBottom;
    }));
    var yEnd = Math.max.apply(null, group.segments.map(function (segment) {
      return segment.toAnchor.yTop;
    }));
    var bandTop = Math.max.apply(null, group.segments.map(function (segment) {
      return visualBandForSegment(segment).top;
    }));
    var bandBottom = Math.min.apply(null, group.segments.map(function (segment) {
      return visualBandForSegment(segment).bottom;
    }));
    var distance = yEnd - yStart;
    var centerY = bandBottom > bandTop ? bandTop + (bandBottom - bandTop) / 2 : yStart + distance / 2;
    var height = CABLE_CYLINDER_HEIGHT;
    var xValues = group.segments.map(function (segment) {
      return xAtCableY(segment, centerY);
    });
    var minX = Math.min.apply(null, xValues);
    var maxX = Math.max.apply(null, xValues);
    var spanWidth = Math.max(1, maxX - minX);
    var width = Math.max(CABLE_CYLINDER_MIN_WIDTH, spanWidth + CABLE_CYLINDER_HORIZONTAL_PADDING);
    var maxWidth = Math.min(CABLE_CYLINDER_MAX_WIDTH, Math.max(CABLE_CYLINDER_MIN_WIDTH, canvasWidth - 64));
    width = Math.min(width, maxWidth);
    var centerX = minX + spanWidth / 2;
    var x = centerX - width / 2;
    x = Math.max(32, Math.min(canvasWidth - width - 32, x));
    return {
      x: x,
      y: centerY - height / 2,
      width: width,
      height: height,
      centerY: centerY,
    };
  }

  function drawCableAssemblyLabel(svg, group, geometry, width) {
    var label = cableAssemblyLabel(group.cableAssembly);
    var labelFitsRight = geometry.x + geometry.width + 190 < width;
    if (labelFitsRight) {
      drawRightBraceLabel(svg, label, geometry.x + geometry.width + 16, geometry.y + 2, geometry.y + geometry.height - 2, {
        labelX: geometry.x + geometry.width + 28,
        width: 18,
        fontSize: 10,
        lineHeight: 12,
      });
      return;
    }
    drawBraceLabel(svg, label, geometry.x - 38, geometry.y + 2, geometry.y + geometry.height - 2, {
      labelX: geometry.x - 48,
      width: 18,
      fontSize: 10,
      lineHeight: 12,
    });
  }

  function drawCableAssemblyOverlays(svg, paths, stageLayouts, width) {
    cableAssemblyGroups(paths, stageLayouts).forEach(function (group) {
      if (!group.segments.length) {
        return;
      }
      var geometry = cableCylinderGeometry(group, width);
      drawCableCylinder(svg, group, geometry);
      drawCableAssemblyLabel(svg, group, geometry, width);
    });
  }

  function pathLegendLabel(path) {
    if (path.bundle_label) {
      return path.bundle_label + ' (' + (path.bundle_size || 1) + ' lanes)';
    }
    return 'lane ' + path.lane_index + ' - ' + path.wavelength_nm + 'nm';
  }

  function drawLegend(svg, paths, width, y) {
    var x = 34;
    var rowY = y;
    var itemW = paths.length > 8 ? 138 : 178;
    paths.forEach(function (path, index) {
      if (index > 0 && index % 8 === 0) {
        x = 34;
        rowY += 24;
      }
      var color = LANE_COLORS[index % LANE_COLORS.length];
      var legendLabel = pathLegendLabel(path);
      var legendGroup = createSvgNode('g');
      legendGroup.appendChild(
        createSvgNode('rect', {
          x: x,
          y: rowY - 15,
          width: itemW - 8,
          height: 18,
          fill: 'transparent',
        })
      );
      legendGroup.appendChild(
        createSvgNode('rect', {
          x: x,
          y: rowY - 10,
          width: 18,
          height: 8,
          fill: color,
          rx: 2,
        })
      );
      var legendText = createSvgNode('text', {
        x: x + 24,
        y: rowY - 3,
        fill: '#a9b2bf',
        'font-size': 10,
        'font-family': 'ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", "Courier New", monospace',
      });
      legendText.textContent = compactMiddle(legendLabel, 20);
      legendGroup.appendChild(legendText);
      svg.appendChild(linkedSvgNode(appendTitle(legendGroup, legendLabel), path.source_lane_url || path.destination_lane_url, legendLabel));
      x += itemW;
    });
  }

  function bringTextToFront(svg) {
    Array.from(svg.querySelectorAll('text')).forEach(function (node) {
      svg.appendChild(node);
    });
  }

  function renderSchematic(container) {
    var svg = container.querySelector('[data-fanout-schematic-svg]');
    if (!svg) {
      return;
    }
    var paths = parsePayload(container).filter(function (path) {
      return path && path.path_found;
    });
    var interfaceLayers = hasExpandedInterfaceLayers(container, paths);
    var renderPaths = interfaceLayers ? withInterfaceLayerHops(paths) : paths;
    var stages = buildStages(renderPaths, parseStagePayload(container), { interfaceLayers: interfaceLayers });
    var plan = visualStagePlan(stages, { sourceTitle: sourceStageTitle(container), sourceUrl: sourceStageUrl(container) });
    var visualStages = plan.stages;
    var width = schematicWidthForStages(visualStages);
    var top = 18;
    var gap = pathsHaveCableAssemblySpans(renderPaths) ? CABLE_ASSEMBLY_STAGE_GAP : DEFAULT_STAGE_GAP;
    var stageLayouts = [];

    visualStages.forEach(function (stage) {
      var previewHeight = stageHeight(stage);
      stageLayouts.push({ top: top, height: previewHeight, anchors: {} });
      top += previewHeight + gap;
    });
    var legendRows = Math.max(1, Math.ceil(paths.length / 8));
    var legendTop = top + 12;
    var height = Math.max(320, legendTop + legendRows * 24 + 18);

    svg.replaceChildren();
    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svg.appendChild(
      createSvgNode('rect', {
        x: 0,
        y: 0,
        width: width,
        height: height,
        fill: '#03070d',
        stroke: '#2b313a',
        'stroke-width': 1,
        rx: 4,
      })
    );

    if (!renderPaths.length || !visualStages.length) {
      drawText(svg, 'No resolved lane paths available for this trace.', {
        x: width / 2,
        y: height / 2,
        'text-anchor': 'middle',
        fill: '#8f98a7',
        'font-size': 13,
      });
      return;
    }

    stageLayouts = [];
    var stageLayoutsByDepth = [];
    top = 18;
    visualStages.forEach(function (stage, visualIndex) {
      var layout = drawStage(svg, stage, top, width);
      stageLayouts.push(layout);
      (plan.depthsByVisualIndex[visualIndex] || []).forEach(function (depth) {
        if (!stageLayoutsByDepth[depth]) {
          stageLayoutsByDepth[depth] = { anchors: {} };
        }
        Object.keys(layout.anchors || {}).forEach(function (anchorKey) {
          stageLayoutsByDepth[depth].anchors[anchorKey] = Object.assign({}, layout.anchors[anchorKey], {
            visualIndex: visualIndex,
            visualTop: top,
            visualBottom: top + layout.height,
          });
        });
      });
      top += layout.height + gap;
    });

    visualStages.forEach(function (stage, visualIndex) {
      drawPassiveShuffleCassetteStrands(svg, stage, stageLayouts[visualIndex], renderPaths);
    });

    for (var i = 0; i < paths.length; i += 1) {
      drawLanePath(svg, renderPaths[i], i, stageLayoutsByDepth, LANE_COLORS[i % LANE_COLORS.length]);
    }
    drawCableAssemblyOverlays(svg, renderPaths, stageLayoutsByDepth, width);
    drawLegend(svg, paths, width, legendTop);
    bringTextToFront(svg);
    installTooltips(container, svg);
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-fanout-schematic]').forEach(renderSchematic);
  });
})();
