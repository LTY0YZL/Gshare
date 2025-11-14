import sys
import os

# Add the gshare_project directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
gshare_project_dir = os.path.dirname(current_dir)
sys.path.insert(0, gshare_project_dir)

from groqai.groq_proxy import call_groq
import json

def chat_with_ai():
    """Interactive chat with AI through Cloudflare Worker"""
    print("Chat with AI (type 'exit' to quit)")
    print("-" * 50)

    while True:
        # Get user input
        user_input = input("\nYou: ").strip()

        if user_input.lower() == 'exit':
            print("Goodbye!")
            break

        if not user_input:
            continue

        print("\nAI: ", end="", flush=True)

        try:
            # Call Groq through Cloudflare Worker
            response = call_groq(
                messages=[
                    {
                        "role": "user",
                        "content": user_input
                    }
                ],
                model="moonshotai/kimi-k2-instruct-0905",
                temperature=0.6,
                max_tokens=4096,
                stream=True
            )

            # Handle streaming response (Server-Sent Events format)
            for line in response.iter_lines():
                if line:
                    line_str = line.decode('utf-8') if isinstance(line, bytes) else line

                    # Skip empty lines and [DONE] marker
                    if not line_str or line_str == '[DONE]':
                        continue

                    # Remove "data: " prefix if present
                    if line_str.startswith('data: '):
                        line_str = line_str[6:]

                    try:
                        data = json.loads(line_str)
                        if 'choices' in data and len(data['choices']) > 0:
                            delta = data['choices'][0].get('delta', {})
                            content = delta.get('content', '')
                            if content:
                                print(content, end="", flush=True)
                    except json.JSONDecodeError:
                        pass

            print()  # New line after response

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    chat_with_ai()

