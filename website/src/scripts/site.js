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
const closeButton = document.querySelector("[data-close-image]");
let triggerButton = null;

document.querySelectorAll("[data-open-image]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!(dialog instanceof HTMLDialogElement) || !(viewerImage instanceof HTMLImageElement) || !(viewerCaption instanceof HTMLElement)) return;
    const locale = root.dataset.locale === "en" ? "en" : "es";
    triggerButton = button;
    viewerImage.src = button.dataset.imageSrc ?? "";
    viewerImage.alt = locale === "en" ? button.dataset.imageAltEn ?? "" : button.dataset.imageAltEs ?? "";
    viewerCaption.textContent = locale === "en" ? button.dataset.imageCaptionEn ?? "" : button.dataset.imageCaptionEs ?? "";
    dialog.showModal();
    closeButton?.focus();
  });
});

closeButton?.addEventListener("click", () => {
  if (dialog instanceof HTMLDialogElement) dialog.close();
});

dialog?.addEventListener("click", (event) => {
  if (event.target === dialog && dialog instanceof HTMLDialogElement) dialog.close();
});

dialog?.addEventListener("close", () => {
  viewerImage?.removeAttribute("src");
  if (triggerButton instanceof HTMLElement) triggerButton.focus();
  triggerButton = null;
});
