import subprocess
import requests
import psutil
import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

LITELLM_API = "http://localhost:4000"
OLLAMA_API = "http://localhost:11434"

def check_power() -> bool:
    try:
        return "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, check=True).stdout
    except Exception:
        return False

def check_internet() -> bool:
    try:
        requests.get("https://1.1.1.1", timeout=3)
        return True
    except requests.RequestException:
        return False

def get_available_memory_gb() -> float:
    return psutil.virtual_memory().available / (1024 ** 3)

def unload_all_models():
    try:
        res = requests.get(f"{OLLAMA_API}/api/ps")
        if res.status_code == 200:
            for model in res.json().get("models", []):
                requests.post(f"{OLLAMA_API}/api/generate", json={"model": model.get("model"), "keep_alive": 0})
    except Exception:
        pass

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def transparent_proxy(request: Request, path: str):
    # Only enforce strict checks when generating a chat response
    if "chat/completions" in path and request.method == "POST":
        if not check_power():
            unload_all_models()
            raise HTTPException(status_code=503, detail="Guardian: Mac disconnected from power. Models flushed.")
        if not check_internet():
            unload_all_models()
            raise HTTPException(status_code=504, detail="Guardian: Internet timeout.")
        
        required_ram = 22.0 
        if get_available_memory_gb() < required_ram:
            raise HTTPException(status_code=507, detail="Guardian: Insufficient RAM for 32B model inference.")

    # Forward the exact request to LiteLLM
    client = httpx.AsyncClient(base_url=LITELLM_API)
    url = httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8"))
    
    req = client.build_request(
        request.method,
        url,
        headers={k: v for k, v in request.headers.items() if k.lower() not in ["host"]},
        content=await request.body()
    )
    
    response = await client.send(req, stream=True)
    return StreamingResponse(
        response.aiter_raw(), 
        status_code=response.status_code, 
        headers=response.headers
    )
