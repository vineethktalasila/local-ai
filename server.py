import sys
import os
import time
import subprocess
import requests
import psutil
import httpx
import json
import logging
from logging.handlers import RotatingFileHandler
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import StreamingResponse

app = FastAPI()

LITELLM_API = "http://localhost:4000"
OLLAMA_API = "http://localhost:11434"

# 100 MB tolerance buffer
SWAP_THRESHOLD_BYTES = 100 * 1024 * 1024  

# --- CSV Logger Setup ---
os.makedirs("logs", exist_ok=True)
csv_file = "logs/metrics.csv"
csv_logger = logging.getLogger("csv_metrics")
csv_logger.setLevel(logging.INFO)
csv_handler = RotatingFileHandler(csv_file, maxBytes=5 * 1024 * 1024, backupCount=1)
csv_handler.setFormatter(logging.Formatter('%(message)s'))
csv_logger.addHandler(csv_handler)
# ------------------------

def check_power() -> bool:
    try:
        return "AC Power" in subprocess.run(["pmset", "-g", "batt"], capture_output=True, text=True, check=True).stdout
    except Exception:
        return False

def check_internet(retries=3, delay=2) -> bool:
    for attempt in range(retries):
        try:
            requests.get("https://1.1.1.1", timeout=3)
            return True
        except requests.RequestException:
            if attempt < retries - 1:
                time.sleep(delay)
    return False

def get_memory_pressure() -> int:
    try:
        res = subprocess.run(
            ["sysctl", "-n", "kern.memorystatus_vm_pressure_level"], 
            capture_output=True, text=True, check=True
        )
        return int(res.stdout.strip())
    except Exception:
        return 1

def unload_all_models():
    try:
        res = requests.get(f"{OLLAMA_API}/api/ps", timeout=2)
        if res.status_code == 200:
            for m in res.json().get("models", []):
                requests.post(f"{OLLAMA_API}/api/generate", json={"model": m.get("model"), "keep_alive": 0})
    except Exception:
        pass

