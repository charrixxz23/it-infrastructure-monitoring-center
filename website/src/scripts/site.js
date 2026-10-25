const root = document.documentElement;

function setLocale(locale) {
  if (locale !== "es" && locale !== "en") return;
  root.dataset.locale = locale;
  root.lang = locale;

  document.querySelectorAll("[data-locale-button]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.localeButton === locale));
  });

  document.querySelectorAll("[data-aria-es][data-aria-en]").forEach((element) => {
    element.setAttribute("aria-label", locale === "es" ? element.dataset.ariaEs : element.dataset.ariaEn);
  });

  const dialog = document.querySelector("#image-viewer");
  if (dialog) dialog.setAttribute("aria-label", locale === "es" ? "Visor de capturas" : "Screenshot viewer");
}

document.querySelectorAll("[data-locale-button]").forEach((button) => {
  button.addEventListener("click", () => setLocale(button.dataset.localeButton));
});

const dialog = document.querySelector("#image-viewer");
const viewerImage = document.querySelector("#viewer-image");
const viewerCaption = document.querySelector("#viewer-caption");
const viewerViewport = document.querySelector("#viewer-viewport");
const zoomLevel = document.querySelector("#zoom-level");
const zoomOutButton = document.querySelector("[data-zoom-out]");
const zoomInButton = document.querySelector("[data-zoom-in]");
const zoomResetButton = document.querySelector("[data-zoom-reset]");
const closeButton = document.querySelector("[data-close-image]");
let triggerButton = null;
let zoomIndex = 0;
let touchTapCandidate = null;
let lastTouchTap = null;

const zoomLevels = [1, 1.5, 2, 3];

function updateZoomControls() {
  const zoom = zoomLevels[zoomIndex];
  if (zoomLevel instanceof HTMLOutputElement) zoomLevel.value = `${zoom}×`;
  if (zoomOutButton instanceof HTMLButtonElement) zoomOutButton.disabled = zoomIndex === 0;
  if (zoomInButton instanceof HTMLButtonElement) zoomInButton.disabled = zoomIndex === zoomLevels.length - 1;
  if (zoomResetButton instanceof HTMLButtonElement) zoomResetButton.disabled = zoomIndex === 0;
}

function applyZoom(nextIndex, focus = null) {
  if (!(viewerImage instanceof HTMLImageElement) || !(viewerViewport instanceof HTMLElement)) return;

  const previousImageRect = viewerImage.getBoundingClientRect();
  const viewportRect = viewerViewport.getBoundingClientRect();
  const focusX = focus?.clientX ?? viewportRect.left + viewportRect.width / 2;
  const focusY = focus?.clientY ?? viewportRect.top + viewportRect.height / 2;
  const focusRatio = {
    x: previousImageRect.width ? Math.min(1, Math.max(0, (focusX - previousImageRect.left) / previousImageRect.width)) : 0.5,
    y: previousImageRect.height ? Math.min(1, Math.max(0, (focusY - previousImageRect.top) / previousImageRect.height)) : 0.5,
  };

  zoomIndex = Math.min(zoomLevels.length - 1, Math.max(0, nextIndex));
  updateZoomControls();

  if (!viewerImage.naturalWidth || !viewerImage.naturalHeight || !viewerViewport.clientWidth || !viewerViewport.clientHeight) return;

  const fitScale = Math.min(
    viewerViewport.clientWidth / viewerImage.naturalWidth,
    viewerViewport.clientHeight / viewerImage.naturalHeight,
    1,
  );
  const scale = fitScale * zoomLevels[zoomIndex];
  viewerImage.style.width = `${Math.round(viewerImage.naturalWidth * scale)}px`;
  viewerImage.style.height = `${Math.round(viewerImage.naturalHeight * scale)}px`;

  requestAnimationFrame(() => {
    const imageRect = viewerImage.getBoundingClientRect();
    viewerViewport.scrollLeft += imageRect.left + focusRatio.x * imageRect.width - focusX;
    viewerViewport.scrollTop += imageRect.top + focusRatio.y * imageRect.height - focusY;
  });
}

