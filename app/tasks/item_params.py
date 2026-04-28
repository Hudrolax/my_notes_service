# Модуль находит все items, проверяет у них свойство path и исправляет его, если он не правильный.
# path - это путь, где лежит вещь

import asyncio
import logging
import os
import re
import string
import time
from datetime import datetime
from pathlib import Path

from config import settings
from utils.file_writing import read_markdown_body_async, update_frontmatter_and_body_async, update_frontmatter_async
from utils.utils import (
    build_item_path,
    is_file_in_item_container,
    is_item_container_dir,
    is_item_true,
    return_file_params,
    walk_through_files,
)

logger = logging.getLogger(__name__)

STORAGE_NOTE_LINK_RE = re.compile(r"^\[[^\]\n]+\]\([^\n)]+\.md\)$")
LINK_TARGET_SAFE_ASCII = frozenset(string.ascii_letters + string.digits + "-._~")


def is_file_too_young(path: Path, cooldown_seconds: int = 300) -> bool:
    """
    Проверяет, прошло ли достаточно времени с момента последнего изменения (или создания) файла.
    Возвращает True, если файл "слишком молодой" (cooldown еще не прошел).
    """
    try:
        stat = path.stat()
        # Выбираем самое позднее время из создания и модификации
        last_action_time = max(stat.st_mtime, getattr(stat, "st_ctime", 0))
        if time.time() - last_action_time < cooldown_seconds:
            return True
    except OSError:
        # Если не удалось получить статистику, считаем файл старым/безопасным или несуществующим
        pass
    return False


def encode_storage_note_link_target(filename: str) -> str:
    """
    Кодирует символы, которые ломают markdown-ссылку, но оставляет читаемые Unicode-имена.
    """
    encoded = []
    for char in filename:
        if char.isascii() and char not in LINK_TARGET_SAFE_ASCII:
            encoded.extend(f"%{byte:02X}" for byte in char.encode("utf-8"))
        else:
            encoded.append(char)
    return "".join(encoded)


def build_storage_note_link(item_path: str) -> str:
    storage_note_name = Path(item_path).name
    storage_note_filename = f"{storage_note_name}.md"
    encoded_filename = encode_storage_note_link_target(storage_note_filename)
    return f"[{storage_note_name}]({encoded_filename})"


def is_storage_note_file(path: Path) -> bool:
    return path.name == f"{path.parent.name}.md"


def ensure_storage_note_link(markdown_body: str, expected_link: str, newline: str = "\n") -> str:
    first_line, separator, rest = markdown_body.partition(newline)

    if first_line == expected_link:
        return markdown_body

    if STORAGE_NOTE_LINK_RE.fullmatch(first_line):
        return f"{expected_link}{separator}{rest}" if separator else expected_link

    if markdown_body:
        return f"{expected_link}{newline}{markdown_body}"

    return f"{expected_link}{newline}"


async def build_storage_contents_links(folder: Path) -> list[tuple[str, str]]:
    """
    Возвращает список пар (текст ссылки, target) для непосредственного содержимого
    папки места хранения. Сначала идут обычные .md-файлы первого уровня, затем
    заметки мест хранения подпапок. Внутри групп — сортировка по имени без учёта регистра.
    """
    self_filename = f"{folder.name}.md"
    try:
        entries = await asyncio.to_thread(lambda: list(folder.iterdir()))
    except OSError:
        return []

    regular_stems: list[str] = []
    container_names: list[str] = []

    for entry in entries:
        try:
            if entry.is_file():
                if entry.name == self_filename:
                    continue
                if entry.suffix == ".md":
                    regular_stems.append(entry.stem)
            elif entry.is_dir():
                if entry.name == ".trash":
                    continue
                if await is_item_container_dir(entry):
                    container_names.append(entry.name)
        except OSError:
            continue

    regular_stems.sort(key=str.lower)
    container_names.sort(key=str.lower)

    links: list[tuple[str, str]] = []
    for stem in regular_stems:
        target = encode_storage_note_link_target(f"{stem}.md")
        links.append((stem, target))
    for name in container_names:
        encoded_dir = encode_storage_note_link_target(name)
        encoded_file = encode_storage_note_link_target(f"{name}.md")
        links.append((name, f"{encoded_dir}/{encoded_file}"))

    return links


