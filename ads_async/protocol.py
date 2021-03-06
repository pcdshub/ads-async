import copy
import ctypes
import inspect
import logging
import typing
from typing import Callable, Dict, Generator, Optional, Tuple, Union

from . import constants, log, structs, utils
from .constants import (
    AdsCommandId,
    AdsError,
    AdsIndexGroup,
    AdsTransmissionMode,
    AmsPort,
    AoEHeaderFlag,
    Role,
)
from .exceptions import RequestFailedError
from .structs import AmsNetId
from .symbols import Database, Symbol

module_logger = logging.getLogger(__name__)
IPPort = typing.Tuple[str, int]

# TODO: AMS can be over serial, UDP, etc. and not just TCP
_AMS_HEADER_LENGTH = ctypes.sizeof(structs.AmsTcpHeader)
_AOE_HEADER_LENGTH = ctypes.sizeof(structs.AoEHeader)


class MissingHandlerError(RuntimeError):
    ...


def from_wire(
    buf: bytearray,
    *,
    logger: logging.Logger = module_logger,
) -> Generator[Tuple[structs.AoEHeader, typing.Any], None, None]:
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

    item: structs.T_AdsStructure or memoryview
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
                "Throwing away packet as header.length=%d < %d",
                header.length,
                _AOE_HEADER_LENGTH,
            )
            buf = buf[header.length + _AMS_HEADER_LENGTH :]
            continue

        required_bytes = _AMS_HEADER_LENGTH + header.length
        if len(buf) < required_bytes:
            break

        view = view[_AMS_HEADER_LENGTH:]
        aoe_header = structs.AoEHeader.from_buffer(view)

        view = view[_AOE_HEADER_LENGTH:]
        expected_size = _AMS_HEADER_LENGTH + _AOE_HEADER_LENGTH + aoe_header.length

        buf = buf[required_bytes:]

        if expected_size != required_bytes:
            logger.warning(
                "Throwing away packet as lengths do not add up: "
                "AMS=%d AOE=%d payload=%d -> %d != AMS-header specified %d",
                _AMS_HEADER_LENGTH,
                _AOE_HEADER_LENGTH,
                aoe_header.length,
                expected_size,
                required_bytes,
            )
            item = None
        else:
            try:
                cmd_cls = structs.get_struct_by_command(
                    aoe_header.command_id, request=aoe_header.is_request
                )  # type: structs.T_AdsStructure
                cmd = None
            except KeyError:
                cmd = view[: aoe_header.length]
            else:
                # if hasattr(cmd_cls, 'deserialize'):
                # TODO: can't call super.from_buffer in a subclass
                # classmethod?
                try:
                    cmd = cmd_cls.deserialize(view)
                except Exception as ex:
                    logger.exception(
                        "Deserialization of %s failed: %s bytes=%s (length=%d); "
                        "this may be fatal.",
                        cmd_cls,
                        ex,
                        bytes(view),
                        len(bytes(view)),
                    )
                    continue

            item = (aoe_header, cmd)

        yield required_bytes, item


def response_to_wire(
    *items: structs.T_Serializable,
    request_header: structs.AoEHeader,
    ads_error: AdsError = AdsError.NOERR,
) -> Tuple[list, bytearray]:
    """
    Prepare `items` to be sent over the wire.

    Parameters
    -------
    *items : structs.T_AdsStructure or bytes
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
) -> Tuple[list, bytearray]:
    """
    Prepare `items` to be sent over the wire.

    Parameters
    -------
    *items : structs.T_AdsStructure or bytes
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

    (item,) = items  # TODO

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


def _command_handler(
    cmd: constants.AdsCommandId, index_group: constants.AdsIndexGroup = None
):
    """Decorator to indicate a handler method for the given command/group."""

    def wrapper(method):
        if not hasattr(method, "_handle_commands"):
            method._handles_commands = set()
        method._handles_commands.add((cmd, index_group))
        return method

    return wrapper


class AsynchronousResponse:
    header: structs.AoEHeader
    command: structs.T_AdsStructure
    invoke_id: int
    requester: object

    def __init__(self, header, command, requester):
        self.invoke_id = header.invoke_id
        self.header = header
        self.command = command
        self.requester = requester

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} invoke_id={self.invoke_id} "
            f"command={self.command}>"
        )


