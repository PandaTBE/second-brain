"""Bounded adapter for Model Studio's documented native Qwen model catalog."""

import re

from .errors import AppError

CATALOG_BASE = "https://dashscope-intl.aliyuncs.com/api/v1"


def supports_non_thinking(model_id):
    aliases = {"qwen-plus", "qwen-plus-latest", "qwen-flash", "qwen-flash-latest"}
    return model_id in aliases or bool(re.fullmatch(
        r"qwen3\.(?:[567]-(?:plus|flash|max)|8-(?:flash|max))", model_id,
    ))


def _model(row):
    if not isinstance(row, dict):
        return None
    identifier = row.get("model")
    capabilities = row.get("capabilities")
    if (not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/-]{1,256}", identifier)
            or not isinstance(capabilities, list) or "TG" not in capabilities):
        return None
    inference = row.get("inference_metadata")
    modalities = inference.get("response_modality") if isinstance(inference, dict) else None
    if isinstance(modalities, list) and not any(
        isinstance(value, str) and value.lower() == "text" for value in modalities
    ):
        return None
    information = row.get("model_info")
    context = information.get("context_window") if isinstance(information, dict) else None
    name = row.get("name")
    return {
        "id": identifier, "name": name[:512] if isinstance(name, str) and name else identifier,
        "context_length": context if type(context) is int and context > 0 else None,
        # The catalog's price objects do not establish a currency. Unknown is not free.
        "input_price": None, "output_price": None,
    }


def fetch_catalog(request, token):
    models = ()
    seen = frozenset()
    truncated = False
    for page in range(1, 6):
        path = "/models?providers=qwen&capabilities=TG&page_size=100&page_no=" + str(page)
        payload = request(CATALOG_BASE, path, token)
        output = payload.get("output") if isinstance(payload, dict) else None
        rows = output.get("models") if isinstance(output, dict) else None
        if not isinstance(rows, list):
            raise AppError("invalid_catalog", "Qwen вернул неподдерживаемый каталог. "
                           "Укажите точный ID модели вручную.", 502)
        for row in rows[:100]:
            item = _model(row)
            if item is not None and item["id"] not in seen:
                models = (*models, item)
                seen = seen | {item["id"]}
        if len(rows) < 100:
            break
        truncated = page == 5
    return {"models": list(models), "truncated": truncated,
            "message": "Каталог Qwen показан частично; точный ID можно указать вручную."
            if truncated else None}
