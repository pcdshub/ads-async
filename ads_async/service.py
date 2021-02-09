"""
System service management protocol.

Communication should be done a layer up using UDP packets.

This protocol is undocumented and this implementation is incomplete.

Note: The API may change significantly here if the protocol reverse engineering
- or documentation - ever gets fleshed out.
"""
import enum
import logging
import struct
from typing import Optional

from . import constants
from .structs import AmsNetId

logger = logging.getLogger(__name__)


def _null_terminate(value: str) -> str:
    """Null terminate the given string."""
    if "\x00" in value:
        value = value.split("\x00")[0]
    return f"{value}\x00"


def serialize_string(value: str, encoding: str) -> bytes:
    """Serialize a string (null-terminated with length)."""
    value = _null_terminate(value)
    return b"".join(
        (
            struct.pack("<H", len(value)),
            value.encode(encoding),
        )
    )


def deserialize_string(data: bytes, encoding: str) -> str:
    """Deserialize a string (null-terminated with length)."""
    (length,) = struct.unpack("<H", data[:2])
    return str(data[2 : 2 + length - 1], encoding)


class BadResponse(Exception):
    """Mismatched/bad response to the given packet."""


class SystemServiceRequestCommand(enum.IntEnum):
    GET_INFO = 1
    ADD_ROUTE = 6


