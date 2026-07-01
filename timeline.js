/** 绘制时间轴背景微网格 — 竖向每 12 槽（1h），横向每 25% */
export function drawTimelineGrid(ctx, w, h) {
  ctx.save();
  ctx.strokeStyle = '#e2e8f0';
  ctx.lineWidth = 0.5;
  for (let i = 0; i <= 288; i += 12) {
    const x = (i / 288) * w;
    ctx.beginPath();
    ctx.setLineDash([1, 3]);
    ctx.moveTo(x, 0);
    ctx.lineTo(x, h);
    ctx.stroke();
  }
  for (const pct of [0.25, 0.5, 0.75]) {
    const y = h - (pct * h);
    ctx.beginPath();
    ctx.setLineDash([1, 3]);
    ctx.moveTo(0, y);
    ctx.lineTo(w, y);
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.restore();
}

export function drawUtilLine(canvas, data, color, skipClear = false) {
  const ctx = canvas.getContext('2d');
  const w = canvas.parentElement.clientWidth || 600;
  const h = canvas.parentElement.clientHeight || 40;
  if (!skipClear) {
    canvas.width = w; canvas.height = h;
    canvas._layers = [];
    canvas._snapshot = null;
    drawTimelineGrid(ctx, w, h);
  }
  ctx.strokeStyle = color; ctx.lineWidth = 1.5; ctx.beginPath();
  data.forEach((v, i) => {
    const x = (i / (data.length - 1)) * w;
    const y = (h - 2) - (v / 100) * (h - 4);
    i === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
  });
  ctx.stroke();
  if (!canvas._layers) canvas._layers = [];
  canvas._layers.push({ data, color, label: color === '#ea580c' ? '显存' : 'XPU' });
}

/** 捕获当前 canvas 为快照，Hover 时避免完整重绘 */
export function captureLayerSnapshot(canvas) {
  const img = new Image();
  img.src = canvas.toDataURL();
  canvas._snapshot = img;
}

/** 从快照恢复背景图层 */
export function restoreSnapshot(canvas) {
  if (!canvas._snapshot) return drawAllLayers(canvas);
  const ctx = canvas.getContext('2d');
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  ctx.drawImage(canvas._snapshot, 0, 0);
}

export function drawAllLayers(canvas) {
  const layers = canvas._layers || [];
  const ctx = canvas.getContext('2d');
  const w = canvas.width, h = canvas.height;
  ctx.clearRect(0, 0, w, h);
  layers.forEach(layer => {
    ctx.strokeStyle = layer.color; ctx.lineWidth = 1.5; ctx.beginPath();
    layer.data.forEach((v, i) => {
      const px = (i / (layer.data.length - 1)) * w;
      const py = (h - 2) - (v / 100) * (h - 4);
      i === 0 ? ctx.moveTo(px, py) : ctx.lineTo(px, py);
    });
    ctx.stroke();
  });
}

export function bindCanvasHover(canvases, tooltip) {
  canvases.forEach(canvas => {
    canvas.addEventListener('mousemove', e => {
      const layers = canvas._layers;
      if (!layers || !layers.length) return;
      const rect = canvas.getBoundingClientRect();
      const x = e.clientX - rect.left;
      const dataLen = layers[0].data.length;
      const idx = Math.round(x / rect.width * (dataLen - 1));
      const ci = Math.max(0, Math.min(idx, dataLen - 1));
      const w = canvas.width, h = canvas.height;

      restoreSnapshot(canvas);

      const ctx = canvas.getContext('2d');
      layers.forEach(layer => {
        const val = layer.data[ci];
        const dotX = (ci / (dataLen - 1)) * w;
        const dotY = (h - 2) - (val / 100) * (h - 4);
        ctx.beginPath(); ctx.arc(dotX, dotY, 3, 0, Math.PI * 2); ctx.fillStyle = layer.color; ctx.fill();
      });

      const timeH = Math.floor(ci / 12), timeM = (ci % 12) * 5;
      const vals = layers.map(l => `${l.label}: ${l.data[ci].toFixed(2)}%`).join('  |  ');
      tooltip.textContent = `${timeH}:${String(timeM).padStart(2, '0')}  ${vals}`;
      tooltip.style.display = 'block';
      tooltip.style.left = e.pageX + 10 + 'px'; tooltip.style.top = e.pageY - 30 + 'px';
    });
    canvas.addEventListener('mouseleave', () => {
      tooltip.style.display = 'none';
      if (!canvas._layers || !canvas._layers.length) return;
      restoreSnapshot(canvas);
    });
  });
}
