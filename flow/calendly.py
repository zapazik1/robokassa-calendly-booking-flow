"""Calendly: свободные слоты, одноразовая ссылка на запись, разбор вебхука.

ID диалога агента добавляется в ссылку как utm_source. Calendly возвращает его
в вебхуке в payload.tracking.utm_source, и событие «записался» приходит в нужный диалог.
"""
import datetime
import urllib.parse

import requests

API = "https://api.calendly.com"


def headers(token):
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def available_slots(token, event_type_uri, start, days=7):
    """Свободное время по типу встречи. Calendly отдаёт окно не длиннее 7 дней."""
    end = start + datetime.timedelta(days=min(days, 7))
    response = requests.get(f"{API}/event_type_available_times", headers=headers(token), params={
        "event_type": event_type_uri,
        "start_time": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "end_time": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
    }, timeout=15)
    response.raise_for_status()
    return [item["start_time"] for item in response.json().get("collection", []) if item.get("status") == "available"]


def single_use_link(token, event_type_uri, dialog_id):
    """Одноразовая ссылка на запись с ID диалога в utm_source."""
    response = requests.post(f"{API}/scheduling_links", headers=headers(token), json={
        "max_event_count": 1,
        "owner": event_type_uri,
        "owner_type": "EventType",
    }, timeout=15)
    response.raise_for_status()
    link = response.json()["resource"]["booking_url"]
    return with_dialog_id(link, dialog_id)


def with_dialog_id(link, dialog_id):
    parts = urllib.parse.urlsplit(link)
    query = dict(urllib.parse.parse_qsl(parts.query))
    query["utm_source"] = str(dialog_id)
    return urllib.parse.urlunsplit(parts._replace(query=urllib.parse.urlencode(query)))


def parse_webhook(body):
    """Событие Calendly -> {"dialog_id", "event", "start_time"} или ошибка, если диалог не указан."""
    payload = body.get("payload") or {}
    raw_dialog = (payload.get("tracking") or {}).get("utm_source")
    if not str(raw_dialog or "").isdigit():
        return {"ok": False, "error": "в payload.tracking.utm_source нет ID диалога"}
    event = (payload.get("scheduled_event") or {})
    return {
        "ok": True,
        "dialog_id": int(raw_dialog),
        "event": body.get("event"),
        "start_time": event.get("start_time") or payload.get("start_time"),
        "meeting": event.get("name"),
    }
