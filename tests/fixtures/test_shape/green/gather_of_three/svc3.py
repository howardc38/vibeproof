import asyncio


async def run():
    return await asyncio.gather(a(), b(), c())
