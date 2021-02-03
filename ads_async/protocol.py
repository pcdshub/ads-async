import copy
import ctypes
import inspect
import logging
import typing
from typing import Optional

from . import constants, log, structs, utils
from .constants import (AdsCommandId, AdsError, AdsIndexGroup,
                        AdsTransmissionMode, AmsPort, AoEHeaderFlag)
from .symbols import Database, Symbol

module_logger = logging.getLogger(__name__)

# TODO: AMS can be over serial, UDP, etc. and not just TCP
_AMS_HEADER_LENGTH = ctypes.sizeof(structs.AmsTcpHeader)
_AOE_HEADER_LENGTH = ctypes.sizeof(structs.AoEHeader)


class MissingHandlerError(RuntimeError):
    ...


def from_wire(
        buf: bytearray, *,
        logger: logging.Logger = module_logger,
        ) -> typing.Generator[typing.Tuple[structs.AoEHeader, typing.Any],
                              None, None]:
    """
    Deserialize data from the wire into ctypes structures.

    Parameters
    ----------
    buf : bytearray
        The receive data buffer.

    logger : logging.Logger, optional
        Logger to use in reporting malformed data.  Defaults to the module
        logger.

    Yields
    ------
    header: structs.AoEHeader
        The header associated with `item`.

    item: structs._AdsStructBase or memoryview
        Deserialized to the correct structure, if recognized. If not,
        a memoryview to inside the buffer.
    """
    header: structs.AmsTcpHeader
    aoe_header: structs.AoEHeader

    while len(buf) >= _AMS_HEADER_LENGTH:
        # TCP header / AoE header / frame
        view = memoryview(buf)
        header = structs.AmsTcpHeader.from_buffer(view)
        if header.length < _AOE_HEADER_LENGTH:
            # Not sure?
            logger.warning(
                'Throwing away packet as header.length=%d < %d',
                header.length, _AOE_HEADER_LENGTH
            )
            buf = buf[header.length + _AMS_HEADER_LENGTH:]
            continue

        required_bytes = _AMS_HEADER_LENGTH + header.length
        if len(buf) < required_bytes:
            break

        view = view[_AMS_HEADER_LENGTH:]
        aoe_header = structs.AoEHeader.from_buffer(view)

        view = view[_AOE_HEADER_LENGTH:]
        expected_size = (
            _AMS_HEADER_LENGTH + _AOE_HEADER_LENGTH + aoe_header.length
        )

        buf = buf[required_bytes:]

        if expected_size != required_bytes:
            logger.warning(
                'Throwing away packet as lengths do not add up: '
                'AMS=%d AOE=%d payload=%d -> %d != AMS-header specified %d',
                _AMS_HEADER_LENGTH, _AOE_HEADER_LENGTH, aoe_header.length,
                expected_size, required_bytes
            )
            item = None
        else:
            try:
                cmd_cls = structs.get_struct_by_command(
                    aoe_header.command_id,
                    request=aoe_header.is_request
                )  # type: structs._AdsStructBase
                cmd = None
            except KeyError:
                cmd = view[:aoe_header.length]
            else:
                # if hasattr(cmd_cls, 'deserialize'):
                # TODO: can't call super.from_buffer in a subclass
                # classmethod?
                cmd = cmd_cls.deserialize(view)

            item = (aoe_header, cmd)

        yield required_bytes, item


def response_to_wire(
        *items: structs.T_Serializable,
        request_header: structs.AoEHeader,
        ads_error: AdsError = AdsError.NOERR
        ) -> typing.Tuple[list, bytearray]:
    """
    Prepare `items` to be sent over the wire.

    Parameters
    -------
    *items : structs._AdsStructBase or bytes
        Individual structures or pre-serialized data to send.

    request_header : structs.AoEHeader
        The request to respond to.

    ads_error : AdsError
        The AoEHeader error code to return.

    Returns
    -------
    headers_and_items : list
        All headers and items to be serialized, in order.

    data : bytearray
        Serialized data to send.
    """
    items_serialized = [structs.serialize(item) for item in items]
    item_length = sum(len(item) for item in items_serialized)

    headers = [
        structs.AmsTcpHeader(_AOE_HEADER_LENGTH + item_length),
        structs.AoEHeader.create_response(
                target=request_header.source,
                source=request_header.target,
                command_id=request_header.command_id,
                invoke_id=request_header.invoke_id,
                length=item_length,
                error_code=ads_error,
            ),
    ]

    bytes_to_send = bytearray()
    for item in headers + items_serialized:
        bytes_to_send.extend(item)

    return headers + list(items), bytes_to_send


