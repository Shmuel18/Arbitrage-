import os
for name in ["GEMINI_API_KEY", "GROQ_API_KEY", "ANTHROPIC_API_KEY", "AI_PROVIDER"]:
    v = os.environ.get(name, "")
    if name.endswith("_KEY"):
        print(f"{name}: length={len(v)}, prefix={v[:4] if v else '(unset)'}")
    else:
        print(f"{name}: {v or '(unset)'}")
