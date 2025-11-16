import requests
from groqai.instructions import SYSTEM_INSTRUCTIONS, AIModel

WORKER_URL = "https://groq-voice-orders.ams63tube.workers.dev/"

def call_groq(messages, model=AIModel.VOICE_ORDERS, temperature=1, max_tokens=None, stream=False, system_instructions=None):
    """
    Call Groq API through Cloudflare Worker
    """
    # Use default system instructions if none provided
    if system_instructions is None:
        system_instructions = SYSTEM_INSTRUCTIONS

    # Prepend system instructions as first message
    full_messages = [
        {"role": "system", "content": system_instructions}
    ] + messages

    # Resolve model name and default max_tokens from the enum
    if isinstance(model, AIModel):
        model_name = model.value
        if max_tokens is None:
            max_tokens = model.max_tokens
    else:
        model_name = model
        if max_tokens is None:
            max_tokens = 8192

    payload = {
        "model": model_name,
        "messages": full_messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        "top_p": 1,
        "stream": stream,
    }

    response = requests.post(WORKER_URL, json=payload)
    response.raise_for_status()
    return response

