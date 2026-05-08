from openai import OpenAI

client = OpenAI(
    base_url="http://192.168.100.15:1234/v1",
    api_key="sk-lm-wPb7s5yu:aDNqNIiRG4Pyk7Q4s9ua"
)

response = client.chat.completions.create(
    model="qwen/qwen2.5-vl-7b:2",
    messages=[
        {
            "role": "system",
            "content": "You are an expert QA Assistant. You are tasked with answering questions about the QA process."
        },
        {
            "role": "user",
            "content": "Explain the QA process in simple words."
        }
    ]
)

print(response.choices[0].message.content)