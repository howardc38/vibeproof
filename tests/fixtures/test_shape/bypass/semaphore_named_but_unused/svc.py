import asyncio


async def run(items):
    # Semaphore was considered here
    return await asyncio.gather(*(work(i) for i in items))