def parse_managed_storage_list(body: str, newline: str) -> tuple[int, int]:
    """
    Находит границы управляемого блока ссылок в начале тела заметки.
    Возвращает (block_end, manual_start), где
    block_end — индекс сразу после последней строки управляемого блока,
    manual_start — индекс начала ручного контента (после возможной пустой строки-разделителя).
    """
    if not body:
        return 0, 0

    pos = 0
    block_end = 0
    while pos < len(body):
        nl_idx = body.find(newline, pos)
        if nl_idx == -1:
            line = body[pos:]
            line_end = len(body)
        else:
            line = body[pos:nl_idx]
            line_end = nl_idx + len(newline)

        if STORAGE_NOTE_LINK_RE.fullmatch(line):
            block_end = line_end
            pos = line_end
        else:
            break

    if block_end == 0:
        return 0, 0

    if pos < len(body):
        nl_idx = body.find(newline, pos)
        if nl_idx == -1:
            return block_end, block_end
        if body[pos:nl_idx] == "":
            return block_end, nl_idx + len(newline)

    return block_end, block_end


def render_managed_storage_list(links: list[tuple[str, str]], newline: str) -> str:
    if not links:
        return ""
    return newline.join(f"[{text}]({target})" for text, target in links) + newline


def replace_managed_storage_list(
    body: str, links: list[tuple[str, str]], newline: str
) -> str:
    _, manual_start = parse_managed_storage_list(body, newline)
    manual_content = body[manual_start:]
    rendered = render_managed_storage_list(links, newline)

    if not rendered:
        return manual_content
    if not manual_content:
        return rendered
    return f"{rendered}{newline}{manual_content}"


async def ensure_storage_contents_list(path: Path):
    """
    Поддерживает в заметке места хранения управляемый список ссылок на
    непосредственное содержимое папки. Самой заметки в списке нет.
    """
    if ".trash" in path.parts:
        return

    if not is_storage_note_file(path):
        return

    if is_file_too_young(path):
        return

    try:
        params = await return_file_params(path)
        if not is_item_true(params):
            return

        expected_links = await build_storage_contents_links(path.parent)
        current_body, newline = await read_markdown_body_async(path)
        new_body = replace_managed_storage_list(current_body, expected_links, newline)
        if new_body == current_body:
            return

        logger.info("Обновляю список содержимого места хранения для %s", path)
        await update_frontmatter_and_body_async(
            path,
            params,
            lambda body, nl: replace_managed_storage_list(body, expected_links, nl),
        )

    except Exception as e:
        logger.error(e)
        raise


async def ensure_correct_path(path: Path):
    """
    Проверяет параметр 'path' и первую ссылку на заметку места хранения в заметке item.
    Если они отсутствуют или отличаются от ожидаемых — обновляет файл.
    """
    # Пропускаем .trash
    if ".trash" in path.parts:
        return

    # Проверка на cooldown (5 минут)
    if is_file_too_young(path):
        return

    # Пропускаем файл, если он не в контейнере с вещами
    if not await is_file_in_item_container(path):
        return

    try:
        params = await return_file_params(path)
        if not is_item_true(params):
            # проверяем путь только для вещей
            return

        current_path = params.get("path")
        actual_path = await build_item_path(path)
        if not actual_path:
            return

        changes = []

        if current_path != actual_path:
            params["path"] = actual_path
            changes.append("path")

        should_update_storage_link = not is_storage_note_file(path)
        if should_update_storage_link:
            expected_link = build_storage_note_link(actual_path)
            current_body, newline = await read_markdown_body_async(path)
            if ensure_storage_note_link(current_body, expected_link, newline) != current_body:
                changes.append("ссылку на заметку места хранения")

        if not changes:
            return

        def update_storage_link(markdown_body: str, newline: str) -> str:
            if not should_update_storage_link:
                return markdown_body
            return ensure_storage_note_link(markdown_body, expected_link, newline)

        logger.info("Обновляю %s для %s", ", ".join(changes), path)
        await update_frontmatter_and_body_async(path, params, update_storage_link)

    except Exception as e:
        logger.error(e)
        raise


