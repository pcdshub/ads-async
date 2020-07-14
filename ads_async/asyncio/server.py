import asyncio
import functools
import logging

from .. import constants, protocol, structs
from . import utils

logger = logging.getLogger(__name__)


class AsyncioAcceptedClient:
    server: 'AsyncioServer'
    client: protocol.AcceptedClient
    log: logging.Logger
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    def __init__(
            self, server: 'AsyncioServer', client: protocol.AcceptedClient,
            reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.server = server
        self.client = client
        self.reader = reader
        self.writer = writer
        self.log = client.log

    async def send_response(self, request_header, item):
        bytes_to_send = self.client.response_to_wire(
            item, request_header=request_header)
        self.writer.write(bytes_to_send)
        await self.writer.drain()

    async def _receive_loop(self):
        while True:
            data = await self.reader.read(1024)
            if not len(data):
                self.client.disconnected()
                break

            for header, item in self.client.received_data(data):
                await self._handle_command(header, item)

    async def _handle_command(self, header: structs.AoEHeader, item):
        for response in self.client.handle_command(header, item):
            await self.send_response(header, response)


class AsyncioServer:
    _port: int
    _hosts: list
    _tasks: utils._TaskHandler
    _running: bool
    _shutdown_event: asyncio.Event
    server: protocol.Server

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
                functools.partial(self._handle_new_client,
                                  (host or '0.0.0.0', self._port)),
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

    async def _handle_new_client(self, server_host, reader, writer):
        client_addr = writer.transport.get_extra_info('peername')
        protocol_client = self.server.add_client(server_host, client_addr)
        client = AsyncioAcceptedClient(self, protocol_client, reader, writer)
        try:
            await client._receive_loop()
        finally:
            self.server.remove_client(client_addr)


if __name__ == '__main__':
    server = None

    async def test():
        global server
        server = AsyncioServer()
        await server.start()
        await server.serve_forever()

    from .. import log
    log.configure(level='DEBUG')

    asyncio.run(test())
