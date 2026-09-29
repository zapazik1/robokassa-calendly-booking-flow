import hashlib
import urllib.parse

import flow.booking_flow as bf
from flow import robokassa

CFG = {
    "merchant_login": "demo-shop", "password1": "pass1", "password2": "pass2", "is_test": True,
    "calendly_token": "t", "event_type_uri": "https://api.calendly.com/event_types/ABC",
    "amo_api_base": "https://example.amocrm.ru", "amo_token": "a",
    "status_paid": 111, "status_booked": 222,
}


class Resp:
    def __init__(self, payload=None, status=200, text=""):
        self.payload = payload or {}
        self.status_code = status
        self.text = text

    def json(self):
        return self.payload

    def raise_for_status(self):
        pass


def result_form(invoice_id, dialog_id, out_sum="1500.00", password2="pass2"):
    sign = hashlib.md5(f"{out_sum}:{invoice_id}:{password2}:Shp_dialog={dialog_id}".encode()).hexdigest()
    return {"OutSum": out_sum, "InvId": str(invoice_id), "Shp_dialog": str(dialog_id), "SignatureValue": sign.upper()}


def test_payment_url_signature_includes_dialog():
    url = robokassa.payment_url("demo-shop", "pass1", 1500, 42, "Консультация", 777)
    params = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    expected = hashlib.md5(b"demo-shop:1500.00:42:pass1:Shp_dialog=777").hexdigest()
    assert params["SignatureValue"] == expected
    assert params["Shp_dialog"] == "777"


def test_invoice_id_within_limit():
    assert 0 < robokassa.make_invoice_id(123456789, now=1_900_000_000) <= robokassa.MAX_INVOICE_ID


def test_result_with_wrong_signature_is_rejected():
    form = result_form(42, 777)
    form["SignatureValue"] = "0" * 32
    assert robokassa.verify_result(form, "pass2")["ok"] is False


def test_paid_invoice_moves_deal_and_sends_link_to_same_dialog(monkeypatch):
    calls = []
    monkeypatch.setattr(bf.amocrm.requests, "patch", lambda url, **kw: calls.append((url, kw["json"])) or Resp(status=200))
    monkeypatch.setattr(bf.calendly.requests, "post",
                        lambda url, **kw: Resp({"resource": {"booking_url": "https://calendly.com/d/abc-123"}}))
    store = {}
    created = bf.request_payment(CFG, store, dialog_id=777, lead_id=5, amount=1500, description="Консультация", now=1_900_000_000)
    answer = bf.on_payment_result(CFG, store, result_form(created["invoice_id"], 777))
    assert answer["http_answer"] == f"OK{created['invoice_id']}"
    assert answer["dialog_message"]["dialog_id"] == 777
    assert "utm_source=777" in answer["dialog_message"]["text"]
    assert calls == [("https://example.amocrm.ru/api/v4/leads/5", {"status_id": 111})]


def test_repeated_notification_is_ignored(monkeypatch):
    monkeypatch.setattr(bf.amocrm.requests, "patch", lambda url, **kw: Resp(status=200))
    monkeypatch.setattr(bf.calendly.requests, "post",
                        lambda url, **kw: Resp({"resource": {"booking_url": "https://calendly.com/d/x"}}))
    store = {}
    created = bf.request_payment(CFG, store, 777, 5, 1500, "Консультация", now=1_900_000_000)
    form = result_form(created["invoice_id"], 777)
    assert bf.on_payment_result(CFG, store, form)["dialog_message"] is not None
    assert bf.on_payment_result(CFG, store, form)["dialog_message"] is None


def test_calendly_booking_confirms_in_dialog(monkeypatch):
    moved = []
    monkeypatch.setattr(bf.amocrm.requests, "patch", lambda url, **kw: moved.append(kw["json"]) or Resp(status=200))
    store = {"42": {"dialog_id": 777, "lead_id": 5, "status": "paid"}}
    body = {"event": "invitee.created",
            "payload": {"tracking": {"utm_source": "777"},
                        "scheduled_event": {"start_time": "2099-06-15T07:00:00.000000Z", "name": "Консультация"}}}
    answer = bf.on_calendly_webhook(CFG, store, body)
    assert answer["dialog_message"]["dialog_id"] == 777
    assert moved == [{"status_id": 222}]
    assert store["42"]["status"] == "booked"


def test_opstate_paid():
    xml = ('<OperationStateResponse xmlns="http://merchant.roboxchange.com/WebService/">'
           '<Result><Code>0</Code></Result><State><Code>100</Code></State></OperationStateResponse>')
    assert robokassa.parse_state(xml) == {"result_code": "0", "state_code": "100", "paid": True}
