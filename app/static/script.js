/* PetCare Buddy web client - talks to the FastAPI backend (/api/chat, /api/analyze-image). */
(() => {
  "use strict";

  const API = { chat: "/api/chat", image: "/api/analyze-image", cat: "/api/cat-estimate", health: "/health" };
  const CAT_CHIP = "📷 แนบรูปแมวเพื่อประเมินราคา";
  // A typed question like "แมวในรูปตัวใหญ่ไหม ราคาเท่าไหร่" + photo goes straight to the cat estimate.
  const CAT_INTENT = /แมว|cat/i;
  const ESTIMATE_INTENT = /ราคา|ขนาด|หนัก|ขน|ประเมิน|เท่าไ|size|weight|price/i;
  const MAX_IMAGE_MB = 5;
  const ALLOWED_TYPES = ["image/jpeg", "image/png", "image/webp"];
  const STORE_KEY = "petcare-buddy-session";
  const WELCOME =
    "สวัสดีค่ะ 🐾 PetCare Buddy ยินดีให้บริการค่ะ\n" +
    "สอบถามบริการอาบน้ำ ตัดขน ราคา เวลาเปิด-ปิด การจอง หรือนโยบายของร้านได้เลย " +
    "หรือแนบรูปน้องหมาน้องแมวเพื่อให้ช่วยแนะนำบริการก็ได้ค่ะ";
  const SUGGESTIONS = [
    "ร้านเปิดกี่โมง",
    "อาบน้ำแมวราคาเท่าไหร่",
    "สุนัข 12 กิโล ตัดขนราคาเท่าไหร่",
    "จองคิวต้องทำอย่างไร",
    "ยกเลิกนัดต้องแจ้งก่อนกี่ชั่วโมง",
    "มีโปรโมชั่นไหม",
    CAT_CHIP,
  ];

  const $ = (id) => document.getElementById(id);
  const chatEl = $("chat"), input = $("input"), form = $("composer"), sendBtn = $("sendBtn");
  const fileInput = $("fileInput"), preview = $("imagePreview"), previewImg = $("previewImg"), previewName = $("previewName");
  const errorBox = $("errorBox"), errorText = $("errorText"), retryBtn = $("retryBtn");

  let history = [];       // [{role, content}] sent to /api/chat for context
  let transcript = [];    // rendered messages, kept for this browser session
  let pendingFile = null;
  let busy = false;
  let lastAction = null;  // for "ส่งอีกครั้ง"
  let catMode = false;    // next attached photo goes to the cat size/price estimate
  const catFiles = new Map();  // message key -> File, kept in memory only (not in sessionStorage)

  // ---------------------------------------------------------------- session storage (best effort)
  function save() {
    try { sessionStorage.setItem(STORE_KEY, JSON.stringify({ history, transcript })); } catch (_) { /* storage blocked */ }
  }
  function load() {
    try {
      const raw = sessionStorage.getItem(STORE_KEY);
      if (!raw) return false;
      const data = JSON.parse(raw);
      history = Array.isArray(data.history) ? data.history : [];
      transcript = Array.isArray(data.transcript) ? data.transcript : [];
      return transcript.length > 0;
    } catch (_) { return false; }
  }

  // ---------------------------------------------------------------- rendering
  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function renderMessage(m, scroll = true) {
    const wrap = el("div", `msg ${m.role}`);
    if (m.mode === "guardrail") wrap.classList.add("guard");
    if (m.failed) wrap.classList.add("failed");
    const bubble = el("div", "bubble");
    if (m.imageUrl) {
      const img = el("img", "thumb");
      img.src = m.imageUrl;
      img.alt = "รูปที่ส่ง";
      bubble.appendChild(img);
    }
    if (m.text) bubble.appendChild(document.createTextNode(m.text));
    wrap.appendChild(bubble);

    if (m.observation && (m.observation.observations?.length || m.observation.visible_text)) {
      const box = el("div", "observe");
      if (m.observation.observations?.length) {
        box.appendChild(el("h4", null, "สิ่งที่มองเห็นในภาพ"));
        const ul = el("ul");
        m.observation.observations.forEach((o) => ul.appendChild(el("li", null, o)));
        box.appendChild(ul);
      }
      if (m.observation.visible_text) {
        box.appendChild(el("p", "untrusted", `ข้อความที่อ่านได้ในภาพ (ไม่ใช่ข้อมูลทางการของร้าน): ${m.observation.visible_text}`));
      }
      wrap.appendChild(box);
    }

    // Source references and response times stay in the API response (used as test
    // evidence by scripts/run_eval.py) but are not shown to customers.
    if (m.actions && m.actions.includes("open_booking_form")) {
      const b = el("button", "inline-action", "เปิดแบบฟอร์มจองคิว");
      b.type = "button";
      b.addEventListener("click", () => openBooking("new"));
      wrap.appendChild(b);
    }
    if (m.actions && m.actions.includes("cat_estimate") && catFiles.has(m.catFileKey)) {
      const b = el("button", "inline-action", "🐱 ประเมินขนาดและราคาแมวจากรูปนี้");
      b.type = "button";
      b.addEventListener("click", () => { if (!busy) requestCatEstimate(catFiles.get(m.catFileKey)); });
      wrap.appendChild(b);
    }
    if (m.actions && m.actions.includes("book_with_cat") && m.catEstimate) {
      const b = el("button", "inline-action", "จองคิวอาบน้ำแมวด้วยข้อมูลนี้");
      b.type = "button";
      b.addEventListener("click", () => openBooking("new", m.catEstimate));
      wrap.appendChild(b);
    }
    if (m.actions && m.actions.includes("open_booking_status")) {
      const b = el("button", "inline-action", m.bookingId ? `ตรวจสถานะ ${m.bookingId}` : "ตรวจสถานะการจอง");
      b.type = "button";
      // each message opens ITS OWN booking, not whatever was checked before
      b.addEventListener("click", () => openBooking("status", null, m.bookingId ? { id: m.bookingId } : null));
      wrap.appendChild(b);
    }
    chatEl.appendChild(wrap);
    if (scroll) chatEl.scrollTop = chatEl.scrollHeight;
  }

  function addMessage(m) {
    transcript.push(m);
    renderMessage(m);
    save();
  }

  function renderAll() {
    chatEl.innerHTML = "";
    renderMessage({ role: "bot", text: WELCOME }, false);
    transcript.forEach((m) => renderMessage(m, false));
    chatEl.scrollTop = chatEl.scrollHeight;
  }

  // ---------------------------------------------------------------- UI state
  let typingNode = null;
  function setBusy(on) {
    busy = on;
    sendBtn.disabled = on;
    input.disabled = on;
    if (on) {
      typingNode = $("typingTpl").content.firstElementChild.cloneNode(true);
      chatEl.appendChild(typingNode);
      chatEl.scrollTop = chatEl.scrollHeight;
    } else if (typingNode) {
      typingNode.remove();
      typingNode = null;
      input.focus();
    }
  }

  function showError(message, canRetry) {
    errorText.textContent = message;
    retryBtn.hidden = !canRetry;
    errorBox.hidden = false;
  }
  function hideError() { errorBox.hidden = true; }

  async function readError(res) {
    try {
      const data = await res.json();
      const d = data.detail;
      if (typeof d === "string") return d;
      if (d && d.message) return d.message;
      if (Array.isArray(d)) return "ข้อมูลที่ส่งมาไม่ถูกต้อง กรุณาตรวจสอบอีกครั้ง";
    } catch (_) { /* not json */ }
    return `เกิดข้อผิดพลาดจากเซิร์ฟเวอร์ (HTTP ${res.status})`;
  }

  // ---------------------------------------------------------------- API calls
  // New numbers: PET-BOOK-<appointment date DDMMYYYY><running number>; older ones: BK-XXXXXX
  const BOOKING_ID_RE = /PET-BOOK-\d{10,}|BK-[A-Z0-9]{6}/;
  async function sendText(text) {
    const priorHistory = history.slice();
    addMessage({ role: "user", text });
    // A booking number in the message: answer from the database, never from the AI or chat history.
    const bk = (text.toUpperCase().match(BOOKING_ID_RE) || [])[0];
    if (bk) {
      lastAction = () => chatStatus(bk);
      await chatStatus(bk);
      return;
    }
    lastAction = () => requestChat(text, priorHistory);
    await requestChat(text, priorHistory);
  }

  async function requestChat(text, priorHistory) {
    hideError();
    setBusy(true);
    try {
      const res = await fetch(API.chat, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: text, history: priorHistory }),
      });
      if (!res.ok) {
        const msg = await readError(res);
        showError(msg, res.status >= 500);
        return;
      }
      const data = await res.json();
      history = priorHistory.concat([{ role: "user", content: text }, { role: "assistant", content: data.answer }]);
      const typedId = (text.toUpperCase().match(BOOKING_ID_RE) || [])[0];
      addMessage({ role: "bot", text: data.answer, mode: data.mode, actions: data.actions || [], bookingId: typedId });
      lastAction = null;
    } catch (err) {
      showError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ตรวจสอบว่าเปิด backend อยู่ แล้วลองส่งอีกครั้ง", true);
    } finally {
      setBusy(false);
    }
  }

  async function sendImage(file, question) {
    const imageUrl = await toDataUrl(file);
    addMessage({ role: "user", text: question, imageUrl });
    lastAction = () => requestImage(file, question);
    await requestImage(file, question);
  }

  async function requestImage(file, question) {
    hideError();
    setBusy(true);
    try {
      const body = new FormData();
      body.append("file", file);
      body.append("question", question || "");
      const res = await fetch(API.image, { method: "POST", body });
      if (!res.ok) {
        const msg = await readError(res);
        showError(msg, res.status >= 500);
        return;
      }
      const data = await res.json();
      const isCat = data.observation && data.observation.image_type === "cat";
      const catFileKey = isCat ? `cat-${Date.now()}` : null;
      if (isCat) catFiles.set(catFileKey, file);
      addMessage({
        role: "bot", text: data.answer, mode: data.mode, observation: data.observation,
        actions: isCat ? ["cat_estimate"] : [], catFileKey,
      });
      history.push({ role: "user", content: `[ส่งรูปภาพ] ${question || ""}`.trim() }, { role: "assistant", content: data.answer });
      save();
      lastAction = null;
    } catch (err) {
      showError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ตรวจสอบว่าเปิด backend อยู่ แล้วลองส่งอีกครั้ง", true);
    } finally {
      setBusy(false);
    }
  }

  function toDataUrl(file) {
    // a small thumbnail keeps sessionStorage light
    return new Promise((resolve) => {
      const img = new Image();
      const url = URL.createObjectURL(file);
      img.onload = () => {
        const scale = Math.min(1, 320 / Math.max(img.width, img.height));
        const c = document.createElement("canvas");
        c.width = Math.round(img.width * scale);
        c.height = Math.round(img.height * scale);
        c.getContext("2d").drawImage(img, 0, 0, c.width, c.height);
        URL.revokeObjectURL(url);
        resolve(c.toDataURL("image/jpeg", 0.8));
      };
      img.onerror = () => { URL.revokeObjectURL(url); resolve(null); };
      img.src = url;
    });
  }

  // ---------------------------------------------------------------- cat photo estimate
  async function sendCatEstimate(file, question) {
    const imageUrl = await toDataUrl(file);
    addMessage({ role: "user", text: question || "ช่วยประเมินขนาด น้ำหนัก และราคาของแมวในรูปนี้", imageUrl });
    lastAction = () => requestCatEstimate(file);
    await requestCatEstimate(file);
  }

  async function postCatEstimate(file) {
    const body = new FormData();
    body.append("file", file);
    const res = await fetch(API.cat, { method: "POST", body });
    if (!res.ok) throw new Error(await readError(res));
    return res.json();
  }

  function compactEstimate(data) {
    // the part of the estimate the booking form needs (kept small for sessionStorage)
    return {
      size: data.size, size_label: data.size_label, coat_length: data.coat_length,
      weight_kg_min: data.weight_kg_min, weight_kg_max: data.weight_kg_max,
      weight_kg_estimate: data.weight_kg_estimate, confidence: data.confidence,
      total: data.price ? data.price.total : null,
    };
  }

  async function requestCatEstimate(file) {
    hideError();
    setBusy(true);
    try {
      const data = await postCatEstimate(file);
      const est = data.is_cat ? compactEstimate(data) : null;
      addMessage({
        role: "bot", text: data.message, mode: "cat_estimate",
        observation: data.observations?.length ? { observations: data.observations } : null,
        actions: est ? ["book_with_cat"] : [], catEstimate: est,
      });
      history.push({ role: "user", content: "[ส่งรูปแมวเพื่อประเมินราคา]" }, { role: "assistant", content: data.message });
      save();
      lastAction = null;
    } catch (err) {
      showError(err.message && !/fetch/i.test(err.message) ? err.message
        : "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ตรวจสอบว่าเปิด backend อยู่ แล้วลองส่งอีกครั้ง", true);
    } finally {
      setBusy(false);
    }
  }

  // ---------------------------------------------------------------- events
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    if (busy) return;
    const text = input.value.trim();
    if (!text && !pendingFile) {
      showError("พิมพ์คำถามหรือแนบรูปภาพก่อนกดส่ง", false);
      return;
    }
    input.value = "";
    autoGrow();
    if (pendingFile) {
      const f = pendingFile;
      const wantsCat = catMode || (CAT_INTENT.test(text) && ESTIMATE_INTENT.test(text));
      clearFile();
      if (wantsCat) sendCatEstimate(f, text);
      else sendImage(f, text);
    } else {
      sendText(text);
    }
  });

  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      form.requestSubmit();
    }
  });
  function autoGrow() { input.style.height = "auto"; input.style.height = `${Math.min(input.scrollHeight, 140)}px`; }
  input.addEventListener("input", autoGrow);

  fileInput.addEventListener("change", () => {
    const f = fileInput.files[0];
    fileInput.value = "";
    if (!f) { catMode = false; return; }
    if (!ALLOWED_TYPES.includes(f.type)) {
      showError("รองรับเฉพาะไฟล์ JPG, PNG หรือ WEBP", false);
      return;
    }
    if (f.size > MAX_IMAGE_MB * 1024 * 1024) {
      showError(`ไฟล์ใหญ่เกิน ${MAX_IMAGE_MB} MB กรุณาเลือกรูปที่เล็กลง`, false);
      return;
    }
    hideError();
    pendingFile = f;
    previewImg.src = URL.createObjectURL(f);
    previewName.textContent = f.name;
    $("previewMode").hidden = !catMode;
    preview.hidden = false;
    input.placeholder = catMode
      ? "กดส่งเพื่อประเมินขนาด น้ำหนัก ขน และราคาของแมวในรูป"
      : "พิมพ์คำถามประกอบรูป (ไม่บังคับ) แล้วกดส่ง";
  });
  function clearFile() {
    pendingFile = null;
    catMode = false;
    $("previewMode").hidden = true;
    preview.hidden = true;
    if (previewImg.src) URL.revokeObjectURL(previewImg.src);
    previewImg.removeAttribute("src");
    input.placeholder = "พิมพ์คำถาม เช่น อาบน้ำแมวราคาเท่าไหร่";
  }
  $("removeImage").addEventListener("click", clearFile);
  // the normal paperclip button is a plain photo question, not the cat estimate
  // (clicks on the input itself also bubble here, including the cat chip's fileInput.click(), so skip those)
  fileInput.closest("label").addEventListener("click", (e) => { if (e.target !== fileInput) catMode = false; });

  $("clearBtn").addEventListener("click", () => {
    if (busy) return;
    if (transcript.length && !confirm("ล้างบทสนทนาทั้งหมดและเริ่มใหม่?")) return;
    history = [];
    transcript = [];
    lastAction = null;
    hideError();
    clearFile();
    save();
    renderAll();
  });
  $("dismissError").addEventListener("click", hideError);
  retryBtn.addEventListener("click", () => { if (lastAction && !busy) lastAction(); });

  function renderSuggestions() {
    const box = $("suggestions");
    SUGGESTIONS.forEach((q) => {
      const b = el("button", "chip", q);
      b.type = "button";
      b.addEventListener("click", () => {
        if (busy) return;
        if (q === CAT_CHIP) { catMode = true; fileInput.click(); return; }
        sendText(q);
      });
      box.appendChild(b);
    });
  }

  async function checkHealth() {
    const status = $("status");
    try {
      const res = await fetch(API.health);
      const h = await res.json();
      if (h.llm?.configured) {
        status.hidden = true;  // nothing to report when everything works
      } else {
        status.hidden = false;
        status.textContent = "ยังไม่ได้ตั้งค่า LLM";
        status.className = "status warn";
        status.title = "ตั้งค่า API key ในไฟล์ .env แล้วเริ่มเซิร์ฟเวอร์ใหม่";
      }
    } catch (_) {
      status.hidden = false;
      status.textContent = "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้";
      status.className = "status warn";
    }
  }


  // ---------------------------------------------------------------- booking requests
  const dialog = $("bookingDialog");
  const newForm = $("panelNew"), statusForm = $("panelStatus");
  const LAST_BOOKING_KEY = "petcare-last-booking";
  const BOOKINGS_KEY = "petcare-bookings";  // {booking_id: contact} for bookings made in this browser session
  let lookupSeq = 0;  // ignore answers that arrive after a newer lookup started

  function knownBookings() {
    try { return JSON.parse(sessionStorage.getItem(BOOKINGS_KEY) || "{}") || {}; } catch (_) { return {}; }
  }
  function rememberBooking(id, contact) {
    try {
      const all = knownBookings();
      all[id] = contact;
      sessionStorage.setItem(BOOKINGS_KEY, JSON.stringify(all));
      sessionStorage.setItem(LAST_BOOKING_KEY, JSON.stringify({ id, contact }));
    } catch (_) { /* storage blocked */ }
  }
  function lastBooking() {
    try { return JSON.parse(sessionStorage.getItem(LAST_BOOKING_KEY) || "null"); } catch (_) { return null; }
  }
  function resetStatusPanel() {
    lookupSeq++;  // drop any lookup still in flight
    $("statusError").hidden = true;
    const box = $("statusResult");
    box.hidden = true;
    box.innerHTML = "";
  }
  let options = null;

  async function loadOptions() {
    if (options) return options;
    const res = await fetch("/api/booking-options");
    if (!res.ok) throw new Error(await readError(res));
    options = await res.json();
    $("hoursHint").textContent = `(เริ่มนัดได้ทุก 1 ชั่วโมง ${options.time_slots[0]}–${options.last_start} น.)`;
    const sel = newForm.elements.time;
    (options.time_slots || []).forEach((t) => sel.appendChild(new Option(`${t} น.`, t)));
    const today = new Date();
    newForm.elements.date.min = isoDate(today);
    newForm.elements.date.max = isoDate(new Date(today.getTime() + options.max_days_ahead * 86400000));
    const sizeSel = newForm.elements.cat_size;
    (options.cat_pricing?.sizes || []).forEach((sz) =>
      sizeSel.appendChild(new Option(`${sz.id} (${sz.min_kg}–${sz.max_kg} กก.)`, sz.id)));
    refreshSlots();
    return options;
  }

  function isoDate(d) {
    return new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 10);
  }

  function nowHHMM() {
    const n = new Date();
    return `${String(n.getHours()).padStart(2, "0")}:${String(n.getMinutes()).padStart(2, "0")}`;
  }

  // Hourly slots only; times already past (today) and slots already booked are disabled.
  // Booked slots come from GET /api/availability, which returns times only (no customer data).
  let slotSeq = 0;
  async function refreshSlots() {
    const sel = newForm.elements.time;
    const day = newForm.elements.date.value;
    const isToday = day === isoDate(new Date());
    const now = nowHHMM();
    let taken = [];
    if (day) {
      const seq = ++slotSeq;
      try {
        const res = await fetch(`/api/availability?date=${encodeURIComponent(day)}`);
        if (res.ok) taken = (await res.json()).taken || [];
      } catch (_) { /* offline: the server still refuses a booked slot when the form is sent */ }
      if (seq !== slotSeq) return;  // a newer date was picked meanwhile
    }
    [...sel.options].forEach((o) => {
      if (!o.value) return;
      const booked = taken.includes(o.value);
      o.disabled = (isToday && o.value <= now) || booked;
      o.textContent = booked ? `${o.value} น. (ถูกจองแล้ว)` : `${o.value} น.`;
    });
    if (sel.selectedOptions[0]?.disabled) sel.value = "";
  }

  function timeAllowed(t) {
    return !!options && options.time_slots.includes(t);
  }

  // ------------------------------------------------ cat size / coat (photo estimate or chosen by hand)
  const CAT_FIELDS = ["cat_size", "coat_length"];
  function isCat() { return petType() === "แมว"; }

  function catSizeForWeight(kg) {
    const sizes = options.cat_pricing?.sizes || [];
    const hit = sizes.find((s) => kg <= s.max_kg);
    return hit ? hit.id : (sizes.length ? sizes[sizes.length - 1].id : null);
  }

  function catExtras(ids) {
    const cp = options.cat_pricing;
    if (!cp || !isCat() || !ids.includes(cp.base_service.id)) return [];
    const f = newForm.elements;
    const size = f.cat_size.value || (f.weight_kg.value ? catSizeForWeight(Number(f.weight_kg.value)) : null);
    const coat = f.coat_length.value;
    const items = [];
    const s = cp.sizes.find((x) => x.id === size);
    if (s && s.surcharge) items.push({ label: s.label, amount: s.surcharge });
    const c = cp.coats.find((x) => x.id === coat);
    if (c && c.surcharge) items.push({ label: `แมว${c.label}`, amount: c.surcharge });
    return items;
  }

  function updateCatBox() {
    const show = isCat() && !!options.cat_pricing;
    $("catBox").hidden = !show;
    if (!show) {
      CAT_FIELDS.forEach((n) => { newForm.elements[n].value = ""; });
      clearCatResult();
    }
  }

  function clearCatResult() {
    const box = $("catResult");
    box.hidden = true;
    box.innerHTML = "";
    box.className = "cat-result";
    $("catUploadLabel").textContent = "แนบรูปแมว";
  }

  function applyCatEstimate(est) {
    if (!est) return;
    const f = newForm.elements;
    if (est.size) f.cat_size.value = est.size;
    if (est.coat_length) f.coat_length.value = est.coat_length;
    if (!f.weight_kg.value && est.weight_kg_estimate) f.weight_kg.value = est.weight_kg_estimate;
    const base = options.cat_pricing?.base_service.id;
    const cb = base && newForm.querySelector(`input[name="service_ids"][value="${base}"]`);
    if (cb && !selectedServices().length) cb.checked = true;
    updateEstimate();
  }

  function renderCatResult(data, thumbUrl) {
    const box = $("catResult");
    box.innerHTML = "";
    box.className = "cat-result";
    if (thumbUrl) { const img = el("img"); img.src = thumbUrl; img.alt = "รูปแมวที่แนบ"; box.appendChild(img); }
    const body = el("div");
    if (!data.is_cat) {
      box.classList.add("error");
      body.textContent = data.message;
    } else {
      const dl = el("dl");
      const add = (k, v) => { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); };
      const w = data.weight_kg_min === data.weight_kg_max
        ? `${data.weight_kg_min} กก.` : `${data.weight_kg_min}–${data.weight_kg_max} กก.`;
      add("ขนาด", data.size_label);
      add("น้ำหนักประมาณ", w);
      add("ลักษณะขน", data.coat_length);
      if (data.price) add("ราคาอาบน้ำแมวประมาณ", `${data.price.total.toLocaleString("th-TH")} บาท`);
      body.appendChild(dl);
      body.appendChild(el("span", "note", "ระบบกรอกขนาด ขน และน้ำหนักให้แล้ว แก้ไขเองได้ · ราคาสุดท้ายยืนยันที่ร้าน"));
    }
    box.appendChild(body);
    box.hidden = false;
  }

  $("catFile").addEventListener("change", async (e) => {
    const file = e.target.files[0];
    e.target.value = "";
    if (!file) return;
    const box = $("catResult");
    if (!ALLOWED_TYPES.includes(file.type) || file.size > MAX_IMAGE_MB * 1024 * 1024) {
      box.className = "cat-result error";
      box.textContent = `รองรับเฉพาะไฟล์ JPG, PNG หรือ WEBP ไม่เกิน ${MAX_IMAGE_MB} MB`;
      box.hidden = false;
      return;
    }
    const label = $("catUploadLabel");
    label.textContent = "กำลังประเมินจากรูป…";
    box.className = "cat-result";
    box.textContent = "กำลังดูขนาดตัว น้ำหนัก และความยาวขนของน้องแมว…";
    box.hidden = false;
    try {
      const [data, thumb] = await Promise.all([postCatEstimate(file), toDataUrl(file)]);
      renderCatResult(data, thumb);
      if (data.is_cat) applyCatEstimate(compactEstimate(data));
    } catch (err) {
      box.className = "cat-result error";
      box.textContent = err.message && !/fetch/i.test(err.message) ? err.message : "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองอีกครั้ง";
    } finally {
      label.textContent = "แนบรูปแมวรูปอื่น";
    }
  });

  function petType() { return newForm.elements.pet_type.value; }

  function renderServices() {
    const box = $("serviceList");
    const keep = new Set([...box.querySelectorAll("input:checked")].map((i) => i.value));
    box.innerHTML = "";
    options.services
      .filter((s) => !s.pet_type_confirmed || s.pet_type === petType())
      .forEach((s) => {
        const label = el("label", "service-item");
        const cb = el("input");
        cb.type = "checkbox";
        cb.name = "service_ids";
        cb.value = s.id;
        cb.checked = keep.has(s.id);
        const text = el("span");
        text.appendChild(el("span", "svc-name", `${s.name} · ${s.price} บาท`));
        if (s.detail) text.appendChild(el("span", "svc-meta", s.detail));
        label.append(cb, text);
        box.appendChild(label);
      });
    updateEstimate();
  }

  function selectedServices() {
    return [...newForm.querySelectorAll('input[name="service_ids"]:checked')].map((i) => i.value);
  }

  function updateEstimate() {
    const ids = selectedServices();
    const extras = catExtras(ids);
    const total = options.services.filter((s) => ids.includes(s.id)).reduce((a, s) => a + s.price, 0)
      + extras.reduce((a, x) => a + x.amount, 0);
    const extraText = extras.length ? ` (รวมค่า${extras.map((x) => `${x.label} +${x.amount}`).join(", ")})` : "";
    $("estimate").textContent = ids.length
      ? `ราคาประมาณการ ${total.toLocaleString("th-TH")} บาท${extraText} · ยืนยันราคาสุดท้ายที่ร้าน`
      : "";
  }

  function showFormError(box, message, list) {
    box.innerHTML = "";
    box.appendChild(el("div", null, message));
    if (list && list.length) {
      const ul = el("ul");
      list.forEach((t) => ul.appendChild(el("li", null, t)));
      box.appendChild(ul);
    }
    box.hidden = false;
  }

  function switchTab(which) {
    const isNew = which === "new";
    $("tabNew").setAttribute("aria-selected", String(isNew));
    $("tabStatus").setAttribute("aria-selected", String(!isNew));
    newForm.hidden = !isNew;
    statusForm.hidden = isNew;
  }

  async function openBooking(which = "new", catEstimate = null, target = null) {
    try {
      await loadOptions();
    } catch (err) {
      showError(`เปิดแบบฟอร์มจองไม่ได้: ${err.message}`, false);
      return;
    }
    if (catEstimate) newForm.querySelector('input[name="pet_type"][value="แมว"]').checked = true;
    renderServices();
    updateCatBox();
    refreshSlots();
    if (catEstimate) {
      applyCatEstimate(catEstimate);
      renderCatResult({ is_cat: true, ...catEstimate, price: catEstimate.total ? { total: catEstimate.total } : null }, null);
    }
    switchTab(which);
    let autoLookup = null;
    if (which === "status") {
      // Always start from a clean panel and the booking asked for (or the latest one),
      // never from the id/result left over from an earlier check.
      resetStatusPanel();
      const want = target || lastBooking();
      if (want && want.id) {
        const contact = want.contact || knownBookings()[want.id] || "";
        statusForm.elements.booking_id.value = want.id;
        statusForm.elements.contact.value = contact;
        if (contact) autoLookup = { id: want.id, contact };
      }
    }
    if (!dialog.open) dialog.showModal();
    if (autoLookup) lookup(autoLookup.id, autoLookup.contact);
  }

  newForm.addEventListener("change", (e) => {
    if (e.target.id === "catFile") return;
    if (e.target.name === "pet_type") { renderServices(); updateCatBox(); }
    else if (e.target.name === "date") refreshSlots();
    else updateEstimate();
    const boxes = newForm.querySelectorAll('input[name="service_ids"]');
    const n = selectedServices().length;
    boxes.forEach((b) => { b.disabled = !b.checked && n >= 4; });
  });

  newForm.addEventListener("submit", async (e) => {
    e.preventDefault();
    const err = $("bookingError");
    err.hidden = true;
    const f = newForm.elements;
    const missing = [];
    if (!selectedServices().length) missing.push("เลือกบริการอย่างน้อย 1 รายการ");
    if (!f.date.value) missing.push("เลือกวันที่");
    refreshSlots();
    if (!f.time.value) missing.push(`เลือกเวลา (เริ่มนัดได้ทุก 1 ชั่วโมง ${options.time_slots[0]}–${options.last_start} น.)`);
    else if (!timeAllowed(f.time.value)) missing.push(`เวลาเริ่มนัดต้องอยู่ระหว่าง ${options.time_slots[0]}–${options.last_start} น. ทุก 1 ชั่วโมง`);
    if (!f.contact_name.value.trim()) missing.push("กรอกชื่อผู้ติดต่อ");
    if (!f.contact.value.trim()) missing.push("กรอกเบอร์โทรหรือ LINE ID");
    if (missing.length) { showFormError(err, "กรุณากรอกข้อมูลให้ครบ", missing); return; }

    const body = {
      pet_type: petType(),
      pet_name: f.pet_name.value.trim() || null,
      weight_kg: f.weight_kg.value ? Number(f.weight_kg.value) : null,
      service_ids: selectedServices(),
      date: f.date.value,
      time: f.time.value,
      contact_name: f.contact_name.value.trim(),
      contact: f.contact.value.trim(),
      note: f.note.value.trim() || null,
      cat_size: isCat() ? (f.cat_size.value || null) : null,
      coat_length: isCat() ? (f.coat_length.value || null) : null,
    };
    const btn = $("submitBooking");
    btn.disabled = true;
    btn.textContent = "กำลังส่งคำขอ…";
    try {
      const res = await fetch("/api/bookings", {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = data.detail || {};
        const list = (d.errors || []).map((x) => (typeof x === "string" ? x : fieldError(x)));
        if (d.suggestions && d.suggestions.length) {
          list.push("บริการที่ตรงกับน้ำหนัก: " + d.suggestions.map((s) => `${s.name} (${s.price} บาท)`).join(", "));
        }
        showFormError(err, d.message || `ส่งคำขอไม่สำเร็จ (HTTP ${res.status})`, list);
        return;
      }
      rememberBooking(data.booking_id, body.contact);
      resetStatusPanel();
      dialog.close();
      hideError();
      newForm.reset();
      clearCatResult();
      renderServices();
      updateCatBox();
      const lines = [
        data.message,
        "",
        `บริการ: ${data.services.map((s) => `${s.name} ${s.price} บาท`).join(", ")}`,
        ...(data.extra_items || []).map((x) => `${x.label}: +${x.amount} บาท`),
        `ราคาประมาณการรวม: ${data.estimated_total.toLocaleString("th-TH")} บาท`,
        `วันเวลาที่ขอ: ${thaiDate(data.date)} เวลา ${data.time} น.`,
      ];
      (data.warnings || []).forEach((w) => lines.push(`หมายเหตุ: ${w}`));
      addMessage({ role: "bot", text: lines.join("\n"), mode: "booking", actions: ["open_booking_status"], bookingId: data.booking_id });
    } catch (_) {
      showFormError(err, "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองส่งอีกครั้ง");
    } finally {
      btn.disabled = false;
      btn.textContent = "ส่งคำขอจอง";
    }
  });

  const FIELD_NAMES = {
    pet_type: "ชนิดสัตว์", pet_name: "ชื่อสัตว์เลี้ยง", weight_kg: "น้ำหนัก", service_ids: "บริการ",
    date: "วันที่", time: "เวลา", contact_name: "ชื่อผู้ติดต่อ", contact: "ช่องทางติดต่อ",
    note: "หมายเหตุ", cat_size: "ขนาดแมว", coat_length: "ลักษณะขน",
  };
  function fieldError(e) {
    const key = Array.isArray(e.loc) ? e.loc[e.loc.length - 1] : "";
    return `${FIELD_NAMES[key] || key || "ข้อมูล"}: รูปแบบไม่ถูกต้อง`;
  }

  function thaiDate(iso) {
    const [y, m, d] = iso.split("-").map(Number);
    return new Date(y, m - 1, d).toLocaleDateString("th-TH", { weekday: "short", day: "numeric", month: "short", year: "numeric" });
  }

  function statusLines(data) {
    const lines = [
      `สถานะคำขอ ${data.booking_id}: ${data.status_label}`,
      `วันเวลา: ${thaiDate(data.date)} ${data.time} น.`,
      `สัตว์เลี้ยง: ${[data.pet_type, data.pet_name, data.cat_size ? `ขนาด${data.cat_size}` : "", data.coat_length].filter(Boolean).join(" · ")}`,
      `บริการ: ${data.services.map((s) => `${s.name} ${s.price} บาท`).join(", ")}`,
      ...(data.extra_items || []).map((x) => `${x.label}: +${x.amount} บาท`),
      `ราคาประมาณการ: ${data.estimated_total.toLocaleString("th-TH")} บาท`,
    ];
    if (data.staff_note) lines.push(`ข้อความจากร้าน: ${data.staff_note}`);
    return lines;
  }

  async function chatStatus(id) {
    hideError();
    const contact = knownBookings()[id];
    if (!contact) {
      addMessage({
        role: "bot", mode: "booking", bookingId: id, actions: ["open_booking_status"],
        text: `ตรวจสถานะคำขอ ${id} ได้เลยค่ะ กดปุ่มด้านล่างแล้วกรอกเบอร์โทรหรือ LINE ID ที่ใช้ตอนจอง (เพื่อความปลอดภัยของข้อมูลลูกค้า)`,
      });
      lastAction = null;
      return;
    }
    setBusy(true);
    try {
      const res = await fetch(`/api/bookings/${encodeURIComponent(id)}?contact=${encodeURIComponent(contact)}`, { cache: "no-store" });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        addMessage({ role: "bot", mode: "booking", text: data.detail?.message || `ตรวจสถานะไม่สำเร็จ (HTTP ${res.status})`,
          actions: ["open_booking_status"], bookingId: id });
      } else {
        addMessage({ role: "bot", mode: "booking", text: statusLines(data).join("\n"),
          actions: ["open_booking_status"], bookingId: data.booking_id });
      }
      lastAction = null;
    } catch (_) {
      showError("เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ตรวจสอบว่าเปิด backend อยู่ แล้วลองส่งอีกครั้ง", true);
    } finally {
      setBusy(false);
    }
  }

  function renderStatus(data, contact) {
    const box = $("statusResult");
    box.innerHTML = "";
    const badge = el("span", `badge ${data.status}`, data.status_label);
    box.appendChild(el("strong", null, `${data.booking_id} `));
    box.appendChild(badge);
    const dl = el("dl");
    const add = (k, v) => { if (v) { dl.appendChild(el("dt", null, k)); dl.appendChild(el("dd", null, v)); } };
    add("สัตว์เลี้ยง", [data.pet_type, data.pet_name, data.weight_kg ? `${data.weight_kg} กก.` : "",
      data.cat_size ? `ขนาด${data.cat_size}` : "", data.coat_length].filter(Boolean).join(" · "));
    add("บริการ", data.services.map((s) => `${s.name} ${s.price} บาท`).join(", "));
    add("ค่าบริการเพิ่ม", (data.extra_items || []).map((x) => `${x.label} +${x.amount} บาท`).join(", "));
    add("ราคาประมาณการ", `${data.estimated_total.toLocaleString("th-TH")} บาท`);
    add("วันเวลา", `${thaiDate(data.date)} ${data.time} น.`);
    add("ข้อความจากร้าน", data.staff_note);
    box.appendChild(dl);
    if (data.can_cancel) {
      const b = el("button", "cancel-btn", "ยกเลิกคำขอนี้");
      b.type = "button";
      b.addEventListener("click", () => cancelBooking(data.booking_id, contact));
      box.appendChild(b);
    } else if (data.cancel_blocked_reason && ["pending", "confirmed"].includes(data.status)) {
      box.appendChild(el("p", "opt", data.cancel_blocked_reason));
    }
    box.hidden = false;
  }

  async function lookup(id, contact) {
    const seq = ++lookupSeq;
    const err = $("statusError");
    err.hidden = true;
    const box = $("statusResult");
    box.hidden = false;
    box.innerHTML = "";
    box.appendChild(el("span", "opt", `กำลังตรวจสถานะ ${id}…`));
    try {
      const res = await fetch(`/api/bookings/${encodeURIComponent(id)}?contact=${encodeURIComponent(contact)}`,
        { cache: "no-store" });  // always the current status, never a cached answer
      const data = await res.json().catch(() => ({}));
      if (seq !== lookupSeq) return;  // a newer lookup has started; this answer is stale
      if (!res.ok) {
        box.hidden = true;
        showFormError(err, data.detail?.message || `ตรวจสถานะไม่สำเร็จ (HTTP ${res.status})`);
        return;
      }
      rememberBooking(data.booking_id, contact);
      renderStatus(data, contact);
    } catch (_) {
      if (seq !== lookupSeq) return;
      box.hidden = true;
      showFormError(err, "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองอีกครั้ง");
    }
  }

  async function cancelBooking(id, contact) {
    if (!confirm(`ยืนยันการยกเลิกคำขอ ${id}?`)) return;
    const err = $("statusError");
    try {
      const res = await fetch(`/api/bookings/${encodeURIComponent(id)}/cancel`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ contact }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { showFormError(err, data.detail?.message || `ยกเลิกไม่สำเร็จ (HTTP ${res.status})`); return; }
      renderStatus({ ...data, can_cancel: false }, contact);
      addMessage({ role: "bot", text: data.message, mode: "booking", actions: ["open_booking_status"], bookingId: data.booking_id });
    } catch (_) {
      showFormError(err, "เชื่อมต่อเซิร์ฟเวอร์ไม่ได้ ลองอีกครั้ง");
    }
  }

  statusForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const id = statusForm.elements.booking_id.value.trim().toUpperCase();
    const contact = statusForm.elements.contact.value.trim();
    if (!id || !contact) { showFormError($("statusError"), "กรอกหมายเลขคำขอและเบอร์โทรหรือ LINE ID"); return; }
    lookup(id, contact);
  });

  $("bookBtn").addEventListener("click", () => openBooking("new"));
  $("closeBooking").addEventListener("click", () => dialog.close());
  $("tabNew").addEventListener("click", () => switchTab("new"));
  $("tabStatus").addEventListener("click", () => openBooking("status"));
  dialog.addEventListener("click", (e) => { if (e.target === dialog) dialog.close(); });

  // ---------------------------------------------------------------- init
  load();
  renderAll();
  renderSuggestions();
  checkHealth();

  // Coming from the storefront page (/?book=1 or /?book=status): open the booking form right away,
  // then drop the query string so a refresh doesn't reopen it.
  const params = new URLSearchParams(location.search);
  const bookParam = params.get("book");
  if (bookParam === "1" || bookParam === "status") {
    params.delete("book");
    const qs = params.toString();
    window.history.replaceState(null, "", location.pathname + (qs ? "?" + qs : "") + location.hash);
    openBooking(bookParam === "status" ? "status" : "new");
  }
})();
