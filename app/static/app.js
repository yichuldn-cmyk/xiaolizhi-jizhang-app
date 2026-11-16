/* 交互只负责展示；统计、权限、金额和入账逻辑均在 Python 服务端。 */
(() => {
  const colors = ['#17794a', '#5b9d60', '#98bd75', '#c7d99b', '#628f8a', '#a1b5a4'];
  document.querySelectorAll('.donut').forEach(chart => {
    const values = JSON.parse(chart.dataset.values).map(([, value]) => Math.max(0, value));
    const total = values.reduce((sum, value) => sum + value, 0);
    if (!total) return;
    let position = 0;
    chart.style.background = `conic-gradient(${values.map((value, index) => {
      const start = position;
      position += value / total * 100;
      return `${colors[index % colors.length]} ${start}% ${position}%`;
    }).join(',')})`;
  });

  // SVG 趋势图包含零线，负值退款也能正确展示；横轴保留实际日期间隔。
  const svgNode = (name, attrs, text) => {
    const node = document.createElementNS('http://www.w3.org/2000/svg', name);
    Object.entries(attrs).forEach(([key, value]) => node.setAttribute(key, value));
    if (text !== undefined) node.textContent = text;
    return node;
  };
  document.querySelectorAll('.trend').forEach(container => {
    const days = JSON.parse(container.dataset.days);
    if (!days.length) return;
    const width = Math.max(240, container.clientWidth - 4), height = 220;
    const left = 55, right = width - 14, top = 15, bottom = 185;
    const values = days.map(([, value]) => value / 100);
    const min = Math.min(0, ...values), max = Math.max(1, ...values);
    const y = value => bottom - (value - min) / (max - min) * (bottom - top);
    const month = days[0][0].slice(0, 7), parts = month.split('-').map(Number);
    const last = new Date(parts[0], parts[1], 0).getDate();
    const x = date => left + (Number(date.slice(-2)) - 1) / (last - 1) * (right - left);
    const svg = svgNode('svg', {viewBox: `0 0 ${width} ${height}`, role: 'img', 'aria-label': `${month} 每日净支出，单位元`});
    [min, (max + min) / 2, max].forEach(value => {
      svg.append(svgNode('line', {x1: left, x2: right, y1: y(value), y2: y(value), stroke: '#e4ecdf'}));
      svg.append(svgNode('text', {x: left - 8, y: y(value) + 4, 'text-anchor': 'end'}, Math.round(value)));
    });
    svg.append(svgNode('text', {x: 0, y: 12}, '元'));
    svg.append(svgNode('line', {x1: left, x2: right, y1: y(0), y2: y(0), stroke: '#c7d7be'}));
    svg.append(svgNode('polyline', {points: days.map(([date, value]) => `${x(date)},${y(value / 100)}`).join(' '), fill: 'none', stroke: '#278656', 'stroke-width': 2}));
    days.forEach(([date, value]) => {
      const dot = svgNode('circle', {cx: x(date), cy: y(value / 100), r: 3, fill: '#278656'});
      dot.append(svgNode('title', {}, `${date}：¥${(value / 100).toFixed(2)}`));
      svg.append(dot);
    });
    [1, 15, last].forEach(day => svg.append(svgNode('text', {x: x(`${month}-${String(day).padStart(2, '0')}`), y: 211, 'text-anchor': 'middle'}, `${day}日`)));
    container.replaceChildren(svg);
  });

  document.querySelectorAll('form[data-confirm]').forEach(form => {
    form.addEventListener('submit', event => { if (!confirm(form.dataset.confirm)) event.preventDefault(); });
  });
  document.querySelectorAll('[data-back]').forEach(button => button.addEventListener('click', () => history.back()));
  document.querySelectorAll('[data-copy]').forEach(button => button.addEventListener('click', async () => {
    const input = document.getElementById(button.dataset.copy);
    input.select();
    // 局域网 HTTP 可能不支持剪贴板 API；保留选中文本供手动复制。
    if (navigator.clipboard && window.isSecureContext) {
      try { await navigator.clipboard.writeText(input.value); button.textContent = '已复制'; }
      catch { button.textContent = '已选中，请手动复制'; }
    } else button.textContent = '已选中，请手动复制';
  }));
})();
