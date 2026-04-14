from google import genai
import os

GEMINI_KEY = 'AIzaSyAeFdoshawfL5yz1AUEnh8_MiKaxXrQRcQ'
client = genai.Client(api_key=GEMINI_KEY)

models_to_try = [
    'gemini-2.0-flash-lite',
    'gemini-2.0-flash',
    'gemini-flash-latest', 
    'gemini-2.5-flash',
    'gemini-pro-latest'
]

print("\nVerifying model fallback sequence:")
successful_model = None
for model_name in models_to_try:
    try:
        print(f"Trying {model_name}...", end=" ", flush=True)
        response = client.models.generate_content(
            model=model_name,
            contents="Say 'Hello' then the model name."
        )
        print(f"✅ Success: {response.text.strip()}")
        successful_model = model_name
        break
    except Exception as e:
        err_msg = str(e)
        if '429' in err_msg or '404' in err_msg or 'NOT_FOUND' in err_msg or 'RESOURCE_EXHAUSTED' in err_msg:
            print(f"⏭️ Skipping (Quota/Not Found): {err_msg[:50]}...")
            continue
        print(f"❌ Hard failure: {err_msg}")
        break

if successful_model:
    print(f"\n✨ Verification passed! Used: {successful_model}")
else:
    print("\n❌ Verification failed: All models exhausted.")
