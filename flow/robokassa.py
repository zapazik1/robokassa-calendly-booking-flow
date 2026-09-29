"""Robokassa: ссылка на оплату, проверка уведомления об оплате, запрос статуса.

ID диалога агента передаётся в пользовательском параметре Shp_dialog.
Robokassa возвращает его в уведомлении на ResultURL, поэтому ответ об оплате
попадает ровно в тот диалог, где клиент нажал «Оплатить».
"""
import hashlib
import time
import urllib.parse
import xml.etree.ElementTree as ET

import requests

PAYMENT_URL = "https://auth.robokassa.ru/Merchant/Index.aspx"
OPSTATE_URL = "https://auth.robokassa.ru/Merchant/WebService/Service.asmx/OpStateExt"
MAX_INVOICE_ID = 2_147_483_647   # InvId в Robokassa: целое не больше 2^31 - 1
PAID_STATE = "100"               # StateCode 100: платёж проведён, деньги зачислены


def md5(*parts):
    return hashlib.md5(":".join(str(p) for p in parts).encode("utf-8")).hexdigest()


def shp_part(shp):
    """Пользовательские параметры входят в подпись в алфавитном порядке: Shp_a=1:Shp_b=2."""
    return [f"{key}={shp[key]}" for key in sorted(shp)]


def make_invoice_id(dialog_id, now=None):
    """Уникальный InvId: секунды от эпохи и хвост ID диалога, в пределах лимита Robokassa."""
    seconds = int(now if now is not None else time.time())
    value = (seconds % 1_000_000) * 1000 + int(dialog_id) % 1000
    return value % MAX_INVOICE_ID or 1


def payment_url(merchant_login, password1, out_sum, invoice_id, description, dialog_id, is_test=False):
    """Ссылка на оплату. Подпись: MerchantLogin:OutSum:InvId:Password#1:Shp_..."""
    amount = f"{float(out_sum):.2f}"
    shp = {"Shp_dialog": str(dialog_id)}
    signature = md5(merchant_login, amount, invoice_id, password1, *shp_part(shp))
    params = {
        "MerchantLogin": merchant_login,
        "OutSum": amount,
        "InvId": invoice_id,
        "Description": description,
        "SignatureValue": signature,
        **shp,
    }
    if is_test:
        params["IsTest"] = 1
    return PAYMENT_URL + "?" + urllib.parse.urlencode(params)


def verify_result(form, password2):
    """Проверяет уведомление на ResultURL. Возвращает {"ok", "invoice_id", "dialog_id", "answer"}.

    Подпись уведомления: OutSum:InvId:Password#2:Shp_... Регистр подписи не важен.
    answer — строка, которую нужно вернуть Robokassa, чтобы она перестала повторять уведомление.
    """
    shp = {key: value for key, value in form.items() if key.startswith("Shp_")}
    expected = md5(form.get("OutSum", ""), form.get("InvId", ""), password2, *shp_part(shp))
    received = str(form.get("SignatureValue", "")).lower()
    if received != expected:
        return {"ok": False, "invoice_id": None, "dialog_id": None, "answer": "bad sign"}
    invoice_id = str(form["InvId"])
    return {
        "ok": True,
        "invoice_id": invoice_id,
        "dialog_id": int(shp["Shp_dialog"]) if shp.get("Shp_dialog", "").isdigit() else None,
        "answer": f"OK{invoice_id}",
    }


def payment_state(merchant_login, password2, invoice_id):
    """Статус оплаты через XML API: запасной путь, если уведомление не пришло."""
    response = requests.get(OPSTATE_URL, params={
        "MerchantLogin": merchant_login,
        "InvoiceID": invoice_id,
        "Signature": md5(merchant_login, invoice_id, password2),
    }, timeout=15)
    response.raise_for_status()
    return parse_state(response.text)


def parse_state(xml_text):
    """Коды из ответа OpStateExt: Result/Code (ответ API) и State/Code (состояние платежа)."""
    root = ET.fromstring(xml_text)
    codes = {}
    for parent in root.iter():
        parent_tag = parent.tag.rsplit("}", 1)[-1]
        if parent_tag not in ("Result", "State"):
            continue
        for child in parent:
            if child.tag.rsplit("}", 1)[-1] == "Code":
                codes[parent_tag] = (child.text or "").strip()
    state = codes.get("State")
    return {"result_code": codes.get("Result"), "state_code": state, "paid": state == PAID_STATE}