async def ensure_correct_dates(path: Path) -> None:
    """
    Проверяет параметры 'creation_date' и 'modification_date' во фронтматтере Markdown-файла.
    Если их нет или они отличаются от фактических метаданных файла — обновляет файл.
    При обновлении сохраняем исходные atime/mtime, чтобы избежать бессмысленных перезаписей.
    """
    # Пропускаем .trash
    if ".trash" in path.parts:
        return

    # Проверка на cooldown здесь тоже не помешает, если вдруг включим эту функцию
    if is_file_too_young(path):
        return

    try:
        # --- Получаем метаданные файла ---
        stat = await asyncio.to_thread(path.stat)
        atime = stat.st_atime
        creation_ts = getattr(stat, "st_ctime", stat.st_mtime)
        modification_ts = stat.st_mtime

        # Приводим к требуемому формату: 10.10.2025 22:27:02
        fmt = "%d.%m.%Y %H:%M:%S"
        creation_str = datetime.fromtimestamp(creation_ts).strftime(fmt)
        modification_str = datetime.fromtimestamp(modification_ts).strftime(fmt)

        # --- Читаем текущие параметры ---
        params = await return_file_params(path)
        current_creation = params.get("creation_date")
        current_modification = params.get("modification_date")

        # --- Проверяем необходимость обновления ---
        need_update = False

        if current_creation != creation_str:
            params["creation_date"] = creation_str
            need_update = True

        if current_modification != modification_str:
            params["modification_date"] = modification_str
            need_update = True

        # --- Обновляем фронтматтер при необходимости ---
        if need_update:
            logger.info("Обновляю даты в файле: %s", path)
            await update_frontmatter_async(path, params)

            # ВОССТАНАВЛИВАЕМ исходные времена, чтобы не зациклиться на каждом проходе
            # (mtime после записи увеличился, вернём его к modification_ts)
            await asyncio.to_thread(os.utime, path, (atime, modification_ts))
        else:
            logger.debug("Даты в файле актуальны: %s", path)

    except Exception as e:
        logger.error("Ошибка при проверке дат в файле %s: %s", path, e)
        raise


async def remove_unnamed_files(start_folder: Path):
    """
    Удаляет все .md файлы, название которых начинается с 'Без названия',
    кроме тех, что находятся:
      — в каталоге '.trash' (на любом уровне);
      — в корневом каталоге start_folder.
    """
    deleted = 0
    for root, _, files in os.walk(start_folder):
        root_path = Path(root)

        # Пропускаем .trash и сам корень
        if ".trash" in root_path.parts or root_path == start_folder:
            continue

        for f in files:
            if f.lower().startswith("без названия") and f.endswith(".md"):
                full_path = root_path / f
                
                # Проверка на cooldown (5 минут)
                if is_file_too_young(full_path):
                    continue

                try:
                    if settings.FAKE_FILE_WORKING:
                        logger.info(f"Фейковое удаление файла {full_path}")
                    else:
                        await asyncio.to_thread(full_path.unlink)
                        logger.info(f"Удалён файл: {full_path}")
                        deleted += 1
                except Exception as e:
                    logger.error(f"Не удалось удалить {full_path}: {e}")

    if deleted:
        logger.info(f"Удалено {deleted} файлов без названия.")
    else:
        logger.debug("Файлов без названия не найдено.")


async def make_actual_item_params():
    logger.info("Запустил задачу корректировке параметров для заметок")
    while True:
        await walk_through_files(Path("/data"), handler=ensure_correct_path)
        await walk_through_files(Path("/data"), handler=ensure_storage_contents_list)
        # await walk_through_files(Path("/data"), handler=ensure_correct_dates)
        await remove_unnamed_files(Path("/data"))

        await asyncio.sleep(60)