def request_to_wire(
        *items: structs.T_Serializable,
        target: structs.AmsAddr,
        source: structs.AmsAddr,
        invoke_id: int,
        state_flags: AoEHeaderFlag = AoEHeaderFlag.ADS_COMMAND,
        error_code: int = 0,
        ) -> typing.Tuple[list, bytearray]:
    """
    Prepare `items` to be sent over the wire.

    Parameters
    -------
    *items : structs._AdsStructBase or bytes
        Individual structures or pre-serialized data to send.

    ads_error : AdsError
        The AoEHeader error code to return.

    Returns
    -------
    headers_and_items : list
        All headers and items to be serialized, in order.

    data : bytearray
        Serialized data to send.
    """
    items_serialized = [structs.serialize(item) for item in items]
    item_length = sum(len(item) for item in items_serialized)

    item, = items  # TODO

    headers = [
        structs.AmsTcpHeader(_AOE_HEADER_LENGTH + item_length),
        structs.AoEHeader.create_request(
                target=target,
                source=source,
                command_id=item.command_id,
                invoke_id=invoke_id,
                length=item_length,
                state_flags=state_flags,
                error_code=error_code,
            ),
    ]

    bytes_to_send = bytearray()
    for item in headers + items_serialized:
        bytes_to_send.extend(item)

    return headers + list(items), bytes_to_send


class Notification:
    handle: int
    symbol: Symbol

    def __init__(self, handle: int, symbol: Symbol):
        self.handle = handle
        self.symbol = symbol


def _command_handler(cmd: constants.AdsCommandId,
                     index_group: constants.AdsIndexGroup = None):
    """Decorator to indicate a handler method for the given command/group."""

    def wrapper(method):
        if not hasattr(method, '_handle_commands'):
            method._handles_commands = set()
        method._handles_commands.add((cmd, index_group))
        return method
    return wrapper