class _Connection:
    """
    A Connection represents a TCP connection between server/client.

    A connection may have any number of Circuits.

    Parameters
    ----------
    their_address: tuple of (addr, port)
        The other side's address.

    role : Role
        The role of the connection.

    our_address : tuple of (addr, port), optional
        May be set later based once a connection is established.

    our_net_id : str, optional
        May be set later based on IP address once a connection is established.

    Attributes
    ----------
    circuits : str
        Dictionary of Net ID string to circuit.

    recv_buffer : bytearray
        The receive buffer for the TCP connection.

    circuit_class : type
        The class to create circuits with - as this differs on server/client
        instances.
    """

    _our_address: IPPort
    _our_net_id: str
    circuit_class: type
    circuits: Dict[str, "_Circuit"]
    recv_buffer: bytearray
    role: Role
    their_address: IPPort

    def __init__(
        self,
        their_address: IPPort,
        role: constants.Role,
        our_address: Optional[IPPort] = None,
        our_net_id: Optional[str] = None,
    ):
        self.recv_buffer = bytearray()
        self.circuits = {}
        self.their_address = their_address

        self._log_tags = {
            "role": str(role),
            "their_address": their_address,
        }
        self.log = log.ComposableLogAdapter(module_logger, self._log_tags)
        self.our_net_id = our_net_id
        self.our_address = our_address

    @property
    def our_address(self) -> IPPort:
        """Our address (IP and port of our socket)."""
        return self._our_address

    @our_address.setter
    def our_address(self, our_address):
        our_address = our_address or ("not_yet_set", 0)

        self._our_address = tuple(our_address)
        self._log_tags["our_address"] = self._our_address

    @property
    def our_net_id(self) -> str:
        """Our Net ID."""
        return self._our_net_id

    @our_net_id.setter
    def our_net_id(self, our_net_id: str):
        our_net_id = our_net_id or "not_yet_set"
        self._our_net_id = str(our_net_id)
        self._log_tags["our_net_id"] = self._our_net_id

    def disconnected(self):
        """Disconnection callback."""

    def received_data(self, data):
        """Hook for new data received over the socket."""
        self.recv_buffer += data
        for consumed, item in from_wire(self.recv_buffer, logger=self.log):
            # header, *_ = item
            self.log.debug(
                "%s",
                item,
                extra=dict(
                    direction="<-",
                    bytesize=consumed,
                ),
            )
            self.recv_buffer = self.recv_buffer[consumed:]
            yield item

    def get_circuit(self, net_id: Union[AmsNetId, str]) -> "_Circuit":
        """Get a circuit for (their) Net ID."""
        if isinstance(net_id, AmsNetId):
            net_id = repr(net_id)

        try:
            return self.circuits[net_id]
        except KeyError:
            circuit = self.circuit_class(
                connection=self,
                our_net_id=self.our_net_id,
                their_net_id=net_id,
                tags=self._log_tags,
            )
            self.circuits[net_id] = circuit
            return circuit


class ClientConnection(_Connection):
    """
    One connection from the role/perspective of the client.

    Parameters
    ----------
    our_address : (host, port)
        The host and local port of client socket.

    our_net_id : str
        The AMS Net ID of the client.

    their_address : (host, port)
        The server address.
    """

    def __init__(
        self,
        our_address: IPPort,
        our_net_id: str,
        their_address: IPPort,
    ):
        self.circuit_class = ClientCircuit  # forward definition
        super().__init__(
            our_address=our_address,
            our_net_id=our_net_id,
            their_address=their_address,
            role=Role.Client,
        )


class ServerConnection(_Connection):
    """
    One connection from the role/perspective of the server.

    Parameters
    ----------
    server : Server
        The server instance.

    our_address : (host, port)
        The server socket address.

    our_net_id : str
        The AMS Net ID of the server.

    their_address : (host, port)
        The client socket address.

    Attributes
    ----------
    server : Server
        The server instance.
    """

    server: "Server"

    def __init__(
        self,
        server: "Server",
        our_address: IPPort,
        our_net_id: str,
        their_address: IPPort,
    ):
        self.circuit_class = ServerCircuit  # forward definition
        self.server = server
        super().__init__(
            our_address=our_address,
            our_net_id=our_net_id,
            their_address=their_address,
            role=Role.Server,
        )


