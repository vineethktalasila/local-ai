# Local AI Guardian Pipeline

A robust, memory-safe local LLM pipeline optimized for Apple Silicon (macOS). 

Running large local models (like DeepSeek 32B or Qwen 2.5 Coder) can easily exhaust unified memory, pushing the system into heavy swap usage and causing lockups. This project introduces a **Guardian Proxy** that sits between Open WebUI, LiteLLM, and Ollama to actively monitor macOS hardware telemetry, protect your SSD from excessive wear, and ensure smooth inference.

## ✨ Key Features

*   **Proactive Memory Protection:** Monitors macOS memory pressure via `sysctl`. Aborts model loading if pressure hits Critical (Red).
*   **Active Swap Guard:** Tracks SSD swap memory delta in real-time during token generation. If swap increases by >100 MB, the stream is killed and models are instantly flushed from RAM.
*   **Tool-Stripping Firewall:** Intercepts Open WebUI requests and forcibly removes hidden `tools` arrays, preventing local models from hallucinating JSON function calls instead of writing code/text.
*   **Live Terminal Dashboard:** Replaces the messy output stream with a dynamic, single-line CLI status bar tracking chunk speed, free RAM, and swap delta.
*   **CSV Telemetry Logging:** Automatically measures Time-to-First-Token (Prompt Eval Time), generation speeds, and hardware states, logging them to a size-capped (5MB) rolling CSV file.
*   **Automated Log Rotation:** Uses a custom `logger.py` to pipe and rotate background service logs (Ollama, LiteLLM, WebUI) at 50MB, keeping your main terminal clean.

## 🏗️ Architecture

```text
├── Procfile         # Honcho config to run all services simultaneously
├── server.py        # The FastAPI Guardian Proxy (Hardware monitor & routing)
├── logger.py        # Universal 50MB rotating file handler
└── logs/            # Auto-generated folder
    ├── metrics.csv  # Telemetry ledger (Speeds, Tokens, RAM, Swap)
    ├── ollama.log   
    ├── router.log   
    └── webui.log    
```

## 📋 Prerequisites

*   **OS:** macOS (Apple Silicon recommended for memory and Metal GPU architecture)
*   **Ollama:** Installed and running locally
*   **Python:** 3.10 or higher

### Install Python Dependencies

You will need the standard AI routing stack, our process manager, and the system monitoring tools:

```bash
pip install fastapi uvicorn httpx psutil requests honcho litellm open-webui
```

## 🚀 Installation & Setup

1. **Clone/Create the Files:** Ensure `server.py`, `logger.py`, and `Procfile` are in the same root directory.
2. **Configure LiteLLM:** Ensure you have a `config.yaml` file mapped for LiteLLM if you are using specific routing rules, or adjust the `router` command in the `Procfile` as needed.
3. **Download Models:** Pull your desired models via Ollama (e.g., `ollama pull qwen2.5-coder:32b`).

## 💻 Usage

To launch the entire pipeline, simply run the process manager from your terminal:

```bash
honcho start
```

### What Happens Next?
1. `honcho` will spin up Ollama, LiteLLM, Open WebUI, and the Guardian Proxy.
2. Standard logs from Ollama, LiteLLM, and Open WebUI are silently piped into the `./logs/` folder and capped at 50MB to prevent disk bloat.
3. Open WebUI will be available at `http://localhost:8080`. (Ensure your WebUI is pointed to the Guardian proxy at `http://localhost:8000/v1` instead of Ollama or LiteLLM directly).

### The Terminal UI
When you submit a prompt, the terminal will display a clean, dynamic status bar:

```text
⏳ [Generating] qwen2.5-coder | Chunks: 132 | Speed: 13.1 c/s | RAM: 7.3 GB | Swap Δ: 1.2 MB | Press: 🟢 Normal 
```

Upon completion, a summary card is printed and the data is appended to `logs/metrics.csv`:

```text
==================================================
✅ GUARDIAN METRICS SUMMARY
--------------------------------------------------
🤖 Model         : qwen2.5-coder:32b
⏱️ Prompt Eval   : 0.85 s
⏱️ Generation    : 24.30 s
📦 Prompt Size   : 312 tokens
📦 Response Size : 341 tokens
⚡ Gen Speed     : ~14.0 tokens/sec
💾 Peak Swap     : 1.2 MB
🧠 Free RAM      : 6.8 GB
==================================================
```

## ⚙️ Configuration

You can tweak the Guardian's thresholds directly inside `server.py`:
*   `SWAP_THRESHOLD_BYTES = 100 * 1024 * 1024`: Change this to allow more or less SSD swapping before triggering an emergency flush (Default: 100 MB).
*   `timeout=120.0`: The `httpx` timeout limit. Increase this if you frequently use massive RAG contexts (100k+ tokens) that take longer than 2 minutes to evaluate before generating the first token.