class SystemService:
    """
    This class helps assemble packets for communicating with the system service
    port.

    It does not include UDP handling, which must be done on top.

    It is incomplete, based on previous reverse-engineering efforts of those
    from pyads contributors - copyright 2015 by Stefan Lehmann, under the MIT
    license.

    Ref: https://github.com/stlehmann/pyads/blob/master/pyads/pyads_ex.py
    """

    string_encoding = "ascii"
    REQUEST_MAGIC = b"\x03\x66\x14\x71\x00\x00\x00\x00"
    RESPONSE_MAGIC = b"\x03\x66\x14\x71\x00\x00\x00\x00"

    def create_request_header(
        self,
        source_net_id: str,
        request_id: SystemServiceRequestCommand,
        port: int = constants.SYSTEM_SERVICE_PORT,
    ) -> bytes:
        """
        Create a system service header.

        Parameters
        ----------
        source_net_id : str
            Source Net ID.
        request_id : SystemServiceRequestCommand
            The request type identifier
        port : int, optional
            Service port.

        Returns
        -------
        bytes
            Header packet.
        """
        return b"".join(
            (
                self.REQUEST_MAGIC,
                # Fixed header (may be "magic", may be meaningful)
                struct.pack("<H", request_id),
                # It's possible the request id is 1 or 4 bytes, but this is
                # marked as padding for now:
                b"\x00\x00",
                AmsNetId.from_string(source_net_id).serialize(),
                # Service port
                struct.pack("<H", port),
            )
        )

    def deserialize_header(
        self,
        data: bytes,
        addr,
    ) -> bytes:
        """
        Deserialize a system service header.

        Parameters
        ----------
        data : bytes
            Header packet.

        addr : (addr, port)
            Source address and port.

        Returns
        -------
        info : dict
            Header and packet information.
        """
        if len(data) < 22:
            raise BadResponse("Packet not long enough")
        if data[: len(self.RESPONSE_MAGIC)] != self.RESPONSE_MAGIC:
            raise BadResponse("Response header magic missing")
        if data[11] != 0x80:
            raise BadResponse("Response marker missing")

        return dict(
            command_id=SystemServiceRequestCommand(struct.unpack("<H", data[8:10])[0]),
            source_net_id=repr(AmsNetId.from_buffer_copy(data[12:18])),
            source_ams_port=struct.unpack("<H", data[18:20])[0],
            source_addr=addr,
            payload=data[22:],
            # This may be a sequence identifier?
            unknown_response_id=struct.unpack("<H", data[20:22])[0],
        )

    def deserialize_response(
        self,
        data: bytes,
        addr=None,
    ) -> bytes:
        """
        Create a system service header.

        Parameters
        ----------
        data : bytes
            Header packet.

        Returns
        -------
        """
        header_info = self.deserialize_header(data, addr)

        command_id = SystemServiceRequestCommand(struct.unpack("<H", data[8:10])[0])
        if command_id == SystemServiceRequestCommand.GET_INFO:
            result = self.deserialize_get_info_response(data, addr)
            header_info.pop("payload")
        elif command_id == SystemServiceRequestCommand.ADD_ROUTE:
            result = self.deserialize_add_route_response(data, addr)
            header_info.pop("payload")
        else:
            result = {}

        return dict(
            **header_info,
            **result,
        )

    def add_route_to_plc(
        self,
        source_net_id: str,
        source_name: str,
        username: str = "Administrator",
        password: str = "1",
        route_name: Optional[str] = None,
        net_id_to_add: Optional[str] = None,
    ) -> bytes:
        """
        Add a new route to a PLC.

        Parameters
        ----------
        source_net_id : str
            Source Net ID.
        source_name : str
            Hostname or IP of the route.
        username : str, optional
            Username for PLC (TwinCAT3 default 'Administrator').
        password : str, optional
            Password for PLC (TwinCAT3 default '1').
        route_name : str, optional
            PLC side name for route, defaults to source_name.
        net_id_to_add : str, optional
            Net ID that is being added to the PLC, defaults to source_net_id.

        Returns
        -------
        bytes
            Packet to send to request the new route.
        """
        net_id_to_add = net_id_to_add or source_net_id
        route_name = route_name or source_name

        # The head of the UDP AMS packet containing host routing information
        header = self.create_request_header(
            source_net_id=source_net_id,
            request_id=SystemServiceRequestCommand.ADD_ROUTE,
        )
        return b"".join(
            (
                header,
                # Write command
                b"\x05\x00",
                # Block of unknown
                b"\x00\x00\x0c\x00",
                # Sender host name
                serialize_string(source_name, self.string_encoding),
                # Block of unknown
                b"\x07\x00",
                # Byte length of AMS ID (6 octets)
                struct.pack("<H", 6),
                # Net ID being added to the PLC
                AmsNetId.from_string(net_id_to_add).serialize(),
                # Block of unknown
                struct.pack(">2s", b"\x0d\x00"),
                # PLC Username
                serialize_string(username, self.string_encoding),
                # Block of unknown
                struct.pack(">2s", b"\x02\x00"),
                # PLC Password
                serialize_string(password, self.string_encoding),
                # Block of unknown
                struct.pack(">2s", b"\x05\x00"),
                # Route name
                serialize_string(route_name, self.string_encoding),
            )
        )

    def deserialize_add_route_response(self, data: bytes, addr):
        """
        Parse an add route response message.

        Raises
        ------
        BadResponse
            If the response does not match the request for adding a route.
        """

        assert len(data) == 32
        # The response to the request
        payload = data[22:]

        result = dict(
            password_correct=(payload[4:7] == b"\x04\x00\x00"),
            authentication_error=(payload[4:7] == b"\x00\x04\x07"),
        )
        # 0x040000 when password was correct, 0x000407 when it was incorrect
        if not result["password_correct"] and not result["authentication_error"]:
            ex = BadResponse("Route may or may not have been added")
            ex.result = result
            raise ex
        return result

    def deserialize_get_info_response(self, data: bytes, addr) -> dict:
        """Deserialize GET_NET_ID payload."""
        return {
            "plc_name": deserialize_string(data[26:], self.string_encoding),
        }

    def get_info(self, source_net_id="1.1.1.1.1.1") -> bytes:
        """
        Get the AMS Net ID of the target PLC.

        Parameters
        ----------
        source_net_id : str, optional
            Optional net ID.  Not required to be valid.
        """
        return b"".join(
            (
                self.create_request_header(
                    source_net_id=source_net_id,
                    request_id=SystemServiceRequestCommand.GET_INFO,
                ),
                # Empty payload?
                b"\x00\x00\x00\x00",
            )
        )


