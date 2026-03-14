# Модуль находит все items, проверяет у них свойство path и исправляет его, если он не правильный.
# path - это путь, где лежит вещь

import asyncio
import logging
import os
import time
from datetime import datetime
from pathlib import Path

from config import settings
from utils.file_writing import update_frontmatter_async
from utils.utils import build_item_path, is_file_in_item_container, is_item_true, return_file_params, walk_through_files

logger = logging.getLogger(__name__)


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


async def ensure_correct_path(path: Path):
    """
    Проверяет параметр 'path' в заметке item.
    Если его нет или он отличается от expected_path — обновляет файл.
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

        if current_path != actual_path:
            params["path"] = actual_path
            logger.info(f"Обновляю path для {path}")
            await update_frontmatter_async(path, params)

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
        # await walk_through_files(Path("/data"), handler=ensure_correct_dates)
        await remove_unnamed_files(Path("/data"))

        await asyncio.sleep(60)
