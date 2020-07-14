import asyncio
import functools
import logging

from .. import constants, protocol
from . import utils

logger = logging.getLogger(__name__)


class AsyncioServer:
    def __init__(self,
                 port: int = constants.ADS_TCP_SERVER_PORT,
                 hosts: list = None):
        self._port = port
        self._hosts = hosts or [None]
        self._tasks = utils._TaskHandler()
        self._running = False
        self._shutdown_event = asyncio.Event()
        self.server = protocol.Server()

    async def start(self):
        if self._running:
            return

        self._running = True
        self._shutdown_event.clear()
        for host in self._hosts:
            await asyncio.start_server(
                functools.partial(self.new_client, (host, self._port)),
                host=host,
                port=self._port,
            )

    async def stop(self):
        if self._running:
            await self._tasks.cancel_all(wait=True)
            self._shutdown_event.set()
            self._running = False

    async def serve_forever(self):
        await self._shutdown_event.wait()

    async def new_client(self, server_host, reader, writer):
        client_addr = writer.transport.get_extra_info('peername')
        client = self.server.add_client(server_host, client_addr)
        while True:
            data = await reader.read(1024)
            if not len(data):
                client.disconnected()
                break
            else:
                client.received_data(data)

        self.server.remove_client(client_addr)


if __name__ == '__main__':
    server = None

    async def test():
        global server
        server = AsyncioServer()
        await server.start()
        await server.serve_forever()

    from .. import log
    log.configure(level='INFO')

    asyncio.run(test())
