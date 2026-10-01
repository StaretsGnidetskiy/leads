# client.py
"""
Универсальный клиент для отправки VK-ссылок на сервер резолва.

Работает:
  - как обычный Python-скрипт (python client.py),
  - как .exe, собранный PyInstaller (Windows),
  - как .app, собранный PyInstaller (macOS).

Файлы (config.json, filtered_export.csv, output/) ищутся:
  1) рядом с приложением (.exe / .app / скрипт),
  2) если не найдено — в ~/CatCutClient/.

Выходной CSV сохраняется в <рядом с приложением>/output/ (или ~/CatCutClient/output/).
"""

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import requests


# ---------------------------------------------------------------------------
# Настройки
# ---------------------------------------------------------------------------
API_URL = "http://api.catcutweb.ru/upload"
REQUEST_TIMEOUT = 120  # секунд; резолв 100+ ссылок может занять время


# ---------------------------------------------------------------------------
# Определение рабочей папки
# ---------------------------------------------------------------------------
def get_app_dir() -> Path:
    """
    Возвращает папку, где пользователь кладёт config.json и filtered_export.csv.

    - Обычный скрипт:       папка, где лежит client.py
    - PyInstaller на Win:   папка, где лежит .exe
    - PyInstaller на macOS: папка, где лежит .app (на уровень выше бандла)
    """
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        # macOS: .../CatCutClient.app/Contents/MacOS/CatCutClient
        # → три уровня вверх дают .../CatCutClient.app
        # → четыре — папку, где лежит .app
        if sys.platform == "darwin" and ".app/Contents/MacOS" in exe.as_posix():
            return exe.parents[3]
        return exe.parent

    # Обычный запуск .py
    return Path(__file__).resolve().parent


def get_home_dir() -> Path:
    """Fallback-папка в домашней директории пользователя."""
    d = Path.home() / "CatCutClient"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _find_file(name: str) -> Path | None:
    """
    Ищет файл сначала рядом с приложением, затем в ~/CatCutClient/.
    Возвращает Path или None.
    """
    candidates = [
        get_app_dir() / name,
        get_home_dir() / name,
    ]
    for c in candidates:
        if c.exists():
            return c
    return None


# ---------------------------------------------------------------------------
# Конфиг
# ---------------------------------------------------------------------------
def load_config() -> dict:
    path = _find_file("config.json")
    if path is None:
        app_dir = get_app_dir()
        home_dir = get_home_dir()
        raise SystemExit(
            "Не найден config.json.\n"
            "Положите его в одну из папок:\n"
            f"  1) {app_dir}\n"
            f"  2) {home_dir}\n"
            "\nПример config.json:\n"
            '{\n'
            '  "user": {\n'
            '    "name": "Иван",\n'
            '    "API_key": "ваш_ключ"\n'
            '  }\n'
            '}'
        )

    try:
        with path.open("r", encoding="utf-8") as f:
            config = json.load(f)
    except json.JSONDecodeError as e:
        raise SystemExit(f"Ошибка: невалидный JSON в '{path}': {e}")

    user = config.get("user")
    if not isinstance(user, dict):
        raise SystemExit(f"Ошибка: в '{path}' отсутствует объект 'user'.")
    if not user.get("name") or not user.get("API_key"):
        raise SystemExit(
            f"Ошибка: в '{path}' нужны непустые 'user.name' и 'user.API_key'."
        )
    return config


