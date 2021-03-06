import logging
import socket

from .. import constants, service
from ..service import BadResponse

module_logger = logging.getLogger(__name__)


def _send_and_receive_udp(
    host: str,
    packet: bytes,
    port: int = constants.ADS_UDP_SERVER_PORT,
    recv_length: int = 1024,
    timeout: float = 1.0,
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
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    try:
        # Bind before send is necessary at least on win32
        sock.bind(("", 0))
        sock.settimeout(timeout)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(packet, (host, port))

        while True:
            try:
                yield sock.recvfrom(recv_length)
            except socket.timeout:
                # Timeout, but give caller the option to retry
                yield TimeoutError()
                sock.sendto(packet, (host, port))
    finally:
        sock.close()


def send_and_receive_service_udp(
    svc: service.SystemService,
    plc_hostname: str,
    to_send: bytes,
    command_id: service.SystemServiceRequestCommand,
    timeout: float = 2.0,
    logger=None,
) -> dict:
    """
    Send a system service UDP packet and await a response.

    Parameters
    ----------
    svc : SystemService
        SystemService protocol instance.

    plc_hostname: str
        The PLC IP / hostname, or broadcast address.

    to_send : bytes
        Packet to send.

    command_id : SystemServiceRequestCommand
        Command ID to check when verifying responses.

    timeout : float, optional
        Timeout after no responses for a certain period.

    logger : logging.Logger
        Logger instance for status information.

    Yields
    ------
    response : dict
        PLC response to request, in a user-friendly dict.
    """
    logger = logger or module_logger

    valid_responses = 0
    logger.debug("Sending %s to %s", to_send, plc_hostname)
    for recv in _send_and_receive_udp(plc_hostname, to_send, timeout=timeout):
        if isinstance(recv, TimeoutError):
            if valid_responses == 0:
                logger.debug("Timed out waiting for responses")
            break
        try:
            packet, addr = recv
            result = svc.deserialize_response(packet, addr)
            if result["command_id"] == command_id:
                valid_responses += 1
                yield result
        except BadResponse as ex:
            logger.warning("Got bad response; waiting: %s", ex)
            logger.debug("Got bad response; waiting: %s", ex, exc_info=ex)
