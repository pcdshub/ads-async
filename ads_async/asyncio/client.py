import asyncio
import collections
import contextvars
import ctypes
import typing
from typing import Optional, Tuple

from .. import constants, log, protocol, structs
from ..constants import AdsTransmissionMode, AmsPort
from . import utils

_target_address = contextvars.ContextVar('_target_address', default=None)


class FailureResponse(Exception):
    ...


class Notification(utils.CallbackHandler):
    """
    Represents one subscription, specified by a notification ID and handle.

    It may fan out to zero, one, or multiple user-registered callback
    functions.

    This object should never be instantiated directly by user code; rather
    it should be made by calling the ``add_notification()`` methods on
    the connection (or ``Symbol``).
    """
    def __init__(self, owner, user_callback_executor, command, target):
        super().__init__(notification_id=0, handle=None,
                         user_callback_executor=user_callback_executor)
        self.command = command
        self.target = target
        self.owner = owner
        self.log = self.owner.log
        self.most_recent_notification = None

    async def _response_handler(
        self,
        header,
        response: structs.AoENotificationHandleResponse
    ):
        if response.result != constants.AdsError.NOERR:
            self.log.debug(
                "Notification failed to initialize: %s", response.result
            )
            return

        self.handle = response.handle
        self.log.debug(
            "Notification initialized (handle=%d)", self.handle
        )
        # TODO: unsubscribe if no listeners?
        client = self.owner.client
        client.handle_to_notification[response.handle] = self

    def process(self, header, timestamp, sample):
        self.log.debug("Notification update [%s]: %s", timestamp, sample)
        notification = dict(header=header, timestamp=timestamp, sample=sample)
        super().process(self, **notification)
        self.most_recent_notification = notification

    def __repr__(self):
        return f"<Notification {self.notification_id!r} handle={self.handle}>"

    async def __aiter__(self):
        queue = utils.AsyncioQueue()

        async def iter_callback(sub, header, timestamp, sample):
            await queue.async_put((header, timestamp, sample))

        sid = self.add_callback(iter_callback)
        try:
            while True:
                item = await queue.async_get()
                yield item
        finally:
            await self.remove_callback(sid)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        # await self.clear()
        ...

    async def clear(self):
        """
        Remove all callbacks.
        """
        with self.callback_lock:
            for cb_id in list(self.callbacks):
                await self.remove_callback(cb_id)
        # Once self.callbacks is empty, self.remove_callback calls
        # self._unsubscribe for us.

    def _subscribe(self):
        async def subscribe():
            await self.owner.send(
                self.command,
                net_id=self.target[0],
                port=self.target[1],
                response_handler=self._response_handler,
            )

        self.user_callback_executor.submit(subscribe)

    async def _unsubscribe(self):
        """
        This is automatically called if the number of callbacks goes to 0.
        """
        with self.callback_lock:
            if self.handle is None:
                # Already unsubscribed.
                return
            handle = self.handle
            self.handle = None
            self.most_recent_notification = None

        # self.owner._notification_handlers.pop(handle, None)

        if self.handle is not None:
            await self.owner.remove_notification(handle)

    def add_callback(self, func):
        """
        Add a callback to receive responses.

        Parameters
        ----------
        func : callable
            Expected signature: ``func(sub, response)``.

        Returns
        -------
        token : int
            Integer token that can be passed to :meth:`remove_callback`.
        """
        # Handle func with signature func(response) for back-compat.
        with self.callback_lock:
            was_empty = not self.callbacks
            cb_id = super().add_callback(func)
            most_recent_notification = self.most_recent_notification
        if was_empty:
            # This is the first callback. Set up a subscription, which
            # should elicit a response from the server soon giving the
            # current value to this func (and any other funcs added in the
            # mean time).
            self._subscribe()
        else:
            # This callback is piggy-backing onto an existing subscription.
            # Send it the most recent response, unless we are still waiting
            # for that first response from the server.
            if most_recent_notification is not None:
                self.user_callback_executor.submit(
                    func, self, **most_recent_notification
                )

        return cb_id

    async def remove_callback(self, token):
        """
        Remove callback using token that was returned by :meth:`add_callback`.

        Parameters
        ----------

        token : integer
            Token returned by :meth:`add_callback`.
        """
        with self.callback_lock:
            super().remove_callback(token)
            if not self.callbacks:
                # Go dormant.
                await self._unsubscribe()
                self.most_recent_notification = None


