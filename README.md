# 🛵 Zepto Voice Agent

An LLM-powered **voice agent** that logs into your Zepto quick-commerce account, parses
natural-language shopping commands, searches for products, applies your preferences
(cheapest / best rated), and places the order — **after asking you to confirm**.

The agent responds in natural language (text + voice) just like a human assistant.

---

## Quick Start

```bash
# 1. Clone & enter project
cd project-ai

# 2. Create virtual environment & install
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# 3. Install Playwright browser engine
playwright install chromium

# 4. Configure your credentials (see below)
cp .env.example .env
# ⚠️  Edit .env — set your DEEPSEEK_API_KEY and ZEPTO_PHONE

# 5. Start the server
uvicorn app.main:app --reload
```

### Open the UI

[http://127.0.0.1:8000/api/v1/app](http://127.0.0.1:8000/api/v1/app)

Works best in **Google Chrome** on desktop (full SpeechRecognition + SpeechSynthesis support).

---

## 📖 Full Setup Guide

### Prerequisites

- **Python 3.11+**
- **Google Chrome** or Chromium installed (for Playwright browser automation)
- A **DeepSeek API key** — [platform.deepseek.com](https://platform.deepseek.com) (or OpenAI / Anthropic key as alternative)
- A **Zepto account** with your phone number registered

### 1. Install dependencies

```bash
python3 -m venv .venv           # create virtual environment
source .venv/bin/activate       # activate it (macOS/Linux)
pip install -r requirements.txt
playwright install chromium     # download browser engine for automation
```

### 2. Configure environment (credentials)

```bash
cp .env.example .env
```

Open `.env` in your editor and fill in your credentials:

```ini
# ── REQUIRED ────────────────────────────────────────────────
DEEPSEEK_API_KEY="sk-your-deepseek-api-key-here"   # DeepSeek API key
ZEPTO_PHONE="+919876543210"                         # Your Zepto phone number

# ── OPTIONAL (defaults are fine) ───────────────────────────
LLM_PROVIDER="deepseek"          # deepseek | openai | anthropic
LLM_MODEL="deepseek-v4-flash"
BROWSER_HEADLESS=false           # false = see the browser (OTP login needs this)
ZEPTO_URL="https://www.zepto.com"
```

> **🔒 Security:** `.env` is listed in `.gitignore` — it will **never** be committed
> to git. Only `.env.example` (which has placeholder values) is tracked.
>
> If you switch provider, set `LLM_PROVIDER=openai` / `LLM_PROVIDER=anthropic`
> and fill in the corresponding `OPENAI_API_KEY` or `ANTHROPIC_API_KEY`.

### 3. Run the server

```bash
uvicorn app.main:app --reload
```

You should see:
```
INFO:     Uvicorn running on http://127.0.0.1:8000
INFO:     Application startup complete.
```

### 4. Open the UI

| URL | What it is |
|---|---|
| [http://127.0.0.1:8000/api/v1/app](http://127.0.0.1:8000/api/v1/app) | 🎤 **Voice agent chat UI** (this is what you want) |
| [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs) | 📖 Swagger API docs |
| [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health) | ✅ Health check |

---

## 🎮 How to Use

### The Chat Interface

The UI is a conversational chat — like WhatsApp but for ordering groceries.

```
┌─────────────────────────────────────┐
│  🛵 Zepto Voice Agent     Done! 🎉  │
├─────────────────────────────────────┤
│                                     │
│  ┌────────────────────────────┐     │
│  │ You                        │     │
│  │ Order milk, eggs and bread │     │
│  │ Buy cheapest. Max ₹600     │     │
│  └────────────────────────────┘     │
│                                     │
│  ┌────────────────────────────┐     │
│  │ 🛵 Assistant               │     │
│  │ Got it! I understood 3     │     │
│  │ items: milk, eggs, bread.  │     │
│  │ I'll keep it under ₹600.   │     │
│  │ Shall I go ahead?          │     │
│  │                            │     │
│  │  [✅ Yes, proceed] [❌ No] │     │
│  └────────────────────────────┘     │
│                                     │
│         🔊 Agent speaks this        │
│            out loud                 │
│                                     │
│  ┌────────────────────────────┐     │
│  │ 🛵 Assistant               │     │
│  │ Found milk at ₹56. Found   │     │
│  │ eggs at ₹72. Found bread   │     │
│  │ at ₹35...                  │     │
│  └────────────────────────────┘     │
│                                     │
│  ┌────────────────────────────┐     │
│  │ 🛵 Assistant               │     │
│  │ Your cart total is ₹163.   │     │
│  │ It's within your budget.   │     │
│  │ Shall I place the order?   │     │
│  │                            │     │
│  │  [✅ Yes, proceed] [❌ No] │     │
│  └────────────────────────────┘     │
│                                     │
│  ┌────────────────────────────┐     │
│  │ 🛵 Assistant               │     │
│  │ Order placed successfully! │     │
│  │ Your delivery is on its    │     │
│  │ way! 🎉                    │     │
│  └────────────────────────────┘     │
│                                     │
├─────────────────────────────────────┤
│  🎤 [Type or speak here…]    [Send] │
└─────────────────────────────────────┘
```

### Voice Input 🎤

1. Tap the **microphone button** (🎤) in the bottom-left
2. Speak clearly in English — e.g. *"Order a dozen eggs and full cream milk under ₹200"*
3. The agent transcribes your speech and sends it automatically
4. The agent replies in **text** and **speaks aloud**

> **Browser support:** Voice input uses the Web Speech API. Works best in **Google Chrome**
> on desktop. Safari has partial support; Firefox does not support speech recognition.
> Text input always works in any browser.

### Text Input ⌨️

Type your command into the text box and press **Enter** or tap **Send**.

### Voice Replies 🔊

Agent responses are spoken aloud using browser speech synthesis (Indian English voice).
Toggle voice on/off using the **🔊 / 🔇** button in the top-right corner of the UI.

### Confirmation Flow ✅

The agent asks you to confirm at two checkpoints:

1. **After parsing** — "I understood milk, eggs, bread. Shall I search Zepto?"
2. **After adding to cart** — "Total is ₹163. Shall I place the order?"

Tap **✅ Yes, proceed** or say "Yes" — the agent continues. Say "No" or tap **❌ No, cancel**
to abort.

---

## 🗣️ Example Voice Commands

| You say | What the agent does |
|---|---|
| *"Order milk, eggs and bread. Cheapest."* | Finds the cheapest milk, eggs, and bread on Zepto and adds them to cart |
| *"Buy a dozen eggs and full cream milk under ₹200"* | Parses quantity (12 eggs), preference (cheapest), and budget (₹200) |
| *"Get me apples, bananas, and a packet of biscuits"* | Searches for each item generically |
| *"Buy the best rated basmati rice"* | Picks the top result for basmati rice |
| *"Order paneer, butter, and wheat bread under ₹500"* | Three items with a budget cap |

---

## 🧠 Architecture

```
                    ┌──────────────────┐
                    │   🌐 Browser UI   │
                    │  (Chat + Voice)   │
                    └────────┬─────────┘
                             │ POST /agent/say
                             ▼
┌─────────────────────────────────────────────────────┐
│                  FastAPI Server                      │
│                                                     │
│  ┌──────────────┐   ┌──────────────────────────┐    │
│  │   LLM Parser  │   │   Session Orchestrator   │    │
│  │  (DeepSeek)   │──▶│  (state machine + msgs)  │    │
│  └──────────────┘   └───────────┬──────────────┘    │
│                                 │                    │
│                    ┌────────────▼──────────────┐    │
│                    │  Playwright Zepto Client  │    │
│                    │   (Browser Automation)    │    │
│                    └───────────────────────────┘    │
└─────────────────────────────────────────────────────┘
```

### Key Components

| Component | Role |
|---|---|
| **Frontend** (`app/static/`) | Chat UI, SpeechRecognition (STT), SpeechSynthesis (TTS) |
| **LLM Parser** (`app/agent/parser.py`) | Parses NL commands → structured order, generates natural responses |
| **Orchestrator** (`app/agent/orchestrator.py`) | Manages session state, message history, background execution |
| **Zepto Client** (`app/browser/zepto_client.py`) | Playwright browser automation: login, search, cart, checkout |
| **FastAPI Routes** (`app/api/v1/routes.py`) | REST endpoints: `/agent/say`, `/agent/execute`, `/agent/confirm` |

---

## 🔧 Configuration Reference

All configuration is in `.env`. Copy `.env.example` and fill in your values.

| Variable | Required | Default | Description |
|---|---|---|---|
| `DEEPSEEK_API_KEY` | ✅ | `""` | DeepSeek API key (get from [platform.deepseek.com](https://platform.deepseek.com)) |
| `ZEPTO_PHONE` | ✅ | `""` | Your Zepto account phone number |
| `LLM_PROVIDER` | ❌ | `deepseek` | `deepseek` \| `openai` \| `anthropic` |
| `LLM_MODEL` | ❌ | `deepseek-v4-flash` | Model name for your provider |
| `DEEPSEEK_BASE_URL` | ❌ | `https://api.deepseek.com` | DeepSeek API endpoint |
| `OPENAI_API_KEY` | ❌ | `""` | OpenAI key (if provider=openai) |
| `ANTHROPIC_API_KEY` | ❌ | `""` | Anthropic key (if provider=anthropic) |
| `ZEPTO_URL` | ❌ | `https://www.zepto.com` | Zepto web app URL |
| `BROWSER_HEADLESS` | ❌ | `false` | `false` = see the browser window (needed for OTP entry) |
| `PLAYWRIGHT_COOKIES_PATH` | ❌ | `.zepto_cookies.json` | Where to persist login cookies |

---

## 🔒 Security

- **`.env`** is in `.gitignore` — your API keys and phone number are never committed
- Only **`.env.example`** (with placeholder values) is tracked in git
- Zepto session cookies are stored locally in **`.zepto_cookies.json`** (also gitignored)
- OTP login requires you to enter the code in the browser window — the agent never sees it

---

## 🐛 Troubleshooting

### "Zepto selectors not matching"
The website may have changed. Update CSS selectors in:
`app/browser/zepto_client.py` → `SELECTORS` dict.

### "Browser opens but login doesn't work"
- Make sure `BROWSER_HEADLESS=false` — you need to see the browser to enter the OTP
- Cookies are saved after first login, so you won't need OTP again unless they expire
- If cookies expire, delete `.zepto_cookies.json` and try again

### "Speech recognition not working"
- Use **Google Chrome** — Firefox and Safari don't support Web Speech API for recognition
- Voice replies (TTS) work in Chrome, Safari, and Edge

### "LLM parsing errors"
- Check your `DEEPSEEK_API_KEY` in `.env` is valid
- Verify your API key has credits
- Try setting `LLM_PROVIDER=openai` if you have an OpenAI key instead

---

## Development

```bash
# Run tests
pytest -v

# Start server with auto-reload
uvicorn app.main:app --reload
```
