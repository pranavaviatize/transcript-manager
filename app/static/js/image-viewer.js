/* Image viewer (lightbox) for the transcript detail page.
   Navigates between all images of a transcript with arrows, keyboard, a
   filmstrip, click-to-zoom + pan, and mobile swipe. Vanilla IIFE, no deps. */
(function () {
  const dialog = document.getElementById('imgViewerDialog');
  const dataEl = document.getElementById('imageData');
  if (!dialog || !dataEl) return;

  let data = [];
  try { data = JSON.parse(dataEl.textContent || '[]'); } catch (e) { data = []; }
  if (!data.length) return;

  // ----- DOM refs ----------------------------------------------------------
  const img = document.getElementById('ivImg');
  const stage = document.getElementById('ivStage');
  const counter = document.getElementById('ivCounter');
  const nameEl = document.getElementById('ivName');
  const captionEl = document.getElementById('ivCaption');
  const download = document.getElementById('ivDownload');
  const prevBtn = document.getElementById('ivPrev');
  const nextBtn = document.getElementById('ivNext');
  const zoomBtn = document.getElementById('ivZoom');
  const closeBtn = document.getElementById('ivClose');
  const strip = document.getElementById('ivStrip');

  // ----- state -------------------------------------------------------------
  let index = 0;
  let zoomed = false;
  let pan = { x: 0, y: 0 };
  let lastFocus = null;
  let touch = null;        // swipe start {x, y, t}
  let panStart = null;     // drag start {x, y, panX, panY}
  let moved = false;       // did the last pointer interaction drag?
  const ZOOM = 2;

  const n = data.length;
  const url = (i) => '/api/images/' + data[i].id + '/file';
  const pad = (v) => String(v).length < 2 ? '0' + v : String(v);

  // ----- filmstrip (built once) --------------------------------------------
  function buildStrip() {
    dialog.setAttribute('data-count', String(n));
    if (n <= 1) return;
    const frag = document.createDocumentFragment();
    data.forEach((im, i) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'iv-thumb';
      btn.dataset.index = String(i);
      btn.setAttribute('aria-label', 'Image ' + (i + 1));
      const t = document.createElement('img');
      t.src = url(i);
      t.alt = '';
      t.loading = 'lazy';
      btn.appendChild(t);
      frag.appendChild(btn);
    });
    strip.appendChild(frag);
  }

  // ----- zoom / pan --------------------------------------------------------
  function applyTransform() {
    img.style.transform = zoomed
      ? 'translate(' + pan.x + 'px, ' + pan.y + 'px) scale(' + ZOOM + ')'
      : '';
  }
  function clampPan() {
    // Keep the scaled image covering the stage so no backdrop gap shows.
    const sr = stage.getBoundingClientRect();
    const iw = img.clientWidth * ZOOM;
    const ih = img.clientHeight * ZOOM;
    const maxX = Math.max(0, (iw - sr.width) / 2);
    const maxY = Math.max(0, (ih - sr.height) / 2);
    pan.x = Math.max(-maxX, Math.min(maxX, pan.x));
    pan.y = Math.max(-maxY, Math.min(maxY, pan.y));
  }
  function resetZoom() {
    zoomed = false;
    pan = { x: 0, y: 0 };
    img.classList.remove('is-zoomed', 'is-panning');
    img.style.transform = '';
    if (zoomBtn) zoomBtn.setAttribute('aria-pressed', 'false');
  }
  function toggleZoom(originX, originY) {
    if (zoomed) { resetZoom(); return; }
    zoomed = true;
    img.classList.add('is-zoomed');
    if (zoomBtn) zoomBtn.setAttribute('aria-pressed', 'true');
    // Zoom toward the click point: shift so that point stays roughly put.
    if (originX != null) {
      const r = img.getBoundingClientRect();
      pan.x = (r.left + r.width / 2 - originX) * (ZOOM - 1);
      pan.y = (r.top + r.height / 2 - originY) * (ZOOM - 1);
      clampPan();
    }
    applyTransform();
  }

  // ----- render ------------------------------------------------------------
  function render() {
    const im = data[index];
    resetZoom();
    stage.classList.add('is-loading');
    img.classList.add('is-swapping');
    img.onload = () => { stage.classList.remove('is-loading'); img.classList.remove('is-swapping'); };
    img.onerror = () => { stage.classList.remove('is-loading'); nameEl.textContent = 'Image unavailable'; };
    img.src = url(index);
    img.alt = im.filename || '';

    counter.textContent = pad(index + 1) + ' / ' + pad(n);
    nameEl.textContent = im.filename || '';
    captionEl.textContent = im.caption || '';
    download.href = url(index);

    const single = n <= 1;
    prevBtn.hidden = single;
    nextBtn.hidden = single;

    if (!single) {
      const thumbs = strip.children;
      for (let i = 0; i < thumbs.length; i++) {
        const active = i === index;
        thumbs[i].classList.toggle('is-active', active);
        if (active) thumbs[i].scrollIntoView({ inline: 'center', block: 'nearest', behavior: 'smooth' });
      }
    }
    preload();
  }

  function preload() {
    if (n <= 1) return;
    [ (index + 1) % n, (index - 1 + n) % n ].forEach((i) => { const p = new Image(); p.src = url(i); });
  }

  // ----- navigation --------------------------------------------------------
  function goTo(i) { index = ((i % n) + n) % n; render(); }
  function next() { goTo(index + 1); }
  function prev() { goTo(index - 1); }

  function open(i) {
    index = Math.max(0, Math.min(i, n - 1));
    lastFocus = document.activeElement;
    render();
    if (!dialog.open) dialog.showModal();
    if (closeBtn) closeBtn.focus();
  }
  function close() { if (dialog.open) dialog.close(); }

  // ----- wiring ------------------------------------------------------------
  const grid = document.querySelector('.image-grid');
  if (grid) grid.addEventListener('click', (e) => {
    if (e.target.closest('.image-tile-delete')) return; // let HTMX handle delete
    const tile = e.target.closest('.image-tile');
    if (tile && tile.dataset.index != null) open(+tile.dataset.index);
  });

  if (strip) strip.addEventListener('click', (e) => {
    const thumb = e.target.closest('.iv-thumb');
    if (thumb) goTo(+thumb.dataset.index);
  });

  prevBtn.addEventListener('click', prev);
  nextBtn.addEventListener('click', next);
  closeBtn.addEventListener('click', close);
  if (zoomBtn) zoomBtn.addEventListener('click', () => toggleZoom());

  // click image -> toggle zoom (unless the click was the end of a pan drag);
  // click empty stage -> close
  img.addEventListener('click', (e) => { e.stopPropagation(); if (!moved) toggleZoom(e.clientX, e.clientY); });
  stage.addEventListener('click', (e) => { if (e.target === stage) close(); });

  // keyboard (scoped to the dialog so it can't fire when closed)
  dialog.addEventListener('keydown', (e) => {
    if (e.key === 'ArrowLeft') { e.preventDefault(); prev(); }
    else if (e.key === 'ArrowRight') { e.preventDefault(); next(); }
    else if (e.key === 'Escape') { close(); } // native dialog also closes
  });

  // restore focus + reset zoom on close
  dialog.addEventListener('close', () => { resetZoom(); if (lastFocus) lastFocus.focus(); });

  // pan via pointer events (mouse + touch-drag share one path)
  img.addEventListener('pointerdown', (e) => {
    moved = false;
    if (!zoomed) return;
    e.preventDefault();
    panStart = { x: e.clientX, y: e.clientY, panX: pan.x, panY: pan.y };
    img.classList.add('is-panning');
    img.setPointerCapture(e.pointerId);
  });
  img.addEventListener('pointermove', (e) => {
    if (!panStart) return;
    const dx = e.clientX - panStart.x;
    const dy = e.clientY - panStart.y;
    if (Math.abs(dx) > 3 || Math.abs(dy) > 3) moved = true;
    pan.x = panStart.panX + dx;
    pan.y = panStart.panY + dy;
    clampPan();
    applyTransform();
  });
  const endPan = (e) => {
    if (!panStart) return;
    img.classList.remove('is-panning');
    try { img.releasePointerCapture(e.pointerId); } catch (err) { /* noop */ }
    panStart = null;
  };
  img.addEventListener('pointerup', endPan);
  img.addEventListener('pointercancel', endPan);

  // swipe to navigate (touch, only when not zoomed)
  stage.addEventListener('touchstart', (e) => {
    if (zoomed || e.touches.length !== 1) { touch = null; return; }
    const t = e.touches[0];
    touch = { x: t.clientX, y: t.clientY, t: e.timeStamp };
  }, { passive: true });
  stage.addEventListener('touchend', (e) => {
    if (!touch) return;
    const t = e.changedTouches[0];
    const dx = t.clientX - touch.x;
    const dy = t.clientY - touch.y;
    const fast = (e.timeStamp - touch.t) < 600;
    if (fast && Math.abs(dx) > 50 && Math.abs(dx) > Math.abs(dy)) {
      dx < 0 ? next() : prev();
    }
    touch = null;
  }, { passive: true });

  buildStrip();
  window.ImageViewer = { open: open };
})();