class ConnectionBase:
    handle_to_notification: dict
    handle_to_symbol: dict
    log: log.ComposableLogAdapter
    our_port: AmsPort
    recv_buffer: bytearray
    tags: dict

    def __init__(self,
                 server_host: typing.Tuple[str, int],
                 address: typing.Tuple[str, int],
                 tags: Optional[dict] = None,
                 our_port: AmsPort = AmsPort.R0_PLC_TC3,
                 their_port: AmsPort = AmsPort.R0_PLC_TC3,
                 ):
        self.address = address
        self.server_host = server_host
        self.our_port = our_port
        self.their_port = their_port
        self.recv_buffer = bytearray()
        self.handle_to_symbol = {}
        self.handle_to_notification = {}
        self.id_to_request = {}
        self._handle_counter = utils.ThreadsafeCounter(
            initial_value=100,
            max_count=2 ** 32,  # handles are uint32
            dont_clash_with=self.handle_to_symbol,
        )
        self._notification_counter = utils.ThreadsafeCounter(
            initial_value=100,
            max_count=2 ** 32,  # handles are uint32
            dont_clash_with=self.handle_to_notification,
        )
        self._invoke_counter = utils.ThreadsafeCounter(
            initial_value=100,
            max_count=2 ** 32,  # handles are uint32
        )
        self.tags = tags or {}
        self.log = log.ComposableLogAdapter(module_logger, self.tags)

    def disconnected(self):
        """Disconnected callback."""

    def handle_command(self, header: structs.AoEHeader,
                       request: Optional[structs._AdsStructBase]):
        """
        Top-level command dispatcher.

        First stop to determine which method will handle the given command.

        Parameters
        ----------
        header : structs.AoEHeader
            The request header.

        request : structs._AdsStructBase or None
            The request itself.  May be optional depending on the header's
            AdsCommandId.

        Returns
        -------
        response : list
            List of commands or byte strings to be serialized.
        """
        command = header.command_id
        self.log.debug('Handling %s', command,
                       extra={'sequence': header.invoke_id})

        if hasattr(request, 'index_group'):
            keys = [(command, request.index_group),  # type: ignore
                    (command, None)]
        else:
            keys = [(command, None)]

        for key in keys:
            try:
                handler = self._handlers[key]
            except KeyError:
                ...
            else:
                return handler(self, header, request)

        raise MissingHandlerError(f'No handler defined for command {keys}')

    def received_data(self, data):
        """Hook for new data received over the socket."""
        self.recv_buffer += data
        for consumed, item in from_wire(self.recv_buffer, logger=self.log):
            self.log.debug('%s', item,
                           extra={'direction': '<<<---',
                                  'bytesize': consumed})
            self.recv_buffer = self.recv_buffer[consumed:]
            yield item

    def response_to_wire(self, *items: structs.T_Serializable,
                         request_header: structs.AoEHeader,
                         ads_error: AdsError = AdsError.NOERR
                         ) -> bytearray:
        """
        Prepare `items` to be sent over the wire.

        Parameters
        -------
        *items : structs._AdsStructBase or bytes
            Individual structures or pre-serialized data to send.

        request_header : structs.AoEHeader
            The request to respond to.

        ads_error : AdsError
            The AoEHeader error code to return.

        Returns
        -------
        data : bytearray
            Serialized data to send.
        """

        items, bytes_to_send = response_to_wire(
            *items,
            request_header=request_header,
            ads_error=ads_error
        )

        if self.log.isEnabledFor(logging.DEBUG):
            extra = {
                'direction': '--->>>',
                'sequence': request_header.invoke_id,
                'bytesize': len(bytes_to_send),
            }
            for idx, item in enumerate(items, 1):
                extra['counter'] = (idx, len(items))
                self.log.debug('%s', item, extra=extra)

        return bytes_to_send

    def request_to_wire(self, *items: structs.T_Serializable,
                        ads_error: AdsError = AdsError.NOERR,
                        port: constants.AmsPort = constants.AmsPort.R0_PLC_TC3,
                        ) -> bytearray:
        """
        Prepare `items` to be sent over the wire.

        Parameters
        -------
        *items : structs._AdsStructBase or bytes
            Individual structures or pre-serialized data to send.

        request_header : structs.AoEHeader
            The request to respond to.

        ads_error : AdsError
            The AoEHeader error code to return.

        Returns
        -------
        data : bytearray
            Serialized data to send.
        """
        invoke_id = self._invoke_counter()
        assert len(items) == 1  # TODO

        item, = items
        self.id_to_request[invoke_id] = item

        items, bytes_to_send = request_to_wire(
            *items,
            target=structs.AmsAddr(
                structs.AmsNetId.from_string(self.server_net_id),
                port
            ),
            source=structs.AmsAddr(
                structs.AmsNetId.from_string(self.client_net_id),
                self.our_port
            ),
            invoke_id=invoke_id,
        )

        if self.log.isEnabledFor(logging.DEBUG):
            extra = {
                'direction': '--->>>',
                'sequence': invoke_id,
                'bytesize': len(bytes_to_send),
            }
            for idx, item in enumerate(items, 1):
                extra['counter'] = (idx, len(items))
                self.log.debug('%s', item, extra=extra)

        return invoke_id, bytes_to_send


