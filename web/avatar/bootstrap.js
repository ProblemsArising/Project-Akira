"use strict";

window.__akiraAvatarAppStarted = false;

window.setTimeout(() => {
  if (window.__akiraAvatarAppStarted) return;

  const connectionText = document.getElementById("connectionText");
  const notice = document.getElementById("modelNotice");
  const noticeText = document.getElementById("modelNoticeText");

  if (connectionText) connectionText.textContent = "Renderer unavailable";
  if (notice && noticeText) {
    notice.hidden = false;
    notice.classList.add("error");
    noticeText.textContent =
      "The embedded avatar scripts could not start. Restart Akira or check the developer console.";
  }
}, 5000);
