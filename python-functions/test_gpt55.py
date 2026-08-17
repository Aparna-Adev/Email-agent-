from openai import OpenAI

client = OpenAI(
    base_url="https://email-summary-model.openai.azure.com/openai/v1",
    api_key="<AZURE_OPENAI_API_KEY>"
)

response = client.chat.completions.create(
    model="gpt-5.5",
    messages=[
        {
            "role": "user",
            "content": "Classify: Production portal is down for all users."
        }
    ]
)

print(response.choices[0].message.content)