import asyncio
import functools
import logging
import typing

from .. import constants, log, protocol, structs, symbols
from ..constants import AdsTransmissionMode, AmsPort
from . import utils

from typing import Optional

logger = logging.getLogger(__name__)


class AsyncioClient:
    client: protocol.Client
    log: log.ComposableLogAdapter
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    def __init__(
            self,
            server_host: typing.Tuple[str, int],
            server_net_id: str,
            client_net_id: str,
            ):
        self.client = protocol.Client(
            server_host=server_host,
            server_net_id=server_net_id,
            client_net_id=client_net_id,
            address=('0.0.0.0', 0)
        )
        self.log = self.client.log
        self._should_reconnect = True
        self._queue = utils.AsyncioQueue()
        self._handle_index = 0
        self._tasks = utils._TaskHandler()
        self._tasks.create(self._connect())

    async def _connect(self):
        while self._should_reconnect:
            self.reader, self.writer = await asyncio.open_connection(
                host=self.client.server_host[0],
                port=self.client.server_host[1],
            )
            self.log.debug('Connected')

            await self._receive_loop()
            self.log.debug('Disconnected')
            if self._should_reconnect:
                self.log.debug('Reconnecting in 10 seconds...')
                await asyncio.sleep(10)
            else:
                self.log.debug('Not reconnecting.')

    async def send(
            self, *items,
            ads_error: constants.AdsError = constants.AdsError.NOERR,
            port: Optional[AmsPort] = None,
    ):
        """
        Package and send items over the wire.

        Parameters
        ----------
        *items :
            Items to send.

        port : AmsPort, optional
            Port to request notifications from.  Defaults to the current target
            port.
        """
        bytes_to_send = self.client.request_to_wire(
            *items, ads_error=ads_error,
            port=port or self.client.their_port
        )
        self.writer.write(bytes_to_send)
        await self.writer.drain()

    # async def _receive_loop(self):
    #     with open('ads_messages3.txt', 'rb') as f:
    #         data = bytearray(f.read())

    #     for header, item in self.client.received_data(data):
    #         await self._handle_command(header, item)

    async def _receive_loop(self):
        self._tasks.create(self._request_queue_loop())
        while True:
            data = await self.reader.read(1024)
            print('received', data)
            if not len(data):
                self.client.disconnected()
                break

            for header, item in self.client.received_data(data):
                await self._handle_command(header, item)

    async def _handle_command(self, header: structs.AoEHeader, item):
        try:
            response = self.client.handle_command(header, item)
        except Exception:
            logger.exception('handle_command failed with unknown error')
            return

        if response is None:
            return

        if isinstance(response, protocol.AsynchronousResponse):
            response.requester = self
            await self._queue.async_put(response)
        elif isinstance(response, protocol.ErrorResponse):
            self.log.error('Error response: %r', response)

            err_cls = structs.get_struct_by_command(
                response.request.command_id,
                request=False)  # type: typing.Type[structs._AdsStructBase]
            err_response = err_cls(result=response.code)
            await self.send_response(err_response,
                                     request_header=header,
                                     )
        else:
            await self.send_response(*response, request_header=header)

    async def _request_queue_loop(self):
        ...
        # server = self.server
        # while server.running:
        #     request = await self._queue.async_get()
        #     self.log.debug('Handling %s', request)

        #     index_group = request.command.index_group
        #     if index_group == constants.AdsIndexGroup.SYM_HNDBYNAME:
        #         ...
        #     # self._tasks.create()

    async def enable_log_system(self, length=255):
        return await self.add_notification_by_index(
            index_group=1,
            index_offset=65535,
            length=length,
            port=AmsPort.LOGGER,
        )

    async def add_notification_by_index(
        self,
        index_group: int,
        index_offset: int,
        length: int,
        mode: AdsTransmissionMode = AdsTransmissionMode.SERVERCYCLE,
        max_delay: int = 1,
        cycle_time: int = 100,
        port: Optional[AmsPort] = None,
    ):
        """
        Add an advanced notification by way of index group/offset.

        Parameters
        -----------
        index_group : int
            Contains the index group number of the requested ADS service.

        index_offset : int
            Contains the index offset number of the requested ADS service.

        length : int
            Max length.

        mode : AdsTransmissionMode
            Specifies if the event should be fired cyclically or only if the
            variable has changed.

        max_delay : int
            The event is fired at *latest* when this time has elapsed. [ms]

        cycle_time : int
            The ADS server checks whether the variable has changed after this
            time interval. [ms]

        port : AmsPort, optional
            Port to request notifications from.  Defaults to the current target
            port.
        """
        await self.send(
            self.client.add_notification_by_index(
                index_group=index_group,
                index_offset=index_offset,
                length=length,
                mode=mode,
                max_delay=max_delay,
                cycle_time=cycle_time,
            ),
            port=port,
        )


if __name__ == '__main__':
    server = None

    async def test():
        client = AsyncioClient(server_host=('localhost', 48898),
                               server_net_id='172.21.148.227.1.1',
                               client_net_id='172.21.148.164.1.1',
                               )
        await asyncio.sleep(1)  # connection event
        await client.enable_log_system()
        await asyncio.sleep(100)

    from .. import log
    log.configure(level='DEBUG')

    asyncio.run(test(), debug=True)
