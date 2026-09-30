"""Parse comma-separated multi-select query parameters."""

def csv_texts(*values) -> list[str]:
    items: list[str] = []
    for value in values:
        if value is None or value == "":
            continue
        parts = value if isinstance(value, (list, tuple)) else str(value).split(",")
        for part in parts:
            text = str(part).strip()
            if text and text not in items:
                items.append(text)
    return items


def csv_ints(*values) -> list[int]:
    result: list[int] = []
    for text in csv_texts(*values):
        try:
            number = int(text)
        except (TypeError, ValueError):
            continue
        if number not in result:
            result.append(number)
    return result
