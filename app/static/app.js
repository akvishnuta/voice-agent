/* ── Zepto Voice Agent — Full conversational frontend ────────────── */

(function () {
  "use strict";

  // ── DOM ──────────────────────────────────────────────────────────
  const chatEl = document.getElementById("chat");
  const messagesEl = document.getElementById("messages");
  const welcomeEl = document.getElementById("welcome");
  const micBtn = document.getElementById("micBtn");
  const micIcon = document.getElementById("micIcon");
  const textInput = document.getElementById("textInput");
  const sendBtn = document.getElementById("sendBtn");
  const statusBadge = document.getElementById("statusBadge");
  const listenIndicator = document.getElementById("listeningIndicator");
  const ttsToggle = document.getElementById("ttsToggle");
  const ttsIcon = document.getElementById("ttsIcon");

  // ── State ────────────────────────────────────────────────────────
  let sessionId = null;
  let sessionStatus = "";
  let pollingId = null;
  let ttsEnabled = true;
  let knownMessageCount = 0;
  let audioContext = null;
  let isSending = false;

  console.log("🛵 Zepto Voice Agent — chat UI loaded");

  // ── Audio (backend TTS via gTTS) ─────────────────────────────────

  function primeAudio() {
    if (!audioContext) {
      try {
        audioContext = new (window.AudioContext || window.webkitAudioContext)();
        console.log("🔊 AudioContext primed");
      } catch (_) { /* noop */ }
    }
    if (audioContext && audioContext.state === "suspended") {
      console.log("Audio context suspended")
      audioContext.resume().catch(() => {});
    }
  }

  function playAudio(url) {
    if (!ttsEnabled || !url) return;
    primeAudio();
    const audio = new Audio(url);
    audio.play().catch((err) => console.warn("🔇 Autoplay blocked:", err.message));
  }

  function playTextAsAudio(text) {
    if (!ttsEnabled || !text) return;
    primeAudio();
    const url = `/api/v1/agent/tts?text=${encodeURIComponent(text)}`;
    new Audio(url).play().catch((err) => console.warn("🔇 TTS autoplay blocked:", err.message));
  }

  // ── Speech Recognition (STT) ─────────────────────────────────────
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let isListening = false;

  function supportsSTT() { return !!SpeechRecognition; }

  function startListening() {
    if (!supportsSTT()) {
      addMessage("agent", "Speech recognition not available. Try Chrome.", "error");
      return;
    }
    if (isListening) return;
    isListening = true;
    micBtn.classList.add("listening");
    micIcon.textContent = "🔴";
    listenIndicator.hidden = false;

    recognition = new SpeechRecognition();
    recognition.lang = "en-IN";
    recognition.continuous = false;
    recognition.interimResults = true;

    let finalText = "";

    recognition.onresult = (ev) => {
      for (let i = ev.resultIndex; i < ev.results.length; i++) {
        const t = ev.results[i][0].transcript;
        if (ev.results[i].isFinal) finalText += t;
      }
    };

    recognition.onerror = () => { stopListening(); };

    recognition.onend = () => {
      stopListening();
      const trimmed = finalText.trim();
      if (trimmed) {
        console.log("🎤 STT heard:", trimmed);
        textInput.value = trimmed;
        handleSend();
      } else {
        console.log("🎤 STT ended — no speech detected");
      }
    };

    recognition.start();
    console.log("🎤 Listening…");
  }

  function stopListening() {
    isListening = false;
    micBtn.classList.remove("listening");
    micIcon.textContent = "🎤";
    listenIndicator.hidden = true;
    if (recognition) {
      try { recognition.abort(); } catch (_) { }
      recognition = null;
    }
  }

  // ── Chat rendering ──────────────────────────────────────────────
  function addMessage(role, text, type, audioUrl) {
    console.log("📨 render [%s] type=%s: %.80s", role, type || "msg", text);

    welcomeEl.hidden = true;

    // Remove typing indicator if present
    const typingEl = messagesEl.querySelector(".msg.typing-indicator");
    if (typingEl) typingEl.remove();

    const div = document.createElement("div");
    const classes = ["msg", role];
    if (type === "error") classes.push("error");
    if (type === "done") classes.push("done");
    if (type === "otp") classes.push("otp-request");
    div.className = classes.join(" ");

    const safeText = escapeHtml(text);

    if (role === "user") {
      div.innerHTML = `<div class="sender">You</div>${safeText}`;
    } else {
      const dataAttr = escapeAttr(text);
      const audioBtn = ttsEnabled && text
        ? ` <button class="play-audio-btn" data-text="${dataAttr}" title="Play">🔊</button>`
        : "";
      div.innerHTML = `<div class="sender">🛵 Assistant</div>${safeText}${audioBtn}`;

      if (type === "confirmation") {
        div.innerHTML += `<div class="confirm-chips">
          <button class="chip yes" data-confirm="yes">✅ Yes, proceed</button>
          <button class="chip no" data-confirm="no">❌ No, cancel</button>
        </div>`;
      }
    }

    messagesEl.appendChild(div);
    scrollDown();

    // Agent audio playback — speak every agent message, including progress
    if (role === "agent") {
      if (audioUrl) {
        playAudio(audioUrl);
      } else {
        playTextAsAudio(text);
      }
    }

    // Wire 🔊 buttons
    div.querySelectorAll(".play-audio-btn").forEach((btn) => {
      btn.addEventListener("click", () => playTextAsAudio(btn.dataset.text));
    });

    // Wire confirmation chips
    if (type === "confirmation") {
      div.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const confirmed = chip.dataset.confirm === "yes";
          div.querySelectorAll(".chip").forEach((c) => (c.disabled = true));
          addMessage("user", confirmed ? "Yes, proceed!" : "No, cancel");

          if (!confirmed) {
            confirmOrder(false);
            return;
          }

          // "Yes" means different things depending on current stage:
          if (sessionStatus === "parsed") {
            // Stage 1: start searching Zepto
            startExecution();
          } else if (sessionStatus === "awaiting_confirmation") {
            // Stage 2: place the order
            confirmOrder(true);
          }
        });
      });
    }
  }

  function showTyping() {
    const div = document.createElement("div");
    div.className = "msg agent typing-indicator";
    div.innerHTML = `<div class="sender">🛵 Assistant</div><span class="typing"><span></span><span></span><span></span></span>`;
    messagesEl.appendChild(div);
    scrollDown();
  }

  function removeTyping() {
    const el = messagesEl.querySelector(".msg.typing-indicator");
    if (el) el.remove();
  }

  function scrollDown() {
    requestAnimationFrame(() => {
      chatEl.scrollTop = chatEl.scrollHeight;
    });
  }

  function escapeHtml(text) {
    const d = document.createElement("div");
    d.textContent = text;
    return d.innerHTML;
  }

  function escapeAttr(text) {
    if (!text) return "";
    return String(text).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
  }

  function isMessageAlreadyRendered(role, text) {
    if (!text) return false;
    const t = text.trim();
    return Array.from(messagesEl.querySelectorAll(`.msg.${role}`)).some((el) => {
      // Get the message body by removing the sender prefix text
      const full = el.textContent.replace("You", "").replace("🛵 Assistant", "").trim();
      return full === t || el.textContent.includes(t);
    });
  }

  // ── API ─────────────────────────────────────────────────────────
  async function api(method, path, body) {
    const opts = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    console.log("🌐 %s %s", method, path);
    const resp = await fetch(path, opts);
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      throw new Error(errBody.detail || `HTTP ${resp.status}`);
    }
    const data = await resp.json();
    console.log("🌐 → status=%s new_msgs=%d total=%s",
                data.status, (data.messages || []).length, data.total_messages);
    return data;
  }

  // ── Command lifecycle ───────────────────────────────────────────

  async function handleSend() {
    if (isSending) {
      console.log("⏳ Already sending — ignoring duplicate");
      return;
    }
    const text = textInput.value.trim();
    if (!text) return;
    isSending = true;
    textInput.value = "";
    sendBtn.disabled = true;
    primeAudio();

    console.log("📤 SEND: %.150s", text);
    addMessage("user", text);
    showTyping();

    try {
      const data = await api("POST", "/api/v1/agent/say", {
        session_id: sessionId || "",
        text: text,
        since: knownMessageCount,
      });

      sessionId = data.session_id;
      renderMessages(data.messages || []);
      knownMessageCount = data.total_messages ?? knownMessageCount;
      updateStatus(data);

      // Clear typing if no new messages were returned
      if (!data.messages || !data.messages.length) {
        removeTyping();
      }

      // If the backend kicked off execution (e.g. via spoken "yes"),
      // start polling for progress
      if (["searching", "adding_to_cart", "checking_out", "awaiting_confirmation"].includes(data.status)) {
        startPolling();
      }
    } catch (err) {
      console.error("❌ Send failed:", err);
      addMessage("agent", `Oops: ${err.message}`, "error");
      updateStatus({ status: "error" });
    } finally {
      sendBtn.disabled = false;
      isSending = false;
    }
  }

  async function startExecution() {
    if (!sessionId) return;
    console.log("▶️ Starting execution for session %s", sessionId);
    showTyping();
    try {
      const data = await api("POST", `/api/v1/agent/execute/${sessionId}?since=${knownMessageCount}`);
      renderMessages(data.messages || []);
      knownMessageCount = data.total_messages ?? knownMessageCount;
      updateStatus(data);

      // If no new messages yet (background task still starting), clear the
      // typing indicator to avoid a frozen spinner while polling kicks in.
      if (!data.messages || !data.messages.length) {
        removeTyping();
      }

      startPolling();
    } catch (err) {
      console.error("❌ Execution failed:", err);
      addMessage("agent", `Error: ${err.message}`, "error");
    }
  }

  async function confirmOrder(confirmed) {
    if (!sessionId) return;
    primeAudio();

    try {
      const data = await api("POST", `/api/v1/agent/confirm/${sessionId}`, {
        confirmed,
        since: knownMessageCount,
      });
      renderMessages(data.messages || []);
      knownMessageCount = data.total_messages ?? knownMessageCount;
      updateStatus(data);

      const s = data.status;
      if (["searching", "adding_to_cart", "checking_out", "awaiting_confirmation"].includes(s)) {
        startPolling();
      }
    } catch (err) {
      console.error("❌ Confirm failed:", err);
      addMessage("agent", `Error: ${err.message}`, "error");
    }
  }

  // ── Polling ─────────────────────────────────────────────────────
  function startPolling() {
    if (pollingId) clearInterval(pollingId);
    console.log("⏳ Polling started for session %s", sessionId);

    pollingId = setInterval(async () => {
      try {
        const data = await api("GET", `/api/v1/agent/status/${sessionId}?since=${knownMessageCount}`);
        renderMessages(data.messages || []);
        knownMessageCount = data.total_messages ?? knownMessageCount;
        updateStatus(data);

        if (["completed", "cancelled", "failed"].includes(data.status)) {
          clearInterval(pollingId);
          pollingId = null;
          console.log("⏳ Polling stopped — terminal state:", data.status);
        }
      } catch (_) { /* transient */ }
    }, 1200);
  }

  // ── UI updates ──────────────────────────────────────────────────
  function renderMessages(messages) {
    if (!messages || !messages.length) return;
    console.log("📨 renderMessages: rendering %d new message(s)", messages.length);

    for (const m of messages) {
      if (!m || !m.text) continue;

      // Safety check — avoid duplicates if a race condition occurs
      if (isMessageAlreadyRendered(m.role, m.text)) {
        console.log("📨  skip role=%s (already shown)", m.role);
        continue;
      }

      addMessage(m.role, m.text, m.type || "message", m.audio_url || null);
    }
  }

  function updateStatus(data) {
    if (!data) return;
    const s = data.status || "init";
    sessionStatus = s;
    statusBadge.textContent = s.replace(/_/g, " ");
    statusBadge.className = "status-badge";
    if (["searching", "adding_to_cart", "checking_out"].includes(s)) {
      statusBadge.classList.add("active");
    } else if (s === "failed") {
      statusBadge.classList.add("error");
    } else if (["completed", "done"].includes(s)) {
      statusBadge.classList.add("done");
      statusBadge.textContent = "Done! 🎉";
    }
    console.log("📊 Status: %s", s);
  }

  // ── Event listeners ─────────────────────────────────────────────

  micBtn.addEventListener("click", () => {
    primeAudio();
    if (isListening) { stopListening(); return; }
    startListening();
  });

  sendBtn.addEventListener("click", handleSend);

  textInput.addEventListener("input", () => {
    sendBtn.disabled = !textInput.value.trim();
  });

  textInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && textInput.value.trim()) {
      e.preventDefault();
      handleSend();
    }
  });

  chatEl.addEventListener("click", primeAudio);

  ttsToggle.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    ttsIcon.textContent = ttsEnabled ? "🔊" : "🔇";
    ttsToggle.classList.toggle("muted", !ttsEnabled);
    console.log("🔊 TTS %s", ttsEnabled ? "ON" : "OFF");
  });

  // ── Init ────────────────────────────────────────────────────────
  if (!supportsSTT()) {
    micBtn.title = "Speech not supported";
    micBtn.style.opacity = "0.35";
    console.log("🎤 STT not available in this browser");
  }

  console.log("✅ Zepto Voice Agent ready");
})();
