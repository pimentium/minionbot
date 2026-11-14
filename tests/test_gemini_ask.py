import json

import httpx
import pytest

from minionbot.translate.gemini import ASK_SYSTEM_PROMPT, CUT_OFF_NOTE, GeminiClient
from minionbot.web.errors import TranslationError, TranslationServiceError

KEY = "AIza-SECRET-KEY-123"


def Client(handler, **kwargs):
    async def NoSleep(seconds):
        pass

    return GeminiClient(KEY, client=httpx.AsyncClient(transport=httpx.MockTransport(handler)), sleep=NoSleep, **kwargs)


def Answer(*parts, finish="STOP"):
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [p if isinstance(p, dict) else {"text": p} for p in parts]}, "finishReason": finish}]})


async def test_the_prompt_goes_out_untouched_with_a_chat_system_instruction_and_no_json_mode():
    seen = {}

    def Handler(request):
        seen["url"], seen["key"], seen["body"] = str(request.url), request.headers.get("x-goog-api-key"), json.loads(request.content)
        return Answer("**Hi** there")

    prompt = "what is  this?\n\nSecond   paragraph"
    assert await Client(Handler, model="gemini-test").Ask(prompt) == "**Hi** there"
    assert seen["url"].endswith("/models/gemini-test:generateContent") and KEY not in seen["url"] and seen["key"] == KEY
    body = seen["body"]
    assert body["contents"] == [{"role": "user", "parts": [{"text": prompt}]}]
    assert body["systemInstruction"]["parts"][0]["text"] == ASK_SYSTEM_PROMPT and "generationConfig" not in body


async def test_several_parts_are_joined_and_thought_parts_skipped():
    handler = lambda request: Answer("Part one. ", {"text": "hidden reasoning", "thought": True}, "Part two.")
    assert await Client(handler).Ask("q") == "Part one. Part two."


async def test_a_cut_off_answer_says_so():
    assert await Client(lambda request: Answer("Long answer", finish="MAX_TOKENS")).Ask("q") == "Long answer" + CUT_OFF_NOTE


@pytest.mark.parametrize("response,match", [
    (httpx.Response(200, json={"promptFeedback": {"blockReason": "SAFETY"}}), "declined.*safety"),
    (Answer("x", finish="RECITATION"), "declined.*recitation"),
    (Answer("   "), "empty"),
    (httpx.Response(200, json={"candidates": []}), "empty"),
    (httpx.Response(200, text="not json at all"), "couldn't read"),
])
async def test_unusable_answers(response, match):
    with pytest.raises(TranslationError, match=match):
        await Client(lambda request: response).Ask("q")


async def test_errors_use_the_same_handling_as_translation():
    key = httpx.Response(400, json={"error": {"code": 400, "status": "INVALID_ARGUMENT", "message": "API key not valid.", "details": [{"reason": "API_KEY_INVALID"}]}})
    with pytest.raises(TranslationServiceError, match="Gemini service refused the API key"):
        await Client(lambda request: key).Ask("q")
    busy = httpx.Response(429, json={"error": {"code": 429, "status": "RESOURCE_EXHAUSTED", "message": "slow down"}})
    with pytest.raises(TranslationError, match="Gemini service is busy"):
        await Client(lambda request: busy).Ask("q")
    with pytest.raises(TranslationServiceError, match="'gemini-x' was not found"):
        await Client(lambda request: httpx.Response(404, json={"error": {"message": "nope"}}), model="gemini-x").Ask("q")
