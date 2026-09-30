"""Provider-neutral SMS delivery."""
import os
from dataclasses import dataclass

import requests


class SMSProviderError(Exception):
    pass


@dataclass
class SMSResponse:
    provider_reference: str = ""


class SMSProvider:
    def send(self, *, phone_number: str, message: str) -> SMSResponse:
        raise NotImplementedError


class ConfiguredHTTPProvider(SMSProvider):
    def send(self, *, phone_number: str, message: str) -> SMSResponse:
        endpoint = os.getenv("SMS_API_URL", "")
        token = os.getenv("SMS_API_TOKEN", "")
        sender = os.getenv("SMS_SENDER_ID", "")
        if not endpoint or not token:
            raise SMSProviderError("SMS gateway not configured.")
        try:
            response = requests.post(
                endpoint,
                json={"to": phone_number, "message": message, "from": sender},
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SMSProviderError(str(exc)[:255]) from exc
        body = response.json() if response.content else {}
        return SMSResponse(str(body.get("id") or body.get("message_id") or ""))


def get_sms_provider() -> SMSProvider:
    provider = os.getenv("SMS_PROVIDER", "http").lower()
    if provider == "http":
        return ConfiguredHTTPProvider()
    raise SMSProviderError(f"Unsupported SMS provider: {provider}")


def send_sms(*, phone_number: str, message: str) -> SMSResponse:
    if not os.getenv("SMS_API_URL") or not os.getenv("SMS_API_TOKEN"):
        raise SMSProviderError("SMS gateway not configured.")
    if not phone_number:
        raise SMSProviderError("Recipient has no phone number on file.")
    return get_sms_provider().send(phone_number=phone_number, message=message)
