import os
import json

from openai import AzureOpenAI


def get_thread_llm_client():
    api_key = os.getenv("AZURE_OPENAI_API_KEY")
    endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")

    if not api_key or not endpoint:
        raise ValueError(
            "Missing Azure OpenAI env vars: "
            "AZURE_OPENAI_API_KEY or AZURE_OPENAI_ENDPOINT"
        )

    return AzureOpenAI(
        api_key=api_key,
        api_version="2024-12-01-preview",
        azure_endpoint=endpoint
    )


def clean_llm_json_response(content: str) -> str:
    """
    Remove markdown code block wrappers from LLM output.
    """

    content = content.strip()

    if content.startswith("```json"):
        content = content.replace(
            "```json",
            "",
            1
        ).strip()

    if content.startswith("```"):
        content = content.replace(
            "```",
            "",
            1
        ).strip()

    if content.endswith("```"):
        content = content[:-3].strip()

    return content


def summarize_thread_state(thread_state):
    """
    Summarize cleaned thread state.
    """

    client = get_thread_llm_client()

    prompt = f"""
Return valid JSON only.

Subject:
{thread_state.get("latest_subject", "")}

Thread Status:
{thread_state.get("thread_status", "")}

Latest Clean Reply:
{thread_state.get("latest_reply_body", "")}

Return JSON with:
summary
action_needed
business_impact
"""

    response = client.chat.completions.create(
        model="email-summary-model",
        messages=[
            {
                "role": "system",
                "content": (
                    "You summarize enterprise "
                    "support email threads."
                )
            },
            {
                "role": "user",
                "content": prompt
            }
        ],
        temperature=0.2
    )

    content = response.choices[0].message.content

    content = clean_llm_json_response(content)

    try:
        return json.loads(content)

    except Exception:

        return {
            "summary": content,
            "action_needed": "Review manually",
            "business_impact": "Unknown"
        }