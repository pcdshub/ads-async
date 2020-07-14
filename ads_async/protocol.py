import ctypes
import logging

from . import structs

logger = logging.getLogger(__name__)

_AMS_HEADER_LENGTH = ctypes.sizeof(structs.AmsTcpHeader)
_AOE_HEADER_LENGTH = ctypes.sizeof(structs.AoEHeader)


def deserialize_buffer(buf):
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


class Client:
    def __init__(self, server):
        self.recv_buffer = bytearray()
        self.server = server

    def disconnected(self):
        ...

    def received_data(self, data):
        self.recv_buffer += data
        for item in deserialize_buffer(self.recv_buffer):
            print('item', item)


class Server:
    def __init__(self):  # , frame_queue, database):
        self.frame_queue = None  # frame_queue
        self.database = None  # database
        self.clients = {}

    def add_client(self, identifier):
        client = Client(identifier)
        self.clients[identifier] = client
        return client

    def remove_client(self, identifier):
        self.clients.pop(identifier)

    def received_frame(self, frame):
        self.frame_queue.put(frame)