function resetZoomStyles() {
  if (viewerImage instanceof HTMLImageElement) {
    viewerImage.style.removeProperty("width");
    viewerImage.style.removeProperty("height");
  }
  if (viewerViewport instanceof HTMLElement) {
    viewerViewport.scrollLeft = 0;
    viewerViewport.scrollTop = 0;
  }
  zoomIndex = 0;
  updateZoomControls();
}

updateZoomControls();

document.querySelectorAll("[data-open-image]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!(dialog instanceof HTMLDialogElement) || !(viewerImage instanceof HTMLImageElement) || !(viewerCaption instanceof HTMLElement)) return;
    const locale = root.dataset.locale === "en" ? "en" : "es";
    triggerButton = button;
    viewerImage.src = button.dataset.imageSrc ?? "";
    viewerImage.alt = locale === "en" ? button.dataset.imageAltEn ?? "" : button.dataset.imageAltEs ?? "";
    viewerCaption.textContent = locale === "en" ? button.dataset.imageCaptionEn ?? "" : button.dataset.imageCaptionEs ?? "";
    resetZoomStyles();
    dialog.showModal();
    closeButton?.focus();
    requestAnimationFrame(() => applyZoom(0));
  });
});

viewerImage?.addEventListener("load", () => {
  if (dialog instanceof HTMLDialogElement && dialog.open) applyZoom(zoomIndex);
});

zoomOutButton?.addEventListener("click", () => applyZoom(zoomIndex - 1));
zoomInButton?.addEventListener("click", () => applyZoom(zoomIndex + 1));
zoomResetButton?.addEventListener("click", () => applyZoom(0));

dialog?.addEventListener("keydown", (event) => {
  if (!(event instanceof KeyboardEvent) || event.altKey || event.ctrlKey || event.metaKey) return;

  if (event.key === "+" || (event.key === "=" && event.shiftKey)) {
    event.preventDefault();
    applyZoom(zoomIndex + 1);
  } else if (event.key === "-") {
    event.preventDefault();
    applyZoom(zoomIndex - 1);
  } else if (event.key === "0") {
    event.preventDefault();
    applyZoom(0);
  }
});

viewerViewport?.addEventListener("pointerdown", (event) => {
  if (event.pointerType !== "touch" || event.target !== viewerImage) return;
  touchTapCandidate = { x: event.clientX, y: event.clientY, time: performance.now() };
});

viewerViewport?.addEventListener("pointermove", (event) => {
  if (!touchTapCandidate) return;
  if (Math.hypot(event.clientX - touchTapCandidate.x, event.clientY - touchTapCandidate.y) > 12) touchTapCandidate = null;
});

viewerViewport?.addEventListener("pointerup", (event) => {
  if (event.pointerType !== "touch" || !touchTapCandidate) return;
  const tap = touchTapCandidate;
  touchTapCandidate = null;
  if (performance.now() - tap.time > 500) return;

  if (lastTouchTap && performance.now() - lastTouchTap.time < 350 && Math.hypot(event.clientX - lastTouchTap.x, event.clientY - lastTouchTap.y) < 32) {
    applyZoom(zoomIndex === 0 ? zoomLevels.indexOf(2) : 0, { clientX: event.clientX, clientY: event.clientY });
    lastTouchTap = null;
  } else {
    lastTouchTap = { x: event.clientX, y: event.clientY, time: performance.now() };
  }
});

viewerViewport?.addEventListener("pointercancel", () => {
  touchTapCandidate = null;
});

window.addEventListener("resize", () => {
  if (dialog instanceof HTMLDialogElement && dialog.open) applyZoom(zoomIndex);
});

closeButton?.addEventListener("click", () => {
  if (dialog instanceof HTMLDialogElement) dialog.close();
});

dialog?.addEventListener("click", (event) => {
  if (event.target === dialog && dialog instanceof HTMLDialogElement) dialog.close();
});

dialog?.addEventListener("close", () => {
  viewerImage?.removeAttribute("src");
  resetZoomStyles();
  touchTapCandidate = null;
  lastTouchTap = null;
  if (triggerButton instanceof HTMLElement) triggerButton.focus();
  triggerButton = null;
});
