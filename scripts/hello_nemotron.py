"""Check that the build.nvidia.com NIM API works before any harness code depends on it.

Two probes, in order:
  1. one plain chat completion, which proves the key and endpoint are good;
  2. one tool-calling round trip, which proves the model returns the tool-call shape our
     agent workflows rely on.

The second probe matters on its own. A key can be valid for chat yet the deployment still
not behave as our workflows assume, and finding that out here costs two or three calls
instead of a debugging session later.
"""
import json
import os
import sys
from pathlib import Path

from openai import OpenAI


def load_env() -> None:
    env = Path(__file__).resolve().parents[1] / ".env"
    if not env.exists():
        sys.exit(".env 가 없습니다. .env.example 을 복사해 NVIDIA_API_KEY 를 채우세요.")
    for line in env.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())


def main() -> None:
    load_env()
    client = OpenAI(
        base_url=os.environ.get("NVIDIA_BASE_URL", "https://integrate.api.nvidia.com/v1"),
        api_key=os.environ["NVIDIA_API_KEY"],
    )
    model = os.environ.get("MODEL_WORKER", "nvidia/nemotron-3.5-lightning-30b-a3b")

    # Probe 1: a plain completion. If this fails, the key or the endpoint is wrong.
    r = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": "한 문장으로 자기소개를 해 주세요."}],
        temperature=0.6,
        top_p=0.95,
        max_tokens=128,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    print("[chat]", r.choices[0].message.content.strip())

    # Probe 2: tool calling. The model must come back with a tool call, not prose about one.
    tools = [{
        "type": "function",
        "function": {
            "name": "prr",
            "description": "2x2 표에서 비례보고비(PRR)를 계산한다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "integer", "description": "약물+사례"},
                    "b": {"type": "integer", "description": "약물+기타사례"},
                    "c": {"type": "integer", "description": "기타약물+사례"},
                    "d": {"type": "integer", "description": "기타약물+기타사례"},
                },
                "required": ["a", "b", "c", "d"],
            },
        },
    }]
    msgs = [{"role": "user", "content": "a=40, b=960, c=200, d=98800 일 때 PRR 을 도구로 계산해 주세요."}]
    r = client.chat.completions.create(
        model=model, messages=msgs, tools=tools, tool_choice="auto",
        temperature=0.6, top_p=0.95, max_tokens=256,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    m = r.choices[0].message
    if not m.tool_calls:
        sys.exit("[tool] 모델이 도구를 호출하지 않았습니다: " + str(m.content))
    call = m.tool_calls[0]
    args = json.loads(call.function.arguments)
    a, b, c, d = (args[k] for k in "abcd")
    prr = (a / (a + b)) / (c / (c + d))
    print(f"[tool] {call.function.name}({args}) -> PRR={prr:.3f}")
    msgs += [m, {"role": "tool", "tool_call_id": call.id, "content": json.dumps({"prr": round(prr, 3)})}]
    r = client.chat.completions.create(
        model=model, messages=msgs, tools=tools, temperature=0.6, top_p=0.95, max_tokens=128,
        extra_body={"chat_template_kwargs": {"enable_thinking": False}},
    )
    print("[final]", r.choices[0].message.content.strip())
    print("OK: NIM API 와 도구 호출이 동작합니다.")


if __name__ == "__main__":
    main()