# FIX: Passed start_time into the function as an argument
async def stream_with_swap_guard(response: httpx.Response, initial_swap_out: int, requested_model: str, start_time: float):
    chunk_count = 0
    first_token_time = None
    peak_swap_delta_mb = 0.0
    
    prompt_tokens = 0
    completion_tokens = 0
    
    print(f"\n🚀 Starting generation for: {requested_model[:15]}...", flush=True)
    
    async for chunk in response.aiter_raw():
        yield chunk
        chunk_count += 1
        
        if first_token_time is None:
            first_token_time = time.time()
            
        chunk_str = chunk.decode("utf-8", errors="ignore")
        if '"usage"' in chunk_str:
            for line in chunk_str.split("\n"):
                if line.startswith("data: ") and line.strip() != "data: [DONE]":
                    try:
                        data = json.loads(line[6:])
                        if "usage" in data and data["usage"]:
                            prompt_tokens = data["usage"].get("prompt_tokens", 0)
                            completion_tokens = data["usage"].get("completion_tokens", 0)
                    except json.JSONDecodeError:
                        pass
        
        if chunk_count % 20 == 0:
            elapsed = time.time() - start_time
            current_speed = chunk_count / elapsed if elapsed > 0 else 0
            
            current_swap_out = psutil.swap_memory().sout
            swap_delta_mb = (current_swap_out - initial_swap_out) / (1024 * 1024)
            if swap_delta_mb > peak_swap_delta_mb:
                peak_swap_delta_mb = swap_delta_mb
                
            free_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
            pressure_val = get_memory_pressure()
            
            if pressure_val == 1:
                pressure_str = "🟢 Normal"
            elif pressure_val == 2:
                pressure_str = "🟡 Warning"
            else:
                pressure_str = "🔴 Critical"
            
            print(f"⏳ [Generating] Chunks: {chunk_count} | Speed: {current_speed:.1f} c/s | RAM: {free_ram_gb:.1f} GB | Swap Δ: {swap_delta_mb:.1f} MB | Press: {pressure_str}", flush=True)

        if chunk_count % 10 == 0:
            current_swap_out = psutil.swap_memory().sout
            swap_delta_mb = (current_swap_out - initial_swap_out) / (1024 * 1024)
            pressure_val = get_memory_pressure()
            
            if swap_delta_mb > (SWAP_THRESHOLD_BYTES / (1024 * 1024)) or pressure_val >= 4:
                unload_all_models()
                reason = "Excessive Swap Detected" if swap_delta_mb > (SWAP_THRESHOLD_BYTES / (1024 * 1024)) else "Critical Memory Pressure"
                error_msg = f"\n\n**[Guardian Alert: {reason}]**\nSystem limits exceeded. Model flushed from RAM."
                error_payload = {"choices": [{"delta": {"content": error_msg}}]}
                
                yield f"data: {json.dumps(error_payload)}\n\n".encode("utf-8")
                yield b"data: [DONE]\n\n"
                
                print("\n" + "=" * 50)
                print(f"❌ GUARDIAN KILLED STREAM: {reason}")
                print("-" * 50)
                print(f"🤖 Model Requested : {requested_model}")
                print(f"💾 Peak Swap Delta : {peak_swap_delta_mb:.1f} MB")
                print("=" * 50 + "\n")
                return

    end_time = time.time()
    
    prompt_eval_sec = (first_token_time - start_time) if first_token_time else 0.0
    gen_sec = (end_time - first_token_time) if first_token_time else 0.0
    
    gen_speed = (completion_tokens / max(gen_sec, 0.001)) if completion_tokens > 0 else 0.0

    final_free_ram_gb = psutil.virtual_memory().available / (1024 ** 3)
    
    # --- Write cleaned metrics to CSV ---
    write_header = not os.path.exists(csv_file) or os.path.getsize(csv_file) == 0
    if write_header:
        csv_logger.info("Timestamp,Model,Prompt_Tokens,Response_Tokens,Eval_Time_s,Gen_Time_s,Gen_Speed_tps,Peak_Swap_MB,Free_RAM_GB")
    
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    csv_row = f"{timestamp},{requested_model},{prompt_tokens},{completion_tokens},{prompt_eval_sec:.2f},{gen_sec:.2f},{gen_speed:.1f},{peak_swap_delta_mb:.1f},{final_free_ram_gb:.1f}"
    csv_logger.info(csv_row)
    
    # --- Cleaned Terminal Summary Card ---
    print("\n" + "=" * 50)
    print(f"✅ GUARDIAN METRICS SUMMARY")
    print("-" * 50)
    print(f"🤖 Model         : {requested_model}")
    print(f"⏱️ Prompt Eval   : {prompt_eval_sec:.2f} s")
    print(f"⏱️ Generation    : {gen_sec:.2f} s")
    print(f"📦 Prompt Size   : {prompt_tokens} tokens")
    print(f"📦 Response Size : {completion_tokens} tokens")
    print(f"⚡ Gen Speed     : ~{gen_speed:.1f} tokens/sec")
    print(f"💾 Peak Swap     : {peak_swap_delta_mb:.1f} MB")
    print(f"🧠 Free RAM      : {final_free_ram_gb:.1f} GB")
    print("=" * 50 + "\n", flush=True)


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def transparent_proxy(request: Request, path: str):
    body_bytes = await request.body()
    
    if "chat/completions" in path and request.method == "POST":
        if not check_power():
            unload_all_models()
            raise HTTPException(status_code=503, detail="Guardian: Mac disconnected from power. Models flushed.")
        if not check_internet():
            unload_all_models()
            raise HTTPException(status_code=504, detail="Guardian: Internet timeout.")
            
        pressure = get_memory_pressure()
        if pressure >= 4:
            unload_all_models()
            raise HTTPException(status_code=507, detail="Guardian: Memory pressure Critical (Red). Loading aborted.")

        requested_model = "unknown"
        try:
            payload = json.loads(body_bytes.decode("utf-8"))
            requested_model = payload.get("model", "unknown")
            
            if "tools" in payload:
                del payload["tools"]
            if "tool_choice" in payload:
                del payload["tool_choice"]
                
            if payload.get("stream", False):
                payload["stream_options"] = {"include_usage": True}
                
            body_bytes = json.dumps(payload).encode("utf-8")
        except Exception:
            pass
            
        initial_swap_out = psutil.swap_memory().sout
        
        client = httpx.AsyncClient(base_url=LITELLM_API, timeout=120.0)
        url = httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8"))
        safe_headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
        
        req = client.build_request(request.method, url, headers=safe_headers, content=body_bytes)
        
        # FIX: Start the timer exactly before handing the request to LiteLLM
        start_time = time.time()
        
        response = await client.send(req, stream=True)
        
        current_swap_out = psutil.swap_memory().sout
        swap_delta = current_swap_out - initial_swap_out
        
        if swap_delta > SWAP_THRESHOLD_BYTES or get_memory_pressure() >= 4:
            unload_all_models()
            raise HTTPException(status_code=507, detail="Guardian: Critical memory limits hit during load.")
        
        return StreamingResponse(
            stream_with_swap_guard(response, initial_swap_out, requested_model, start_time), 
            status_code=response.status_code, 
            headers=response.headers
        )
        
    client = httpx.AsyncClient(base_url=LITELLM_API, timeout=120.0)
    url = httpx.URL(path=request.url.path, query=request.url.query.encode("utf-8"))
    safe_headers = {k: v for k, v in request.headers.items() if k.lower() not in ["host", "content-length"]}
    req = client.build_request(request.method, url, headers=safe_headers, content=body_bytes)
    response = await client.send(req, stream=True)
    return StreamingResponse(response.aiter_raw(), status_code=response.status_code, headers=response.headers)
