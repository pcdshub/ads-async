import ctypes
import logging
import typing

from . import constants, log, structs

module_logger = logging.getLogger(__name__)

_AMS_HEADER_LENGTH = ctypes.sizeof(structs.AmsTcpHeader)
_AOE_HEADER_LENGTH = ctypes.sizeof(structs.AoEHeader)


def from_wire(buf, *, logger=module_logger
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

        yield buf, item


class AcceptedClient:
    # Client only from perspective of server, for now

    def __init__(self, server, server_host, address):
        self.recv_buffer = bytearray()
        self.server = server
        self.server_host = server_host
        self.address = address
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

    def handle_command(self, header: structs.AoEHeader, data):
        command = header.command_id
        self.log.debug('Handling %s', command,
                       extra={'sequence': header.invoke_id})
        if command == constants.AdsCommandId.READ_DEVICE_INFO:
            yield self.server.device_info
        elif command == constants.AdsCommandId.READ_WRITE:
            ...
        elif command == constants.AdsCommandId.READ:
            ...
        elif command == constants.AdsCommandId.WRITE:
            ...
        elif command == constants.AdsCommandId.READ_STATE:
            ...
        elif command == constants.AdsCommandId.WRITE_CONTROL:
            ...
        elif command == constants.AdsCommandId.ADD_DEVICE_NOTIFICATION:
            ...
        elif command == constants.AdsCommandId.DEL_DEVICE_NOTIFICATION:
            ...
        elif command == constants.AdsCommandId.DEVICE_NOTIFICATION:
            ...

    def received_data(self, data):
        self.recv_buffer += data
        for buf, item in from_wire(self.recv_buffer, logger=self.log):
            self.log.debug('Received %s', item,
                           extra={'direction': '<<<---'})
            self.recv_buffer = buf
            yield item

    def response_to_wire(
            self, item: structs._AdsStructBase,
            request_header: structs.AoEHeader,
            ads_error: constants.AdsError = constants.AdsError.NOERR
            ) -> bytearray:
        request_id = request_header.invoke_id

        extra = {
            'direction': '--->>>',
            'sequence': request_id,
        }

        bytes_to_send = bytearray()
        self.log.debug('Response %s', item, extra=extra)

        item_length = ctypes.sizeof(item)
        aoe_header = structs.AoEHeader.create_response(
            target=request_header.source,
            source=request_header.target,
            command_id=item._command_id,
            invoke_id=request_id,
            length=item_length,
        )

        ams_tcp_header = structs.AmsTcpHeader(_AOE_HEADER_LENGTH + item_length)

        # TODO can multiple AoEHeaders be in a single AMS/TCP packet?
        bytes_to_send = bytearray(ams_tcp_header)
        bytes_to_send.extend(aoe_header)
        bytes_to_send.extend(structs.AoEResponseHeader(ads_error))
        bytes_to_send.extend(item)
        return bytes_to_send


class Server:
    _version = (0, 0, 0)  # TODO: from versioneer

    def __init__(self, *, name='AdsAsync'):  # , frame_queue, database):
        self.frame_queue = None  # frame_queue
        self.database = None  # database
        self.clients = {}
        tags = {
            'role': 'SERVER',
        }

        self.log = log.ComposableLogAdapter(module_logger, tags)
        self.device_info = structs.AdsDeviceInfo(*self._version,
                                                 name=name)

    def add_client(self, server_host, address):
        client = AcceptedClient(self, server_host, address)
        self.clients[address] = client
        self.log.info('New client (%d total): %s', len(self.clients), client)
        return client

    def remove_client(self, address):
        client = self.clients.pop(address)
        self.log.info('Removing client (%d total): %s', len(self.clients),
                      client)

    def received_frame(self, frame):
        self.frame_queue.put(frame)
