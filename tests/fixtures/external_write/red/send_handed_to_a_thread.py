"""RED -- readback.  The send is real; nothing keeps what it returned.

The ``asyncio.to_thread(client.send_message, ...)`` form found in the
reference adopter's chat poller.  The callee is passed by reference
rather than called, which a ``\\.send_message\\s*\\(`` regex misses entirely --
matching over the AST is what makes it visible.

The statement discards its value, and ``to_thread`` stores nothing, so the
returned ``Message`` -- with the id every later edit needs -- is gone.  The
scope reads nothing back either.
"""

import asyncio


class Poller:
    def __init__(self, client) -> None:
        self.client = client

    async def send_final_text(self, *, chat_id: int, message_id, chunks) -> None:
        for index, chunk in enumerate(chunks):
            await asyncio.to_thread(
                self.client.send_message,
                chat_id=chat_id,
                text=chunk,
                reply_to_message_id=message_id if index == 0 else None,
            )
