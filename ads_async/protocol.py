import ctypes
import logging
import typing

from . import constants, log, structs, utils
from .constants import AdsCommandId, AdsError
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

    # TODO: better way around this? would like to bake it into __bytes__
    items_serialized = [item.serialize()
                        if hasattr(item, 'serialize')
                        else bytes(item)
                        for item in items]

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


class AcceptedClient:
    # Client only from perspective of server, for now

    def __init__(self, server, server_host, address):
        self.recv_buffer = bytearray()
        self.server = server
        self.server_host = server_host
        self.address = address
        self.handle_to_symbol = {}
        # self.symbol_to_handle = {}
        self._handle_counter = utils.ThreadsafeCounter(
            max_count=2 ** 32,  # handles are uint32
            dont_clash_with=self.handle_to_symbol,
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

    def _handle_read_write(self, header: structs.AoEHeader,
                           request: structs.AdsReadWriteRequest):
        def get_symbol_by_name() -> Symbol:
            symbol_name = request.data_as_symbol_name
            return self.server.database.get_symbol_by_name(symbol_name)

        if request.index_group == constants.AdsIndexGroup.SYM_HNDBYNAME:
            try:
                symbol = get_symbol_by_name()
            except KeyError as ex:
                return ErrorResponse(code=AdsError.DEVICE_SYMBOLNOTFOUND,
                                     reason=f'{ex} not in database')

            handle = self._handle_counter()
            self.handle_to_symbol[handle] = symbol
            return [structs.AoEHandleResponse(result=AdsError.NOERR,
                                              handle=handle)]
        elif request.index_group == constants.AdsIndexGroup.SYM_INFOBYNAMEEX:
            try:
                symbol = get_symbol_by_name()
            except KeyError as ex:
                return ErrorResponse(code=AdsError.DEVICE_SYMBOLNOTFOUND,
                                     reason=f'{ex} not in database')

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
            # TODO: double serialization
            return [
                structs.AoEReadResponseHeader(
                    read_length=len(symbol_entry.serialize())),
                symbol_entry
            ]

        return AsynchronousResponse(header, request, self)

    def handle_command(self, header: structs.AoEHeader,
                       request: typing.Optional[structs._AdsStructBase]):
        command = header.command_id
        self.log.debug('Handling %s', command,
                       extra={'sequence': header.invoke_id})
        if command == AdsCommandId.READ_DEVICE_INFO:
            return [structs.AoEResponseHeader(AdsError.NOERR),
                    structs.AdsDeviceInfo(*self.server.version,
                                          name=self.server.name)
                    ]
        if command == AdsCommandId.READ_STATE:
            return [structs.AoEResponseHeader(AdsError.NOERR),
                    structs.AdsReadStateResponse(self.server.ads_state,
                                                 0  # TODO: find docs
                                                 )
                    ]
        if command == AdsCommandId.READ_WRITE:
            return self._handle_read_write(header, request)
        if command in {AdsCommandId.ADD_DEVICE_NOTIFICATION,
                       AdsCommandId.DEL_DEVICE_NOTIFICATION,
                       AdsCommandId.DEVICE_NOTIFICATION,
                       AdsCommandId.READ,
                       AdsCommandId.WRITE,
                       AdsCommandId.WRITE_CONTROL}:
            return AsynchronousResponse(header, request, self)

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
            extra['counter'] = (idx, len(items) + 1)
            self.log.debug('%s', item, extra=extra)

        return bytes_to_send


class ErrorResponse:
    code: AdsError
    reason: str

    def __init__(self, code: AdsError, reason: str = ''):
        self.code = code
        self.reason = reason

    def __repr__(self):
        return f'<ErrorResponse {self.code} ({self.reason})>'


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


def serialize_data(data_type: constants.AdsDataType, data: typing.Any):
    # TODO: endian swapping for data? :(
    ...