class Server:
    """
    An ADS server which manages :class:`ServerConnection` instances.

    This server does not interact with sockets directly.  A layer must be added
    on top to have a functional server.

    See Also
    --------
    :class:`.asyncio.server.AsyncioServer`
    """

    _version: Tuple[int, int, int] = (0, 0, 0)  # TODO: from versioneer
    _name: str
    connections: Dict[IPPort, ServerConnection]
    database: Database
    log: log.ComposableLogAdapter

    def __init__(self, database: Database, net_id: str, *, name="AdsAsync"):
        self._name = name
        self.database = database
        self.connections = {}
        self.net_id = net_id
        self.log = log.ComposableLogAdapter(
            module_logger,
            extra=dict(
                role=Role.Server,
            ),
        )

    def add_connection(
        self,
        our_address: IPPort,
        their_address: IPPort,
    ) -> ServerConnection:
        """
        Add a new client.

        Parameters
        ----------
        our_address : (host, port)
            The host and port the client connected to.

        their_address : (host, port)
            The client address.

        Returns
        -------
        connection : ServerConnection
        """
        connection = ServerConnection(
            server=self,
            our_address=our_address,
            our_net_id=self.net_id,
            their_address=their_address,
        )
        self.connections[their_address] = connection
        self.log.info(
            "New client connection (%d total): %s", len(self.connections), connection
        )
        return connection

    def remove_connection(self, address: IPPort):
        """
        Remove a client by address.

        Parameters
        ----------
        address : (host, port)
            The client address.
        """
        client = self.connections.pop(address)
        self.log.info(
            "Removing client connection (%d total): %s", len(self.connections), client
        )

    @property
    def ads_state(self) -> constants.AdsState:
        """The current state of the server."""
        return constants.AdsState.RUN

    @property
    def version(self) -> Tuple[int, int, int]:
        """The server version."""
        return self._version

    @property
    def name(self) -> str:
        """The server name."""
        return self._name


