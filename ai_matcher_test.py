import os
import sys
import time

# Ensure imports from current directory work
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from match_service.ai_clients import call_cerebras_fast_judge, call_gemini_judge
import config

def run_test_suite():
    print("\n" + "="*40)
    print("🚀 AI Matching Engine - Plain Text Test Suite")
    print("="*40)
    
    tokens = [
        {"symbol": "PEPE", "name": "Pepe the Frog"},
        {"symbol": "DOGE", "name": "Dogecoin"},
        {"symbol": "WTM", "name": "我踏马"},
        {"symbol": "超级周期", "name": "超级周期"},
    ]
    
    # Test Cases: (Tweet, Expected for Fast List, Expected for Gemini Index, Description)
    test_cases = [
        ("Looking for a moon shot with PEPE!", [0], 0, "Direct Symbol Match"),
        ("狗狗币真可爱，想买一点", [1], 1, "Chinese Translation Match (DOGE)"),
        ("WTM! 这个项目太酷了", [2], 2, "Direct Abbreviation Match"),
        ("If you think a supercycle is coming because of this tweet, you are going to be very disappointed Lower your expectations It's possible that absolutely nothing happens over the next year And that would be a good thing, because it would mean you get to stack more", [3], 3, "Chinese Name Match"),
        ("This is just a random tweet about weather.", [], -1, "No Match Case"),
    ]

    print(f"\nTarget Tokens: {[(t['symbol'], t['name']) for t in tokens]}")

    # 0. Warm-up Phase
    print("\n" + "-"*30)
    print("🔥 Warm-up Phase (Ping Cerebras 2x)")
    print("-"*30)
    for i in range(1, 3):
        print(f"Warm-up #{i}...")
        start = time.time()
        call_cerebras_fast_judge("ping", tokens)
        print(f"Warm-up #{i} Time: {time.time()-start:.2f}s")

    # 1. Cerebras Fast Test
    print("\n" + "-"*30)
    print("📡 Testing Cerebras Fast (gpt-oss-120b)")
    print("-"*30)
    for tweet, expected_fast, _, desc in test_cases:
        print(f"\n[Case] {desc}")
        print(f"Tweet: \"{tweet}\"")
        start = time.time()
        result = call_cerebras_fast_judge(tweet, tokens)
        print(f"Result: {result} (Expected: {expected_fast}) | Time: {time.time()-start:.2f}s")
        if result == expected_fast:
            print("Status: PASS ✅")
        else:
            print("Status: FAIL ❌")

    # 2. Gemini Precise Test
    print("\n" + "-"*30)
    print("🤖 Testing Gemini Precise (Reasoning)")
    print("-" * 30)
    for tweet, _, expected_gem, desc in test_cases:
        print(f"\n[Case] {desc}")
        print(f"Tweet: \"{tweet}\"")
        start = time.time()
        result = call_gemini_judge(tweet, tokens, image_paths=None)
        print(f"Result: {result} (Expected: {expected_gem}) | Time: {time.time()-start:.2f}s")
        if result == expected_gem:
            print("Status: PASS ✅")
        else:
            print("Status: FAIL ❌")

if __name__ == "__main__":
    if not config.DEEPSEEK_API_KEY or not config.GEMINI_API_KEY:
        print("❌ Error: Missing API keys in config.py")
        sys.exit(1)
        
    run_test_suite()