# TODO these will be moved out to some console entrypoint tool


def send_and_receive_udp(
    host: str,
    packet: bytes,
    port: int = constants.ADS_UDP_SERVER_PORT,
    recv_length: int = 1024,
):
    """
    Send a UDP packet by way of stdlib socket, and yield responses.

    Parameters
    ----------
    host : str
        The hostname.

    packet : bytes
        The packet to send.

    port : int, optional
        The UDP port to send to.

    recv_length : int, optional
        The receive buffer size.
    """
    import socket

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # Bind before send is necessary at least on win32
        sock.bind(("", 0))
        sock.sendto(packet, (host, port))
        sock.settimeout(1)

        while True:
            try:
                yield sock.recvfrom(recv_length)
            except socket.timeout:
                # Timeout, but give caller the option to retry
                raise TimeoutError()
                sock.sendto(packet, (host, port))
    finally:
        sock.close()


def add_route_to_plc(
    plc_hostname: str,
    source_net_id: str,
    source_name: str,
    username: str = "Administrator",
    password: str = "1",
    route_name: Optional[str] = None,
    net_id_to_add: Optional[str] = None,
) -> dict:
    """
    Add a new static route to a PLC.

    Parameters
    ----------
    plc_hostname: str
        The PLC IP / hostname.
    source_net_id : str
        Sourec net id.
    source_name : str
        host name (or IP) of the PC being added.
    username : str, optional
        Username for PLC (TwinCAT3 default 'Administrator').
    password : str, optional
        Password for PLC (TwinCAT3 default '1').
    route_name : str, optional
        PLC side name for route, defaults to source_name.
    net_id_to_add : str, optional
        net id that is being added to the PLC, defaults to source_net_id.

    Returns
    -------
    info : dict
        Information dictionary of the response.
    """
    svc = SystemService()
    to_send = svc.add_route_to_plc(
        source_net_id=source_net_id,
        source_name=source_name,
        username=username,
        password=password,
        route_name=route_name,
        net_id_to_add=net_id_to_add,
    )

    logger.debug("Sending %s to %s", to_send, plc_hostname)
    for recv in send_and_receive_udp(plc_hostname, to_send):
        if isinstance(recv, TimeoutError):
            # TODO max timeout/resend/etc
            logger.warning("Timeout; waiting")
            continue
        try:
            packet, addr = recv
            result = svc.deserialize_response(packet, addr)
            if result["command_id"] != SystemServiceRequestCommand.ADD_ROUTE:
                raise BadResponse("Not matching response")
            return result
        except BadResponse as ex:
            logger.warning("Got bad response; waiting: %s", ex)
            logger.debug("Got bad response; waiting: %s", ex, exc_info=ex)


def get_plc_net_id(
    plc_hostname: str,
) -> str:
    """
    Get a PLC's Net ID from its IP address.

    Parameters
    ----------
    plc_hostname: str
        The PLC IP / hostname.

    Returns
    -------
    net_id : str
        The Net ID.
    """
    svc = SystemService()
    to_send = svc.get_info()

    logger.debug("Sending %s to %s", to_send, plc_hostname)
    for recv in send_and_receive_udp(plc_hostname, to_send):
        if isinstance(recv, TimeoutError):
            # TODO max timeout/resend/etc
            logger.warning("Timeout; waiting")
            continue
        try:
            packet, addr = recv
            result = svc.deserialize_response(packet, addr)
            if result["command_id"] != SystemServiceRequestCommand.GET_INFO:
                raise BadResponse("Not matching response")
            return result
        except BadResponse as ex:
            logger.warning("Got bad response; waiting: %s", ex)
            logger.debug("Got bad response; waiting: %s", ex, exc_info=ex)


if __name__ == "__main__":
    svc = SystemService()
    print(
        add_route_to_plc(
            "plc-tst-proto6.pcdsn",
            source_net_id="1.1.1.1.1.1",
            source_name="my_host",
        )
    )
    print(
        get_plc_net_id(
            "plc-tst-proto6.pcdsn",
        )
    )