class _Circuit:
    """
    A Circuit represents a "source net id" to "target net id" connection.

    Parameters
    ----------
    connection : Connection
        The TCP connection.

    tags : dict, optional
        Tags for logging.

    our_net_id : str
        Our AMS NetID.

    our_port : AmsPort
        Defaults to AmsPort.R0_PLC_TC3

    their_net_id : str
        Their AMS NetID.

    their_port : AmsPort
        Defaults to AmsPort.R0_PLC_TC3.
    """

    handle_to_notification: dict
    handle_to_symbol: dict
    log: log.ComposableLogAdapter
    our_port: AmsPort
    tags: dict

    def __init__(
        self,
        connection: "_Connection",
        our_net_id: str,
        their_net_id: str,
        tags: Optional[dict] = None,
        our_port: AmsPort = AmsPort.R0_PLC_TC3,
        their_port: AmsPort = AmsPort.R0_PLC_TC3,
    ):
        self.connection = connection
        self.our_net_id = our_net_id
        self.their_net_id = their_net_id
        self.our_port = our_port
        self.their_port = their_port
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

    def __init_subclass__(cls):
        """For all subclasses, aggregate handlers."""
        super().__init_subclass__()

        cls._handlers = {}
        for attr, obj in inspect.getmembers(cls):
            for handles in getattr(obj, "_handles_commands", []):
                cls._handlers[handles] = getattr(cls, attr)

    def disconnected(self):
        """Disconnected callback."""

    def __repr__(self):
        return (
            f"<{self.__class__.__name__} our_net_id={self.our_net_id} "
            f"their_net_id={self.their_net_id}>"
        )

    def get_handler(
        self, header: structs.AoEHeader, command: Optional[structs.T_AdsStructure]
    ) -> list:
        """
        Top-level command dispatcher.

        First stop to determine which method will handle the given command.

        Parameters
        ----------
        header : structs.AoEHeader
            The header.

        command : structs.T_AdsStructure or None
            The command itself.  May be optional depending on the header's
            AdsCommandId.

        Returns
        -------
        response : list
            List of commands or byte strings to be serialized.
        """
        if hasattr(command, "index_group"):
            keys = [
                (header.command_id, command.index_group),  # type: ignore
                (header.command_id, None),
            ]
        else:
            keys = [(header.command_id, None)]

        for key in keys:
            try:
                return self._handlers[key]
            except KeyError:
                ...

        raise MissingHandlerError(f"No handler defined for command {keys}")

    def response_to_wire(
        self,
        *items: structs.T_Serializable,
        request_header: structs.AoEHeader,
        ads_error: AdsError = AdsError.NOERR,
    ) -> bytearray:
        """
        Prepare `items` to be sent over the wire.

        Parameters
        -------
        *items : structs.T_AdsStructure or bytes
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
            *items, request_header=request_header, ads_error=ads_error
        )

        if self.log.isEnabledFor(logging.DEBUG):
            extra = {
                "direction": "->",
                "sequence": request_header.invoke_id,
                "bytesize": len(bytes_to_send),
                "our_address": tuple(request_header.target),
                "their_address": tuple(request_header.source),
            }
            for idx, item in enumerate(items, 1):
                extra["counter"] = (idx, len(items))
                self.log.debug("%s", item, extra=extra)

        return bytes_to_send

    def request_to_wire(
        self,
        *items: structs.T_Serializable,
        ads_error: AdsError = AdsError.NOERR,
        target: Optional[structs.AmsAddr] = None,
    ) -> bytearray:
        """
        Prepare `items` to be sent over the wire.

        Parameters
        -------
        *items : structs.T_AdsStructure or bytes
            Individual structures or pre-serialized data to send.

        request_header : structs.AoEHeader
            The request to respond to.

        ads_error : AdsError
            The AoEHeader error code to return.

        target : AmsAddr or (addr, port), optional
            Target AMS address.  Defaults to configured server Net ID and
            ``their_port`` (R0_PLC_TC3 or 851).

        Returns
        -------
        data : bytearray
            Serialized data to send.
        """
        if target is None:
            target = structs.AmsAddr(self.server_net_id, self.their_port)
        else:
            target_net_id, target_port = target
            target = structs.AmsAddr(
                structs.AmsNetId.from_string(target_net_id), target_port
            )

        invoke_id = self._invoke_counter()
        assert len(items) == 1  # TODO

        (item,) = items
        self.id_to_request[invoke_id] = item

        items, bytes_to_send = request_to_wire(
            *items,
            target=target,
            source=structs.AmsAddr(
                structs.AmsNetId.from_string(self.our_net_id), self.our_port
            ),
            invoke_id=invoke_id,
        )

        if self.log.isEnabledFor(logging.DEBUG):
            extra = {
                "direction": "->",
                "sequence": invoke_id,
                "bytesize": len(bytes_to_send),
            }
            for idx, item in enumerate(items, 1):
                extra["counter"] = (idx, len(items))
                self.log.debug("%s", item, extra=extra)

        return invoke_id, bytes_to_send


class ClientCircuit(_Circuit):
    """
    Client role - circuit from the perspective of the client.

    Parameters
    ----------
    server_host : (host, port)
        The host and port the client connected to.

    address : (host, port)
        The client address.
    """

    _handle_counter: utils.ThreadsafeCounter
    _notification_counter: utils.ThreadsafeCounter
    _handlers: Dict[Tuple[AdsCommandId, Optional[AdsIndexGroup]], Callable]
    address: IPPort
    server_host: IPPort

    def __init__(
        self,
        connection: ClientConnection,
        our_net_id: str,
        their_net_id: str,
        tags: Optional[dict] = None,
        our_port: AmsPort = AmsPort.R0_PLC_TC3,
        their_port: AmsPort = AmsPort.R0_PLC_TC3,
    ):
        self.unknown_notifications = set()
        tags = dict(tags or {})
        tags.update(
            {
                "role": Role.Client,
                "our_address": (our_net_id, 0),  # TODO
                "their_address": (their_net_id, 0),
            }
        )
        super().__init__(
            connection=connection,
            our_net_id=our_net_id,
            their_net_id=their_net_id,
            tags=tags,
            our_port=our_port,
            their_port=their_port,
        )

    def handle_command(
        self,
        header: structs.AoEHeader,
        response: structs.T_AdsStructure,
    ) -> list:
        """
        Top-level command dispatcher.

        First stop to determine which method will handle the given command.

        Parameters
        ----------
        header : structs.AoEHeader
            The request header.

        response : structs.T_AdsStructure
            The server response.

        Returns
        -------
        response : list
            List of commands or byte strings to be serialized.
        """
        self.log.debug(
            "Handling %s",
            response,
            extra={"sequence": header.invoke_id, "direction": "<-"},
        )

        handler = self.get_handler(header, response)
        request = self.id_to_request.pop(header.invoke_id, None)
        return handler(self, request, header, response)

    def _get_symbol_by_request_handle(self, request) -> Symbol:
        """Get symbol by a request with a handle as its payload."""
        try:
            return self.handle_to_symbol[request.handle]
        except KeyError as ex:
            raise RequestFailedError(
                code=AdsError.CLIENT_INVALIDPARM,  # TODO?
                reason=f"{ex} bad handle",
                request=request,
            ) from None

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
    def _device_notification(
        self, request, header: structs.AoEHeader, stream: structs.AdsNotificationStream
    ):
        for stamp in stream.stamps:
            timestamp = stamp.timestamp
            for sample in stamp.samples:
                try:
                    handler = self.handle_to_notification[sample.notification_handle]
                except KeyError:
                    self.log.debug(
                        "No handler for notification id %s %d?",
                        header.source,
                        sample.notification_handle,
                    )
                    self.unknown_notifications.add(
                        (tuple(header.source), sample.notification_handle)
                    )
                else:
                    handler.process(header=header, timestamp=timestamp, sample=sample)

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
        return structs.AdsDeleteDeviceNotificationRequest(handle=handle)

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


class ServerCircuit(_Circuit):
    """
    Server role - circuit from the perspective of the server.

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
    _handlers: Dict[Tuple[AdsCommandId, Optional[AdsIndexGroup]], Callable]
    address: IPPort
    server: "Server"
    server_host: IPPort
    connection: ServerConnection

    def __init__(
        self,
        connection: ServerConnection,
        our_net_id: str,
        their_net_id: str,
        tags: Optional[dict] = None,
        our_port: AmsPort = AmsPort.R0_PLC_TC3,
        their_port: AmsPort = AmsPort.R0_PLC_TC3,
    ):
        tags = dict(tags or {})
        tags.update(
            {
                "role": Role.Server,
                "our_address": (our_net_id, 0),  # TODO
                "their_address": (their_net_id, 0),
            }
        )
        super().__init__(
            connection=connection,
            our_net_id=our_net_id,
            their_net_id=their_net_id,
            tags=tags,
            our_port=our_port,
            their_port=their_port,
        )
        self.server = connection.server

    def handle_command(
        self, header: structs.AoEHeader, request: Optional[structs.T_AdsStructure]
    ) -> list:
        """
        Top-level command dispatcher.

        First stop to determine which method will handle the given command.

        Parameters
        ----------
        header : structs.AoEHeader
            The request header.

        request : structs.T_AdsStructure or None
            The request itself.  May be optional depending on the header's
            AdsCommandId.

        Returns
        -------
        response : list
            List of commands or byte strings to be serialized.
        """
        self.log.debug(
            "Handling %s", header.command_id, extra={"sequence": header.invoke_id}
        )

        handler = self.get_handler(header, request)
        return handler(self, header, request)

    def _get_symbol_by_request_name(self, request) -> Symbol:
        """Get symbol by a request with a name as its payload."""
        symbol_name = structs.byte_string_to_string(request.data)
        try:
            return self.server.database.get_symbol_by_name(symbol_name)
        except KeyError:
            raise RequestFailedError(
                code=AdsError.DEVICE_SYMBOLNOTFOUND,
                reason=f"{symbol_name!r} not in database",
                request=request,
            ) from None

    def _get_symbol_by_request_handle(self, request) -> Symbol:
        """Get symbol by a request with a handle as its payload."""
        try:
            return self.handle_to_symbol[request.handle]
        except KeyError as ex:
            raise RequestFailedError(
                code=AdsError.CLIENT_INVALIDPARM,  # TODO?
                reason=f"{ex} bad handle",
                request=request,
            ) from None

    @_command_handler(AdsCommandId.ADD_DEVICE_NOTIFICATION)
    def _add_notification(
        self,
        header: structs.AoEHeader,
        request: structs.AdsAddDeviceNotificationRequest,
    ):
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
        self,
        header: structs.AoEHeader,
        request: structs.AdsDeleteDeviceNotificationRequest,
    ):
        self.handle_to_notification.pop(request.handle)
        return [structs.AoEResponseHeader(AdsError.NOERR)]

    @_command_handler(AdsCommandId.WRITE_CONTROL)
    def _write_control(
        self, header: structs.AoEHeader, request: structs.AdsWriteControlRequest
    ):
        raise NotImplementedError("write_control")

    @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_RELEASEHND)
    def _write_release_handle(
        self, header: structs.AoEHeader, request: structs.AdsWriteRequest
    ):
        handle = request.handle
        self.handle_to_symbol.pop(handle, None)
        return [structs.AoEResponseHeader()]

    @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_VALBYHND)
    @_command_handler(AdsCommandId.WRITE, AdsIndexGroup.SYM_VALBYNAME)
    def _write_value(self, header: structs.AoEHeader, request: structs.AdsWriteRequest):
        if request.index_group == AdsIndexGroup.SYM_VALBYHND:
            symbol = self._get_symbol_by_request_handle(request)
        else:
            symbol = self._get_symbol_by_request_name(request)

        old_value = repr(symbol.value)
        symbol.write(request.data)
        self.log.debug(
            "Writing symbol %s old value: %s new value: %s",
            symbol,
            old_value,
            symbol.value,
        )
        return [structs.AoEResponseHeader()]

    @_command_handler(AdsCommandId.READ, AdsIndexGroup.SYM_VALBYHND)
    @_command_handler(AdsCommandId.READ, AdsIndexGroup.SYM_VALBYNAME)
    def _read_value(self, header: structs.AoEHeader, request: structs.AdsReadRequest):
        if request.index_group == AdsIndexGroup.SYM_VALBYHND:
            symbol = self._get_symbol_by_request_handle(request)
        else:
            symbol = self._get_symbol_by_request_name(request)

        return [structs.AoEReadResponse(data=symbol.read())]

    @_command_handler(AdsCommandId.READ)
    def _read_catchall(
        self, header: structs.AoEHeader, request: structs.AdsReadRequest
    ):
        try:
            data_area = self.server.database.index_groups[request.index_group]
        except KeyError:
            raise RequestFailedError(
                code=AdsError.DEVICE_INVALIDACCESS,  # TODO?
                reason=f"Invalid index group: {request.index_group}",
                request=request,
            ) from None
        else:
            data = data_area.memory.read(request.index_offset, request.length)
            return [structs.AoEReadResponse(data=data)]

    @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SYM_HNDBYNAME)
    def _read_write_handle(
        self, header: structs.AoEHeader, request: structs.AdsReadWriteRequest
    ):
        symbol = self._get_symbol_by_request_name(request)
        handle = self._handle_counter()
        self.handle_to_symbol[handle] = symbol
        return [structs.AoEHandleResponse(result=AdsError.NOERR, handle=handle)]

    @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SYM_INFOBYNAMEEX)
    def _read_write_info(
        self, header: structs.AoEHeader, request: structs.AdsReadWriteRequest
    ):
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
            comment=symbol.__doc__ or "",
        )
        return [structs.AoEReadResponse(data=symbol_entry)]

    @_command_handler(AdsCommandId.READ_WRITE, AdsIndexGroup.SUMUP_READ)
    def _read_write_sumup_read(
        self, header: structs.AoEHeader, request: structs.AdsReadWriteRequest
    ):
        read_size = ctypes.sizeof(structs.AdsReadRequest)
        count = len(request.data) // read_size
        self.log.debug("Request to read %d items", count)
        buf = bytearray(request.data)

        results = []
        data = []

        # Handle these as normal reads by making a fake READ header:
        read_header = copy.copy(header)
        read_header.command_id = AdsCommandId.READ

        for idx in range(count):
            read_req = structs.AdsReadRequest.from_buffer(buf)
            read_response = self.handle_command(read_header, read_req)
            self.log.debug("[%d] %s -> %s", idx + 1, read_req, read_response)
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

        return [
            structs.AoEReadResponse(
                data=b"".join(structs.serialize(res) for res in to_send)
            )
        ]

    @_command_handler(AdsCommandId.READ_WRITE)
    def _read_write_catchall(
        self, header: structs.AoEHeader, request: structs.AdsReadWriteRequest
    ):
        ...

    @_command_handler(AdsCommandId.READ_DEVICE_INFO)
    def _read_device_info(self, header: structs.AoEHeader, request):
        return [
            structs.AoEResponseHeader(AdsError.NOERR),
            structs.AdsDeviceInfo(*self.server.version, name=self.server.name),
        ]

    @_command_handler(AdsCommandId.READ_STATE)
    def _read_state(self, header: structs.AoEHeader, request):
        return [
            structs.AoEResponseHeader(AdsError.NOERR),
            structs.AdsReadStateResponse(self.server.ads_state, 0),  # TODO: find docs
        ]