class AcceptedClient(ConnectionBase):
    """
    A client which connected to a :class:`Server`.

    Parameters
    ----------
    server : Server
        The server instance.

    server_host : (host, port)
        The host and port the client connected to.

    address : (host, port)
        The client address.
    """

    _handle_counter: utils.ThreadsafeCounter
    _notification_counter: utils.ThreadsafeCounter
    _handlers: typing.Dict[typing.Tuple[AdsCommandId,
                                        Optional[AdsIndexGroup]],
                           typing.Callable]
    address: typing.Tuple[str, int]
    server: 'Server'
    server_host: typing.Tuple[str, int]

    def __init__(self,
                 server: 'Server',
                 server_host: typing.Tuple[str, int],
                 address: typing.Tuple[str, int]
                 ):
        self.server = server
        tags = {
            'role': 'CLIENT',
            'our_address': address,
            'their_address': server_host or ('0.0.0.0', 0),
        }
        super().__init__(server_host=server_host, address=address, tags=tags)

    def __repr__(self):
        return (
            f'<{self.__class__.__name__} address={self.address} '
            f'server_host={self.server_host}>'
        )

    def _get_symbol_by_request_name(self, request) -> Symbol:
        """Get symbol by a request with a name as its payload."""
        symbol_name = structs.byte_string_to_string(request.data)
        try:
            return self.server.database.get_symbol_by_name(symbol_name)
        except KeyError:
            raise ErrorResponse(
                code=AdsError.DEVICE_SYMBOLNOTFOUND,
                reason=f'{symbol_name!r} not in database',
                request=request,
            ) from None

    def _get_symbol_by_request_handle(self, request) -> Symbol:
        """Get symbol by a request with a handle as its payload."""
        try:
            return self.handle_to_symbol[request.handle]
        except KeyError as ex:
            raise ErrorResponse(code=AdsError.CLIENT_INVALIDPARM,  # TODO?
                                reason=f'{ex} bad handle',
                                request=request) from None

    @_command_handler(AdsCommandId.ADD_DEVICE_NOTIFICATION)
    def _add_notification(self, header: structs.AoEHeader,
                          request: structs.AdsAddDeviceNotificationRequest):
        if request.index_group == AdsIndexGroup.SYM_VALBYHND:
            symbol = self._get_symbol_by_request_handle(request)
            handle = self._handle_counter()
            self.handle_to_notification[handle] = Notification(
                symbol=symbol,
                handle=handle,
            )
            return [structs.AoENotificationHandleResponse(handle=handle)]

    @_command_handler(AdsCommandId.DEL_DEVICE_NOTIFICATION)
    def _delete_notification(
            self, header: structs.AoEHeader,
            request: structs.AdsDeleteDeviceNotificationRequest):
        self.handle_to_notification.pop(request.handle)
        return [structs.AoEResponseHeader(AdsError.NOERR)]

    @_command_handler(AdsCommandId.WRITE_CONTROL)
    def _write_control(self, header: structs.AoEHeader,
                       request: structs.AdsWriteControlRequest):
        raise NotImplementedError('write_control')

    @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_RELEASEHND)
    def _write_release_handle(self, header: structs.AoEHeader,
                              request: structs.AdsWriteRequest):
        handle = request.handle
        self.handle_to_symbol.pop(handle, None)
        return [structs.AoEResponseHeader()]

    @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_VALBYHND)
    @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_VALBYNAME)
    def _write_value(self, header: structs.AoEHeader,
                     request: structs.AdsWriteRequest):
        if request.index_group == AdsIndexGroup.SYM_VALBYHND:
            symbol = self._get_symbol_by_request_handle(request)
        else:
            symbol = self._get_symbol_by_request_name(request)

        old_value = repr(symbol.value)
        symbol.write(request.data)
        self.log.debug('Writing symbol %s old value: %s new value: %s',
                       symbol, old_value, symbol.value)
        return [structs.AoEResponseHeader()]

    @_command_handler(AdsCommandId.READ, AdsIndexGroup.SYM_VALBYHND)
    @_command_handler(AdsCommandId.READ, AdsIndexGroup.SYM_VALBYNAME)
    def _read_value(self, header: structs.AoEHeader,
                    request: structs.AdsReadRequest):
        if request.index_group == AdsIndexGroup.SYM_VALBYHND:
            symbol = self._get_symbol_by_request_handle(request)
        else:
            symbol = self._get_symbol_by_request_name(request)

        return [structs.AoEReadResponse(data=symbol.read())]

    @_command_handler(AdsCommandId.READ)
    def _read_catchall(self, header: structs.AoEHeader,
                       request: structs.AdsReadRequest):
        try:
            data_area = self.server.database.index_groups[request.index_group]
        except KeyError:
            raise ErrorResponse(
                code=AdsError.DEVICE_INVALIDACCESS,  # TODO?
                reason=f'Invalid index group: {request.index_group}',
                request=request,
            ) from None
        else:
            data = data_area.memory.read(request.index_offset, request.length)
            return [structs.AoEReadResponse(data=data)]

    @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SYM_HNDBYNAME)
    def _read_write_handle(self, header: structs.AoEHeader,
                           request: structs.AdsReadWriteRequest):
        symbol = self._get_symbol_by_request_name(request)
        handle = self._handle_counter()
        self.handle_to_symbol[handle] = symbol
        return [
            structs.AoEHandleResponse(result=AdsError.NOERR, handle=handle)
        ]

    @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SYM_INFOBYNAMEEX)
    def _read_write_info(self, header: structs.AoEHeader,
                         request: structs.AdsReadWriteRequest):
        symbol = self._get_symbol_by_request_name(request)
        index_group = symbol.data_area.index_group.value  # TODO
        symbol_entry = structs.AdsSymbolEntry(
            index_group=index_group,  # type: ignore
            index_offset=symbol.offset,
            size=symbol.byte_size,
            data_type=symbol.data_type,
            flags=constants.AdsSymbolFlag(0),  # TODO symbol.flags
            name=symbol.name,
            type_name=symbol.data_type_name,
            comment=symbol.__doc__ or '',
        )
        return [structs.AoEReadResponse(data=symbol_entry)]

    @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SUMUP_READ)
    def _read_write_sumup_read(self, header: structs.AoEHeader,
                               request: structs.AdsReadWriteRequest):
        read_size = ctypes.sizeof(structs.AdsReadRequest)
        count = len(request.data) // read_size
        self.log.debug('Request to read %d items', count)
        buf = bytearray(request.data)

        results = []
        data = []

        # Handle these as normal reads by making a fake READ header:
        read_header = copy.copy(header)
        read_header.command_id = AdsCommandId.READ

        for idx in range(count):
            read_req = structs.AdsReadRequest.from_buffer(buf)
            read_response = self.handle_command(read_header, read_req)
            self.log.debug('[%d] %s -> %s', idx + 1, read_req, read_response)
            if read_response is not None:
                results.append(read_response[0].result)
                data.append(read_response[0].data)
            else:
                # TODO (?)
                results.append(AdsError.DEVICE_ERROR)
                data.append(bytes(read_req.length))
            buf = buf[read_size:]

        if request.index_offset != 0:
            to_send = [ctypes.c_uint32(res) for res in results] + data
        else:
            to_send = data

        return [structs.AoEReadResponse(
            data=b''.join(structs.serialize(res) for res in to_send)
        )]

    @_command_handler(AdsCommandId.READ_WRITE)
    def _read_write_catchall(self, header: structs.AoEHeader,
                             request: structs.AdsReadWriteRequest):
        ...

    @_command_handler(AdsCommandId.READ_DEVICE_INFO)
    def _read_device_info(self, header: structs.AoEHeader, request):
        return [
            structs.AoEResponseHeader(AdsError.NOERR),
            structs.AdsDeviceInfo(*self.server.version, name=self.server.name)
        ]

    @_command_handler(AdsCommandId.READ_STATE)
    def _read_state(self, header: structs.AoEHeader, request):
        return [
            structs.AoEResponseHeader(AdsError.NOERR),
            structs.AdsReadStateResponse(self.server.ads_state,
                                         0  # TODO: find docs
                                         )
        ]


