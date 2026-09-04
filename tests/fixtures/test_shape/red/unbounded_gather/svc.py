import asyncio


async def run(items):
    return await asyncio.gather(*(work(i) for i in items))
