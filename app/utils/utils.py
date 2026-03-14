import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import aiofiles
import yaml


logger = logging.getLogger(__name__)


async def walk_through_files(start_folder: Path, handler: callable, *, max_concurrency: int = 64):
    sem = asyncio.Semaphore(max_concurrency)
    tasks = []

    async def wrapped(p: Path):
        async with sem:
            try:
                await handler(p)
            except Exception as e:
                logger.error("Handler failed for %s: %s", p, e)

    for root, _, files in os.walk(start_folder):
        for file in files:
            if file.endswith(".md"):
                full_path = Path(os.path.join(root, file))
                tasks.append(asyncio.create_task(wrapped(full_path)))

    # не отменяем всё из-за одной ошибки
    await asyncio.gather(*tasks, return_exceptions=True)


def is_item_true(val: Any) -> bool:
    return str(val.get("item", "")).lower() == "true"


async def is_item_container_dir(folder_path: Path) -> bool:
    warehouse_item_file = folder_path / f"{folder_path.name}.md"
    try:
        params = await return_file_params(warehouse_item_file)
        if is_item_true(params):
            logger.debug("%s - является местом хранения", folder_path)
            return True
        return False
    except FileNotFoundError:
        logger.debug("%s НЕ ЯВЛЯЕТСЯ МЕСТОМ ХРАНЕНИЯ!!!", folder_path)
        return False
    except Exception:
        raise


async def is_file_in_item_container(file_path: Path) -> bool:
    return await is_item_container_dir(file_path.parent)


async def build_item_path(file_path: Path, root: Path = Path("/data")) -> str:
    parts: list[str] = []
    current_dir = file_path.parent

    while current_dir != current_dir.parent:
        if current_dir == root:
            break

        if await is_item_container_dir(current_dir):
            parts.append(current_dir.name)

        current_dir = current_dir.parent

    parts.reverse()
    return str(Path(*parts)) if parts else ""


async def return_file_params(path: Path) -> dict[str, Any]:
    """Проверяет один .md файл на наличие yaml параметров. Возвращает параметры."""
    async with aiofiles.open(path, "r", encoding="utf-8") as f:
        lines = await f.readlines()

    # Ищем секцию параметров вида:
    # ---
    # item: true
    # ---
    if len(lines) < 3 or not lines[0].strip().startswith("---"):
        return {}

    params = []
    for line in lines[1:]:
        if line.strip().startswith("---"):
            break
        params.append(line)

    yaml_text = "".join(params)
    data = yaml.safe_load(yaml_text) or {}
    return data
