import sys
import os

# Add the gshare_project directory to path
current_dir = os.path.dirname(os.path.abspath(__file__))
gshare_project_dir = os.path.dirname(current_dir)
sys.path.insert(0, gshare_project_dir)

from groqai.groq_proxy import call_groq


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
            # Call Groq through Cloudflare Worker (non-streaming)
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
            )

            # Get final response JSON and print assistant message
            data = response.json()
            content = data["choices"][0]["message"]["content"]
            print(content)

        except Exception as e:
            print(f"Error: {e}")
            import traceback
            traceback.print_exc()

if __name__ == "__main__":
    chat_with_ai()