# ---------------------------------------------------------------------------
# Чтение входного CSV
# ---------------------------------------------------------------------------
def read_csv_links() -> list[str]:
    path = _find_file("filtered_export.csv")
    if path is None:
        app_dir = get_app_dir()
        home_dir = get_home_dir()
        raise SystemExit(
            "Не найден filtered_export.csv.\n"
            "Положите его в одну из папок:\n"
            f"  1) {app_dir}\n"
            f"  2) {home_dir}\n"
            "В файле должна быть колонка 'vk' или 'VK'."
        )

    links: list[str] = []
    try:
        with path.open("r", encoding="utf-8-sig", newline="") as f:
            sample = f.read(4096)
            f.seek(0)
            try:
                delimiter = csv.Sniffer().sniff(sample, delimiters=",;\t").delimiter
            except csv.Error:
                delimiter = ";"

            reader = csv.DictReader(f, delimiter=delimiter)
            if reader.fieldnames is None:
                raise SystemExit(f"Ошибка: файл '{path}' пуст или без заголовков.")

            vk_column = None
            for name in reader.fieldnames:
                if name and name.strip().lstrip("\ufeff").lower() == "vk":
                    vk_column = name
                    break

            if vk_column is None:
                raise SystemExit(
                    f"Ошибка: в '{path}' нет колонки 'vk'/'VK'. "
                    f"Найдены: {reader.fieldnames}"
                )

            for row in reader:
                value = (row.get(vk_column) or "").strip()
                if value:
                    links.append(value)
    except FileNotFoundError:
        raise SystemExit(f"Ошибка: файл '{path}' не найден.")

    return links


# ---------------------------------------------------------------------------
# Отправка на сервер
# ---------------------------------------------------------------------------
def send_to_server(
    config: dict,
    links: list[str],
    url: str = API_URL,
    timeout: int = REQUEST_TIMEOUT,
) -> requests.Response | None:
    if not links:
        print("Список ссылок пуст — отправка отменена.")
        return None

    payload = {"user": config["user"], "links": links}

    try:
        response = requests.post(url, json=payload, timeout=timeout)
        response.raise_for_status()
        return response
    except requests.exceptions.ConnectionError:
        print(f"Ошибка: сервер {url} недоступен.")
    except requests.exceptions.Timeout:
        print(f"Ошибка: превышен таймаут ({timeout} сек).")
    except requests.exceptions.HTTPError as e:
        print(f"HTTP {e.response.status_code}: {e.response.text}")
    except requests.exceptions.RequestException as e:
        print(f"Ошибка запроса: {e}")
    return None


# ---------------------------------------------------------------------------
# Сохранение ответа
# ---------------------------------------------------------------------------
def _filename_from_response(response: requests.Response) -> str:
    cd = response.headers.get("Content-Disposition", "")
    if "filename=" in cd:
        part = cd.split("filename=", 1)[1].strip()
        if part.startswith('"') and part.endswith('"'):
            part = part[1:-1]
        elif part.startswith("'") and part.endswith("'"):
            part = part[1:-1]
        part = Path(part).name  # защита от path traversal
        if part:
            return part
    return f"leads_{datetime.now():%Y-%m-%d_%H-%M-%S}.csv"


def save_csv_response(response: requests.Response) -> Path:
    # output/ рядом с приложением, если app_dir доступен на запись,
    # иначе — в ~/CatCutClient/output/
    app_dir = get_app_dir()
    try:
        out_dir = app_dir / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        # проверка на запись
        test = out_dir / ".write_test"
        test.touch()
        test.unlink()
    except OSError:
        out_dir = get_home_dir() / "output"
        out_dir.mkdir(parents=True, exist_ok=True)

    out_path = out_dir / _filename_from_response(response)
    if out_path.exists():
        stem, suffix = out_path.stem, out_path.suffix
        i = 1
        while True:
            candidate = out_dir / f"{stem}_{i}{suffix}"
            if not candidate.exists():
                out_path = candidate
                break
            i += 1

    with out_path.open("wb") as f:
        f.write(response.content)

    return out_path


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------
def main() -> None:
    print(f"Рабочая папка: {get_app_dir()}")

    config = load_config()
    links = read_csv_links()
    print(f"Загружено ссылок из CSV: {len(links)}")

    response = send_to_server(config, links)
    if response is None:
        return

    print("\nОтвет сервера:")
    print(f"  всего на входе : {response.headers.get('X-Total-Input', '?')}")
    print(f"  резолвнуто     : {response.headers.get('X-Resolved', '?')}")
    print(f"  в CSV          : {response.headers.get('X-Exported', '?')}")

    try:
        out_path = save_csv_response(response)
    except OSError as e:
        print(f"Ошибка сохранения CSV: {e}")
        return

    print(f"\nCSV сохранён: {out_path.resolve()}")
    input('''
    Нажмите enter, чтобы закрыть''')





if __name__ == "__main__":
    main()