def _aggregate_handlers(
        cls: type
        ) -> typing.Generator[typing.Tuple[typing.Tuple[AdsCommandId,
                                                        AdsIndexGroup],
                                           typing.Callable],
                              None, None]:
    """
    Aggregate the `_handlers` dictionary, for use in
    `AcceptedClient.handle_command`.
    """
    for attr, obj in inspect.getmembers(cls):
        for handles in getattr(obj, '_handles_commands', []):
            yield handles, getattr(cls, attr)


AcceptedClient._handlers = dict(_aggregate_handlers(AcceptedClient))


class ErrorResponse(Exception):
    code: AdsError
    reason: str

    def __init__(self, reason: str, code: AdsError,
                 request: structs._AdsStructBase):
        super().__init__(reason)
        self.code = code
        self.request = request

    def __repr__(self):
        return f'<ErrorResponse {self.code} ({self})>'


class AsynchronousResponse:
    header: structs.AoEHeader
    command: structs._AdsStructBase
    invoke_id: int
    requester: object

    def __init__(self, header, command, requester):
        self.invoke_id = header.invoke_id
        self.header = header
        self.command = command
        self.requester = requester

    def __repr__(self):
        return (f'<{self.__class__.__name__} invoke_id={self.invoke_id} '
                f'command={self.command}>')


