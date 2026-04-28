(function () {
  function createSvgNode(tagName) {
    return document.createElementNS('http://www.w3.org/2000/svg', tagName);
  }

  function stageText(item, selector, fallback) {
    const node = item.querySelector(selector);
    const text = node ? node.textContent.trim() : '';
    return text || fallback;
  }

  function stageLabel(item) {
    const primary = stageText(item, '[data-stage-label]', 'Step');
    const secondary = stageText(item, '[data-stage-kind]', '');
    return secondary ? primary + ' (' + secondary + ')' : primary;
  }

  function truncateLabel(text, maxLength) {
    if (text.length <= maxLength) {
      return text;
    }
    return text.slice(0, maxLength - 1) + '\u2026';
  }

  function renderPathCanvas(container) {
    const svg = container.querySelector('[data-workspace-path-canvas-svg]');
    const items = Array.from(container.querySelectorAll('[data-path-stage-item]'));
    if (!svg || !items.length) {
      return;
    }

    const width = Math.max(320, items.length * 148);
    const height = 96;
    const leftPadding = 32;
    const rightPadding = 32;
    const usableWidth = Math.max(width - leftPadding - rightPadding, 1);
    const step = items.length === 1 ? 0 : usableWidth / (items.length - 1);
    const baselineY = 34;
    const labelY = 74;

    svg.setAttribute('viewBox', '0 0 ' + width + ' ' + height);
    svg.replaceChildren();

    items.forEach(function (item, index) {
      const x = items.length === 1 ? width / 2 : leftPadding + step * index;
      const label = stageLabel(item);

      if (index < items.length - 1) {
        const line = createSvgNode('line');
        line.setAttribute('x1', String(x));
        line.setAttribute('y1', String(baselineY));
        line.setAttribute('x2', String(leftPadding + step * (index + 1)));
        line.setAttribute('y2', String(baselineY));
        line.setAttribute('stroke', 'var(--lane-workspace-canvas-line)');
        line.setAttribute('stroke-width', '3');
        line.setAttribute('stroke-linecap', 'round');
        svg.appendChild(line);
      }

      const circle = createSvgNode('circle');
      circle.setAttribute('cx', String(x));
      circle.setAttribute('cy', String(baselineY));
      circle.setAttribute('r', '14');
      circle.setAttribute('fill', 'var(--lane-workspace-canvas-node)');
      svg.appendChild(circle);

      const number = createSvgNode('text');
      number.setAttribute('x', String(x));
      number.setAttribute('y', '39');
      number.setAttribute('text-anchor', 'middle');
      number.setAttribute('font-size', '12');
      number.setAttribute('font-weight', '700');
      number.setAttribute('fill', 'var(--lane-workspace-canvas-node-text)');
      number.textContent = String(index + 1);
      svg.appendChild(number);

      const text = createSvgNode('text');
      text.setAttribute('x', String(x));
      text.setAttribute('y', String(labelY));
      text.setAttribute('text-anchor', 'middle');
      text.setAttribute('font-size', '11');
      text.setAttribute('fill', 'currentColor');
      text.textContent = truncateLabel(label, 18);
      svg.appendChild(text);

      const title = createSvgNode('title');
      title.textContent = label;
      circle.appendChild(title);
    });
  }

  function restoreHashFocus() {
    if (!window.location.hash) {
      return;
    }

    const target = document.getElementById(window.location.hash.slice(1));
    if (!target || typeof target.focus !== 'function') {
      return;
    }

    if (!target.hasAttribute('tabindex')) {
      return;
    }

    window.requestAnimationFrame(function () {
      target.focus({ preventScroll: true });
      target.scrollIntoView({ block: 'start' });
    });
  }

  document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('[data-workspace-path-canvas]').forEach(renderPathCanvas);
    restoreHashFocus();
  });
})();
