"""amoCRM: перевод сделки на этап воронки."""
import requests


def move_lead(api_base, token, lead_id, status_id, pipeline_id=None):
    body = {"status_id": int(status_id)}
    if pipeline_id is not None:
        body["pipeline_id"] = int(pipeline_id)
    response = requests.patch(
        f"{api_base.rstrip('/')}/api/v4/leads/{int(lead_id)}",
        headers={"Authorization": f"Bearer {token}"},
        json=body,
        timeout=15,
    )
    return response.status_code in (200, 204)
