import asyncio


async def run(items):
    sem = asyncio.Semaphore(8)
    async def one(i):
        async with sem:
            return await work(i)
    return await asyncio.gather(*(one(i) for i in items))
