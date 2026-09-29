"""Сценарий «оплатить и записаться» для ИИ-агента.

1. Агент показывает ближайшие слоты консультанта (Calendly).
2. Клиент согласен: агент выдаёт ссылку на оплату (Robokassa, ID диалога в Shp_dialog).
3. Robokassa присылает уведомление на ResultURL: сверяем подпись, двигаем сделку
   в amoCRM на этап «Оплачено» и отправляем в тот же диалог одноразовую ссылку на запись.
4. Calendly присылает вебхук о записи: двигаем сделку на этап «Записан»
   и отправляем клиенту подтверждение.

Хранилище store связывает номер счёта со сделкой. В рабочем проекте это
переменные диалога платформы; здесь подойдёт любой объект с интерфейсом dict.
"""
import datetime

from flow import amocrm, calendly, robokassa


def offer_slots(cfg, now=None, limit=3):
    start = now or datetime.datetime.now(datetime.timezone.utc)
    slots = calendly.available_slots(cfg["calendly_token"], cfg["event_type_uri"], start)
    if not slots:
        return {"status": "no_slots", "instruction": "Свободного времени на неделе нет. Предложи оставить контакт."}
    return {"status": "ok", "slots_utc": slots[:limit],
            "instruction": "Назови клиенту эти варианты в его часовом поясе. Другого времени не предлагай."}


def request_payment(cfg, store, dialog_id, lead_id, amount, description, now=None):
    invoice_id = robokassa.make_invoice_id(dialog_id, now)
    store[str(invoice_id)] = {"dialog_id": int(dialog_id), "lead_id": int(lead_id), "status": "waiting_payment"}
    url = robokassa.payment_url(cfg["merchant_login"], cfg["password1"], amount, invoice_id,
                                description, dialog_id, is_test=cfg.get("is_test", False))
    return {"status": "ok", "invoice_id": invoice_id, "payment_url": url,
            "instruction": "Отправь клиенту ссылку на предоплату. Не подтверждай запись до оплаты."}


def on_payment_result(cfg, store, form):
    """Обработчик ResultURL. Возвращает ответ для Robokassa и сообщение для диалога."""
    checked = robokassa.verify_result(form, cfg["password2"])
    if not checked["ok"]:
        return {"http_answer": checked["answer"], "dialog_message": None}

    order = store.get(checked["invoice_id"])
    if order is None or order["dialog_id"] != checked["dialog_id"]:
        return {"http_answer": "unknown invoice", "dialog_message": None}
    if order["status"] != "waiting_payment":
        return {"http_answer": checked["answer"], "dialog_message": None}  # повтор уведомления

    order["status"] = "paid"
    amocrm.move_lead(cfg["amo_api_base"], cfg["amo_token"], order["lead_id"], cfg["status_paid"])
    link = calendly.single_use_link(cfg["calendly_token"], cfg["event_type_uri"], order["dialog_id"])
    return {
        "http_answer": checked["answer"],
        "dialog_message": {
            "dialog_id": order["dialog_id"],
            "text": f"Оплата получена. Выберите удобное время по ссылке: {link}",
        },
    }


def on_calendly_webhook(cfg, store, body):
    event = calendly.parse_webhook(body)
    if not event["ok"] or event["event"] != "invitee.created":
        return {"dialog_message": None}
    order = next((o for o in store.values() if o["dialog_id"] == event["dialog_id"]), None)
    if order is not None:
        order["status"] = "booked"
        amocrm.move_lead(cfg["amo_api_base"], cfg["amo_token"], order["lead_id"], cfg["status_booked"])
    return {"dialog_message": {"dialog_id": event["dialog_id"],
                               "text": f"Вы записаны на консультацию: {event['start_time']}."}}
