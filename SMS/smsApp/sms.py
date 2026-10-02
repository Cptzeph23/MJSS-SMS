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


class AfricasTalkingProvider(SMSProvider):
    def send(self, *, phone_number: str, message: str) -> SMSResponse:
        username = os.getenv("AFRICASTALKING_USERNAME", "")
        api_key = os.getenv("AFRICASTALKING_API_KEY", "")
        endpoint = os.getenv("AFRICASTALKING_API_URL", "https://api.africastalking.com/version1/messaging")
        sender = os.getenv("SMS_SENDER_ID", "")
        if not username or not api_key:
            raise SMSProviderError("Africa's Talking credentials are not configured.")
        data = {"username": username, "to": phone_number, "message": message}
        if sender:
            data["from"] = sender
        try:
            response = requests.post(endpoint, data=data, headers={"apiKey": api_key, "Accept": "application/json"}, timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SMSProviderError(str(exc)[:255]) from exc
        body = response.json() if response.content else {}
        recipients = body.get("SMSMessageData", {}).get("Recipients", [])
        reference = recipients[0].get("messageId", "") if recipients else ""
        return SMSResponse(str(reference))


class TwilioProvider(SMSProvider):
    def send(self, *, phone_number: str, message: str) -> SMSResponse:
        account_sid = os.getenv("TWILIO_ACCOUNT_SID", "")
        auth_token = os.getenv("TWILIO_AUTH_TOKEN", "")
        sender = os.getenv("TWILIO_FROM_NUMBER", "")
        if not account_sid or not auth_token or not sender:
            raise SMSProviderError("Twilio credentials are not configured.")
        endpoint = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
        try:
            response = requests.post(endpoint, data={"To": phone_number, "From": sender, "Body": message}, auth=(account_sid, auth_token), timeout=10)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise SMSProviderError(str(exc)[:255]) from exc
        body = response.json() if response.content else {}
        return SMSResponse(str(body.get("sid", "")))


def get_sms_provider() -> SMSProvider:
    provider = os.getenv("SMS_PROVIDER", "http").lower()
    if provider == "http":
        return ConfiguredHTTPProvider()
    if provider in {"africastalking", "africa_talking"}:
        return AfricasTalkingProvider()
    if provider == "twilio":
        return TwilioProvider()
    raise SMSProviderError(f"Unsupported SMS provider: {provider}")


def send_sms(*, phone_number: str, message: str) -> SMSResponse:
    if not phone_number:
        raise SMSProviderError("Recipient has no phone number on file.")
    provider = get_sms_provider()
    if isinstance(provider, ConfiguredHTTPProvider) and (not os.getenv("SMS_API_URL") or not os.getenv("SMS_API_TOKEN")):
        raise SMSProviderError("SMS gateway not configured.")
    return provider.send(phone_number=phone_number, message=message)