class Server:
    """
    An ADS server which manages :class:`AcceptedClient` instances.

    This server does not interact with sockets directly.  A layer must be added
    on top to have a functional server.

    See Also
    --------
    :class:`.asyncio.server.AsyncioServer`
    """

    _version: typing.Tuple[int, int, int] = (0, 0, 0)  # TODO: from versioneer
    _name: str
    clients: dict
    database: Database
    log: log.ComposableLogAdapter

    def __init__(self, database: Database, *, name='AdsAsync'):
        self._name = name
        self.database = database
        self.clients = {}
        tags = {
            'role': 'SERVER',
        }

        self.log = log.ComposableLogAdapter(module_logger, tags)

    def add_client(self,
                   server_host: typing.Tuple[str, int],
                   address: typing.Tuple[str, int]
                   ) -> AcceptedClient:
        """
        Add a new client.

        Parameters
        ----------
        server_host : (host, port)
            The host and port the client connected to.

        address : (host, port)
            The client address.

        Returns
        -------
        client : AcceptedClient
        """
        client = AcceptedClient(self, server_host, address)
        self.clients[address] = client
        self.log.info('New client (%d total): %s', len(self.clients), client)
        return client

    def remove_client(self, address: typing.Tuple[str, int]):
        """
        Remove a client by address.

        Parameters
        ----------
        address : (host, port)
            The client address.
        """
        client = self.clients.pop(address)
        self.log.info('Removing client (%d total): %s', len(self.clients),
                      client)

    @property
    def ads_state(self) -> constants.AdsState:
        """The current state of the server."""
        return constants.AdsState.RUN

    @property
    def version(self) -> typing.Tuple[int, int, int]:
        """The server version."""
        return self._version

    @property
    def name(self) -> str:
        """The server name."""
        return self._name


