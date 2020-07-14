import ctypes
import logging
import typing

from . import constants, log, structs

module_logger = logging.getLogger(__name__)

_AMS_HEADER_LENGTH = ctypes.sizeof(structs.AmsTcpHeader)
_AOE_HEADER_LENGTH = ctypes.sizeof(structs.AoEHeader)


def deserialize_buffer(
        buf, *, logger=module_logger
        ) -> typing.Generator[typing.Tuple[structs.AoEHeader, typing.Any], None,
                              None]:
    while len(buf) >= _AMS_HEADER_LENGTH:
        # TCP header / AoE header / frame
        view = memoryview(buf)
        header = structs.AmsTcpHeader.from_buffer(view)
        if header.length < _AOE_HEADER_LENGTH:
            # Not sure?
            logger.warning(
                'Throwing away packet (header.length=%d < %d',
                header.length, _AOE_HEADER_LENGTH
            )
            buf = buf[header.length + _AMS_HEADER_LENGTH:]
            continue

        required_bytes = _AMS_HEADER_LENGTH + header.length
        if len(buf) < required_bytes:
            break

        view = view[_AMS_HEADER_LENGTH:]
        aoe_header = structs.AoEHeader.from_buffer(view)

        required_bytes += aoe_header.length

        if len(buf) < required_bytes:
            break

        view = view[_AOE_HEADER_LENGTH:]
        payload = view[:aoe_header.length]

        yield aoe_header, payload
        buf = buf[required_bytes:]

    return buf


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
        self.log.debug('Handling %s', command)
        if command == constants.AdsCommandId.READ_DEVICE_INFO:
            yield self.server.device_info

    def received_data(self, data):
        self.recv_buffer += data
        for item in deserialize_buffer(self.recv_buffer, logger=self.log):
            self.log.debug('Received %s', item, extra={'direction': '<<<---'})
            yield item

    def send(self, *items):
        bytes_to_send = bytearray()
        for item in items:
            self.log.debug('Sending %s', item, extra={'direction': '--->>>'})


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