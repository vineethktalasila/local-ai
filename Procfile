ollama: OLLAMA_KEEP_ALIVE=10m OLLAMA_MAX_MODELS=1 ollama serve
router: litellm --config config.yaml --port 4000
guardian: uvicorn server:app --host 0.0.0.0 --port 8000
webui: open-webui serve
