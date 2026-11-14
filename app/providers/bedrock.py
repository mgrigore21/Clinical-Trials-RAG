"""Amazon Bedrock backend: models running on AWS. See docs/07-providers.md.

Written now so both backends sit side by side, but only exercised on AWS in the deployment
phase. Tests use a fake client, so nothing here ever contacts AWS during development.
"""

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from app.providers.base import Generation, Message, ProviderError, Usage


def _client(region: str):
    # Creating the client does not call AWS or need credentials yet; that happens on the first call.
    return boto3.client("bedrock-runtime", region_name=region)


class BedrockLLM:
    def __init__(self, model_id: str, region: str, client=None):
        self.model_id = model_id
        self.client = client or _client(region)

    def generate(self, messages: list[Message], temperature: float = 0.0) -> Generation:
        # Bedrock's Converse API wants the system prompt separate from the conversation, and
        # wraps every text in a list of "content blocks".
        system = [{"text": m["content"]} for m in messages if m["role"] == "system"]
        conversation = [
            {"role": m["role"], "content": [{"text": m["content"]}]}
            for m in messages
            if m["role"] != "system"
        ]
        try:
            response = self.client.converse(
                modelId=self.model_id,
                messages=conversation,
                system=system,
                inferenceConfig={"temperature": temperature},
            )
        except (ClientError, BotoCoreError) as exc:
            raise ProviderError(f"Bedrock call failed for model {self.model_id}: {exc}") from exc

        return Generation(
            text=response["output"]["message"]["content"][0]["text"],
            usage=Usage(
                input_tokens=response["usage"]["inputTokens"],
                output_tokens=response["usage"]["outputTokens"],
            ),
        )


class BedrockEmbedder:
    def __init__(self, model_id: str, dim: int, region: str, client=None):
        self.model_id = model_id
        self.dim = dim
        self.client = client or _client(region)

    def embed(self, texts: list[str]) -> list[list[float]]:
        # Titan embeds one text per call (Ollama accepts a whole batch).
        return [self._embed_one(text) for text in texts]

    def _embed_one(self, text: str) -> list[float]:
        body = {
            "inputText": text,
            # Titan v2 can output 256, 512 or 1024 numbers. We ask for 1024 so the same database
            # column works locally and on AWS.
            "dimensions": self.dim,
            "normalize": True,
        }
        try:
            response = self.client.invoke_model(modelId=self.model_id, body=json.dumps(body))
        except (ClientError, BotoCoreError) as exc:
            raise ProviderError(f"Bedrock call failed for model {self.model_id}: {exc}") from exc
        return json.loads(response["body"].read())["embedding"]
