import sys
import os
sys.path.append(os.path.abspath('backend'))
from stream_bot import StreamBot

def test():
    print("Initializing StreamBot...")
    bot = StreamBot()
    if bot.ai_client:
        print("AI Client successfully loaded from ~/.env!")
        print("\n--- Testing Chat ---")
        reply1 = bot.generate_ai_response("Viewer123", "This stream is amazing! What kind of music is this?")
        print(f"Viewer123: This stream is amazing! What kind of music is this?")
        print(f"AI DJ: {reply1}")
        
        print("\n--- Testing Song Request ---")
        reply2 = bot.generate_ai_response("DanceFan", "Can you play some DNB Bloodrave?")
        print(f"DanceFan: Can you play some DNB Bloodrave?")
        print(f"AI DJ: {reply2}")
    else:
        print("Failed to load Gemini API key. Ensure it's in ~/.env")

if __name__ == "__main__":
    test()
