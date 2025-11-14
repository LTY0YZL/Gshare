import requests
from groqai.instructions import SYSTEM_INSTRUCTIONS

WORKER_URL = "https://groq-voice-orders.ams63tube.workers.dev/"

def call_groq(messages, model="moonshotai/kimi-k2-instruct-0905", temperature=1, max_tokens=8192, stream=False, system_instructions=None):
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

    payload = {
        "model": model,
        "messages": full_messages,
        "temperature": temperature,
        "max_completion_tokens": max_tokens,
        "top_p": 1,
        "stream": stream,
    }

    response = requests.post(WORKER_URL, json=payload)
    response.raise_for_status()
    return response

