import os

import pytest_asyncio
from dotenv import load_dotenv

from kapusta import AlchemyCRUD, Kapusta

load_dotenv()


@pytest_asyncio.fixture(scope='function')
async def kapusta() -> Kapusta:
    return Kapusta(
        crud=AlchemyCRUD(os.getenv('DB_URL'))  # type: ignore
    )
