import asyncio
import ctypes
import logging
import sys
# TODO move to asyncio
import threading

from . import constants, structs

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


if sys.version_info < (3, 7):
    # python <= 3.6 compatibility
    def get_running_loop():
        return asyncio.get_event_loop()

    def run(coro, debug=False):
        return get_running_loop().run_until_complete(coro)

    def create_task(coro):
        return get_running_loop().create_task(coro)

else:
    get_running_loop = asyncio.get_running_loop
    run = asyncio.run
    create_task = asyncio.create_task


class _TaskHandler:
    def __init__(self):
        self.tasks = []
        self._lock = threading.Lock()

    def create(self, coro):
        """Schedule the execution of a coroutine object in a spawn task."""
        task = create_task(coro)
        with self._lock:
            self.tasks.append(task)
        task.add_done_callback(self._remove_completed_task)
        return task

    def _remove_completed_task(self, task):
        try:
            with self._lock:
                self.tasks.remove(task)
        except ValueError:
            # May have been cancelled or removed otherwise
            ...

    async def cancel(self, task):
        task.cancel()
        await task

    async def cancel_all(self, wait=False):
        with self._lock:
            tasks = list(self.tasks)
            self.tasks.clear()

        for task in tasks:
            task.cancel()

        if wait and tasks:
            await asyncio.wait(tasks)

    async def wait(self):
        with self._lock:
            tasks = list(self.tasks)

        if tasks:
            await asyncio.wait(tasks)


class AsyncioServer:
    def __init__(self,
                 port: int = constants.ADS_TCP_SERVER_PORT,
                 hosts: list = None):
        self._port = port
        self._hosts = hosts or [None]
        self._tasks = _TaskHandler()
        self._running = False
        self._shutdown_event = asyncio.Event()
        self.server = Server()

    async def start(self):
        if self._running:
            return

        self._running = True
        self._shutdown_event.clear()
        for interface in self._hosts:
            await asyncio.start_server(
                self.new_client,
                host=interface,
                port=self._port,
            )

    async def stop(self):
        if self._running:
            await self._tasks.cancel_all(wait=True)
            self._shutdown_event.set()
            self._running = False

    async def serve_forever(self):
        await self._shutdown_event.wait()

    async def new_client(self, reader, writer):
        print('new client', reader, writer)
        client_key = (reader, writer)
        client = self.server.add_client(client_key)
        while True:
            data = await reader.read(1024)
            if not len(data):
                client.disconnected()
                break
            else:
                client.received_data(data)

        self.server.remove_client(client_key)


if __name__ == '__main__':
    async def test():
        server = AsyncioServer()
        await server.start()
        await server.serve_forever()

    asyncio.run(test())
