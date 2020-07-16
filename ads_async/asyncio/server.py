import asyncio
import functools
import logging

from .. import constants, protocol, structs, symbols
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
        self._queue = utils.AsyncioQueue()
        self._handle_index = 0

    async def send_response(
            self, *items, request_header,
            ads_error: constants.AdsError = constants.AdsError.NOERR):
        bytes_to_send = self.client.response_to_wire(
            *items,
            request_header=request_header,
            ads_error=ads_error)
        self.writer.write(bytes_to_send)
        await self.writer.drain()

    async def _receive_loop(self):
        self.server._tasks.create(self._request_queue_loop())
        while True:
            data = await self.reader.read(1024)
            if not len(data):
                self.client.disconnected()
                break

            for header, item in self.client.received_data(data):
                await self._handle_command(header, item)

    async def _handle_command(self, header: structs.AoEHeader, item):
        try:
            response = self.client.handle_command(header, item)
        except Exception as ex:
            logger.exception('handle_command failed')
            response = protocol.ErrorResponse(
                code=constants.AdsError.DEVICE_ERROR,  # TODO
                reason=str(ex))

        if isinstance(response, protocol.AsynchronousResponse):
            response.requester = self
            await self._queue.async_put(response)
        elif isinstance(response, protocol.ErrorResponse):
            self.log.error('Error response: %r', response)
            await self.send_response(request_header=header,
                                     ads_error=response.code,
                                     )
        else:
            await self.send_response(*response, request_header=header)

    async def _request_queue_loop(self):
        server = self.server
        while server.running:
            request = await self._queue.async_get()
            self.log.debug('Handling %s', request)

            index_group = request.command.index_group
            if index_group == constants.AdsIndexGroup.SYM_HNDBYNAME:
                ...
            # self._tasks.create()


class AsyncioServer:
    _port: int
    _hosts: list
    _tasks: utils._TaskHandler
    _running: bool
    _shutdown_event: asyncio.Event
    server: protocol.Server

    def __init__(self,
                 database: symbols.Database,
                 port: int = constants.ADS_TCP_SERVER_PORT,
                 hosts: list = None):
        self._port = port
        self._hosts = hosts or [None]
        self._tasks = utils._TaskHandler()
        self._running = False
        self._shutdown_event = asyncio.Event()
        self.server = protocol.Server(database)
        self.log = self.server.log
        self.database = database

    @property
    def running(self) -> bool:
        return self._running

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

            # TODO: debug stuff:
            import os
            if bool(os.environ.get('ADS_ASYNC_SINGLE_SHOT', 0)):
                await self.stop()


if __name__ == '__main__':
    server = None

    async def test():
        global server
        from ..symbols import TmcDatabase
        import pathlib
        module_path = pathlib.Path(__file__).parent.parent
        database = TmcDatabase(module_path / 'tests' / 'kmono.tmc')
        for data_area in database.data_areas:
            for name, symbol in data_area.symbols.items():
                print(name, symbol)

        server = AsyncioServer(database)
        await server.start()
        await server.serve_forever()

    from .. import log
    log.configure(level='DEBUG')

    asyncio.run(test(), debug=True)
