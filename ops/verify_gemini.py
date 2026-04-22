import os
k = os.environ.get("GEMINI_API_KEY", "")
print(f"length: {len(k)}")
print(f"prefix: {k[:4]}")
print(f"AI_PROVIDER: {os.environ.get('AI_PROVIDER', '(unset)')}")
