"""
System service management protocol.

Communication should be done a layer up using UDP packets.

This protocol is undocumented and this implementation is incomplete.
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


class BadResponse(Exception):
    """Mismatched/bad response to the given packet."""


class SystemServiceRequestID(enum.IntEnum):
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

    def create_header(
        self,
        source_net_id: str,
        request_id: SystemServiceRequestID,
        port: int = constants.SYSTEM_SERVICE_PORT,
    ) -> bytes:
        """
        Create a system service header.

        Parameters
        ----------
        source_net_id : str
            Source Net ID.
        request_id : SystemServiceRequestID
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
                # Fixed header (may be "magic", may be meaningful)
                b"\x03\x66",
                b"\x14\x71" b"\x00\x00\x00\x00",
                struct.pack("<H", request_id),
                # It's possible the request id is 1 or 4 bytes, but this is
                # marked as padding for now:
                b"\x00\x00",
                AmsNetId.from_string(source_net_id).serialize(),
                # Service port
                struct.pack("<H", port),
            )
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
        header = self.create_header(
            source_net_id=source_net_id,
            request_id=SystemServiceRequestID.ADD_ROUTE,
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

    def parse_add_route_response(self, data: bytes, addr):
        """
        Parse an add route response message.

        Raises
        ------
        BadResponse
            If the response does not match the request for adding a route.
        """

        assert len(data) == 32
        # AMS Packet header defines communication type
        header = data[0:12]
        # If the last byte in the header is 0x80, then this is a response to
        # our request somehow!
        if header[-1] != 0x80:
            raise BadResponse("Not a matching response")

        # The response to the request
        response = data[22:]

        result = dict(
            source=addr,
            # Convert to a String AMS ID
            ams_id=repr(AmsNetId.from_buffer_copy(data[12:18])),
            # Some sort of AMS port? Little endian
            ams_port=struct.unpack("<H", data[18:20])[0],
            # Command code?
            command_code=struct.unpack("<2s", data[20:22])[0],
            password_correct=(response[4:7] == b"\x04\x00\x00"),
            authentication_error=(response[4:7] == b"\x00\x04\x07"),
        )
        # 0x040000 when password was correct, 0x000407 when it was incorrect
        if not result["password_correct"] and not result["authentication_error"]:
            ex = BadResponse("Route may or may not have been added")
            ex.result = result
            raise ex
        return result

    def get_net_id(self, source_net_id="1.1.1.1.1.1") -> bytes:
        """
        Get the AMS Net ID of the target PLC.

        Parameters
        ----------
        source_net_id : str, optional
            Optional net ID.  Not required to be valid.
        """
        return b"".join(
            (
                self.create_header(
                    source_net_id=source_net_id,
                    request_id=SystemServiceRequestID.GET_INFO,
                ),
                # Empty payload?
                b"\x00\x00\x00\x00",
            )
        )

    def deserialize_get_net_id_response(self, response: bytes, addr) -> dict:
        """
        Parse a get_net_id response.

        Raises
        ------
        BadResponse
            If the response does not match the request for getting a net id.
        """
        if len(response) < 300:
            raise BadResponse("Bad length")

        header = response[0:12]
        if header[-1] == 0x80:
            return repr(AmsNetId.from_buffer_copy(response[12:18]))

        raise BadResponse("Unknown response")


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
            return svc.deserialize_add_route_response(packet, addr)
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
    to_send = svc.get_net_id()

    logger.debug("Sending %s to %s", to_send, plc_hostname)
    for recv in send_and_receive_udp(plc_hostname, to_send):
        if isinstance(recv, TimeoutError):
            # TODO max timeout/resend/etc
            logger.warning("Timeout; waiting")
            continue
        try:
            packet, addr = recv
            return svc.deserialize_get_net_id_response(packet, addr)
        except BadResponse as ex:
            logger.warning("Got bad response; waiting: %s", ex)
            logger.debug("Got bad response; waiting: %s", ex, exc_info=ex)


if __name__ == "__main__":
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
