import asyncio
import functools
import logging
import pathlib
import sys

from .. import constants, exceptions, log, protocol, structs, symbols
from . import utils

logger = logging.getLogger(__name__)


class AsyncioServerConnection:
    server: 'AsyncioServer'
    connection: protocol.ServerConnection
    log: log.ComposableLogAdapter
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    def __init__(
        self,
        server: 'AsyncioServer',
        connection: protocol.ServerConnection,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ):
        self.server = server
        self.connection = connection
        self.reader = reader
        self.writer = writer
        self.log = connection.log
        self._queue = utils.AsyncioQueue()
        self._handle_index = 0

    async def send_response(
        self,
        *items,
        request_header: structs.AoEHeader,
        ads_error: constants.AdsError = constants.AdsError.NOERR
    ):
        circuit = self.connection.get_circuit(request_header.source.net_id)
        bytes_to_send = circuit.response_to_wire(
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
                self.connection.disconnected()
                break

            for header, item in self.connection.received_data(data):
                await self._handle_command(header, item)

    async def _handle_command(self, header: structs.AoEHeader, item):
        circuit = self.connection.get_circuit(header.source.net_id)
        try:
            response = circuit.handle_command(header, item)
        except exceptions.RequestFailedError as ex:
            logger.debug('handle_command failed with caught error',
                         exc_info=ex)
            response = ex
        except Exception as ex:
            logger.exception('handle_command failed with unknown error')
            response = exceptions.RequestFailedError(
                code=constants.AdsError.DEVICE_ERROR,  # TODO
                reason=str(ex),
                request=item,
            )

        if response is None:
            response = exceptions.RequestFailedError(
                code=constants.AdsError.DEVICE_ERROR,  # TODO
                reason='unhandled codepath',
                request=item,
            )
            logger.error('handle_command returned None: %s %s', header, item)

        if isinstance(response, protocol.AsynchronousResponse):
            response.requester = self
            await self._queue.async_put(response)
        elif isinstance(response, exceptions.RequestFailedError):
            self.log.error('Error response: %r', response)

            err_cls = structs.get_struct_by_command(
                response.request.command_id,
                request=False,
            )
            print('err_Cls', err_cls)
            err_response = err_cls(result=response.code)
            await self.send_response(err_response,
                                     request_header=header,
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

    def __init__(
        self,
        database: symbols.Database,
        port: int = constants.ADS_TCP_SERVER_PORT,
        net_id: str = '127.0.0.1.1.1',  # TODO
        hosts: list = None,
    ):
        self._port = port
        self._hosts = hosts or [None]
        self._tasks = utils._TaskHandler()
        self._running = False
        self._shutdown_event = asyncio.Event()
        self.server = protocol.Server(database, net_id=net_id)
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
        protocol_conn = self.server.add_connection(
            our_address=server_host,
            their_address=client_addr,
        )
        connection = AsyncioServerConnection(
            self, protocol_conn, reader, writer
        )
        try:
            await connection._receive_loop()
        finally:
            self.server.remove_connection(client_addr)

            # TODO: debug stuff:
            import os
            if bool(os.environ.get('ADS_ASYNC_SINGLE_SHOT', 0)):
                await self.stop()


async def run_server_with_tmc(tmc_filename):
    """
    Spawn a server using a .tmc file for symbol information.

    Parameters
    ----------
    tmc_filename : str or pathlib.Path
        Path to tmc file.
    """
    from ..symbols import TmcDatabase, dump_memory  # noqa
    database = TmcDatabase(tmc_filename)
    for data_area in database.data_areas:
        # for name, symbol in data_area.symbols.items():
        #   print(name, symbol)
        print()
        print(data_area.area_type)
        # dump_memory(data_area.memory, data_area.symbols.values())

    server = AsyncioServer(database)
    await server.start()
    await server.serve_forever()


if __name__ == '__main__':
    from .. import log
    log.configure(level='DEBUG')

    try:
        tmc_filename = sys.argv[1]
    except IndexError:
        module_path = pathlib.Path(__file__).parent.parent
        tmc_filename = module_path / 'tests' / 'kmono.tmc'

    asyncio.run(run_server_with_tmc(tmc_filename), debug=True)
