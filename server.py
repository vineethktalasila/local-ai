import subprocess
import requests
import psutil
import httpx
import json
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

LITELLM_API = "http://localhost:4000"
OLLAMA_API = "http://localhost:11434"

# Set a 100 MB tolerance buffer (in bytes) to prevent OS background noise from triggering false alarms
SWAP_THRESHOLD_BYTES = 100 * 1024 * 1024  

def check_power() -> bool:
    """Returns True if connected to AC power, False otherwise."""
    try:
        return "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, check=True).stdout
    except Exception:
        return False

def check_internet(retries=3, delay=2) -> bool:
    """Checks internet connectivity with a retry mechanism."""
    for attempt in range(retries):
        try:
            requests.get("https://1.1.1.1", timeout=3)
            return True
        except requests.RequestException:
            if attempt < retries - 1:
                import time
                time.sleep(delay)
    return False

def get_memory_pressure() -> int:
    """
    Returns the macOS memory pressure level.
    1 = Normal (Green), 2 = Warning (Yellow), 4 = Critical (Red)
    """
    try:
        res = subprocess.run(
            ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], 
            capture_output=True, text=True, check=True
        )
        return int(res.stdout.strip())
    except Exception:
        return 1

def unload_all_models():
    """Aggressively flushes all models from Ollama to protect memory and save battery."""
    try:
        res = requests.get(f"{OLLAMA_API}/api/ps", timeout=2)
        if res.status_code == 200:
            for m in res.json().get("models", []):
                requests.post(f"{OLLAMA_API}/api/generate", json={"model": m.get("model"), "keep_alive": 0})
    except Exception:
        pass

async def stream_with_swap_guard(response: httpx.Response, initial_swap_out: int):
    """
    Wraps the LLM output stream. Passes text to the UI normally, 
    but hijacks the stream if macOS pages out excessively or hits critical memory pressure.
    """
    chunk_count = 0
    
    async for chunk in response.aiter_raw():
        yield chunk
        chunk_count += 1
        
        # Check system state every 10 chunks (~200-300ms)
        if chunk_count % 10 == 0:
            current_swap_out = psutil.swap_memory().sout
            swap_delta = current_swap_out - initial_swap_out
            pressure = get_memory_pressure()
            
            # Trigger if swap increases by > 100 MB OR if memory pressure hits Critical
            if swap_delta > SWAP_THRESHOLD_BYTES or pressure >= 4:
                unload_all_models()
                
                reason = "Excessive Swap Detected" if swap_delta > SWAP_THRESHOLD_BYTES else "Critical Memory Pressure"
                error_msg = f"\n\n**[Guardian Alert: {reason}]**\nSystem limits exceeded during generation. The model has been instantly flushed from RAM to protect performance. Please start a new chat to clear the context window."
                
                error_payload = {"choices": [{"delta": {"content": error_msg}}]}
                
                yield f"data: {json.dumps(error_payload)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                break

@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def transparent_proxy(request: Request, path: str):
    body_bytes = await request.body()
    
    if "chat/completions" in path and request.method == "POST":
        # 1. Standard Pre-Flight Checks
        if not check_power():
            unload_all_models()
            raise HTTPException(status_code=503, detail="Guardian: Mac disconnected from power. Models flushed.")
        if not check_internet():
            unload_all_models()
            raise HTTPException(status_code=504, detail="Guardian: Internet timeout.")
            
        pressure = get_memory_pressure()
        if pressure >= 4:
            unload_all_models()
            raise HTTPException(
                status_code=507, 
                detail="Guardian: macOS memory pressure is Critical (Red). Model loading aborted to prevent system lockup."
            )
            
        # 2. Record the exact amount of swap memory used before inference begins
        initial_swap_out = psutil.swap_memory().sout
        
        # 3. Forward the request to LiteLLM
        client = httpx.AsyncClient(base_url=LITELLM_API)
        url = httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8"))
        
        req = client.build_request(
            request.method,
            url,
            headers={k: v for k, v in request.headers.items() if k.lower() not in ["host"]},
            content=body_bytes
        )
        
        # Send the request and await the initial headers (this is the window when the model loads into RAM)
        response = await client.send(req, stream=True)
        
        # 4. Pre-Stream Swap & Pressure Check 
        current_swap_out = psutil.swap_memory().sout
        swap_delta = current_swap_out - initial_swap_out
        current_pressure = get_memory_pressure()
        
        if swap_delta > SWAP_THRESHOLD_BYTES or current_pressure >= 4:
            unload_all_models()
            raise HTTPException(
                status_code=507, 
                detail="Guardian: Critical memory limits hit during model load. Model flushed."
            )
        
        # 5. Return the wrapped active-monitoring stream
        return StreamingResponse(
            stream_with_swap_guard(response, initial_swap_out), 
            status_code=response.status_code, 
            headers=response.headers
        )
        
    # Fallback for non-chat API requests
    client = httpx.AsyncClient(base_url=LITELLM_API, timeout=150.0)
    url = httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8"))
    req = client.build_request(
        request.method,
        url,
        headers={k: v for k, v in request.headers.items() if k.lower() not in ["host"]},
        content=body_bytes
    )
    response = await client.send(req, stream=True)
    return StreamingResponse(response.aiter_raw(), status_code=response.status_code, headers=response.headers)
