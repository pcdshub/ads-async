import copy
import ctypes
import inspect
import logging
import typing

from . import constants, log, structs, utils
from .constants import AdsCommandId, AdsError, AdsIndexGroup
from .symbols import Database, Symbol

module_logger = logging.getLogger(__name__)

# TODO: AMS can be over serial, UDP, etc. and not just TCP
_AMS_HEADER_LENGTH = ctypes.sizeof(structs.AmsTcpHeader)
_AOE_HEADER_LENGTH = ctypes.sizeof(structs.AoEHeader)


def from_wire(buf, *, logger=module_logger,
              ) -> typing.Generator[
                      typing.Tuple[structs.AoEHeader, typing.Any], None, None]:

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
                    aoe_header.command_id, request=aoe_header.is_request)
                cmd = None
            except KeyError:
                cmd = view[:aoe_header.length]
            else:
                if hasattr(cmd_cls, 'from_buffer_extended'):
                    # TODO: can't call super.from_buffer in a subclass
                    # classmethod?
                    cmd = cmd_cls.from_buffer_extended(view)
                else:
                    cmd = cmd_cls.from_buffer(view)

            item = (aoe_header, cmd)

        yield required_bytes, item


def response_to_wire(
        *items: structs._AdsStructBase,
        request_header: structs.AoEHeader,
        ads_error: AdsError = AdsError.NOERR
        ) -> typing.Tuple[list, bytearray]:

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


class Notification:
    handle: int
    symbol: Symbol

    def __init__(self, handle: int, symbol: Symbol):
        self.handle = handle
        self.symbol = symbol


def _command_handler(cmd: constants.AdsCommandId,
                     index_group: constants.AdsIndexGroup = None):
    def wrapper(method):
        if not hasattr(method, '_handle_commands'):
            method._handles_commands = set()
        method._handles_commands.add((cmd, index_group))
        return method
    return wrapper


class AcceptedClient:
    # Client only from perspective of server, for now

    def __init__(self, server, server_host, address):
        self.recv_buffer = bytearray()
        self.server = server
        self.server_host = server_host
        self.address = address
        self.handle_to_symbol = {}
        self.handle_to_notification = {}
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

        tags = {
            'role': 'CLIENT',
            'our_address': address,
            'their_address': server_host or ('0.0.0.0', 0),
        }
        self.log = log.ComposableLogAdapter(module_logger, tags)

    def __repr__(self):
        return (
            f'<{self.__class__.__name__} address={self.address} '
            f'server_host={self.server_host}>'
        )

    def disconnected(self):
        ...

    def _get_symbol_by_request_name(self, request) -> Symbol:
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
        symbol_entry = structs.AdsSymbolEntry(
            index_group=symbol.data_area.index_group.value,  # TODO
            index_offset=symbol.offset,
            size=symbol.size,
            data_type=symbol.data_type,
            flags=0,  # TODO symbol.flags
            name=symbol.name,
            type_name=symbol.data_type.name,
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

    def handle_command(self, header: structs.AoEHeader,
                       request: typing.Optional[structs._AdsStructBase]):
        command = header.command_id
        self.log.debug('Handling %s', command,
                       extra={'sequence': header.invoke_id})

        if hasattr(request, 'index_group'):
            keys = [(command, request.index_group), (command, None)]
        else:
            keys = [(command, None)]

        for key in keys:
            try:
                handler = self._handlers[key]
            except KeyError:
                ...
            else:
                return handler(self, header, request)

        raise RuntimeError(f'No handler defined for command {keys}')

    def received_data(self, data):
        self.recv_buffer += data
        for consumed, item in from_wire(self.recv_buffer, logger=self.log):
            self.log.debug('%s', item,
                           extra={'direction': '<<<---',
                                  'bytesize': consumed})
            self.recv_buffer = self.recv_buffer[consumed:]
            yield item

    def response_to_wire(
            self, *items: structs._AdsStructBase,
            request_header: structs.AoEHeader,
            ads_error: AdsError = AdsError.NOERR
            ) -> bytearray:

        items, bytes_to_send = response_to_wire(
            *items,
            request_header=request_header,
            ads_error=ads_error
        )

        extra = {
            'direction': '--->>>',
            'sequence': request_header.invoke_id,
            'bytesize': len(bytes_to_send),
        }
        for idx, item in enumerate(items, 1):
            extra['counter'] = (idx, len(items))
            self.log.debug('%s', item, extra=extra)

        return bytes_to_send


def _aggregate_handlers(cls: type) -> dict:
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
    _version = (0, 0, 0)  # TODO: from versioneer

    def __init__(self, database: Database, *, name='AdsAsync'):
        self._name = name
        self.database = database
        self.clients = {}
        tags = {
            'role': 'SERVER',
        }

        self.log = log.ComposableLogAdapter(module_logger, tags)

    def add_client(self, server_host, address):
        client = AcceptedClient(self, server_host, address)
        self.clients[address] = client
        self.log.info('New client (%d total): %s', len(self.clients), client)
        return client

    def remove_client(self, address):
        client = self.clients.pop(address)
        self.log.info('Removing client (%d total): %s', len(self.clients),
                      client)

    @property
    def ads_state(self) -> constants.AdsState:
        return constants.AdsState.RUN

    @property
    def version(self) -> typing.Tuple[int, int, int]:
        return self._version

    @property
    def name(self) -> str:
        return self._name
