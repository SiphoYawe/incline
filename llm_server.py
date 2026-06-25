"""llm_server.py — self-hosted Hermes on Modal (the agent's brain, no external API key).

Serves a Nous Research **Hermes** model on a Modal GPU with vLLM's
OpenAI-compatible API, so the loop's llm.py talks to it exactly like any
/v1/chat/completions endpoint. Uses Modal credits — no Anthropic/Nous key needed.

Deploy:   modal deploy llm_server.py
          -> note the printed `serve` URL, then set in the incline-secrets Modal Secret:
             HERMES_BASE_URL = https://<that-url>/v1
             HERMES_MODEL    = hermes
             HERMES_API_KEY  = EMPTY        (vLLM here is open; any token works)

Cold start downloads the weights once into a Volume; the GPU scales to zero after
5 idle minutes so it only costs credits while actually answering.
"""

import subprocess

import modal

# A capable, fast Hermes that fits one GPU. Swap to a larger Hermes (e.g.
# NousResearch/Hermes-3-Llama-3.1-70B on an 80GB GPU) for higher codegen quality.
MODEL_NAME = "NousResearch/Hermes-3-Llama-3.1-8B"
SERVED_NAME = "hermes"
VLLM_PORT = 8000

# CUDA *devel* base ships nvcc, which vLLM's flashinfer kernels JIT-compile against.
vllm_image = (
    modal.Image.from_registry("nvidia/cuda:12.4.1-devel-ubuntu22.04", add_python="3.11")
    .pip_install("vllm", "huggingface_hub[hf_transfer]")
    .env({"HF_HUB_ENABLE_HF_TRANSFER": "1"})
)

# Cache HF weights + vLLM compile artifacts so cold starts are fast after the first.
hf_cache = modal.Volume.from_name("incline-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("incline-vllm-cache", create_if_missing=True)

app = modal.App("incline-llm")


@app.function(
    image=vllm_image,
    gpu="L40S",
    volumes={
        "/root/.cache/huggingface": hf_cache,
        "/root/.cache/vllm": vllm_cache,
    },
    scaledown_window=300,   # idle 5 min -> scale to zero (conserve credits)
    timeout=900,
    # If the model is gated on Hugging Face, create a Modal Secret named
    # "huggingface" with HF_TOKEN and add it here: secrets=[modal.Secret.from_name("huggingface")]
)
@modal.concurrent(max_inputs=24)
@modal.web_server(port=VLLM_PORT, startup_timeout=900)
def serve():
    cmd = (
        f"vllm serve {MODEL_NAME} "
        f"--host 0.0.0.0 --port {VLLM_PORT} "
        f"--served-model-name {SERVED_NAME} {MODEL_NAME} "
        f"--max-model-len 8192 "
        f"--gpu-memory-utilization 0.90"
    )
    subprocess.Popen(cmd, shell=True)
