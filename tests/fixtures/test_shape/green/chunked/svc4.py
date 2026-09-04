import asyncio


async def run(items):
    out = []
    for chunk in chunks(items, 10):
        out += await asyncio.gather(*(work(i) for i in chunk))
    return out
