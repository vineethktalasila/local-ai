ollama: bash -c "OLLAMA_KEEP_ALIVE=10m OLLAMA_MAX_MODELS=1 OLLAMA_DEBUG=0 ollama serve 2>&1 | python3 logger.py logs/ollama.log"
router: bash -c "litellm --config config.yaml --port 4000 2>&1 | python3 logger.py logs/router.log"
guardian: uvicorn server:app --host 0.0.0.0 --port 8000
webui: bash -c "open-webui serve 2>&1 | python3 logger.py logs/webui.log"
