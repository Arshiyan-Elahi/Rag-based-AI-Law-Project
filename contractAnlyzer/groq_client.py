import requests
from config import GROQ_API_KEY, GROQ_MODEL


def query_groq(prompt):
    headers = {
        "Authorization": f"Bearer {GROQ_API_KEY}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": GROQ_MODEL,
        "temperature": 0,
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an Austrian legal contract analysis assistant. "
                    "Always respond EXACTLY in the requested format. "
                    "No extra text, no explanations."
                )
            },
            {"role": "user", "content": prompt}
        ]
    }
    response = requests.post(
        "https://api.groq.com/openai/v1/chat/completions",
        headers=headers,
        json=payload
    )
    if response.status_code == 429:
        print(f"Rate limit reached for the API key.")
        raise Exception("Groq API rate limit reached. Please wait and try again later.")
    if response.status_code != 200:
        print(f"Groq API Error: {response.status_code} - {response.text}")
        raise Exception(f"Groq API returned {response.status_code}")
    data = response.json()
    if "choices" not in data:
        raise Exception(f"Groq API invalid response: {data}")
    return data["choices"][0]["message"]["content"].strip()