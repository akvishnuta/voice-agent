/* ── Zepto Voice Agent — Full conversational frontend ────────────── */

(function () {
  "use strict";

  // ── DOM ──────────────────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const chatEl = $("chat");
  const messagesEl = $("messages");
  const welcomeEl = $("welcome");
  const micBtn = $("micBtn");
  const micIcon = $("micIcon");
  const textInput = $("textInput");
  const sendBtn = $("sendBtn");
  const statusBadge = $("statusBadge");
  const listenIndicator = $("listeningIndicator");
  const ttsToggle = $("ttsToggle");
  const ttsIcon = $("ttsIcon");

  // ── State ────────────────────────────────────────────────────────
  let sessionId = null;
  let pollingId = null;
  let isSpeaking = false;
  let ttsEnabled = true;
  let knownMessageCount = 0;

  // ── Speech Recognition (STT) ─────────────────────────────────────
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
  let recognition = null;
  let isListening = false;

  function supportsSTT() { return !!SpeechRecognition; }

  function startListening() {
    if (!supportsSTT()) {
      addMessage("agent", "Speech recognition isn't available in this browser. Try Chrome.", "error");
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
      if (finalText.trim()) {
        textInput.value = finalText.trim();
        handleSend();
      }
    };

    recognition.start();
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

  // ── Speech Synthesis (TTS) ──────────────────────────────────────
  function speak(text) {
    if (!ttsEnabled || !text || isSpeaking) return;
    if (!window.speechSynthesis) return;

    // Cancel any ongoing speech
    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "en-IN";
    utterance.rate = 1.0;
    utterance.pitch = 1.0;
    utterance.volume = 1.0;

    // Pick an Indian English voice if available
    const voices = window.speechSynthesis.getVoices();
    const indianVoice = voices.find(
      (v) => v.lang.startsWith("en") && v.lang.includes("IN")
    );
    if (indianVoice) utterance.voice = indianVoice;

    isSpeaking = true;
    utterance.onend = () => { isSpeaking = false; };
    utterance.onerror = () => { isSpeaking = false; };

    window.speechSynthesis.speak(utterance);
  }

  // Pre-load voices (they load async)
  if (window.speechSynthesis) {
    window.speechSynthesis.getVoices(); // trigger async load
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.getVoices();
    };
  }

  // ── Chat rendering ──────────────────────────────────────────────
  function addMessage(role, text, type, extra) {
    welcomeEl.hidden = true;

    // Remove typing indicator if present
    const typingEl = messagesEl.querySelector(".msg.typing-indicator");
    if (typingEl) typingEl.remove();

    const div = document.createElement("div");
    div.className = `msg ${role}${type === "error" ? " error" : ""}${type === "done" ? " done" : ""}`;

    let html = "";
    if (role === "user") {
      html = `<div class="sender">You</div>${escapeHtml(text)}`;
    } else {
      html = `<div class="sender">🛵 Assistant</div>${escapeHtml(text)}`;
      if (type === "confirmation" && extra) {
        html += `<div class="confirm-chips">
          <button class="chip yes" data-confirm="yes">✅ Yes, proceed</button>
          <button class="chip no" data-confirm="no">❌ No, cancel</button>
        </div>`;
      }
    }

    div.innerHTML = html;
    messagesEl.appendChild(div);
    scrollDown();

    // Agent messages: speak + yield for confirmation auto-tap
    if (role === "agent") {
      speak(text);
    }

    // Wire confirmation chips
    if (type === "confirmation" && extra) {
      div.querySelectorAll(".chip").forEach((chip) => {
        chip.addEventListener("click", () => {
          const confirmed = chip.dataset.confirm === "yes";
          // Disable both chips
          div.querySelectorAll(".chip").forEach((c) => (c.disabled = true));
          addMessage("user", confirmed ? "Yes, proceed!" : "No, cancel");
          confirmOrder(confirmed);
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

  // ── API ─────────────────────────────────────────────────────────
  async function api(method, path, body) {
    const opts = {
      method,
      headers: { "Content-Type": "application/json" },
    };
    if (body) opts.body = JSON.stringify(body);
    const resp = await fetch(path, opts);
    if (!resp.ok) {
      const errBody = await resp.json().catch(() => ({}));
      throw new Error(errBody.detail || `HTTP ${resp.status}`);
    }
    return resp.json();
  }

  // ── Command lifecycle ───────────────────────────────────────────

  async function handleSend() {
    const text = textInput.value.trim();
    if (!text) return;
    textInput.value = "";
    sendBtn.disabled = true;

    addMessage("user", text);
    showTyping();

    try {
      const data = await api("POST", "/api/v1/agent/say", {
        session_id: sessionId || "",
        text: text,
      });

      sessionId = data.session_id;
      renderMessages(data.messages || []);
      updateStatus(data);

      // If this was a new parse, start execution automatically after a beat
      if (data.status === "parsed" && !sessionId) {
        // Actually sessionId is now set, check if we should auto-execute
        // We won't auto-execute - wait for user confirmation via chips
      }
    } catch (err) {
      addMessage("agent", `Oops: ${err.message}`, "error");
      updateStatus({ status: "error" });
    } finally {
      sendBtn.disabled = false;
    }
  }

  async function confirmOrder(confirmed) {
    if (!sessionId) return;

    try {
      // /say handles yes/no auto-detection, or use /confirm directly
      const data = await api("POST", `/api/v1/agent/confirm/${sessionId}`, {
        confirmed,
      });
      renderMessages(data.messages || []);
      updateStatus(data);

      if (confirmed && data.status === "awaiting_confirmation") {
        // Still awaiting → start the execution flow
        await startExecution();
      } else if (confirmed && data.status === "searching") {
        // Already proceeded → start polling
        startPolling();
      } else if (data.status === "checking_out" || data.status === "completed") {
        startPolling();
      }
    } catch (err) {
      addMessage("agent", `Error: ${err.message}`, "error");
    }
  }

  async function startExecution() {
    if (!sessionId) return;
    showTyping();

    try {
      const data = await api("POST", `/api/v1/agent/execute/${sessionId}`);
      renderMessages(data.messages || []);
      updateStatus(data);
      startPolling();
    } catch (err) {
      addMessage("agent", `Error: ${err.message}`, "error");
    }
  }

  // ── Polling ─────────────────────────────────────────────────────
  function startPolling() {
    if (pollingId) clearInterval(pollingId);

    pollingId = setInterval(async () => {
      try {
        const data = await api("GET", `/api/v1/agent/status/${sessionId}`);
        renderMessages(data.messages || []);
        updateStatus(data);

        if (["completed", "cancelled", "failed"].includes(data.status)) {
          clearInterval(pollingId);
          pollingId = null;
        }
      } catch (_) { /* ignore transient polling errors */ }
    }, 1200);
  }

  // ── UI updates ──────────────────────────────────────────────────
  function renderMessages(messages) {
    if (!messages || !messages.length) return;

    // Only render new messages
    for (let i = knownMessageCount; i < messages.length; i++) {
      const m = messages[i];
      // Skip if already rendered (check by text + role)
      const existing = messagesEl.querySelectorAll(".msg");
      let alreadyShown = false;
      existing.forEach((el) => {
        const textEl = el.querySelector(":scope > :not(.sender):not(.confirm-chips)");
        if (textEl && textEl.textContent.trim() === m.text && el.classList.contains(m.role)) {
          alreadyShown = true;
        }
      });
      if (!alreadyShown) {
        addMessage(m.role, m.text, m.type || "message", m.type === "confirmation" ? m : null);

        // If it's a confirmation message, auto-tap if we have a pending confirmation
        // But let the user tap manually for better experience
      }
    }
    knownMessageCount = messages.length;
  }

  function updateStatus(data) {
    if (!data) return;
    const s = data.status || "init";
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
  }

  // ── Reset / new session ─────────────────────────────────────────
  function resetSession() {
    sessionId = null;
    knownMessageCount = 0;
    if (pollingId) {
      clearInterval(pollingId);
      pollingId = null;
    }
  }

  // ── Event listeners ─────────────────────────────────────────────

  micBtn.addEventListener("click", () => {
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

  ttsToggle.addEventListener("click", () => {
    ttsEnabled = !ttsEnabled;
    ttsIcon.textContent = ttsEnabled ? "🔊" : "🔇";
    ttsToggle.classList.toggle("muted", !ttsEnabled);
    if (!ttsEnabled) window.speechSynthesis.cancel();
  });

  // ── Init ────────────────────────────────────────────────────────
  if (!supportsSTT()) {
    micBtn.title = "Speech not supported";
    micBtn.style.opacity = "0.35";
  }

  console.log("🛵 Zepto Voice Agent ready");
})();