class _BlockingRequest:
    """Helper for handling blocking requests in the client."""

    owner: 'AsyncioClient'
    request: structs.T_AdsStructure
    header: Optional[structs.AoEHeader]
    response: Optional[structs.T_AdsStructure]
    options: dict

    def __init__(self, owner, request, **options):
        self.owner = owner
        self.request = request
        self.options = options
        self._event = asyncio.Event()
        self.header = None
        self.response = None

    async def __aenter__(self):
        await self.owner.send(self.request, **self.options,
                              response_handler=self.got_response)
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        ...

    async def got_response(self, header, response):
        self.header = header
        self.response = response
        self._event.set()

    async def wait(self):
        await self._event.wait()


class Symbol:
    """A symbol in an AsyncioClient.  Not to be instantiated by itself."""
    owner: 'AsyncioClient'
    index_group: Optional[int]
    index_offset: Optional[int]
    name: Optional[str]
    info: Optional[structs.AdsSymbolEntry]
    handle: Optional[int]
    string_encoding: str

    def __init__(self,
                 owner: 'AsyncioClient',
                 *,
                 index_group: Optional[int] = None,
                 index_offset: Optional[int] = None,
                 name: Optional[str] = None,
                 string_encoding: str = 'utf-8',
                 ):
        self.owner = owner
        self.name = name
        self.index_group = index_group
        self.index_offset = index_offset
        self.info = None
        self.handle = None
        self.string_encoding = string_encoding

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.clear()

    async def release(self):
        """Clean up symbol handle, notifications, etc."""
        if self.handle is not None:
            await self.owner.release_handle(self.handle)
            self.handle = None

    @property
    def is_initialized(self) -> bool:
        return self.handle is not None

    async def initialize(self):
        if self.index_group is None or self.index_offset is None:
            if self.name is None:
                raise ValueError(
                    'Must specify either name or index_group/index_offset'
                )
            self.info = await self.owner.get_symbol_info_by_name(self.name)
            self.index_group = self.info.index_group
            self.index_offset = self.info.index_offset
        else:
            # self.info = await self.owner.get_symbol_info_by_...()
            raise NotImplementedError('index_group')

        self.handle = await self.owner.get_symbol_handle_by_name(self.name)

    async def read(self):
        if not self.is_initialized:
            await self.initialize()

        response = await self.owner.get_value_by_handle(
            self.handle,
            size=self.info.size,
        )

        # TODO: move this handling up
        ctypes_type = self.info.data_type.ctypes_type
        _, data = structs.deserialize_data(
            data_type=self.info.data_type,
            length=self.info.size // ctypes.sizeof(ctypes_type),
            data=response.data,
        )

        if self.info.data_type == constants.AdsDataType.STRING:
            try:
                data = b''.join(data)
                data = data[:data.index(0)]
                return str(data, self.string_encoding)
            except ValueError:
                # Fall through
                ...

        return data


