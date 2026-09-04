import asyncio


async def run(items):
    sem = asyncio.Semaphore(8)
    return await asyncio.gather(*(work(i) for i in items))
