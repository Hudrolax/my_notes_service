# frontmatter_safe.py
# Асинхронное безопасное обновление YAML-фронтматтера Markdown-файлов.
# Требования безопасности:
# - Любые ошибки формата приводят к исключению, файл не меняется.
# - Запись выполняется только после полной валидации и сборки нового текста в памяти.
# - При ошибке записи выполняется попытка восстановления исходного содержимого.
# - Временных файлов не создаём.

from __future__ import annotations
from collections.abc import Callable
import logging
from pathlib import Path
from typing import Dict, Tuple

import aiofiles
from config import settings
import yaml


logger = logging.getLogger(__name__)


class FrontMatterError(Exception):
    """Ошибка при обработке YAML-фронтматтера."""


def _detect_newline(text: str) -> str:
    """Определяет стиль перевода строк исходного файла (\\r\\n или \\n)."""
    return "\r\n" if "\r\n" in text else "\n"


def _strip_bom(text: str) -> Tuple[str, bool]:
    """Убирает UTF-8 BOM в начале текста, если он есть, и возвращает (текст, был_ли_BOM)."""
    BOM = "\ufeff"
    if text.startswith(BOM):
        return text[len(BOM) :], True
    return text, False


def _find_frontmatter_bounds(text: str) -> Tuple[int, int, str]:
    """
    Возвращает кортеж (start_idx, end_idx, newline),
    где [start_idx:end_idx] — СОДЕРЖИМОЕ YAML (без строк с '---'),
    newline — стиль перевода строк.

    Условия:
      - Файл должен начинаться с строки '---' (ровно, без префиксов);
      - Закрывающая строка '---' должна быть на отдельной строке;
    В противном случае бросает FrontMatterError.
    """
    nl = _detect_newline(text)

    # Фронтматтер должен начинаться строго с '---' в первой строке
    if not text.startswith(f"---{nl}"):
        raise FrontMatterError("Файл не начинается с корректного фронтматтера '---'.")

    # Разбиваем на строки без сохранения разделителей
    lines = text.split(nl)  # lines[0] == '---' гарантировано условием выше
    for i in range(1, len(lines)):
        if lines[i] == "---":  # закрывающий разделитель найден
            # YAML — это строки между первой и второй '---'
            prefix_len = len(f"---{nl}")  # длина начала '---' + перевод строки
            yaml_part = nl.join(lines[1:i])  # содержимое YAML между разделителями
            start = prefix_len
            end = start + len(yaml_part)
            return start, end, nl

    raise FrontMatterError("Не найден закрывающий разделитель '---' у фронтматтера.")


def _find_frontmatter_parts(text: str) -> tuple[int, int, int, str]:
    """
    Возвращает (yaml_start, yaml_end, body_start, newline).
    body_start указывает на первый символ после закрывающей строки '---'.
    """
    yaml_start, yaml_end, nl = _find_frontmatter_bounds(text)
    lines = text.split(nl)

    for i in range(1, len(lines)):
        if lines[i] == "---":
            body_start = len(nl.join(lines[: i + 1]))
            if i + 1 < len(lines):
                body_start += len(nl)
            return yaml_start, yaml_end, body_start, nl

    raise FrontMatterError("Не найден закрывающий разделитель '---' у фронтматтера.")


async def _read_text_async(path: Path) -> str:
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        return await f.read()


async def _write_text_async(path: Path, data: str) -> None:
    async with aiofiles.open(path, "w", encoding="utf-8") as f:
        await f.write(data)


async def read_markdown_body_async(path: Path) -> tuple[str, str]:
    original = await _read_text_async(path)
    body_text, _ = _strip_bom(original)
    _, _, body_start, nl = _find_frontmatter_parts(body_text)
    return body_text[body_start:], nl


async def update_frontmatter_async(path: Path, params: Dict[str, object]) -> None:
    if settings.FAKE_FILE_WORKING:
        return await _fake_update_frontmatter_async(path, params)
    else:
        return await _update_frontmatter_async(path, params)


async def update_frontmatter_and_body_async(
    path: Path,
    params: Dict[str, object],
    body_transform: Callable[[str, str], str],
) -> None:
    if settings.FAKE_FILE_WORKING:
        return await _fake_update_frontmatter_and_body_async(path, params, body_transform)
    else:
        return await _update_frontmatter_and_body_async(path, params, body_transform)


async def _fake_update_frontmatter_async(path: Path, params: Dict[str, object]) -> None:
    logger.info(f'Фейковый вызов изменения файла для {path}')
    pass


async def _fake_update_frontmatter_and_body_async(
    path: Path,
    params: Dict[str, object],
    body_transform: Callable[[str, str], str],
) -> None:
    logger.info(f"Фейковый вызов изменения параметров и тела файла для {path}")
    pass