class AsyncioClient:
    """
    ADS client based on asyncio.

    Parameters
    ----------
    server_host : (host, port)

    server_net_id : str, optional
        Server AMS Net ID.  If unspecified, assumed to be
        ``'{server_host}.1.1'`` by a reasonably standard convention.

    client_net_id : str, optional
        Client AMS Net ID.  May be automatically determined by the local
        network interface used to connect to the server host.

    reconnect_rate : int, optional
        Rate, in seconds, to reconnect.  None to disable automatic
        reconnection.
    """

    client: protocol.Client
    log: log.ComposableLogAdapter
    reader: asyncio.StreamReader
    writer: asyncio.StreamWriter

    def __init__(
            self,
            server_host: typing.Tuple[str, int],
            server_net_id: Optional[str] = None,
            client_net_id: Optional[str] = None,  # can be determined later
            reconnect_rate: Optional[int] = 10,
            ):
        if server_net_id is None:
            server_net_id = f'{server_host[0]}.1.1'

        self.client = protocol.Client(
            server_host=server_host,
            server_net_id=server_net_id,
            client_net_id=client_net_id,
            address=('0.0.0.0', 0)
        )
        self._default_target = (server_net_id, self.client.their_port)
        self.log = self.client.log
        self._queue = utils.AsyncioQueue()
        self._handle_index = 0
        self._tasks = utils._TaskHandler()
        self._tasks.create(self._connect())
        self._response_handlers = collections.defaultdict(list)
        self.user_callback_executor = utils.CallbackExecutor(self.log)
        self.reconnect_rate = reconnect_rate
        self._connect_event = asyncio.Event()
        self._symbols = {}

    async def __aenter__(self):
        await self.wait_for_connection()
        return self

    async def __aexit__(self, exc_type, exc_value, traceback):
        await self.close()

    async def close(self):
        """Close the connection and clean up."""
        self.reconnect_rate = None
        for sym in self._symbols.values():
            await sym.release()
        self._symbols.clear()
        try:
            self.writer.close()
        except OSError as ex:
            self.log.debug('Error closing writer: %s', ex, exc_info=ex)

    async def wait_for_connection(self):
        """Block until connected."""
        await self._connect_event.wait()

    async def _connect(self):
        self._tasks.create(self._request_queue_loop())
        while True:
            self.log.debug('Connecting...')
            try:
                self.reader, self.writer = await asyncio.open_connection(
                    host=self.client.server_host[0],
                    port=self.client.server_host[1],
                )
            except OSError:
                self.log.exception('Failed to connect')
            else:
                await self._connected_loop()

            if self.reconnect_rate is None:
                self.log.debug('Not reconnecting.')
                break

            self.log.debug(
                'Reconnecting in %d seconds...', self.reconnect_rate
            )
            await asyncio.sleep(self.reconnect_rate)

    async def send(
        self, *items,
        ads_error: constants.AdsError = constants.AdsError.NOERR,
        net_id: Optional[str] = None,
        port: Optional[AmsPort] = None,
        response_handler: Optional[typing.Coroutine] = None,
    ):
        """
        Package and send items over the wire.

        Parameters
        ----------
        *items :
            Items to send.

        net_id : str, optional
            Net ID to send to.

        port : AmsPort, optional
            Port to request notifications from.  Defaults to the current target
            port.

        Returns
        -------
        invoke_id : int
            Returns the invocation ID associated with the request.
        """
        invoke_id, bytes_to_send = self.client.request_to_wire(
            *items, ads_error=ads_error,
            target=self._get_target_or_default(net_id, port),
        )
        if response_handler is not None:
            self._response_handlers[invoke_id].append(response_handler)

        self.writer.write(bytes_to_send)
        await self.writer.drain()
        return invoke_id

    async def _connected_loop(self):
        self.log.debug('Connected')

        if self.client.client_net_id is None:
            our_ip, *_ = self.writer.get_extra_info('socket')
            self.client.client_net_id = f'{our_ip}.1.1'
            self.log.debug('Auto-configuring local net ID: %s',
                           self.client.client_net_id)

        self._connect_event.set()
        while True:
            data = await self.reader.read(1024)
            if not len(data):
                self.client.disconnected()
                self.log.debug('Disconnected')
                self._connect_event.clear()
                break

            for header, item in self.client.received_data(data):
                await self._handle_command(header, item)

    async def _handle_command(self, header: structs.AoEHeader, item):
        invoke_id_handlers = self._response_handlers.get(header.invoke_id, [])
        try:
            _ = self.client.handle_command(header, item)
        except protocol.MissingHandlerError:
            if not invoke_id_handlers:
                self.log.debug('No registered handler for %s %s', header, item)
        except Exception:
            self.log.exception('handle_command failed with unknown error')

        for handler in invoke_id_handlers:
            try:
                await handler(header, item)
            except Exception:
                self.log.exception('handle_command failed with unknown error')

        # if response is None:
        #     return

        # if isinstance(response, protocol.AsynchronousResponse):
        #     response.requester = self
        #     await self._queue.async_put(response)
        # elif isinstance(response, protocol.ErrorResponse):
        #     self.log.error('Error response: %r', response)

        #     err_cls = structs.get_struct_by_command(
        #         response.request.command_id,
        #         request=False)  # type: typing.Type[structs._AdsStructBase]
        #     err_response = err_cls(result=response.code)
        #     await self.send_response(err_response,
        #                              request_header=header,
        #                              )
        # else:
        #     await self.send_response(*response, request_header=header)

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

    @property
    def default_target(self):
        """
        Default target (net_id, port).
        """
        return self._default_target

    @default_target.setter
    def default_target(self, default_target):
        self._default_target = default_target

    def enable_log_system(self, length=255) -> Notification:
        """
        Enable the logging system to get messages from the LOGGER port.

        This returns a :class:`Notification` instance which can support an
        arbitrary number of user-provided callback methods.

        Parameters
        ----------
        length : int
            Maximum length of each notification, in bytes.
        """
        return self.add_notification_by_index(
            index_group=1,
            index_offset=65535,
            length=length,
            port=AmsPort.LOGGER,
        )

    def add_notification_by_index(
        self,
        index_group: int,
        index_offset: int,
        length: int,
        mode: AdsTransmissionMode = AdsTransmissionMode.SERVERCYCLE,
        max_delay: int = 1,
        cycle_time: int = 100,
        net_id: Optional[str] = None,
        port: Optional[AmsPort] = None,
    ) -> Notification:
        """
        Add an advanced notification by way of index group/offset.

        Parameters
        -----------
        index_group : int
            Contains the index group number of the requested ADS service.

        index_offset : int
            Contains the index offset number of the requested ADS service.

        length : int
            Maximum length of each notification, in bytes.

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
        # TODO: reuse one if it already exists
        # TODO: notifications should be tracked on the 'protocol' level
        return Notification(
            owner=self, user_callback_executor=self.user_callback_executor,
            command=self.client.add_notification_by_index(
                index_group=index_group,
                index_offset=index_offset,
                length=length,
                mode=mode,
                max_delay=max_delay,
                cycle_time=cycle_time,
            ),
            target=self._get_target_or_default(net_id, port),
        )

    def _get_target_or_default(
        self,
        net_id: Optional[str],
        port: Optional[constants.AmsPort],
    ) -> Tuple[str, constants.AmsPort]:
        """
        Optionally override the target net ID and port, or use the current
        default.
        """
        return (
            net_id or self._default_target[0],
            constants.AmsPort(port or self._default_target[1]),
        )

    async def write_and_read(
        self,
        item,
        net_id: Optional[str] = None,
        port: Optional[AmsPort] = None,
    ):
        """
        Write `item` and read the server's response.

        Parameters
        ----------
        item : T_AdsStructure
            The structure instance to write.

        port : AmsPort, optional
            The port to send the item to.
        """
        net_id, port = self._get_target_or_default(net_id, port)
        async with _BlockingRequest(self, item, net_id=net_id,
                                    port=port) as req:
            await req.wait()
        result = getattr(req, 'result', None)
        if result is not None and result != constants.AdsError.NOERR:
            raise FailureResponse(f'Request {item} failed with {result}')
        return req.response

    async def get_device_information(self) -> structs.AdsDeviceInfo:
        """Get device information."""
        return await self.write_and_read(self.client.get_device_information())

    async def get_symbol_info_by_name(
        self,
        name: str
    ) -> structs.AdsSymbolEntry:
        """
        Get symbol information by name.

        Parameters
        ----------
        name : str
            The symbol name.

        Returns
        -------
        info : AdsSymbolEntry
            The symbol entry information.
        """
        res = await self.write_and_read(
            self.client.get_symbol_info_by_name(name)
        )
        return res.upcast_by_index_group(
            constants.AdsIndexGroup.SYM_INFOBYNAMEEX
        )

    async def get_symbol_handle_by_name(
        self,
        name: str
    ) -> structs.AdsSymbolEntry:
        """
        Get symbol handle by name.

        Parameters
        ----------
        name : str
            The symbol name.

        Returns
        -------
        handle : int
            The symbol handle.
        """
        res = await self.write_and_read(
            self.client.get_symbol_handle_by_name(name)
        )
        return res.as_handle_response().handle

    async def release_handle(
        self,
        handle: int,
    ):
        """
        Release a handle by id.

        Parameters
        -----------
        handle : int
            The handle identifier.
        """
        await self.send(
            self.client.release_handle(handle),
        )

    async def get_value_by_handle(
        self,
        handle: int,
        size: int,
    ):
        """
        Get symbol value by handle.

        Parameters
        -----------
        handle : int
            The handle identifier.

        size : int
            The size (in bytes) to read.
        """
        return await self.write_and_read(
            self.client.get_value_by_handle(handle, size),
        )

    def get_symbol_by_name(self, name) -> Symbol:
        """Get a symbol by name."""
        try:
            # TODO: weakref and finalizer for cleanup
            return self._symbols[name]
        except KeyError:
            sym = Symbol(self, name=name)
            self._symbols[name] = sym
        return sym

    # TODO: single-use symbols? Or is persisting them through the
    # connection OK? Use weakrefs?

    async def get_project_name(self) -> structs.AdsDeviceInfo:
        """Get project name from global variable listing."""
        return await self.get_symbol_by_name(
            "TwinCAT_SystemInfoVarList._AppInfo.ProjectName"
        ).read()

    async def get_app_name(self) -> structs.AdsDeviceInfo:
        """Get the application name from global variable listing."""
        return await self.get_symbol_by_name(
            "TwinCAT_SystemInfoVarList._AppInfo.AppName"
        ).read()

    async def get_task_count(self) -> int:
        """Get the number of tasks running on the PLC."""
        count, = await self.get_symbol_by_name(
            "TwinCAT_SystemInfoVarList._AppInfo.TaskCnt"
        ).read()
        return count

    async def get_task_names(self) -> typing.Dict[int, str]:
        """Get the names of tasks running on the PLC."""
        task_count = await self.get_task_count()
        names = {}
        for task_id in range(1, task_count + 1):
            names[task_id] = await self.get_symbol_by_name(
                f"TwinCAT_SystemInfoVarList._TaskInfo[{task_id}].TaskName"
            ).read()
        return names

    async def prune_unknown_notifications(self):
        """Prune all unknown notification IDs by unregistering each of them."""
        for source, handle in self.client.unknown_notifications:
            _, port = source
            await self.send(self.client.remove_notification(handle),
                            port=port)
