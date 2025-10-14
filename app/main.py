import asyncio
import logging

from config import settings
from tasks import make_actual_item_params

logger = logging.getLogger(__name__)

async def main() -> None:
    logger.info('my_notes_service запущен')
    await asyncio.gather(make_actual_item_params())


if __name__ == "__main__":
    asyncio.run(main())