async def _update_frontmatter_async(path: Path, params: Dict[str, object]) -> None:
    """
    Безопасно ОБНОВЛЯЕТ YAML-параметры во фронтматтере MD-файла (асинхронно).
    Условия:
      - файл обязан НАЧИНАТЬСЯ строкой '---' и иметь закрывающую '---' на отдельной строке;
      - YAML между ними должен парситься в mapping (dict);
    В противном случае бросает FrontMatterError и файл не меняется.
    Временных файлов НЕ создаёт. При ошибке записи пытается восстановить исходник.

    :param path: Path к Markdown-файлу
    :param params: словарь параметров для обновления/добавления в фронтматтер
    """
    # 1) Чтение всего файла в память
    original = await _read_text_async(path)

    # 2) Учитываем BOM
    body_text, had_bom = _strip_bom(original)

    # 3) Находим границы YAML-блока (строго в начале и строго '---' / '---')
    start, end, nl = _find_frontmatter_bounds(body_text)

    # 4) Парсим YAML
    yaml_text = body_text[start:end]
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise FrontMatterError(f"Ошибка разбора YAML: {e}")
    if not isinstance(meta, dict):
        raise FrontMatterError("Фронтматтер должен быть YAML mapping (ключ-значение).")

    # 5) Обновляем ключи
    meta.update(params)

    # 6) Сериализуем YAML назад (с учётом юникода и порядка ключей)
    new_yaml = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    # Привести переводы строк к исходному стилю
    if nl != "\n":
        new_yaml = new_yaml.replace("\n", nl)

    # 7) Собираем новый текст, заменив только YAML-содержимое
    prefix = body_text[:start]
    suffix = body_text[end:]
    new_body = f"{prefix}{new_yaml}{suffix}"

    # Вернуть BOM, если он был
    final_text = ("\ufeff" + new_body) if had_bom else new_body

    # Если ничего не поменялось — вообще не трогаем файл
    if final_text == original:
        return

    # 8) Пишем обратно СТРОГО после всех проверок.
    #    При ошибке записи пробуем восстановить исходник и генерируем FrontMatterError.
    try:
        await _write_text_async(path, final_text)
        logger.debug("Обновил параметры в файле: %s", path)
    except Exception as write_err:
        logger.warning("Не удалось записать параметры в файл: %s (%s)", path, write_err)
        try:
            # попытка восстановления исходника
            await _write_text_async(path, original)
            logger.warning("Восстановил оригинал файла после ошибки записи: %s", path)
        except Exception as restore_err:
            logger.critical("Не удалось восстановить исходник файла: %s (%s)", path, restore_err)
        # Итоговое исключение наружу (файл либо восстановлен, либо нет — это в логах)
        raise FrontMatterError("Ошибка записи файла; предпринята попытка восстановления.") from write_err


async def _update_frontmatter_and_body_async(
    path: Path,
    params: Dict[str, object],
    body_transform: Callable[[str, str], str],
) -> None:
    """
    Безопасно обновляет YAML-параметры и тело Markdown-файла за одну запись.
    body_transform получает текущее тело файла и стиль перевода строк.
    """
    original = await _read_text_async(path)
    body_text, had_bom = _strip_bom(original)
    yaml_start, yaml_end, body_start, nl = _find_frontmatter_parts(body_text)

    yaml_text = body_text[yaml_start:yaml_end]
    try:
        meta = yaml.safe_load(yaml_text) or {}
    except yaml.YAMLError as e:
        raise FrontMatterError(f"Ошибка разбора YAML: {e}")
    if not isinstance(meta, dict):
        raise FrontMatterError("Фронтматтер должен быть YAML mapping (ключ-значение).")

    meta.update(params)

    new_yaml = yaml.safe_dump(meta, sort_keys=False, allow_unicode=True)
    if nl != "\n":
        new_yaml = new_yaml.replace("\n", nl)

    current_body = body_text[body_start:]
    new_markdown_body = body_transform(current_body, nl)

    prefix = body_text[:yaml_start]
    between_yaml_and_body = body_text[yaml_end:body_start]
    new_body = f"{prefix}{new_yaml}{between_yaml_and_body}{new_markdown_body}"
    final_text = ("\ufeff" + new_body) if had_bom else new_body

    if final_text == original:
        return

    try:
        await _write_text_async(path, final_text)
        logger.debug("Обновил параметры и тело файла: %s", path)
    except Exception as write_err:
        logger.warning("Не удалось записать параметры и тело файла: %s (%s)", path, write_err)
        try:
            await _write_text_async(path, original)
            logger.warning("Восстановил оригинал файла после ошибки записи: %s", path)
        except Exception as restore_err:
            logger.critical("Не удалось восстановить исходник файла: %s (%s)", path, restore_err)
        raise FrontMatterError("Ошибка записи файла; предпринята попытка восстановления.") from write_err