class Client(ConnectionBase):
    """
    A client instance.

    Parameters
    ----------
    server_host : (host, port)
        The host and port the client connected to.

    address : (host, port)
        The client address.
    """

    _handle_counter: utils.ThreadsafeCounter
    _notification_counter: utils.ThreadsafeCounter
    _handlers: typing.Dict[typing.Tuple[AdsCommandId,
                                        Optional[AdsIndexGroup]],
                           typing.Callable]
    address: typing.Tuple[str, int]
    server_host: typing.Tuple[str, int]

    def __init__(self,
                 server_host: typing.Tuple[str, int],
                 server_net_id: str,
                 client_net_id: typing.Optional[str],
                 address: typing.Tuple[str, int],
                 ):
        self.server_net_id = server_net_id
        self.client_net_id = client_net_id
        self.unknown_notifications = set()
        tags = {
            'role': 'CLIENT',
            'our_address': address,
            'their_address': server_host,
        }
        super().__init__(server_host=server_host, address=address, tags=tags)

    def __repr__(self):
        return (
            f'<{self.__class__.__name__} address={self.address} '
            f'server_host={self.server_host}>'
        )

    def handle_command(self, header: structs.AoEHeader,
                       response: Optional[structs._AdsStructBase]):
        """
        Top-level command dispatcher.

        First stop to determine which method will handle the given command.

        Parameters
        ----------
        header : structs.AoEHeader
            The request header.

        response : structs._AdsStructBase or None
            The response itself.  May be optional depending on the header's
            AdsCommandId.

        Returns
        -------
        response : list
            List of commands or byte strings to be serialized.
        """
        command = header.command_id
        self.log.debug('Handling %s', command,
                       extra={'sequence': header.invoke_id})

        if hasattr(response, 'index_group'):
            keys = [(command, response.index_group),  # type: ignore
                    (command, None)]
        else:
            keys = [(command, None)]

        for key in keys:
            try:
                handler = self._handlers[key]
            except KeyError:
                ...
            else:
                request = self.id_to_request.pop(header.invoke_id, None)
                return handler(self, request, header, response)

        raise MissingHandlerError(f'No handler defined for command {keys}')

    def _get_symbol_by_request_name(self, request) -> Symbol:
        """Get symbol by a request with a name as its payload."""
        symbol_name = structs.byte_string_to_string(request.data)
        try:
            return self.server.database.get_symbol_by_name(symbol_name)
        except KeyError:
            raise ErrorResponse(
                code=AdsError.DEVICE_SYMBOLNOTFOUND,
                reason=f'{symbol_name!r} not in database',
                request=request,
            ) from None

    def _get_symbol_by_request_handle(self, request) -> Symbol:
        """Get symbol by a request with a handle as its payload."""
        try:
            return self.handle_to_symbol[request.handle]
        except KeyError as ex:
            raise ErrorResponse(code=AdsError.CLIENT_INVALIDPARM,  # TODO?
                                reason=f'{ex} bad handle',
                                request=request) from None

    @_command_handler(AdsCommandId.ADD_DEVICE_NOTIFICATION)
    def _add_notification(
            self,
            request: structs.AdsAddDeviceNotificationRequest,
            header: structs.AoEHeader,
            response: structs.AoENotificationHandleResponse,
    ):
        key = (tuple(header.source), request.handle)
        if key in self.unknown_notifications:
            self.unknown_notifications.remove(key)

    @_command_handler(AdsCommandId.DEL_DEVICE_NOTIFICATION)
    def _delete_notification(
            self,
            request: structs.AdsDeleteDeviceNotificationRequest,
            header: structs.AoEHeader,
            response: structs.AoEResponseHeader,
    ):
        if request is None:
            return

        self.handle_to_notification.pop(request.handle, None)

        key = (tuple(header.source), request.handle)
        if key in self.unknown_notifications:
            self.unknown_notifications.remove(key)

    # @_command_handler(AdsCommandId.WRITE_CONTROL)
    # def _write_control(self, header: structs.AoEHeader,
    #                    response: structs.AdsWriteControlResponse):
    #     ...

    # @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_RELEASEHND)
    # def _write_release_handle(self, header: structs.AoEHeader,
    #                           response: structs.AdsWriteResponse):
    #     ...

    # @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_VALBYHND)
    # @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_VALBYNAME)
    # def _write_value(self, header: structs.AoEHeader,
    #                  response: structs.AdsWriteResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ, AdsIndexGroup.SYM_VALBYHND)
    # @_command_handler(AdsCommandId.READ, AdsIndexGroup.SYM_VALBYNAME)
    # def _read_value(self, header: structs.AoEHeader,
    #                 response: structs.AdsReadResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ)
    # def _read_catchall(self, header: structs.AoEHeader,
    #                    response: structs.AdsReadResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SYM_HNDBYNAME)
    # def _read_write_handle(self, header: structs.AoEHeader,
    #                        response: structs.AdsReadWriteResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ_WRITE,
    #                   AdsIndexGroup.SYM_INFOBYNAMEEX)
    # def _read_write_info(self, header: structs.AoEHeader,
    #                      response: structs.AdsReadWriteResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SUMUP_READ)
    # def _read_write_sumup_read(self, header: structs.AoEHeader,
    #                            response: structs.AdsReadWriteResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ_WRITE)
    # def _read_write_catchall(self, header: structs.AoEHeader,
    #                          response: structs.AdsReadWriteResponse):
    #     ...

    # @_command_handler(AdsCommandId.READ_DEVICE_INFO)
    # def _read_device_info(self, header: structs.AoEHeader, response):
    #     ...

    # @_command_handler(AdsCommandId.READ_STATE)
    # def _read_state(self, header: structs.AoEHeader, response):
    #     ...

    @_command_handler(AdsCommandId.DEVICE_NOTIFICATION)
    def _device_notification(self,
                             request,
                             header: structs.AoEHeader,
                             stream: structs.AdsNotificationStream):
        for stamp in stream.stamps:
            timestamp = stamp.timestamp
            for sample in stamp.samples:
                try:
                    handler = self.handle_to_notification[
                        sample.notification_handle]
                except KeyError:
                    self.log.debug('No handler for notification id %s %d?',
                                   header.source, sample.notification_handle)
                    self.unknown_notifications.add(
                        (tuple(header.source), sample.notification_handle)
                    )
                else:
                    handler.process(header=header, timestamp=timestamp,
                                    sample=sample)

    def add_notification_by_index(
        self,
        index_group: int,
        index_offset: int,
        length: int,
        mode: AdsTransmissionMode = AdsTransmissionMode.SERVERCYCLE,
        max_delay: int = 1,
        cycle_time: int = 100,
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
        """
        return structs.AdsAddDeviceNotificationRequest(
            index_group,
            index_offset,
            length,
            int(mode),
            max_delay,
            cycle_time,
        )

    def remove_notification(self, handle: int):
        """
        Remove a notification given its handle.

        Parameters
        -----------
        handle : int
            The notification handle.
        """
        return structs.AdsDeleteDeviceNotificationRequest(
            handle=handle
        )

    def get_device_information(self):
        """
        Remove a notification given its handle.

        Parameters
        -----------
        handle : int
            The notification handle.
        """
        return structs.AdsDeviceInfoRequest()

    def get_symbol_info_by_name(
        self,
        name: str,
    ) -> structs.AdsReadWriteRequest:
        """
        Get symbol information by name.

        Parameters
        -----------
        name : str
            The symbol name.
        """
        return structs.AdsReadWriteRequest.create_info_by_name_request(name)

    def get_symbol_handle_by_name(
        self,
        name: str,
    ) -> structs.AdsReadWriteRequest:
        """
        Get symbol handle by name.

        Parameters
        -----------
        name : str
            The symbol name.
        """
        return structs.AdsReadWriteRequest.create_handle_by_name_request(name)

    def release_handle(
        self,
        handle: int,
    ) -> structs.AdsWriteRequest:
        """
        Release a handle by id.

        Parameters
        -----------
        handle : int
            The handle identifier.
        """
        return structs.AdsWriteRequest(
            constants.AdsIndexGroup.SYM_RELEASEHND,
            handle,
            0,
        )

    def get_value_by_handle(
        self,
        handle: int,
        size: int,
    ) -> structs.AdsReadRequest:
        """
        Get symbol value by handle.

        Parameters
        -----------
        handle : int
            The handle identifier.

        size : int
            The size, in bytes, to read.
        """
        return structs.AdsReadRequest(
            constants.AdsIndexGroup.SYM_VALBYHND,
            handle,
            size,
        )


Client._handlers = dict(_aggregate_handlers(Client))
