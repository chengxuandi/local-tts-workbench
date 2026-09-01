(() => {
  const form = document.querySelector("#generate-form");
  if (!form) return;
  const text = document.querySelector("#text");
  const emotion = document.querySelector("#emotion");
  const model = document.querySelector("#model");
  const character = document.querySelector("#character");
  const speed = document.querySelector("#speed");
  const preview = document.querySelector("#effective-preview");
  const byteCount = document.querySelector("#byte-count");
  const cost = document.querySelector("#cost");
  const button = document.querySelector("#generate-button");
  const result = document.querySelector("#result");
  let previewTimer;

  async function updatePreview() {
    const query = new URLSearchParams({text: text.value, emotion: emotion.value, model: model.value});
    try {
      const response = await fetch(`/api/preview?${query}`);
      const data = await response.json();
      preview.textContent = data.effective_text;
      byteCount.textContent = data.utf8_bytes;
      cost.textContent = `$${Number(data.estimated_cost_usd).toFixed(6)}`;
    } catch (_) {
      preview.textContent = "预览暂时不可用";
    }
  }
  function schedulePreview() {
    clearTimeout(previewTimer);
    previewTimer = setTimeout(updatePreview, 120);
  }
  [text, emotion, model].forEach((field) => field.addEventListener("input", schedulePreview));

  character?.addEventListener("change", () => {
    const option = character.selectedOptions[0];
    emotion.value = option?.dataset.emotion || "";
    speed.value = option?.dataset.speed || "";
    if (option?.dataset.model) model.value = option.dataset.model;
    updatePreview();
  });

  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    if (button.disabled) return;
    button.disabled = true;
    button.textContent = "生成中…";
    result.classList.remove("hidden");
    result.innerHTML = "<strong>正在请求 Fish Audio，请不要关闭页面或重复提交。</strong>";
    try {
      const response = await fetch(form.action, {method: "POST", body: new FormData(form)});
      const data = await response.json();
      if (!response.ok || data.status !== "success") {
        throw new Error(data.error_message || data.detail || "生成失败");
      }
      result.innerHTML = `<h3>生成成功 · ${String(data.sequence_number).padStart(3, "0")}</h3><p>${data.utf8_bytes} UTF-8 bytes · 预计费用 $${Number(data.estimated_cost_usd).toFixed(6)}</p><audio controls autoplay src="${data.audio_url}"></audio><p><a href="/history">查看历史</a></p>`;
      document.querySelector("#client_request_id").value = crypto.randomUUID();
    } catch (error) {
      result.innerHTML = `<div class="notice error">${escapeHtml(error.message)}</div>`;
    } finally {
      button.disabled = false;
      button.textContent = "生成音频";
    }
  });

  function escapeHtml(value) {
    const element = document.createElement("div");
    element.textContent = value;
    return element.innerHTML;
  }
  updatePreview();
})();
